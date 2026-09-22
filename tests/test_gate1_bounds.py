"""게이트 1 회수율 범위 테스트 (이슈 #78).

기대값은 아래 6건 픽스처를 손으로 따라간 것이다.

    # | 라벨 쌍              | 하한 (약한 등급, min) | 상한 (강한 등급, 채택된 쪽)
    1 | E 1.0 + E 1.0       | E 1.0               | E 1.0
    2 | E 1.0 + I 0.6       | I 0.6               | E 1.0
    3 | E 1.0 + U 0.0       | U 0.0               | E 1.0
    4 | I 0.9 + U 0.0       | U 0.0               | I 0.9
    5 | I 0.6 + I 0.9       | I 0.6               | I 0.9
    6 | U 0.0 + U 0.0       | U 0.0               | U 0.0

    구간            | 하한 회수      | 상한 회수          | 차이 원인
    EXPLICIT만      | 1 → 1/6       | 1,2,3 → 3/6       | E↔I 1 (#2), E↔U 1 (#3)
    +INFERRED≥0.5  | 1,2,5 → 3/6   | 1,2,3,4,5 → 5/6   | E↔U 1 (#3), I↔U 1 (#4)
    +INFERRED≥0.8  | 1 → 1/6       | 1,2,3,4,5 → 5/6   | E↔I 1, E↔U 1, I↔U 1, I↔I(신뢰도) 1
"""

import json

import pytest

from eval import gate1, gate1_bounds

PAIRS = [
    (("EXPLICIT", 1.0), ("EXPLICIT", 1.0)),
    (("EXPLICIT", 1.0), ("INFERRED", 0.6)),
    (("EXPLICIT", 1.0), ("UNKNOWN", 0.0)),
    (("INFERRED", 0.9), ("UNKNOWN", 0.0)),
    (("INFERRED", 0.6), ("INFERRED", 0.9)),
    (("UNKNOWN", 0.0), ("UNKNOWN", 0.0)),
]


def label(labeler, grade, confidence):
    return {
        "labeler": labeler,
        "reason_label": "UNK" if grade == "UNKNOWN" else "DEAD",
        "evidence_grade": grade,
        "confidence": confidence,
        "note": "",
    }


def merged_rows():
    rows = []
    for index, (first, second) in enumerate(PAIRS, 1):
        rows.append(
            {
                "record_id": f"r{index}",
                "labels": [label("sj", *first), label("jh", *second)],
                "final": None,
            }
        )
    return rows


@pytest.fixture
def bounds():
    results, disagreements, excluded = gate1_bounds.compute_bounds(merged_rows())
    return {item.tier.key: item for item in results}, disagreements, excluded


def test_resolve_lower_takes_weaker_grade_and_min_confidence():
    labels = [label("sj", "EXPLICIT", 1.0), label("jh", "INFERRED", 0.6)]
    assert gate1_bounds.resolve_lower(labels) == gate1_bounds.Resolution("INFERRED", 0.6)


def test_resolve_upper_takes_stronger_grade_and_its_own_confidence():
    # 신뢰도는 채택된 등급을 준 라벨의 값이다. 두 값의 max(0.9)가 아니다.
    labels = [label("sj", "EXPLICIT", 0.7), label("jh", "INFERRED", 0.9)]
    assert gate1_bounds.resolve_upper(labels) == gate1_bounds.Resolution("EXPLICIT", 0.7)


def test_resolve_upper_same_grade_takes_max_confidence():
    labels = [label("sj", "INFERRED", 0.6), label("jh", "INFERRED", 0.9)]
    assert gate1_bounds.resolve_upper(labels) == gate1_bounds.Resolution("INFERRED", 0.9)


def test_resolve_without_grade_is_none():
    assert gate1_bounds.resolve_lower([]) == gate1_bounds.Resolution(None, None)
    assert gate1_bounds.resolve_upper([{"evidence_grade": None}]) == gate1_bounds.Resolution(
        None, None
    )


