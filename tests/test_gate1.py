"""게이트 1 측정 테스트 (이슈 #37).

기대값은 전부 손으로 계산해 고정한다. 판정 규칙이 바뀌면 여기서 바로 드러나야 한다 —
이 파일이 라벨링 전에 커밋됐다는 것이 "결과를 보고 맞춘 것이 아니다"의 증거다.
"""

import json
from pathlib import Path

import pytest

from classify import labels
from eval import gate1

DEFAULT_REASON = {"EXPLICIT": "BUG", "INFERRED": "LIB", "UNKNOWN": "UNK"}
DEFAULT_CONFIDENCE = {"EXPLICIT": 1.0, "INFERRED": 0.7, "UNKNOWN": 0.0}


def label_row(record_id, labeler, reason=None, grade="EXPLICIT", confidence=None, note=""):
    """가이드 §7.2 개인 라벨 1줄."""
    return {
        "record_id": record_id,
        "labeler": labeler,
        "reason_label": reason or DEFAULT_REASON[grade],
        "evidence_grade": grade,
        "evidence_text": None if grade == "UNKNOWN" else f"{labeler} 근거",
        "evidence_source": {"EXPLICIT": "commit", "INFERRED": "diff"}.get(grade),
        "evidence_locator": None,
        "confidence": DEFAULT_CONFIDENCE[grade] if confidence is None else confidence,
        "note": note,
        "labeled_at": "2026-09-22T14:03:11+09:00",
        "guide_version": "v1",
    }


def agreed(record_id, grade, confidence=None, reason=None):
    """sj·jh 2인이 같은 이유·등급·신뢰도로 라벨한 병합 1줄 (#34 merge_labels 결과)."""
    personal = {
        labeler: [label_row(record_id, labeler, reason, grade, confidence)]
        for labeler in ("sj", "jh")
    }
    return labels.merge_labels(personal)[0]


def records_of(spec):
    """[(grade, confidence), ...] → 병합 줄 목록."""
    return [agreed(f"r{index:03d}", grade, conf) for index, (grade, conf) in enumerate(spec)]


def tiers_of(report):
    return {result.tier.key: result for result in report.recovery.tiers}


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


# --------------------------------------------------------------------------------------
# 회수율 3구간 — 손계산
# --------------------------------------------------------------------------------------


def test_three_tiers_match_hand_computation_and_split_decision():
    """손계산 (10건, 전부 2인 일치):

    EXPLICIT 3 / INFERRED 0.9 × 2 / INFERRED 0.6 × 2 / UNKNOWN 3
    EXPLICIT만              3        / 10 = 30% → 축 이동
    + INFERRED(≥0.5)   3 + 2 + 2 = 7 / 10 = 70% → 진행
    + INFERRED(≥0.8)   3 + 2     = 5 / 10 = 50% → 저장소 기준 강화
    세 판정이 다르다 → 판정 갈림
    """
    spec = [("EXPLICIT", None)] * 3 + [("INFERRED", 0.9)] * 2 + [("INFERRED", 0.6)] * 2
    spec += [("UNKNOWN", None)] * 3
    report = gate1.build_report(records_of(spec))
    tiers = tiers_of(report)

    assert [r.recovered for r in report.recovery.tiers] == [3, 7, 5]
    assert [r.resolved for r in report.recovery.tiers] == [10, 10, 10]
    assert tiers["explicit_only"].rate == pytest.approx(0.3)
    assert tiers["inferred_min"].rate == pytest.approx(0.7)
    assert tiers["inferred_strong"].rate == pytest.approx(0.5)
    assert tiers["explicit_only"].verdict == gate1.VERDICT_PIVOT
    assert tiers["inferred_min"].verdict == gate1.VERDICT_PROCEED
    assert tiers["inferred_strong"].verdict == gate1.VERDICT_TIGHTEN
    assert report.recovery.decision == gate1.VERDICT_SPLIT
    assert any(gate1.VERDICT_SPLIT in warning for warning in report.warnings)


