"""라벨 병합·일치도·회수율 테스트 (이슈 #34).

kappa 는 손으로 계산한 값을 고정한다 (가이드 §8.4 공식). 계산이 바뀌면 바로 드러나게.
"""

import json

import pytest

from classify import labels


def label_row(record_id, labeler, reason="BUG", grade="EXPLICIT", note="", **extra):
    """가이드 §7.2 개인 라벨 1줄."""
    row = {
        "record_id": record_id,
        "labeler": labeler,
        "reason_label": reason,
        "evidence_grade": grade,
        "evidence_text": f"{labeler} 가 적은 근거",
        "evidence_source": "commit",
        "evidence_locator": "commit:message",
        "confidence": 1.0,
        "note": note,
        "labeled_at": "2026-09-22T14:03:11+09:00",
        "guide_version": "v1",
    }
    row.update(extra)
    return row


def empty_row(record_id, labeler):
    """#33 이 만든 빈 틀 (사람이 아직 안 채운 상태)."""
    return label_row(record_id, labeler, reason=None, grade=None)


# --------------------------------------------------------------------------------------
# Cohen's kappa — 손계산 고정 (가이드 §8.4)
# --------------------------------------------------------------------------------------


def test_kappa_perfect_agreement_on_two_classes():
    """BUG 1건·PERF 1건이 전부 일치. p_o=1, p_e=0.5² 두 개 합 = 0.5 → κ=1.0"""
    pairs = [("BUG", "BUG"), ("PERF", "PERF")]
    kappa, observed, expected = labels.cohens_kappa(pairs, labels.REASON_LABELS)

    assert observed == 1.0
    assert expected == pytest.approx(0.5)
    assert kappa == pytest.approx(1.0)


def test_kappa_hand_computed_partial_agreement():
    """손계산:

    A: BUG 6 / PERF 4, B: BUG 5 / PERF 5, 일치 7건 (BUG 4 + PERF 3)
    p_o = 7/10 = 0.7
    p_e = (0.6 × 0.5) + (0.4 × 0.5) = 0.30 + 0.20 = 0.50
    κ   = (0.7 − 0.5) / (1 − 0.5) = 0.4
    """
    pairs = (
        [("BUG", "BUG")] * 4
        + [("BUG", "PERF")] * 2
        + [("PERF", "BUG")] * 1
        + [("PERF", "PERF")] * 3
    )
    kappa, observed, expected = labels.cohens_kappa(pairs, labels.REASON_LABELS)

    assert observed == pytest.approx(0.7)
    assert expected == pytest.approx(0.5)
    assert kappa == pytest.approx(0.4)


def test_kappa_complete_disagreement_is_negative():
    """p_o=0, p_e=0.5 → κ = (0−0.5)/0.5 = −1.0"""
    pairs = [("BUG", "PERF"), ("PERF", "BUG")]
    kappa, _, _ = labels.cohens_kappa(pairs, labels.REASON_LABELS)

    assert kappa == pytest.approx(-1.0)


def test_kappa_is_undefined_when_only_one_class_is_used():
    """둘 다 UNK 만 썼다 → p_e = 1 이라 나눗셈이 정의되지 않는다. 0이나 1로 채우지 않는다."""
    kappa, observed, expected = labels.cohens_kappa([("UNK", "UNK")] * 5, labels.REASON_LABELS)

    assert kappa is None
    assert observed == 1.0
    assert expected == pytest.approx(1.0)


def test_kappa_of_empty_input():
    assert labels.cohens_kappa([], labels.REASON_LABELS) == (None, 0.0, 0.0)


# --------------------------------------------------------------------------------------
# 병합 (가이드 §7.3)
# --------------------------------------------------------------------------------------


def test_merge_keeps_both_labels_and_auto_confirms_agreement():
    personal = {
        "sj": [label_row("r1", "sj")],
        "jh": [label_row("r1", "jh")],
        "hs": [],
    }
    merged = labels.merge_labels(personal)

    assert len(merged) == 1
    row = merged[0]
    assert [entry["labeler"] for entry in row["labels"]] == ["sj", "jh"]
    assert row["final"]["method"] == "AGREED"
    assert row["final"]["reason_label"] == "BUG"
    assert row["batch"] == "pre200"
    assert row["split"] is None


