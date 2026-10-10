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


# --------------------------------------------------------------------------------------
# 병합 뒤에도 같은 대상, 이어하기 검증, 병합 경로 (#166 CodeRabbit)
# --------------------------------------------------------------------------------------


def adjudicate_all_and_merge(workspace):
    """세 블록을 모두 판정하고 `classify.labels` 로 병합 파일을 쓴다 → 병합 파일 경로."""
    for block in PAIRS:
        run_block(workspace, block, ["1", "2", ""])
    merged_path = workspace / "merged.jsonl"
    assert labels.main(["--labels-dir", str(workspace), "--out", str(merged_path)]) == 0
    assert {row["final"]["method"] for row in read_rows(merged_path)} == {"AGREED", "THIRD_PARTY"}
    return merged_path


def test_targets_stay_the_same_after_rulings_are_merged(workspace):
    """병합 파일에 THIRD_PARTY 가 들어간 뒤에도 대상은 그대로다. 전에는 0건이 되어 멈췄다."""
    before = adjudicate_cli.load_targets(workspace, None, expected_total=6)
    merged_path = adjudicate_all_and_merge(workspace)

    after = adjudicate_cli.load_targets(workspace, merged_path, expected_total=6)

    assert {block: [row["record_id"] for row in rows] for block, rows in after.items()} == {
        block: [row["record_id"] for row in rows] for block, rows in before.items()
    }
    assert all(row["final"]["method"] == "THIRD_PARTY" for rows in after.values() for row in rows)


def test_resume_after_merge_asks_nothing_new(workspace):
    merged_path = adjudicate_all_and_merge(workspace)
    blocks = adjudicate_cli.load_targets(workspace, merged_path, expected_total=6)
    records, _ = label_cli.load_records(workspace / sampling_main500.RECORDS_OUT)
    out_path = workspace / adjudicate_cli.OUT_FILENAME
    before = out_path.read_bytes()
    transcript = []

    for block in PAIRS:
        session = adjudicate_cli.AdjudicationSession(
            block,
            blocks[block],
            records,
            out_path,
            ask=Script([], transcript),
            say=transcript.append,
        )
        assert (len(session.done), session.invalid) == (2, [])
        assert session.run() == 0

    assert "어느 라벨?" not in "\n".join(transcript)
    assert out_path.read_bytes() == before


def _break_ruling(kind, row):
    final = dict(row["final"])
    if kind == "method":
        final["method"] = "DISCUSSED"
    elif kind == "judge":
        final["adjudicated_by"] = ["sj"]  # 블록 A 의 라벨러다. 제3자는 hs
    elif kind == "mixed":
        final["evidence_grade"] = "UNKNOWN"  # 두 라벨 어느 쪽과도 통째로 같지 않다
    elif kind == "chosen":
        return {**row, "chosen_labeler": "jh" if row["chosen_labeler"] == "sj" else "sj"}
    return {**row, "final": final}


@pytest.mark.parametrize("kind", ["method", "judge", "mixed", "chosen"])
def test_resume_asks_again_for_an_invalid_ruling(workspace, kind):
    """판정 파일에 있어도 병합이 거부할 줄은 끝난 건이 아니다 - 다시 묻고 그 줄을 교체한다."""
    rows, _ = run_block(workspace, "A", ["1", "1", ""])
    out_path = workspace / adjudicate_cli.OUT_FILENAME
    write_jsonl(out_path, [_break_ruling(kind, rows[0]), rows[1]])

    fixed, screen = run_block(workspace, "A", ["1", ""])

    assert "[2/2]" in screen and "[1/2]" not in screen  # 유효한 1건은 끝난 건, 깨진 1건만 다시
    assert sorted(row["record_id"] for row in fixed) == ["rec-002", "rec-003"]
    assert fixed[-1]["record_id"] == rows[0]["record_id"]  # 다시 판정한 줄이 끝으로 간다
    assert fixed[-1] == rows[0]
    personal = labels.load_personal_labels(workspace, "main500")
    assignment = labels.load_assignment(workspace, "main500")
    merged = labels.merge_labels(personal, batch="main500", assignment=assignment)
    targets = [row for row in merged if assignment[row["record_id"]]["block"] == "A"]
    assert not [
        problem
        for problem in labels.find_adjudication_problems(targets, fixed)
        if "판정이 없는" not in problem
    ]


def test_default_merged_file_is_only_used_with_the_default_labels_dir(tmp_path):
    default_dir = adjudicate_cli.DEFAULT_LABELS_DIR
    explicit = tmp_path / "other.jsonl"

    assert (
        adjudicate_cli.resolve_merged_path(default_dir, None) == adjudicate_cli.DEFAULT_MERGED_PATH
    )
    assert adjudicate_cli.resolve_merged_path(tmp_path, None) is None
    assert adjudicate_cli.resolve_merged_path(tmp_path, explicit) == explicit
    assert adjudicate_cli.resolve_merged_path(default_dir, explicit) == explicit
    assert adjudicate_cli.DEFAULT_MERGED_PATH.name == labels.MERGED_FILENAME
    args = adjudicate_cli.build_parser().parse_args(["--block", "A", "--merged", str(explicit)])
    assert (args.merged, args.labels_dir) == (explicit, default_dir)


def test_another_labels_dir_does_not_inherit_the_default_merged_file(workspace, monkeypatch):
    """`--labels-dir` 만 바꾼 실행이 현재 폴더의 기본 병합 파일(다른 묶음의 확정)을 읽지 않는다."""
    merged_path = adjudicate_all_and_merge(workspace)
    stale = [
        {**row, "final": {**row["final"], "method": "DISCUSSED"}}
        if row["final"]["method"] == "THIRD_PARTY"
        else row
        for row in read_rows(merged_path)
    ]
    cwd = workspace / "cwd"
    (cwd / "datasets").mkdir(parents=True)
    write_jsonl(cwd / adjudicate_cli.DEFAULT_MERGED_PATH, stale)
    monkeypatch.chdir(cwd)

    # 기본 병합 파일을 읽으면 DISCUSSED 6건이 대상에서 빠져 "0건 vs 6건" 으로 멈춘다
    with pytest.raises(adjudicate_cli.TargetError, match="세 블록 합 0건"):
        adjudicate_cli.load_targets(workspace, adjudicate_cli.DEFAULT_MERGED_PATH, expected_total=6)
    path = adjudicate_cli.resolve_merged_path(workspace, None)
    blocks = adjudicate_cli.load_targets(workspace, path, expected_total=6)
    assert {block: len(rows) for block, rows in blocks.items()} == {"A": 2, "B": 2, "C": 2}
