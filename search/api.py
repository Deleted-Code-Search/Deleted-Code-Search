"""FastAPI 앱 - 통계 대시보드 (#93) + 검색 1단계 키워드 (#191) · 2단계 의미 (#193). 담당: 성제/희수

기준: CHARTER.md §4.2 "통계 대시보드", §4.2 ⑤, §10.5.
    - /api/stats: `search.stats` 가 만든 집계 파일을 그대로 돌려준다. 요청마다 파일을 다시 읽는다 -
      `python -m search.stats` 를 다시 돌리면 서버를 껐다 켜지 않아도 된다
    - /api/search, /api/search/meta, /api/records/{id}: `search.index` 가 만든 SQLite FTS5 색인을
      읽기 전용으로 연다 (ADR-022). 요청마다 연결을 새로 연다 - 색인을 다시 만들어도 그대로 반영된다
    - /api/search?mode=semantic: `search.embed` 가 만든 벡터 파일(ADR-023). 모델·벡터는 서버 시작 때
      한 번 읽어 둔다 (요청마다 읽지 않는다). 파일이 없으면 semantic 만 503, keyword 는 그대로다.
      결과 목록 칼럼은 키워드 색인에서 id 로 가져온다 - 키워드 색인도 있어야 한다
    - 진단(확장 2)은 범위 밖

실행:
    uvicorn search.api:app --reload
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import zlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from search.embed import DEFAULT_OUT as DEFAULT_SEMANTIC
from search.index import DEFAULT_OUT as DEFAULT_INDEX
from search.index import FACET_COLUMN, FACET_FIELDS, FTS_COLUMNS, facet_token
from search.semantic import SemanticIndex, SemanticUnavailable, default_encoder
from search.stats import DEFAULT_OUT

STATS_ENV = "DASHBOARD_STATS"
INDEX_ENV = "SEARCH_INDEX"
SEMANTIC_ENV = "SEARCH_SEMANTIC"
# "1" 이면 벡터를 메모리맵 그대로 둔다 (메모리 1.1 GB 절약, 요청당 약 0.4초 더, search.semantic)
SEMANTIC_MMAP_ENV = "SEARCH_SEMANTIC_MMAP"
# 개발 중 Vite 개발 서버가 부른다.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
# snippet 강조 표시. 화면이 이 두 글자로 나눠 <mark> 로 그린다 - API 는 HTML 을 만들지 않는다.
MARK_OPEN = "\x02"
MARK_CLOSE = "\x03"
# bm25 칼럼 가중치. search.index.FTS_COLUMNS 순서 (함수 이름 > 경로 > 커밋·PR > 이슈 > 본문).
BM25_WEIGHTS = (10.0, 4.0, 3.0, 3.0, 2.0, 1.0)
# 총 건수는 여기까지만 센다 - 흔한 단어는 수십만 건이 맞아 전부 세면 느려진다.
COUNT_CAP = 10_000
RESULT_COLUMNS = (
    "r.id, r.repo, r.function_name, r.file_path, r.year, r.deletion_kind, r.reason_label, "
    "r.evidence_grade, r.source, r.code_policy, r.is_test_code, r.commit_first_line"
)
META_KEYS = (
    "dataset_version",
    "records",
    "repos",
    "reasons",
    "grades",
    "sources",
    "kinds",
    "year_min",
    "year_max",
    "fts_columns",
)

# 의미 검색은 FULL_FUNCTION 만 임베딩했다 (search.embed).
SEMANTIC_KIND = "FULL_FUNCTION"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """서버 시작 때 모델·벡터를 한 번 읽는다. 파일이 없어도 서버는 뜬다 (semantic 만 503)."""
    try:
        semantic_index()
    except HTTPException:
        pass
    yield


app = FastAPI(title="Deleted Code Search", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET"])


def stats_path() -> Path:
    return Path(os.environ.get(STATS_ENV, DEFAULT_OUT))


def index_path() -> Path:
    return Path(os.environ.get(INDEX_ENV, DEFAULT_INDEX))


def semantic_path() -> Path:
    return Path(os.environ.get(SEMANTIC_ENV, DEFAULT_SEMANTIC))


# 테스트는 가짜 모델로 바꿔 끼운다.
encoder_factory = default_encoder
_semantic: dict[Path, SemanticIndex] = {}
_semantic_lock = threading.Lock()


def semantic_index() -> SemanticIndex:
    """읽어 둔 의미 검색 색인. 처음 한 번만 모델·벡터를 읽는다 (경로가 바뀌면 다시).

    실패는 기억하지 않는다 - 파일 확인은 싸고, 임베딩을 다 만든 뒤 서버를 껐다 켜지 않아도 된다.
    """
    path = semantic_path().resolve()
    with _semantic_lock:
        if path not in _semantic:
            try:
                _semantic.clear()
                in_memory = os.environ.get(SEMANTIC_MMAP_ENV) != "1"
                _semantic[path] = SemanticIndex.load(path, encoder_factory, in_memory)
            except SemanticUnavailable as err:
                raise HTTPException(
                    status_code=503, detail=f"의미 검색을 쓸 수 없다: {err}"
                ) from err
            except ImportError as err:
                raise HTTPException(
                    status_code=503,
                    detail=f'의미 검색 모델을 읽지 못했다 ({err}). pip install -e ".[embed]"',
                ) from err
        return _semantic[path]


def semantic_manifest() -> dict[str, Any] | None:
    """모델을 읽지 않고 manifest 만 본다 (meta·health 용)."""
    path = semantic_path() / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "stats_file": stats_path().is_file(),
        "search_index": index_path().is_file(),
        "semantic_index": semantic_manifest() is not None,
    }


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    path = stats_path()
    if not path.is_file():
        raise HTTPException(
            status_code=503, detail=f"{path} 가 없다. python -m search.stats 로 먼저 만든다"
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 검색 1단계 (#191)


@contextmanager
def open_index() -> Iterator[sqlite3.Connection]:
    path = index_path()
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"검색 색인 {path} 가 없다. python -m search.index 먼저 돌린다",
        )
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def read_info(conn: sqlite3.Connection) -> dict[str, Any]:
    return {key: json.loads(value) for key, value in conn.execute("SELECT key, value FROM info")}


def fts_query(q: str) -> str | None:
    """입력 → FTS5 질의. 공백으로 나눈 조각마다 구문으로 묶고 AND 로 잇는다.

    사용자가 FTS5 문법(AND·OR·NEAR·칼럼 필터)을 몰라도 되고, 따옴표·괄호를 넣어도 오류가 나지
    않는다. `retry_backoff` 처럼 밑줄로 이은 이름은 토크나이저가 "retry backoff" 구문으로 본다.
    끝에 `*` 를 붙이면 접두어 검색이다 (`retr*`).
    """
    terms = []
    for chunk in q.split():
        prefix = chunk.endswith("*")
        body = chunk.rstrip("*")
        if not any(ch.isalnum() for ch in body):
            continue
        terms.append('"' + body.replace('"', '""') + '"' + ("*" if prefix else ""))
    return " ".join(terms) or None


def check_filters(info: dict[str, Any], params: dict[str, Any]) -> None:
    """선택지에 없는 값은 422 - 오타가 "결과 0건" 으로 숨지 않게."""
    allowed = {
        "repo": info["repos"],
        "reason": info["reasons"],
        "grade": info["grades"],
        "source": info["sources"],
        "kind": info["kinds"],
    }
    for name, values in allowed.items():
        value = params.get(name)
        if value and value not in values:
            raise HTTPException(status_code=422, detail=f"{name}={value!r} 는 선택지에 없다")


def facet_match(info: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """필터 → facets 칼럼 질의 조각 (search.index 모듈 설명). AND 로 잇는다."""
    parts = [
        f"{FACET_COLUMN} : {facet_token(field, params[field])}"
        for field in FACET_FIELDS
        if params.get(field)
    ]
    if params.get("year_from") is not None or params.get("year_to") is not None:
        low = max(params.get("year_from") or info["year_min"], info["year_min"])
        high = min(params.get("year_to") or info["year_max"], info["year_max"])
        years = [facet_token("year", y) for y in range(low, high + 1)]
        # 범위가 비면 아무 행에도 없는 토큰으로 0건을 낸다
        parts.append(f"{FACET_COLUMN} : ({' OR '.join(years) or 'year0'})")
    return parts


def _result(row: sqlite3.Row) -> dict[str, Any]:
    result = {key: row[key] for key in row.keys()}  # noqa: SIM118 - sqlite3.Row 는 keys() 만 있다
    result.pop("rid", None)
    result["is_test_code"] = bool(result["is_test_code"])
    result.setdefault("snippet", None)
    return result


def _keyword_page(
    conn: sqlite3.Connection, terms: str, facets: list[str], limit: int, offset: int
) -> tuple[list[sqlite3.Row], str, list[Any]]:
    """키워드 검색 한 쪽. 순위는 fts 의 rowid 만으로 먼저 정하고, 그 쪽의 행에만 snippet 을 만든다.

    한 번에 `SELECT ..., snippet(...) ORDER BY bm25` 로 하면 정렬 전에 맞은 행 전부(흔한 단어는
    수십만 건)에 snippet 을 만든다 - `self`·`test` 가 1.1~1.3초 걸렸다 (#191 측정). 필터는 facets
    토큰으로 같은 MATCH 안에 넣어 records 와 잇지 않는다 (search.index 모듈 설명).
    """
    keyword = "{" + " ".join(FTS_COLUMNS) + "} : (" + terms + ")"
    clause = "FROM fts WHERE fts MATCH ?"
    clause_args: list[Any] = [" AND ".join([keyword, *facets])]
    weights = ", ".join(str(w) for w in BM25_WEIGHTS)
    ranked = [
        row[0]
        for row in conn.execute(
            f"SELECT rowid {clause} ORDER BY bm25(fts, {weights}) LIMIT ? OFFSET ?",
            [*clause_args, limit, offset],
        )
    ]
    if not ranked:
        return [], clause, clause_args
    # snippet 은 키워드만으로 다시 맞춘다 - facets 칼럼이 snippet 으로 뽑히지 않게
    snippet = f"snippet(fts, -1, '{MARK_OPEN}', '{MARK_CLOSE}', '…', 16) AS snippet"
    marks = ", ".join("?" * len(ranked))
    rows = conn.execute(
        f"SELECT r.rowid AS rid, {RESULT_COLUMNS}, {snippet} FROM fts"
        f" JOIN records r ON r.rowid = fts.rowid WHERE fts MATCH ? AND fts.rowid IN ({marks})",
        [keyword, *ranked],
    ).fetchall()
    by_rowid = {row["rid"]: row for row in rows}
    return [by_rowid[rowid] for rowid in ranked], clause, clause_args


def _filter_page(
    conn: sqlite3.Connection, facets: list[str], limit: int, offset: int
) -> tuple[list[sqlite3.Row], str, list[Any]]:
    """필터만 검색 한 쪽 (rowid 순). records 칼럼 색인으로 필터를 여러 개 걸면 0.6~0.7초였다 -
    facets 토큰 교집합은 FTS5 가 rowid 순으로 바로 낸다 (#191 측정)."""
    clause = "FROM fts WHERE fts MATCH ?"
    clause_args: list[Any] = [" AND ".join(facets)]
    ranked = [
        row[0]
        for row in conn.execute(
            f"SELECT rowid {clause} ORDER BY rowid LIMIT ? OFFSET ?", [*clause_args, limit, offset]
        )
    ]
    if not ranked:
        return [], clause, clause_args
    marks = ", ".join("?" * len(ranked))
    rows = conn.execute(
        f"SELECT {RESULT_COLUMNS} FROM records r WHERE r.rowid IN ({marks}) ORDER BY r.rowid",
        ranked,
    ).fetchall()
    return rows, clause, clause_args


@app.get("/api/search/meta")
def search_meta() -> dict[str, Any]:
    """필터 선택지. 색인을 만들 때 info 테이블에 넣어 둔 값이다."""
    with open_index() as conn:
        info = read_info(conn)
    meta = {key: info[key] for key in META_KEYS}
    manifest = semantic_manifest()
    meta["semantic"] = (
        None
        if manifest is None
        else {key: manifest[key] for key in ("model", "dim", "count", "created_at", "repos")}
    )
    return meta


def _semantic_page(
    conn: sqlite3.Connection, q: str, params: dict[str, Any], limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """의미 검색 한 쪽. 순위·유사도는 벡터에서, 목록 칼럼은 키워드 색인 records 에서 id 로."""
    if params.get("kind") and params["kind"] != SEMANTIC_KIND:
        return [], 0
    engine = semantic_index()
    hits, total = engine.search(q, offset + limit, params)
    hits = hits[offset:]
    if not hits:
        return [], total
    marks = ", ".join("?" * len(hits))
    rows = conn.execute(
        f"SELECT {RESULT_COLUMNS} FROM records r WHERE r.id IN ({marks})", [h[0] for h in hits]
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    missing = [rid for rid, _ in hits if rid not in by_id]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"임베딩과 키워드 색인이 맞지 않는다 (색인에 없는 id {missing[0]} 외 "
            f"{len(missing) - 1}건) - 같은 데이터셋으로 다시 만든다",
        )
    results = []
    for rid, score in hits:
        result = _result(by_id[rid])
        result["score"] = round(score, 4)
        results.append(result)
    return results, total


@app.get("/api/search")
def search(
    q: str = "",
    mode: Literal["keyword", "semantic"] = "keyword",
    repo: str | None = None,
    reason: str | None = None,
    grade: str | None = None,
    source: str | None = None,
    kind: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=COUNT_CAP),
) -> dict[str, Any]:
    """키워드(FTS5 bm25 순) 또는 필터만(rowid 순). q 가 비어도 필터만으로 찾는다.

    필터는 키워드가 있든 없든 facets 토큰으로 FTS5 안에서 푼다 (search.index 모듈 설명).
    mode=semantic 은 질의 임베딩과 유사도 순이고 결과마다 `score`(코사인)가 붙는다. FULL_FUNCTION 만
    임베딩했으므로 kind=PARTIAL 은 0건이다. 총 건수는 필터를 통과한 임베딩 수다.
    """
    started = time.perf_counter()
    params = {
        "repo": repo,
        "reason": reason,
        "grade": grade,
        "source": source,
        "kind": kind,
        "year_from": year_from,
        "year_to": year_to,
    }
    if mode == "semantic":
        if not q.strip():
            raise HTTPException(status_code=422, detail="의미 검색은 질의가 필요하다")
        with open_index() as conn:
            check_filters(read_info(conn), params)
            results, total = _semantic_page(conn, q, params, limit, offset)
        return {
            "q": q,
            "mode": mode,
            "match": None,
            "total": total,
            "total_capped": False,
            "limit": limit,
            "offset": offset,
            "results": results,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    with open_index() as conn:
        info = read_info(conn)
        check_filters(info, params)
        match = fts_query(q)
        try:
            facets = facet_match(info, params)
            if match:
                rows, clause, clause_args = _keyword_page(conn, match, facets, limit, offset)
            elif facets:
                rows, clause, clause_args = _filter_page(conn, facets, limit, offset)
            else:
                clause, clause_args = "FROM records r", []
                select = f"SELECT {RESULT_COLUMNS} {clause} ORDER BY r.rowid LIMIT ? OFFSET ?"
                rows = conn.execute(select, [limit, offset]).fetchall()
            total = conn.execute(
                f"SELECT count(*) FROM (SELECT 1 {clause} LIMIT {COUNT_CAP + 1})", clause_args
            ).fetchone()[0]
        except sqlite3.OperationalError as err:
            raise HTTPException(status_code=400, detail=f"검색어를 해석하지 못했다: {err}") from err
    return {
        "q": q,
        "mode": mode,
        "match": match,
        "total": min(total, COUNT_CAP),
        "total_capped": total > COUNT_CAP,
        "limit": limit,
        "offset": offset,
        "results": [_result(row) for row in rows],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


@app.get("/api/records/{record_id}")
def record_detail(record_id: str) -> dict[str, Any]:
    """레코드 원문(Release jsonl 한 줄 그대로) + 리뷰 코멘트 수. LINK_ONLY 는 코드 칸이 null."""
    with open_index() as conn:
        row = conn.execute("SELECT data FROM records WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"레코드 {record_id} 가 없다")
    record = json.loads(zlib.decompress(row["data"]))
    comments = (record.get("context") or {}).get("review_comments")
    return {
        "record": record,
        "review_comment_count": None if comments is None else len(comments),
    }