def test_merge_leaves_final_empty_when_reasons_differ():
    """확정은 두 사람이 근거를 말하고 나서 하는 토론이다 (§8.3). 기계가 고르지 않는다."""
    personal = {
        "sj": [label_row("r1", "sj", reason="BUG")],
        "jh": [label_row("r1", "jh", reason="DESIGN")],
        "hs": [],
    }
    merged = labels.merge_labels(personal)

    assert merged[0]["final"] is None
    assert len(merged[0]["labels"]) == 2


def test_merge_leaves_final_empty_when_only_grade_differs():
    personal = {
        "sj": [label_row("r1", "sj", grade="EXPLICIT")],
        "jh": [label_row("r1", "jh", grade="INFERRED")],
        "hs": [],
    }
    assert labels.merge_labels(personal)[0]["final"] is None


def test_merge_ignores_unfilled_template_rows():
    personal = {"sj": [label_row("r1", "sj"), empty_row("r2", "sj")], "jh": [], "hs": []}
    merged = labels.merge_labels(personal)

    assert [row["record_id"] for row in merged] == ["r1"]


def test_merge_does_not_modify_individual_labels():
    """개별 라벨을 고치면 kappa 를 다시 계산할 수 없다 (§8.3 5번)."""
    original = label_row("r1", "sj", reason="BUG")
    personal = {"sj": [original], "jh": [label_row("r1", "jh", reason="DESIGN")], "hs": []}
    labels.merge_labels(personal)

    assert original["reason_label"] == "BUG"
    assert original["evidence_text"] == "sj 가 적은 근거"


def test_merge_carries_over_human_adjudicated_final():
    """토론 결과에는 "왜 그렇게 정했는지"가 들어 있다. 재병합이 지우면 토론을 다시 해야 한다."""
    personal = {
        "sj": [label_row("r1", "sj", reason="BUG")],
        "jh": [label_row("r1", "jh", reason="DESIGN")],
        "hs": [],
    }
    existing = [
        {
            "record_id": "r1",
            "final": {
                "reason_label": "BUG",
                "evidence_grade": "EXPLICIT",
                "method": "DISCUSSED",
                "note": "jh 가 커밋 메시지를 안 봤다",
            },
        }
    ]
    merged = labels.merge_labels(personal, existing=existing)

    assert merged[0]["final"]["method"] == "DISCUSSED"
    assert merged[0]["final"]["note"] == "jh 가 커밋 메시지를 안 봤다"


def test_merge_recomputes_auto_confirmed_final():
    """AGREED 는 labels 에서 다시 계산한다 — 라벨이 바뀌면 확정도 바뀌어야 한다."""
    personal = {
        "sj": [label_row("r1", "sj", reason="PERF")],
        "jh": [label_row("r1", "jh", reason="DESIGN")],
        "hs": [],
    }
    existing = [{"record_id": "r1", "final": {"reason_label": "BUG", "method": "AGREED"}}]
    merged = labels.merge_labels(personal, existing=existing)

    assert merged[0]["final"] is None  # 이제 불일치라 확정이 사라진다


def test_disagreements_are_split_by_field():
    """이유가 갈리는 건은 §5 문제, 등급만 갈리는 건은 §6 문제라 대응이 다르다."""
    personal = {
        "sj": [
            label_row("reason-gap", "sj", reason="BUG"),
            label_row("grade-gap", "sj", grade="EXPLICIT"),
            label_row("ok", "sj"),
        ],
        "jh": [
            label_row("reason-gap", "jh", reason="LIB"),
            label_row("grade-gap", "jh", grade="INFERRED"),
            label_row("ok", "jh"),
        ],
        "hs": [],
    }
    found = labels.find_disagreements(labels.merge_labels(personal))

    assert found["reason_label"] == ["reason-gap"]
    assert found["evidence_grade"] == ["grade-gap"]


# --------------------------------------------------------------------------------------
# anchored 제외 (#7 코멘트 5번)
# --------------------------------------------------------------------------------------


def test_anchored_labels_are_excluded_from_agreement():
    """라벨러가 "끌렸다"고 표시한 건은 독립 라벨이 아니라 kappa 를 부풀린다."""
    personal = {
        "sj": [label_row("r1", "sj"), label_row("r2", "sj", note="anchored LLM 후보를 봤다")],
        "jh": [label_row("r1", "jh"), label_row("r2", "jh")],
        "hs": [],
    }
    merged = labels.merge_labels(personal)
    results = labels.agreement_for(merged, "reason_label", labels.REASON_LABELS)

    assert len(results) == 1
    assert results[0].total == 1  # r2 는 빠졌다


