"""pipeline/postfilter.py 테스트 (Issue #152, 사후 필터 NOISE_FORMAT)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import postfilter
from pipeline.filter import FILTER_RULE_VERSION
from pipeline.postfilter import (
    NOISE_FORMAT,
    PostFilterError,
    apply_file,
    apply_row,
    format_keywords,
    reappear_counts,
    report_path_for,
)

# black이 인자를 한 줄로 접은 재포맷. 삭제 줄 4개가 모두 추가 쪽에 다시 나타난다.
_DELETED = "    x = foo(\n        'a',\n        b,\n    )\n"
_ADDED = '    x = foo("a", b)\n'


def _row(
    *,
    message: str = "Reformat with black",
    deleted: str = _DELETED,
    added: str = _ADDED,
    status: str = "KEPT",
    **extra,
) -> dict:
    return {
        "id": "r1",
        "repo": "acme/widgets",
        "deletion_kind": "PARTIAL",
        "commit_message": message,
        "deleted_body": deleted,
        "added_hunks_same_file": [{"new_start": 1, "new_count": 1, "added_body": added}],
        "filter_status": status,
        "filter_rule_version": "v0.7",
        **extra,
    }


def test_version_bumped_to_v08():
    assert FILTER_RULE_VERSION == "v0.8"
    assert apply_row(_row())["filter_rule_version"] == "v0.8"
    assert apply_row(_row(message="Add feature"))["filter_rule_version"] == "v0.8"


def test_keyword_and_reappearance_becomes_noise_format():
    out = apply_row(_row())
    assert out["filter_status"] == NOISE_FORMAT
    assert out["filter_evidence"] == {
        "keywords": ["reformat", "black"],
        "reappear_ratio": 1.0,
        "reappeared_lines": 4,
        "deleted_lines": 4,
    }


def test_keyword_without_reappearance_stays_kept():
    row = _row(added="    x = bar()\n")
    out = apply_row(row)
    assert out["filter_status"] == "KEPT"
    assert "filter_evidence" not in out


def test_reappearance_without_keyword_stays_kept():
    """재들여쓰기 같은 재등장 100%라도 메시지가 포맷 커밋이 아니면 둔다 (분석 문서 A3 손실)."""
    out = apply_row(_row(message="Add support for US in the integration"))
    assert reappear_counts(_DELETED, _ADDED) == (4, 4)
    assert out["filter_status"] == "KEPT"
    assert "filter_evidence" not in out


@pytest.mark.parametrize("status", ["NOISE_MOVE", "NOISE_TRIVIAL"])
def test_existing_noise_row_unchanged_except_version(status):
    row = _row(status=status, filter_evidence={"line_count": 4})
    out = apply_row(row)
    assert out == {**row, "filter_rule_version": FILTER_RULE_VERSION}


def test_threshold_is_ninety_percent():
    deleted = "".join(f"    line{i}()\n" for i in range(10))
    nine = "".join(f"line{i}()\n" for i in range(9))
    assert apply_row(_row(deleted=deleted, added=nine))["filter_status"] == NOISE_FORMAT
    eight = "".join(f"line{i}()\n" for i in range(8))
    assert apply_row(_row(deleted=deleted, added=eight))["filter_status"] == "KEPT"


def test_keywords_only_from_subject_line_and_whole_words():
    assert format_keywords("Fix bug\n\nran ruff and black") == []
    assert format_keywords("Remove blacklist and stylesheet") == []
    assert format_keywords("STYLE: Apply black formatting") == ["style", "black", "formatting"]
    assert format_keywords("CLN: re-wrap docstrings") == ["re-wrap"]
    assert format_keywords("fix linting, PEP 8 and flake8") == ["linting", "pep 8", "flake8"]


def test_normalization_matches_analysis_definition():
    # 따옴표 통일, 닫는 괄호 앞 쉼표, 줄 끝 쉼표, 공백뿐인 줄은 세지 않음
    assert reappear_counts("  a = ['x', ]\n\n   \n  y,\n", 'a=["x"]\nf(y)\n') == (2, 2)
    assert reappear_counts("   \n", "anything") == (0, 0)


def test_missing_added_field_stays_kept():
    row = _row()
    del row["added_hunks_same_file"]
    assert apply_row(row)["filter_status"] == "KEPT"


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_apply_file_writes_new_file_and_report(tmp_path: Path):
    src = tmp_path / "records.jsonl"
    rows = [
        _row(),
        _row(message="Add feature"),
        _row(status="NOISE_MOVE"),
        {**_row(), "repo": "acme/other", "deletion_kind": "FULL_FUNCTION"},
    ]
    _write(src, rows)
    before = src.read_bytes()
    out = tmp_path / "out" / "records_v0.8.jsonl"

    report = apply_file(src, out)

    assert src.read_bytes() == before  # 원본은 그대로
    result = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["filter_status"] for r in result] == [
        NOISE_FORMAT,
        "KEPT",
        "NOISE_MOVE",
        NOISE_FORMAT,
    ]
    assert {r["filter_rule_version"] for r in result} == {FILTER_RULE_VERSION}
    assert report["noise_format_count"] == 2
    assert report["noise_format_by_kind"] == {"FULL_FUNCTION": 1, "PARTIAL": 1}
    assert report["noise_format_by_repo"] == {
        "acme/other": {"FULL_FUNCTION": 1},
        "acme/widgets": {"PARTIAL": 1},
    }
    assert report["filter_status_before"] == {"KEPT": 3, "NOISE_MOVE": 1}
    assert report["filter_status_after"] == {"KEPT": 1, "NOISE_FORMAT": 2, "NOISE_MOVE": 1}
    assert json.loads(report_path_for(out).read_text(encoding="utf-8")) == report
    assert not list(out.parent.glob("*.tmp"))


def test_apply_file_is_idempotent_on_its_output(tmp_path: Path):
    src, first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "c.jsonl"
    _write(src, [_row(), _row(message="Add feature")])
    apply_file(src, first)
    report = apply_file(first, second)
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert report["noise_format_count"] == 0  # 이미 NOISE_FORMAT이라 바뀐 것이 없다


def test_apply_file_rejects_other_version_and_keeps_old_output(tmp_path: Path):
    src, out = tmp_path / "records.jsonl", tmp_path / "out.jsonl"
    out.write_text("previous\n", encoding="utf-8")
    _write(src, [_row(), {**_row(), "filter_rule_version": "v0.6"}])
    with pytest.raises(PostFilterError, match="v0.6"):
        apply_file(src, out)
    assert out.read_text(encoding="utf-8") == "previous\n"
    assert not (tmp_path / "out.jsonl.tmp").exists()


def test_apply_file_refuses_to_overwrite_input(tmp_path: Path):
    src = tmp_path / "records.jsonl"
    _write(src, [_row()])
    with pytest.raises(PostFilterError, match="입력과 같은 경로"):
        apply_file(src, src)


def test_cli(tmp_path: Path):
    src, out = tmp_path / "records.jsonl", tmp_path / "out.jsonl"
    _write(src, [_row()])
    assert postfilter.main(["--input", str(src), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["filter_status"] == NOISE_FORMAT
    assert postfilter.main(["--input", str(src), "--out", str(src)]) == 1
