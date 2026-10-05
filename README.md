# 跨时区交接记录核验

本地运行的跨时区交接记录核验网页。按业务顺序录入 2～12 条记录，服务端将每条
本地时刻按 `fold=0/1` 转成 UTC 候选，在相邻秒数闭区间约束下，整体搜索
**UTC 序列字典序最早** 的完整可行路径（非逐行贪选），并在页面展示各行采用的
UTC、时区偏移与相邻实际秒数。

技术栈：Python 3 · FastAPI · 标准库 `zoneinfo`（PEP 495 fold） · 原生 HTML/CSS/JavaScript，无前端构建。

## 一条命令启动（Docker Compose）

```bash
docker compose up --build
```

浏览器打开：

```
http://localhost:8000
```

停止：`Ctrl+C`（后台运行用 `docker compose up -d --build`，停止用 `docker compose down`）。

仅用 Docker 单条命令（无 Compose 时）：

```bash
docker build -t tz-handoff-checker . && docker run --rm -p 8000:8000 tz-handoff-checker
```

**本地访问端口：`8000`**（映射到容器内同一端口）。

### 不用 Docker，直接本地运行

需要 Python 3.9+（开发使用 3.11）。Linux 须装有系统时区数据库（Debian/Ubuntu：
`sudo apt-get install tzdata`）；Windows/macOS 可直接使用 pip 包 `tzdata`（已列入依赖）。

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 使用规则

1. 按业务顺序录入 **2～12 条** 记录，每行包含：
   - 本地时刻，严格格式 `YYYY-MM-DD HH:mm`（24 小时制，分钟精度）；
   - IANA 时区名，如 `Asia/Shanghai`、`America/New_York`、`Europe/London`、`UTC`。
2. 从第 2 条起，填写距上一条的 **最短、最长经过秒数**：
   - 均为 `0～86400` 的整数，且最短 ≤ 最长；
   - 相邻 UTC 差值必须落入对应 **闭区间** `[最短, 最长]`（端点可取）。
3. 点击“核验”：
   - 成功：表格逐行给出 **采用的 UTC**、**时区偏移**（如 `UTC-05:00`）、时区缩写、
     是否取了夏令时回拨的第 1/2 次出现，以及 **距上一条实际秒数**；
   - 无解：指出 **首个无法延伸的前缀行**（前 k 条已不存在可行路径，后续行不再检查）；
   - 格式错误、无效时区、不存在的本地时刻等会 **定位到具体行和字段**，可一次报告多处。
4. 任何输入被修改后，旧结论（结果表与错误提示）立即消失，需重新核验；页面不会展示过期结果。

页面“填入示例”按钮提供一个 **非贪选反例**（纽约 2024-11-03 回拨日，4 行）：
逐行贪选第 1 行较小的 UTC 候选会在第 2 行走死，只有回退取第 2 次出现才能走通，
用以说明结果是整体择优而非逐行贪心。

## 时区与夏令时处理

服务端对每行的本地挂钟时刻分别用 `fold=0` 与 `fold=1` 转 UTC，并做
**往返校验**（UTC 转回该时区，挂钟与 fold 均一致才接受），随后去重：

- **普通时刻**：两个 fold 得到同一 UTC → 1 个候选；
- **夏令时回拨（歧义时刻）**：如纽约 2024-11-03 01:30 存在两次 →
  `05:30Z`（EDT, fold=0，第 1 次出现）与 `06:30Z`（EST, fold=1，第 2 次出现），**保留两个候选**；
- **夏令时向前跳变（不存在的时刻）**：如纽约 2024-03-10 02:30 落在缺口里，
  两次往返校验都失败 → 0 个候选，对该行报“本地时刻不存在”。

## 算法说明

- 每行最多 2 个 UTC 候选（按 UTC 升序），行数 ≤ 12，候选组合最多 2^12 = 4096。
- 用带剪枝的深度优先搜索整体求可行路径：每一行按 UTC 从小到大尝试，
  第一条走通的路径即为 **逐分量字典序最早** 的可行序列；
  小候选在当前行能接上但在后续行无解时会自动回退（示例即此情形），故不是逐行贪选。
- 无解时，按前缀长度递增做同样的前缀可行性搜索，报告第一个不可行前缀对应的行号。
- 秒数差按相邻“采用的 UTC”计算，因此跨时区与夏令时跳变都以真实经过时间为准。

## HTTP 接口

- `GET /`：页面；`GET /static/*`：静态资源；`GET /api/health`：健康检查（同时验证时区数据可用）。
- `POST /api/verify`，请求体：

```json
{
  "records": [
    {"local": "2024-11-03 01:30", "timezone": "America/New_York"},
    {"local": "2024-11-03 02:30", "timezone": "America/New_York", "min_seconds": 0, "max_seconds": 3600}
  ]
}
```

响应：

- `200 {"ok": true, "feasible": true, "rows": [...]}`：成功，`rows` 每行含
  `utc`、`utc_iso`、`offset`、`offset_seconds`、`tz_abbr`、`fold`、
  `occurrence`（回拨第几次出现）、`ambiguous`、`actual_seconds`（第 1 行为 `null`）；
- `200 {"ok": true, "feasible": false, "first_bad_row": n}`：无解，`n` 为首个无法延伸的前缀行（1 基）；
- `422 {"ok": false, "kind": "validation", "errors": [{"row": 2, "field": "local", "message": "..."}]}`：
  行级错误（格式/时区/秒数/不存在时刻）；
- `400 {"ok": false, "kind": "bad_request", ...}`：请求体不是合法 JSON。

## 测试

```bash
PYTHONPATH=. python -m pytest -q        # 27 个用例：候选生成、校验定位、回搜索路与接口
```

## 项目结构

```
Dockerfile              基于 python:3.12-slim，apt 安装 tzdata（时区数据随容器）
docker-compose.yml      单服务，宿主 8000 -> 容器 8000
requirements.txt        fastapi / uvicorn / tzdata（兜底时区库）
app/main.py             校验、fold 候选、往返校验、字典序最早路径搜索、API
web/index.html          录入与结果页面（原生 HTML）
web/styles.css          样式
web/app.js              行增删、提交、错误定位与结果渲染
tests/test_core.py      单元与接口测试
```

## 时区数据

容器内通过 Debian 包 `tzdata` 安装完整 IANA 时区数据库（位于
`/usr/share/zoneinfo`），`zoneinfo` 优先使用系统库；`requirements.txt` 中的
`tzdata` 包作为兜底（也方便 Windows 本地开发）。容器默认时区设为 UTC，但所有
结果均由显式时区转换得到，与容器自身时区无关。