def test_pairs_are_grouped_per_labeler_pair():
    personal = {
        "sj": [label_row("r1", "sj"), label_row("r3", "sj")],
        "jh": [label_row("r1", "jh"), label_row("r2", "jh")],
        "hs": [label_row("r2", "hs"), label_row("r3", "hs")],
    }
    merged = labels.merge_labels(personal)
    results = labels.agreement_for(merged, "reason_label", labels.REASON_LABELS)

    assert {result.pair for result in results} == {("jh", "sj"), ("hs", "jh"), ("hs", "sj")}
    assert all(result.total == 1 for result in results)


def test_agreement_reports_per_class_and_confusions():
    personal = {
        "sj": [label_row(f"r{i}", "sj", reason="BUG") for i in range(3)]
        + [label_row("r9", "sj", reason="DESIGN")],
        "jh": [label_row(f"r{i}", "jh", reason="BUG") for i in range(3)]
        + [label_row("r9", "jh", reason="FEAT")],
        "hs": [],
    }
    merged = labels.merge_labels(personal)
    result = labels.agreement_for(merged, "reason_label", labels.REASON_LABELS)[0]

    assert result.per_class["BUG"] == (3, 3)
    assert ("DESIGN", "FEAT", 1) in result.confusions


def test_mean_kappa_skips_undefined_values():
    defined = labels.Agreement(pair=("a", "b"), field_name="x", kappa=0.8)
    undefined = labels.Agreement(pair=("b", "c"), field_name="x", kappa=None)

    assert labels.mean_kappa([defined, undefined]) == pytest.approx(0.8)
    assert labels.mean_kappa([undefined]) is None


# --------------------------------------------------------------------------------------
# 이유 회수율 (§15) + 게이트 1
# --------------------------------------------------------------------------------------


def test_resolved_grade_prefers_final_then_agreement():
    confirmed = {
        "labels": [label_row("r", "sj", grade="INFERRED")],
        "final": {"evidence_grade": "EXPLICIT"},
    }
    agreed = {
        "labels": [label_row("r", "sj", grade="INFERRED"), label_row("r", "jh", grade="INFERRED")],
        "final": None,
    }
    split = {
        "labels": [label_row("r", "sj", grade="EXPLICIT"), label_row("r", "jh", grade="UNKNOWN")],
        "final": None,
    }

    assert labels.resolved_grade(confirmed) == "EXPLICIT"
    assert labels.resolved_grade(agreed) == "INFERRED"
    assert labels.resolved_grade(split) is None  # 토론 전에는 세지 않는다


def make_merged(grades, repo_of=None):
    """등급 목록 → 병합 레코드 (2인 일치 상태)."""
    rows = []
    for index, grade in enumerate(grades):
        record_id = f"r{index}"
        reason = "UNK" if grade == "UNKNOWN" else "BUG"
        rows.append(
            {
                "record_id": record_id,
                "batch": "pre200",
                "split": None,
                "labels": [
                    label_row(record_id, "sj", reason=reason, grade=grade),
                    label_row(record_id, "jh", reason=reason, grade=grade),
                ],
                "final": {"evidence_grade": grade, "reason_label": reason, "method": "AGREED"},
                "guide_version": "v1",
            }
        )
    return rows


def test_recovery_rate_counts_explicit_and_inferred():
    merged = make_merged(["EXPLICIT", "INFERRED", "UNKNOWN", "UNKNOWN"])
    overall, _ = labels.recovery_rate(merged)

    assert overall.resolved == 4
    assert overall.recovered == 2
    assert overall.rate == pytest.approx(0.5)
    assert overall.by_grade == {"EXPLICIT": 1, "INFERRED": 1, "UNKNOWN": 2}


def test_unresolved_records_are_excluded_from_the_denominator():
    """불일치 건을 한쪽으로 세면 회수율이 토론보다 먼저 정해진다."""
    merged = make_merged(["EXPLICIT", "EXPLICIT"])
    merged.append(
        {
            "record_id": "gap",
            "labels": [
                label_row("gap", "sj", grade="EXPLICIT"),
                label_row("gap", "jh", grade="UNKNOWN"),
            ],
            "final": None,
        }
    )
    overall, _ = labels.recovery_rate(merged)

    assert overall.resolved == 2
    assert overall.unresolved == 1
    assert overall.rate == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("grades", "expected"),
    [
        (["EXPLICIT"] * 7 + ["UNKNOWN"] * 3, "통과"),
        (["EXPLICIT"] * 5 + ["UNKNOWN"] * 5, "경계"),
        (["EXPLICIT"] * 3 + ["UNKNOWN"] * 7, "미달"),
    ],
)
def test_gate1_verdict_follows_the_charter_thresholds(grades, expected):
    overall, _ = labels.recovery_rate(make_merged(grades))
    assert expected in overall.gate1


