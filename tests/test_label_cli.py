"""라벨링 CLI 테스트 (이슈 #37). 사람 입력은 스크립트로 흉내 낸다."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from classify import labels, sampling
from eval import gate1
from tools import label_cli

LEAK_TEXT = "CLASSIFIER-LEAK-SENTINEL"
LEAK_VERSION = "clf-v9-sentinel"


def make_record(index, **overrides):
    """§4.4 DeletionRecord 모양. `reason.*` 에 분류기 출력이 들어 있다 — 라벨러에게 새면 안 된다."""
    record = {
        "id": f"rec-{index:03d}",
        "repo": "psf/requests",
        "repo_license": "Apache-2.0",
        "commit_sha": f"sha{index}",
        "file_path": f"src/mod{index}.py",
        "function_name": f"fn_{index}",
        "function_signature": f"def fn_{index}(headers):",
        "deleted_body": f"def fn_{index}(headers):\n    return headers[0]\n",
        "deletion_kind": "FULL_FUNCTION",
        "is_test_code": False,
        "filter_status": "KEPT",
        "context": {
            "commit_message": f"fix: fn_{index} raised IndexError on empty header list (#812)",
            "pr_number": 812,
            "pr_title": "Fix empty headers",
            "pr_body": "The parser crashed.",
            "issue_numbers": [812],
            "issue_titles": ["IndexError on empty headers"],
            "review_comments": [],
        },
        "replacement": {
            "code": "if not headers:\n    return None",
            "match_method": "SAME_LOCATION",
            "confidence": 0.8,
        },
        "reason": {
            "label": "SEC",
            "evidence_grade": "INFERRED",
            "evidence_text": LEAK_TEXT,
            "confidence": 0.42,
            "classifier_version": LEAK_VERSION,
        },
        "source_url": f"https://github.com/psf/requests/commit/sha{index}",
    }
    record.update(overrides)
    return record


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def workspace(tmp_path):
    """#33 산출물과 같은 모양: 레코드 파일 1개 + sj 빈 틀 3줄."""
    records = [make_record(index) for index in range(3)]
    records_path = tmp_path / sampling.RECORDS_FILENAME
    write_jsonl(records_path, [sampling.build_labeling_record(record) for record in records])
    label_path = tmp_path / sampling.LABEL_FILENAME_TEMPLATE.format(labeler="sj")
    write_jsonl(label_path, [sampling.empty_label_row(record["id"], "sj") for record in records])
    return records_path, label_path


class Script:
    """사람 입력. 다 떨어지면 EOF — 터미널을 닫은 것과 같다.

    묻는 문장도 `transcript` 에 남긴다. 라벨러가 보는 화면은 출력과 질문을 합친 것이다.
    """

    def __init__(self, answers, transcript=None):
        self.answers = list(answers)
        self.transcript = [] if transcript is None else transcript

    def __call__(self, prompt):
        self.transcript.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def run_session(workspace, answers, clock=lambda: 0.0, now=None):
    records_path, label_path = workspace
    records, problems = label_cli.load_records(records_path)
    assert problems == []
    transcript = []
    session = label_cli.LabelSession(
        "sj",
        records,
        label_cli.LabelFile.load(label_path),
        ask=Script(answers, transcript),
        say=transcript.append,
        clock=clock,
        **({"now": now} if now else {}),
    )
    session.run()
    return "\n".join(transcript), read_rows(label_path)


def explicit_bug(index):
    """레코드 index 를 BUG / EXPLICIT 으로 (커밋 메시지 원문 복사)."""
    return [
        "1",  # 이유 BUG
        "e",  # EXPLICIT
        f"fn_{index} raised IndexError on empty header list",
        "commit",
        "",  # locator → commit:message
        "",  # note
        "y",  # 저장
    ]


# --------------------------------------------------------------------------------------
# 스키마
# --------------------------------------------------------------------------------------


def test_cli_schema_is_the_guide_schema_and_the_template_schema():
    """가이드 §7.2 = #33 빈 틀 = CLI 출력. 셋 중 하나만 바뀌면 여기서 걸린다."""
    assert list(label_cli.LABEL_FIELDS) == list(sampling.empty_label_row("x", "sj"))


def test_explicit_label_is_written_in_place(workspace):
    _, rows = run_session(workspace, explicit_bug(0))

    assert len(rows) == 3
    row = rows[0]
    assert list(row) == list(label_cli.LABEL_FIELDS)
    assert row["record_id"] == "rec-000"
    assert row["labeler"] == "sj"
    assert row["reason_label"] == "BUG"
    assert row["evidence_grade"] == "EXPLICIT"
    assert row["evidence_text"] == "fn_0 raised IndexError on empty header list"
    assert row["evidence_source"] == "commit"
    assert row["evidence_locator"] == "commit:message"
    assert row["confidence"] == 1.0
    assert row["note"] == ""
    assert row["guide_version"] == sampling.GUIDE_VERSION
    assert "+" in row["labeled_at"] or row["labeled_at"].endswith("Z")  # 타임존 포함
    assert not labels.is_filled(rows[1])


def test_output_passes_gate1_validation(workspace):
    answers = explicit_bug(0) + ["4", "i", "0.9", "json.loads 호출이 대체", "", "", "", "y"]
    run_session(workspace, answers)
    _, label_path = workspace

    inputs = gate1.load_inputs([label_path])

    assert inputs.problems == []
    assert labels.find_label_problems({"sj": read_rows(label_path)}) == []


# --------------------------------------------------------------------------------------
# 보여주는 것 / 안 보여주는 것 (가이드 §2)
# --------------------------------------------------------------------------------------


def test_reason_fields_are_never_shown(workspace):
    output, _ = run_session(workspace, [])

    assert "fn_0" in output
    assert "raised IndexError" in output
    assert LEAK_TEXT not in output
    assert LEAK_VERSION not in output


