"""v3 재판정 준비 도구 테스트 (#164). 대상 밖 줄이 바이트 그대로인지가 핵심이다."""

import json
from pathlib import Path

import pytest

from classify import sampling
from tools import label_cli, rejudge_prep

REPORT = """# 재판정 대상

| 파일 | sha256 앞 16자리 |
|---|---|
| `datasets/labels/sj_main500.jsonl` | `5f71a37677f807d6` |

jh가 블록 A 51번(`aaaaaaaa-0000-0000-0000-000000000051`)을 v2로 저장했다.

## sj — 2건 (A 1 · C 1)

| 건 | `record_id` | 저장소 |
|---|---|---|
| A01 | `aaaaaaaa-0000-0000-0000-000000000001` | a/b |
| C02 | `cccccccc-0000-0000-0000-000000000002` | a/b |

## jh — 1건 (A 1)

| 건 | `record_id` | 저장소 |
|---|---|---|
| A01 | `aaaaaaaa-0000-0000-0000-000000000001` | a/b |
"""
A1 = "aaaaaaaa-0000-0000-0000-000000000001"
A2 = "aaaaaaaa-0000-0000-0000-000000000002"
A51 = "aaaaaaaa-0000-0000-0000-000000000051"
C2 = "cccccccc-0000-0000-0000-000000000002"


def label(record_id, labeler="sj", reason="BUG", note=""):
    return {
        "record_id": record_id,
        "labeler": labeler,
        "reason_label": reason,
        "evidence_grade": "EXPLICIT",
        "evidence_text": "fix crash",
        "evidence_source": "commit",
        "evidence_locator": "commit:message",
        "confidence": 1.0,
        "note": note,
        "labeled_at": "2026-10-06T14:03:05+09:00",
        "guide_version": "v2",
    }


def line(row, **dump_options):
    """label_cli 가 쓰는 모양(`ensure_ascii=False`)이 기본. 다른 모양은 `dump_options` 로."""
    options = {"ensure_ascii": False, **dump_options}
    return (json.dumps(row, **options) + "\n").encode("utf-8")


def sj_file_bytes():
    """대상 2줄(A1, C2) + 대상 밖 줄. 대상 밖 줄은 label_cli 가 쓰지 않을 모양으로 일부러 만든다 —
    다시 직렬화하면 바이트가 달라지므로, 그대로 남았는지가 이 도구의 검증 대상이다."""
    return b"".join(
        [
            line(label(A1)),
            # 한 줄 안의 U+2028. splitlines() 로 나누면 여기서 레코드가 잘린다 (#146)
            line(label(A2, note="tag second line")),
            # ensure_ascii 이스케이프·다른 구분자 — 다시 쓰면 바이트가 바뀐다
            line(label(C2, reason="DEAD"), ensure_ascii=True, separators=(",", ":")),
            # 빈 틀 줄 (51번 이후)
            line(sampling.empty_label_row(A51, "sj")),
        ]
    )


def assignment_rows():
    rows = []
    for record_id, block, position in ((A1, "A", 1), (A2, "A", 2), (A51, "A", 51), (C2, "C", 2)):
        labelers = ["sj", "jh"] if block == "A" else ["hs", "sj"]
        rows.append(
            {
                "record_id": record_id,
                "block": block,
                "labelers": labelers,
                "block_position": position,
                "interim": position <= 50,
                "split": "train",
            }
        )
    return rows


@pytest.fixture
def workspace(tmp_path):
    """라벨 디렉터리(배분·sj 라벨·스냅샷) + 재판정 문서."""
    labels_dir = tmp_path / "labels"
    snapshot_dir = labels_dir / rejudge_prep.SNAPSHOT_SUBDIR
    snapshot_dir.mkdir(parents=True)
    data = sj_file_bytes()
    (labels_dir / "sj_main500.jsonl").write_bytes(data)
    (snapshot_dir / "sj_main500.jsonl").write_bytes(data)
    (labels_dir / "main500_assignment.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in assignment_rows()), encoding="utf-8"
    )
    report = tmp_path / "rejudge.md"
    report.write_text(REPORT, encoding="utf-8")
    return labels_dir, report


def run(workspace, *extra):
    labels_dir, report = workspace
    argv = ["--labeler", "sj", "--labels-dir", str(labels_dir), "--report", str(report), *extra]
    return rejudge_prep.main(argv)


# --------------------------------------------------------------------------------------
# 대상 목록
# --------------------------------------------------------------------------------------


def test_targets_come_only_from_labeler_sections():
    """입력 표·본문의 id(블록 A 51번)는 대상이 아니다."""
    targets = rejudge_prep.parse_targets(REPORT)

    assert targets == {"sj": [("A01", A1), ("C02", C2)], "jh": [("A01", A1)]}


def test_section_count_must_match_the_table():
    """머리 줄 건수와 표 행 수가 다르면 문서에서 행이 빠졌을 수 있다."""
    broken = REPORT.replace("## sj — 2건", "## sj — 3건")

    with pytest.raises(rejudge_prep.PrepError, match="sj 절은 3건"):
        rejudge_prep.parse_targets(broken)


def test_the_real_rejudge_report_lists_48_42_48():
    """docs/reports/interim_90_rejudge.md 의 라벨러별 건수 (sj 48 · jh 42 · hs 48, 69건 × 2인)."""
    text = Path("docs/reports/interim_90_rejudge.md").read_text(encoding="utf-8")

    targets = rejudge_prep.parse_targets(text)

    assert {labeler: len(rows) for labeler, rows in targets.items()} == {
        "sj": 48,
        "jh": 42,
        "hs": 48,
    }
    assert len({record_id for rows in targets.values() for _, record_id in rows}) == 69


