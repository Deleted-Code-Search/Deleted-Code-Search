"""통계 대시보드 집계 (#93, CHARTER §4.2 "통계 대시보드", §10.5). 담당: 성제

Release `dataset-v0.9` 의 jsonl.gz 를 한 줄씩 읽어 대시보드가 쓰는 집계값만 낸다. 레코드 원문은
출력에 넣지 않는다. 표준 라이브러리(gzip, json)로만 읽는다 - pyarrow·pandas 는 일부 노트북에서
DLL 이 막힌다.

지키는 판정:
    - 이유 분포·근거 등급 분포는 확정 500건(HUMAN)과 LLM 표본(MODEL_LLM)으로만 낸다. 모델만
      (MODEL_ONLY) 라벨은 이유 분포로 쓰지 않는다 (판정 2026-10-28, docs/reports/apply100.md §3).
      그래서 여기서는 MODEL_ONLY 의 라벨을 아예 세지 않는다
    - 모델 성능 숫자는 docs/reports 의 json 에서 읽는다. 모델만 버전에 test 60.0% 를 붙이지 않는다

입력:
    --data       jsonl.gz 폴더 (기본 data/export/dataset-v0.9)
    --dataset    datasets/dataset-v0.9 (repos.csv, summary.json, training_repos.txt)
    --repo-list  docs/repo_final100.csv (recent_only)
    --reports    docs/reports (gate2_test.json, classifier_val.json, filter_precision_v2.json,
                 gate2_h.json)
    --runs       pipeline.run 의 *_run.json 폴더 (여러 번 줄 수 있다). 없으면 시스템 평가는 비운다

실행:
    python -m search.stats
    python -m search.stats --runs data/run100_sj --out docs/reports/dashboard_stats.json
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from classify.labels import EVIDENCE_GRADES, REASON_LABELS, RECOVERED_GRADES

SCHEMA_VERSION = "dashboard_stats/1"
DATASET_VERSION = "dataset-v0.9"
DEFAULT_DATA = Path("data") / "export" / DATASET_VERSION
DEFAULT_DATASET = Path("datasets") / DATASET_VERSION
DEFAULT_REPO_LIST = Path("docs") / "repo_final100.csv"
DEFAULT_REPORTS = Path("docs") / "reports"
DEFAULT_OUT = DEFAULT_REPORTS / "dashboard_stats.json"
DOWNLOAD_HINT = (
    f'gh release download {DATASET_VERSION} -p "*.jsonl.gz" -D data/export/{DATASET_VERSION}'
)

HUMAN = "HUMAN"
MODEL_LLM = "MODEL_LLM"
MODEL_ONLY = "MODEL_ONLY"
SOURCES = (HUMAN, MODEL_LLM, MODEL_ONLY, "UNDISTINGUISHED", "NOT_CLASSIFIED")
# 이유·등급 분포를 그리는 출처. MODEL_ONLY 는 판정(2026-10-28)에 따라 넣지 않는다.
DISTRIBUTION_SOURCES = (HUMAN, MODEL_LLM)
KINDS = ("FULL_FUNCTION", "PARTIAL")
KEPT = "KEPT"
SHARD_GLOB = "*-of-*.jsonl.gz"
# 모델만 버전의 검증 정확도 행 (classifier_val.json experiments). 이름이 바뀌면 찾지 못해 멈춘다.
MODEL_ONLY_EXPERIMENT = "모델만: + 화면 모양 + 추가 헝크 (m2)"


class StatsError(RuntimeError):
    """입력이 없거나 집계가 기대와 다르다."""


# ---------------------------------------------------------------------------
# 레코드 집계


def _empty_partial() -> dict[str, Any]:
    return {
        "records": 0,
        "by_kind": Counter(),
        "by_source": Counter(),
        "with_context": 0,
        "by_year_kind": Counter(),
        "reason": {source: Counter() for source in DISTRIBUTION_SOURCES},
        "grade": {source: Counter() for source in DISTRIBUTION_SOURCES},
        "repos": {},
    }


def _repo_entry(partial: dict[str, Any], repo: str) -> dict[str, Any]:
    if repo not in partial["repos"]:
        partial["repos"][repo] = {
            "kept": 0,
            "by_kind": Counter(),
            "by_source": Counter(),
            "recovered": Counter(),
            "training_repo": set(),
        }
    return partial["repos"][repo]


def add_record(partial: dict[str, Any], record: dict[str, Any]) -> None:
    """레코드 하나를 집계에 더한다. Release 는 KEPT 행만 낸다 - 다른 상태가 오면 멈춘다."""
    if record.get("filter_status") != KEPT:
        raise StatsError(f"KEPT 가 아닌 행 {record.get('id')}: {record.get('filter_status')}")
    kind = record["deletion_kind"]
    classification = record.get("classification") or {}
    source = classification.get("source")
    if source not in SOURCES:
        raise StatsError(f"알 수 없는 classification.source {source!r} (행 {record.get('id')})")
    year = (record.get("author_date") or "")[:4] or "unknown"

    partial["records"] += 1
    partial["by_kind"][kind] += 1
    partial["by_source"][source] += 1
    partial["by_year_kind"][(year, kind)] += 1
    # 맥락 결합 여부는 pipeline/export.py 와 같은 기준이다 - 결합되면 pr_labels 가 None 이 아니다.
    if (record.get("context") or {}).get("pr_labels") is not None:
        partial["with_context"] += 1

    repo = _repo_entry(partial, record["repo"])
    repo["kept"] += 1
    repo["by_kind"][kind] += 1
    repo["by_source"][source] += 1
    if "training_repo" in classification:
        repo["training_repo"].add(bool(classification["training_repo"]))

    if source in DISTRIBUTION_SOURCES:
        reason = record.get("reason") or {}
        grade = reason.get("evidence_grade")
        partial["reason"][source][reason.get("label")] += 1
        partial["grade"][source][grade] += 1
        if grade in RECOVERED_GRADES:
            repo["recovered"][source] += 1


def scan_file(path: Path) -> dict[str, Any]:
    """jsonl.gz 하나를 한 줄씩 읽어 부분 집계. 줄은 바이트 `\\n` 으로만 나눈다 (U+2028, #146)."""
    partial = _empty_partial()
    with gzip.open(path, "rb") as handle:
        for line in handle:
            if line.strip():
                add_record(partial, json.loads(line))
    return partial


def merge(partials: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = _empty_partial()
    for part in partials:
        total["records"] += part["records"]
        total["with_context"] += part["with_context"]
        for key in ("by_kind", "by_source", "by_year_kind"):
            total[key].update(part[key])
        for key in ("reason", "grade"):
            for source in DISTRIBUTION_SOURCES:
                total[key][source].update(part[key][source])
        for name, repo in part["repos"].items():
            into = _repo_entry(total, name)
            into["kept"] += repo["kept"]
            into["training_repo"] |= repo["training_repo"]
            for key in ("by_kind", "by_source", "recovered"):
                into[key].update(repo[key])
    return total


def scan_dir(data_dir: Path, jobs: int = 1) -> tuple[dict[str, Any], list[Path]]:
    # 저장소별 shard 만 읽는다. 같은 폴더의 main500-v0.9.jsonl.gz 는 확정 500건 사본이라 겹친다.
    files = sorted(data_dir.glob(SHARD_GLOB)) if data_dir.is_dir() else []
    if not files:
        raise StatsError(
            f"{data_dir} 에 {SHARD_GLOB} 가 없다. Release 를 먼저 받는다:\n    {DOWNLOAD_HINT}"
        )
    if jobs <= 1:
        return merge(scan_file(path) for path in files), files
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return merge(pool.map(scan_file, files)), files


# ---------------------------------------------------------------------------
# 보조 입력


def read_repos_csv(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["repo"]: {
            "records": int(row["records"]),
            "noise_rows": int(row["noise_rows"]),
            "training_repo": row["training_repo"] == "True",
        }
        for row in rows
    }


def read_recent_only(path: Path) -> dict[str, str | None]:
    """recent_only 저장소 → 채굴 시작 연도. 목록에 없으면 빈 dict."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            row["repo"]: row.get("mining_since_year") or None
            for row in csv.DictReader(handle)
            if row.get("recent_only") == "true"
        }


def wilson(correct: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = correct / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(centre - half, 4), round(centre + half, 4)]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StatsError(f"성능 숫자 파일이 없다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _card(
    key: str, label: str, value: float, n: int | None, source: str, note: str, ci95: Any = None
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": round(value, 4),
        "n": n,
        "ci95": ci95,
        "source": source,
        "note": note,
    }


def performance_cards(reports: Path) -> list[dict[str, Any]]:
    """모델·필터 성능 카드. 숫자는 전부 docs/reports 의 json 에서 읽는다."""
    gate2 = _load_json(reports / "gate2_test.json")
    val = _load_json(reports / "classifier_val.json")
    precision = _load_json(reports / "filter_precision_v2.json")
    gate2_h = _load_json(reports / "gate2_h.json")

    methods = gate2["methods"]
    ours = methods["ours"]
    model_only = [e for e in val["experiments"] if e["name"] == MODEL_ONLY_EXPERIMENT]
    if len(model_only) != 1 or model_only[0]["val_accuracy"] is None:
        raise StatsError(f"classifier_val.json 에서 '{MODEL_ONLY_EXPERIMENT}' 행을 찾지 못했다")
    val_n = val["val"]["ours"]["n"]
    agreement = gate2_h["agreement"]
    return [
        _card(
            "classifier_test",
            "분류기 test 정확도 (r1+m2+c3+llm)",
            ours["accuracy"],
            ours["n"],
            "docs/reports/gate2_test.json methods.ours",
            "LLM 포함 버전. 학습과 같은 20개 저장소의 test 100건",
            wilson(ours["correct"], ours["n"]),
        ),
        _card(
            "model_only_val",
            "모델만 val 정확도 (r1+m2)",
            model_only[0]["val_accuracy"],
            val_n,
            f"docs/reports/classifier_val.json experiments '{MODEL_ONLY_EXPERIMENT}'",
            "모델만 라벨의 숫자는 이것뿐이다. test 60.0% 를 붙이지 않는다",
        ),
        _card(
            "baseline_a",
            "기준선 A (커밋 메시지 키워드)",
            methods["keyword_a"]["accuracy"],
            methods["keyword_a"]["n"],
            "docs/reports/gate2_test.json methods.keyword_a",
            "test 100건",
            wilson(methods["keyword_a"]["correct"], methods["keyword_a"]["n"]),
        ),
        _card(
            "baseline_b",
            "기준선 B (LLM 직접 질의)",
            methods["llm_b"]["accuracy"],
            methods["llm_b"]["n"],
            "docs/reports/gate2_test.json methods.llm_b",
            "test 100건",
            wilson(methods["llm_b"]["correct"], methods["llm_b"]["n"]),
        ),
        _card(
            "human_agreement",
            "사람 일치 H",
            gate2_h["H"],
            agreement["denominator"],
            "docs/reports/gate2_h.json H",
            "라벨러 두 명이 test 에서 같은 이유를 낸 비율",
            [round(x, 4) for x in gate2_h["wilson95"]],
        ),
        _card(
            "filter_precision",
            "필터 정밀도 (KEPT 표본)",
            precision["precision"],
            precision["sample_counts"]["KEPT"],
            "docs/reports/filter_precision_v2.json precision",
            f"규칙 {precision['key']['filter_rule_version']} 2차 표본. 목표 90%",
            [round(x, 4) for x in precision["precision_ci95"]],
        ),
    ]


def read_runs(run_dirs: Sequence[Path]) -> dict[str, Any]:
    """pipeline.run 실행 기록. 같은 저장소가 여러 번 있으면 가장 늦게 끝난 것을 쓴다."""
    by_repo: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        for path in sorted(run_dir.glob("*_run.json")):
            meta = json.loads(path.read_text(encoding="utf-8"))
            previous = by_repo.get(meta["repo"])
            if previous is None or (meta.get("finished_at") or "") > (
                previous.get("finished_at") or ""
            ):
                by_repo[meta["repo"]] = meta
    if not by_repo:
        return {"available": False, "n": 0, "note": "실행 기록 없음", "runs": [], "summary": None}
    runs = [
        {
            "repo": meta["repo"],
            "status": meta["status"],
            "elapsed_seconds": meta.get("elapsed_seconds"),
            "attempt_count": meta.get("attempt_count"),
            "commit_count": meta.get("commit_count"),
            "recent_only": meta.get("recent_only"),
            "filter_rule_version": meta.get("filter_rule_version"),
            "failure_stage": meta.get("failure_stage"),
        }
        for meta in sorted(by_repo.values(), key=lambda m: m["repo"])
    ]
    elapsed = [r["elapsed_seconds"] for r in runs if r["elapsed_seconds"] is not None]
    failed = sum(1 for r in runs if r["status"] != "SUCCESS")
    retried = sum(1 for r in runs if (r["attempt_count"] or 0) > 1)
    return {
        "available": True,
        "n": len(runs),
        "note": "이 컴퓨터에 있는 실행 기록만 센다. 저장소 100개 전부가 아닐 수 있다",
        "runs": runs,
        "summary": {
            "repos": len(runs),
            "failed": failed,
            "failure_rate": round(failed / len(runs), 4),
            "retried": retried,
            "total_elapsed_seconds": round(sum(elapsed), 1),
            "median_elapsed_seconds": round(sorted(elapsed)[len(elapsed) // 2], 1)
            if elapsed
            else None,
            "max_elapsed_seconds": round(max(elapsed), 1) if elapsed else None,
        },
    }


# ---------------------------------------------------------------------------
# 출력


def _ordered(counter: Counter, keys: Sequence[str]) -> dict[str, int]:
    extra = sorted(str(k) for k in counter if k not in keys)
    if extra:
        raise StatsError(f"정해진 값 밖: {extra}")
    return {key: counter.get(key, 0) for key in keys}


def build_stats(
    totals: dict[str, Any],
    files: Sequence[Path],
    dataset_dir: Path,
    repo_list: Path,
    reports: Path,
    run_dirs: Sequence[Path] = (),
) -> dict[str, Any]:
    repos_csv = read_repos_csv(dataset_dir / "repos.csv")
    summary = json.loads((dataset_dir / "summary.json").read_text(encoding="utf-8"))
    training = {
        line.strip()
        for line in (dataset_dir / "training_repos.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    recent_only = read_recent_only(repo_list)

    noise_rows = sum(row["noise_rows"] for row in repos_csv.values())
    by_kind = _ordered(totals["by_kind"], KINDS)
    by_source = _ordered(totals["by_source"], SOURCES)

    repo_rows = []
    for name in sorted(set(repos_csv) | set(totals["repos"])):
        seen = totals["repos"].get(name) or _repo_entry(_empty_partial(), name)
        llm_n = seen["by_source"].get(MODEL_LLM, 0)
        llm_recovered = seen["recovered"].get(MODEL_LLM, 0)
        repo_rows.append(
            {
                "repo": name,
                "kept": seen["kept"],
                "full_function": seen["by_kind"].get("FULL_FUNCTION", 0),
                "partial": seen["by_kind"].get("PARTIAL", 0),
                "noise_rows": repos_csv.get(name, {}).get("noise_rows"),
                "training_repo": name in training,
                "recent_only": name in recent_only,
                "human_n": seen["by_source"].get(HUMAN, 0),
                "llm_n": llm_n,
                "llm_recovered": llm_recovered,
                "llm_recovery_rate": round(llm_recovered / llm_n, 4) if llm_n else None,
            }
        )

    years = sorted({year for year, _ in totals["by_year_kind"]})
    yearly = [
        {"year": year, **{kind: totals["by_year_kind"].get((year, kind), 0) for kind in KINDS}}
        for year in years
    ]

    def distribution(key: str, keys: Sequence[str]) -> dict[str, Any]:
        return {
            source: {
                "n": sum(totals[key][source].values()),
                "counts": _ordered(totals[key][source], keys),
            }
            for source in DISTRIBUTION_SOURCES
        }

    grade = distribution("grade", EVIDENCE_GRADES)
    human_grade = grade[HUMAN]
    explicit_rate = (
        round(human_grade["counts"]["EXPLICIT"] / human_grade["n"], 4) if human_grade["n"] else None
    )

    checks = _checks(totals, by_kind, by_source, repos_csv, summary, training, repo_rows)
    data_src = f"Release {DATASET_VERSION} jsonl.gz {len(files)}개"
    return {
        "schema": SCHEMA_VERSION,
        "dataset_version": summary.get("dataset_version", DATASET_VERSION),
        "filter_rule_version": summary.get("filter_rule_version"),
        "headline": {
            "source": f"{data_src} + datasets/{DATASET_VERSION}/repos.csv (noise_rows)",
            "repos": len(repo_rows),
            "deleted_before_filter": totals["records"] + noise_rows,
            "noise_rows": noise_rows,
            "kept": totals["records"],
            "full_function": by_kind["FULL_FUNCTION"],
            "partial": by_kind["PARTIAL"],
            "with_context": totals["with_context"],
        },
        "label_sources": {
            "source": f"{data_src} classification.source",
            "n": totals["records"],
            "counts": by_source,
        },
        "reason_distribution": {
            "source": f"{data_src} reason.label (HUMAN·MODEL_LLM 만)",
            "excluded_sources": [s for s in SOURCES if s not in DISTRIBUTION_SOURCES],
            "note": "모델만(MODEL_ONLY) 라벨은 이유 분포로 쓰지 않는다 (판정 2026-10-28, "
            "docs/reports/apply100.md §3)",
            "labels": list(REASON_LABELS),
            **distribution("reason", REASON_LABELS),
        },
        "grade_distribution": {
            "source": f"{data_src} reason.evidence_grade (HUMAN·MODEL_LLM 만)",
            "note": "분류기 등급은 보수적이다. EXPLICIT 비율은 확정 500건 기준으로 보고한다",
            "grades": list(EVIDENCE_GRADES),
            "explicit_rate_human": explicit_rate,
            **grade,
        },
        "repos": {
            "source": f"{data_src} + repos.csv (noise_rows) + training_repos.txt "
            f"+ {repo_list.as_posix()} (recent_only)",
            "n": len(repo_rows),
            "note": "회수율 = LLM 표본 중 EXPLICIT+INFERRED 비율. 저장소당 표본이 30건 안팎이라 "
            "거칠다 (95% 구간 폭 ±18%p 정도)",
            "rows": repo_rows,
        },
        "yearly": {
            "source": f"{data_src} author_date 연도",
            "n": totals["records"],
            "note": f"recent_only 저장소 {len(recent_only)}개는 2015년 이후 커밋만 순회했다 - "
            "2015년 전 건수는 그 저장소들이 빠진 값이다",
            "recent_only_repos": sorted(recent_only),
            "rows": yearly,
        },
        "performance": {
            "source": reports.as_posix() + "/*.json",
            "cards": performance_cards(reports),
        },
        "system": {**read_runs(run_dirs), "expected_repos": len(repo_rows)},
        "checks": checks,
    }


def _checks(
    totals: dict[str, Any],
    by_kind: dict[str, int],
    by_source: dict[str, int],
    repos_csv: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    training: set[str],
    repo_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """집계값을 summary.json·repos.csv 와 맞춰 본다. 다른 것이 있으면 `ok = false`."""
    expected = summary.get("totals", {})
    mismatches = []

    def compare(name: str, got: Any, want: Any) -> None:
        if got != want:
            mismatches.append({"field": name, "got": got, "summary": want})

    compare("records", totals["records"], expected.get("records"))
    compare("with_context", totals["with_context"], expected.get("with_context"))
    for kind, count in by_kind.items():
        compare(f"by_kind.{kind}", count, expected.get("by_kind", {}).get(kind))
    for source, count in by_source.items():
        compare(f"by_source.{source}", count, expected.get("by_source", {}).get(source))
    for row in repo_rows:
        want = repos_csv.get(row["repo"], {}).get("records")
        compare(f"repos.{row['repo']}.records", row["kept"], want)
        seen = totals["repos"].get(row["repo"], {}).get("training_repo", set())
        if seen and seen != {row["training_repo"]}:
            mismatches.append({"field": f"repos.{row['repo']}.training_repo", "got": sorted(seen)})
    return {"ok": not mismatches, "against": "summary.json, repos.csv", "mismatches": mismatches}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--repo-list", type=Path, default=DEFAULT_REPO_LIST)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--runs", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args(argv)

    try:
        totals, files = scan_dir(args.data, args.jobs)
        stats = build_stats(totals, files, args.dataset, args.repo_list, args.reports, args.runs)
    except StatsError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    head = stats["headline"]
    print(
        f"{args.out}: 저장소 {head['repos']} / 필터 전 {head['deleted_before_filter']:,} / "
        f"KEPT {head['kept']:,} / checks ok={stats['checks']['ok']}"
    )
    if not stats["checks"]["ok"]:
        print(json.dumps(stats["checks"]["mismatches"][:20], ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