def test_reason_is_dropped_even_if_the_records_file_contains_it():
    """레코드 파일이 잘못 만들어져 reason 이 섞여 들어와도 화면에는 안 나온다."""
    leaked = {**sampling.build_labeling_record(make_record(0)), "reason": make_record(0)["reason"]}

    for row in (leaked, make_record(0)):
        view = label_cli.labeler_view(row)
        rendered = label_cli.render_record(view)
        assert "reason" not in view
        assert LEAK_TEXT not in rendered
        assert LEAK_VERSION not in rendered


def test_context_is_shown_before_code():
    """가이드 §3: 명시(맥락)를 추론(대체 코드)보다 먼저 읽는다."""
    rendered = label_cli.render_record(label_cli.labeler_view(make_record(0)))

    assert rendered.index("commit_message") < rendered.index("deleted_body")
    assert rendered.index("deleted_body") < rendered.index("match_method=SAME_LOCATION")


def test_replacement_confidence_is_on_screen():
    """파일에 실려도 화면이 안 찍으면 라벨러는 못 본다 - 가이드 §6.2.2 근거 ① 구간 상한 (#117)."""
    rendered = label_cli.render_record(label_cli.labeler_view(make_record(0)))

    assert "match_method=SAME_LOCATION, confidence=0.8)" in rendered


# --------------------------------------------------------------------------------------
# 잘못된 입력 거부
# --------------------------------------------------------------------------------------


def test_invalid_reason_is_rejected_until_valid(workspace):
    answers = ["BUGG", "9", "0", "other"] + ["bug"] + explicit_bug(0)[1:]
    output, rows = run_session(workspace, answers)

    assert output.count("이유 8종이 아니다") == 4
    assert rows[0]["reason_label"] == "BUG"


def test_invalid_grade_is_rejected(workspace):
    answers = ["1", "x", "u", "e"] + explicit_bug(0)[2:]
    output, rows = run_session(workspace, answers)

    assert "e(EXPLICIT) 또는 i(INFERRED)" in output
    assert "UNKNOWN 등급은 이유 UNK 와만" in output
    assert rows[0]["evidence_grade"] == "EXPLICIT"


def test_explicit_requires_evidence_text(workspace):
    answers = ["1", "e", ""] + explicit_bug(0)[2:]
    output, rows = run_session(workspace, answers)

    assert "evidence_text 가 필수" in output
    assert "원문을 그대로 복사" in output
    assert rows[0]["evidence_text"] == "fn_0 raised IndexError on empty header list"


def test_explicit_rejects_diff_source(workspace):
    answers = explicit_bug(0)[:3] + ["diff"] + explicit_bug(0)[3:]
    output, rows = run_session(workspace, answers)

    assert "diff 는 문장이 아니다" in output
    assert rows[0]["evidence_source"] == "commit"


def test_explicit_text_not_found_in_context_asks_before_keeping(workspace):
    """요약한 문장 → 거절(n) → 원문 복사."""
    answers = ["1", "e", "header 버그 요약", "n"] + explicit_bug(0)[2:]
    output, rows = run_session(workspace, answers)

    assert "찾지 못했다" in output
    assert rows[0]["evidence_text"] == "fn_0 raised IndexError on empty header list"


def test_found_in_context_allows_whitespace_and_ellipsis():
    view = label_cli.labeler_view(make_record(0))

    assert label_cli.found_in_context("fn_0   raised IndexError", view)
    assert label_cli.found_in_context("fix: fn_0 raised … header list (#812)", view)
    assert label_cli.found_in_context("The parser crashed.", view)
    assert not label_cli.found_in_context("the parser crashed", view)  # 복사는 대소문자까지


def test_inferred_requires_confidence_in_range(workspace):
    answers = ["4", "i", "abc", "1.5", "", "0.9", "json.loads 호출이 대체", "", "", "", "y"]
    output, rows = run_session(workspace, answers)

    assert "0.8 이상" in output  # §6.2 구간 표시
    assert output.count("숫자가 아니다") == 2
    assert "0~1 사이" in output
    row = rows[0]
    assert row["reason_label"] == "LIB"
    assert row["evidence_grade"] == "INFERRED"
    assert row["confidence"] == 0.9
    assert (row["evidence_source"], row["evidence_locator"]) == ("diff", "diff:replacement")


def test_inferred_requires_evidence_text(workspace):
    answers = ["4", "i", "0.7", "", "호출자가 사라짐", "", "", "", "y"]
    output, rows = run_session(workspace, answers)

    assert "INFERRED 면 evidence_text 가 필수" in output
    assert rows[0]["evidence_text"] == "호출자가 사라짐"


def test_inferred_below_0_5_offers_unknown(workspace):
    answers = ["1", "i", "0.3", "y", "", "no-replacement vague-message", "y"]
    output, rows = run_session(workspace, answers)

    assert "UNK + UNKNOWN 으로 바꿀까" in output
    assert "note 가 필수" in output
    row = rows[0]
    assert (row["reason_label"], row["evidence_grade"]) == ("UNK", "UNKNOWN")
    assert (row["evidence_text"], row["evidence_source"], row["evidence_locator"]) == (
        None,
        None,
        None,
    )
    assert row["confidence"] == 0.0
    assert row["note"] == "no-replacement vague-message"


def test_inferred_below_0_5_declined_asks_again_and_0_5_is_accepted(workspace):
    answers = ["1", "i", "0.3", "n", "0.5", "테스트 추가", "", "", "", "y"]
    output, rows = run_session(workspace, answers)

    assert "0.5 이상이어야" in output
    assert (rows[0]["evidence_grade"], rows[0]["confidence"]) == ("INFERRED", 0.5)


