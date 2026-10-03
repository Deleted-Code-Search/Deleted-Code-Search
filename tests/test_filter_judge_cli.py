"""필터 정밀도 판정 CLI 테스트 (이슈 #89). 사람 입력은 스크립트로 흉내 낸다."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tools import filter_judge_cli as cli


def make_record(index, similar=None):
    return {
        "sample_id": f"fp-{index:03d}",
        "record_id": f"rec-{index}",
        "repo": "psf/requests",
        "commit_sha": f"sha{index}",
        "source_url": f"https://github.com/psf/requests/commit/sha{index}",
        "file_path": f"src/mod{index}.py",
        "function_name": f"fn_{index}",
        "function_signature": f"def fn_{index}():",
        "deletion_kind": "FULL_FUNCTION",
        "deleted_line_count": 2,
        "deleted_body": f"def fn_{index}():\n    return 1",
        "commit_message": "remove fn\x1b[2J",
        "similar_function": similar,
    }


def scripted(answers):
    queue = list(answers)

    def ask(_prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def records_path(tmp_path):
    records = [
        make_record(1),
        make_record(
            2,
            {
                "file_path": "src/b.py",
                "function_name": "g",
                "start_line": 3,
                "end_line": 9,
                "similarity": 0.93,
            },
        ),
        make_record(3),
    ]
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def session(records_path, out, answers, said=None):
    records = cli.load_records(records_path)
    judgments = cli.load_judgments(out, "sj", records)
    return cli.JudgeSession(
        "sj",
        records,
        out,
        judgments,
        ask=scripted(answers),
        say=(said.append if said is not None else lambda _m: None),
        now=Clock(),
    )


def read(out):
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def test_resume_after_quit(records_path, tmp_path):
    out = tmp_path / "sj_judgments.jsonl"
    said = []
    assert session(records_path, out, ["y", "", ":q"], said).run() == 1
    assert "판정자끼리 서로의 판정 파일을 보지 않는다" in "\n".join(said)
    assert [(r["sample_id"], r["meaningful"]) for r in read(out)] == [("fp-001", True)]

    # 다시 실행하면 fp-002 부터
    said = []
    assert session(records_path, out, ["n", "moved to b.py", "y", ""], said).run() == 2
    rows = read(out)
    assert [(r["sample_id"], r["meaningful"], r["note"]) for r in rows] == [
        ("fp-001", True, None),
        ("fp-002", False, "moved to b.py"),
        ("fp-003", True, None),
    ]
    assert all(tuple(r) == cli.JUDGMENT_FIELDS and r["judge"] == "sj" for r in rows)


def test_invalid_answers_are_rejected(records_path, tmp_path):
    out = tmp_path / "sj_judgments.jsonl"
    said = []
    session(records_path, out, ["", "maybe", "Y", "", ":q"], said).run()
    assert sum("y(예) 또는 n(아니오)" in m for m in said) == 2
    assert read(out)[0]["meaningful"] is True


def test_undo_rejudges_previous(records_path, tmp_path):
    out = tmp_path / "sj_judgments.jsonl"
    session(records_path, out, ["y", "", ":u", "n", "actually a move", ":q"]).run()
    rows = read(out)
    assert [(r["sample_id"], r["meaningful"], r["note"]) for r in rows] == [
        ("fp-001", False, "actually a move")
    ]


def test_display_is_neutral_and_escaped(records_path):
    records = cli.load_records(records_path)
    plain = cli.render_record(records[0])
    similar = cli.render_record(records[1])
    assert "\x1b" not in plain and "\\x1b[2J" in plain
    assert "참고: 같은 커밋의 비슷한 함수" not in plain
    assert "참고: 같은 커밋의 비슷한 함수" in similar and "유사도 0.93" in similar
    for text in (plain, similar):
        assert "NOISE" not in text and "KEPT" not in text and "필터" not in text
        assert "2줄" in text


def test_refuses_records_with_filter_status(tmp_path):
    path = tmp_path / "leak.jsonl"
    path.write_text(
        json.dumps({**make_record(1), "filter_status": "KEPT"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(cli.JudgeFileError, match="filter_status"):
        cli.load_records(path)


def test_refuses_other_judges_file(records_path, tmp_path):
    out = tmp_path / "x.jsonl"
    row = {"sample_id": "fp-001", "record_id": "rec-1", "judge": "jh", "meaningful": True}
    out.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(cli.JudgeFileError, match="sj 의 파일이 아니다"):
        cli.load_judgments(out, "sj", cli.load_records(records_path))
    out.write_text(
        json.dumps({**row, "judge": "sj", "record_id": "rec-9"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(cli.JudgeFileError, match="표본이 바뀌었다"):
        cli.load_judgments(out, "sj", cli.load_records(records_path))


def hunk(new_start, new_count, body):
    return {
        "old_start": 1,
        "old_count": 0,
        "new_start": new_start,
        "new_count": new_count,
        "added_body": body,
    }


def test_display_shows_added_hunks_after_deleted_body():
    """판정은 diff 로 한다 (#155, 가이드 §6.3.3). 라벨 화면과 같은 모양."""
    record = make_record(1) | {
        "added_hunks_same_file": [hunk(10, 2, "def g():\n    return 2"), hunk(30, 1, "x\x1b[1A")]
    }

    lines = cli.render_record(record).splitlines()

    assert "== 추가 헝크 (added_hunks_same_file, 2개 — 같은 커밋이 이 파일에 추가한 줄) ==" in lines
    at = lines.index("[1/2] new_start 10 · new_count 2")
    assert lines[at + 1 : at + 3] == ["    def g():", "        return 2"]
    assert "[2/2] new_start 30 · new_count 1" in lines
    assert "    x\\x1b[1A" in lines
    assert lines.index("== 삭제된 본문 (deleted_body, 2줄) ==") < at


def test_display_without_added_lines():
    empty = cli.render_record(make_record(1) | {"added_hunks_same_file": []})
    missing = cli.render_record(make_record(1))

    assert "(이 커밋이 이 파일에 추가한 줄 없음)" in empty
    assert "(이 커밋이 이 파일에 추가한 줄 없음)" not in missing


def test_added_hunks_do_not_let_filter_status_in(tmp_path):
    """추가 헝크를 넣어도 판정용 파일 규칙은 그대로다 — filter_status 가 있으면 거부한다."""
    path = tmp_path / "records.jsonl"
    good = make_record(1) | {"added_hunks_same_file": [hunk(1, 1, "a")]}
    path.write_text(json.dumps(good) + "\n", encoding="utf-8")
    shown = cli.render_record(cli.load_records(path)[0])
    assert "filter_status" not in shown and "NOISE" not in shown and "KEPT" not in shown

    path.write_text(json.dumps(good | {"filter_status": "KEPT"}) + "\n", encoding="utf-8")
    with pytest.raises(cli.JudgeFileError, match="filter_status"):
        cli.load_records(path)


def test_start_screen_states_the_four_no_cases():
    """가이드 §6.3.3 (#155): n 은 네 가지뿐, diff 를 보고 판정."""
    for text in (
        "순수 이동·리네임",
        "기계적 포맷·스타일 변환",
        "생성·벤더링 코드",
        "다른 저장소로 분리",
    ):
        assert text in cli.START_SCREEN
    assert "revert 로 지워짐 y" in cli.START_SCREEN
    assert "커밋 메시지만으로 판정하지 않는다" in cli.START_SCREEN


def test_parse_answer():
    assert cli.parse_answer("예") is True
    assert cli.parse_answer("N") is False
    with pytest.raises(ValueError):
        cli.parse_answer("")
