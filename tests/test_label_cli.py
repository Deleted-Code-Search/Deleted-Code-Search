"""라벨링 CLI 테스트 (이슈 #37). 사람 입력은 스크립트로 흉내 낸다."""

import json

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


def run_session(workspace, answers, clock=lambda: 0.0):
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


def test_main_refuses_missing_template(tmp_path):
    write_jsonl(tmp_path / sampling.RECORDS_FILENAME, [])

    assert label_cli.main(["--labeler", "sj", "--labels-dir", str(tmp_path)]) == 2


def test_main_runs_a_session(workspace, monkeypatch):
    records_path, _ = workspace
    monkeypatch.setattr("builtins.input", Script(explicit_bug(0)))

    code = label_cli.main(["--labeler", "sj", "--labels-dir", str(records_path.parent)])

    assert code == 0
    assert read_rows(records_path.parent / "sj_pre200.jsonl")[0]["reason_label"] == "BUG"