def test_unk_reason_forces_unknown_grade(workspace):
    output, rows = run_session(workspace, ["8", "no-context", "y"])

    assert "UNK 는 항상 UNKNOWN" in output
    assert (rows[0]["reason_label"], rows[0]["evidence_grade"], rows[0]["confidence"]) == (
        "UNK",
        "UNKNOWN",
        0.0,
    )


# --------------------------------------------------------------------------------------
# 중단·재개·되돌리기
# --------------------------------------------------------------------------------------


def test_resume_skips_labeled_records_without_duplicates(workspace):
    _, first = run_session(workspace, explicit_bug(0))
    output, rows = run_session(workspace, explicit_bug(1))

    assert "record_id  rec-000" not in output
    assert "record_id  rec-001" in output
    assert len(rows) == 3
    assert len({row["record_id"] for row in rows}) == 3
    assert [labels.is_filled(row) for row in rows] == [True, True, False]
    assert rows[0] == first[0]  # 앞 실행의 라벨은 그대로


def test_quit_in_the_middle_saves_nothing(workspace):
    output, rows = run_session(workspace, ["1", "e", ":q"])

    assert "저장하지 않았다" in output
    assert not any(labels.is_filled(row) for row in rows)


def test_restart_discards_current_input(workspace):
    answers = ["2", "e", ":r"] + explicit_bug(0)
    _, rows = run_session(workspace, answers)

    assert rows[0]["reason_label"] == "BUG"


def test_answering_no_at_confirmation_restarts_the_record(workspace):
    answers = explicit_bug(0)[:-1] + ["n"] + ["8", "no-context", "y"]
    _, rows = run_session(workspace, answers)

    assert rows[0]["reason_label"] == "UNK"
    assert not labels.is_filled(rows[1])


def test_undo_edits_the_previous_record_in_place(workspace):
    relabel_perf = ["2", "i", "0.8", "캐시 제거 후 결과가 같다", "", "", "", "y"]
    answers = explicit_bug(0) + [":u"] + relabel_perf
    output, rows = run_session(workspace, answers)

    assert "[직전 건 수정]" in output
    assert len(rows) == 3
    assert (rows[0]["reason_label"], rows[0]["evidence_grade"], rows[0]["confidence"]) == (
        "PERF",
        "INFERRED",
        0.8,
    )
    assert not labels.is_filled(rows[1])


def test_undo_in_a_new_session_reopens_the_latest_labeled_record(workspace):
    run_session(workspace, explicit_bug(0))
    _, rows = run_session(workspace, [":u", "8", "no-context", "y"])

    assert rows[0]["reason_label"] == "UNK"
    assert not labels.is_filled(rows[1])


def test_undo_works_on_the_last_record(workspace):
    """마지막 건을 저장하자마자 세션이 끝나면 그 건을 고칠 수 없었다 (E2E 실행에서 발견)."""
    answers = explicit_bug(0) + explicit_bug(1) + explicit_bug(2) + [":u", "8", "no-context", "y"]
    output, rows = run_session(workspace, answers)

    assert "모두 라벨했다" in output
    assert [row["reason_label"] for row in rows] == ["BUG", "BUG", "UNK"]


def test_finished_file_can_still_reopen_the_last_record(workspace):
    run_session(workspace, explicit_bug(0) + explicit_bug(1) + explicit_bug(2) + [""])
    _, rows = run_session(workspace, [":u", "8", "no-context", "y"])

    assert [row["reason_label"] for row in rows] == ["BUG", "BUG", "UNK"]


def test_undo_with_nothing_to_undo_stays_on_current(workspace):
    output, rows = run_session(workspace, [":u"] + explicit_bug(0))

    assert "수정할 직전 건이 없다" in output
    assert rows[0]["reason_label"] == "BUG"


SAME_SECOND = datetime(2026, 9, 22, 14, 3, 11, tzinfo=timezone(timedelta(hours=9)))
RELABEL_PERF = ["2", "i", "0.8", "캐시 제거 후 결과가 같다", "", "", "", "y"]


def test_undo_breaks_same_second_tie_by_persisted_save_order(workspace):
    """PR #38 리뷰: 같은 초에 저장한 건끼리 labeled_at 이 같으면 :u 가 줄 번호가 큰 쪽을 골랐다.

    1회차: rec-000, rec-001 저장 → rec-001 수정(:u) 중 한 번 더 :u → rec-000 을 다시 저장.
           세 번 모두 같은 초. 마지막 저장은 rec-000 이다.
    2회차: 새 실행이라 history 가 비어 있다 → 파일에서 고른다. rec-000 이 열려야 한다.
           (예전 코드는 동률을 줄 번호로 가려 rec-001 을 열었다)
    """
    first = explicit_bug(0) + explicit_bug(1) + [":u", ":u"] + RELABEL_PERF
    _, after_first = run_session(workspace, first, now=lambda: SAME_SECOND)
    assert after_first[0]["labeled_at"] == after_first[1]["labeled_at"]  # 동률 재현
    assert after_first[0]["reason_label"] == "PERF"

    _, rows = run_session(workspace, [":u", "8", "no-context", "y"], now=lambda: SAME_SECOND)

    assert rows[0]["reason_label"] == "UNK"  # 마지막으로 저장한 rec-000 을 고쳤다
    assert rows[1]["reason_label"] == "BUG"
    assert not labels.is_filled(rows[2])


def test_save_order_is_persisted_next_to_the_label_file_not_inside_it(workspace):
    """순번을 라벨 줄에 넣으면 가이드 §7.2 스키마가 바뀐다. 옆 파일에 둔다."""
    _, label_path = workspace
    _, rows = run_session(workspace, explicit_bug(0) + explicit_bug(1), now=lambda: SAME_SECOND)

    order_path = label_path.with_name(label_path.name + label_cli.ORDER_SUFFIX)
    order = json.loads(order_path.read_text("utf-8"))
    assert order == {"rec-000": 1, "rec-001": 2}
    assert all(list(row) == list(label_cli.LABEL_FIELDS) for row in rows)