def test_same_verdict_on_all_tiers_is_the_decision():
    """EXPLICIT 7 / UNKNOWN 3 → 세 구간 모두 7/10 = 70% → 진행."""
    report = gate1.build_report(records_of([("EXPLICIT", None)] * 7 + [("UNKNOWN", None)] * 3))

    assert [r.recovered for r in report.recovery.tiers] == [7, 7, 7]
    assert report.recovery.decision == gate1.VERDICT_PROCEED


def test_report_is_never_a_single_number():
    report = gate1.build_report(records_of([("EXPLICIT", None)]))
    as_dict = report.to_dict()

    assert len(as_dict["recovery"]["tiers"]) == 3
    assert "3구간" in "\n".join(gate1.format_markdown(report))


# --------------------------------------------------------------------------------------
# 경계값 — 코드와 docs/evaluation.md 가 같은 쪽을 포함해야 한다 (전부 "이상")
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recovered", "resolved", "expected"),
    [
        (3, 5, gate1.VERDICT_PROCEED),  # 정확히 60% → 진행
        (120, 200, gate1.VERDICT_PROCEED),  # 정확히 60%, 200건 규모
        (599, 1000, gate1.VERDICT_TIGHTEN),  # 59.9%
        (2, 5, gate1.VERDICT_TIGHTEN),  # 정확히 40% → 저장소 기준 강화
        (80, 200, gate1.VERDICT_TIGHTEN),  # 정확히 40%, 200건 규모
        (399, 1000, gate1.VERDICT_PIVOT),  # 39.9%
        (0, 10, gate1.VERDICT_PIVOT),
        (10, 10, gate1.VERDICT_PROCEED),
        (0, 0, gate1.VERDICT_UNDECIDABLE),
    ],
)
def test_gate1_boundaries_are_inclusive(recovered, resolved, expected):
    assert gate1.gate1_verdict(recovered, resolved) == expected


def test_exactly_60_and_40_percent_through_the_whole_report():
    at_60 = gate1.build_report(records_of([("EXPLICIT", None)] * 6 + [("UNKNOWN", None)] * 4))
    at_40 = gate1.build_report(records_of([("EXPLICIT", None)] * 4 + [("UNKNOWN", None)] * 6))

    assert at_60.recovery.decision == gate1.VERDICT_PROCEED
    assert at_40.recovery.decision == gate1.VERDICT_TIGHTEN


def test_confidence_exactly_0_5_and_0_8_are_included():
    """INFERRED 0.5 → ≥0.5 구간에 들어가고 ≥0.8 구간에는 안 들어간다.
    INFERRED 0.8 → 두 구간 모두. 0.79 → ≥0.5 구간만.

    손계산: ≥0.5 구간 3/3, ≥0.8 구간 1/3
    """
    report = gate1.build_report(
        records_of([("INFERRED", 0.5), ("INFERRED", 0.8), ("INFERRED", 0.79)])
    )
    tiers = tiers_of(report)

    assert tiers["explicit_only"].recovered == 0
    assert tiers["inferred_min"].recovered == 3
    assert tiers["inferred_strong"].recovered == 1


@pytest.mark.parametrize(
    ("grade", "confidence", "expected"),
    [
        ("EXPLICIT", 1.0, [True, True, True]),
        ("INFERRED", 0.5, [False, True, False]),
        ("INFERRED", 0.8, [False, True, True]),
        ("INFERRED", 0.49, [False, False, False]),
        ("INFERRED", None, [False, False, False]),
        ("UNKNOWN", 0.0, [False, False, False]),
        (None, None, [False, False, False]),
    ],
)
def test_counts_as_recovered_per_tier(grade, confidence, expected):
    got = [gate1.counts_as_recovered(grade, confidence, tier) for tier in gate1.TIERS]
    assert got == expected


# --------------------------------------------------------------------------------------
# 어떤 신뢰도로 구간을 나누나 / 분모
# --------------------------------------------------------------------------------------


