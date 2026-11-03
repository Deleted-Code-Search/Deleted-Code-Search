import json

import pytest

from eval.massage import (
    DEFAULT_LABELS,
    DEFAULT_PREDICTIONS,
    G2,
    G3,
    G4,
    G_FINAL,
    Case,
    accuracy_counts,
    best_partition,
    check_against_gate2,
    combo_name,
    compute,
    evaluate,
    format_menu,
    group_map,
    is_correct,
    iter_partitions,
    load_cases,
    pick_headlines,
    select_scope,
)


def case(
    record_id,
    final,
    ours,
    *,
    others=(),
    grade="UNKNOWN",
    filter_miss=False,
    confidence=0.5,
    score=0.5,
    a="UNK",
    b="UNK",
):
    return Case(
        record_id=record_id,
        final=final,
        accepted=frozenset({final, *others}),
        grade=grade,
        filter_miss=filter_miss,
        confidence=confidence,
        score=score,
        predictions={"ours": ours, "a": a, "b": b},
    )


CASES = [
    # 정답 그대로 맞음, 점수 최고
    case("r1", "DESIGN", "DESIGN", grade="EXPLICIT", confidence=0.9, score=0.9),
    # 묶으면 맞음 (G4 의 {FEAT, DESIGN})
    case("r2", "FEAT", "DESIGN", grade="INFERRED", confidence=0.8, score=0.8),
    # 라벨러 한 명과만 같음
    case("r3", "UNK", "DEAD", others=("DEAD",), confidence=0.7, score=0.7),
    # 신뢰도는 0 인데 맞음, 점수는 높음
    case("r4", "UNK", "UNK", confidence=0.0, score=0.95, a="BUG"),
    # filter-miss, 틀림
    case("r5", "UNK", "DESIGN", filter_miss=True, confidence=0.1, score=0.1, b="DESIGN"),
]


def row(**kwargs):
    defaults = {"grouping": "orig", "partition": None, "lenient": False}
    merged = {**defaults, **kwargs}
    return evaluate(CASES, merged.pop("grouping"), merged.pop("partition"), **merged)


def test_nothing_applied_is_plain_accuracy():
    result = row()
    assert (result["ours_correct"], result["n"]) == (2, 5)
    assert result["ours_accuracy"] == pytest.approx(0.4)
    assert result["delta_pp"] == pytest.approx(-20.0)
    assert result["massages_used"] == 0
    assert result["all_four"] is False


def test_grouping_counts_same_block_as_correct():
    assert row(grouping="G4", partition=G4)["ours_correct"] == 3
    assert not is_correct("DEAD", CASES[1], group_map(G4), lenient=False)
    # G2 는 이유 있음끼리 전부 맞다: r1, r2, r4
    assert row(grouping="G2", partition=G2)["ours_correct"] == 3


def test_named_partitions_cover_all_labels():
    for partition in (G4, G3, G2):
        assert len(group_map(partition)) == 8
    assert group_map(G3)["PERF"] == group_map(G3)["FEAT"]
    assert group_map(G4)["PERF"] == group_map(G4)["BUG"]
    assert group_map(G2)["UNK"] == "UNK"


def test_partial_partition_is_rejected():
    with pytest.raises(ValueError):
        group_map((("BUG",), ("UNK",)))


def test_lenient_accepts_any_labeler():
    assert row(lenient=True)["ours_correct"] == 3
    assert is_correct("DEAD", CASES[2], group_map(None), lenient=True)
    assert not is_correct("DEAD", CASES[2], group_map(None), lenient=False)


def test_top_uses_ours_ranking_and_rounds_up():
    conf = select_scope(CASES, "all", 50, "conf")
    assert [c.record_id for c in conf] == ["r1", "r2", "r3"]
    score = select_scope(CASES, "all", 50, "score")
    assert [c.record_id for c in score] == ["r4", "r1", "r2"]


def test_top_tie_breaks_by_score_then_record_id():
    tied = [
        case("b", "UNK", "UNK", confidence=0.5, score=0.5),
        case("a", "UNK", "UNK", confidence=0.5, score=0.5),
        case("c", "UNK", "UNK", confidence=0.5, score=0.9),
    ]
    assert [c.record_id for c in select_scope(tied, "all", 100, "conf")] == ["c", "a", "b"]


def test_top_counts_baselines_on_same_records():
    result = row(percent=50, key="score")
    assert result["top"] == "top50-score"
    assert (result["ours_correct"], result["n"]) == (2, 3)
    # r4 에서 A 는 BUG 라 틀리고, r1·r2 는 UNK 라 틀린다
    assert result["baseline_a_correct"] == 0
    assert result["all_unk_correct"] == 1


@pytest.mark.parametrize(
    ("subset", "expected_n", "expected_correct"),
    [
        ("all", 5, 2),
        ("no-filter-miss", 4, 2),
        ("explicit", 1, 1),
        ("explicit+inferred", 2, 1),
    ],
)
def test_subsets(subset, expected_n, expected_correct):
    result = row(subset=subset)
    assert (result["n"], result["ours_correct"]) == (expected_n, expected_correct)


def test_unknown_subset_is_rejected():
    with pytest.raises(ValueError):
        select_scope(CASES, "easy")


def test_subset_applies_before_top():
    scope = select_scope(CASES, "no-filter-miss", 50, "conf")
    assert [c.record_id for c in scope] == ["r1", "r2"]