def test_missing_order_file_falls_back_to_line_order(workspace):
    """순번 파일이 없어도(도입 전 파일) 멈추지 않는다. 동률이면 줄 순서로 가린다."""
    _, label_path = workspace
    run_session(workspace, explicit_bug(0) + explicit_bug(1), now=lambda: SAME_SECOND)
    label_path.with_name(label_path.name + label_cli.ORDER_SUFFIX).unlink()

    _, rows = run_session(workspace, [":u", "8", "no-context", "y"], now=lambda: SAME_SECOND)

    assert [row["reason_label"] for row in rows[:2]] == ["BUG", "UNK"]


def test_progress_line_counts_and_averages(workspace):
    ticks = iter(range(0, 10_000, 30))
    output, _ = run_session(workspace, explicit_bug(0), clock=lambda: float(next(ticks)))

    assert "[0/3] sj" in output
    assert "[1/3] sj" in output
    assert "건당 평균" in output


# --------------------------------------------------------------------------------------
# 파일 검사 / 진입점
# --------------------------------------------------------------------------------------


def test_label_file_problems_are_found():
    rows = [
        sampling.empty_label_row("rec-000", "sj"),
        sampling.empty_label_row("rec-000", "sj"),
        sampling.empty_label_row("rec-999", "sj"),
        sampling.empty_label_row("rec-001", "jh"),
        {**sampling.empty_label_row("rec-002", "sj"), "reason_label": "bug", "evidence_grade": "E"},
    ]
    problems = label_cli.find_label_file_problems(rows, "sj", {"rec-000", "rec-001", "rec-002"})

    assert any("중복" in problem for problem in problems)
    assert any("rec-999" in problem for problem in problems)
    assert any("jh" in problem for problem in problems)
    assert any("8종·3등급 밖" in problem for problem in problems)


def test_label_file_line_that_is_not_an_object_reports_the_real_line(tmp_path):
    """빈 줄을 건너뛰어도 줄 번호는 파일의 실제 줄이다 (3번째 줄)."""
    path = tmp_path / "sj_pre200.jsonl"
    row = json.dumps(sampling.empty_label_row("rec-000", "sj"))
    path.write_text(f"{row}\n\n[]\n", encoding="utf-8")

    with pytest.raises(label_cli.LabelFileError, match=r":3: 한 줄은 JSON 객체여야 한다 \(list\)"):
        label_cli.LabelFile.load(path)


def test_records_line_that_is_not_an_object_is_reported(tmp_path):
    path = tmp_path / sampling.RECORDS_FILENAME
    record = json.dumps(sampling.build_labeling_record(make_record(0)))
    path.write_text(f"null\n{record}\n", encoding="utf-8")

    records, problems = label_cli.load_records(path)

    assert list(records) == ["rec-000"]
    assert len(problems) == 1
    assert ":1: 한 줄은 JSON 객체여야 한다 (NoneType)" in problems[0]


def test_main_reports_non_object_label_line_with_location(workspace, capsys):
    records_path, label_path = workspace
    label_path.write_text('"just a string"\n', encoding="utf-8")

    code = label_cli.main(["--labeler", "sj", "--labels-dir", str(records_path.parent)])

    assert code == 2
    assert f"{label_path}:1: 한 줄은 JSON 객체여야 한다 (str)" in capsys.readouterr().err


def test_main_refuses_missing_template(tmp_path):
    write_jsonl(tmp_path / sampling.RECORDS_FILENAME, [])

    assert label_cli.main(["--labeler", "sj", "--labels-dir", str(tmp_path)]) == 2


def test_main_runs_a_session(workspace, monkeypatch):
    records_path, _ = workspace
    monkeypatch.setattr("builtins.input", Script(explicit_bug(0)))

    code = label_cli.main(["--labeler", "sj", "--labels-dir", str(records_path.parent)])

    assert code == 0
    assert read_rows(records_path.parent / "sj_pre200.jsonl")[0]["reason_label"] == "BUG"


# --------------------------------------------------------------------------------------
# UNKNOWN 원인 태그 강제 (#88, 가이드 v2 §6.3.2)
# --------------------------------------------------------------------------------------


def unknown_label(note, **overrides):
    """CLI 가 UNK 로 저장하는 라벨 모양 (가이드 §6.3)."""
    label = {
        "reason_label": "UNK",
        "evidence_grade": "UNKNOWN",
        "evidence_text": None,
        "evidence_source": None,
        "evidence_locator": None,
        "confidence": 0.0,
        "note": note,
    }
    label.update(overrides)
    return label


def v1_untagged_unknown_row(record_id):
    """예비 200건(v1) 파일에 있는 모양 그대로 — 태그 없는 UNKNOWN (게이트 1에서 43건)."""
    return {
        "record_id": record_id,
        "labeler": "sj",
        "reason_label": "UNK",
        "evidence_grade": "UNKNOWN",
        "evidence_text": None,
        "evidence_source": None,
        "evidence_locator": None,
        "confidence": 0.0,
        "note": "커밋 메시지만으로는 판단 불가",
        "labeled_at": "2026-09-20T10:00:00+09:00",
        "guide_version": "v1",
    }


def put_v1_row_first(workspace):
    """빈 틀의 첫 줄을 v1 태그 없는 UNKNOWN 으로 바꿔 둔다."""
    _, label_path = workspace
    rows = read_rows(label_path)
    rows[0] = v1_untagged_unknown_row("rec-000")
    write_jsonl(label_path, rows)
    return label_path