def test_two_labelers_confidence_uses_the_lower_one():
    """sj 0.9, jh 0.7 로 INFERRED 일치. #34 는 이걸 AGREED 로 자동 확정하면서 sj 의 0.9 만 옮긴다.
    그 값을 쓰면 ≥0.8 구간에 들어가 버린다. 작은 쪽(0.7)을 써서 ≥0.8 구간에서 뺀다."""
    personal = {
        "sj": [label_row("r1", "sj", "LIB", "INFERRED", 0.9)],
        "jh": [label_row("r1", "jh", "LIB", "INFERRED", 0.7)],
    }
    merged = labels.merge_labels(personal)
    assert merged[0]["final"]["method"] == "AGREED"
    assert merged[0]["final"]["confidence"] == 0.9  # #34 동작 확인 — 이 값을 쓰면 안 된다

    tiers = tiers_of(gate1.build_report(merged))
    assert tiers["inferred_min"].recovered == 1
    assert tiers["inferred_strong"].recovered == 0


def test_discussed_final_confidence_is_used():
    """등급이 갈렸지만(EXPLICIT vs INFERRED) 토론으로 INFERRED 0.85 확정 → ≥0.8 구간에 든다."""
    row = {
        "record_id": "r1",
        "batch": "pre200",
        "split": None,
        "labels": [
            label_row("r1", "sj", "LIB", "EXPLICIT"),
            label_row("r1", "jh", "LIB", "INFERRED", 0.6),
        ],
        "final": {
            "reason_label": "LIB",
            "evidence_grade": "INFERRED",
            "evidence_text": "대체 코드가 json.loads 호출이다",
            "evidence_source": "diff",
            "evidence_locator": "diff:replacement",
            "confidence": 0.85,
            "method": "DISCUSSED",
            "adjudicated_by": ["sj", "jh"],
            "adjudicated_at": "2026-09-23T20:10:00+09:00",
            "note": "커밋 메시지는 이유가 아니라 수단이었다",
        },
        "guide_version": "v1",
    }
    report = gate1.build_report([row])
    tiers = tiers_of(report)

    assert report.recovery.unresolved == 0
    assert tiers["explicit_only"].recovered == 0
    assert tiers["inferred_strong"].recovered == 1


def test_unresolved_grade_is_left_out_of_the_denominator():
    """r1: 2인 EXPLICIT 일치 / r2: sj EXPLICIT vs jh UNKNOWN (토론 전) / r3: sj 혼자 라벨.
    손계산: 분모 1 (r1), 회수 1 → 100%, 미확정 2건."""
    personal = {
        "sj": [
            label_row("r1", "sj"),
            label_row("r2", "sj"),
            label_row("r3", "sj"),
        ],
        "jh": [
            label_row("r1", "jh"),
            label_row("r2", "jh", "UNK", "UNKNOWN"),
        ],
    }
    report = gate1.build_report(labels.merge_labels(personal))

    assert [r.resolved for r in report.recovery.tiers] == [1, 1, 1]
    assert [r.recovered for r in report.recovery.tiers] == [1, 1, 1]
    assert report.recovery.unresolved == 2
    assert any("분모에서 뺐다" in warning for warning in report.warnings)


def test_inferred_below_0_5_is_warned_and_never_recovered():
    report = gate1.build_report(records_of([("INFERRED", 0.3)]))

    assert [r.recovered for r in report.recovery.tiers] == [0, 0, 0]
    assert report.inferred_below_min == 2  # 2인 × 1건
    assert any("§6.2" in warning for warning in report.warnings)


def test_no_resolved_record_is_undecidable():
    personal = {"sj": [label_row("r1", "sj")]}
    report = gate1.build_report(labels.merge_labels(personal))

    assert report.recovery.decision == gate1.VERDICT_UNDECIDABLE


# --------------------------------------------------------------------------------------
# kappa (계산은 #34 labels.py 것을 쓴다. 여기서는 보고와 경고를 본다)
# --------------------------------------------------------------------------------------


def test_kappa_perfect_agreement_is_one():
    personal = {
        "sj": [label_row(f"r{i}", "sj", reason) for i, reason in enumerate(["BUG", "PERF"] * 2)],
        "jh": [label_row(f"r{i}", "jh", reason) for i, reason in enumerate(["BUG", "PERF"] * 2)],
    }
    report = gate1.build_report(labels.merge_labels(personal))
    (reason,) = report.agreements["reason_label"]

    assert reason.pair == ("jh", "sj")
    assert reason.kappa == pytest.approx(1.0)
    assert not any("§11" in warning for warning in report.warnings)


