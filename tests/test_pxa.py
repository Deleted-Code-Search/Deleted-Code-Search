"""P × A 논증 숫자 테스트 (#129).

기대값은 아래 7건 픽스처를 손으로 센 것이다.

    # | split | method      | 라벨 (filter-miss 표시 *)    | 확정    | 예측   | M  | C
    1 | test  | AGREED      | sj UNK* / jh UNK*            | UNK     | UNK    | 아니오 | 예
    2 | test  | AGREED      | sj UNK* / jh UNK*            | UNK     | DESIGN | 아니오 | 아니오
    3 | test  | AGREED      | sj UNK* / jh UNK             | UNK     | UNK    | 예  | 예 (1인만)
    4 | test  | THIRD_PARTY | sj UNK* / hs DESIGN (고름)   | DESIGN  | DESIGN | 예  | 예
    5 | test  | THIRD_PARTY | sj UNK* (고름) / hs DEAD     | UNK     | UNK    | 아니오 | 예
    6 | test  | AGREED      | jh DEAD / hs DEAD            | DEAD    | FEAT   | 예  | 아니오
    7 | train | AGREED      | sj UNK* / jh UNK*            | UNK     | -      | (test 아님)

    test 6건, M 아님 {1,2,5} = 3, 한 사람이라도 filter-miss {1..5} = 5, 그중 맞힌 것 {1,5} = 2
    P(C|K) = {1,3,4,5} 4/6, P(C|M,K) = {3,4} 2/3, 직접 P(M∧C|K) = 2/6, 그중 UNK 아님 {4} = 1/6
    P = 3/4 → 경우 2 곱 0.75 × 4/6 = 0.5, Fréchet [0.75 + 4/6 − 1, 4/6] = [5/12, 2/3]
              경우 1 곱 0.75 × 2/3 = 0.5
"""

import json

import pytest

from classify.labels import CHOSEN_LABEL_FIELDS
from eval import pxa


def _label(labeler, reason, *, miss=False):
    return {
        "labeler": labeler,
        "reason_label": reason,
        "evidence_grade": "UNKNOWN" if reason == "UNK" else "INFERRED",
        "evidence_text": None,
        "evidence_source": None,
        "evidence_locator": None,
        "confidence": 0.0,
        "note": "filter-miss 1번 이동: a.py" if miss else "no-context",
    }


def _row(record_id, split, labels, chosen=None):
    source = chosen if chosen is not None else labels[0]
    final = {name: source[name] for name in CHOSEN_LABEL_FIELDS}
    final["method"] = "THIRD_PARTY" if chosen is not None else "AGREED"
    return {"record_id": record_id, "split": split, "labels": labels, "final": final}


def _fixture():
    hs_design = _label("hs", "DESIGN")
    sj_unk_miss = _label("sj", "UNK", miss=True)
    rows = [
        _row("r1", "test", [_label("sj", "UNK", miss=True), _label("jh", "UNK", miss=True)]),
        _row("r2", "test", [_label("sj", "UNK", miss=True), _label("jh", "UNK", miss=True)]),
        _row("r3", "test", [_label("sj", "UNK", miss=True), _label("jh", "UNK")]),
        _row("r4", "test", [_label("sj", "UNK", miss=True), hs_design], chosen=hs_design),
        _row("r5", "test", [sj_unk_miss, _label("hs", "DEAD")], chosen=sj_unk_miss),
        _row("r6", "test", [_label("jh", "DEAD"), _label("hs", "DEAD")]),
        _row("r7", "train", [_label("sj", "UNK", miss=True), _label("jh", "UNK", miss=True)]),
    ]
    predictions = {
        "r1": "UNK",
        "r2": "DESIGN",
        "r3": "UNK",
        "r4": "DESIGN",
        "r5": "UNK",
        "r6": "FEAT",
    }
    return rows, predictions


def test_not_meaningful_rule():
    rows, _ = _fixture()
    assert [pxa.not_meaningful(row) for row in rows[:6]] == [True, True, False, False, True, False]
    assert [pxa.any_filter_miss(row) for row in rows[:6]] == [True] * 5 + [False]


def test_compute_counts_and_cases():
    rows, predictions = _fixture()
    result = pxa.compute(rows, predictions, (3, 4), example=True)
    assert result["test_records"] == 6
    assert result["not_meaningful"] == 3
    assert result["not_meaningful_any_labeler"] == 5
    assert result["not_meaningful_correct"] == 2
    assert result["not_meaningful_gold_not_unk"] == 0
    assert (result["A_given_K"]["x"], result["A_given_K"]["n"]) == (4, 6)
    assert (result["A_given_MK"]["x"], result["A_given_MK"]["n"]) == (2, 3)
    assert (result["MC_direct"]["x"], result["MC_direct"]["n"]) == (2, 6)
    assert result["MC_reason_direct"]["x"] == 1
    assert (result["P_label"]["x"], result["P_label"]["n"]) == (3, 6)
    assert result["case2"]["product"] == pytest.approx(0.5)
    assert result["case2"]["frechet"] == pytest.approx([5 / 12, 2 / 3])
    assert result["case1"]["product"] == pytest.approx(0.5)


