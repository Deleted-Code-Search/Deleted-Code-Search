"""게이트 1 예비 200건 사전 확정 규칙 병합 테스트 (이슈 #76).

병합 규칙은 팀이 실제 라벨 데이터를 보기 전에 확정했다(이슈 본문). 기대값은 그 규칙을 손으로
따라간 것이다 — 규칙이 바뀌면(팀 회의 없이는 바뀌지 않는다) 여기서 바로 드러나야 한다.
"""

import json

import pytest

from classify import labels
from eval import gate1, gate1_merge

DEFAULT_REASON = {"EXPLICIT": "BUG", "INFERRED": "LIB", "UNKNOWN": "UNK"}
DEFAULT_CONFIDENCE = {"EXPLICIT": 1.0, "INFERRED": 0.7, "UNKNOWN": 0.0}


def label_row(record_id, labeler, grade="EXPLICIT", *, reason=None, confidence=None, note=""):
    """가이드 §7.2 개인 라벨 1줄."""
    return {
        "record_id": record_id,
        "labeler": labeler,
        "reason_label": reason or DEFAULT_REASON[grade],
        "evidence_grade": grade,
        "evidence_text": None if grade == "UNKNOWN" else f"{labeler} 근거",
        "evidence_source": {"EXPLICIT": "commit", "INFERRED": "diff"}.get(grade),
        "evidence_locator": None if grade == "UNKNOWN" else f"{labeler}:file.py:1",
        "confidence": DEFAULT_CONFIDENCE[grade] if confidence is None else confidence,
        "note": note,
        "labeled_at": "2026-09-22T14:03:11+09:00",
        "guide_version": "v1",
    }


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def personal_of(rows_by_labeler):
    """{labeler: [row, ...]} 형태를 merge_pre200 입력으로 채운다 (빠진 라벨러는 빈 목록)."""
    return {labeler: rows_by_labeler.get(labeler, []) for labeler in labels.LABELERS}


# --------------------------------------------------------------------------------------
# 병합 규칙 — evidence_grade
# --------------------------------------------------------------------------------------


def test_explicit_plus_explicit_stays_explicit():
    pair = [label_row("r1", "sj", "EXPLICIT"), label_row("r1", "jh", "EXPLICIT")]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["evidence_grade"] == "EXPLICIT"
    assert final["confidence"] == 1.0