def test_recovery_rate_per_repo():
    merged = make_merged(["EXPLICIT", "UNKNOWN", "EXPLICIT"])
    repo_of = {"r0": "a/b", "r1": "a/b", "r2": "c/d"}
    overall, per_repo = labels.recovery_rate(merged, repo_of)

    assert overall.rate == pytest.approx(2 / 3)
    rates = {item.name: item.rate for item in per_repo}
    assert rates["a/b"] == pytest.approx(0.5)
    assert rates["c/d"] == pytest.approx(1.0)


def test_unknown_cause_tags_are_counted():
    """분포가 게이트 1 대응 방향을 가른다 (가이드 §6.3, §11)."""
    merged = [
        {
            "record_id": "r1",
            "labels": [
                label_row("r1", "sj", reason="UNK", grade="UNKNOWN", note="no-context"),
                label_row("r1", "jh", reason="UNK", grade="UNKNOWN", note="vague-message 였다"),
            ],
            "final": None,
        },
        {
            "record_id": "r2",
            "labels": [label_row("r2", "sj", reason="UNK", grade="UNKNOWN", note="")],
            "final": None,
        },
    ]
    causes = labels.unknown_causes(merged)

    assert causes["no-context"] == 1
    assert causes["vague-message"] == 1
    assert causes["(태그 없음)"] == 1


def unknown_rows(*notes: str) -> list[dict]:
    return [
        {
            "record_id": f"r{i}",
            "labels": [label_row(f"r{i}", "sj", reason="UNK", grade="UNKNOWN", note=note)],
            "final": None,
        }
        for i, note in enumerate(notes)
    ]


def test_filter_miss_only_unknown_is_not_counted_as_missing_tag():
    """filter-miss만 단 UNKNOWN은 §6.3.2 예외로 유효하다. 태그 누락 칸에 넣지 않는다 (#134)."""
    causes = labels.unknown_causes(unknown_rows("filter-miss", "filter-miss 재등장: abc1234", ""))

    assert causes["filter-miss"] == 2
    assert causes["(태그 없음)"] == 1
    assert all(causes[tag] == 0 for tag in labels.UNKNOWN_CAUSE_TAGS)


def test_filter_miss_with_cause_tag_counts_as_cause_only():
    """원인 태그와 함께 달린 filter-miss는 원인 태그로만 센다 — §6.3.2 "예외 조항에서만
    원인 태그의 자리를 대신한다". UNKNOWN 1건은 원인 / filter-miss / 태그 없음 중 한 곳에만."""
    causes = labels.unknown_causes(unknown_rows("filter-miss no-context"))

    assert causes["no-context"] == 1
    assert causes["filter-miss"] == 0
    assert causes["(태그 없음)"] == 0


def test_filter_miss_uses_the_same_word_match_as_label_cli():
    """저장 검증(#88)과 판정이 같아야 한다. 낱말이 아닌 `filter-misses` 는 태그가 아니다."""
    causes = labels.unknown_causes(unknown_rows("filter-misses", "filter-miss로 보임"))

    assert causes["filter-miss"] == 1
    assert causes["(태그 없음)"] == 1


# --------------------------------------------------------------------------------------
# 리포트 / CLI
# --------------------------------------------------------------------------------------


def test_report_explains_why_the_recovery_denominator_is_larger():
    """이유는 갈렸지만 등급은 같은 건이 회수율 분모에 들어간다. 숫자가 안 맞아 보이면 안 된다."""
    merged = make_merged(["EXPLICIT", "EXPLICIT"])
    merged.append(
        {
            "record_id": "reason-gap",
            "labels": [
                label_row("reason-gap", "sj", reason="BUG", grade="EXPLICIT"),
                label_row("reason-gap", "jh", reason="DESIGN", grade="EXPLICIT"),
            ],
            "final": None,
        }
    )
    text = "\n".join(labels.format_report(merged))

    assert "등급이 정해진 3건" in text
    assert "라벨 확정(2건)보다 큰 이유" in text


