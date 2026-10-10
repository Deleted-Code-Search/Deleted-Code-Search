"""제3자 판정 CLI 테스트 (이슈 #90). 사람 입력은 스크립트로 흉내 낸다."""

import json
import re
from datetime import UTC, datetime

import pytest

from classify import labels, sampling, sampling_main500
from tests.test_label_cli import Script, make_record, read_rows, write_jsonl
from tools import adjudicate_cli, label_cli

NOW = datetime(2026, 10, 10, 15, 0, 0, tzinfo=UTC)
PAIRS = {"A": ("sj", "jh"), "B": ("jh", "hs"), "C": ("hs", "sj")}


def label(record_id, labeler, reason, grade, note=""):
    unknown = grade == "UNKNOWN"
    return {
        "record_id": record_id,
        "labeler": labeler,
        "reason_label": reason,
        "evidence_grade": grade,
        "evidence_text": None if unknown else f"근거-{reason}-{grade}",
        "evidence_source": None if unknown else "commit",
        "evidence_locator": None if unknown else "commit:message",
        "confidence": {"EXPLICIT": 1.0, "INFERRED": 0.6, "UNKNOWN": 0.0}[grade],
        "note": note,
        "labeled_at": "2026-10-09T10:00:00+09:00",
        "guide_version": "v3",
    }


@pytest.fixture
def workspace(tmp_path):
    """블록마다 3건: 1번은 일치, 2번은 이유가, 3번은 등급이 갈린다 → 블록당 대상 2건."""
    records, assignment = [], []
    personal = {labeler: [] for labeler in sampling.LABELERS}
    for offset, (block, (first, second)) in enumerate(PAIRS.items()):
        for position in range(1, 4):
            record = make_record(offset * 10 + position)
            rid = record["id"]
            records.append(sampling.build_labeling_record(record))
            assignment.append(
                {
                    "record_id": rid,
                    "batch": "main500",
                    "block": block,
                    "labelers": [first, second],
                    "block_position": position,
                    "interim": False,
                    "split": "train",
                }
            )
            personal[first].append(label(rid, first, "BUG", "EXPLICIT"))
            other = [("BUG", "EXPLICIT"), ("DESIGN", "EXPLICIT"), ("BUG", "INFERRED")][position - 1]
            personal[second].append(label(rid, second, *other, note="priority-rule"))
    write_jsonl(tmp_path / sampling_main500.RECORDS_OUT, records)
    write_jsonl(tmp_path / sampling_main500.ASSIGNMENT_OUT, assignment)
    for labeler, rows in personal.items():
        write_jsonl(tmp_path / sampling_main500.LABEL_OUT_TEMPLATE.format(labeler=labeler), rows)
    return tmp_path


def run_block(workspace, block, answers):
    blocks = adjudicate_cli.load_targets(workspace, workspace / "none.jsonl", expected_total=6)
    records, problems = label_cli.load_records(workspace / sampling_main500.RECORDS_OUT)
    assert problems == []
    transcript = []
    out_path = workspace / adjudicate_cli.OUT_FILENAME
    session = adjudicate_cli.AdjudicationSession(
        block,
        blocks[block],
        records,
        out_path,
        ask=Script(answers, transcript),
        say=transcript.append,
        now=lambda: NOW,
    )
    session.run()
    rows = read_rows(out_path) if out_path.is_file() else []
    return rows, "\n".join(transcript)


def test_targets_are_the_records_labels_py_leaves_without_final(workspace):
    blocks = adjudicate_cli.load_targets(workspace, workspace / "none.jsonl", expected_total=6)
    assert {block: len(rows) for block, rows in blocks.items()} == {"A": 2, "B": 2, "C": 2}
    personal = labels.load_personal_labels(workspace, "main500")
    assignment = labels.load_assignment(workspace, "main500")
    merged = labels.merge_labels(personal, batch="main500", assignment=assignment)
    expected = {row["record_id"] for row in merged if not row["final"]}
    assert {row["record_id"] for rows in blocks.values() for row in rows} == expected