@pytest.mark.parametrize(
    ("key", "lower", "upper"),
    [("explicit_only", 1, 3), ("inferred_min", 3, 5), ("inferred_strong", 1, 5)],
)
def test_tier_lower_and_upper_counts(bounds, key, lower, upper):
    by_key, _, excluded = bounds
    assert (by_key[key].lower, by_key[key].upper, by_key[key].resolved) == (lower, upper, 6)
    assert excluded == 0


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("explicit_only", {"E↔I": 1, "E↔U": 1}),
        ("inferred_min", {"E↔U": 1, "I↔U": 1}),
        ("inferred_strong", {"E↔I": 1, "E↔U": 1, "I↔U": 1, "I↔I(신뢰도)": 1}),
    ],
)
def test_diff_types_per_tier(bounds, key, expected):
    by_key, _, _ = bounds
    assert dict(by_key[key].diff_types) == expected


def test_grade_disagreement_types(bounds):
    _, disagreements, _ = bounds
    # #5 는 등급이 같아서 등급 불일치가 아니다.
    assert dict(disagreements) == {"E↔I": 1, "E↔U": 1, "I↔U": 1}


def test_verdicts_use_gate1_thresholds(bounds):
    by_key, _, _ = bounds
    # 1/6 = 16.7% 축 이동, 3/6 = 50% 강화, 5/6 = 83.3% 진행
    assert by_key["explicit_only"].lower_verdict == gate1.VERDICT_PIVOT
    assert by_key["explicit_only"].upper_verdict == gate1.VERDICT_TIGHTEN
    assert by_key["inferred_min"].lower_verdict == gate1.VERDICT_TIGHTEN
    assert by_key["inferred_min"].upper_verdict == gate1.VERDICT_PROCEED
    assert not any(item.same_verdict for item in by_key.values())


def test_same_verdict_when_both_bounds_proceed():
    rows = [
        {"record_id": f"e{index}", "labels": [label("sj", "EXPLICIT", 1.0)] * 2}
        for index in range(3)
    ] + [{"record_id": "x", "labels": [label("sj", "EXPLICIT", 1.0), label("jh", "UNKNOWN", 0.0)]}]
    results, _, _ = gate1_bounds.compute_bounds(rows)
    # 하한 3/4 = 75%, 상한 4/4 = 100% — 둘 다 진행
    assert all(item.same_verdict for item in results)
    assert results[0].lower_verdict == gate1.VERDICT_PROCEED


def test_lower_bound_equals_gate1_on_merge_rule_final():
    """하한은 #76 규칙 그 자체라, 그 규칙으로 채운 final 을 eval/gate1.py 가 읽은 값과 같다."""
    rows = merged_rows()
    for row in rows:
        lower = gate1_bounds.resolve_lower(row["labels"])
        row["final"] = {
            "evidence_grade": lower.grade,
            "confidence": lower.confidence,
            "method": "GATE1_RULE",
        }
    results, _, _ = gate1_bounds.compute_bounds(rows)
    assert [item.lower for item in results] == gate1_bounds.gate1_lower(rows)


def test_cli_writes_json(tmp_path, capsys):
    source = tmp_path / "merged.jsonl"
    source.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged_rows()),
        encoding="utf-8",
    )
    out = tmp_path / "bounds.json"
    # final 이 None 이면 eval/gate1.py 는 2인 일치 건만 세므로 하한과 달라 1을 돌려준다.
    assert gate1_bounds.main([str(source), "--json-out", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert [tier["key"] for tier in report["tiers"]] == [
        "explicit_only",
        "inferred_min",
        "inferred_strong",
    ]
    assert report["tiers"][1]["lower"]["recovered"] == 3
    assert report["tiers"][1]["upper"]["recovered"] == 5
    assert report["grade_disagreements"] == {"E↔I": 1, "E↔U": 1, "I↔U": 1}
    assert "하한 = eval/gate1.py" in capsys.readouterr().out


def test_read_merged_rejects_personal_label_file(tmp_path):
    path = tmp_path / "sj_pre200.jsonl"
    path.write_text(json.dumps(label("sj", "EXPLICIT", 1.0)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="labels 키 없음"):
        gate1_bounds.read_merged(path)
