"""FastAPI 入口：核验接口 + 静态页面。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from zoneinfo import available_timezones

from .solver import InfeasibleError, RowError, verify_records

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="跨时区交接记录核验", version="1.0.0")


@app.get("/api/timezones")
def list_timezones() -> dict:
    # available_timezones 中可能混入 "localtime" 这类非 IANA 名，剔除。
    names = sorted(t for t in available_timezones() if t != "localtime")
    return {"timezones": names}


@app.post("/api/verify")
async def verify(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "row": 1, "message": "请求体不是合法 JSON"},
        )

    try:
        result = verify_records(body if isinstance(body, list) else body.get("records"))
    except RowError as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "row": exc.row, "message": exc.message},
        )
    except InfeasibleError as exc:
        return JSONResponse(status_code=200, content=exc.payload)

    return JSONResponse(status_code=200, content=result)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
