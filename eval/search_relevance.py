"""검색 관련성 판정 시트와 Precision@10 (#193, CHARTER §9 8주차, §10.3). 담당: 성제

절차 (`docs/reports/search_relevance.md`):
1) `export`: 질의 10개 × (keyword, semantic) 상위 10개를 뽑아
   **블라인드 시트**와 **키 파일**로 나눈다.
   - 시트(`search_relevance_sheet.csv`): 모드·순위 칸이 없다. 질의마다 두 모드 결과를 합쳐 같은
     레코드는 한 번만 넣고, 고정 시드로 섞은 순서로 둔다. 판정자 칸(`relevant_sj`, `relevant_hs`)은
     비워 둔다 - **판정은 사람이 한다** (ADR-005 정신: 모델·도구가 정답을 채우지 않는다).
   - 키(`search_relevance_key.json`): 질의·모드별 순위대로의 record_id.
     판정이 끝날 때까지 열지 않는다.
2) `score`: 채운 시트 + 키 → 판정자별·모드별 P@10(질의별 평균)과 MRR(첫 관련 순위 역수 평균).
   두 판정자가 다 채웠으면 판정 일치도(일치율·Cohen's kappa)와 두 사람 평균도 낸다.

질의는 자연어 8개 + 코드 조각 2개다. 의미 검색에는 문장을 그대로, 키워드 검색에는 같은 뜻의 낱말
(`keyword`)을 넣는다 - 긴 문장을 FTS 에 그대로 넣으면 낱말 전부 AND 라 0건이 되기 쉽다. 이것은
키워드 쪽에 유리한 설정이다(사람이 낱말을 골라 준다). §10.3 의 질의 30개(자연어 15 + 코드 15)보다
적다 - 8주차 1차 측정이다. 처음 넣었던 `load_config` 코드 질의는 뺐다: 시연 예시로 "표본에서 결과가
좋은 것"을 골랐던 질의라 평가에 넣으면 의미 검색에 유리하게 치우친다 (#194 희수 리뷰).

판정 값: 1/0 (y/n, o/x 도 받는다). 판정자 칸이 일부만 차 있으면 score 는 멈춘다(전부 빈 판정자는
아직 안 한 것으로 보고 건너뛴다).

실행:
    python -m eval.search_relevance export            # 색인·임베딩 필요
    python -m eval.search_relevance score
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

K = 10
SEED = 20261031
DEFAULT_SHEET = Path("docs") / "reports" / "search_relevance_sheet.csv"
DEFAULT_KEY = Path("docs") / "reports" / "search_relevance_key.json"
MODES = ("keyword", "semantic")
# 판정자 (칸 이름 접미사 → 이름). 작성자(성제) 말고 한 명 더 (#194 희수 리뷰)
JUDGES = {"sj": "성제", "hs": "희수"}
JUDGE_COLUMNS = tuple(f"relevant_{judge}" for judge in JUDGES)
COLUMNS = (
    "item",
    "query_id",
    "query",
    "record_id",
    "repo",
    "file_path",
    "function_name",
    "commit_first_line",
    *JUDGE_COLUMNS,
)
TRUE = {"1", "y", "yes", "o", "true"}
FALSE = {"0", "n", "no", "x", "false"}
# 스프레드시트가 수식으로 읽는 첫 글자 (CSV 따옴표로는 막히지 않는다)
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

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
        "def chunks(items, size):\n    for i in range(0, len(items), size):\n"
        "        yield items[i:i + size]",
        "chunks",
    ),
    (
        "async def fetch(session, url):\n    async with session.get(url) as r:\n"
        "        return await r.json()",
        "fetch session",
    ),
)


class RelevanceError(RuntimeError):
    """판정 시트·키가 비었거나 모양이 다르다."""


def query_id(number: int) -> str:
    return f"q{number:02d}"


def safe_cell(value: Any) -> Any:
    """스프레드시트 수식 주입 막기 - 수식 첫 글자면 앞에 ' 를 붙인다 (CodeRabbit #194)."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


