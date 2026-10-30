"""FastAPI 앱 - 통계 대시보드 API (#93). 검색·진단(확장 1·2)은 #8 에서 붙인다. 담당: 성제/희수

기준: CHARTER.md §4.2 "통계 대시보드", §10.5. `search.stats` 가 만든 집계 파일을 그대로 돌려준다.
요청마다 파일을 다시 읽는다 - `python -m search.stats` 를 다시 돌리면 서버를 껐다 켜지 않아도 된다.

실행:
    uvicorn search.api:app --reload
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from search.stats import DEFAULT_OUT

STATS_ENV = "DASHBOARD_STATS"
# 개발 중 Vite 개발 서버가 부른다.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

app = FastAPI(title="Deleted Code Search", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET"])


def stats_path() -> Path:
    return Path(os.environ.get(STATS_ENV, DEFAULT_OUT))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "stats_file": stats_path().is_file()}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    path = stats_path()
    if not path.is_file():
        raise HTTPException(
            status_code=503, detail=f"{path} 가 없다. python -m search.stats 로 먼저 만든다"
        )
    return json.loads(path.read_text(encoding="utf-8"))
