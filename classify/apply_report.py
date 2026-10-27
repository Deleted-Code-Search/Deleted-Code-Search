"""100개 적용 결과 요약 - 버전별 분포, 표본에서 두 버전 일치율 (#180). 담당: 희수

`classify.apply` 의 출력 두 개(모델만 전체, LLM 포함 표본)를 읽어 보고서 숫자를 낸다.
정답이 없으므로 **정확도가 아니다.** 표본에서 두 버전이 같은 라벨을 냈는지와 어디서 갈렸는지만
센다. 성능은 버전별 기존 숫자(모델만 val 52.0%, LLM 포함 test 60.0%)만 인용한다.

실행:
    python -m classify.apply_report --model-only data/classified/model_only.jsonl \\
        --with-llm data/classified/llm_sample.jsonl --json docs/reports/apply100.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from classify.labels import MERGED_FILENAME, read_jsonl

DEFAULT_LABELS = Path("datasets") / MERGED_FILENAME


def iter_rows(path: Path) -> Iterator[dict[str, Any]]:
    """출력 파일을 한 줄씩. 모델만 출력은 수십만 줄이다."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def distribution(path: Path, labeled: set[str]) -> dict[str, Any]:
    """버전·라벨·등급·저장소별 건수, 사람 확정 500건과 겹친 건수."""
    versions: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    grades: Counter[str] = Counter()
    repos: Counter[str] = Counter()
    overlap = 0
    for row in iter_rows(path):
        versions[row["classifier_version"]] += 1
        labels[row["label"]] += 1
        grades[row["evidence_grade"]] += 1
        repos[str(row.get("repo"))] += 1
        overlap += row["record_id"] in labeled
    return {
        "n": sum(labels.values()),
        "versions": dict(versions),
        "labels": dict(labels.most_common()),
        "grades": dict(grades.most_common()),
        "repos": dict(sorted(repos.items())),
        "human_labeled_overlap": overlap,
    }


def compare(model_only: Path, with_llm: Path) -> dict[str, Any]:
    """표본에서 두 버전의 라벨 일치율 - 전체, 저장소별, 갈린 쌍.

    표본 레코드는 모델만 출력에도 있어야 한다 (같은 대상 범위). 없으면 거부한다 - 입력 파일이
    서로 다른 것이다.
    """
    sample = {row["record_id"]: row for row in iter_rows(with_llm)}
    model: dict[str, dict[str, Any]] = {}
    for row in iter_rows(model_only):
        if row["record_id"] in sample:
            model[row["record_id"]] = row
    missing = sorted(set(sample) - set(model))
    if missing:
        raise ValueError(f"모델만 출력에 없는 표본 {len(missing)}건 {missing[:3]}")

    pairs = Counter((model[rid]["label"], row["label"]) for rid, row in sample.items())
    per_repo: dict[str, Counter[str]] = {}
    for rid, row in sample.items():
        bucket = per_repo.setdefault(str(row.get("repo")), Counter())
        bucket["n"] += 1
        bucket["agree"] += model[rid]["label"] == row["label"]
    agree = sum(count for (a, b), count in pairs.items() if a == b)
    n = len(sample)
    return {
        "n": n,
        "agree": agree,
        "agreement": agree / n if n else None,
        "wilson95": _wilson(agree, n),
        "per_repo": {
            repo: {"n": c["n"], "agree": c["agree"], "agreement": c["agree"] / c["n"]}
            for repo, c in sorted(per_repo.items())
        },
        "disagreements_top": [
            {"model_only": a, "with_llm": b, "count": count}
            for (a, b), count in pairs.most_common()
            if a != b
        ][:10],
    }


def _wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """일치율의 95% Wilson 구간."""
    if not n:
        return None
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(center - half, 4), round(center + half, 4)]


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자. 실행 방법은 모듈 독스트링 "실행" 절."""
    parser = argparse.ArgumentParser(
        prog="python -m classify.apply_report",
        description="100개 적용 결과 요약 - 버전별 분포, 표본 일치율 (#180).",
    )
    parser.add_argument("--model-only", type=Path, required=True)
    parser.add_argument("--with-llm", type=Path, required=True)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--json", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """요약을 찍고 원하면 JSON 으로 남긴다."""
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    labeled = {str(row["record_id"]) for row in read_jsonl(args.labels)}
    try:
        result = {
            "model_only": distribution(args.model_only, labeled),
            "with_llm": distribution(args.with_llm, labeled),
            "sample_agreement": compare(args.model_only, args.with_llm),
        }
    except ValueError as error:
        print(f"요약할 수 없다: {error}", file=sys.stderr)
        return 2
    agreement = result["sample_agreement"]
    for name in ("model_only", "with_llm"):
        part = result[name]
        overlap = part["human_labeled_overlap"]
        print(f"{name}: {part['n']:,}건 {part['versions']} (확정 500건과 겹침 {overlap})")
        print("  라벨 " + ", ".join(f"{k} {v:,}" for k, v in part["labels"].items()))
        print("  등급 " + ", ".join(f"{k} {v:,}" for k, v in part["grades"].items()))
    print(
        f"표본 두 버전 일치 {agreement['agree']}/{agreement['n']}"
        f" ({agreement['agreement']:.1%}, 95% {agreement['wilson95']})"
    )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