def export(
    search: Callable[[dict[str, Any]], dict[str, Any]], seed: int = SEED
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """search(params) → /api/search 응답. (시트 행, 키) 를 돌려준다."""
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    key: dict[str, Any] = {"k": K, "seed": seed, "modes": list(MODES), "queries": []}
    for number, (query, keyword) in enumerate(QUERIES, 1):
        qid = query_id(number)
        ranked: dict[str, list[str]] = {}
        hits: dict[str, dict[str, Any]] = {}
        for mode in MODES:
            q = keyword if mode == "keyword" else query
            body = search({"q": q, "mode": mode, "limit": K})
            ranked[mode] = [hit["id"] for hit in body["results"][:K]]
            for hit in body["results"][:K]:
                hits.setdefault(hit["id"], hit)  # 두 모드에 나온 레코드는 한 번만
        key["queries"].append({"id": qid, "query": query, "keyword": keyword, "ranked": ranked})
        order = sorted(hits)  # 응답 순서를 지우고 고정 시드로 섞는다
        rng.shuffle(order)
        for rid in order:
            hit = hits[rid]
            rows.append(
                {
                    "query_id": qid,
                    "query": query,
                    "record_id": rid,
                    "repo": hit["repo"],
                    "file_path": hit.get("file_path") or "",
                    "function_name": hit.get("function_name") or "",
                    "commit_first_line": hit.get("commit_first_line") or "",
                    **dict.fromkeys(JUDGE_COLUMNS, ""),
                }
            )
    for item, row in enumerate(rows, 1):
        row["item"] = item
    return rows, key


def write_sheet(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 열어도 한글·기호가 깨지지 않게
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: safe_cell(row[col]) for col in COLUMNS})