def test_inferred_plus_inferred_uses_the_lower_confidence():
    """PR #38 규칙: INFERRED+INFERRED 는 두 값 중 작은 쪽을 쓴다."""
    pair = [
        label_row("r1", "sj", "INFERRED", confidence=0.9),
        label_row("r1", "jh", "INFERRED", confidence=0.6),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["evidence_grade"] == "INFERRED"
    assert final["confidence"] == 0.6


def test_explicit_plus_inferred_becomes_inferred():
    pair = [
        label_row("r1", "sj", "EXPLICIT"),
        label_row("r1", "jh", "INFERRED", confidence=0.65),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["evidence_grade"] == "INFERRED"
    assert final["confidence"] == 0.65  # min(1.0, 0.65)


@pytest.mark.parametrize(
    "other_grade",
    ["EXPLICIT", "INFERRED", "UNKNOWN"],
)
def test_any_unknown_forces_unknown(other_grade):
    pair = [
        label_row("r1", "sj", "UNKNOWN"),
        label_row("r1", "jh", other_grade),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["evidence_grade"] == "UNKNOWN"
    assert final["confidence"] == 0.0  # UNKNOWN 쪽이 항상 0.0 이라 min 도 0.0


# --------------------------------------------------------------------------------------
# 병합 규칙 — reason_label (같으면 기록, 다르면 null. 등급과 무관)
# --------------------------------------------------------------------------------------


EVIDENCE_FIELDS = ("evidence_text", "evidence_source", "evidence_locator")


def test_same_reason_and_same_grade_keeps_reason_and_evidence():
    pair = [
        label_row("r1", "sj", "EXPLICIT", reason="BUG"),
        label_row("r1", "jh", "EXPLICIT", reason="BUG"),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["reason_label"] == "BUG"
    assert final["evidence_grade"] == "EXPLICIT"
    # 등급 동률 → 라벨러 이름 알파벳순으로 앞선 jh 의 근거 (기존 동작)
    assert final["evidence_text"] == "jh 근거"
    assert final["evidence_source"] == "commit"
    assert final["evidence_locator"] == "jh:file.py:1"
    assert "일치해" in final["note"]


@pytest.mark.parametrize("order", [("sj", "jh"), ("jh", "sj")])
def test_same_reason_and_different_grade_keeps_reason_and_weaker_evidence(order):
    """등급만 갈리면 reason 은 합의된 것이다. 더 약한 등급 쪽 근거를 남긴다."""
    strong, weak = order
    pair = [
        label_row("r1", strong, "EXPLICIT", reason="BUG"),
        label_row("r1", weak, "INFERRED", reason="BUG", confidence=0.7),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["reason_label"] == "BUG"
    assert final["evidence_grade"] == "INFERRED"
    assert final["confidence"] == 0.7
    assert final["evidence_text"] == f"{weak} 근거"
    assert final["evidence_source"] == "diff"
    assert final["evidence_locator"] == f"{weak}:file.py:1"


def test_different_reason_and_same_grade_nulls_reason_and_evidence():
    """reason 이 달라도(BUG vs DESIGN) evidence_grade·confidence 는 규칙대로만 정해진다."""
    pair = [
        label_row("r1", "sj", "INFERRED", reason="BUG", confidence=0.8),
        label_row("r1", "jh", "INFERRED", reason="DESIGN", confidence=0.6),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["reason_label"] is None
    assert all(final[field] is None for field in EVIDENCE_FIELDS)
    assert final["evidence_grade"] == "INFERRED"
    assert final["confidence"] == 0.6
    assert "null" in final["note"]


@pytest.mark.parametrize("order", [("sj", "jh"), ("jh", "sj")])
def test_different_reason_and_different_grade_nulls_reason_and_evidence(order):
    """약한 등급 쪽 라벨러의 reason·evidence 도 넣지 않는다. 등급·confidence 는 규칙대로."""
    strong, weak = order
    pair = [
        label_row("r1", strong, "EXPLICIT", reason="BUG"),
        label_row("r1", weak, "INFERRED", reason="DESIGN", confidence=0.7),
    ]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["reason_label"] is None
    assert all(final[field] is None for field in EVIDENCE_FIELDS)
    assert final["evidence_grade"] == "INFERRED"
    assert final["confidence"] == 0.7


def test_labels_keep_both_original_evidence_when_final_evidence_is_null():
    personal = personal_of(
        {
            "sj": [label_row("r1", "sj", "EXPLICIT", reason="BUG")],
            "jh": [label_row("r1", "jh", "INFERRED", reason="DESIGN", confidence=0.7)],
        }
    )
    row = gate1_merge.merge_pre200(personal)[0]

    assert row["final"]["reason_label"] is None
    assert all(row["final"][field] is None for field in EVIDENCE_FIELDS)

    by_labeler = {entry["labeler"]: entry for entry in row["labels"]}
    assert by_labeler["sj"]["reason_label"] == "BUG"
    assert by_labeler["sj"]["evidence_text"] == "sj 근거"
    assert by_labeler["sj"]["evidence_source"] == "commit"
    assert by_labeler["sj"]["evidence_locator"] == "sj:file.py:1"
    assert by_labeler["jh"]["reason_label"] == "DESIGN"
    assert by_labeler["jh"]["evidence_text"] == "jh 근거"
    assert by_labeler["jh"]["evidence_source"] == "diff"
    assert by_labeler["jh"]["evidence_locator"] == "jh:file.py:1"


def test_method_marks_automatic_gate1_rule_not_discussion():
    pair = [label_row("r1", "sj", "EXPLICIT"), label_row("r1", "jh", "EXPLICIT")]
    final = gate1_merge.merge_evidence_grade(pair)

    assert final["method"] == "GATE1_RULE"
    assert final["method"] not in ("AGREED", "DISCUSSED", "THIRD_PARTY")


# --------------------------------------------------------------------------------------
# 불일치 집계
# --------------------------------------------------------------------------------------


def test_count_grade_mismatches():
    personal = personal_of(
        {
            "sj": [
                label_row("r1", "sj", "EXPLICIT"),
                label_row("r2", "sj", "INFERRED", confidence=0.8),
                label_row("r3", "sj", "EXPLICIT"),
            ],
            "jh": [
                label_row("r1", "jh", "EXPLICIT"),  # 일치
                label_row("r2", "jh", "UNKNOWN"),  # 불일치
            ],
            "hs": [
                label_row("r3", "hs", "INFERRED", confidence=0.6),  # 불일치
            ],
        }
    )
    merged = gate1_merge.merge_pre200(personal)

    assert gate1_merge.count_grade_mismatches(merged) == 2


def reason_stats_personal():
    """r1 합의(BUG) / r2 합의(LIB, 등급만 다름) / r3 합의(BUG) / r4 불일치(BUG vs LIB) /
    r5 불일치(BUG vs DESIGN, 등급도 다름)."""
    return personal_of(
        {
            "sj": [
                label_row("r1", "sj", "EXPLICIT", reason="BUG"),
                label_row("r2", "sj", "EXPLICIT", reason="LIB"),
                label_row("r3", "sj", "EXPLICIT", reason="BUG"),
                label_row("r4", "sj", "EXPLICIT", reason="BUG"),
                label_row("r5", "sj", "EXPLICIT", reason="BUG"),
            ],
            "jh": [
                label_row("r1", "jh", "EXPLICIT", reason="BUG"),
                label_row("r2", "jh", "INFERRED", reason="LIB", confidence=0.7),
                label_row("r3", "jh", "EXPLICIT", reason="BUG"),
                label_row("r4", "jh", "EXPLICIT", reason="LIB"),
                label_row("r5", "jh", "INFERRED", reason="DESIGN", confidence=0.7),
            ],
        }
    )


def test_reason_distribution_counts_only_agreed_reasons():
    merged = gate1_merge.merge_pre200(reason_stats_personal())

    # 불일치 r4(BUG vs LIB), r5(BUG vs DESIGN)는 어느 이유에도 들어가지 않는다
    assert gate1_merge.reason_distribution(merged) == {"BUG": 2, "LIB": 1}


def test_count_reason_mismatches():
    merged = gate1_merge.merge_pre200(reason_stats_personal())

    assert gate1_merge.count_reason_mismatches(merged) == 2
    assert gate1_merge.count_grade_mismatches(merged) == 2  # r2, r5 — reason 통계와 별개


def test_reason_mismatch_keeps_original_labels_for_kappa():
    """final 은 null 이어도 원본 labels 쌍은 그대로라 kappa 계산 입력이 바뀌지 않는다."""
    merged = gate1_merge.merge_pre200(reason_stats_personal())
    r4 = next(row for row in merged if row["record_id"] == "r4")

    assert r4["final"]["reason_label"] is None
    assert sorted(entry["reason_label"] for entry in r4["labels"]) == ["BUG", "LIB"]


def test_count_grade_mismatches_is_zero_when_all_agree():
    personal = personal_of(
        {
            "sj": [label_row("r1", "sj", "EXPLICIT")],
            "jh": [label_row("r1", "jh", "EXPLICIT")],
        }
    )
    merged = gate1_merge.merge_pre200(personal)

    assert gate1_merge.count_grade_mismatches(merged) == 0


# --------------------------------------------------------------------------------------
# record_id 기반 대응 (줄 번호로 짝짓지 않는다)
# --------------------------------------------------------------------------------------


def test_pairing_follows_record_id_not_line_order():
    """세 파일의 줄 순서가 서로 다르고, 레코드마다 실제로 라벨한 2인이 다르다.

    sj 파일: r2, r1 순서 / jh 파일: r1, r3 순서 / hs 파일: r3, r2 순서.
    줄 번호로 짝지으면 (sj의 1번째=r2)-(jh의 1번째=r1)처럼 엉뚱하게 묶인다. record_id 로
    묶으면 r1=sj+jh, r2=sj+hs, r3=jh+hs 가 되어야 한다.
    """
    personal = personal_of(
        {
            "sj": [
                label_row("r2", "sj", "INFERRED", confidence=0.9),
                label_row("r1", "sj", "EXPLICIT"),
            ],
            "jh": [
                label_row("r1", "jh", "EXPLICIT"),
                label_row("r3", "jh", "INFERRED", confidence=0.6),
            ],
            "hs": [
                label_row("r3", "hs", "EXPLICIT"),
                label_row("r2", "hs", "INFERRED", confidence=0.5),
            ],
        }
    )
    merged = gate1_merge.merge_pre200(personal)
    by_id = {row["record_id"]: row for row in merged}

    assert {entry["labeler"] for entry in by_id["r1"]["labels"]} == {"sj", "jh"}
    assert {entry["labeler"] for entry in by_id["r2"]["labels"]} == {"sj", "hs"}
    assert {entry["labeler"] for entry in by_id["r3"]["labels"]} == {"jh", "hs"}

    # r2: sj EXPLICIT(1.0) vs hs INFERRED(0.5) → INFERRED, confidence min(1.0, 0.5) = 0.5
    assert by_id["r2"]["final"]["evidence_grade"] == "INFERRED"
    assert by_id["r2"]["final"]["confidence"] == 0.5
    # r3: jh INFERRED(0.6) vs hs EXPLICIT(1.0) → INFERRED, confidence 0.6
    assert by_id["r3"]["final"]["evidence_grade"] == "INFERRED"
    assert by_id["r3"]["final"]["confidence"] == 0.6


# --------------------------------------------------------------------------------------
# 누락 / 중복 / 비정상 입력 검증
# --------------------------------------------------------------------------------------


def test_missing_second_labeler_is_reported():
    """1인만 라벨한 레코드는 이 규칙으로 병합할 수 없다 (가이드 §8.1 전건 2인)."""
    personal = personal_of({"sj": [label_row("r1", "sj", "EXPLICIT")]})
    problems = gate1_merge.find_pairing_problems(personal)

    assert len(problems) == 1
    assert "r1" in problems[0]
    assert "라벨러 1명" in problems[0]


def test_duplicate_record_in_same_labeler_file_is_reported():
    personal = personal_of(
        {
            "sj": [
                label_row("r1", "sj", "EXPLICIT"),
                label_row("r1", "sj", "INFERRED", confidence=0.6),
            ],
            "jh": [label_row("r1", "jh", "EXPLICIT")],
        }
    )
    problems = gate1_merge.find_pairing_problems(personal)

    assert any("중복" in p and "r1" in p for p in problems)


def test_three_labelers_on_one_record_is_reported():
    """블록 배분은 2인만 맡긴다 (§8.1). 3인이 라벨했으면 배분 오류다."""
    personal = personal_of(
        {
            "sj": [label_row("r1", "sj", "EXPLICIT")],
            "jh": [label_row("r1", "jh", "EXPLICIT")],
            "hs": [label_row("r1", "hs", "EXPLICIT")],
        }
    )
    problems = gate1_merge.find_pairing_problems(personal)

    assert any("라벨러 3명" in p for p in problems)


def test_load_personal_reports_malformed_json_line(tmp_path):
    (tmp_path / "sj_pre200.jsonl").write_text("{not json}\n", encoding="utf-8")
    for labeler in ("jh", "hs"):
        (tmp_path / f"{labeler}_pre200.jsonl").write_text("", encoding="utf-8")

    _, problems = gate1_merge.load_personal(tmp_path)

    assert any("JSON 이 아니다" in p for p in problems)


def test_load_personal_reports_undefined_enum_value(tmp_path):
    write_jsonl(tmp_path / "sj_pre200.jsonl", [label_row("r1", "sj", reason="BUGG")])
    write_jsonl(tmp_path / "jh_pre200.jsonl", [label_row("r1", "jh")])
    (tmp_path / "hs_pre200.jsonl").write_text("", encoding="utf-8")

    _, problems = gate1_merge.load_personal(tmp_path)

    assert any("reason_label" in p and "BUGG" in p for p in problems)


def test_load_personal_reports_missing_file(tmp_path):
    write_jsonl(tmp_path / "sj_pre200.jsonl", [label_row("r1", "sj")])
    # jh_pre200.jsonl, hs_pre200.jsonl 없음

    _, problems = gate1_merge.load_personal(tmp_path)

    assert any("파일이 없다" in p for p in problems)


@pytest.mark.parametrize(
    ("grade", "wrong_confidence"),
    [("EXPLICIT", 0.9), ("UNKNOWN", 0.3)],
)
def test_load_personal_rejects_confidence_not_fixed_for_grade(tmp_path, grade, wrong_confidence):
    """EXPLICIT 은 confidence 1.0, UNKNOWN 은 confidence 0.0 으로 고정이다 (가이드 §6.1, §6.3).

    병합이 min 을 쓰므로, 잘못된 고정값이 조용히 통과하면 게이트 1 회수율이 틀어질 수 있다.
    """
    row = label_row("r1", "sj", grade)
    row["confidence"] = wrong_confidence
    write_jsonl(tmp_path / "sj_pre200.jsonl", [row])
    write_jsonl(tmp_path / "jh_pre200.jsonl", [label_row("r1", "jh", grade)])
    (tmp_path / "hs_pre200.jsonl").write_text("", encoding="utf-8")

    _, problems = gate1_merge.load_personal(tmp_path)

    assert any("고정" in p and "confidence" in p for p in problems)


def test_load_personal_reports_labeler_mismatched_with_file(tmp_path):
    """sj_pre200.jsonl 파일에 labeler="jh" 인 줄이 섞이면 grouping 이 엉뚱한 짝을 만든다."""
    write_jsonl(tmp_path / "sj_pre200.jsonl", [label_row("r1", "jh")])
    write_jsonl(tmp_path / "jh_pre200.jsonl", [label_row("r1", "jh")])
    (tmp_path / "hs_pre200.jsonl").write_text("", encoding="utf-8")

    _, problems = gate1_merge.load_personal(tmp_path)

    assert any("labeler" in p and "'sj'" in p for p in problems)


def test_load_personal_reports_non_numeric_confidence(tmp_path):
    row = label_row("r1", "sj", "EXPLICIT")
    row["confidence"] = "높음"  # 손으로 고친 흔적
    write_jsonl(tmp_path / "sj_pre200.jsonl", [row])
    write_jsonl(tmp_path / "jh_pre200.jsonl", [label_row("r1", "jh")])
    (tmp_path / "hs_pre200.jsonl").write_text("", encoding="utf-8")

    _, problems = gate1_merge.load_personal(tmp_path)

    assert any("confidence" in p and "높음" in p for p in problems)


def test_valid_personal_files_have_no_problems(tmp_path):
    write_jsonl(tmp_path / "sj_pre200.jsonl", [label_row("r1", "sj", "EXPLICIT")])
    write_jsonl(tmp_path / "jh_pre200.jsonl", [label_row("r1", "jh", "INFERRED", confidence=0.6)])
    (tmp_path / "hs_pre200.jsonl").write_text("", encoding="utf-8")

    personal, problems = gate1_merge.load_personal(tmp_path)

    assert problems == []
    assert [row["record_id"] for row in personal["sj"]] == ["r1"]


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


@pytest.fixture
def labels_dir(tmp_path):
    directory = tmp_path / "labels"
    directory.mkdir()
    write_jsonl(
        directory / "sj_pre200.jsonl",
        [
            label_row("r1", "sj", "EXPLICIT"),
            label_row("r2", "sj", "INFERRED", confidence=0.9),
        ],
    )
    write_jsonl(
        directory / "jh_pre200.jsonl",
        [
            label_row("r1", "jh", "EXPLICIT"),
            label_row("r3", "jh", "UNKNOWN"),
        ],
    )
    write_jsonl(
        directory / "hs_pre200.jsonl",
        [
            label_row("r2", "hs", "INFERRED", confidence=0.6),
            label_row("r3", "hs", "EXPLICIT"),
        ],
    )
    return directory


def test_cli_writes_merged_file_and_reports_grade_mismatches(labels_dir, capsys):
    exit_code = gate1_merge.main(["--labels-dir", str(labels_dir)])

    assert exit_code == 0
    out_path = labels_dir / gate1_merge.MERGED_FILENAME
    merged = [json.loads(line) for line in out_path.read_text("utf-8").splitlines()]
    assert {row["record_id"] for row in merged} == {"r1", "r2", "r3"}

    by_id = {row["record_id"]: row for row in merged}
    assert by_id["r1"]["final"]["evidence_grade"] == "EXPLICIT"
    assert by_id["r2"]["final"]["evidence_grade"] == "INFERRED"  # 둘 다 INFERRED, min(0.9,0.6)
    assert by_id["r2"]["final"]["confidence"] == 0.6
    assert by_id["r3"]["final"]["evidence_grade"] == "UNKNOWN"  # jh UNKNOWN vs hs EXPLICIT
    assert by_id["r1"]["final"]["reason_label"] == "BUG"
    assert by_id["r2"]["final"]["reason_label"] == "LIB"
    assert by_id["r3"]["final"]["reason_label"] is None  # jh UNK vs hs BUG

    out = capsys.readouterr().out
    assert "evidence_grade 가 달랐던 레코드: 1건 / 3건" in out
    assert "reason_label 이 달랐던 레코드: 1건 / 3건" in out
    assert "합의 레코드 2건의 분포" in out
    assert "BUG: 1건" in out
    assert "LIB: 1건" in out


def test_cli_stops_and_writes_nothing_on_pairing_problem(tmp_path):
    directory = tmp_path / "labels"
    directory.mkdir()
    write_jsonl(directory / "sj_pre200.jsonl", [label_row("r1", "sj", "EXPLICIT")])
    (directory / "jh_pre200.jsonl").write_text("", encoding="utf-8")
    (directory / "hs_pre200.jsonl").write_text("", encoding="utf-8")

    exit_code = gate1_merge.main(["--labels-dir", str(directory)])

    assert exit_code == 2
    assert not (directory / gate1_merge.MERGED_FILENAME).exists()


def test_cli_reports_nothing_labeled(tmp_path):
    directory = tmp_path / "labels"
    directory.mkdir()
    for labeler in ("sj", "jh", "hs"):
        (directory / f"{labeler}_pre200.jsonl").write_text("", encoding="utf-8")

    assert gate1_merge.main(["--labels-dir", str(directory)]) == 1


# --------------------------------------------------------------------------------------
# eval/gate1.py 가 실제로 읽는지 (연동 확인)
# --------------------------------------------------------------------------------------


def test_gate1_py_reads_the_merged_output(labels_dir):
    """이 CLI 의 출력이 eval/gate1.py 의 입력 계약(가이드 §7.3, load_inputs)을 만족하는지."""
    assert gate1_merge.main(["--labels-dir", str(labels_dir)]) == 0
    merged_path = labels_dir / gate1_merge.MERGED_FILENAME

    inputs = gate1.load_inputs([merged_path])
    assert inputs.problems == []
    assert len(inputs.merged) == 3

    report = gate1.build_report(inputs.merged)
    # r1 EXPLICIT / r2 INFERRED(0.6) / r3 UNKNOWN — 전부 등급이 확정된다(토론 없이)
    assert report.recovery.unresolved == 0
    tiers = {r.tier.key: r for r in report.recovery.tiers}
    assert tiers["explicit_only"].recovered == 1
    assert tiers["inferred_min"].recovered == 2  # EXPLICIT 1 + INFERRED(0.6≥0.5) 1
    assert tiers["inferred_strong"].recovered == 1  # EXPLICIT 만 (0.6 < 0.8)


def test_gate1_check_final_allows_null_reason_only_for_gate1_rule():
    final = {"reason_label": None, "evidence_grade": "EXPLICIT", "confidence": 1.0}

    assert gate1.check_final({**final, "method": "GATE1_RULE"}, "x") == []
    assert gate1.check_final({**final, "method": "DISCUSSED"}, "x") != []


def test_gate1_reason_kappa_still_uses_original_label_pairs(labels_dir):
    """reason 불일치 건(r3)이 final 에서 null 이어도 kappa 는 원본 labels 쌍으로 계산된다."""
    assert gate1_merge.main(["--labels-dir", str(labels_dir)]) == 0
    inputs = gate1.load_inputs([labels_dir / gate1_merge.MERGED_FILENAME])

    results = gate1.build_report(inputs.merged).agreements["reason_label"]

    # r1(sj-jh) r2(sj-hs) r3(jh-hs) 각 1건. r3(UNK vs BUG)도 final 이 null 이라고 빠지지 않는다
    assert sum(result.total for result in results) == 3
    assert sum(result.agreed for result in results) == 2


def test_gate1_main_end_to_end_on_merge_output(labels_dir, capsys, tmp_path):
    """gate1_merge → eval.gate1 main() 까지 CLI 로 실제로 이어지는지."""
    assert gate1_merge.main(["--labels-dir", str(labels_dir)]) == 0
    merged_path = labels_dir / gate1_merge.MERGED_FILENAME

    json_out = tmp_path / "gate1.json"
    exit_code = gate1.main([str(merged_path), "--json-out", str(json_out)])

    assert exit_code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["records"] == 3
    assert report["recovery"]["resolved"] == 3