def test_report_contains_gate1_and_kappa_sections():
    merged = make_merged(["EXPLICIT"] * 7 + ["UNKNOWN"] * 3)
    text = "\n".join(labels.format_report(merged, {f"r{i}": "a/b" for i in range(10)}))

    assert "이유 회수율" in text
    assert "게이트 1" in text
    assert "통과" in text
    assert "일치도 — reason_label" in text
    assert "UNKNOWN 원인" in text


def test_report_prints_filter_miss_apart_from_cause_distribution():
    """원인 분포(4종 + 태그 없음)와 filter-miss를 분리해 찍는다 (#134)."""
    lines = labels.format_report(unknown_rows("filter-miss", "no-context", ""))
    section = lines[lines.index("### UNKNOWN 원인 (가이드 §6.3)") :]
    footer = next(i for i, line in enumerate(section) if "(§11)" in line)

    distribution = section[1:footer]
    assert "- no-context: 1건" in distribution
    assert "- (태그 없음): 1건" in distribution
    assert not any("filter-miss" in line for line in distribution)

    after = "\n".join(section[footer + 1 :])
    assert "filter-miss (원인 태그 없이): 1건" in after
    assert "원인 분포에서 제외(§6.3.2)" in after


@pytest.fixture
def labels_dir(tmp_path):
    directory = tmp_path / "labels"
    directory.mkdir()
    plan = {
        "sj": [("r1", "BUG", "EXPLICIT"), ("r2", "LIB", "INFERRED")],
        "jh": [("r1", "BUG", "EXPLICIT"), ("r3", "UNK", "UNKNOWN")],
        "hs": [("r2", "DESIGN", "INFERRED"), ("r3", "UNK", "UNKNOWN")],
    }
    for labeler, rows in plan.items():
        path = directory / labels.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        path.write_text(
            "\n".join(
                json.dumps(label_row(rid, labeler, reason=reason, grade=grade), ensure_ascii=False)
                for rid, reason, grade in rows
            ),
            encoding="utf-8",
        )
    records = [
        {"record_id": "r1", "repo": "a/b"},
        {"record_id": "r2", "repo": "a/b"},
        {"record_id": "r3", "repo": "c/d"},
    ]
    (directory / labels.RECORDS_FILENAME).write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8"
    )
    return directory


def test_cli_writes_merged_file_and_prints_report(tmp_path, labels_dir, capsys):
    out = tmp_path / "merged.jsonl"
    exit_code = labels.main(
        ["--batch", "pre200", "--labels-dir", str(labels_dir), "--out", str(out)]
    )

    assert exit_code == 0
    merged = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    assert {row["record_id"] for row in merged} == {"r1", "r2", "r3"}

    by_id = {row["record_id"]: row for row in merged}
    assert by_id["r1"]["final"]["method"] == "AGREED"  # sj·jh 일치
    assert by_id["r2"]["final"] is None  # LIB vs DESIGN
    assert by_id["r3"]["final"]["method"] == "AGREED"  # 둘 다 UNK

    assert "게이트 1" in capsys.readouterr().out


def test_cli_no_write_leaves_no_file(tmp_path, labels_dir):
    out = tmp_path / "merged.jsonl"
    assert (
        labels.main(
            ["--batch", "pre200", "--labels-dir", str(labels_dir), "--out", str(out), "--no-write"]
        )
        == 0
    )
    assert not out.exists()


def test_cli_rerun_preserves_discussed_final(tmp_path, labels_dir):
    out = tmp_path / "merged.jsonl"
    labels.main(["--batch", "pre200", "--labels-dir", str(labels_dir), "--out", str(out)])

    rows = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    for row in rows:
        if row["record_id"] == "r2":
            row["final"] = {
                "reason_label": "LIB",
                "evidence_grade": "INFERRED",
                "method": "DISCUSSED",
                "note": "토론 결과",
            }
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    labels.main(["--batch", "pre200", "--labels-dir", str(labels_dir), "--out", str(out)])
    after = {
        json.loads(line)["record_id"]: json.loads(line)
        for line in out.read_text("utf-8").splitlines()
    }

    assert after["r2"]["final"]["method"] == "DISCUSSED"
    assert after["r2"]["final"]["note"] == "토론 결과"


# --------------------------------------------------------------------------------------
# 라벨 값 검증 (§4.2 ③ 8종 / 근거 3등급)
# --------------------------------------------------------------------------------------