def test_targets_outside_the_first_50_are_refused():
    """재판정은 블록의 처음 50건 안에서만 한다 (§8.4.1.1)."""
    by_id = {row["record_id"]: row for row in assignment_rows()}

    with pytest.raises(rejudge_prep.PrepError, match="처음 50건"):
        rejudge_prep.check_against_assignment("sj", [("A51", A51)], by_id)
    with pytest.raises(rejudge_prep.PrepError, match="몫이 아니다"):
        rejudge_prep.check_against_assignment("jh", [("C02", C2)], by_id)


# --------------------------------------------------------------------------------------
# 계획
# --------------------------------------------------------------------------------------


def test_only_target_rows_are_blanked_and_the_rest_is_byte_identical():
    data = sj_file_bytes()

    plan = rejudge_prep.plan_clear(data, "sj", {A1, C2})

    before, after = rejudge_prep.split_lines(data), rejudge_prep.split_lines(plan.data)
    assert plan.cleared == [A1, C2]
    assert after[1] == before[1] and after[3] == before[3]
    assert json.loads(after[0]) == sampling.empty_label_row(A1, "sj")
    assert json.loads(after[2]) == sampling.empty_label_row(C2, "sj")
    assert rejudge_prep.verify(data, plan.data, "sj", {A1, C2}) == []


def test_split_lines_round_trips_without_a_final_newline():
    data = b'{"record_id": "x"}\n{"record_id": "y"}'

    assert b"".join(rejudge_prep.split_lines(data)) == data
    assert len(rejudge_prep.split_lines(data)) == 2


def test_already_blank_targets_are_skipped_so_a_second_run_changes_nothing():
    first = rejudge_prep.plan_clear(sj_file_bytes(), "sj", {A1, C2})

    second = rejudge_prep.plan_clear(first.data, "sj", {A1, C2})

    assert second.cleared == [] and second.already_blank == [A1, C2]
    assert second.data == first.data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (line(label(A1)), "파일에 없다"),
        (line(label(A1, labeler="jh")) + line(label(C2)), "sj 의 파일이 아니다"),
        (line(label(A1)) + line(label(A1)) + line(label(C2)), "두 번 있다"),
    ],
)
def test_plan_stops_on_a_broken_label_file(data, message):
    with pytest.raises(rejudge_prep.PrepError, match=message):
        rejudge_prep.plan_clear(data, "sj", {A1, C2})


def test_verify_catches_a_change_outside_the_targets():
    data = sj_file_bytes()
    lines = rejudge_prep.split_lines(data)
    lines[1] = line(label(A2, reason="PERF"))

    problems = rejudge_prep.verify(data, b"".join(lines), "sj", {A1, C2})

    assert problems == ["2번째 줄: 대상이 아닌데 바뀌었다"]


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_dry_run_is_the_default_and_writes_nothing(workspace, capsys):
    labels_dir, _ = workspace
    before = (labels_dir / "sj_main500.jsonl").read_bytes()

    assert run(workspace) == 0

    assert (labels_dir / "sj_main500.jsonl").read_bytes() == before
    output = capsys.readouterr().out
    assert "비울 줄 2건" in output and "dry-run" in output
    # 이전 라벨 값을 찍지 않는다 (가이드 §9-11)
    assert "BUG" not in output and "DEAD" not in output


def test_apply_blanks_targets_and_label_cli_reopens_them_first(workspace):
    labels_dir, _ = workspace
    path = labels_dir / "sj_main500.jsonl"
    before = path.read_bytes()

    assert run(workspace, "--apply") == 0

    assert rejudge_prep.verify(before, path.read_bytes(), "sj", {A1, C2}) == []
    label_file = label_cli.LabelFile.load(path)
    assert label_file.rows[label_file.next_unlabeled()]["record_id"] == A1
    assert not list(path.parent.glob("*.tmp"))


def test_apply_refuses_without_a_snapshot(workspace, capsys):
    labels_dir, _ = workspace
    (labels_dir / rejudge_prep.SNAPSHOT_SUBDIR / "sj_main500.jsonl").unlink()
    before = (labels_dir / "sj_main500.jsonl").read_bytes()

    assert run(workspace, "--apply") == 2

    assert (labels_dir / "sj_main500.jsonl").read_bytes() == before
    assert "스냅샷이 없다" in capsys.readouterr().err


def test_apply_will_not_erase_a_label_saved_after_the_snapshot(workspace, capsys):
    """A1 을 비운 뒤 v3 로 다시 저장했다 → 다시 돌려도 그 재판정 라벨은 지우지 않는다."""
    labels_dir, _ = workspace
    path = labels_dir / "sj_main500.jsonl"
    lines = rejudge_prep.split_lines(path.read_bytes())
    rejudged = {**label(A1, reason="DEAD"), "guide_version": "v3"}
    lines[0] = line(rejudged)
    path.write_bytes(b"".join(lines))
    before = path.read_bytes()

    assert run(workspace, "--apply") == 2

    assert path.read_bytes() == before
    assert f"{A1}: 스냅샷과 다르다" in capsys.readouterr().err


def test_dry_run_and_apply_are_exclusive(workspace):
    with pytest.raises(SystemExit):
        run(workspace, "--dry-run", "--apply")
