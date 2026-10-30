"""검색 1단계 색인 - SQLite FTS5 키워드 검색 (#191, ADR-022). 담당: 성제 (2단계 임베딩은 희수)

기준: CHARTER.md §4.2 ⑤ 의 1단계. 임베딩·pgvector(ADR-009)는 #8 이후 2단계로 남긴다.
Release `dataset-v0.9` 의 jsonl.gz 를 한 줄씩 읽어 SQLite 파일 하나에 쓴다. 표준 라이브러리
(gzip, json, sqlite3, zlib)만 쓴다 - pyarrow·PostgreSQL·Docker 는 쓰지 않는다 (ADR-022).

테이블:
    records  행 하나 = 레코드 하나. 결과 목록 칼럼 + 원문(zlib 압축 JSON). 필터 칼럼도 두지만
             B-tree 색인은 만들지 않는다 - 필터는 facets 로 푼다
    fts      FTS5. rowid 가 records.rowid 와 같다. 검색 칼럼은 FTS_COLUMNS, 마지막 칼럼 facets 는
             필터 값 토큰(facet_token) - 키워드+필터를 FTS 안에서 교집합으로 푼다 (아래)
    info     색인 정보 (데이터셋 버전, 건수, 필터 선택지) - /api/search/meta 가 그대로 쓴다

LINK_ONLY 행은 Release 에서 코드 칸이 이미 비어 있다 - 본문은 색인되지 않는다.

왜 facets 칼럼인가: 흔한 단어(`self`)는 수십만 건이 맞는다. 필터를 records 칼럼으로 걸면 맞은 행마다
records 를 찾아 봐야 해서 `self` + 저장소 필터가 1.2초 걸렸다. 필터만 여러 개 걸어도 records 칼럼
색인으로는 0.6~0.7초였다 (#191 측정). 필터 값을 토큰으로 넣어 두면 FTS5 가 역색인 목록끼리 교집합을
rowid 순으로 낸다. 사용자 검색어는 FTS_COLUMNS 로만 제한해서(`{...} : (...)`) facets 토큰에
맞지 않는다.

실행:
    python -m search.index
    python -m search.index --data data/export/dataset-v0.9 --out data/search/dataset-v0.9.sqlite
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from classify.labels import EVIDENCE_GRADES, REASON_LABELS
from search.stats import DATASET_VERSION, DEFAULT_DATA, DOWNLOAD_HINT, KINDS, SHARD_GLOB, SOURCES

SCHEMA_VERSION = "search_index/2"
DEFAULT_OUT = Path("data") / "search" / f"{DATASET_VERSION}.sqlite"
# FTS 대상. 순서가 bm25 가중치(search.api)·snippet 칼럼 번호와 맞물린다.
FTS_COLUMNS = (
    "function_name",
    "file_path",
    "commit_message",
    "pr_title",
    "issue_titles",
    "deleted_body",
)
# pipeline.export.LINK_ONLY 와 같은 값. 그 모듈은 pyarrow 를 불러오므로 가져오지 않는다 (ADR-022).
LINK_ONLY = "LINK_ONLY"
FACET_COLUMN = "facets"
# facets 토큰을 만드는 필터 (search.api 의 파라미터 이름 → records 칼럼). 연도는 따로 year{YYYY}.
FACET_FIELDS = ("repo", "reason", "grade", "source", "kind")
BATCH = 5000

SCHEMA = """
CREATE TABLE records (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    repo TEXT NOT NULL,
    year INTEGER,
    deletion_kind TEXT NOT NULL,
    reason_label TEXT,
    evidence_grade TEXT,
    source TEXT NOT NULL,
    code_policy TEXT NOT NULL,
    is_test_code INTEGER NOT NULL,
    function_name TEXT,
    file_path TEXT NOT NULL,
    commit_first_line TEXT,
    data BLOB NOT NULL
);
CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE fts USING fts5(
    function_name, file_path, commit_message, pr_title, issue_titles, deleted_body, facets,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class SearchIndexError(RuntimeError):
    """입력이 없거나 레코드 모양이 기대와 다르다."""


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """jsonl.gz 를 한 줄씩. 줄은 바이트 `\\n` 으로만 나눈다 (U+2028, #146)."""
    with gzip.open(path, "rb") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def shard_files(data_dir: Path) -> list[Path]:
    """Release 샤드만. `main500-v0.9.jsonl.gz` 같은 사본은 이름 규칙에서 빠진다."""
    files = sorted(data_dir.glob(SHARD_GLOB))
    if not files:
        raise SearchIndexError(f"{data_dir} 에 {SHARD_GLOB} 가 없다. 먼저 받는다: {DOWNLOAD_HINT}")
    return files


def first_line(text: str | None) -> str | None:
    if not text:
        return None
    return text.strip().splitlines()[0] if text.strip() else None


def facet_token(field: str, value: str | int) -> str:
    """필터 값 → facets 토큰 하나. unicode61 이 자르지 않게 영숫자만 (저장소 이름의 `/`·`-`)."""
    if field == "year":
        return f"year{int(value)}"
    return field + hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


def to_rows(record: dict[str, Any]) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """레코드 → (records 행, fts 행). rowid 는 넣는 쪽이 붙인다."""
    source = (record.get("classification") or {}).get("source")
    if source not in SOURCES:
        raise SearchIndexError(
            f"알 수 없는 classification.source {source!r} (행 {record.get('id')})"
        )
    # LINK_ONLY 는 코드 칸이 비어 있어야 한다 - 남아 있으면 색인·상세로 코드가 새어 나간다
    if record.get("code_policy") == LINK_ONLY:
        leaked = [k for k in ("deleted_body", "function_signature") if record.get(k) is not None]
        if leaked:
            raise SearchIndexError(
                f"LINK_ONLY 행 {record.get('id')} 에 코드 칸이 있다: {', '.join(leaked)}"
            )
    context = record.get("context") or {}
    reason = record.get("reason") or {}
    year = (record.get("author_date") or "")[:4]
    meta = (
        record["id"],
        record["repo"],
        int(year) if year.isdigit() else None,
        record["deletion_kind"],
        reason.get("label"),
        reason.get("evidence_grade"),
        source,
        record["code_policy"],
        int(bool(record.get("is_test_code"))),
        record.get("function_name"),
        record["file_path"],
        first_line(context.get("commit_message")),
        zlib.compress(json.dumps(record, ensure_ascii=False).encode("utf-8"), 6),
    )
    values = dict(zip(FACET_FIELDS, (meta[1], meta[4], meta[5], meta[6], meta[3]), strict=True))
    facets = [facet_token(field, value) for field, value in values.items() if value]
    if meta[2] is not None:
        facets.append(facet_token("year", meta[2]))
    text = (
        record.get("function_name") or "",
        record["file_path"],
        context.get("commit_message") or "",
        context.get("pr_title") or "",
        "\n".join(t for t in context.get("issue_titles") or [] if t),
        record.get("deleted_body") or "",
        " ".join(facets),
    )
    return meta, text


def build(data_dir: Path, out: Path, log: Callable[[str], None] = print) -> dict[str, Any]:
    """`out` 옆 임시 파일에 만들고 다 되면 바꿔 끼운다 - API 가 반쯤 된 파일을 보지 않는다."""
    files = shard_files(data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    started = time.perf_counter()
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(
            "PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; PRAGMA cache_size = -400000;"
        )
        conn.executescript(SCHEMA)

        rowid = 0
        years: set[int] = set()
        repos: set[str] = set()
        for number, path in enumerate(files, 1):
            metas: list[tuple[Any, ...]] = []
            texts: list[tuple[Any, ...]] = []
            for record in iter_records(path):
                rowid += 1
                meta, text = to_rows(record)
                metas.append((rowid, *meta))
                texts.append((rowid, *text))
                repos.add(meta[1])
                if meta[2] is not None:
                    years.add(meta[2])
                if len(metas) >= BATCH:
                    _flush(conn, metas, texts)
            _flush(conn, metas, texts)
            conn.commit()
            elapsed = time.perf_counter() - started
            log(f"[{number}/{len(files)}] {path.name} 누적 {rowid:,}행 {elapsed:.0f}초")

        log("FTS 정리(optimize)…")
        conn.execute("INSERT INTO fts(fts) VALUES ('optimize')")
        info = {
            "schema": SCHEMA_VERSION,
            "dataset_version": DATASET_VERSION,
            "records": rowid,
            "files": len(files),
            "fts_columns": list(FTS_COLUMNS),
            "facet_column": FACET_COLUMN,
            "repos": sorted(repos),
            "reasons": list(REASON_LABELS),
            "grades": list(EVIDENCE_GRADES),
            "sources": list(SOURCES),
            "kinds": list(KINDS),
            "year_min": min(years) if years else None,
            "year_max": max(years) if years else None,
        }
        conn.executemany(
            "INSERT INTO info VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in info.items()],
        )
        conn.commit()
        conn.execute("ANALYZE")
    except BaseException:
        # 반쯤 된 임시 파일을 남기지 않는다 (LINK_ONLY 코드 칸 등으로 멈춘 경우)
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(tmp, out)
    info["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    info["bytes"] = out.stat().st_size
    return info


def _flush(conn: sqlite3.Connection, metas: list, texts: list) -> None:
    if not metas:
        return
    conn.executemany(f"INSERT INTO records VALUES ({', '.join('?' * 14)})", metas)
    columns = (*FTS_COLUMNS, FACET_COLUMN)
    marks = ", ".join("?" * (len(columns) + 1))
    conn.executemany(f"INSERT INTO fts(rowid, {', '.join(columns)}) VALUES ({marks})", texts)
    metas.clear()
    texts.clear()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검색 1단계 SQLite FTS5 색인 (#191)")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="jsonl.gz 폴더")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="SQLite 파일")
    args = parser.parse_args(argv)
    try:
        info = build(args.data, args.out, log=functools.partial(print, flush=True))
    except SearchIndexError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    minutes = info["elapsed_seconds"] / 60
    print(
        f"완료: {args.out} - {info['records']:,}행, {info['bytes'] / 1e9:.2f} GB, {minutes:.1f}분"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