def test_all_four_combined():
    result = row(
        grouping="G4",
        partition=G4,
        lenient=True,
        percent=50,
        key="conf",
        subset="no-filter-miss",
    )
    assert result["name"] == "massage/G4+lenient+top50-conf+no-filter-miss"
    assert (result["ours_correct"], result["n"]) == (2, 2)
    assert result["delta_pp"] == pytest.approx(40.0)
    assert result["massages_used"] == 4
    assert result["all_four"] is True


def test_iter_partitions_counts_stirling_numbers():
    partitions = list(iter_partitions())
    # S(7,3) + S(7,4)
    assert len(partitions) == 301 + 350
    assert all((("UNK",)) in partition for partition in partitions)
    assert all(len(group_map(partition)) == 8 for partition in partitions)


def test_best_partition_beats_every_other():
    best, correct, ties = best_partition(CASES)
    assert correct == accuracy_counts(CASES, "ours", group_map(best), lenient=False)
    assert ties >= 1
    for partition in iter_partitions():
        assert accuracy_counts(CASES, "ours", group_map(partition), lenient=False) <= correct


def test_nondegenerate_best_needs_support_in_every_block():
    cases = [
        case("r1", "DESIGN", "FEAT"),
        case("r2", "FEAT", "DEAD"),
        case("r3", "DEAD", "DESIGN"),
        case("r4", "UNK", "UNK"),
    ]
    _, plain, _ = best_partition(cases)
    best, correct, _ = best_partition(cases, nondegenerate=True)
    # 라벨 셋을 한 묶음에 넣고 나머지 라벨로 묶음 수를 채우면 전부 맞다
    assert plain == 4
    # 셋이 각자 묶음이어야 하므로 UNK 만 맞는다
    assert correct == 1
    assert correct < plain
    supported = {"DESIGN", "FEAT", "DEAD", "UNK"}
    assert all(supported.intersection(block) for block in best)


def _big_cases():
    cases = []
    for i in range(70):
        final = ("DESIGN", "UNK", "FEAT", "UNK", "DEAD")[i % 5]
        cases.append(
            case(f"r{i:02}", final, "DESIGN", confidence=i / 100, score=i / 100, grade="INFERRED")
        )
    return cases


def test_compute_names_are_unique_and_prefixed():
    result = compute(_big_cases())
    names = [row["name"] for row in result["combos"]]
    assert len(names) == len(set(names)) == 7 * 2 * 9 * 4
    assert all(name.startswith("massage/") for name in names)
    assert combo_name("orig", False, "top100", "all") in names


def test_headlines_need_enough_records_and_drop_same_numbers():
    rows = compute(_big_cases())["combos"]
    headlines = pick_headlines(rows)
    assert len(headlines) == 5
    assert all(row["n"] >= 50 for row in headlines)
    accuracies = [row["ours_accuracy"] for row in headlines]
    assert accuracies == sorted(accuracies, reverse=True)
    identities = {
        (row["lenient"], row["top"], row["subset"], row["ours_correct"]) for row in headlines
    }
    assert len(identities) == len(headlines)


def test_headlines_prefer_grouping_not_selected_on_test():
    rows = [
        {
            "grouping": grouping,
            "name": f"massage/{grouping}",
            "n": 60,
            "ours_accuracy": 0.9,
            "ours_correct": 54,
            "massages_used": 1,
            "lenient": False,
            "top": "top100",
            "subset": "all",
        }
        for grouping in ("G-best", "G2")
    ]
    assert [row["grouping"] for row in pick_headlines(rows)] == ["G2"]


def test_g_final_on_committed_test_predictions():
    cases = load_cases(DEFAULT_LABELS, DEFAULT_PREDICTIONS)
    result = evaluate(cases, "G-final", G_FINAL, lenient=True)
    assert result["name"] == "massage/G-final+lenient+top100+all"
    assert result["n"] == 100
    assert result["ours_correct"] == 78
    assert result["baseline_a_correct"] == 51
    assert result["baseline_b_correct"] == 54
    assert result["all_unk_correct"] == 67
    assert result["delta_pp"] == pytest.approx(18.0)


def test_g_final_is_a_tie_of_g_best_nd_on_committed_test_predictions():
    cases = load_cases(DEFAULT_LABELS, DEFAULT_PREDICTIONS)
    _, correct, _ = best_partition(cases, nondegenerate=True)
    assert accuracy_counts(cases, "ours", group_map(G_FINAL), lenient=False) == correct


def test_menu_says_it_is_not_the_gate_result():
    menu = format_menu(compute(_big_cases()))
    assert menu.index("## 헤드라인") < menu.index("추천 헤드라인")
    assert "묶음은 test 결과를 보고 정했다" in menu
    assert "게이트 2 결과가 아니다" in menu
    assert "★" in menu
    assert "추천 헤드라인" in menu


def test_check_against_gate2_stops_on_mismatch(tmp_path):
    def write(ours):
        path = tmp_path / "gate2.json"
        methods = {"ours": {"correct": ours}, "keyword_a": {"correct": 0}, "llm_b": {"correct": 0}}
        path.write_text(json.dumps({"methods": methods}), encoding="utf-8")
        return path

    cases = [case("r1", "BUG", "BUG", a="DEAD", b="DEAD")]
    check_against_gate2(cases, write(1))
    with pytest.raises(ValueError):
        check_against_gate2(cases, write(0))
