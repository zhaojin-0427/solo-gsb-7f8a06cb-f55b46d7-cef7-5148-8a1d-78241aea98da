# 跨时区交接记录核验（tz-handover-check）

本地运行的跨时区交接记录核验网页：按业务顺序录入 2～12 条记录，服务端将每条
本地时刻在各自 IANA 时区下转换为 UTC，并在相邻经过秒数的闭区间约束下，求出
**UTC 序列字典序最早的完整可行路径**（全局回溯，不逐行贪心）。

- 后端：Python 3.12 · FastAPI · 标准库 `zoneinfo`（PEP 615，fold 双解释）
- 前端：原生 HTML / CSS / JavaScript，无任何前端构建与框架
- 时区数据：随容器安装系统 `tzdata`（IANA Time Zone Database）

## 一、启动（Docker，推荐）

单条命令（在本项目根目录，即 `Dockerfile` 所在目录）：

```bash
docker compose up --build -d
```

然后浏览器访问：

> **http://localhost:8000**

停止：

```bash
docker compose down
```

不想用 Compose 时，等价的单条 Docker 命令：

```bash
docker build -t tz-handover-check . && docker run --rm -p 8000:8000 tz-handover-check
```

- 本地访问端口：**8000**（映射到容器内 8000）
- 健康检查：<http://localhost:8000/healthz>
- IANA 时区列表接口：`GET /api/timezones`

## 二、本地直接运行（不用 Docker，需自行保证 tzdata）

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Debian/Ubuntu 若 `zoneinfo` 找不到时区，请先安装系统时区数据：
`sudo apt-get install tzdata`。

## 三、页面怎么用

1. 每行填写：
   - **本地时刻**：严格 `YYYY-MM-DD HH:mm`，24 小时制；
   - **IANA 时区**：如 `Asia/Shanghai`、`America/New_York`、`Asia/Kolkata`
     （输入框带自动补全）；
   - 第 2 行起填写**最短 / 最长经过秒数**：整数，`0 ≤ 最短 ≤ 最长 ≤ 86400`。
2. 记录数 2～12 条，可随时增删行。
3. 点击「核验」。**编辑任意字段后旧结论立即消失**，需重新点击核验，
   页面不会保留过期结果。
4. 结果表逐行展示：采用的 UTC、UTC 偏移（如 `UTC+05:30`，含缩写）、
   相邻实际秒数；秋拨歧义行会标注候选数与采用的 `fold`。

## 四、核验规则（与需求逐条对应）

1. **fold=0/1 双解释 + 往返校验 + 去重**
   每个本地时刻分别以 `fold=0`、`fold=1` 转 UTC，再从 UTC 转回该时区，
   只有墙上时刻能还原成原输入的解释才保留：
   - 普通时刻：两个解释相同，去重后 **1 个候选**；
   - 夏令时**回拨（fall-back）歧义时刻**：保留 **2 个 UTC 候选**，均排序参与求解；
   - 夏令时**春播（spring-forward）不存在的时刻**：两个解释都无法往返，
     **候选为空，按录入错误定位到行**。

2. **错误定位到行**：格式错误、非法时钟时间、无效 IANA 时区、不存在的本地时刻、
   秒数非整数 / 越界 / 最短大于最长，都返回首个出错行号（1 起）并在该行高亮。

3. **闭区间约束**：相邻两行所选 UTC 之差（秒）必须满足
   `min_seconds ≤ 实际差 ≤ max_seconds`（端点可取，0 与 86400 均合法，
   允许负差概念上不限制，但区间非负因此实际不会出现倒退路径）。

4. **全局字典序最早，绝不逐行贪选**：
   先用前向可达集定位断点、后向可达集剪枝，再做带回溯的深度优先搜索
   （候选按 UTC 升序尝试）。第一条走通的完整路径即 UTC 序列字典序最早者；
   较早行可能被迫放弃更早的候选以成全后续约束。

5. **无解诊断**：返回**首个无法延伸的前缀行** `first_bad_row`
   （即第一行使前向可达候选集变空的行），并逐条列出上一行各可达候选
   与本行最近候选的实际秒数及超出区间多少秒。

## 五、HTTP 接口

### `POST /api/verify`

请求体为 JSON 数组（第 1 行不需要秒数字段）：

```json
[
  {"local": "2024-11-03 01:30", "timezone": "America/New_York"},
  {"local": "2024-11-03 22:00", "timezone": "Asia/Shanghai",
   "min_seconds": 27000, "max_seconds": 30600}
]
```

成功（200）：

```json
{"status": "ok", "rows": [
  {"row": 1, "local": "2024-11-03 01:30", "timezone": "America/New_York",
   "utc": "2024-11-03 05:30:00", "offset": "UTC-04:00", "offset_abbrev": "EDT",
   "fold": 0, "candidate_count": 2, "min_seconds": null, "max_seconds": null,
   "gap_seconds": null}
]}
```

录入错误（400）：`{"status": "error", "row": 2, "message": "..."}`

无解（200）：`{"status": "infeasible", "first_bad_row": 3, "reason": "...", "details": [...]}`

## 六、目录结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py        # FastAPI 路由 / 静态文件
│   └── solver.py      # fold 往返校验、去重、区间约束、回溯求解、无解定位
├── static/
│   ├── index.html     # 原生 HTML
│   ├── style.css      # 原生 CSS
│   └── app.js         # 原生 JavaScript
├── tests/
│   └── test_solver.py # 求解器单元测试（标准库 unittest）
├── Dockerfile         # 安装 tzdata，时区数据随容器走
├── docker-compose.yml
└── requirements.txt
```

跑测试（仅用标准库，无需安装依赖）：

```bash
python3 tests/test_solver.py
```

测试覆盖：普通/秋拨/春播三类候选、错误行定位、边界秒数、
字典序最早选择、强制选晚候选、必须回溯的非贪心用例、
首个无法延伸前缀的定位（第 2 行 / 第 3 行）、半小时偏移时区。