def test_undefined_label_inflates_kappa():
    """왜 막아야 하는지 — p_e 가 선언된 클래스만 세므로 정의되지 않은 값이 kappa 를 부풀린다."""
    typo = [("BUGG", "BUGG")] * 4 + [("BUG", "PERF")] * 3 + [("PERF", "BUG")] * 3
    valid = [("DEAD", "DEAD")] * 4 + [("BUG", "PERF")] * 3 + [("PERF", "BUG")] * 3

    inflated, _, _ = labels.cohens_kappa(typo, labels.REASON_LABELS)
    correct, _, _ = labels.cohens_kappa(valid, labels.REASON_LABELS)

    assert inflated > correct + 0.15  # 같은 일치 패턴인데 kappa 가 훨씬 높다


@pytest.mark.parametrize("bad", ["BUGG", "bug", "BUG ", "", None])
def test_bad_reason_label_is_reported(bad):
    personal = {"sj": [label_row("r1", "sj", reason=bad)], "jh": [], "hs": []}
    problems = labels.find_label_problems(personal)

    # 빈 값·None 은 아직 라벨 안 한 줄이라 여기서는 걸리지 않는다 (is_filled 가 거른다)
    if bad in ("", None):
        assert problems == []
    else:
        assert len(problems) == 1
        assert "reason_label" in problems[0]


def test_bad_evidence_grade_is_reported():
    personal = {"sj": [label_row("r1", "sj", grade="EXPLICT")], "jh": [], "hs": []}
    problems = labels.find_label_problems(personal)

    assert len(problems) == 1
    assert "evidence_grade" in problems[0]
    assert "EXPLICT" in problems[0]


def test_label_problem_message_points_at_the_line():
    personal = {
        "sj": [label_row("r1", "sj"), label_row("r2", "sj", reason="TYPO")],
        "jh": [],
        "hs": [],
    }
    problems = labels.find_label_problems(personal)

    assert "sj 2번째 줄" in problems[0]


def test_valid_labels_have_no_problems():
    personal = {
        "sj": [label_row("r1", "sj", reason=reason) for reason in labels.REASON_LABELS],
        "jh": [label_row("r2", "jh", grade=grade) for grade in labels.EVIDENCE_GRADES],
        "hs": [empty_row("r3", "hs")],
    }
    assert labels.find_label_problems(personal) == []


def test_cli_stops_on_undefined_label_and_writes_nothing(tmp_path, labels_dir):
    """틀린 kappa 를 내느니 멈춘다 — 게이트 1 판정에 쓰는 값이다."""
    path = labels_dir / labels.LABEL_FILENAME_TEMPLATE.format(labeler="sj")
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    rows[0]["reason_label"] = "BUGG"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )

    out = tmp_path / "merged.jsonl"
    assert (
        labels.main(["--batch", "pre200", "--labels-dir", str(labels_dir), "--out", str(out)]) == 2
    )
    assert not out.exists()


