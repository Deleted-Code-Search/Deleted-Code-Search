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
    exit_code = labels.main(["--labels-dir", str(labels_dir), "--out", str(out)])

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
    assert labels.main(["--labels-dir", str(labels_dir), "--out", str(out), "--no-write"]) == 0
    assert not out.exists()


def test_cli_rerun_preserves_discussed_final(tmp_path, labels_dir):
    out = tmp_path / "merged.jsonl"
    labels.main(["--labels-dir", str(labels_dir), "--out", str(out)])

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

    labels.main(["--labels-dir", str(labels_dir), "--out", str(out)])
    after = {
        json.loads(line)["record_id"]: json.loads(line)
        for line in out.read_text("utf-8").splitlines()
    }

    assert after["r2"]["final"]["method"] == "DISCUSSED"
    assert after["r2"]["final"]["note"] == "토론 결과"


def test_cli_stops_when_nothing_is_labelled(tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    for labeler in labels.LABELERS:
        path = directory / labels.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        path.write_text(json.dumps(empty_row("r1", labeler)), encoding="utf-8")

    assert labels.main(["--labels-dir", str(directory), "--no-write"]) == 1
