"""跨时区交接记录核验服务。

业务侧按顺序录入 2~12 条本地时刻记录，服务端将每个本地时刻按
PEP 495 的 fold=0/1 分别转成 UTC 候选，经往返校验去重后，在
相邻秒数闭区间约束下搜索字典序最早的完整 UTC 序列。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

MIN_ROWS = 2
MAX_ROWS = 12
MIN_SECONDS = 0
MAX_SECONDS = 86400

_LOCAL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$")


def parse_local(text: str) -> datetime:
    """严格按 YYYY-MM-DD HH:mm 解析本地挂钟时刻，失败抛 ValueError。"""
    m = _LOCAL_RE.match(text)
    if not m:
        raise ValueError("格式应为 YYYY-MM-DD HH:mm")
    year, month, day, hour, minute = (int(x) for x in m.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        raise ValueError(f"不是有效的日历日期/时间：{text}") from None


def utc_candidates(naive: datetime, tz: ZoneInfo) -> list[datetime]:
    """把本地挂钟时刻按 fold=0/1 转 UTC，往返校验后按 UTC 升序去重。

    夏令时回拨（fold 歧义）可得到两个候选；夏令时向前跳动产生的
    不存在时刻，往返校验失败，返回空列表。
    """
    found: set[datetime] = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=tz, fold=fold)
        utc = local.astimezone(timezone.utc).replace(tzinfo=None)
        back = utc.replace(tzinfo=timezone.utc).astimezone(tz)
        if back.replace(tzinfo=None) == naive and back.fold == fold:
            found.add(utc)
    return sorted(found)


def validate_payload(payload: object) -> tuple[list[dict], list[dict]]:
    """校验载荷，返回 (records, errors)。errors 非空时 records 不可用。"""
    errors: list[dict] = []

    if not isinstance(payload, dict):
        return [], [{"row": None, "field": None, "message": "请求体必须是 JSON 对象"}]

    records = payload.get("records")
    if not isinstance(records, list):
        return [], [{"row": None, "field": "records", "message": "records 必须是数组"}]

    if not (MIN_ROWS <= len(records) <= MAX_ROWS):
        errors.append({
            "row": None,
            "field": "records",
            "message": f"记录数必须在 {MIN_ROWS}～{MAX_ROWS} 条之间，当前 {len(records)} 条",
        })
        return [], errors

    for i, item in enumerate(records):
        row = i + 1
        if not isinstance(item, dict):
            errors.append({"row": row, "field": None, "message": "该行必须是 JSON 对象"})
            continue

        local_text = item.get("local")
        if not isinstance(local_text, str):
            errors.append({"row": row, "field": "local", "message": "本地时刻必须是字符串"})
        else:
            try:
                parse_local(local_text)
            except ValueError as exc:
                errors.append({
                    "row": row,
                    "field": "local",
                    "message": f"本地时刻格式错误：{exc}",
                })

        tz_name = item.get("timezone")
        if not isinstance(tz_name, str) or not tz_name:
            errors.append({"row": row, "field": "timezone", "message": "时区不能为空"})
        else:
            try:
                ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                errors.append({
                    "row": row,
                    "field": "timezone",
                    "message": f"无效的 IANA 时区：{tz_name}",
                })

        if i == 0:
            continue

        for field in ("min_seconds", "max_seconds"):
            value = item.get(field)
            # bool 是 int 的子类，需显式排除
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append({"row": row, "field": field, "message": "必须是整数秒"})
            elif not (MIN_SECONDS <= value <= MAX_SECONDS):
                errors.append({
                    "row": row,
                    "field": field,
                    "message": f"必须是 {MIN_SECONDS}～{MAX_SECONDS} 的整数",
                })

        min_s = item.get("min_seconds")
        max_s = item.get("max_seconds")
        if (
            isinstance(min_s, int)
            and not isinstance(min_s, bool)
            and isinstance(max_s, int)
            and not isinstance(max_s, bool)
            and MIN_SECONDS <= min_s <= MAX_SECONDS
            and MIN_SECONDS <= max_s <= MAX_SECONDS
            and min_s > max_s
        ):
            errors.append({
                "row": row,
                "field": "max_seconds",
                "message": f"最短秒数（{min_s}）不能大于最长秒数（{max_s}）",
            })

    return records if not errors else [], errors


def solve(records: list[dict], tzs: list[ZoneInfo]) -> dict:
    """构造每行 UTC 候选并搜索字典序最早的可行完整路径。"""
    row_options: list[list[dict]] = []
    for item, tz in zip(records, tzs):
        naive = parse_local(item["local"])
        options = []
        for utc in utc_candidates(naive, tz):
            aware = utc.replace(tzinfo=timezone.utc)
            local_back = aware.astimezone(tz)
            options.append({
                "utc": utc,
                "tz": tz,
                "fold": local_back.fold,
                "offset": local_back.utcoffset(),
                "abbr": local_back.tzname(),
            })
        row_options.append(options)

    constraints = [None] + [
        (records[i]["min_seconds"], records[i]["max_seconds"])
        for i in range(1, len(records))
    ]

    chosen: list[dict] = []

    def extend(k: int) -> bool:
        """尝试为第 k 行（0 基）选一个候选；候选按 UTC 升序，首个走通即字典序最早。"""
        if k == len(row_options):
            return True
        for opt in row_options[k]:
            if k > 0:
                delta = int((opt["utc"] - chosen[-1]["utc"]).total_seconds())
                lo, hi = constraints[k]
                if not (lo <= delta <= hi):
                    continue
            chosen.append(opt)
            if extend(k + 1):
                return True
            chosen.pop()
        return False

    if extend(0):
        return {"feasible": True, "options_count": [len(o) for o in row_options], "path": chosen}

    # 无解：定位首个无法延伸的前缀行（第一个不存在可行路径的前缀）
    def dfs_prefix(idx: int, end: int, previous: dict | None) -> bool:
        """前缀 [0..end-1]（共 end 行）是否存在可行路径。"""
        if idx == end:
            return True
        for opt in row_options[idx]:
            if previous is not None:
                lo, hi = constraints[idx]
                delta = int((opt["utc"] - previous["utc"]).total_seconds())
                if not (lo <= delta <= hi):
                    continue
            if dfs_prefix(idx + 1, end, opt):
                return True
        return False

    first_bad = len(records)
    for end in range(1, len(row_options) + 1):
        if not dfs_prefix(0, end, None):
            first_bad = end  # 1 基行号
            break

    return {
        "feasible": False,
        "options_count": [len(o) for o in row_options],
        "first_bad_row": first_bad,
    }


def format_offset(delta) -> str:
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{total % 3600 // 60:02d}"


app = FastAPI(title="跨时区交接记录核验")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    # 能成功构造 ZoneInfo 即说明时区数据可用
    ZoneInfo("Asia/Shanghai")
    return {"status": "ok"}


@app.post("/api/verify")
async def verify(request: Request) -> JSONResponse:
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "kind": "bad_request", "errors": [
                {"row": None, "field": None, "message": "请求体不是合法 JSON"}]}
        )

    records, errors = validate_payload(payload)
    if errors:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "kind": "validation", "errors": errors},
        )

    tzs = [ZoneInfo(item["timezone"]) for item in records]

    # 往返校验后可能出现“不存在的本地时刻”（夏令时缺口）
    gap_errors = []
    for i, (item, tz) in enumerate(zip(records, tzs)):
        if not utc_candidates(parse_local(item["local"]), tz):
            gap_errors.append({
                "row": i + 1,
                "field": "local",
                "message": "该本地时刻不存在（处于夏令时向前跳变的缺口中）",
            })
    if gap_errors:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "kind": "validation", "errors": gap_errors},
        )

    result = solve(records, tzs)

    if not result["feasible"]:
        return JSONResponse(content={
            "ok": True,
            "feasible": False,
            "first_bad_row": result["first_bad_row"],
            "options_count": result["options_count"],
        })

    rows_out = []
    for i, opt in enumerate(result["path"]):
        utc_aware = opt["utc"].replace(tzinfo=timezone.utc)
        ambiguous = result["options_count"][i] > 1
        rows_out.append({
            "row": i + 1,
            "utc": opt["utc"].strftime("%Y-%m-%d %H:%M"),
            "utc_iso": utc_aware.isoformat(),
            "offset": format_offset(opt["offset"]),
            "offset_seconds": int(opt["offset"].total_seconds()),
            "tz_abbr": opt["abbr"],
            "fold": opt["fold"] if ambiguous else None,
            "occurrence": (opt["fold"] + 1) if ambiguous else None,
            "actual_seconds": None if i == 0 else int(
                (result["path"][i]["utc"] - result["path"][i - 1]["utc"]).total_seconds()
            ),
            "ambiguous": ambiguous,
        })

    return JSONResponse(content={
        "ok": True,
        "feasible": True,
        "rows": rows_out,
        "options_count": result["options_count"],
    })