def test_cli_stops_when_nothing_is_labelled(tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    for labeler in labels.LABELERS:
        path = directory / labels.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        path.write_text(json.dumps(empty_row("r1", labeler)), encoding="utf-8")

    assert labels.main(["--batch", "pre200", "--labels-dir", str(directory), "--no-write"]) == 1


# --------------------------------------------------------------------------------------
# 본 라벨링 500건 - 배분 파일·split·중간 점검 (#158)
# --------------------------------------------------------------------------------------


def assignment_rows(block, labelers, count, test_ids=()):
    """`main500_assignment.jsonl` 한 블록. 앞 50건이 `interim` 이다 (#85)."""
    return [
        {
            "record_id": f"{block}{position:03d}",
            "batch": "main500",
            "repo": "a/b",
            "block": block,
            "labelers": list(labelers),
            "block_position": position,
            "interim": position <= 50,
            "split": "test" if f"{block}{position:03d}" in test_ids else "train",
        }
        for position in range(1, count + 1)
    ]


def write_main500(directory, assignment, labels_by_labeler):
    """배분 파일과 개인 라벨 파일(`{labeler}_main500.jsonl`)을 쓴다."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "main500_assignment.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in assignment), encoding="utf-8"
    )
    for labeler, rows in labels_by_labeler.items():
        (directory / f"{labeler}_main500.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
    return directory


def block_labels(assignment, reason_of):
    """블록의 두 라벨러가 각자 라벨한 줄. `reason_of(record_id, labeler)` 가 이유를 정한다.

    등급도 이유에 따라 둘로 갈라 둔다 - 한 등급만 쓰면 등급 kappa 가 정의되지 않는다.
    """

    def row(record_id, labeler):
        """한 사람의 라벨 한 줄. DEAD 는 INFERRED(0.7), 나머지는 EXPLICIT."""
        reason = reason_of(record_id, labeler)
        if reason == "DEAD":
            return label_row(record_id, labeler, reason, "INFERRED", confidence=0.7)
        return label_row(record_id, labeler, reason)

    return {
        labeler: [row(item["record_id"], labeler) for item in assignment]
        for labeler in assignment[0]["labelers"]
    }


def test_personal_labels_are_read_from_the_batch_files(tmp_path):
    """전에는 `batch` 를 받고도 `*_pre200.jsonl` 을 읽었다 (#158)."""
    (tmp_path / "hs_pre200.jsonl").write_text(json.dumps(label_row("old", "hs")), "utf-8")
    (tmp_path / "hs_main500.jsonl").write_text(json.dumps(label_row("new", "hs")), "utf-8")

    assert [row["record_id"] for row in labels.load_personal_labels(tmp_path, "main500")["hs"]] == [
        "new"
    ]
    assert [row["record_id"] for row in labels.load_personal_labels(tmp_path, "pre200")["hs"]] == [
        "old"
    ]


def test_merge_takes_split_from_the_assignment():
    """#86 이 train/val/test 를 나눌 수 있게. 예비 200건은 그대로 null (가이드 §7.3)."""
    personal = {"jh": [label_row("r1", "jh")], "hs": [label_row("r1", "hs")]}

    with_split = labels.merge_labels(
        personal, batch="main500", assignment={"r1": {"split": "test"}}
    )

    assert with_split[0]["split"] == "test"
    assert labels.merge_labels(personal)[0]["split"] is None


def test_merge_refuses_labels_outside_the_assignment():
    """배분 파일에 없는 레코드는 어느 split 에도 속하지 않는다 - null 로 두면 조용히 빠진다."""
    personal = {"hs": [label_row("stray", "hs")]}

    with pytest.raises(ValueError, match="배분 파일에 없는"):
        labels.merge_labels(personal, batch="main500", assignment={"r1": {"split": "train"}})


def _alternating(record_id, labeler):
    """두 사람이 같은 이유를 붙이되 클래스가 둘이라 kappa 가 정의된다."""
    return "BUG" if int(record_id[1:]) % 2 else "DEAD"


def test_interim_counts_only_the_first_50_of_each_block():
    """51번째부터는 두 사람이 다 라벨했어도 점검 표본이 아니다 (가이드 §8.4.1)."""
    assignment = assignment_rows("B", ("jh", "hs"), 52)
    personal = block_labels(
        assignment,
        lambda rid, labeler: (
            "SEC" if rid in {"B051", "B052"} and labeler == "hs" else _alternating(rid, labeler)
        ),
    )
    merged = labels.merge_labels(
        personal, batch="main500", assignment={r["record_id"]: r for r in assignment}
    )

    text = "\n".join(labels.format_interim_report(merged, {r["record_id"]: r for r in assignment}))

    assert "블록 B (jh + hs) - 통과" in text
    assert "2인 완료 50/50건, kappa 분모 50건" in text
    assert "reason_label kappa 1.000" in text


def test_interim_waits_until_both_labelers_finish_the_first_50():
    """한 사람이 30건에서 멈췄으면 아직 판정하지 않는다."""
    assignment = assignment_rows("C", ("hs", "sj"), 50)
    personal = block_labels(assignment, _alternating)
    personal["sj"] = personal["sj"][:30]
    by_id = {r["record_id"]: r for r in assignment}

    text = "\n".join(
        labels.format_interim_report(labels.merge_labels(personal, assignment=by_id), by_id)
    )

    assert "진행 중" in text and "2인 완료 30/50건" in text


def test_interim_fails_a_block_below_the_threshold():
    """reason 이 절반쯤 갈리면 kappa 가 0.6 밑이다 - 가이드 보강 후 그 50건 재라벨."""
    assignment = assignment_rows("A", ("sj", "jh"), 50)
    personal = block_labels(
        assignment,
        lambda rid, labeler: (
            "DESIGN" if labeler == "jh" and int(rid[1:]) % 3 else _alternating(rid, labeler)
        ),
    )
    by_id = {r["record_id"]: r for r in assignment}

    text = "\n".join(
        labels.format_interim_report(labels.merge_labels(personal, assignment=by_id), by_id)
    )

    assert "블록 A (sj + jh) - 미달" in text
    assert "혼동 쌍 상위" in text


def test_cli_interim_prints_the_check_and_writes_nothing(tmp_path, capsys):
    """기본 묶음이 main500 이다. `--interim` 은 병합 파일을 쓰지 않는다."""
    assignment = assignment_rows("B", ("jh", "hs"), 50)
    directory = write_main500(
        tmp_path / "labels", assignment, block_labels(assignment, _alternating)
    )
    out = tmp_path / "merged.jsonl"

    assert labels.main(["--labels-dir", str(directory), "--out", str(out), "--interim"]) == 0
    assert "블록 B (jh + hs) - 통과" in capsys.readouterr().out
    assert not out.exists()


def test_cli_merge_writes_split_for_main500(tmp_path):
    """병합 파일의 `split` 이 배분 파일과 같다."""
    assignment = assignment_rows("B", ("jh", "hs"), 4, test_ids={"B002"})
    directory = write_main500(
        tmp_path / "labels", assignment, block_labels(assignment, _alternating)
    )
    out = tmp_path / "merged.jsonl"

    assert labels.main(["--labels-dir", str(directory), "--out", str(out)]) == 0
    merged = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    assert {row["record_id"]: row["split"] for row in merged} == {
        "B001": "train",
        "B002": "test",
        "B003": "train",
        "B004": "train",
    }
    assert all(row["batch"] == "main500" for row in merged)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--interim"], "배분 파일이 없다"),
        (["--batch", "pre200", "--interim"], "중간 점검을 할 수 없다"),
    ],
)
def test_cli_refuses_checks_it_cannot_do(tmp_path, capsys, argv, message):
    """배분 파일이 없으면 split·중간 점검을 지어내지 않는다."""
    directory = tmp_path / "labels"
    directory.mkdir()
    for labeler in labels.LABELERS:
        for batch in ("main500", "pre200"):
            (directory / f"{labeler}_{batch}.jsonl").write_text(
                json.dumps(label_row("r1", labeler)), "utf-8"
            )

    assert labels.main(["--labels-dir", str(directory), "--no-write", *argv]) == 2
    assert message in capsys.readouterr().err