def test_stops_when_total_differs_from_expected(workspace):
    with pytest.raises(adjudicate_cli.TargetError, match="기대 150건"):
        adjudicate_cli.load_targets(workspace, workspace / "none.jsonl")


def test_final_copies_the_chosen_label_whole(workspace):
    rows, _ = run_block(workspace, "A", ["1", "2"])
    personal = labels.load_personal_labels(workspace, "main500")
    assert [row["record_id"] for row in rows] == ["rec-002", "rec-003"]
    for row in rows:
        source = next(
            item
            for item in personal[row["chosen_labeler"]]
            if item["record_id"] == row["record_id"]
        )
        final = row["final"]
        assert list(final) == list(labels.build_final([source, source]))
        for name in (*adjudicate_cli.CHOSEN_FIELDS, "note"):
            assert final[name] == source[name]
        assert final["method"] == "THIRD_PARTY"
        assert final["adjudicated_by"] == ["hs"]
        assert final["adjudicated_at"] == NOW.isoformat(timespec="seconds")
        assert row["block"] == "A"


def test_screen_hides_labelers_and_shows_header_and_progress(workspace):
    _, screen = run_block(workspace, "A", ["1", "1"])
    assert "블록 A — 판정자 hs" in screen
    assert "[1/2]" in screen and "[2/2]" in screen
    assert "== 라벨 1 ==" in screen and "== 라벨 2 ==" in screen
    assert "priority-rule" in screen and "commit:message" in screen
    assert not re.search(r"\b(sj|jh)\b", screen)
    assert "labeled_at" not in screen


def test_display_order_is_fixed_per_record_and_not_always_alphabetical():
    pair = [{"labeler": "sj"}, {"labeler": "jh"}]
    firsts = set()
    for index in range(40):
        ordered = adjudicate_cli.display_order(f"rec-{index}", pair)
        assert ordered == adjudicate_cli.display_order(f"rec-{index}", pair[::-1])
        firsts.add(ordered[0]["labeler"])
    assert firsts == {"sj", "jh"}


def test_each_choice_is_on_disk_immediately_and_resume_skips_it(workspace):
    rows, _ = run_block(workspace, "B", ["1"])  # 두 번째 건에서 EOF — 터미널을 닫은 것과 같다
    assert [row["record_id"] for row in rows] == ["rec-012"]
    rows, screen = run_block(workspace, "B", ["2", ""])
    assert [row["record_id"] for row in rows] == ["rec-012", "rec-013"]
    assert "[2/2]" in screen and "[1/2]" not in screen


def test_undo_reopens_the_previous_record_and_replaces_its_line(workspace):
    rows, screen = run_block(workspace, "C", ["1", ":u", "2", "1", ""])
    assert [row["record_id"] for row in rows] == ["rec-022", "rec-023"]
    assert "다시 판정" in screen
    first = adjudicate_cli.display_order("rec-022", [{"labeler": "hs"}, {"labeler": "sj"}])
    assert rows[0]["chosen_labeler"] == first[1]["labeler"]


def test_undo_after_restart_and_other_blocks_are_untouched(workspace):
    run_block(workspace, "A", ["1", "1"])
    rows, _ = run_block(workspace, "B", ["1", ":q"])
    assert [row["block"] for row in rows] == ["A", "A", "B"]
    rows, screen = run_block(workspace, "B", [":u", "2", ":q"])
    assert [row["record_id"] for row in rows] == ["rec-002", "rec-003", "rec-012"]
    assert "다시 판정" in screen


def test_invalid_input_is_asked_again_and_label_files_are_not_modified(workspace):
    paths = [workspace / f"{who}_main500.jsonl" for who in sampling.LABELERS]
    before = [path.read_bytes() for path in paths]
    rows, screen = run_block(workspace, "A", ["3", "", "sj", "1", ":q"])
    assert len(rows) == 1
    assert screen.count("! 1 또는 2") == 3
    assert [path.read_bytes() for path in paths] == before
    assert json.loads(json.dumps(rows[0]))["final"]["method"] == "THIRD_PARTY"