def test_cause_tags_are_the_four_the_guide_fixed():
    """가이드 §6.3.2 가 철자·개수를 고정한 4종. CLI 는 labels.py 의 같은 상수를 쓴다."""
    assert labels.UNKNOWN_CAUSE_TAGS == (
        "no-context",
        "vague-message",
        "no-replacement",
        "no-caller-info",
    )
    assert labels.FILTER_MISS_TAG == "filter-miss"
    assert labels.FILTER_MISS_TAG not in labels.UNKNOWN_CAUSE_TAGS
    assert label_cli.UNKNOWN_CAUSE_TAGS is labels.UNKNOWN_CAUSE_TAGS
    assert label_cli.FILTER_MISS_TAG is labels.FILTER_MISS_TAG


@pytest.mark.parametrize("note", ["", "   ", None, "커밋 메시지가 cleanup 뿐"])
def test_unknown_without_cause_tag_is_a_violation(note):
    """태그 없는 UNKNOWN 은 완성된 라벨이 아니다 (§6.3). 빈 note 와 서술만 있는 note 둘 다."""
    violations = label_cli.unknown_cause_tag_violations(unknown_label(note))

    assert len(violations) == 1
    for tag in (*labels.UNKNOWN_CAUSE_TAGS, labels.FILTER_MISS_TAG):
        assert tag in violations[0]  # 허용 값을 함께 보여준다


@pytest.mark.parametrize("tag", labels.UNKNOWN_CAUSE_TAGS)
def test_unknown_with_each_cause_tag_passes(tag):
    """4종 중 어느 하나만 있어도 통과한다."""
    assert label_cli.unknown_cause_tag_violations(unknown_label(tag)) == []


def test_unknown_with_only_filter_miss_passes():
    """filter-miss 는 원인 태그 자리를 대신하는 유일한 예외다 (§6.3.2)."""
    assert label_cli.unknown_cause_tag_violations(unknown_label("filter-miss")) == []


def test_unknown_with_several_cause_tags_and_free_text_passes():
    """§6.3.2 "복수 허용 — 해당하는 것을 다 단다". 태그 뒤 자유 서술도 된다 (§7.2)."""
    note = "no-context vague-message no-replacement no-caller-info PR 없음, 메시지는 cleanup"
    assert label_cli.unknown_cause_tag_violations(unknown_label(note)) == []
    assert label_cli.unknown_cause_tag_violations(unknown_label("filter-miss no-context")) == []


def test_unknown_with_a_misspelled_cause_tag_is_a_violation():
    """목록 밖 태그 검사는 하지 않지만(닫힌 어휘가 아니다), 오타는 "원인 태그 없음"으로 걸린다."""
    for note in ("no-contexts", "vauge-message", "no_replacement", "filtermiss", "xno-context"):
        assert label_cli.unknown_cause_tag_violations(unknown_label(note)), note


def test_cause_tag_can_sit_next_to_punctuation_or_korean():
    """쉼표로 잇거나 한국어 조사를 붙여도 태그로 본다 — 영문 경계만 본다."""
    for note in ("no-context,vague-message", "(no-replacement)", "no-caller-info로 판단 불가"):
        assert label_cli.unknown_cause_tag_violations(unknown_label(note)) == [], note


def test_other_tags_are_not_rejected_on_unknown():
    """목록 밖 태그(가이드 §10.3 `stale-v1` 등)가 함께 있어도 막지 않는다."""
    note = "no-context stale-v1 needs-discussion"
    assert label_cli.unknown_cause_tag_violations(unknown_label(note)) == []


@pytest.mark.parametrize(
    ("reason", "grade", "confidence"),
    [("BUG", "EXPLICIT", 1.0), ("DEAD", "INFERRED", 0.6)],
)
def test_non_unknown_label_without_tag_passes(reason, grade, confidence):
    """UNKNOWN 이 아닌 라벨은 이 규칙과 무관하다."""
    label = {
        "reason_label": reason,
        "evidence_grade": grade,
        "evidence_text": "근거",
        "confidence": confidence,
        "note": "",
    }
    assert label_cli.unknown_cause_tag_violations(label) == []


def test_unk_reason_is_a_target_even_if_grade_was_hand_edited():
    """가이드상 UNK ⇔ UNKNOWN(§4 UNK, §6.3). 등급만 어긋난 줄이 새로 저장되려 해도 잡는다."""
    label = unknown_label("", evidence_grade="INFERRED", confidence=0.5)
    assert label_cli.requires_unknown_cause_tag(label)
    assert label_cli.unknown_cause_tag_violations(label)


def test_cli_rejects_untagged_unknown_note_until_a_tag_is_given(workspace):
    """신규 입력 경로: 태그가 없으면 저장하지 않고 허용 태그를 보여 주며 다시 묻는다."""
    answers = ["8", "", "메시지가 부실함", "vauge-message", "vague-message 메시지가 부실함", "y"]
    output, rows = run_session(workspace, answers)

    assert output.count("UNKNOWN 이면 note 가 필수다") == 3
    assert "note 가 비었다" in output
    assert "no-context vague-message no-replacement no-caller-info" in output
    assert "filter-miss" in output
    assert rows[0]["note"] == "vague-message 메시지가 부실함"
    assert (rows[0]["reason_label"], rows[0]["evidence_grade"]) == ("UNK", "UNKNOWN")


def test_cli_accepts_filter_miss_alone(workspace):
    """filter-miss 만 단 UNKNOWN 은 CLI 에서도 저장된다."""
    _, rows = run_session(workspace, ["8", "filter-miss", "y"])

    assert rows[0]["note"] == "filter-miss"


def test_cli_low_confidence_inferred_turned_unknown_also_requires_a_tag(workspace):
    """INFERRED 신뢰도 < 0.5 → UNK 로 바뀌는 경로도 같은 검사를 거친다."""
    answers = ["1", "i", "0.3", "y", "호출자 불명", "no-caller-info", "y"]
    output, rows = run_session(workspace, answers)

    assert "원인 태그가 없다" in output
    assert rows[0]["note"] == "no-caller-info"