def test_main500_merge_without_assignment_is_refused():
    """CLI 를 거치지 않고 함수를 바로 불러도 split 을 null 로 지어내지 않는다 (#158 코드래빗)."""
    personal = {"jh": [label_row("B001", "jh")], "hs": [label_row("B001", "hs")]}

    with pytest.raises(ValueError, match="배분 정보가 필요"):
        labels.merge_labels(personal, batch="main500")


def test_labels_from_someone_not_assigned_to_the_block_are_refused():
    """블록 B 는 jh·hs 몫이다. sj 라벨이 섞이면 엉뚱한 쌍으로 확정되고 kappa 가 틀린다."""
    assignment = {r["record_id"]: r for r in assignment_rows("B", ("jh", "hs"), 2)}
    personal = {"hs": [label_row("B001", "hs")], "sj": [label_row("B001", "sj")]}

    with pytest.raises(ValueError, match="맡지 않은 사람"):
        labels.merge_labels(personal, batch="main500", assignment=assignment)
    # 파일 주인은 맞아도 줄의 labeler 가 다르면 같은 문제다
    mislabeled = {"hs": [label_row("B001", "sj")]}
    with pytest.raises(ValueError, match="맡지 않은 사람"):
        labels.merge_labels(mislabeled, batch="main500", assignment=assignment)


def test_cli_will_not_overwrite_another_batchs_merged_file(tmp_path, capsys):
    """기본 출력 경로는 묶음과 상관없이 같다. 거기 예비 200건 토론 확정이 있으면 지키고 멈춘다."""
    assignment = assignment_rows("B", ("jh", "hs"), 4)
    directory = write_main500(
        tmp_path / "labels", assignment, block_labels(assignment, _alternating)
    )
    out = tmp_path / "merged.jsonl"
    old = json.dumps(
        {"record_id": "p1", "batch": "pre200", "labels": [], "final": {"method": "DISCUSSED"}}
    )
    out.write_text(old + "\n", encoding="utf-8")

    assert labels.main(["--labels-dir", str(directory), "--out", str(out)]) == 2
    assert "다른 묶음(pre200)" in capsys.readouterr().err
    assert out.read_text("utf-8") == old + "\n"
    # 중간 점검은 병합 파일을 읽지도 쓰지도 않으니 막지 않는다
    assert labels.main(["--labels-dir", str(directory), "--out", str(out), "--interim"]) == 0
