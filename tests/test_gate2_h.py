"""게이트 2 H 테스트 (#92).

기대값은 아래 7건 픽스처를 손으로 센 것이다. 쌍이 섞여 있어도 한 분모로 풀링한다.

    # | split | 1~50번 | 쌍     | reason_label        | 비고
    1 | test  | 예     | sj+jh | DESIGN / DESIGN     | 일치 (v2 유지)
    2 | test  | 예     | sj+jh | DEAD / DEAD         | 일치 (v3 재판정)
    3 | test  | 아니오 | jh+hs | UNK / DESIGN        | 불일치
    4 | test  | 아니오 | jh+hs | FEAT / FEAT         | 일치. final 은 BUG - 읽지 않아야 한다
    5 | test  | 아니오 | hs+sj | DEAD / FEAT         | 불일치
    6 | test  | 아니오 | hs+sj | UNK / UNK (anchored) | 분모에서 빠짐
    7 | train | 아니오 | sj+jh | BUG / BUG           | test 가 아님

    H = 일치 {1, 2, 4} / 분모 {1..5} = 3/5 = 60%, 빠진 레코드 1건 (#6)
    51번 이후 = {3, 4, 5} 중 일치 {4} = 1/3, 빠진 레코드 1건 (#6)
    1~50번 = {1, 2}, 그중 두 라벨이 모두 v3 = {2} → 재판정 1건
    기준 = min(80%, 60%) = 60%
"""

import json

import pytest

from eval import gate2_h

PAIRS = {"A": ("sj", "jh"), "B": ("jh", "hs"), "C": ("hs", "sj")}


def _label(labeler, reason, version, note=""):
    return {
        "labeler": labeler,
        "reason_label": reason,
        "evidence_grade": "UNKNOWN" if reason == "UNK" else "INFERRED",
        "note": note,
        "guide_version": version,
    }


def _row(record_id, split, block, reasons, version="v3", anchored=False, final="UNK"):
    left, right = PAIRS[block]
    note = "anchored" if anchored else ""
    return {
        "record_id": record_id,
        "split": split,
        "labels": [
            _label(left, reasons[0], version, note),
            _label(right, reasons[1], version),
        ],
        "final": {"reason_label": final},
    }


ROWS = [
    _row("r1", "test", "A", ("DESIGN", "DESIGN"), version="v2", final="DESIGN"),
    _row("r2", "test", "A", ("DEAD", "DEAD"), final="DEAD"),
    _row("r3", "test", "B", ("UNK", "DESIGN"), final="DESIGN"),
    _row("r4", "test", "B", ("FEAT", "FEAT"), final="BUG"),
    _row("r5", "test", "C", ("DEAD", "FEAT"), final="DEAD"),
    _row("r6", "test", "C", ("UNK", "UNK"), anchored=True),
    _row("r7", "train", "A", ("BUG", "BUG"), final="BUG"),
]
ASSIGNMENT = {
    "r1": {"split": "test", "interim": True},
    "r2": {"split": "test", "interim": True},
    "r3": {"split": "test", "interim": False},
    "r4": {"split": "test", "interim": False},
    "r5": {"split": "test", "interim": False},
    "r6": {"split": "test", "interim": False},
    "r7": {"split": "train", "interim": False},
}


def test_h_matches_hand_count():
    result = gate2_h.compute(ROWS, ASSIGNMENT)
    assert result["agreement"] == {"records": 6, "denominator": 5, "agreed": 3, "excluded": 1}
    assert result["H"] == pytest.approx(0.6)
    assert result["accuracy_threshold"] == pytest.approx(0.6)


def test_reference_after_50_and_rejudged_count():
    result = gate2_h.compute(ROWS, ASSIGNMENT)
    later = result["reference_after_50"]
    assert (later["records"], later["denominator"], later["agreed"], later["excluded"]) == (
        4,
        3,
        1,
        1,
    )
    assert result["interim_records"] == 2
    assert result["rejudged_v3_records"] == 1


def test_final_is_not_read():
    """final 을 바꿔도 H 는 그대로다 — 독립 라벨끼리만 비교한다."""
    flipped = [{**row, "final": {"reason_label": "SEC"}} for row in ROWS]
    assert gate2_h.compute(flipped, ASSIGNMENT)["H"] == gate2_h.compute(ROWS, ASSIGNMENT)["H"]


@pytest.mark.parametrize(("h", "expected"), [(0.6, 0.6), (0.8, 0.8), (0.808, 0.8), (1.0, 0.8)])
def test_threshold_is_capped_at_80(h, expected):
    assert gate2_h.accuracy_threshold(h) == pytest.approx(expected)


def test_wilson_interval_known_value():
    low, high = gate2_h.wilson_interval(80, 99)
    assert low == pytest.approx(0.7203, abs=1e-3)
    assert high == pytest.approx(0.8743, abs=1e-3)


def test_split_mismatch_is_rejected():
    bad = {**ASSIGNMENT, "r3": {"split": "val", "interim": False}}
    with pytest.raises(ValueError, match="split"):
        gate2_h.compute(ROWS, bad)


def test_cli_writes_json(tmp_path, capsys):
    merged = tmp_path / "merged.jsonl"
    merged.write_text("".join(json.dumps(row) + "\n" for row in ROWS), encoding="utf-8")
    assignment = tmp_path / "assignment.jsonl"
    assignment.write_text(
        "".join(json.dumps({"record_id": k, **v}) + "\n" for k, v in ASSIGNMENT.items()),
        encoding="utf-8",
    )
    out = tmp_path / "h.json"
    code = gate2_h.main([str(merged), "--assignment", str(assignment), "--json-out", str(out)])
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["agreement"]["agreed"] == 3
    assert "H = 3/5 = 60.0%" in capsys.readouterr().out
