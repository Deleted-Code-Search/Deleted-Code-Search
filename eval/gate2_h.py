"""게이트 2 정확도 기준의 H - 테스트 100건 사람 간 `reason_label` 단순 일치율 (#92). 담당: 성제 (sj)

정의 (ADR-016 결정 1, CHARTER §10.2, `docs/evaluation.md` "게이트 2 사전 등록 → H 정의"):
    H = `split == "test"` 레코드에서 두 라벨러의 독립 라벨(확정 전) `reason_label` 단순 일치율.
    쌍별이 아니라 테스트 전체를 풀링한다. 분모는 `anchored` 라벨을 뺀 뒤 독립 라벨이 2개인
    레코드 수, 분자는 그중 두 값이 같은 레코드 수. 빠진 건수를 함께 낸다.
    `anchored` 제외는 `classify.labels.pair_labels` 를 그대로 쓴다.

독립 라벨이 어느 줄인가:
    `labels[]` 다. `final`(2인 일치 또는 제3자 판정, ADR-019)은 읽지 않는다.
    1~50번 중 v3 로 재판정한 건은 재판정이 개인 라벨 파일의 같은 줄을 제자리에서 덮어썼으므로
    (`datasets/labels/snapshots/v2_interim/README.md`) `labels[]` 가 곧 v3 재판정 라벨이다 —
    "500건 라벨링 v3 사전 등록 → H 와의 관계" 가 정한 줄이다. v2 스냅샷은 읽지 않는다.

함께 내되 판정에 쓰지 않는 것:
    51번 이후(배분 파일 `interim == false`) 테스트 레코드만으로 낸 H (같은 절의 참고치).
    95% Wilson 구간 (ADR-016 한계 "표본이 100건이라 신뢰구간이 넓다").

읽지 않는 것:
    분류기·기준선 코드와 예측, `final`. 이 스크립트는 라벨 파일의 사람 라벨만 본다.

실행:
    python -m eval.gate2_h
    python -m eval.gate2_h --json-out docs/reports/gate2_h.json

    게이트 실행은 test 레코드가 정확히 100건(CHARTER §10.2 분할)이 아니면 결과를 내지 않고 멈춘다.
    `--example` 은 작은 예제(테스트 픽스처)용이고, 이때만 건수 검증을 끈다.
    예제 모드 결과는 게이트 기록에 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from classify.labels import pair_labels, read_jsonl

# 게이트 2 ① 의 상한 (ADR-016 결정 1). 80% 를 넘는 H 는 기준을 엄격하게 만들지 않는다.
ACCURACY_CAP = 0.80
# 게이트 2 ② (§10.2, ADR-016 결정 2). 이 스크립트는 값을 적기만 하고 비교하지 않는다.
BASELINE_MARGIN_PP = 10
TEST_SPLIT = "test"
# CHARTER §10.2 분할 (학습 300 / 검증 100 / 테스트 100).
# 게이트 실행에서 test 가 이 건수가 아니면 H 를 내지 않는다.
EXPECTED_TEST_RECORDS = 100
_WILSON_Z = 1.959964


@dataclass(frozen=True)
class Agreement:
    records: int  # 대상 레코드 수 (anchored 제외 전)
    denominator: int  # anchored 제외 후 독립 라벨 2개인 레코드
    agreed: int
    excluded: int  # 분모에서 빠진 레코드 (anchored 등으로 독립 라벨이 2개가 안 됨)

    @property
    def rate(self) -> float | None:
        return self.agreed / self.denominator if self.denominator else None


def agreement(rows: Sequence[dict[str, Any]]) -> Agreement:
    """`reason_label` 단순 일치를 풀링해 센다. 쌍 구분 없이 모든 쌍을 한 분모에 넣는다."""
    pairs = [pair for values in pair_labels(rows, "reason_label").values() for pair in values]
    agreed = sum(1 for left, right in pairs if left == right)
    return Agreement(
        records=len(rows),
        denominator=len(pairs),
        agreed=agreed,
        excluded=len(rows) - len(pairs),
    )


def wilson_interval(agreed: int, total: int, z: float = _WILSON_Z) -> tuple[float, float] | None:
    if total == 0:
        return None
    p = agreed / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return centre - half, centre + half


def accuracy_threshold(h: float) -> float:
    """게이트 2 ① 기준값 min(80%, H)."""
    return min(ACCURACY_CAP, h)


def select_test_rows(merged: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in merged if row.get("split") == TEST_SPLIT]


def rejudged_count(
    rows: Sequence[dict[str, Any]], assignment: Mapping[str, Mapping[str, Any]]
) -> int:
    """1~50번(`interim`) 중 두 라벨이 모두 v3 인 레코드 = v3 재판정 라벨이 들어간 건.

    1~50번은 v2 로 라벨했고 갈린 건만 v3 로 재판정했다 (사전 등록 6번). 그래서 1~50번에서
    v3 라벨이 있으면 재판정 줄이다. H 가 그 줄을 읽었는지 확인하는 숫자다.
    """
    return sum(
        1
        for row in rows
        if assignment[row["record_id"]].get("interim")
        and all(label.get("guide_version") == "v3" for label in row["labels"])
    )


def compute(
    merged: Sequence[dict[str, Any]],
    assignment: Mapping[str, Mapping[str, Any]],
    *,
    example: bool = False,
) -> dict[str, Any]:
    """H 와 게이트 2 ① 기준값을 낸다.

    `example=False`(게이트 실행)이면 test 레코드가 `EXPECTED_TEST_RECORDS` 건이 아닐 때 멈춘다.
    다른 건수에서 나온 H 는 ADR-016 의 H 가 아니다. `example=True` 는 손으로 센 예제용이다.
    """
    rows = select_test_rows(merged)
    if not example and len(rows) != EXPECTED_TEST_RECORDS:
        raise ValueError(
            f"test 레코드가 {len(rows)}건이다 - "
            f"게이트 실행은 정확히 {EXPECTED_TEST_RECORDS}건이어야 한다"
        )
    missing = [row["record_id"] for row in rows if row["record_id"] not in assignment]
    if missing:
        raise ValueError(f"배분 파일에 없는 테스트 레코드 {len(missing)}건 {missing[:3]}")
    mismatched = [
        row["record_id"] for row in rows if assignment[row["record_id"]].get("split") != TEST_SPLIT
    ]
    if mismatched:
        raise ValueError(f"배분 파일과 split 이 다른 레코드 {len(mismatched)}건 {mismatched[:3]}")

    main = agreement(rows)
    if main.rate is None:
        raise ValueError("독립 라벨 2개인 테스트 레코드가 없다")
    later = agreement([row for row in rows if not assignment[row["record_id"]].get("interim")])
    interval = wilson_interval(main.agreed, main.denominator)
    return {
        "split": TEST_SPLIT,
        "example": example,
        "H": main.rate,
        "agreement": asdict(main),
        "wilson95": list(interval) if interval else None,
        "interim_records": sum(1 for row in rows if assignment[row["record_id"]].get("interim")),
        "rejudged_v3_records": rejudged_count(rows, assignment),
        "reference_after_50": {**asdict(later), "rate": later.rate},
        "accuracy_threshold": accuracy_threshold(main.rate),
        "accuracy_cap": ACCURACY_CAP,
        "baseline_margin_pp": BASELINE_MARGIN_PP,
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def format_report(result: Mapping[str, Any]) -> list[str]:
    main = result["agreement"]
    later = result["reference_after_50"]
    low, high = result["wilson95"]
    return [
        f"테스트 레코드 {main['records']}건 (1~50번 {result['interim_records']}건, "
        f"그중 v3 재판정 라벨 {result['rejudged_v3_records']}건)",
        f"H = {main['agreed']}/{main['denominator']} = {_pct(result['H'])} "
        f"(anchored 등으로 분모에서 빠진 레코드 {main['excluded']}건)",
        f"  95% Wilson 구간 {_pct(low)} ~ {_pct(high)} (참고)",
        f"참고 - 51번 이후 {later['records']}건: {later['agreed']}/{later['denominator']} = "
        f"{_pct(later['rate'])} (빠진 레코드 {later['excluded']}건, 판정에 쓰지 않는다)",
        f"게이트 2 ① 정확도 기준 = min({_pct(result['accuracy_cap'])}, H) = "
        f"{_pct(result['accuracy_threshold'])}",
        f"게이트 2 ② 정확도 ≥ max(기준선 A, 기준선 B) + {result['baseline_margin_pp']}%p",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.gate2_h",
        description="게이트 2 H - 테스트 100건 사람 간 reason_label 단순 일치율 (ADR-016, #92).",
    )
    parser.add_argument("merged", type=Path, nargs="?", default=Path("datasets/labeled_500.jsonl"))
    parser.add_argument(
        "--assignment", type=Path, default=Path("datasets/labels/main500_assignment.jsonl")
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--example",
        action="store_true",
        help=f"예제 모드: test {EXPECTED_TEST_RECORDS}건 검증을 끈다. 게이트 기록에 쓰지 않는다",
    )
    args = parser.parse_args(argv)

    assignment = {row["record_id"]: row for row in read_jsonl(args.assignment)}
    try:
        result = compute(read_jsonl(args.merged), assignment, example=args.example)
    except ValueError as error:
        print(f"H 를 낼 수 없다: {error}", file=sys.stderr)
        return 1
    title = "예제 모드 - 게이트 기록에 쓰지 않는다" if args.example else str(args.merged)
    print(f"### 게이트 2 H ({title})")
    print()
    for line in format_report(result):
        print(line)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