def test_cli_non_unknown_note_is_still_optional(workspace):
    """UNKNOWN 이 아니면 note 를 비워도 된다 (기존 동작 유지)."""
    _, rows = run_session(workspace, explicit_bug(0))

    assert rows[0]["note"] == ""


def test_undo_relabel_as_unknown_requires_a_tag(workspace):
    """:u 수정 경로도 같은 검사를 거친다."""
    answers = explicit_bug(0) + [":u", "8", "", "no-replacement", "y"]
    output, rows = run_session(workspace, answers)

    assert "[직전 건 수정]" in output
    assert "UNKNOWN 이면 note 가 필수다" in output
    assert (rows[0]["reason_label"], rows[0]["note"]) == ("UNK", "no-replacement")


def test_save_refuses_a_violating_label_and_leaves_the_file_untouched(workspace):
    """저장 직전 관문. collect_note 를 거치지 않는 경로가 생겨도 파일에 닿지 않는다."""
    records_path, label_path = workspace
    before = label_path.read_text(encoding="utf-8")
    records, _ = label_cli.load_records(records_path)
    session = label_cli.LabelSession(
        "sj", records, label_cli.LabelFile.load(label_path), ask=Script([]), say=lambda _: None
    )

    with pytest.raises(label_cli.UnknownTagViolation, match="원인 태그"):
        session.save(0, unknown_label("태그 없음"))

    assert label_path.read_text(encoding="utf-8") == before
    order_path = label_path.with_name(label_path.name + label_cli.ORDER_SUFFIX)
    assert not order_path.exists()

    # 대조: 같은 세션이 정상 라벨을 저장하면 같은 경로에 순번 파일이 생긴다.
    # 위의 "없다"가 "원래 안 쓴다"가 아니라 "거부해서 안 썼다"임을 보인다.
    session.save(0, unknown_label("no-context"))
    assert order_path.exists()
    assert label_path.read_text(encoding="utf-8") != before


def test_run_reasks_when_save_is_refused(workspace):
    """run() 이 관문에서 막히면 저장하지 않고 같은 건을 다시 입력받는다."""
    records_path, label_path = workspace
    records, _ = label_cli.load_records(records_path)
    transcript = []
    session = label_cli.LabelSession(
        "sj",
        records,
        label_cli.LabelFile.load(label_path),
        ask=Script([":q"], transcript),
        say=transcript.append,
    )
    produced = iter([unknown_label("태그 없음"), unknown_label("no-context")])
    original = session.collect_label

    def collect_label(view, started):
        try:
            return next(produced)
        except StopIteration:
            return original(view, started)

    session.collect_label = collect_label
    session.run()

    output = "\n".join(transcript)
    rows = read_rows(label_path)
    assert "저장하지 않았다. 이 건을 처음부터 다시 입력한다" in output
    assert output.count("record_id  rec-000") == 2
    assert rows[0]["note"] == "no-context"
    assert not labels.is_filled(rows[1])


def test_new_label_is_stamped_guide_version_v2(workspace):
    """새로 저장하는 줄의 guide_version 은 classify.sampling.GUIDE_VERSION(#100 에서 v2)."""
    _, rows = run_session(workspace, ["8", "no-context", "y"])

    assert sampling.GUIDE_VERSION == "v2"
    assert rows[0]["guide_version"] == "v2"


# ---- 기존 v1 파일 호환: 읽기·표시·집계 경로는 검사하지 않는다 ----


def test_v1_untagged_unknown_rows_load_and_pass_the_file_check(workspace):
    """예비 200건 형식의 태그 없는 UNKNOWN 줄은 읽기 경로에서 에러가 나지 않는다."""
    label_path = put_v1_row_first(workspace)

    label_file = label_cli.LabelFile.load(label_path)
    problems = label_cli.find_label_file_problems(
        label_file.rows, "sj", {"rec-000", "rec-001", "rec-002"}
    )

    assert problems == []
    assert label_file.labeled_count() == 1
    assert label_file.next_unlabeled() == 1
    assert label_cli.unknown_cause_tag_violations(label_file.rows[0])  # 규칙상으론 위반인 줄


def test_labeling_other_records_keeps_the_v1_row_as_is(workspace):
    """다른 건을 저장하면 파일 전체를 다시 쓰지만, v1 줄은 검사도 변경도 없이 그대로 남는다."""
    put_v1_row_first(workspace)

    _, after = run_session(workspace, explicit_bug(1))

    assert after[0] == v1_untagged_unknown_row("rec-000")
    assert after[1]["reason_label"] == "BUG"
    assert after[1]["guide_version"] == "v2"


def test_v1_row_is_shown_on_undo_and_relabel_requires_a_tag(workspace):
    """:u 로 v1 줄을 열면 저장값은 그대로 보여 주고, 다시 저장할 때만 검사한다."""
    put_v1_row_first(workspace)

    output, after = run_session(workspace, [":u", "8", "", "vague-message", "y"])

    assert "[직전 건 수정] 저장된 값: UNK / UNKNOWN" in output
    assert "UNKNOWN 이면 note 가 필수다" in output
    assert (after[0]["note"], after[0]["guide_version"]) == ("vague-message", "v2")


def test_real_pre200_label_files_still_load():
    """저장소에 있는 예비 200건(v1) 파일 그대로. 태그 없는 UNKNOWN 이 있어도 읽기가 통과한다."""
    labels_dir = Path(__file__).resolve().parent.parent / "datasets" / "labels"
    records_path = labels_dir / sampling.RECORDS_FILENAME
    if not records_path.is_file():
        pytest.skip("예비 200건 파일이 없다")
    records, record_problems = label_cli.load_records(records_path)
    assert record_problems == []

    untagged = 0
    for labeler in sampling.LABELERS:
        path = labels_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        label_file = label_cli.LabelFile.load(path)
        assert label_cli.find_label_file_problems(label_file.rows, labeler, records.keys()) == []
        untagged += sum(1 for row in label_file.rows if label_cli.unknown_cause_tag_violations(row))
    assert untagged > 0  # 규칙상 위반인 v1 줄이 실제로 있는데도 읽기는 통과한다


