"""100개 적용 결과 요약 - 버전별 분포, 표본에서 두 버전 일치율 (#180). 담당: 희수

`classify.apply` 의 출력(모델만 전체, LLM 포함 표본 - 각각 파일 여러 개)을 읽어 보고서 숫자를
낸다. 정답이 없으므로 **정확도가 아니다.** 표본에서 두 버전이 같은 라벨을 냈는지와 어디서
갈렸는지만 센다. 성능은 버전별 기존 숫자(모델만 val 52.0%, LLM 포함 test 60.0%)만 인용한다.

학습 저장소 / 신규 저장소로 나눠 센다 (`training_repo`, 성제 결정):
    신규 저장소에서 모델만 버전이 LLM 포함 버전과 UNK·DESIGN 에서만 맞는다면, 신규 저장소의
    모델만 라벨은 "이유 미구분"으로 내린다. 그 판단에 쓰는 숫자 - 그룹마다 일치율, LLM 라벨별
    일치, LLM 이 UNK·DESIGN 이 아닌 이유를 낸 건에서의 일치, 우연 일치를 뺀 Cohen kappa.

실행:
    python -m classify.apply_report --model-only data/classified/model_only_old20.jsonl \\
        --model-only data/classified/model_only_hs.jsonl ... \\
        --with-llm data/classified/llm_old20.jsonl ... --json docs/reports/apply100.json
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
GROUPS = {True: "training_repos", False: "new_repos"}
# 이유를 구분하지 못한 쪽. 여기서만 맞는다면 이유를 맞힌 게 아니다.
UNCLEAR_LABELS = ("UNK", "DESIGN")


def iter_rows(paths: Sequence[Path]) -> Iterator[dict[str, Any]]:
    """출력 파일들을 한 줄씩. 모델만 출력은 수십만 줄이다.

    같은 `record_id` 가 두 번 나오면 거부한다 - 분포는 두 번 세고 일치율은 덮어써서 두 숫자의
    분모가 갈린다 (같은 파일을 두 번 넘긴 경우 등).
    """
    seen: set[str] = set()
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if row["record_id"] in seen:
                        raise ValueError(f"같은 record_id 가 두 번 나온다: {row['record_id']}")
                    seen.add(row["record_id"])
                    yield row


def repo_group(row: dict[str, Any]) -> str:
    """`training_repo` 플래그로 그룹 이름. 플래그가 없으면 플래그 전 출력이다 - 거부한다."""
    flag = row.get("training_repo")
    if not isinstance(flag, bool):
        raise ValueError(f"training_repo 가 없는 행 {row.get('record_id')} - apply 로 다시 만든다")
    return GROUPS[flag]


def distribution(paths: Sequence[Path], labeled: set[str]) -> dict[str, Any]:
    """버전·라벨·등급·저장소별 건수, 그룹별 라벨, 사람 확정 500건과 겹친 건수."""
    versions: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    grades: Counter[str] = Counter()
    repos: Counter[str] = Counter()
    by_group: dict[str, Counter[str]] = {name: Counter() for name in GROUPS.values()}
    overlap = 0
    for row in iter_rows(paths):
        versions[row["classifier_version"]] += 1
        labels[row["label"]] += 1
        grades[row["evidence_grade"]] += 1
        repos[str(row.get("repo"))] += 1
        by_group[repo_group(row)][row["label"]] += 1
        overlap += row["record_id"] in labeled
    return {
        "n": sum(labels.values()),
        "versions": dict(versions),
        "labels": dict(labels.most_common()),
        "labels_by_repo_group": {name: dict(c.most_common()) for name, c in by_group.items()},
        "grades": dict(grades.most_common()),
        "repos": dict(sorted(repos.items())),
        "human_labeled_overlap": overlap,
    }


def compare(model_only: Sequence[Path], with_llm: Sequence[Path]) -> dict[str, Any]:
    """표본에서 두 버전의 라벨 일치율 - 전체, 학습/신규 저장소 그룹별, 저장소별, 갈린 쌍.

    표본 레코드는 모델만 출력에도 있어야 한다 (같은 대상 범위). 없으면 거부한다 - 입력 파일이
    서로 다른 것이다. 그룹은 모델만 출력의 플래그로 나눈다.
    """
    sample = {row["record_id"]: row for row in iter_rows(with_llm)}
    model: dict[str, dict[str, Any]] = {}
    for row in iter_rows(model_only):
        if row["record_id"] in sample:
            model[row["record_id"]] = row
    missing = sorted(set(sample) - set(model))
    if missing:
        raise ValueError(f"모델만 출력에 없는 표본 {len(missing)}건 {missing[:3]}")

    pairs: Counter[tuple[str, str]] = Counter()
    group_pairs: dict[str, Counter[tuple[str, str]]] = {name: Counter() for name in GROUPS.values()}
    per_repo: dict[str, Counter[str]] = {}
    for rid, row in sample.items():
        pair = (model[rid]["label"], row["label"])
        pairs[pair] += 1
        group_pairs[repo_group(model[rid])][pair] += 1
        bucket = per_repo.setdefault(str(row.get("repo")), Counter())
        bucket["n"] += 1
        bucket["agree"] += pair[0] == pair[1]
    return {
        **agreement(pairs),
        "by_repo_group": {name: agreement(c) for name, c in group_pairs.items()},
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


def agreement(pairs: Counter[tuple[str, str]]) -> dict[str, Any]:
    """(모델만, LLM 포함) 라벨 쌍들의 일치율, Wilson 구간, kappa, LLM 라벨별 일치.

    `outside_unclear` 는 LLM 포함 버전이 UNK·DESIGN 이 아닌 이유를 낸 건에서의 일치다.
    """
    n = sum(pairs.values())
    agree = sum(count for (a, b), count in pairs.items() if a == b)
    by_llm: dict[str, Counter[str]] = {}
    for (a, b), count in pairs.items():
        bucket = by_llm.setdefault(b, Counter())
        bucket["n"] += count
        bucket["agree"] += count if a == b else 0
    outside = [c for label, c in by_llm.items() if label not in UNCLEAR_LABELS]
    outside_n = sum(c["n"] for c in outside)
    outside_agree = sum(c["agree"] for c in outside)
    return {
        "n": n,
        "agree": agree,
        "agreement": agree / n if n else None,
        "wilson95": _wilson(agree, n),
        "kappa": _kappa(pairs),
        "by_llm_label": {
            label: {"n": c["n"], "agree": c["agree"]}
            for label, c in sorted(by_llm.items(), key=lambda item: -item[1]["n"])
        },
        "outside_unclear": {
            "n": outside_n,
            "agree": outside_agree,
            "agreement": outside_agree / outside_n if outside_n else None,
        },
    }


def _kappa(pairs: Counter[tuple[str, str]]) -> float | None:
    """Cohen kappa - 두 버전의 라벨 분포만으로 우연히 맞을 비율을 뺀 일치."""
    n = sum(pairs.values())
    if not n:
        return None
    model: Counter[str] = Counter()
    llm: Counter[str] = Counter()
    for (a, b), count in pairs.items():
        model[a] += count
        llm[b] += count
    observed = sum(count for (a, b), count in pairs.items() if a == b) / n
    expected = sum(model[label] * llm[label] for label in model) / (n * n)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


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
    parser.add_argument("--model-only", type=Path, action="append", required=True, help="여러 번")
    parser.add_argument("--with-llm", type=Path, action="append", required=True, help="여러 번")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--json", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """요약을 찍고 원하면 JSON 으로 남긴다."""
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    # `read_jsonl` 은 없는 파일을 빈 목록으로 읽는다 - 경로가 틀리면 겹침이 0 으로 조용히 남는다.
    if not args.labels.is_file():
        print(f"라벨 파일이 없다: {args.labels}", file=sys.stderr)
        return 2
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
    for name in ("model_only", "with_llm"):
        part = result[name]
        overlap = part["human_labeled_overlap"]
        print(f"{name}: {part['n']:,}건 {part['versions']} (확정 500건과 겹침 {overlap})")
        for group, labels in part["labels_by_repo_group"].items():
            print(f"  {group} 라벨 " + ", ".join(f"{k} {v:,}" for k, v in labels.items()))
        print("  등급 " + ", ".join(f"{k} {v:,}" for k, v in part["grades"].items()))
    overall = result["sample_agreement"]
    for name, part in [("전체", overall), *overall["by_repo_group"].items()]:
        outside = part["outside_unclear"]
        print(
            f"표본 두 버전 일치 [{name}] {part['agree']}/{part['n']}"
            f" ({_percent(part['agreement'])}, 95% {part['wilson95']}, kappa {part['kappa']})"
            f" / LLM 이 UNK·DESIGN 아닌 건 {outside['agree']}/{outside['n']}"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


if __name__ == "__main__":
    raise SystemExit(main())