def test_kappa_complete_disagreement_is_negative_and_warned():
    """sj BUG,PERF / jh PERF,BUG → p_o = 0, p_e = 0.5 → κ = −1.0"""
    personal = {
        "sj": [label_row("r0", "sj", "BUG"), label_row("r1", "sj", "PERF")],
        "jh": [label_row("r0", "jh", "PERF"), label_row("r1", "jh", "BUG")],
    }
    report = gate1.build_report(labels.merge_labels(personal))
    (reason,) = report.agreements["reason_label"]

    assert reason.kappa < 0
    assert reason.kappa == pytest.approx(-1.0)
    assert any("§11" in warning and "reason_label" in warning for warning in report.warnings)


@pytest.mark.parametrize(
    ("kappa", "warned"),
    [(0.6, False), (0.6000000001, False), (0.5999, True), (0.2, True), (None, False)],
)
def test_kappa_warning_boundary(kappa, warned):
    """정확히 0.6 은 경고하지 않는다 (CHARTER §11 "kappa < 0.6")."""
    result = labels.Agreement(pair=("jh", "sj"), field_name="reason_label", total=10, kappa=kappa)
    warnings = gate1.kappa_warnings({"reason_label": [result]})

    assert bool(warnings) is warned


# --------------------------------------------------------------------------------------
# 분포·교차표
# --------------------------------------------------------------------------------------


def test_distribution_and_crosstab():
    """개별 라벨 8건: BUG/EXPLICIT 4, LIB/INFERRED 2, DEAD/INFERRED 1, DESIGN/INFERRED 1
    확정 레코드 3건: BUG/EXPLICIT 2, LIB/INFERRED 1 (DEAD vs DESIGN 은 이유 미확정)"""
    personal = {
        "sj": [
            label_row("r1", "sj"),
            label_row("r2", "sj"),
            label_row("r3", "sj", "LIB", "INFERRED", 0.9),
            label_row("r4", "sj", "DEAD", "INFERRED", 0.7),
        ],
        "jh": [
            label_row("r1", "jh"),
            label_row("r2", "jh"),
            label_row("r3", "jh", "LIB", "INFERRED", 0.9),
            label_row("r4", "jh", "DESIGN", "INFERRED", 0.7),
        ],
    }
    report = gate1.build_report(labels.merge_labels(personal))
    by_label = report.labels_distribution
    by_record = report.records_distribution

    assert by_label.total == 8
    assert by_label.crosstab["BUG"]["EXPLICIT"] == 4
    assert by_label.crosstab["DEAD"]["INFERRED"] == 1
    assert by_label.by_grade == {"EXPLICIT": 4, "INFERRED": 4, "UNKNOWN": 0}
    assert by_label.by_reason["LIB"] == 2
    assert by_record.total == 3
    assert by_record.crosstab["BUG"]["EXPLICIT"] == 2
    assert by_record.crosstab["LIB"]["INFERRED"] == 1
    assert by_record.by_reason["DEAD"] == 0


# --------------------------------------------------------------------------------------
# 상수 — 바꾸면 티가 나게
# --------------------------------------------------------------------------------------

THRESHOLDS = (
    "INFERRED_MIN_CONFIDENCE",
    "INFERRED_STRONG_CONFIDENCE",
    "GATE1_PROCEED",
    "GATE1_TIGHTEN",
    "KAPPA_RISK",
)


def test_threshold_values_are_the_pre_registered_ones():
    """사전 확정 값. 이 테스트를 고치는 커밋은 회의 결정과 근거를 본문에 적는다."""
    assert gate1.INFERRED_MIN_CONFIDENCE == 0.5
    assert gate1.INFERRED_STRONG_CONFIDENCE == 0.8
    assert gate1.GATE1_PROCEED == 0.60
    assert gate1.GATE1_TIGHTEN == 0.40
    assert gate1.KAPPA_RISK == 0.60


def test_every_threshold_constant_cites_its_source():
    source = Path(gate1.__file__).read_text(encoding="utf-8").splitlines()
    for name in THRESHOLDS:
        index = next(i for i, line in enumerate(source) if line.startswith(f"{name} = "))
        comment = source[index - 1]
        assert comment.startswith("#") and "§" in comment, name


