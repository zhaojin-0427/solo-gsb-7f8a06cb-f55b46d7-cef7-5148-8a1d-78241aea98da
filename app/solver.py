"""跨时区交接记录核验核心逻辑。

每条记录给出业务本地墙上时刻 ``YYYY-MM-DD HH:mm`` 与 IANA 时区名。
本地时刻分别以 ``fold=0`` / ``fold=1`` 两次转为 UTC，经往返校验后
去重：

* 夏令时春播（gap）中的时刻，两种 fold 都无法往返还原 -> 无候选；
* 夏令时秋拨（overlap）中的歧义时刻，保留两个不同的 UTC 候选；
* 普通时刻两个候选相同，去重后仅一个。

相邻记录的 UTC 差值必须落在 [min_seconds, max_seconds] 闭区间内。
在所有完整可行路径中取 UTC 序列字典序最早的一条，使用带回溯的 DFS，
而不是逐行贪心。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FMT = "%Y-%m-%d %H:%M"
_SHAPE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
MIN_RECORDS = 2
MAX_RECORDS = 12
MAX_SECONDS = 86_400  # 一天


class RowError(Exception):
    """定位到具体行（1 起）的录入错误。"""

    def __init__(self, row: int, message: str):
        super().__init__(message)
        self.row = row
        self.message = message


def parse_local(value: object, row: int) -> datetime:
    """严格按 ``YYYY-MM-DD HH:mm`` 解析，拒绝多余/缺失字符与单位数字段。"""
    if not isinstance(value, str) or not value.strip():
        raise RowError(row, "本地时刻不能为空，格式须为 YYYY-MM-DD HH:mm")
    text = value.strip()
    # Python strptime 对 %m/%d/%H/%M 接受单位数，先锁死形状。
    if not _SHAPE_RE.match(text):
        raise RowError(row, f"本地时刻格式错误：{text!r}，须为 YYYY-MM-DD HH:mm（24 小时制，月日时分均为两位）")
    try:
        dt = datetime.strptime(text, FMT)
    except ValueError:
        raise RowError(row, f"本地时刻不是合法日历时间：{text!r}")
    return dt.replace(tzinfo=None)


def load_zone(tz_name: object, row: int) -> ZoneInfo:
    if not isinstance(tz_name, str) or not tz_name.strip():
        raise RowError(row, "IANA 时区不能为空，例如 Asia/Shanghai")
    name = tz_name.strip()
    try:
        return ZoneInfo(name)
    except Exception:
        raise RowError(row, f"无效的 IANA 时区：{name!r}，例如 Asia/Shanghai、America/New_York")


def utc_candidates(naive_local: datetime, zone: ZoneInfo) -> list[datetime]:
    """fold=0/1 分别转 UTC，往返（墙上时刻）校验后去重，按时间升序返回。

    依据 PEP 495：能从转换后的 UTC 再还原出同样的年月日时分，
    才说明该 fold 解释真实存在。春播 gap 时两个 fold 都还原失败，
    返回空列表；秋拨 overlap 时返回两个不同的 UTC。
    """
    found: list[datetime] = []
    for fold in (0, 1):
        aware = naive_local.replace(tzinfo=zone, fold=fold)
        utc_dt = aware.astimezone(timezone.utc)
        wall_back = utc_dt.astimezone(zone).replace(tzinfo=None)
        if wall_back == naive_local:
            found.append(utc_dt)
    found.sort()
    # 去重（普通时刻两种 fold 得到同一 UTC）
    unique: list[datetime] = []
    for dt in found:
        if not unique or unique[-1] != dt:
            unique.append(dt)
    return unique


def _parse_bounds(value: object, row: int, field: str) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise RowError(row, f"{field}不能为空，须为 0～{MAX_SECONDS} 的整数")
    if isinstance(value, bool) or not isinstance(value, int):
        raise RowError(row, f"{field}必须是整数秒（0～{MAX_SECONDS}）")
    if not 0 <= value <= MAX_SECONDS:
        raise RowError(row, f"{field}超出范围：{value}，允许 0～{MAX_SECONDS} 秒")
    return value


@dataclass
class PreparedRow:
    row: int
    local: datetime
    zone: ZoneInfo
    zone_name: str
    min_seconds: int | None
    max_seconds: int | None
    candidates: list[datetime]


def _offset_string(local_aware: datetime) -> str:
    """形如 UTC+08:00 / UTC-05:00 的偏移字符串（按该行时区）。"""
    off = local_aware.utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, mm = divmod(total // 60, 60)
    return f"UTC{sign}{hh:02d}:{mm:02d}"


def verify_records(records: object) -> dict:
    """核验入口。

    返回成功结构::

        {"status": "ok", "rows": [...]}

    失败时抛出 :class:`RowError`（录入错误）或带 ``payload`` 的
    :class:`InfeasibleError`（约束无解）。
    """
    if not isinstance(records, list):
        raise RowError(1, "请求体须为记录数组")
    if not MIN_RECORDS <= len(records) <= MAX_RECORDS:
        raise RowError(
            1,
            f"记录条数须为 {MIN_RECORDS}～{MAX_RECORDS} 条，当前 {len(records) if isinstance(records, list) else 0} 条",
        )

    prepared: list[PreparedRow] = []
    for idx, item in enumerate(records):
        row_no = idx + 1
        if not isinstance(item, dict):
            raise RowError(row_no, "每条记录须为对象，含 local/timezone/min_seconds/max_seconds")
        naive = parse_local(item.get("local"), row_no)
        zone = load_zone(item.get("timezone"), row_no)

        min_s = max_s = None
        if idx > 0:
            min_s = _parse_bounds(item.get("min_seconds"), row_no, "最短经过秒数")
            max_s = _parse_bounds(item.get("max_seconds"), row_no, "最长经过秒数")
            if min_s > max_s:
                raise RowError(row_no, f"最短经过秒数（{min_s}）不能大于最长经过秒数（{max_s}）")

        cands = utc_candidates(naive, zone)
        if not cands:
            raise RowError(
                row_no,
                f"本地时刻 {naive.strftime(FMT)} 在时区 {item.get('timezone')} 不存在"
                "（处于夏令时春播跳过区间）",
            )
        prepared.append(
            PreparedRow(
                row=row_no,
                local=naive,
                zone=zone,
                zone_name=item.get("timezone").strip(),
                min_seconds=min_s,
                max_seconds=max_s,
                candidates=cands,
            )
        )

    return _solve(prepared)


class InfeasibleError(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("reason", "无可行路径"))
        self.payload = payload


def _solve(rows: list[PreparedRow]) -> dict:
    """前向可达集定位首个断点，后向 good 集辅助 DFS 取字典序最早路径。"""
    n = len(rows)

    def allowed(i: int, prev_utc: datetime, utc_dt: datetime) -> bool:
        delta = int((utc_dt - prev_utc).total_seconds())
        return rows[i].min_seconds <= delta <= rows[i].max_seconds

    # 前向可达：R[i] 为第 i 行中“能被某条从第 1 行出发的路径走到”的候选下标。
    reachable: list[set[int]] = [set(range(len(rows[0].candidates)))]
    dead_row: int | None = None
    for i in range(1, n):
        r: set[int] = set()
        for a in reachable[i - 1]:
            ua = rows[i - 1].candidates[a]
            for b, ub in enumerate(rows[i].candidates):
                if allowed(i, ua, ub):
                    r.add(b)
        if not r:
            dead_row = i
            break
        reachable.append(r)

    if dead_row is not None:
        raise InfeasibleError(_infeasible_payload(rows, allowed, reachable, dead_row))

    # good[i]：第 i 行候选中能够走到最后一行的下标，用于 DFS 剪枝。
    good: list[set[int]] = [set() for _ in range(n)]
    good[n - 1] = set(range(len(rows[n - 1].candidates)))
    for i in range(n - 2, -1, -1):
        for a, ua in enumerate(rows[i].candidates):
            for b, ub in enumerate(rows[i + 1].candidates):
                if allowed(i + 1, ua, ub) and b in good[i + 1]:
                    good[i].add(a)
                    break

    # DFS：候选本身已升序，按序尝试即可保证字典序最早（含回溯，不逐行贪选）。
    chosen: list[int] = []

    def dfs(i: int) -> bool:
        if i == n:
            return True
        if i == 0:
            enum = enumerate(rows[0].candidates)
        else:
            prev_utc = rows[i - 1].candidates[chosen[i - 1]]
            enum = (
                (j, uc)
                for j, uc in enumerate(rows[i].candidates)
                if allowed(i, prev_utc, uc)
            )
        for j, _uc in enum:
            if j not in good[i]:
                continue
            chosen.append(j)
            if dfs(i + 1):
                return True
            chosen.pop()
        return False

    assert dfs(0), "所有前缀可达却搜索失败（不应发生）"

    path_utc = [rows[i].candidates[chosen[i]] for i in range(n)]
    out_rows = []
    for i, p in enumerate(rows):
        utc_dt = path_utc[i]
        local_aware = utc_dt.astimezone(p.zone)
        gap = None if i == 0 else int((utc_dt - path_utc[i - 1]).total_seconds())
        out_rows.append(
            {
                "row": p.row,
                "local": p.local.strftime(FMT),
                "timezone": p.zone_name,
                "utc": utc_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "offset": _offset_string(local_aware),
                "offset_abbrev": local_aware.tzname() or "",
                "fold": local_aware.fold,
                "candidate_count": len(p.candidates),
                "min_seconds": p.min_seconds,
                "max_seconds": p.max_seconds,
                "gap_seconds": gap,
            }
        )
    return {"status": "ok", "rows": out_rows}


def _infeasible_payload(rows: list[PreparedRow], allowed, reachable: list[set[int]], dead_row: int) -> dict:
    """dead_row（下标）是第一个前向可达集为空的行。

    列出上一行所有可达候选与本行各候选的最近差距，解释区间为何落空。
    """
    details = []
    prev_reachable = reachable[dead_row - 1]
    r = rows[dead_row]
    target_mid = mid(r)
    for a in sorted(prev_reachable):
        ua = rows[dead_row - 1].candidates[a]
        best = None
        for ub in rows[dead_row].candidates:
            delta = int((ub - ua).total_seconds())
            if best is None or abs(delta - target_mid) < abs(best[0] - target_mid):
                best = (delta, ub)
        if best is None:
            continue
        delta, ub = best
        if allowed(dead_row, ua, ub):  # 不应发生：dead_row 处必无解
            continue
        if delta < r.min_seconds:
            relation = f"比下限早 {r.min_seconds - delta} 秒"
        elif delta > r.max_seconds:
            relation = f"比上限晚 {delta - r.max_seconds} 秒"
        else:
            relation = "落在区间内"
        details.append(
            {
                "from_utc": ua.strftime("%Y-%m-%d %H:%M:%S"),
                "candidate_utc": ub.strftime("%Y-%m-%d %H:%M:%S"),
                "actual_seconds": delta,
                "required": [r.min_seconds, r.max_seconds],
                "message": (
                    f"上一行 UTC {ua:%Y-%m-%d %H:%M:%S} → 本行最近候选 "
                    f"{ub:%Y-%m-%d %H:%M:%S} 实际相差 {delta} 秒，"
                    f"要求 [{r.min_seconds}, {r.max_seconds}]，{relation}"
                ),
            }
        )

    first_bad = dead_row + 1
    return {
        "status": "infeasible",
        "first_bad_row": first_bad,
        "reason": (
            f"第 1～{first_bad} 行的前缀无法延伸：前 {dead_row} 行存在可行解释，"
            f"但第 {first_bad} 行的全部 UTC 候选与上一行的差值都不在 "
            f"[{r.min_seconds}, {r.max_seconds}] 秒闭区间内"
        ),
        "details": details,
    }


def mid(r: PreparedRow) -> float:
    if r.min_seconds is None:
        return 0
    return (r.min_seconds + r.max_seconds) / 2
