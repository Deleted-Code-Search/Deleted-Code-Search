"""CHARTER §10.6 P × A 논증의 숫자 (#129). 담당: 성제 (sj)

사건 (모두 데이터셋 레코드 하나에 대한 것):
    K = 필터 통과(`filter_status == KEPT`), M = 의미 있는 삭제(가이드 §6.3.3 "예"),
    C = 이유 라벨(`reason_label`)이 사람 확정 라벨과 같다.
    목표량 = P(M ∧ C | K). P = P(M | K) (#89 필터 정밀도).

A 는 둘 중 무엇으로 쟀느냐에 따라 곱의 뜻이 다르다 (`docs/reports/pxa_argument.md`):
    경우 1  A = P(C | M, K) → P(M ∧ C | K) = P × A (연쇄 법칙, 독립 가정은 필요 없다.
                              다만 P 와 A 를 다른 표본에서 재면 두 M 이 같은 사건이고 두 값이
                              같은 모집단·가중치라는 가정 아래의 근사다)
    경우 2  A = P(C | K)    → 곱은 독립 가정이 있어야 하고, 가정 없이 말할 수 있는 것은
                              Fréchet 범위 max(0, P + A − 1) ≤ P(M ∧ C | K) ≤ min(P, A)
                              (P·A 점추정에 넣은 범위다 - 표본 오차는 반영하지 않는다)

게이트 2 test 100건은 KEPT 에서 뽑았고 의미 없는 삭제도 표본에 남겨 `UNK` + `filter-miss` 로
라벨했다 (가이드 §6.3.1-3, §6.3.3). 그래서 게이트 2 정확도 60.0% 는 경우 2 의 P(C | K) 다.
같은 라벨에서 M 이 아닌 건을 가려내면 경우 1 의 P(C | M, K) 와 P(M ∧ C | K) 도 직접 셀 수 있다.

M 이 아닌 레코드 (확정 라벨이 `filter-miss` 를 단 라벨인가):
    `AGREED`      두 라벨 모두 `filter-miss`
    `THIRD_PARTY` 제3자가 고른 라벨(`classify.labels.chosen_labeler`)이 `filter-miss`
    태그 판정은 `classify.labels.has_note_tag` (label_cli 저장 검증과 같은 낱말 매칭).
    민감도로 "한 사람이라도 `filter-miss`" 건수를 함께 낸다.

저장소 가중 (참고): 500건은 저장소마다 균등(25건, test 5건)으로 뽑았고 P 표본은 모집단 비례다.
`--repo-sizes` (`docs/reports/apply100.json` 의 모델만 저장소별 건수 = v0.8 FULL_FUNCTION·KEPT)
로 저장소별 비율을 모집단 크기로 가중한 값을 함께 낸다. 판정에 쓰지 않는다.

읽지 않는 것: 필터 정밀도 표본의 개별 판정(`data/`, 커밋하지 않는다). P 는
`docs/reports/filter_precision_v2.json` 의 KEPT 층 다수결 혼동표에서 읽는다. 그 P 는
FULL_FUNCTION + PARTIAL 이고 A' 는 FULL_FUNCTION 만이라 범위가 다르다. 같은 범위의
P(FULL) 은 판정 파일에서만 셀 수 있어 `--precision-full YES N` 으로 넘긴다 (2차: 36 48).

실행 (보고서 숫자):
    python -m eval.pxa --precision-full 36 48 --json-out docs/reports/pxa.json

실행:
    python -m eval.pxa
    python -m eval.pxa --json-out docs/reports/pxa.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from classify.labels import FILTER_MISS_TAG, THIRD_PARTY, chosen_labeler, has_note_tag, read_jsonl
from eval.gate2_h import EXPECTED_TEST_RECORDS, TEST_SPLIT, wilson_interval

UNKNOWN_LABEL = "UNK"
_Z = 1.959964


def has_filter_miss(label: Mapping[str, Any]) -> bool:
    return has_note_tag(label.get("note"), FILTER_MISS_TAG)


def not_meaningful(row: Mapping[str, Any]) -> bool:
    """확정 라벨이 `filter-miss` 를 단 라벨인가 (= 사람이 M 이 아니라고 확정했다)."""
    final = row["final"]
    labels = row.get("labels") or []
    if final.get("method") == THIRD_PARTY:
        chosen = chosen_labeler(row, final)
        if chosen is None:
            # 누구의 라벨을 골랐는지 모르면 M 여부를 정할 수 없다. M 으로 쳐서 넘기지 않는다.
            raise ValueError(
                "THIRD_PARTY 확정 라벨이 labels[] 중 딱 한 라벨과 같지 않다: "
                f"{row.get('record_id')}"
            )
        return any(has_filter_miss(label) for label in labels if label.get("labeler") == chosen)
    return bool(labels) and all(has_filter_miss(label) for label in labels)


def any_filter_miss(row: Mapping[str, Any]) -> bool:
    return any(has_filter_miss(label) for label in row.get("labels") or [])


def frechet_bounds(p: float, a: float) -> tuple[float, float]:
    """P(M | K) = p, P(C | K) = a 만 알 때 P(M ∧ C | K) 가 가질 수 있는 범위."""
    return max(0.0, p + a - 1.0), min(p, a)


def product_interval(
    x1: int, n1: int, x2: int, n2: int, z: float = _Z
) -> tuple[float, float] | None:
    """독립인 두 표본 비율의 곱 (x1/n1)(x2/n2) 의 95% 구간 - 로그 척도 델타 방법.

    두 비율이 다른 표본에서 나왔을 때만 쓴다 (P 표본과 test 100건은 겹치지 않는다,
    `filter_precision_v2` 의 `excluded_ids`). 0 이 있으면 로그를 못 잡으므로 None.
    """
    if not (0 < x1 <= n1 and 0 < x2 <= n2):
        return None
    p1, p2 = x1 / n1, x2 / n2
    se = math.sqrt((1 - p1) / (n1 * p1) + (1 - p2) / (n2 * p2))
    point = p1 * p2
    return point * math.exp(-z * se), point * math.exp(z * se)


def filter_precision_counts(report: Mapping[str, Any]) -> tuple[int, int]:
    """`filter_precision_v2.json` 의 KEPT 층 다수결 (예, 표본 수)."""
    kept = report["confusion"]["KEPT"]
    yes = int(kept["yes"])
    return yes, yes + int(kept["no"])


def _rate(x: int, n: int) -> dict[str, Any]:
    interval = wilson_interval(x, n)
    return {
        "x": x,
        "n": n,
        "rate": x / n if n else None,
        "wilson95": list(interval) if interval else None,
    }


def _weighted(
    rows: Sequence[Mapping[str, Any]],
    repo_of: Mapping[str, str],
    sizes: Mapping[str, int],
    numerator: Callable[[Mapping[str, Any]], bool],
    denominator: Callable[[Mapping[str, Any]], bool] = lambda _row: True,
) -> float | None:
    """저장소별 비율 Σ w_r · (분자 / 분모), w_r = 모집단 크기 비. 분모가 0 인 저장소는 뺀다."""
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[repo_of[row["record_id"]]].append(row)
    parts = []
    for repo, members in groups.items():
        base = [row for row in members if denominator(row)]
        if base:
            parts.append((sizes[repo], sum(1 for row in base if numerator(row)) / len(base)))
    total = sum(size for size, _rate_value in parts)
    return sum(size * value for size, value in parts) / total if total else None


def compute(
    merged: Sequence[dict[str, Any]],
    predictions: Mapping[str, str],
    precision_counts: tuple[int, int],
    *,
    repo_of: Mapping[str, str] | None = None,
    repo_sizes: Mapping[str, int] | None = None,
    precision_full: tuple[int, int] | None = None,
    example: bool = False,
) -> dict[str, Any]:
    rows = [row for row in merged if row.get("split") == TEST_SPLIT]
    if not example and len(rows) != EXPECTED_TEST_RECORDS:
        raise ValueError(
            f"test 레코드가 {len(rows)}건이다 - 정확히 {EXPECTED_TEST_RECORDS}건이어야 한다"
        )
    missing = [row["record_id"] for row in rows if row["record_id"] not in predictions]
    if missing:
        raise ValueError(f"예측이 없는 test 레코드 {len(missing)}건 {missing[:3]}")

    def correct(row: Mapping[str, Any]) -> bool:
        return predictions[row["record_id"]] == row["final"]["reason_label"]

    def meaningful(row: Mapping[str, Any]) -> bool:
        return not not_meaningful(row)

    n = len(rows)
    m_rows = [row for row in rows if meaningful(row)]
    c_all = sum(1 for row in rows if correct(row))
    mc = sum(1 for row in m_rows if correct(row))
    mc_reason = sum(
        1 for row in m_rows if correct(row) and row["final"]["reason_label"] != UNKNOWN_LABEL
    )
    not_m = [row for row in rows if not_meaningful(row)]

    p_yes, p_n = precision_counts
    p = p_yes / p_n
    a_k = c_all / n
    a_m = mc / len(m_rows) if m_rows else None
    low, high = frechet_bounds(p, a_k)

    result: dict[str, Any] = {
        "split": TEST_SPLIT,
        "example": example,
        "test_records": n,
        "not_meaningful": len(not_m),
        "not_meaningful_any_labeler": sum(1 for row in rows if any_filter_miss(row)),
        "not_meaningful_gold_not_unk": sum(
            1 for row in not_m if row["final"]["reason_label"] != UNKNOWN_LABEL
        ),
        "not_meaningful_correct": sum(1 for row in not_m if correct(row)),
        "P_filter": _rate(p_yes, p_n),
        "P_label": _rate(len(m_rows), n),
        "A_given_K": _rate(c_all, n),
        "A_given_MK": _rate(mc, len(m_rows)),
        "MC_direct": _rate(mc, n),
        "MC_reason_direct": _rate(mc_reason, n),
        "case2": {
            "product": p * a_k,
            "product_ci95": product_interval(p_yes, p_n, c_all, n),
            "frechet": [low, high],
        },
        "case1": {
            "product": p * a_m if a_m is not None else None,
            "product_ci95": product_interval(p_yes, p_n, mc, len(m_rows)),
        },
    }
    if precision_full is not None:
        # A' 는 FULL_FUNCTION 만이다. 같은 범위의 P(FULL) 로 낸 곱을 나란히 둔다.
        f_yes, f_n = precision_full
        result["P_filter_full"] = _rate(f_yes, f_n)
        result["case1_full"] = {
            "product": f_yes / f_n * a_m if a_m is not None else None,
            "product_ci95": product_interval(f_yes, f_n, mc, len(m_rows)),
        }
    if repo_of is not None and repo_sizes is not None:
        result["repo_weighted"] = {
            "A_given_K": _weighted(rows, repo_of, repo_sizes, correct),
            "P_label": _weighted(rows, repo_of, repo_sizes, meaningful),
            "A_given_MK": _weighted(rows, repo_of, repo_sizes, correct, meaningful),
            "MC_direct": _weighted(
                rows, repo_of, repo_sizes, lambda row: meaningful(row) and correct(row)
            ),
        }
    return result


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _ci(interval: Sequence[float] | None) -> str:
    return "-" if not interval else f"{_pct(interval[0])} ~ {_pct(interval[1])}"


def _line(name: str, rate: Mapping[str, Any]) -> str:
    return (
        f"{name} = {rate['x']}/{rate['n']} = {_pct(rate['rate'])} "
        f"(Wilson 95% {_ci(rate['wilson95'])})"
    )


def format_report(result: Mapping[str, Any]) -> list[str]:
    case1, case2 = result["case1"], result["case2"]
    lines = [
        f"test {result['test_records']}건 중 확정 라벨이 filter-miss (M 아님): "
        f"{result['not_meaningful']}건 "
        f"(한 사람이라도 단 건 {result['not_meaningful_any_labeler']}건, "
        f"그중 정답이 UNK 가 아닌 건 {result['not_meaningful_gold_not_unk']}건, "
        f"분류기가 맞힌 건 {result['not_meaningful_correct']}건)",
        _line("P   = P(M | K)   필터 정밀도", result["P_filter"]),
        _line("P'  = P(M | K)   test 라벨", result["P_label"]),
        _line("A   = P(C | K)   게이트 2", result["A_given_K"]),
        _line("A'  = P(C | M,K)", result["A_given_MK"]),
        _line("직접 P(M ∧ C | K)", result["MC_direct"]),
        _line("  그중 정답이 UNK 아닌 것", result["MC_reason_direct"]),
        f"경우 2  P × A  = {_pct(case2['product'])} (95% {_ci(case2['product_ci95'])}, 독립 가정), "
        f"점추정 기반 Frechet {_pct(case2['frechet'][0])} ~ {_pct(case2['frechet'][1])}",
        f"경우 1  P × A' = {_pct(case1['product'])} (95% {_ci(case1['product_ci95'])}, "
        "연쇄 법칙 - 독립 가정 없음, M 동일·모집단 동일 가정의 근사)",
    ]
    full = result.get("case1_full")
    if full:
        lines.insert(2, _line("P_F = P(M | K, FULL)", result["P_filter_full"]))
        lines.append(
            f"경우 1  P_F × A' = {_pct(full['product'])} (95% {_ci(full['product_ci95'])}, "
            "A' 와 같은 FULL_FUNCTION 범위)"
        )
    weighted = result.get("repo_weighted")
    if weighted:
        lines.append(
            "참고 - 저장소 가중: "
            f"A {_pct(weighted['A_given_K'])} · P' {_pct(weighted['P_label'])} · "
            f"A' {_pct(weighted['A_given_MK'])} · 직접 {_pct(weighted['MC_direct'])}"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.pxa",
        description="CHARTER §10.6 P × A 논증의 숫자 (#129).",
    )
    parser.add_argument("merged", type=Path, nargs="?", default=Path("datasets/labeled_500.jsonl"))
    parser.add_argument(
        "--predictions", type=Path, default=Path("docs/reports/gate2_test/ours.jsonl")
    )
    parser.add_argument(
        "--precision", type=Path, default=Path("docs/reports/filter_precision_v2.json")
    )
    parser.add_argument(
        "--assignment", type=Path, default=Path("datasets/labels/main500_assignment.jsonl")
    )
    parser.add_argument("--repo-sizes", type=Path, default=Path("docs/reports/apply100.json"))
    parser.add_argument(
        "--precision-full",
        type=int,
        nargs=2,
        metavar=("YES", "N"),
        help="KEPT 표본 중 FULL_FUNCTION 의 다수결 예·표본 수. 판정 파일이 커밋되지 않아 "
        "손으로 넘긴다 (2차: 36 48)",
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--example", action="store_true", help="test 100건 검증을 끈다")
    args = parser.parse_args(argv)

    predictions = {row["record_id"]: row["predicted_label"] for row in read_jsonl(args.predictions)}
    precision = json.loads(args.precision.read_text(encoding="utf-8"))
    repo_of = {row["record_id"]: row["repo"] for row in read_jsonl(args.assignment)}
    sizes = json.loads(args.repo_sizes.read_text(encoding="utf-8"))["model_only"]["repos"]
    try:
        result = compute(
            read_jsonl(args.merged),
            predictions,
            filter_precision_counts(precision),
            repo_of=repo_of,
            repo_sizes=sizes,
            precision_full=tuple(args.precision_full) if args.precision_full else None,
            example=args.example,
        )
    except (ValueError, KeyError) as error:
        print(f"숫자를 낼 수 없다: {error}", file=sys.stderr)
        return 1
    print(f"### P × A (#129, {args.merged})")
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