def test_thresholds_agree_with_labels_module():
    """#34 labels.py 도 같은 기준을 들고 있다. 한쪽만 바뀌면 두 리포트의 판정이 갈린다."""
    assert gate1.GATE1_PROCEED == labels.GATE1_PASS
    assert gate1.GATE1_TIGHTEN == labels.GATE1_TIGHTEN
    assert gate1.KAPPA_RISK == labels.KAPPA_WARN


# --------------------------------------------------------------------------------------
# 입력 검사와 CLI
# --------------------------------------------------------------------------------------


def test_invalid_values_are_reported_with_location(tmp_path):
    path = tmp_path / "sj_pre200.jsonl"
    rows = [
        label_row("r1", "sj", reason="bug "),  # 오타
        {**label_row("r2", "sj", "LIB", "INFERRED"), "confidence": None},  # 신뢰도 없음
        label_row("r3", "sj", "UNK", "EXPLICIT"),  # UNK 인데 EXPLICIT
        label_row("r4", "sj"),
        label_row("r4", "sj"),  # 중복
    ]
    write_jsonl(path, rows)

    problems = gate1.load_inputs([path]).problems

    assert any(":1:" in p and "reason_label" in p for p in problems)
    assert any(":2:" in p and "confidence" in p for p in problems)
    assert any(":3:" in p and "UNKNOWN" in p for p in problems)
    assert any(":5:" in p and "중복" in p for p in problems)
    assert gate1.main([str(path)]) == 2


def test_empty_templates_only_means_nothing_to_measure(tmp_path):
    path = tmp_path / "sj_pre200.jsonl"
    empty = {**label_row("r1", "sj"), "reason_label": None, "evidence_grade": None}
    write_jsonl(path, [empty])

    assert gate1.load_inputs([path]).merged == []
    assert gate1.main([str(path)]) == 1


def test_main_writes_json_and_markdown(tmp_path, capsys):
    write_jsonl(tmp_path / "sj_pre200.jsonl", [label_row("r1", "sj"), label_row("r2", "sj")])
    write_jsonl(
        tmp_path / "jh_pre200.jsonl",
        [label_row("r1", "jh"), label_row("r2", "jh", "UNK", "UNKNOWN")],
    )
    write_jsonl(
        tmp_path / "pre200_records.jsonl",
        [{"record_id": "r1", "repo": "psf/requests"}, {"record_id": "r2", "repo": "pallets/flask"}],
    )
    json_out = tmp_path / "out" / "gate1.json"
    md_out = tmp_path / "out" / "gate1.md"

    code = gate1.main(
        [
            str(tmp_path / "sj_pre200.jsonl"),
            str(tmp_path / "jh_pre200.jsonl"),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
        ]
    )

    assert code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert [tier["key"] for tier in report["recovery"]["tiers"]] == [
        "explicit_only",
        "inferred_min",
        "inferred_strong",
    ]
    assert report["constants"]["GATE1_PROCEED"] == 0.6
    assert {repo["name"] for repo in report["per_repo"]} == {"psf/requests", "pallets/flask"}
    markdown = md_out.read_text(encoding="utf-8")
    assert "이유 회수율 3구간" in markdown
    assert "판정" in markdown
    assert markdown == capsys.readouterr().out


def test_merged_file_input_keeps_discussed_final(tmp_path):
    row = labels.merge_labels(
        {
            "sj": [label_row("r1", "sj", "LIB", "EXPLICIT")],
            "jh": [label_row("r1", "jh", "LIB", "INFERRED", 0.6)],
        }
    )[0]
    row["final"] = {
        "reason_label": "LIB",
        "evidence_grade": "EXPLICIT",
        "confidence": 1.0,
        "method": "DISCUSSED",
        "note": "PR 본문에 이유가 있었다",
    }
    merged_path = tmp_path / "labeled_500.jsonl"
    write_jsonl(merged_path, [row])

    inputs = gate1.load_inputs([merged_path])
    report = gate1.build_report(inputs.merged)

    assert inputs.problems == []
    assert [r.recovered for r in report.recovery.tiers] == [1, 1, 1]