def write_key(path: Path, key: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def _judgement(value: str, where: str) -> bool | None:
    value = value.strip().lower()
    if not value:
        return None
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    raise RelevanceError(f"{where}: 알 수 없는 판정 값 {value!r} (1/0)")


def read_sheet(path: Path) -> dict[tuple[str, str], dict[str, bool | None]]:
    """시트 → {(query_id, record_id): {판정자: 판정 | None}}."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise RelevanceError(f"{path} 에 칸이 없다: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise RelevanceError(f"{path} 가 비었다")
    judged: dict[tuple[str, str], dict[str, bool | None]] = {}
    for line, row in enumerate(rows, 2):
        cell = (row["query_id"], row["record_id"])
        if cell in judged:
            raise RelevanceError(f"{line}행: 같은 질의·레코드가 두 번 있다 {cell}")
        judged[cell] = {
            judge: _judgement(row[f"relevant_{judge}"], f"{line}행 relevant_{judge}")
            for judge in JUDGES
        }
    return judged


def _check_key(key: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """키가 지금의 QUERIES·MODES 와 같은지 - 빠진 질의·모드가 있으면 거부 (CodeRabbit #194)."""
    by_id = {entry["id"]: entry for entry in key.get("queries", [])}
    ranked = {}
    for number, (query, _) in enumerate(QUERIES, 1):
        qid = query_id(number)
        entry = by_id.get(qid)
        if entry is None or entry["query"] != query:
            raise RelevanceError(f"키에 질의 {qid} 가 없거나 질의가 바뀌었다 - export 를 다시")
        absent = [mode for mode in MODES if mode not in entry["ranked"]]
        if absent:
            raise RelevanceError(f"키의 {qid} 에 모드가 없다: {absent}")
        ranked[qid] = entry["ranked"]
    extra = set(by_id) - set(ranked)
    if extra:
        raise RelevanceError(f"키에 모르는 질의가 있다: {sorted(extra)}")
    return ranked


def _mode_scores(
    ranked: dict[str, dict[str, list[str]]],
    judged: dict[tuple[str, str], dict[str, bool | None]],
    judge: str,
    k: int,
) -> dict[str, Any]:
    per_mode = {}
    for mode in MODES:
        precisions = []
        reciprocal = []
        per_query = {}
        for qid, modes in ranked.items():
            ranks = [
                rank for rank, rid in enumerate(modes[mode][:k], 1) if judged[(qid, rid)][judge]
            ]
            # 결과가 k 개보다 적으면 모자란 칸은 무관으로 센다 - 분모는 늘 k
            precision = len(ranks) / k
            precisions.append(precision)
            reciprocal.append(1 / ranks[0] if ranks else 0.0)
            per_query[qid] = {"p_at_k": precision, "first_relevant": ranks[0] if ranks else None}
        per_mode[mode] = {
            "queries": len(precisions),
            "p_at_k": round(sum(precisions) / len(precisions), 3),
            "mrr": round(sum(reciprocal) / len(reciprocal), 3),
            "per_query": per_query,
        }
    return per_mode


def agreement(pairs: Sequence[tuple[bool, bool]]) -> dict[str, Any]:
    """두 판정자의 일치율과 Cohen's kappa."""
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    p_a = sum(a for a, _ in pairs) / n
    p_b = sum(b for _, b in pairs) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    kappa = 1.0 if expected == 1 else (observed - expected) / (1 - expected)
    return {"items": n, "percent": round(observed, 3), "cohen_kappa": round(kappa, 3)}


def score(sheet: Path, key_path: Path, k: int = K) -> dict[str, Any]:
    """채운 시트 + 키 → 판정자별·모드별 P@k·MRR, 둘 다 채웠으면 일치도와 평균."""
    ranked = _check_key(json.loads(key_path.read_text(encoding="utf-8")))
    judged = read_sheet(sheet)
    needed = {(qid, rid) for qid, modes in ranked.items() for ids in modes.values() for rid in ids}
    absent = needed - set(judged)
    if absent:
        raise RelevanceError(f"시트에 키의 레코드 {len(absent)}건이 없다 (예: {min(absent)})")
    done = []
    for judge, name in JUDGES.items():
        values = [judged[cell][judge] for cell in needed]
        blank = sum(value is None for value in values)
        if blank == len(values):
            continue  # 아직 판정 안 함
        if blank:
            raise RelevanceError(f"relevant_{judge}({name}) 칸에 빈칸이 {blank}개 남았다")
        done.append(judge)
    if not done:
        raise RelevanceError(f"{sheet} 에 판정이 하나도 없다 ({', '.join(JUDGE_COLUMNS)})")
    result: dict[str, Any] = {
        "k": k,
        "sheet": sheet.as_posix(),
        "key": key_path.as_posix(),
        "judges": {judge: JUDGES[judge] for judge in done},
        "by_judge": {judge: _mode_scores(ranked, judged, judge, k) for judge in done},
    }
    if len(done) == len(JUDGES):
        cells = sorted(needed)
        a, b = JUDGES
        result["agreement"] = agreement([(judged[c][a], judged[c][b]) for c in cells])  # type: ignore[misc]
        result["mean"] = {
            mode: {
                metric: round(sum(result["by_judge"][j][mode][metric] for j in done) / len(done), 3)
                for metric in ("p_at_k", "mrr")
            }
            for mode in MODES
        }
    return result


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
    parser = argparse.ArgumentParser(description="검색 관련성 블라인드 판정 시트·P@10 (#193)")
    sub = parser.add_subparsers(dest="command", required=True)
    exporter = sub.add_parser("export", help="블라인드 시트 + 키 뽑기 (판정 칸은 비움)")
    exporter.add_argument("--out", type=Path, default=DEFAULT_SHEET)
    exporter.add_argument("--key", type=Path, default=DEFAULT_KEY)
    exporter.add_argument("--force", action="store_true", help="이미 있는 시트·키를 덮어쓴다")
    scorer = sub.add_parser("score", help="채운 시트 + 키로 P@10·MRR·일치도")
    scorer.add_argument("sheet", type=Path, nargs="?", default=DEFAULT_SHEET)
    scorer.add_argument("--key", type=Path, default=DEFAULT_KEY)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            existing = [p for p in (args.out, args.key) if p.exists()]
            if existing and not args.force:
                # 사람이 채우던 판정을 덮어쓰지 않게
                raise RelevanceError(f"{existing[0]} 가 이미 있다 (덮어쓰려면 --force)")
            rows, key = export(_api_search())
            write_sheet(args.out, rows)
            write_key(args.key, key)
            print(
                f"{len(rows)}행 → {args.out} "
                f"(판정 칸 {', '.join(JUDGE_COLUMNS)} 을 사람이 채운다)\n"
                f"키 → {args.key} (판정이 끝날 때까지 열지 않는다)"
            )
        else:
            result = score(args.sheet, args.key)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except RelevanceError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
