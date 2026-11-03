"""test 100건 정확도 다시 세기 "massage" (exp/massage-sj 전용). 담당: 성제 (sj)

게이트 2 판정이 아니다. 판정은 사전 등록 기준의 60.0% 미달 그대로다
(ADR-016, `docs/meetings/gate-2.md`).
새 라벨링·재학습·API 호출 없이, 커밋된 예측과 확정 라벨을 정의만 바꿔 다시 센다.
이 브랜치는 main 에 머지하지 않는다.

수단 4개 (조합 가능):
    ① 묶기     이유 8종을 큰 묶음으로 합치고, 같은 묶음이면 맞은 것으로 센다
    ② 관대     확정 라벨 또는 라벨러 누구의 라벨과 같으면 맞은 것으로 센다
    ③ 상위     우리 방식 순위 상위 X% 만 센다
    ④ 쉬운 부분 전체 / filter-miss 제외 / 정답 등급 EXPLICIT / EXPLICIT+INFERRED

적용 순서와 정의:
    범위는 ④ 를 먼저 적용하고 그 안에서 ③ 상위를 올림으로 고른다 (예: 85건의 top70 = 60건).
    ③ 순위는 우리 방식 것을 쓰고, 기준선 A·B 는 같은 레코드에서 센다. 기준선에는 신뢰도가 없다.
    순위 키는 두 가지다. `conf` 는 예측의 `confidence` 필드이고, `score` 는 예측 라벨의
    `scores` 값이다. `confidence` 는 UNK 예측이 전부 0.0 이라 상위를 고르면 UNK 가 먼저 빠진다.
    `score` 는 그 사실을 test 에서 본 뒤 추가한 키다.
    동점은 score, record_id 순으로 푼다. 정답을 보지 않는 순서다.

G-best (test 를 보고 고른 묶음):
    UNK 를 따로 두고 나머지 7종을 3~4묶음으로 나누는 651가지를 전부 세어, 묶기만 적용한
    전체 100건에서 우리 방식 정확도가 가장 높은 하나로 고정한다. 칸마다 다시 고르지 않는다.
    `G-best-nd` 는 그중 모든 묶음에 test 정답이 1건 이상 있는 경우만 본다. test 정답이 없는
    라벨을 혼자 두어 묶음 수만 채우면 G2 와 같은 계산이 되기 때문이다.

실행:
    python -m eval.massage
    python -m eval.massage --out-dir docs/reports/massage
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from classify.labels import read_jsonl
from eval.gate2_h import EXPECTED_TEST_RECORDS, TEST_SPLIT
from eval.pxa import not_meaningful

UNK = "UNK"
REASONS = ("BUG", "PERF", "SEC", "LIB", "DEAD", "DESIGN", "FEAT")
PREFIX = "massage"
ORIGINAL = "orig"
GATE2_ACCURACY = 0.60
MIN_HEADLINE_RECORDS = 50
HEADLINE_COUNT = 5
TOP_PERCENTS = (90, 80, 70, 50)
RANK_KEYS = ("conf", "score")
SUBSETS = ("all", "no-filter-miss", "explicit", "explicit+inferred")
METHOD_FILES = {"ours": "ours.jsonl", "a": "keyword_a.jsonl", "b": "llm_b.jsonl"}

DEFAULT_LABELS = Path("datasets/labeled_500.jsonl")
DEFAULT_PREDICTIONS = Path("docs/reports/gate2_test")
DEFAULT_GATE2 = Path("docs/reports/gate2_test.json")
DEFAULT_OUT = Path("docs/reports/massage")

Partition = tuple[tuple[str, ...], ...]

G4: Partition = (("FEAT", "DESIGN"), ("BUG", "PERF", "SEC"), ("DEAD", "LIB"), (UNK,))
G3: Partition = (("FEAT", "DESIGN", "PERF"), ("BUG", "SEC"), ("DEAD", "LIB"), (UNK,))
G2: Partition = (REASONS, (UNK,))


@dataclass(frozen=True)
class Case:
    record_id: str
    final: str
    accepted: frozenset[str]  # 확정 라벨 + 라벨러 라벨
    grade: str
    filter_miss: bool
    confidence: float
    score: float
    predictions: Mapping[str, str]  # 방식 → 예측 라벨


def group_map(partition: Partition | None) -> dict[str, str]:
    """라벨 → 묶음 이름. `None` 은 묶지 않는다."""
    if partition is None:
        return {label: label for label in (*REASONS, UNK)}
    mapping = {label: "|".join(block) for block in partition for label in block}
    if set(mapping) != {*REASONS, UNK}:
        raise ValueError(f"묶음이 8종을 정확히 덮지 않는다: {partition}")
    return mapping


def is_correct(prediction: str, case: Case, mapping: Mapping[str, str], *, lenient: bool) -> bool:
    answers = case.accepted if lenient else {case.final}
    return mapping[prediction] in {mapping[answer] for answer in answers}


def in_subset(case: Case, subset: str) -> bool:
    if subset == "all":
        return True
    if subset == "no-filter-miss":
        return not case.filter_miss
    if subset == "explicit":
        return case.grade == "EXPLICIT"
    if subset == "explicit+inferred":
        return case.grade in {"EXPLICIT", "INFERRED"}
    raise ValueError(f"알 수 없는 범위: {subset}")


def top_cases(cases: Sequence[Case], percent: int, key: str) -> list[Case]:
    if key == "conf":
        ranked = sorted(cases, key=lambda c: (-c.confidence, -c.score, c.record_id))
    elif key == "score":
        ranked = sorted(cases, key=lambda c: (-c.score, c.record_id))
    else:
        raise ValueError(f"알 수 없는 순위 키: {key}")
    return ranked[: math.ceil(len(ranked) * percent / 100)]


def select_scope(
    cases: Sequence[Case], subset: str, percent: int = 100, key: str = "conf"
) -> list[Case]:
    """④ 범위를 먼저 적용하고 그 안에서 ③ 상위를 고른다."""
    return top_cases([case for case in cases if in_subset(case, subset)], percent, key)


def accuracy_counts(
    cases: Sequence[Case], method: str, mapping: Mapping[str, str], *, lenient: bool
) -> int:
    return sum(
        is_correct(case.predictions[method], case, mapping, lenient=lenient) for case in cases
    )


def iter_partitions(labels: Sequence[str] = REASONS) -> Iterator[Partition]:
    """`labels` 를 3~4묶음으로 나누는 모든 경우 + UNK 단독 묶음."""

    def walk(index: int, blocks: list[list[str]]) -> Iterator[Partition]:
        if index == len(labels):
            if len(blocks) in {3, 4}:
                yield (*(tuple(block) for block in blocks), (UNK,))
            return
        for block in blocks:
            block.append(labels[index])
            yield from walk(index + 1, blocks)
            block.pop()
        if len(blocks) < 4:
            blocks.append([labels[index]])
            yield from walk(index + 1, blocks)
            blocks.pop()

    yield from walk(0, [])


def has_support_everywhere(partition: Partition, cases: Sequence[Case]) -> bool:
    supported = {case.final for case in cases}
    return all(supported.intersection(block) for block in partition)


def best_partition(
    cases: Sequence[Case], *, nondegenerate: bool = False
) -> tuple[Partition, int, int]:
    """묶기만 적용한 우리 방식 정확도 최고 묶음 → (묶음, 맞은 건수, 동점 수).

    동점은 묶음 수가 많은 쪽, 그다음 이름순으로 푼다.
    """
    scored: list[tuple[int, Partition]] = []
    for partition in iter_partitions():
        if nondegenerate and not has_support_everywhere(partition, cases):
            continue
        scored.append(
            (accuracy_counts(cases, "ours", group_map(partition), lenient=False), partition)
        )
    if not scored:
        raise ValueError("조건에 맞는 묶음이 없다 (test 정답 라벨이 너무 적다)")
    top = max(correct for correct, _ in scored)
    tied = sorted(
        (partition for correct, partition in scored if correct == top),
        key=lambda partition: (-len(partition), partition),
    )
    return tied[0], top, len(tied)


def top_name(percent: int, key: str) -> str:
    return "top100" if percent == 100 else f"top{percent}-{key}"


def combo_name(grouping: str, lenient: bool, top: str, subset: str) -> str:
    lenient_name = "lenient" if lenient else "strict"
    return f"{PREFIX}/{grouping}+{lenient_name}+{top}+{subset}"


def evaluate(
    cases: Sequence[Case],
    grouping: str,
    partition: Partition | None,
    *,
    lenient: bool,
    percent: int = 100,
    key: str = "conf",
    subset: str = "all",
) -> dict[str, Any]:
    scope = select_scope(cases, subset, percent, key)
    mapping = group_map(partition)
    n = len(scope)
    counts = {
        method: accuracy_counts(scope, method, mapping, lenient=lenient) for method in METHOD_FILES
    }
    all_unk = sum(is_correct(UNK, case, mapping, lenient=lenient) for case in scope)

    def rate(correct: int) -> float | None:
        return correct / n if n else None

    used = [partition is not None, lenient, percent != 100, subset != "all"]
    ours = rate(counts["ours"])
    top = top_name(percent, key)
    return {
        "name": combo_name(grouping, lenient, top, subset),
        "grouping": grouping,
        "lenient": lenient,
        "top": top,
        "subset": subset,
        "n": n,
        "ours_correct": counts["ours"],
        "ours_accuracy": ours,
        "baseline_a_correct": counts["a"],
        "baseline_a_accuracy": rate(counts["a"]),
        "baseline_b_correct": counts["b"],
        "baseline_b_accuracy": rate(counts["b"]),
        "all_unk_correct": all_unk,
        "all_unk_accuracy": rate(all_unk),
        "delta_pp": None if ours is None else round((ours - GATE2_ACCURACY) * 100, 1),
        "massages_used": sum(used),
        "all_four": all(used),
    }


def compute(cases: Sequence[Case]) -> dict[str, Any]:
    best, best_correct, best_ties = best_partition(cases)
    nondegenerate, nd_correct, nd_ties = best_partition(cases, nondegenerate=True)
    groupings: dict[str, Partition | None] = {
        ORIGINAL: None,
        "G4": G4,
        "G3": G3,
        "G2": G2,
        "G-best": best,
        "G-best-nd": nondegenerate,
    }
    tops = [(100, "conf"), *((p, k) for k in RANK_KEYS for p in TOP_PERCENTS)]
    rows = [
        evaluate(
            cases,
            grouping,
            partition,
            lenient=lenient,
            percent=percent,
            key=key,
            subset=subset,
        )
        for grouping, partition in groupings.items()
        for lenient in (False, True)
        for percent, key in tops
        for subset in SUBSETS
    ]
    return {
        "warning": "massage 숫자다. 게이트 2 판정(60.0%, 미달)을 바꾸지 않는다",
        "records": len(cases),
        "groupings": {
            name: None if partition is None else [list(block) for block in partition]
            for name, partition in groupings.items()
        },
        "g_best": {
            "selected_on_test": True,
            "partitions_tried": sum(1 for _ in iter_partitions()),
            "ours_correct": best_correct,
            "ties": best_ties,
        },
        "g_best_nd": {"selected_on_test": True, "ours_correct": nd_correct, "ties": nd_ties},
        "headlines": [row["name"] for row in pick_headlines(rows)],
        "combos": rows,
    }


def _rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """정확도, 건수, 수단 수 순. 동점이면 test 를 보고 고르지 않은 묶음이 앞선다."""
    selected_on_test = row["grouping"].startswith("G-best")
    return (-row["ours_accuracy"], -row["n"], row["massages_used"], selected_on_test, row["name"])


def _dedupe(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """같은 범위에서 우리 방식 숫자가 같은 조합(G-best 와 G2 등)은 먼저 오는 하나만 둔다."""
    seen: set[tuple[Any, ...]] = set()
    kept = []
    for row in sorted(rows, key=_rank):
        identity = (
            row["lenient"],
            row["top"],
            row["subset"],
            row["ours_correct"],
        )
        if identity not in seen:
            seen.add(identity)
            kept.append(row)
    return kept


def pick_headlines(
    rows: Sequence[Mapping[str, Any]], count: int = HEADLINE_COUNT
) -> list[Mapping[str, Any]]:
    eligible = [row for row in rows if row["n"] >= MIN_HEADLINE_RECORDS]
    return _dedupe(eligible)[:count]


def pick_all_four(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    eligible = [row for row in rows if row["all_four"] and row["n"] >= MIN_HEADLINE_RECORDS]
    return _dedupe(eligible)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _delta(value: float | None) -> str:
    return "-" if value is None else f"{value:+.1f}"


def _row(row: Mapping[str, Any]) -> str:
    return (
        f"| `{row['name']}` | {_pct(row['ours_accuracy'])} ({row['ours_correct']}/{row['n']}) "
        f"| {row['n']} | {_pct(row['baseline_a_accuracy'])} | {_pct(row['baseline_b_accuracy'])} "
        f"| {_pct(row['all_unk_accuracy'])} | {_delta(row['delta_pp'])} |"
    )


_HEADER = [
    "| 조합 | 우리 방식 | 평가 건수 | 기준선 A | 기준선 B | 전부 UNK | 60% 대비 %p |",
    "|---|---:|---:|---:|---:|---:|---:|",
]


def _partition_text(partition: Sequence[Sequence[str]]) -> str:
    return " / ".join("{" + ", ".join(block) + "}" for block in partition)


def format_menu(result: Mapping[str, Any]) -> str:
    rows = result["combos"]
    by_name = {row["name"]: row for row in rows}
    all_four = pick_all_four(rows)
    groupings = result["groupings"]
    conf50_row = by_name[combo_name(ORIGINAL, False, "top50-conf", "all")]
    conf50 = f"{_pct(conf50_row['ours_accuracy'])}, {conf50_row['ours_correct']}/{conf50_row['n']}"
    lines = [
        "# massage 메뉴 — test 100건 다시 세기",
        "",
        "> **게이트 2 결과가 아니다.** 사전 등록 기준의 test 정확도는 "
        "**60.0%, 게이트 2 미달** 그대로다.",
        "> 아래 숫자는 같은 예측을 정의만 바꿔 다시 센 값이다. 분류기는 한 글자도 바뀌지 않았다.",
        "> 숫자를 인용할 때는 조합 이름의 정의(묶기·관대·범위)와 평가 건수를 함께 적는다.",
        "",
        "`python -m eval.massage` 로 재생성. 같은 숫자: `menu.json`.",
        "",
        f"## 추천 헤드라인 {HEADLINE_COUNT}개",
        "",
        f"평가 건수 {MIN_HEADLINE_RECORDS}건 이상에서 정확도 순. 숫자가 같은 조합은 하나만 둔다.",
        "",
        *_HEADER,
        *(_row(by_name[name]) for name in result["headlines"]),
        "",
        "## ★ 네 개 다 적용한 조합",
        "",
        f"묶기 + 관대 + 상위 + 쉬운 부분. 평가 건수 {MIN_HEADLINE_RECORDS}건 이상에서 정확도 순.",
        "",
        *_HEADER,
        *(_row(row) for row in all_four[:10]),
        "",
        "## 읽을 때 주의",
        "",
        "- **`전부 UNK` 열이 바닥이다.** 항상 UNK 라고 답한 정확도다. "
        "묶기·관대·범위는 이 바닥도 함께 움직인다. 상승폭은 분류기가 나아진 양이 아니다",
        "- **④ 는 정답을 보고 범위를 고른다.** 운영에서는 정답 등급이나 filter-miss 를 "
        "미리 알 수 없다. "
        "EXPLICIT·INFERRED 범위에는 확정 정답 UNK 가 한 건도 없다",
        "- **`conf` 상위는 숫자를 크게 올리지 못한다.** UNK 예측의 `confidence` 가 전부 0.0 "
        f"이라 UNK 부터 빠진다 (묶기·관대 없이 top50-conf {conf50}). "
        "`score` 키는 이걸 test 에서 본 뒤 추가했으므로 사전에 정한 신뢰도 기준이 아니다",
        "- **상위 X% 는 나머지를 답하지 않은 것으로 친다.** "
        "해당 건수 밖의 레코드는 평가에서 빠진다",
        f"- **G-best 는 test 를 보고 골랐다** "
        f"({result['g_best']['partitions_tried']}가지 중 최고). "
        f"정의: {_partition_text(groupings['G-best'])}. test 정답이 없는 라벨이 혼자 있으면 G2 와 "
        "같은 계산이다",
        f"- **G-best-nd 도 test 를 보고 골랐다.** 모든 묶음에 test 정답이 있는 경우만 본 최고다. "
        f"정의: {_partition_text(groupings['G-best-nd'])} "
        f"(동점 {result['g_best_nd']['ties']}가지, 이름순 첫째)",
        "- **조합을 많이 시험했다.** 전체 "
        f"{len(rows)}개 중 위쪽만 고른 값이라 낙관적이다. 표본도 100건 이하다",
        "",
        "## 묶기 정의",
        "",
        *(
            f"- `{name}`: {'8종 그대로' if partition is None else _partition_text(partition)}"
            for name, partition in groupings.items()
        ),
        "",
        f"## 전체 표 ({len(rows)}개)",
        "",
        "★ = 네 개 다 적용.",
        "",
        *_HEADER,
    ]
    for row in rows:
        text = _row(row)
        lines.append(text.replace("| `", "| ★ `", 1) if row["all_four"] else text)
    return "\n".join(lines) + "\n"


def load_cases(labels_path: Path, predictions_dir: Path) -> list[Case]:
    rows = [row for row in read_jsonl(labels_path) if row.get("split") == TEST_SPLIT]
    predictions = {
        method: {row["record_id"]: row for row in read_jsonl(predictions_dir / filename)}
        for method, filename in METHOD_FILES.items()
    }
    ids = {row["record_id"] for row in rows}
    if len(rows) != EXPECTED_TEST_RECORDS:
        raise ValueError(f"test 레코드가 {EXPECTED_TEST_RECORDS}건이 아니다: {len(rows)}")
    for method, by_id in predictions.items():
        if set(by_id) != ids:
            raise ValueError(f"{method} 예측의 record_id 가 test 와 다르다")

    cases = []
    for row in rows:
        record_id = row["record_id"]
        final = row["final"]
        ours = predictions["ours"][record_id]
        labels = {label["reason_label"] for label in row["labels"]}
        cases.append(
            Case(
                record_id=record_id,
                final=final["reason_label"],
                accepted=frozenset({final["reason_label"], *labels}),
                grade=final["evidence_grade"],
                filter_miss=not_meaningful(row),
                confidence=float(ours["confidence"]),
                score=float(ours["scores"][ours["predicted_label"]]),
                predictions={
                    method: by_id[record_id]["predicted_label"]
                    for method, by_id in predictions.items()
                },
            )
        )
    return cases


def check_against_gate2(cases: Sequence[Case], gate2_path: Path) -> None:
    """수단을 하나도 안 쓴 값이 게이트 2 보고서와 같은지 본다. 다르면 입력이 틀린 것이다."""
    methods = json.loads(gate2_path.read_text(encoding="utf-8"))["methods"]
    mapping = group_map(None)
    for gate2_name, method in (("ours", "ours"), ("keyword_a", "a"), ("llm_b", "b")):
        expected = methods[gate2_name]["correct"]
        actual = accuracy_counts(cases, method, mapping, lenient=False)
        if actual != expected:
            raise ValueError(f"{gate2_name}: 게이트 2 보고서 {expected}건, 다시 센 값 {actual}건")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--gate2", type=Path, default=DEFAULT_GATE2)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    cases = load_cases(args.labels, args.predictions)
    check_against_gate2(cases, args.gate2)
    result = compute(cases)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "menu.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (args.out_dir / "menu.md").write_text(format_menu(result), encoding="utf-8", newline="\n")
    print(f"{len(result['combos'])}개 조합 → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
