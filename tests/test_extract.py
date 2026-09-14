"""extract.py 테스트 (이슈 #5).

`tmp_path`에 실제 git 저장소를 만들어(subprocess) 커밋 두 개(부모/자식)를 쌓고,
`walk_commits`(walk.py, 기존 구현 그대로 재사용)로 `CommitPair`를 얻어
`extract_deletions`에 넘긴다. 네트워크 없음.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from pipeline import extract as extract_module
from pipeline.context import parse_targets  # 읽기 전용 재사용 — context.py는 수정하지 않는다
from pipeline.walk import CommitPair, walk_commits

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

_REPO = "acme/widgets"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    return path


def _write(repo: Path, rel_path: str, content: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _last_commit_pair(repo: Path) -> CommitPair:
    """가장 최근 커밋의 `CommitPair` (부모 대비). walk.py를 그대로 재사용한다."""
    return walk_commits(repo, "main")[-1]


def _sample_records(count: int = 3) -> list[extract_module.DeletedFunction]:
    """JSONL 저장 테스트용 `DeletedFunction` 더미 — git 없이 씀."""
    return [
        extract_module.DeletedFunction(
            repo=_REPO,
            commit_sha=f"c{i}",
            parent_sha=f"p{i}",
            file_path="a.py",
            function_name=f"fn{i}",
            start_line=1,
            end_line=2,
            deletion_kind="FULL_FUNCTION",
            deleted_hunk=f"def fn{i}():\n    return {i}",
            added_hunk_same_file="",
            author_date="2026-01-01T00:00:00+00:00",
            commit_message=f"delete fn{i}",
        )
        for i in range(count)
    ]


_GIT_MISSING = subprocess.run(["git", "--version"], capture_output=True).returncode != 0
requires_git = pytest.mark.skipif(_GIT_MISSING, reason="git CLI가 필요하다")


# --------------------------------------------------------------------------------------
# parse_file_diffs: 순수 함수, git 없이 캔 diff 문자열로 검증
# --------------------------------------------------------------------------------------


def test_parse_file_diffs_splits_deleted_and_added_lines():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "index 111..222 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -4,2 +3 @@ def foo():\n"
        "-    x = 1\n"
        "-    y = 2\n"
        "+    z = 3\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert set(files) == {"a.py"}
    assert files["a.py"].deleted_lines == {4: "    x = 1", 5: "    y = 2"}
    assert files["a.py"].added_lines == ["    z = 3"]


def test_parse_file_diffs_ignores_no_newline_marker():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-x = 1\n"
        "\\ No newline at end of file\n"
    )
    files = extract_module.parse_file_diffs(diff)
    assert files["a.py"].deleted_lines == {1: "x = 1"}


def test_parse_file_diffs_skips_pure_new_file():
    diff = "diff --git a/a.py b/a.py\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n"
    files = extract_module.parse_file_diffs(diff)
    assert files == {}


# --------------------------------------------------------------------------------------
# extract_deletions: 실제 git 저장소
# --------------------------------------------------------------------------------------


@requires_git
class TestExtractDeletions:
    def test_full_function_deletion(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        _write(repo, "a.py", "")
        _commit_all(repo, "delete foo")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        record = records[0]
        assert record.function_name == "foo"
        assert record.deletion_kind == "FULL_FUNCTION"
        assert record.deleted_hunk == "def foo():\n    return 1"
        assert record.repo == _REPO
        assert record.file_path == "a.py"

    def test_partial_function_deletion(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def bar():\n    a = 1\n    return a\n")
        _commit_all(repo, "add bar")
        _write(repo, "a.py", "def bar():\n    return 1\n")
        _commit_all(repo, "simplify bar")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].function_name == "bar"
        assert records[0].deletion_kind == "PARTIAL"
        assert records[0].deleted_hunk == "    a = 1\n    return a"

    def test_deletion_outside_function_produces_no_record(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "import os\n\n\ndef foo():\n    return 1\n")
        _commit_all(repo, "add foo with import")
        _write(repo, "a.py", "\n\ndef foo():\n    return 1\n")
        _commit_all(repo, "drop unused import")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert records == []

    def test_function_spanning_multiple_hunks_is_one_record(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "a.py",
            "def baz():\n    a = 1\n    b = 2\n    c = 3\n    return b\n",
        )
        _commit_all(repo, "add baz")
        # a=1 과 c=3 을 지우고(서로 붙어있지 않음, 사이에 b=2 가 남음) b=2/return b는 남긴다
        # -> 최소 2개의 별개 diff hunk가 생기지만 baz record는 1개여야 한다.
        _write(repo, "a.py", "def baz():\n    b = 2\n    return b\n")
        _commit_all(repo, "trim baz")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].function_name == "baz"
        assert records[0].deletion_kind == "PARTIAL"
        assert records[0].deleted_hunk == "    a = 1\n    c = 3"

    def test_multiple_functions_deleted_in_one_commit(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "a.py",
            "def foo():\n    return 1\n\n\n"
            "def bar():\n    return 2\n\n\n"
            "def baz():\n    return 3\n",
        )
        _commit_all(repo, "add three functions")
        _write(repo, "a.py", "")
        _commit_all(repo, "delete all three")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert {r.function_name for r in records} == {"foo", "bar", "baz"}
        assert len(records) == 3
        assert all(r.deletion_kind == "FULL_FUNCTION" for r in records)

    def test_same_name_functions_in_different_classes_are_not_merged(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "a.py",
            "class ClassA:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "\n\n"
            "class ClassB:\n"
            "    def __init__(self):\n"
            "        self.y = 2\n",
        )
        _commit_all(repo, "add two classes")
        _write(repo, "a.py", "class ClassA:\n    pass\n\n\nclass ClassB:\n    pass\n")
        _commit_all(repo, "delete both __init__")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 2  # 병합되지 않고 2개 그대로
        assert all(r.function_name == "__init__" for r in records)
        start_lines = {r.start_line for r in records}
        assert len(start_lines) == 2  # 서로 다른 인스턴스 (다른 start_line)

    def test_nested_inner_fully_deleted_outer_is_partial(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "a.py",
            "def outer():\n    def inner():\n        return 1\n    return inner() + 1\n",
        )
        _commit_all(repo, "add outer+inner")
        _write(repo, "a.py", "def outer():\n    return inner() + 1\n")
        _commit_all(repo, "delete inner only")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))
        by_name = {r.function_name: r for r in records}

        assert set(by_name) == {"outer", "inner"}
        assert by_name["inner"].deletion_kind == "FULL_FUNCTION"
        assert by_name["outer"].deletion_kind == "PARTIAL"

    def test_outer_and_inner_both_fully_deleted(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "a.py",
            "def outer():\n    def inner():\n        return 1\n    return inner() + 1\n",
        )
        _commit_all(repo, "add outer+inner")
        _write(repo, "a.py", "")
        _commit_all(repo, "delete outer entirely")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))
        by_name = {r.function_name: r for r in records}

        assert set(by_name) == {"outer", "inner"}
        assert by_name["inner"].deletion_kind == "FULL_FUNCTION"
        assert by_name["outer"].deletion_kind == "FULL_FUNCTION"

    def test_added_hunk_same_file_is_shared_across_records_from_that_file(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
        _commit_all(repo, "add foo and bar")
        _write(repo, "a.py", "def baz():\n    return 3\n")
        _commit_all(repo, "replace foo+bar with baz")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 2
        added = {r.added_hunk_same_file for r in records}
        assert added == {"def baz():\n    return 3"}

    def test_added_hunk_same_file_is_empty_when_nothing_added(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        _write(repo, "a.py", "")
        _commit_all(repo, "delete foo, add nothing")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].added_hunk_same_file == ""

    def test_non_py_files_are_ignored(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _write(repo, "notes.txt", "line one\nline two\n")
        _commit_all(repo, "add py and txt")
        _write(repo, "a.py", "")
        _write(repo, "notes.txt", "line one changed\nline two\n")
        _commit_all(repo, "delete foo, edit txt")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].file_path == "a.py"

    def test_file_path_is_repo_root_relative(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "pkg/sub/mod.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add nested module")
        _write(repo, "pkg/sub/mod.py", "")
        _commit_all(repo, "delete foo")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].file_path == "pkg/sub/mod.py"

    def test_multiline_commit_message_is_preserved(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        _write(repo, "a.py", "")
        message = "delete foo\n\nDead code, unused since #12.\nRefs #5"
        _commit_all(repo, message)

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].commit_message == message


# --------------------------------------------------------------------------------------
# extract_repo: 저장소 하나를 순차로 끝까지 훑는다
# --------------------------------------------------------------------------------------


@requires_git
def test_extract_repo_processes_all_walk_commits_sequentially(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
    _commit_all(repo, "add foo and bar")
    _write(repo, "a.py", "def bar():\n    return 2\n")
    _commit_all(repo, "delete foo")  # 커밋 1: foo만 지움
    _write(repo, "a.py", "")
    _commit_all(repo, "delete bar")  # 커밋 2: bar만 지움

    all_commits = walk_commits(repo, "main")
    assert len(all_commits) == 2  # 둘 다 root/merge가 아니므로 walk_commits에 남는다

    records = extract_module.extract_repo(repo, _REPO, "main")

    # 커밋별로 개별 extract_deletions를 호출한 것과 결과·순서가 같아야 한다.
    expected = [
        record
        for commit in all_commits
        for record in extract_module.extract_deletions(repo, _REPO, commit)
    ]
    assert records == expected
    assert [r.function_name for r in records] == ["foo", "bar"]
    assert [r.commit_sha for r in records] == [c.commit_sha for c in all_commits]


def test_extract_repo_does_not_guess_ref(tmp_path: Path):
    """`extract_repo`가 `ref`를 요구하는지(기본값이 없는지) — clone.py/walk.py와 같은 원칙."""
    with pytest.raises(TypeError):
        extract_module.extract_repo(tmp_path, _REPO)  # type: ignore[call-arg]


# --------------------------------------------------------------------------------------
# DeletedFunction / to_json_dict: 내부 필드명 vs JSONL 바깥 계약
# --------------------------------------------------------------------------------------


def test_deleted_function_field_is_deletion_kind_not_deletion_type():
    field_names = {f.name for f in dataclasses.fields(extract_module.DeletedFunction)}
    assert "deletion_kind" in field_names
    assert "deletion_type" not in field_names


def test_to_json_dict_maps_deleted_hunk_to_deleted_body():
    record = _sample_records(1)[0]

    payload = extract_module.to_json_dict(record)

    assert payload["deleted_body"] == record.deleted_hunk
    assert "deleted_hunk" not in payload


def test_to_json_dict_has_no_deletion_type_key():
    record = _sample_records(1)[0]

    payload = extract_module.to_json_dict(record)

    assert payload["deletion_kind"] == "FULL_FUNCTION"
    assert "deletion_type" not in payload


def test_to_json_dict_has_only_currently_known_fields():
    """§4.4에 있지만 이 단계에서 알 수 없는 필드(repo_license, context, reason, embedding
    등)를 None/빈 값으로 채워 넣지 않는다 — 아예 키 자체가 없어야 한다."""
    record = _sample_records(1)[0]

    payload = extract_module.to_json_dict(record)

    assert set(payload) == {
        "repo",
        "commit_sha",
        "parent_sha",
        "file_path",
        "function_name",
        "start_line",
        "end_line",
        "deletion_kind",
        "deleted_body",
        "added_hunk_same_file",
        "author_date",
        "commit_message",
    }


# --------------------------------------------------------------------------------------
# write_jsonl
# --------------------------------------------------------------------------------------


def test_write_jsonl_writes_one_object_per_line(tmp_path: Path):
    out = tmp_path / "out.jsonl"
    extract_module.write_jsonl(_sample_records(3), out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        assert isinstance(json.loads(line), dict)


def test_write_jsonl_is_not_a_json_array(tmp_path: Path):
    out = tmp_path / "out.jsonl"
    extract_module.write_jsonl(_sample_records(3), out)

    content = out.read_text(encoding="utf-8")
    assert not content.lstrip().startswith("[")
    with pytest.raises(json.JSONDecodeError):
        json.loads(content)  # 파일 전체는 유효한 JSON 하나가 아니다(JSONL이므로)


def test_write_jsonl_preserves_record_order(tmp_path: Path):
    out = tmp_path / "out.jsonl"
    records = _sample_records(5)

    extract_module.write_jsonl(records, out)

    lines = out.read_text(encoding="utf-8").splitlines()
    commit_shas = [json.loads(line)["commit_sha"] for line in lines]
    assert commit_shas == [r.commit_sha for r in records]


def test_write_jsonl_creates_missing_parent_directory(tmp_path: Path):
    out = tmp_path / "does" / "not" / "exist" / "out.jsonl"
    assert not out.parent.exists()

    extract_module.write_jsonl(_sample_records(1), out)

    assert out.exists()


def test_write_jsonl_round_trips_korean_commit_message_as_utf8(tmp_path: Path):
    out = tmp_path / "out.jsonl"
    korean_message = "죽은 코드 삭제\n\n더 이상 쓰이지 않아서 지움. Refs #5"
    record = dataclasses.replace(_sample_records(1)[0], commit_message=korean_message)

    extract_module.write_jsonl([record], out)

    raw_bytes = out.read_bytes()
    # ensure_ascii=False 확인: "\uXXXX" 이스케이프가 아니라 UTF-8 원문 그대로 저장돼야 한다
    assert "죽은 코드".encode() in raw_bytes
    assert b"\\u" not in raw_bytes

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["commit_message"] == korean_message


# --------------------------------------------------------------------------------------
# context.py(희수 담당, 읽기 전용 재사용)와의 호환성
# --------------------------------------------------------------------------------------


def test_context_parse_targets_reads_generated_jsonl():
    """write_jsonl()의 산출물을 context.py의 공개 API(parse_targets)가 그대로 읽는지
    확인한다 — context.py는 여기서 import만 하고 수정하지 않는다."""
    record = _sample_records(1)[0]
    payload = extract_module.to_json_dict(record)
    line = json.dumps(payload, ensure_ascii=False) + "\n"

    targets = parse_targets([line])

    assert len(targets) == 1
    target = targets[0]
    assert target.repo == record.repo
    assert target.commit_sha == record.commit_sha
    assert target.file_path == record.file_path
    assert target.commit_message == record.commit_message
    # 원본 레코드가 그대로 보존돼 있어야 한다(§4.4 이름으로 바뀐 deleted_body 포함).
    assert target.record["deleted_body"] == record.deleted_hunk
    assert "deleted_hunk" not in target.record
    assert "deletion_type" not in target.record


@requires_git
def test_context_parse_targets_reads_write_jsonl_output_file(tmp_path: Path):
    """실제 `write_jsonl()`이 쓴 파일을 디스크에서 읽어 `parse_targets`에 그대로 넘긴다."""
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.py", "def foo():\n    return 1\n")
    _commit_all(repo, "add foo")
    _write(repo, "a.py", "")
    _commit_all(repo, "delete foo")

    records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))
    out = tmp_path / "out.jsonl"
    extract_module.write_jsonl(records, out)

    targets = parse_targets(out.read_text(encoding="utf-8").splitlines())

    assert len(targets) == 1
    assert targets[0].repo == _REPO
    assert targets[0].record["function_name"] == "foo"