# --------------------------------------------------------------------------------------
# 맥락 표시 — 객체형 리뷰 코멘트·여러 줄 필드 (#109, ADR-018)
# --------------------------------------------------------------------------------------


def review_comment(**overrides):
    """ADR-018 객체형 리뷰 코멘트 1건."""
    comment = {
        "comment_id": 777,
        "body": "This `**extra` leaks into JSON Schema.\r\n\r\nPlease use a single `extra` kwarg.",
        "path": "pydantic/fields.py",
        "line": 12,
        "side": "LEFT",
        "outdated": False,
        "author": "reviewer",
    }
    comment.update(overrides)
    return comment


def render_context(**context):
    """레코드 1건의 맥락만 바꿔 화면을 그린다. #33 레코드 파일 경로(`--with-extra-context`)로."""
    record = make_record(0)
    record["context"] = {**record["context"], **context}
    row = sampling.build_labeling_record(record, with_extra_context=True)
    return label_cli.render_record(label_cli.labeler_view(row))


def test_object_review_comment_has_header_and_real_line_breaks():
    """객체형 코멘트: 머리 줄에 comment_id·path·side·line·locator, 본문은 실제 줄바꿈."""
    rendered = render_context(review_comments=[review_comment()])

    header = next(line for line in rendered.splitlines() if "comment_id 777" in line)
    assert "path pydantic/fields.py" in header
    assert "side LEFT" in header
    assert "line 12" in header
    assert "locator review:comment_777" in header  # 가이드 §7.2 표기 그대로 옮겨 적는다
    assert "\\n" not in rendered and "\\r" not in rendered
    assert "{" not in header  # dict 를 한 줄로 찍지 않는다
    lines = rendered.splitlines()
    first = lines.index("      This `**extra` leaks into JSON Schema.")
    assert lines[first + 1 : first + 3] == ["      ", "      Please use a single `extra` kwarg."]


def test_review_comment_with_missing_fields_shows_dash():
    """값이 None 인 칸은 `-`, 빈 본문은 표시만 하고 에러 없이."""
    comment = review_comment(comment_id=None, path=None, line=None, side=None, body="")

    rendered = render_context(review_comments=[comment])

    assert "  - comment_id - · path - · side - · line - · locator -" in rendered.splitlines()
    assert "(본문 없음)" in rendered


def test_outdated_review_comment_line_is_marked():
    """ADR-018: outdated 면 line 은 코멘트 당시 줄이다. 현재 좌표로 오해하지 않게 표시한다."""
    rendered = render_context(review_comments=[review_comment(outdated=True)])

    assert "line 12 (outdated" in rendered


def test_multiple_review_comments_are_separated():
    """코멘트 사이에만 구분선이 들어간다."""
    comments = [review_comment(), review_comment(comment_id=778, body="second")]

    lines = render_context(review_comments=comments).splitlines()

    first = next(i for i, line in enumerate(lines) if "comment_id 777" in line)
    second = next(i for i, line in enumerate(lines) if "comment_id 778" in line)
    assert label_cli.COMMENT_RULE in lines[first:second]
    assert lines.count(label_cli.COMMENT_RULE) == 1  # 사이에만, 끝에는 없다


def test_string_review_comments_from_pre200_show_body_only():
    """예비 200건은 본문 문자열 배열이다. 머리 줄 없이 본문만, 에러 없이."""
    rendered = render_context(review_comments=["old style\r\nsecond line"])

    assert "comment_id" not in rendered
    assert "  - old style" in rendered.splitlines()
    assert "    second line" in rendered.splitlines()


def test_mixed_review_comment_formats_are_handled_one_by_one():
    """문자열·객체가 섞여도 항목마다 타입으로 가른다."""
    rendered = render_context(review_comments=["old style", review_comment()])

    assert "  - old style" in rendered.splitlines()
    assert "locator review:comment_777" in rendered
    assert rendered.count("comment_id") == 1


def test_multiline_pr_body_and_issue_bodies_keep_line_breaks():
    """문자열 필드·문자열 목록 필드도 줄바꿈을 살린다."""
    rendered = render_context(
        pr_body="Summary\r\n\r\n- removes fn_0",
        issue_bodies=["Steps:\n1. call fn_0\n2. crash"],
    )
    lines = rendered.splitlines()

    assert "\\n" not in rendered
    assert lines[lines.index("  Summary") + 2] == "  - removes fn_0"
    assert lines[lines.index("  - Steps:") + 1 : lines.index("  - Steps:") + 3] == [
        "    1. call fn_0",
        "    2. crash",
    ]


def test_pr_labels_are_listed_as_reference_only():
    """ADR-018 결정 3: pr_labels 는 목록으로, 등급 근거가 아니라는 표시와 함께."""
    rendered = render_context(pr_labels=["bug", "performance"])
    lines = rendered.splitlines()

    title = lines.index("pr_labels (참고 정보 — 등급 근거 아님, ADR-018):")
    assert lines[title + 1 : title + 3] == ["  - bug", "  - performance"]


def test_quote_spanning_lines_of_object_review_comment_is_found():
    """객체형 코멘트도 본문에서 찾는다. str(dict) 로는 줄바꿈이 `\\n` 이 되어 못 찾았다."""
    record = make_record(0)
    record["context"]["review_comments"] = [review_comment()]
    view = label_cli.labeler_view(record)

    assert label_cli.found_in_context(
        "leaks into JSON Schema. Please use a single `extra` kwarg.", view
    )


