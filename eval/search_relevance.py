"""검색 관련성 판정 시트와 Precision@10 (#193, CHARTER §9 8주차, §10.3). 담당: 성제

1) `export`: 질의 10개 × (keyword, semantic) 상위 10개를 판정용 CSV 로 뽑는다. `relevant` 칸은
   비워 둔다 - **판정은 사람이 한다** (ADR-005 정신: 모델·도구가 정답을 채우지 않는다).
2) `score`: 사람이 채운 CSV 로 모드별 P@10 (질의별 평균)과 MRR(첫 관련 순위 역수 평균)을 낸다.

질의는 자연어 8개 + 코드 조각 2개다. 의미 검색에는 문장을 그대로, 키워드 검색에는 같은 뜻의 낱말
(`keyword`)을 넣는다 - 긴 문장을 FTS 에 그대로 넣으면 낱말 전부 AND 라 0건이 되기 쉽다. 이것은
키워드 쪽에 유리한 설정이다(사람이 낱말을 골라 준다). §10.3 의 질의 30개(자연어 15 + 코드 15)보다
적다 - 8주차 1차 측정이다.

`relevant` 값: 1/0 (y/n, o/x 도 받는다). 빈칸이 남아 있으면 score 는 멈춘다.

실행:
    python -m eval.search_relevance export            # 색인·임베딩 필요
    python -m eval.search_relevance score docs/reports/search_relevance_sheet.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

K = 10
DEFAULT_SHEET = Path("docs") / "reports" / "search_relevance_sheet.csv"
COLUMNS = (
    "query",
    "mode",
    "rank",
    "record_id",
    "repo",
    "function_name",
    "commit_first_line",
    "relevant",
)
MODES = ("keyword", "semantic")
TRUE = {"1", "y", "yes", "o", "true"}
FALSE = {"0", "n", "no", "x", "false"}

# (질의 - 의미 검색에 그대로, 키워드 검색 낱말)
QUERIES: tuple[tuple[str, str], ...] = (
    ("retry a failed call with exponential backoff", "retry backoff"),
    ("compatibility shim for python 2", "py2"),
    ("remove a deprecated function kept for backward compatibility", "deprecated"),
    ("parse command line arguments", "argparse"),
    ("cache results in memory to avoid recomputing them", "cache"),
    ("validate user input and raise an error when it is invalid", "validate"),
    ("convert a timestamp to a timezone aware datetime", "timezone"),
    ("close the database connection when finished", "close connection"),
    (
        "def load_config(path):\n    with open(path) as f:\n        return yaml.safe_load(f)",
        "load_config yaml",
    ),
    (
        "async def fetch(session, url):\n    async with session.get(url) as r:\n"
        "        return await r.json()",
        "fetch session",
    ),
)


class RelevanceError(RuntimeError):
    """판정 시트가 비었거나 모양이 다르다."""


def export_rows(search: Callable[[dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
    """search(params) → /api/search 응답. 질의마다 keyword 다음 semantic, 순위 1..K."""
    rows = []
    for query, keyword in QUERIES:
        for mode in MODES:
            q = keyword if mode == "keyword" else query
            body = search({"q": q, "mode": mode, "limit": K})
            for rank, hit in enumerate(body["results"], 1):
                rows.append(
                    {
                        "query": query,
                        "mode": mode,
                        "rank": rank,
                        "record_id": hit["id"],
                        "repo": hit["repo"],
                        "function_name": hit["function_name"] or "",
                        "commit_first_line": hit["commit_first_line"] or "",
                        "relevant": "",
                    }
                )
    return rows


def write_sheet(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 열어도 한글·기호가 깨지지 않게
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _judgement(value: str, where: str) -> bool:
    value = value.strip().lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    raise RelevanceError(f"{where}: relevant 가 비었거나 알 수 없는 값 {value!r} (1/0)")


def score(path: Path, k: int = K) -> dict[str, Any]:
    """채운 시트 → 모드별 P@k(질의별 평균)·MRR. 결과가 k 개보다 적으면 모자란 칸은 무관으로 센다."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise RelevanceError(f"{path} 에 칸이 없다: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise RelevanceError(f"{path} 가 비었다")
    judged: dict[tuple[str, str], dict[int, bool]] = defaultdict(dict)
    for line, row in enumerate(rows, 2):
        rank = int(row["rank"])
        if rank <= k:
            judged[(row["mode"], row["query"])][rank] = _judgement(row["relevant"], f"{line}행")
    per_mode: dict[str, dict[str, Any]] = {}
    queries = sorted({query for _, query in judged})
    for mode in sorted({mode for mode, _ in judged}):
        precisions = []
        reciprocal = []
        per_query = {}
        for query in queries:
            ranks = judged.get((mode, query), {})
            hits = [rank for rank, ok in sorted(ranks.items()) if ok]
            precision = len(hits) / k
            precisions.append(precision)
            reciprocal.append(1 / hits[0] if hits else 0.0)
            per_query[query] = {"p_at_k": precision, "first_relevant": hits[0] if hits else None}
        per_mode[mode] = {
            "queries": len(queries),
            "p_at_k": round(sum(precisions) / len(precisions), 3),
            "mrr": round(sum(reciprocal) / len(reciprocal), 3),
            "per_query": per_query,
        }
    return {"k": k, "sheet": path.as_posix(), "modes": per_mode}


def _api_search() -> Callable[[dict[str, Any]], dict[str, Any]]:
    from fastapi.testclient import TestClient

    from search import api

    client = TestClient(api.app)

    def call(params: dict[str, Any]) -> dict[str, Any]:
        response = client.get("/api/search", params=params)
        if response.status_code != 200:
            raise RelevanceError(f"{params['mode']} 검색 실패: {response.json().get('detail')}")
        return response.json()

    return call


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검색 관련성 판정 시트·P@10 (#193)")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="판정용 CSV 뽑기 (relevant 칸은 비움)")
    export.add_argument("--out", type=Path, default=DEFAULT_SHEET)
    export.add_argument("--force", action="store_true", help="이미 있는 시트를 덮어쓴다")
    scorer = sub.add_parser("score", help="채운 CSV 로 P@10·MRR")
    scorer.add_argument("sheet", type=Path, nargs="?", default=DEFAULT_SHEET)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            if args.out.exists() and not args.force:
                # 사람이 채우던 판정을 덮어쓰지 않게
                raise RelevanceError(f"{args.out} 가 이미 있다 (덮어쓰려면 --force)")
            rows = export_rows(_api_search())
            write_sheet(args.out, rows)
            print(f"{len(rows)}행 → {args.out} (relevant 칸을 사람이 채운다)")
        else:
            result = score(args.sheet)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except RelevanceError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
