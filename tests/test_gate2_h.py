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

이 7건은 test 가 6건이라 게이트 실행(정확히 100건)에서는 거부된다. 그래서 위 기대값은 모두
예제 모드(`example=True`, CLI `--example`)로 돌린다. 100건 검증은 아래 따로 시험한다.
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
    result = gate2_h.compute(ROWS, ASSIGNMENT, example=True)
    assert result["agreement"] == {"records": 6, "denominator": 5, "agreed": 3, "excluded": 1}
    assert result["H"] == pytest.approx(0.6)
    assert result["accuracy_threshold"] == pytest.approx(0.6)


def test_reference_after_50_and_rejudged_count():
    result = gate2_h.compute(ROWS, ASSIGNMENT, example=True)
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
    assert (
        gate2_h.compute(flipped, ASSIGNMENT, example=True)["H"]
        == gate2_h.compute(ROWS, ASSIGNMENT, example=True)["H"]
    )


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
        gate2_h.compute(ROWS, bad, example=True)


def _write_inputs(tmp_path, rows, assignment):
    merged = tmp_path / "merged.jsonl"
    merged.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assignment_path = tmp_path / "assignment.jsonl"
    assignment_path.write_text(
        "".join(json.dumps({"record_id": k, **v}) + "\n" for k, v in assignment.items()),
        encoding="utf-8",
    )
    return merged, assignment_path


def test_cli_example_mode_writes_json(tmp_path, capsys):
    merged, assignment = _write_inputs(tmp_path, ROWS, ASSIGNMENT)
    out = tmp_path / "h.json"
    code = gate2_h.main(
        [str(merged), "--assignment", str(assignment), "--json-out", str(out), "--example"]
    )
    assert code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["agreement"]["agreed"] == 3
    assert result["example"] is True
    output = capsys.readouterr().out
    assert "예제 모드" in output
    assert "H = 3/5 = 60.0%" in output


# --- 게이트 실행: test 가 정확히 100건이 아니면 결과를 내지 않는다 ---


def _test_rows(count):
    """test 레코드 `count` 건. 짝수 번째만 두 라벨이 같다 → 일치 ceil(count/2)."""
    rows = [
        _row(f"t{i}", "test", "B", ("DEAD", "DEAD" if i % 2 == 0 else "FEAT")) for i in range(count)
    ]
    assignment = {row["record_id"]: {"split": "test", "interim": False} for row in rows}
    return rows, assignment


def test_gate_mode_rejects_example_fixture():
    with pytest.raises(ValueError, match="정확히 100건"):
        gate2_h.compute(ROWS, ASSIGNMENT)


@pytest.mark.parametrize("count", [99, 101])
def test_gate_mode_rejects_not_100(count):
    rows, assignment = _test_rows(count)
    with pytest.raises(ValueError, match=f"{count}건"):
        gate2_h.compute(rows, assignment)


def test_gate_mode_accepts_exactly_100():
    rows, assignment = _test_rows(100)
    train = _row("x", "train", "A", ("BUG", "BUG"))  # test 가 아닌 건은 100건에 세지 않는다
    result = gate2_h.compute([*rows, train], {**assignment, "x": {"split": "train"}})
    assert result["agreement"] == {"records": 100, "denominator": 100, "agreed": 50, "excluded": 0}
    assert result["H"] == pytest.approx(0.5)
    assert result["example"] is False


def test_cli_gate_mode_fails_without_output(tmp_path, capsys):
    merged, assignment = _write_inputs(tmp_path, ROWS, ASSIGNMENT)
    out = tmp_path / "h.json"
    code = gate2_h.main([str(merged), "--assignment", str(assignment), "--json-out", str(out)])
    assert code == 1
    assert not out.exists()
    captured = capsys.readouterr()
    assert "H =" not in captured.out
    assert "정확히 100건" in captured.err