def test_precision_full_gives_same_scope_product():
    rows, predictions = _fixture()
    result = pxa.compute(rows, predictions, (3, 4), precision_full=(1, 2), example=True)
    assert (result["P_filter_full"]["x"], result["P_filter_full"]["n"]) == (1, 2)
    assert result["case1_full"]["product"] == pytest.approx(0.5 * 2 / 3)
    assert "case1_full" not in pxa.compute(rows, predictions, (3, 4), example=True)


def test_third_party_without_chosen_labeler_is_rejected():
    rows, predictions = _fixture()
    # r4 의 확정 라벨을 어느 라벨과도 맞지 않게 바꾼다 (제3자가 새 라벨을 만든 꼴)
    rows[3]["final"]["reason_label"] = "BUG"
    with pytest.raises(ValueError, match="r4"):
        pxa.not_meaningful(rows[3])
    with pytest.raises(ValueError, match="r4"):
        pxa.compute(rows, predictions, (3, 4), example=True)


def test_gate_mode_requires_100_test_records():
    rows, predictions = _fixture()
    with pytest.raises(ValueError, match="정확히 100건"):
        pxa.compute(rows, predictions, (3, 4))


def test_missing_prediction_is_rejected():
    rows, predictions = _fixture()
    del predictions["r6"]
    with pytest.raises(ValueError, match="예측이 없는"):
        pxa.compute(rows, predictions, (3, 4), example=True)


def test_frechet_bounds():
    assert pxa.frechet_bounds(0.76, 0.60) == pytest.approx((0.36, 0.60))
    assert pxa.frechet_bounds(0.2, 0.3) == (0.0, 0.2)


def test_product_interval_contains_point_and_skips_zero():
    low, high = pxa.product_interval(76, 100, 60, 100)
    assert low < 0.456 < high
    assert pxa.product_interval(0, 100, 60, 100) is None


def test_repo_weighted_uses_population_sizes():
    rows, predictions = _fixture()
    # r1..r3 은 큰 저장소(가중 3), r4..r6 은 작은 저장소(가중 1)
    repo_of = {f"r{i}": ("big" if i <= 3 else "small") for i in range(1, 8)}
    result = pxa.compute(
        rows,
        predictions,
        (3, 4),
        repo_of=repo_of,
        repo_sizes={"big": 3, "small": 1},
        example=True,
    )
    weighted = result["repo_weighted"]
    # big: C 2/3, small: C 2/3 → 2/3. big: M {3} 1/3, small: M {4,6} 2/3 → (3·1/3 + 2/3)/4
    assert weighted["A_given_K"] == pytest.approx(2 / 3)
    assert weighted["P_label"] == pytest.approx((1 + 2 / 3) / 4)
    # A' : big {3} 1/1, small {4,6} 1/2 → (3 + 0.5)/4
    assert weighted["A_given_MK"] == pytest.approx(3.5 / 4)


def test_filter_precision_counts_reads_kept_confusion():
    report = {"confusion": {"KEPT": {"yes": 76, "no": 24}, "NOISE_MOVE": {"yes": 1, "no": 33}}}
    assert pxa.filter_precision_counts(report) == (76, 100)


def test_main_writes_json(tmp_path):
    rows, predictions = _fixture()
    merged = tmp_path / "merged.jsonl"
    merged.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    preds = tmp_path / "ours.jsonl"
    preds.write_text(
        "".join(
            json.dumps({"record_id": key, "predicted_label": value}) + "\n"
            for key, value in predictions.items()
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "fp.json"
    precision.write_text(json.dumps({"confusion": {"KEPT": {"yes": 3, "no": 1}}}), encoding="utf-8")
    assignment = tmp_path / "assign.jsonl"
    assignment.write_text(
        "".join(json.dumps({"record_id": f"r{i}", "repo": "x"}) + "\n" for i in range(1, 8)),
        encoding="utf-8",
    )
    sizes = tmp_path / "apply.json"
    sizes.write_text(json.dumps({"model_only": {"repos": {"x": 10}}}), encoding="utf-8")
    out = tmp_path / "pxa.json"
    code = pxa.main(
        [
            str(merged),
            "--predictions",
            str(preds),
            "--precision",
            str(precision),
            "--assignment",
            str(assignment),
            "--repo-sizes",
            str(sizes),
            "--example",
            "--json-out",
            str(out),
        ]
    )
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["not_meaningful"] == 3