def view_with_context(**context):
    """found_in_context 용: 맥락만 바꾼 라벨러 뷰."""
    record = make_record(0)
    record["context"] = {**record["context"], **context}
    return label_cli.labeler_view(sampling.build_labeling_record(record, with_extra_context=True))


def test_quote_across_two_review_comments_is_not_found():
    """인용은 한 곳에서 나와야 한다. 코멘트 경계를 이어 붙인 문장은 미발견이다 (CodeRabbit)."""
    view = view_with_context(
        review_comments=[review_comment(body="alpha"), review_comment(comment_id=2, body="beta")]
    )

    assert label_cli.found_in_context("alpha", view)
    assert label_cli.found_in_context("beta", view)
    assert not label_cli.found_in_context("alpha beta", view)
    assert not label_cli.found_in_context("alpha … beta", view)  # 생략 조각도 한 곳에


def test_quote_across_fields_or_list_items_is_not_found():
    """코멘트뿐 아니라 필드끼리·목록 항목끼리도 이어 붙이지 않는다."""
    view = view_with_context(
        pr_title="Fix empty headers",
        pr_body="The parser crashed.",
        issue_titles=["first issue", "second issue"],
    )

    assert not label_cli.found_in_context("Fix empty headers The parser crashed.", view)
    assert not label_cli.found_in_context("first issue second issue", view)
    assert label_cli.found_in_context("second issue", view)


def test_multiline_quote_inside_one_review_comment_is_still_found():
    """한 코멘트 안에서라면 여러 줄에 걸친 인용·생략 인용 모두 찾는다."""
    view = view_with_context(
        review_comments=[review_comment(), review_comment(comment_id=2, body="other")]
    )

    assert label_cli.found_in_context("JSON Schema. Please use", view)
    assert label_cli.found_in_context("This `**extra` … single `extra` kwarg.", view)


ESC_BODY = "\x1b[2J\x1b[1;1Hlooks fine\x07\r\n\tindented‮evil\x00"


def test_escape_control_keeps_newline_and_tab_only():
    """ESC·BEL·NUL·RLO 는 보이는 형태로, 줄바꿈·탭은 그대로."""
    escaped = label_cli.escape_control("a\x1b[31mb\n\tc\x07\x7f‮d\r")

    assert escaped == "a\\x1b[31mb\n\tc\\x07\\x7f\\u202ed\\x0d"
    assert label_cli.escape_control("한국어 — 이모지 🙂") == "한국어 — 이모지 🙂"


def test_control_sequences_in_review_comment_are_escaped_on_screen():
    """리뷰 본문·머리 줄의 ESC 시퀀스가 터미널에 그대로 닿지 않는다 (CWE-150)."""
    comment = review_comment(body=ESC_BODY, path="evil\x1b]0;title\x07.py", side="LEFT\x1b[K")

    rendered = render_context(review_comments=[comment])
    lines = rendered.splitlines()

    assert "\x1b" not in rendered and "\x07" not in rendered and "‮" not in rendered
    assert "      \\x1b[2J\\x1b[1;1Hlooks fine\\x07" in lines
    assert "      \tindented\\u202eevil\\x00" in lines  # 탭은 그대로
    assert "path evil\\x1b]0;title\\x07.py · side LEFT\\x1b[K" in rendered


def test_control_sequences_in_every_external_field_are_escaped():
    """맥락·코드·머리 줄 — 레코드에서 온 텍스트는 어디에 찍혀도 이스케이프된다."""
    record = make_record(
        0,
        repo="psf/\x1b[8mrequests",
        file_path="src/a\x1b.py",
        function_signature="def f(\x1b):",
        deleted_body="def f():\n\x1b[2K    pass\n",
        source_url="https://x/\x1b",
    )
    record["replacement"] = {"code": "g()\x1b[1A", "match_method": "NONE\x1b", "confidence": 0.0}
    record["context"] = {
        "commit_message": "fix\x1b[31m",
        "pr_number": 1,
        "pr_title": "t\x1b",
        "pr_body": "b\x1b",
        "issue_numbers": [2],
        "issue_titles": ["it\x1b"],
        "issue_bodies": ["ib\x1b\nsecond\x1b"],
        "pr_labels": ["bug\x1b"],
        "review_comments": ["old\x1b"],
    }
    view = label_cli.labeler_view(sampling.build_labeling_record(record, with_extra_context=True))

    rendered = label_cli.render_record(view)

    assert "\x1b" not in rendered
    for shown in (
        "psf/\\x1b[8mrequests",
        "src/a\\x1b.py",
        "def f(\\x1b):",
        "\\x1b[2K    pass",
        "https://x/\\x1b",
        "g()\\x1b[1A",
        "match_method=NONE\\x1b",
        "fix\\x1b[31m",
        "t\\x1b",
        "b\\x1b",
        "it\\x1b",
        "    second\\x1b",
        "bug\\x1b",
        "old\\x1b",
    ):
        assert shown in rendered, shown


def test_single_line_fields_cannot_fake_extra_lines():
    """경로 같은 한 줄 값의 줄바꿈은 이스케이프한다. 가짜 머리 줄을 만들지 못한다."""
    comment = review_comment(path="a.py\n  - comment_id 999 · locator review:comment_999")

    rendered = render_context(review_comments=[comment])

    assert not any(line.startswith("  - comment_id 999") for line in rendered.splitlines())
    assert "a.py\\x0a  - comment_id 999" in rendered


def test_quote_check_uses_raw_text_not_escaped_display():
    """이스케이프는 표시 전용. 인용 검사는 원문으로 해서 표시 형태(`\\x1b`)와는 맞지 않는다."""
    view = view_with_context(pr_body="stop\x1b[0m here")

    assert label_cli.found_in_context("stop\x1b[0m here", view)
    assert not label_cli.found_in_context("stop\\x1b[0m here", view)
