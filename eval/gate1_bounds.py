"""게이트 1 회수율의 범위 — 라벨러 불일치를 양 극단으로 해소했을 때 (이슈 #78). 담당: 성제 (sj)

왜 필요한가:
    `eval/gate1.py`(#37)는 #76 병합 규칙(두 라벨 중 약한 등급, 신뢰도 min) 하나로 회수율을 낸다.
    등급이 갈린 레코드가 많으면 그 규칙 하나가 판정을 정해 버리는지, 데이터가 정하는지 구분이
    안 된다. 그래서 같은 `labels[]` 원본 쌍을 반대 극단(강한 등급 채택)으로도 병합해 본다.
    두 극단의 판정이 같은 구간은 불일치를 어떻게 해소하든 판정이 바뀌지 않는다.

무엇을 바꾸지 않나:
    기준(60/40)·구간(EXPLICIT만 / +INFERRED≥0.5 / +INFERRED≥0.8)·판정 문구·경계 비교는
    `eval/gate1.py`의 것을 그대로 import 한다. `eval/gate1.py`, `eval/gate1_merge.py`는 읽기만 한다.
    하한은 #76 규칙 그 자체이므로 병합 파일의 `final`로 잰 `eval/gate1.py` 값과 같아야 한다.
    같은지를 리포트에 남긴다 (`lower_matches_gate1`).

두 극단:
    하한 — 약한 등급 채택 (#76 규칙). 신뢰도는 전 라벨의 min.
    상한 — 강한 등급 채택 (UNKNOWN < INFERRED < EXPLICIT). 신뢰도는 채택된 등급을 준 라벨의 값,
          그 등급을 준 라벨이 여럿이면 max.

실행:
    python -m eval.gate1_bounds datasets/labels/gate1_pre200_merged.jsonl \\
        --json-out docs/reports/gate1_pre200_bounds.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from eval import gate1

# 등급 서열. 숫자가 클수록 강하다 (eval/gate1_merge.py 의 _GRADE_RANK 와 같은 순서).
GRADE_RANK: dict[str, int] = {"UNKNOWN": 0, "INFERRED": 1, "EXPLICIT": 2}
_SHORT = {"EXPLICIT": "E", "INFERRED": "I", "UNKNOWN": "U"}

# 하한·상한 차이를 만든 원인 유형. 등급이 같은데 신뢰도만 달라 차이가 난 경우도 따로 센다.
DIFF_TYPES: tuple[str, ...] = ("E↔I", "E↔U", "I↔U", "I↔I(신뢰도)")


@dataclass(frozen=True)
class Resolution:
    grade: str | None
    confidence: float | None


def _confidence(label: dict[str, Any]) -> float | None:
    value = label.get("confidence")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _graded(labels: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [label for label in labels if label.get("evidence_grade") in GRADE_RANK]


def resolve_lower(labels: Sequence[dict[str, Any]]) -> Resolution:
    """약한 등급 채택, 신뢰도 min (#76 규칙). 신뢰도가 하나라도 없으면 None."""
    graded = _graded(labels)
    if not graded:
        return Resolution(None, None)
    grade = min((label["evidence_grade"] for label in graded), key=GRADE_RANK.__getitem__)
    values = [_confidence(label) for label in graded]
    confidence = min(values) if all(value is not None for value in values) else None
    return Resolution(grade, confidence)


def resolve_upper(labels: Sequence[dict[str, Any]]) -> Resolution:
    """강한 등급 채택, 신뢰도는 채택된 등급을 준 라벨의 값 (여럿이면 max)."""
    graded = _graded(labels)
    if not graded:
        return Resolution(None, None)
    grade = max((label["evidence_grade"] for label in graded), key=GRADE_RANK.__getitem__)
    values = [_confidence(label) for label in graded if label["evidence_grade"] == grade]
    known = [value for value in values if value is not None]
    return Resolution(grade, max(known) if known else None)


def diff_type(labels: Sequence[dict[str, Any]]) -> str:
    """하한·상한을 가를 수 있는 불일치 유형. 등급이 같으면 신뢰도 차이다."""
    grades = sorted(
        {label["evidence_grade"] for label in _graded(labels)},
        key=GRADE_RANK.__getitem__,
        reverse=True,
    )
    if len(grades) == 1:
        return f"{_SHORT[grades[0]]}↔{_SHORT[grades[0]]}(신뢰도)"
    return f"{_SHORT[grades[0]]}↔{_SHORT[grades[-1]]}"


@dataclass
class TierBound:
    tier: gate1.Tier
    lower: int
    upper: int
    resolved: int
    diff_types: Counter[str]

    @property
    def lower_verdict(self) -> str:
        return gate1.gate1_verdict(self.lower, self.resolved)

    @property
    def upper_verdict(self) -> str:
        return gate1.gate1_verdict(self.upper, self.resolved)

    @property
    def same_verdict(self) -> bool:
        return self.lower_verdict == self.upper_verdict


def compute_bounds(rows: Sequence[dict[str, Any]]) -> tuple[list[TierBound], Counter[str], int]:
    """구간별 하한·상한. 반환: (구간 결과, 등급 불일치 유형 분포, 분모에서 뺀 레코드 수)."""
    pairs = [
        (resolve_lower(row.get("labels") or []), resolve_upper(row.get("labels") or []), row)
        for row in rows
    ]
    decided = [(lo, up, row) for lo, up, row in pairs if lo.grade is not None]
    grade_disagreements: Counter[str] = Counter(
        diff_type(row["labels"])
        for _, _, row in decided
        if len({label["evidence_grade"] for label in _graded(row["labels"])}) > 1
    )
    results = []
    for tier in gate1.TIERS:
        lower = upper = 0
        diffs: Counter[str] = Counter()
        for lo, up, row in decided:
            got_lower = gate1.counts_as_recovered(lo.grade, lo.confidence, tier)
            got_upper = gate1.counts_as_recovered(up.grade, up.confidence, tier)
            lower += got_lower
            upper += got_upper
            if got_lower != got_upper:
                diffs[diff_type(row["labels"])] += 1
        results.append(TierBound(tier, lower, upper, len(decided), diffs))
    return results, grade_disagreements, len(rows) - len(decided)


# --------------------------------------------------------------------------------------
# 입출력
# --------------------------------------------------------------------------------------


def read_merged(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or "labels" not in row:
            raise ValueError(f"{path}:{number}: 병합 파일 줄이 아니다 (labels 키 없음)")
        rows.append(row)
    return rows


def gate1_lower(rows: Sequence[dict[str, Any]]) -> list[int]:
    """병합 파일 `final`로 eval/gate1.py 가 내는 구간별 회수 수. 하한과 같아야 한다."""
    recovery = gate1.recovery_tiers([gate1.resolve_record(row) for row in rows])
    return [result.recovered for result in recovery.tiers]


def _pct(count: int, total: int) -> str:
    return f"{count / total:.1%}" if total else "—"


def format_table(bounds: Sequence[TierBound], disagreements: Counter[str]) -> list[str]:
    lines = [
        "| 구간 | 하한 | 판정(하한) | 상한 | 판정(상한) | 판정 같음 | 차이 원인 |",
        "|---|---:|---|---:|---|---|---|",
    ]
    for item in bounds:
        causes = ", ".join(
            f"{name} {item.diff_types[name]}" for name in DIFF_TYPES if item.diff_types[name]
        )
        lower = f"{item.lower}/{item.resolved} ({_pct(item.lower, item.resolved)})"
        upper = f"{item.upper}/{item.resolved} ({_pct(item.upper, item.resolved)})"
        lines.append(
            f"| {item.tier.title} | {lower} | {item.lower_verdict} "
            f"| {upper} | {item.upper_verdict} "
            f"| {'예' if item.same_verdict else '아니오'} | {causes or '—'} |"
        )
    lines.append("")
    lines.append(
        "등급 불일치 레코드 유형: "
        + ", ".join(f"{name} {disagreements[name]}" for name in DIFF_TYPES[:3])
        + f" (합계 {sum(disagreements.values())})"
    )
    return lines


def to_json(
    source: Path,
    bounds: Sequence[TierBound],
    disagreements: Counter[str],
    excluded: int,
    gate1_counts: Sequence[int],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": str(source),
        "rules": {
            "lower": "약한 등급 채택 (#76), confidence = min",
            "upper": "강한 등급 채택 (UNKNOWN < INFERRED < EXPLICIT), "
            "confidence = 채택된 등급을 준 라벨의 값, 여럿이면 max",
            "thresholds": {"proceed": gate1.GATE1_PROCEED, "tighten": gate1.GATE1_TIGHTEN},
        },
        "excluded_no_grade": excluded,
        "grade_disagreements": {name: disagreements[name] for name in DIFF_TYPES[:3]},
        "lower_matches_gate1": [item.lower for item in bounds] == list(gate1_counts),
        "gate1_recovered": list(gate1_counts),
        "tiers": [
            {
                "key": item.tier.key,
                "title": item.tier.title,
                "resolved": item.resolved,
                "lower": {
                    "recovered": item.lower,
                    "rate": item.lower / item.resolved if item.resolved else None,
                    "verdict": item.lower_verdict,
                },
                "upper": {
                    "recovered": item.upper,
                    "rate": item.upper / item.resolved if item.resolved else None,
                    "verdict": item.upper_verdict,
                },
                "same_verdict": item.same_verdict,
                "diff_types": {name: item.diff_types[name] for name in DIFF_TYPES}
                | dict(item.diff_types),
            }
            for item in bounds
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.gate1_bounds",
        description="게이트 1 회수율 3구간의 하한·상한 — 등급 불일치를 양 극단으로 해소 (#78).",
    )
    parser.add_argument(
        "merged",
        type=Path,
        nargs="?",
        default=Path("datasets/labels/gate1_pre200_merged.jsonl"),
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    rows = read_merged(args.merged)
    bounds, disagreements, excluded = compute_bounds(rows)
    gate1_counts = gate1_lower(rows)

    print(f"### 게이트 1 회수율 범위 ({args.merged}, 레코드 {len(rows)}건)")
    print()
    for line in format_table(bounds, disagreements):
        print(line)
    if excluded:
        print(f"등급이 하나도 없어 분모에서 뺀 레코드: {excluded}건")
    matches = [item.lower for item in bounds] == gate1_counts
    print(
        f"하한 = eval/gate1.py 값 ({', '.join(map(str, gate1_counts))}): "
        f"{'일치' if matches else '불일치'}"
    )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                to_json(args.merged, bounds, disagreements, excluded, gate1_counts),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"JSON: {args.json_out}")
    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main())
