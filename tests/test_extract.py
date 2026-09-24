"""extract.py 테스트 (이슈 #5).

`tmp_path`에 실제 git 저장소를 만들어(subprocess) 커밋 두 개(부모/자식)를 쌓고,
`walk_commits`(walk.py, 기존 구현 그대로 재사용)로 `CommitPair`를 얻어
`extract_deletions`에 넘긴다. 네트워크 없음.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
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
            added_hunks_same_file=(),
            author_date="2026-01-01T00:00:00+00:00",
            commit_message=f"delete fn{i}",
            id=extract_module.make_record_id(_REPO, f"c{i}", "a.py", f"fn{i}", 1),
            function_signature=f"def fn{i}():",
            is_test_code=False,
            source_url=extract_module._source_url(_REPO, f"c{i}"),
        )
        for i in range(count)
    ]


# subprocess.run(["git", "--version"]) 대신 shutil.which를 쓴다 — git 실행 파일 자체가
# 없으면 subprocess.run이 FileNotFoundError를 던져서 skip 마커가 적용되기 전에 테스트
# 수집(collection) 자체가 실패할 수 있다 (CodeRabbit 리뷰).
_GIT_MISSING = shutil.which("git") is None
requires_git = pytest.mark.skipif(_GIT_MISSING, reason="git CLI가 필요하다")


# --------------------------------------------------------------------------------------
# parse_file_diffs: 순수 함수, git 없이 캔 diff 문자열로 검증
# --------------------------------------------------------------------------------------


def test_parse_file_diffs_splits_deleted_and_added_lines():
    """삭제 줄은 옛 줄 번호로, 추가 줄은 헤더 좌표를 가진 AddedHunk 하나로 나뉜다."""
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
    assert files["a.py"].added_hunks == [extract_module.AddedHunk(4, 2, 3, 1, "    z = 3")]


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


def test_parse_file_diffs_strips_trailing_tab_from_path_containing_space():
    """경로에 공백이 있으면 git이 `--- `/`+++ ` 헤더 줄 끝에 탭 하나를 덧붙인다(고전
    unified diff의 `path\\tdate` 필드 구분자 관례 — 실제 git으로 재현해서 확인함:
    `eval/gemini-2.0-flash copy.py` 같은 경로에서 헤더가 `--- a/eval/gemini-2.0-flash
    copy.py\\t`로 나온다). 그 탭 하나만 제거해야 한다 — 파일명 "안"의 공백은 그대로
    남아야 한다. 수정 전에는 이 탭이 file_path에 그대로 남아 이후 `git show
    <parent_sha>:<file_path>`가 "path does not exist"로 실패했다(실제 운영 재현,
    browser-use 저장소에서 저장소 전체 extraction 실패로 이어짐)."""
    diff = (
        "diff --git a/eval/gemini-2.0-flash copy.py b/eval/gemini-2.0-flash copy.py\n"
        "index c2119dc..e69de29 100644\n"
        "--- a/eval/gemini-2.0-flash copy.py\t\n"
        "+++ b/eval/gemini-2.0-flash copy.py\t\n"
        "@@ -1,2 +0,0 @@\n"
        "-def foo():\n"
        "-    return 1\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert set(files) == {"eval/gemini-2.0-flash copy.py"}  # 탭 없음, 내부 공백은 보존
    assert files["eval/gemini-2.0-flash copy.py"].deleted_lines == {
        1: "def foo():",
        2: "    return 1",
    }


def test_parse_file_diffs_plain_path_without_space_is_unaffected():
    """공백이 없는 평범한 경로는 애초에 탭이 안 붙는다(재현 확인) — 이번 수정으로
    동작이 바뀌면 안 된다."""
    diff = (
        "diff --git a/src/example.py b/src/example.py\n"
        "index aaa..bbb 100644\n"
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -1 +0,0 @@\n"
        "-x = 1\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert set(files) == {"src/example.py"}


def test_parse_file_diffs_hunk_content_starting_with_dashes_is_not_mistaken_for_header():
    """헝크 안에서 지워지는/추가되는 소스 줄이 "-- x --"/"++ x ++"처럼 시작하면, diff
    접두사가 붙어 "--- x --"/"+++ x ++"가 된다 — 파일 헤더 줄과 구별이 안 돼 이 파일의
    삭제 레코드가 통째로 사라지던 문제였다(CodeRabbit 리뷰). 수정 전에는 이 입력에
    대해 parse_file_diffs()가 {}를 반환했다(재현 확인됨)."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "index e1e92b7..96c22d6 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -3 +3 @@ def foo():\n"
        "--- section --\n"
        "+++ replacement ++\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert set(files) == {"a.py"}
    assert files["a.py"].deleted_lines == {3: "-- section --"}
    assert files["a.py"].added_hunks == [extract_module.AddedHunk(3, 1, 3, 1, "++ replacement ++")]


# --------------------------------------------------------------------------------------
# parse_file_diffs: added_hunks (Issue #102). 헝크 헤더 좌표 4개 + added_body를 헝크 단위로
# 보존한다. new_count == 0(순수 삭제) 헝크는 넣지 않는다.
# --------------------------------------------------------------------------------------

_A = extract_module.AddedHunk


def test_parse_file_diffs_keeps_each_added_hunk_in_diff_order():
    """한 파일의 여러 헝크가 합쳐지지 않고 헝크마다 AddedHunk 하나로 diff 순서대로 남는다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -2,2 +2,3 @@ def foo():\n"
        "-    x = 1\n"
        "-    y = 2\n"
        "+    x = 10\n"
        "+    y = 20\n"
        "+    z = 30\n"
        "@@ -9,0 +11,2 @@ def bar():\n"
        "+    w = 4\n"
        "+    return w\n"
        "@@ -20,3 +23,1 @@ def baz():\n"
        "-    a = 1\n"
        "-    b = 2\n"
        "-    c = 3\n"
        "+    return 0\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert files["a.py"].added_hunks == [
        _A(2, 2, 2, 3, "    x = 10\n    y = 20\n    z = 30"),
        _A(9, 0, 11, 2, "    w = 4\n    return w"),
        _A(20, 3, 23, 1, "    return 0"),
    ]


def test_parse_file_diffs_omitted_hunk_counts_default_to_one():
    """`@@ -3 +3 @@`처럼 count가 없으면 unified diff 관례대로 1이다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -3 +3 @@ def foo():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert files["a.py"].added_hunks == [_A(3, 1, 3, 1, "    return 2")]


def test_parse_file_diffs_excludes_pure_deletion_hunks():
    """new_count == 0 헝크는 추가 줄이 없으므로 added_hunks에 없다. 삭제 줄 자체는
    deleted_lines에 그대로 남는다. count 생략(`-10`) + 0(`+9,0`) 조합도 함께 본다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -2,2 +1,0 @@\n"
        "-def foo():\n"
        "-    return 1\n"
        "@@ -10 +9,0 @@\n"
        "-x = 1\n"
        "@@ -12 +10 @@\n"
        "-y = 1\n"
        "+y = 2\n"
    )
    files = extract_module.parse_file_diffs(diff)

    deleted = files["a.py"].deleted_lines
    assert deleted == {2: "def foo():", 3: "    return 1", 10: "x = 1", 12: "y = 1"}
    assert files["a.py"].added_hunks == [_A(12, 1, 10, 1, "y = 2")]


def test_parse_file_diffs_includes_pure_insertion_hunk():
    """`-5,0 +6,2`(순수 삽입)는 포함한다 — 삭제는 같은 파일의 다른 헝크에 있다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -2 +1,0 @@\n"
        "-import os\n"
        "@@ -5,0 +6,2 @@\n"
        "+def new():\n"
        "+    return 1\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert files["a.py"].added_hunks == [_A(5, 0, 6, 2, "def new():\n    return 1")]


def test_parse_file_diffs_includes_blank_line_hunk_with_empty_body():
    """빈 줄 하나만 추가한 헝크는 added_body == ""이지만 new_count == 1이라 포함한다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -4 +4 @@\n"
        "-    return 1\n"
        "+\n"
        "@@ -8,0 +9,2 @@\n"
        "+\n"
        "+\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert files["a.py"].added_hunks == [_A(4, 1, 4, 1, ""), _A(8, 0, 9, 2, "\n")]


def test_parse_file_diffs_added_body_excludes_no_newline_marker():
    """`\\ No newline at end of file` 표식 줄은 추가 코드가 아니므로 added_body에 섞이지 않는다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -3 +3,2 @@\n"
        "-    return 1\n"
        "\\ No newline at end of file\n"
        "+    return 2\n"
        "+# end\n"
        "\\ No newline at end of file\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert files["a.py"].added_hunks == [_A(3, 1, 3, 2, "    return 2\n# end")]


def test_parse_file_diffs_added_line_starting_with_plus_plus_is_body_not_header():
    """추가 코드 줄이 "++ ..."로 시작하면 diff에선 "+++ ..."가 된다 — 파일 헤더나 다음
    헝크로 오인되지 않고 added_body에 들어가야 한다."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -2 +2,3 @@\n"
        "-x = 1\n"
        "+++ counter ++\n"
        "+--- divider --\n"
        "+@@ not a header @@\n"
    )
    files = extract_module.parse_file_diffs(diff)

    assert set(files) == {"a.py"}
    assert files["a.py"].added_hunks == [
        _A(2, 1, 2, 3, "++ counter ++\n--- divider --\n@@ not a header @@")
    ]


def _joined_added_bodies(hunks: Iterable[extract_module.AddedHunk]) -> str:
    """새 구조에서 옛 `added_hunk_same_file` 문자열을 복원한다."""
    return "\n".join(hunk.added_body for hunk in hunks)


# 실제 추출 데이터(#5, requests 7,762건, 2026-09-14 로컬 실행)에서 관찰한 added 쪽 형태를
# 축소한 fixture. 파일 자체에는 의존하지 않는다. 관찰된 형태: 추가 줄 없음(2,513건),
# 탭 들여쓰기(103), 앞쪽 빈 줄(223), 연속 빈 줄 3개 이상(995), 줄 끝 공백(466),
# 비-ASCII(497), 공백·탭만 있는 줄로만 된 값(3). 여기에 순수 삭제·순수 삽입 헝크 섞임과
# no-newline 마커를 더했다. 각 fixture의 기대값은 Issue #102 이전 `added_hunk_same_file`이
# 파일별로 내던 문자열(삭제 줄이 있는 옛 경로만 키로 가짐)을 직접 고정한 것이다.
_LEGACY_FIXTURES = {
    "tabs_leading_blank_trailing_ws_multi_hunk": (
        "diff --git a/requests/core.py b/requests/core.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/requests/core.py\n"
        "+++ b/requests/core.py\n"
        "@@ -10,3 +10,3 @@ class Request(object):\n"
        "-\tdef _build_response(self, resp):\n"
        "-\t\tself.response.status_code = resp.code\n"
        "-\t\treturn None\n"
        "+\n"
        "+\tdef _build_response(self, resp):\n"
        '+\t\t"""Build internal Response object from given response."""\n'
        "@@ -20,0 +21,2 @@ class Request(object):\n"
        "+\t\tself.response.headers = resp.info().dict \n"
        "+\t\treturn self.response\n"
        "@@ -40,2 +40,0 @@ class Request(object):\n"
        "-\tdef unused(self):\n"
        "-\t\tpass\n",
        {
            "requests/core.py": (
                "\n"
                "\tdef _build_response(self, resp):\n"
                '\t\t"""Build internal Response object from given response."""\n'
                "\t\tself.response.headers = resp.info().dict \n"
                "\t\treturn self.response"
            )
        },
    ),
    "non_ascii_triple_blank_no_newline": (
        "diff --git a/test_requests.py b/test_requests.py\n"
        "--- a/test_requests.py\n"
        "+++ b/test_requests.py\n"
        "@@ -1,4 +1,8 @@\n"
        "-def httpbin(*suffix):\n"
        "-    return 'http://httpbin.org/' + '/'.join(suffix)\n"
        "-\n"
        "-\n"
        "+\n"
        "+HTTPBIN_URL = 'http://httpbin.org/'\n"
        "+\n"
        "+\n"
        "+\n"
        "+def httpbin(*suffix):\n"
        '+    """Returns url for HTTPBIN resource — 한글 설명."""\n'
        "+    return HTTPBIN_URL + '/'.join(suffix)\n"
        "\\ No newline at end of file\n",
        {
            "test_requests.py": (
                "\n"
                "HTTPBIN_URL = 'http://httpbin.org/'\n"
                "\n"
                "\n"
                "\n"
                "def httpbin(*suffix):\n"
                '    """Returns url for HTTPBIN resource — 한글 설명."""\n'
                "    return HTTPBIN_URL + '/'.join(suffix)"
            )
        },
    ),
    "whitespace_only_and_blank_hunks": (
        "diff --git a/requests/models.py b/requests/models.py\n"
        "--- a/requests/models.py\n"
        "+++ b/requests/models.py\n"
        "@@ -5,2 +5 @@\n"
        "-\tdef a(self):\n"
        "-\t\tpass\n"
        "+\t\t\t\n"
        "@@ -9 +8 @@\n"
        "-\t\tx = 1\n"
        "+\t\t\n"
        "@@ -12,0 +12 @@\n"
        "+\n",
        {"requests/models.py": "\t\t\t\n\t\t\n"},
    ),
    "nothing_added": (
        "diff --git a/requests/api.py b/requests/api.py\n"
        "--- a/requests/api.py\n"
        "+++ b/requests/api.py\n"
        "@@ -3,2 +2,0 @@\n"
        "-def head(url):\n"
        "-    return None\n"
        "diff --git a/requests/old.py b/requests/old.py\n"
        "deleted file mode 100644\n"
        "--- a/requests/old.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def gone():\n"
        "-    pass\n",
        {"requests/api.py": "", "requests/old.py": ""},
    ),
}


@pytest.mark.parametrize(
    ("diff", "expected"), _LEGACY_FIXTURES.values(), ids=_LEGACY_FIXTURES.keys()
)
def test_added_hunks_reconstruct_legacy_added_hunk_same_file(diff: str, expected: dict[str, str]):
    """새 구조를 `"\\n".join(added_body ...)`로 이어붙이면 옛 `added_hunk_same_file` 값과
    정확히 같다 — 두 형태를 함께 저장하지 않아도 정보 손실이 없다(Issue #102)."""
    files = extract_module.parse_file_diffs(diff)

    assert set(files) == set(expected)
    for path, file_diff in files.items():
        assert _joined_added_bodies(file_diff.added_hunks) == expected[path]
        for hunk in file_diff.added_hunks:
            assert hunk.new_count == len(hunk.added_body.split("\n"))


# --------------------------------------------------------------------------------------
# _parse_added_line_ranges: 이동 탐지(Issue #52 B-1 수정)가 쓰는 added-side 실제 줄 범위.
# parse_file_diffs와 달리 새(자식) 경로를 보고, 순수 신규 파일 섹션도 버리지 않는다.
# --------------------------------------------------------------------------------------


def test_parse_added_line_ranges_includes_pure_new_file():
    """parse_file_diffs는 순수 신규 파일을 버리지만(위 skips_pure_new_file 테스트),
    _parse_added_line_ranges는 정확히 이 케이스(이동의 목적지 파일)를 잡아야 한다."""
    diff = "diff --git a/a.py b/a.py\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n"
    assert extract_module._parse_added_line_ranges(diff) == {"a.py": [(1, 1)]}


def test_parse_added_line_ranges_ignores_pure_deletion():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def foo():\n"
        "-    return 1\n"
    )
    assert extract_module._parse_added_line_ranges(diff) == {}


def test_parse_added_line_ranges_uses_new_path_not_old_path():
    """옛 경로와 새 경로가 다르면(--no-renames가 쪼갠 이동), 새 경로에 범위가 잡혀야 한다."""
    diff = (
        "diff --git a/old/a.py b/old/a.py\n--- a/old/a.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def foo():\n-    return 1\n"
        "diff --git a/new/a.py b/new/a.py\n--- /dev/null\n+++ b/new/a.py\n"
        "@@ -0,0 +1,2 @@\n+def foo():\n+    return 1\n"
    )
    assert extract_module._parse_added_line_ranges(diff) == {"new/a.py": [(1, 2)]}


def test_parse_added_line_ranges_covers_multiple_paths():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n-x = 1\n+x = 2\n+y = 3\n"
        "diff --git a/b.py b/b.py\n--- /dev/null\n+++ b/b.py\n@@ -0,0 +1 @@\n+z = 1\n"
    )
    assert extract_module._parse_added_line_ranges(diff) == {"a.py": [(1, 2)], "b.py": [(1, 1)]}


def test_parse_added_line_ranges_accumulates_multiple_hunks_in_one_file():
    """한 파일 안에 서로 떨어진 헝크가 여럿이면 범위도 여럿(합치지 않고 각자 보존)."""
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1,2 @@\n-x = 1\n+x = 2\n+y = 3\n"
        "@@ -10 +11,2 @@\n-p = 1\n+p = 2\n+q = 3\n"
    )
    assert extract_module._parse_added_line_ranges(diff) == {"a.py": [(1, 2), (11, 12)]}


def test_parse_added_line_ranges_returns_empty_for_no_additions():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +0,0 @@\n-x = 1\n"
    assert extract_module._parse_added_line_ranges(diff) == {}


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

    def test_full_function_deletion_in_file_path_containing_space(self, tmp_path: Path):
        """실제 운영 재현: 경로에 공백이 있으면 diff 헤더에 탭이 붙어(git 재현 확인),
        수정 전에는 parent source 조회(`git show <parent_sha>:<file_path>`)가
        "path does not exist"로 실패해 이 파일에서 아무 record도 못 뽑았다."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "eval/gemini-2.0-flash copy.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add file with space in name")
        _write(repo, "eval/gemini-2.0-flash copy.py", "")
        _commit_all(repo, "delete foo")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        record = records[0]
        assert record.function_name == "foo"
        assert record.deletion_kind == "FULL_FUNCTION"
        assert record.file_path == "eval/gemini-2.0-flash copy.py"  # 탭 없음, 공백 보존

    def test_full_function_deletion_populates_issue_75_fields(self, tmp_path: Path):
        """id/function_signature/is_test_code/source_url이 실제 추출 경로(git diff →
        PythonAdapter)를 통해서도 올바르게 채워지는지 확인한다 — 손으로 만든
        `DeletedFunction`이 아니라 `extract_deletions()`의 실제 산출물로 검증한다."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "tests/test_a.py", "def test_foo(x: int) -> None:\n    assert x\n")
        _commit_all(repo, "add test_foo")
        _write(repo, "tests/test_a.py", "")
        commit_sha = _commit_all(repo, "delete test_foo")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        record = records[0]
        assert record.id == extract_module.make_record_id(
            _REPO, commit_sha, "tests/test_a.py", "test_foo", record.start_line
        )
        assert record.function_signature == "def test_foo(x: int) -> None:"
        assert record.is_test_code is True
        assert record.source_url == f"https://github.com/{_REPO}/commit/{commit_sha}"

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

    def test_added_hunks_same_file_is_shared_across_records_from_that_file(self, tmp_path: Path):
        """같은 파일에서 나온 레코드들은 그 파일의 added_hunks_same_file을 똑같이 공유한다."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
        _commit_all(repo, "add foo and bar")
        _write(repo, "a.py", "def baz():\n    return 3\n")
        _commit_all(repo, "replace foo+bar with baz")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 2
        added = {r.added_hunks_same_file for r in records}
        assert added == {(extract_module.AddedHunk(1, 6, 1, 2, "def baz():\n    return 3"),)}

    def test_added_hunks_same_file_is_empty_when_nothing_added(self, tmp_path: Path):
        """추가 줄이 전혀 없는 커밋이면 added_hunks_same_file은 빈 tuple이다."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        _write(repo, "a.py", "")
        _commit_all(repo, "delete foo, add nothing")

        records = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))

        assert len(records) == 1
        assert records[0].added_hunks_same_file == ()

    def test_added_hunks_same_file_matches_real_git_coordinates(self, tmp_path: Path):
        """실제 git diff 헤더 좌표가 그대로 남고, 옛 문자열 값도 그대로 복원된다(Issue #102).
        파일 앞쪽 순수 삽입(1줄)이 뒤쪽 헝크의 new_start를 1 밀어낸다."""
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "a.py",
            "import os\n\n\ndef foo():\n    return 1\n\n\ndef bar():\n    return 2\n",
        )
        _commit_all(repo, "add foo and bar")
        _write(
            repo,
            "a.py",
            "import os\nimport sys\n\n\ndef foo():\n    return 1\n\n\ndef baz():\n    return 3\n",
        )
        _commit_all(repo, "add import, replace bar with baz")
        commit = _last_commit_pair(repo)

        records = extract_module.extract_deletions(repo, _REPO, commit)

        assert [r.function_name for r in records] == ["bar"]
        assert records[0].added_hunks_same_file == (
            extract_module.AddedHunk(1, 0, 2, 1, "import sys"),
            extract_module.AddedHunk(8, 2, 9, 2, "def baz():\n    return 3"),
        )
        # 옛 added_hunk_same_file 값: a.py의 추가 줄을 diff 순서대로 이어붙인 문자열
        assert _joined_added_bodies(records[0].added_hunks_same_file) == (
            "import sys\ndef baz():\n    return 3"
        )

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
# collect_added_functions: 이동 탐지(Issue #52)가 쓰는 added-side 함수 후보
# --------------------------------------------------------------------------------------


@requires_git
class TestCollectAddedFunctions:
    def test_captures_functions_from_pure_new_file(self, tmp_path: Path):
        """--no-renames diff가 이동을 옛 경로 삭제 + 새 경로 신규 파일로 쪼개도(parse_file_diffs
        가 놓치는 바로 그 섹션), 목적지 경로의 함수를 확보해야 한다."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "old/a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        (repo / "old" / "a.py").unlink()
        _write(repo, "new/a.py", "def foo():\n    return 2\n\n\ndef bar():\n    return 3\n")
        _commit_all(repo, "move old/a.py to new/a.py, add bar")

        added = extract_module.collect_added_functions(repo, _last_commit_pair(repo))

        assert set(added) == {"new/a.py"}
        assert {f.name for f in added["new/a.py"]} == {"foo", "bar"}

    def test_includes_same_path_new_function_but_not_untouched_existing_one(self, tmp_path: Path):
        """같은 경로에 새로 추가된 함수는 후보에 넣는다 — "다른 경로"만 남기는 건
        filter.py(호출자) 몫이다. 하지만 그 파일에 원래 있던, 이번 커밋에서 전혀 손
        안 댄 함수(`foo`, 텍스트가 부모/자식에서 완전히 동일)는 후보가 아니어야 한다
        (Issue #52 B-1 수정 — added line과 안 겹치는 함수는 후보에서 뺀다)."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
        _commit_all(repo, "add bar to same file")

        added = extract_module.collect_added_functions(repo, _last_commit_pair(repo))

        assert set(added) == {"a.py"}
        assert {f.name for f in added["a.py"]} == {"bar"}  # foo는 손 안 댔으므로 제외

    def test_empty_when_commit_only_deletes(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "def foo():\n    return 1\n")
        _commit_all(repo, "add foo")
        _write(repo, "a.py", "")
        _commit_all(repo, "delete foo, add nothing")

        added = extract_module.collect_added_functions(repo, _last_commit_pair(repo))

        assert added == {}

    def test_b1_untouched_function_in_partially_edited_file_is_not_a_candidate(
        self, tmp_path: Path
    ):
        """Issue #52 B-1 회귀: x.py는 이번 커밋에서 `helper`만 고치고 `check`는 전혀 안
        건드린다. y.py의 `check`(x.py의 `check`와 완전히 동일한, 흔한 이름의 짧은 함수)는
        정말로 삭제될 뿐이다. `helper`가 고쳐졌다고 해서 x.py의 손 안 댄 `check`까지
        added candidate가 되면 안 된다 — 코드 리뷰 BLOCKER 재현 케이스."""
        repo = _init_repo(tmp_path / "repo")
        _write(
            repo,
            "x.py",
            "def check():\n    return None\n\n\ndef helper():\n    a = 1\n    b = 2\n"
            "    return a + b\n",
        )
        _write(repo, "y.py", "def check():\n    return None\n")
        _commit_all(repo, "add x.py and y.py")
        _write(
            repo,
            "x.py",
            "def check():\n    return None\n\n\ndef helper():  # x\n    a = 1  # x\n"
            "    b = 2  # x\n    c = 3  # x\n    return a + b + c  # x\n",
        )
        _write(repo, "y.py", "")
        _commit_all(repo, "edit helper in x.py (unrelated), delete check() from y.py")

        added = extract_module.collect_added_functions(repo, _last_commit_pair(repo))

        assert set(added) == {"x.py"}  # y.py는 삭제만 있어 후보가 없다
        assert {f.name for f in added["x.py"]} == {"helper"}  # check는 후보가 아니다


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

    # 커밋별로 개별 extract_deletions를 호출한 것과 결과·순서가 같아야 한다 — 이 두 커밋
    # 다 이동 후보(같은 커밋에 추가된 함수)가 전혀 없어서(둘 다 그냥 삭제만 함) 필터가
    # 아무것도 걸러내지 않는 케이스다. 이동이 실제로 걸러지는 케이스는 아래
    # test_extract_repo_excludes_moved_function_but_keeps_ordinary_deletion 참고.
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


@requires_git
def test_extract_repo_excludes_moved_function_but_keeps_ordinary_deletion(tmp_path: Path):
    """`extract_repo`가 `pipeline.filter.exclude_moved()`(Issue #52, NOISE_MOVE)를 실제로
    거쳐야 한다는 통합 회귀 테스트. `find_moved`/`exclude_moved`/`collect_added_functions`
    를 직접 부르는 기존 단위·컴포넌트 테스트는 전부 통과하면서도, `extract_repo`가 그
    함수들을 실제로 연결하지 않는 배선 버그(2라운드 전 상태 — `extract_deletions` 결과를
    필터 없이 그대로 누적)는 하나도 못 잡았다. 이 테스트는 `find_moved`를 모킹해 "호출은
    됐다"만 보는 게 아니라, 실제 작은 git 저장소를 `extract_repo`로 끝까지 돌려 최종
    결과에서 이동한 함수가 정말 사라지는지 본다.

    같은 커밋에서 두 가지가 동시에 일어난다:
    - `old/pkg/a.py`의 `moved_func`가 `new/pkg/a.py`로 그대로 이동(순수 신규 파일
      이동, `--no-renames`가 옛 경로 삭제 + 새 경로 신규 파일로 쪼개는 케이스) — 이동
      이므로 최종 결과에서 빠져야 한다.
    - `c.py`의 `real_delete`는 대체 없이 그냥 삭제된다 — 이동이 아니므로 남아야 한다.
    """
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "old/pkg/a.py", "def moved_func():\n    return 1\n")
    _write(repo, "c.py", "def real_delete():\n    return 2\n")
    _commit_all(repo, "add moved_func and real_delete")
    (repo / "old" / "pkg" / "a.py").unlink()
    _write(repo, "new/pkg/a.py", "def moved_func():\n    return 1\n")
    _write(repo, "c.py", "")
    _commit_all(repo, "move a.py to new/pkg, delete real_delete")

    records = extract_module.extract_repo(repo, _REPO, "main")

    assert [r.function_name for r in records] == ["real_delete"]  # moved_func는 제외됐다
    assert records[0].file_path == "c.py"


@requires_git
def test_extract_repo_with_excluded_preserves_moved_records_across_commits(tmp_path: Path):
    """Issue #97 통합: 여러 커밋에서 이동은 excluded(NOISE_MOVE + 목적지 증거)로, 일반
    삭제는 kept로 누적된다. 누락·중복이 없고, kept는 기존 `extract_repo()` 결과와 같다.

    - 커밋 1: `old/a.py`의 `moved_one` → `new/a.py`(순수 신규 파일), `c.py`의 `gone_one` 삭제
    - 커밋 2: `lib/b.py`의 `moved_two` → `lib/d.py`(순수 신규 파일), `c.py`의 `gone_two` 삭제
    """
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "old/a.py", "def moved_one():\n    return 1\n")
    _write(repo, "lib/b.py", "def moved_two(x):\n    return x * 2\n")
    _write(repo, "c.py", "def gone_one():\n    return 'a'\n\n\ndef gone_two():\n    return 'b'\n")
    _commit_all(repo, "add functions")

    (repo / "old" / "a.py").unlink()
    _write(repo, "new/a.py", "def moved_one():\n    return 1\n")
    _write(repo, "c.py", "def gone_two():\n    return 'b'\n")
    _commit_all(repo, "move moved_one, delete gone_one")

    (repo / "lib" / "b.py").unlink()
    _write(repo, "lib/d.py", "def moved_two(x):\n    return x * 2\n")
    _write(repo, "c.py", "")
    _commit_all(repo, "move moved_two, delete gone_two")

    commits = walk_commits(repo, "main")
    kept, excluded = extract_module.extract_repo_with_excluded(repo, _REPO, "main")

    assert [r.function_name for r in kept] == ["gone_one", "gone_two"]
    assert [e.record.function_name for e in excluded] == ["moved_one", "moved_two"]
    assert [e.record.commit_sha for e in excluded] == [c.commit_sha for c in commits]
    assert {e.filter_status for e in excluded} == {"NOISE_MOVE"}
    assert [e.filter_evidence["file_path"] for e in excluded] == ["new/a.py", "lib/d.py"]
    assert [e.filter_evidence["function_name"] for e in excluded] == ["moved_one", "moved_two"]

    # 누락·중복 없음: kept + excluded = 필터 전 전체 추출
    all_extracted = [
        record
        for commit in commits
        for record in extract_module.extract_deletions(repo, _REPO, commit)
    ]
    ids = [r.id for r in kept] + [e.record.id for e in excluded]
    assert sorted(ids) == sorted(r.id for r in all_extracted)
    assert len(set(ids)) == len(ids)

    # 기존 kept-only API는 그대로
    assert extract_module.extract_repo(repo, _REPO, "main") == kept


@requires_git
def test_extract_repo_with_excluded_applies_noise_trivial_with_noise_move(tmp_path: Path):
    """Issue #63 통합: 한 커밋에서 NOISE_TRIVIAL(4줄 PARTIAL)·NOISE_MOVE(이동한 FULL)·유지
    (5줄 PARTIAL, 일반 FULL 삭제)가 함께 나온다. 누락·중복이 없고, 제외 레코드는 사유가
    섞여도 추출 순서(diff 파일 순서: b.py → c.py → old/a.py → q.py)를 따른다. NOISE_TRIVIAL
    레코드는 #102 `added_hunks_same_file`을 그대로 들고 excluded 행에 남는다."""
    repo = _init_repo(tmp_path / "repo")
    _write(
        repo,
        "b.py",
        "def shrink():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    return 0\n",
    )
    _write(repo, "c.py", "def gone():\n    return 2\n")
    _write(repo, "old/a.py", "def moved_func():\n    return 1\n")
    _write(
        repo,
        "q.py",
        "def keep():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    return 0\n",
    )
    _commit_all(repo, "add functions")
    _write(repo, "b.py", "def shrink():\n    return 0\n\n\ndef extra():\n    return [1]\n")
    _write(repo, "c.py", "")
    (repo / "old" / "a.py").unlink()
    _write(repo, "new/a.py", "def moved_func():\n    return 1\n")
    _write(repo, "q.py", "def keep():\n    return 0\n")
    _commit_all(repo, "shrink, delete gone, move moved_func, trim keep")

    extracted = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))
    assert [(r.function_name, r.deletion_kind) for r in extracted] == [
        ("shrink", "PARTIAL"),
        ("gone", "FULL_FUNCTION"),
        ("moved_func", "FULL_FUNCTION"),
        ("keep", "PARTIAL"),
    ]
    assert [len(r.deleted_hunk.splitlines()) for r in extracted] == [4, 2, 2, 5]

    kept, excluded = extract_module.extract_repo_with_excluded(repo, _REPO, "main")

    assert [r.function_name for r in kept] == ["gone", "keep"]
    # 두 필터를 차례로 적용해도 제외 레코드는 추출 순서다 (TRIVIAL이 MOVE보다 앞)
    assert [(e.record.function_name, e.filter_status) for e in excluded] == [
        ("shrink", "NOISE_TRIVIAL"),
        ("moved_func", "NOISE_MOVE"),
    ]
    trivial, moved = excluded
    assert trivial.filter_evidence == {"line_count": 4}
    assert trivial.filter_rule_version == "v0.5"
    assert moved.filter_evidence["file_path"] == "new/a.py"
    assert trivial.record == extracted[0]  # 원본 레코드 그대로

    # 누락·중복 없음: kept + excluded = 필터 전 전체 추출
    ids = [r.id for r in kept] + [e.record.id for e in excluded]
    assert sorted(ids) == sorted(r.id for r in extracted)
    assert len(set(ids)) == len(ids)

    # #102: NOISE_TRIVIAL excluded 행에도 구조화된 added_hunks_same_file이 그대로 남는다
    assert trivial.record.added_hunks_same_file  # b.py에는 extra()가 추가됐다
    row = extract_module.excluded_to_json_dict(trivial)
    base = extract_module.to_json_dict(extracted[0])
    assert row["added_hunks_same_file"] == base["added_hunks_same_file"]
    assert {key: row[key] for key in base} == base

    # 기존 kept-only API는 같은 kept를 돌려준다
    assert extract_module.extract_repo(repo, _REPO, "main") == kept


def test_extract_repo_with_excluded_does_not_guess_ref(tmp_path: Path):
    """extract_repo_with_excluded는 ref를 추측하지 않으므로 ref 없이 호출하면 TypeError가 난다."""
    with pytest.raises(TypeError):
        extract_module.extract_repo_with_excluded(tmp_path, _REPO)  # type: ignore[call-arg]


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
    등)를 None/빈 값으로 채워 넣지 않는다 — 아예 키 자체가 없어야 한다.

    추출 JSONL에는 `filter_status`·`filter_rule_version`·`filter_evidence`가 없다
    (`docs/filter_rules.md` "계약 분리"·"제외 레코드 보존" 절). 필터가 제외한 레코드는
    Issue #97의 별도 excluded JSONL(`write_excluded_jsonl`)에 이 필드들과 함께 보존하고,
    최종 `filter_status`는 후속 조립 단계가 두 파일을 합쳐 만든다. #63은 같은 구조에
    NOISE_TRIVIAL을 추가한다. 이 테스트는 추출 JSONL 쪽 경계를 고정한다 — filter 필드가
    없어야 한다.
    """
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
        "added_hunks_same_file",
        "author_date",
        "commit_message",
        "id",
        "function_signature",
        "is_test_code",
        "source_url",
    }
    assert "filter_status" not in payload
    assert "filter_rule_version" not in payload


_SAMPLE_HUNKS = (
    extract_module.AddedHunk(3, 2, 3, 1, "  x"),
    extract_module.AddedHunk(9, 0, 8, 2, "\n"),
)


def _record_with_hunks() -> extract_module.DeletedFunction:
    """직렬화 테스트용: 샘플 레코드에 `_SAMPLE_HUNKS` 두 개를 채운 것."""
    return dataclasses.replace(_sample_records(1)[0], added_hunks_same_file=_SAMPLE_HUNKS)


def test_to_json_dict_serializes_added_hunks_as_list_of_five_key_objects():
    """Issue #102: 새 키만 있고 옛 `added_hunk_same_file` 키는 없다. 원소는 정확히 5개
    필드를 가진 객체이며 diff 순서를 유지한다."""
    payload = extract_module.to_json_dict(_record_with_hunks())

    assert "added_hunk_same_file" not in payload
    assert payload["added_hunks_same_file"] == [
        {"old_start": 3, "old_count": 2, "new_start": 3, "new_count": 1, "added_body": "  x"},
        {"old_start": 9, "old_count": 0, "new_start": 8, "new_count": 2, "added_body": "\n"},
    ]
    for item in payload["added_hunks_same_file"]:
        assert list(item) == ["old_start", "old_count", "new_start", "new_count", "added_body"]
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_to_json_dict_added_hunks_empty_is_empty_list():
    """added_hunks_same_file이 비어 있으면 JSON에서 키가 빠지지 않고 빈 리스트로 나온다."""
    payload = extract_module.to_json_dict(_sample_records(1)[0])

    assert payload["added_hunks_same_file"] == []


def test_deleted_function_added_hunks_is_immutable_tuple():
    """frozen dataclass 안의 값이라 tuple로 둔다 — 레코드가 해시 가능하게 남는다."""
    record = _record_with_hunks()

    assert isinstance(record.added_hunks_same_file, tuple)
    hash(record)
    field_names = {f.name for f in dataclasses.fields(extract_module.DeletedFunction)}
    assert "added_hunks_same_file" in field_names
    assert "added_hunk_same_file" not in field_names


# --------------------------------------------------------------------------------------
# id / function_signature / is_test_code / source_url (Issue #75)
# --------------------------------------------------------------------------------------


def test_to_json_dict_id_reuses_make_record_id():
    """`id`는 `make_record_id()`가 만든 값 그대로다 — 새 규칙을 만들지 않는다."""
    record = _sample_records(1)[0]

    payload = extract_module.to_json_dict(record)

    expected = extract_module.make_record_id(
        record.repo, record.commit_sha, record.file_path, record.function_name, record.start_line
    )
    assert payload["id"] == expected


def test_to_json_dict_id_is_stable_across_reextraction():
    """같은 논리적 키(repo/commit/file/function/start_line)면 언제 다시 뽑아도 같은 id다
    — 라벨 재사용의 전제 조건(§4.4 `id` 주석)."""
    first = extract_module.make_record_id(_REPO, "c0", "a.py", "fn0", 1)
    second = extract_module.make_record_id(_REPO, "c0", "a.py", "fn0", 1)
    assert first == second


def test_to_json_dict_source_url_is_github_commit_link():
    """`https://github.com/{repo}/commit/{commit_sha}` — 실제 200건 라벨 데이터
    (`datasets/labels/pre200_records.jsonl`)·`tests/test_sampling.py`가 이미 가정하는
    기존 형식 그대로다."""
    record = _sample_records(1)[0]

    payload = extract_module.to_json_dict(record)

    assert payload["source_url"] == f"https://github.com/{record.repo}/commit/{record.commit_sha}"


def test_to_json_dict_function_signature_passes_through_parser_signature():
    """`function_signature`는 `PythonAdapter`가 이미 계산한 `Function.signature`를 그대로
    옮긴 값이다 — 새로 파싱하지 않는다."""
    record = _sample_records(1)[0]

    payload = extract_module.to_json_dict(record)

    assert payload["function_signature"] == record.function_signature


def test_is_test_code_true_for_top_level_tests_directory():
    """최상위 `tests/` 아래 파일은 테스트 코드로 판정한다."""
    assert extract_module._is_test_code("tests/test_parser.py") is True


def test_is_test_code_true_for_nested_tests_directory():
    """하위 경로의 `tests/` 디렉터리 아래 파일도 테스트 코드로 판정한다."""
    assert extract_module._is_test_code("pkg/tests/test_widget.py") is True


def test_is_test_code_false_for_non_test_path():
    """`tests` 구성요소가 없는 일반 경로는 테스트 코드가 아니다."""
    assert extract_module._is_test_code("pipeline/extract.py") is False


def test_is_test_code_false_for_test_prefixed_filename_without_tests_dir():
    """`test_*.py` 파일명만으로는 True가 되지 않는다 — 근거 없는 확장 금지(Issue #75)."""
    assert extract_module._is_test_code("pkg/test_helpers.py") is False


def test_is_test_code_false_for_conftest_without_tests_dir():
    """`conftest.py`만으로는 True가 되지 않는다 — 근거 없는 확장 금지(Issue #75)."""
    assert extract_module._is_test_code("conftest.py") is False


def test_is_test_code_false_for_path_component_containing_tests_as_substring():
    """`tests`를 포함하지만 정확히 일치하지 않는 디렉터리(`mytests`, `testsuite`)는
    대상이 아니다 — 경로 구성요소 정확히 일치만 본다."""
    assert extract_module._is_test_code("mytests/test_widget.py") is False
    assert extract_module._is_test_code("testsuite/foo.py") is False


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
# excluded JSONL (Issue #97): 추출 JSONL 키 전부 + filter_status·filter_rule_version·
# filter_evidence, flat. 추출 JSONL 쪽 계약(test_to_json_dict_has_only_currently_known_fields)
# 은 그대로다.
# --------------------------------------------------------------------------------------

_FILTER_KEYS = {"filter_status", "filter_rule_version", "filter_evidence"}


def _sample_excluded(count: int = 3) -> list[extract_module.ExcludedRecord]:
    return [
        extract_module.ExcludedRecord(
            record=record,
            filter_status="NOISE_MOVE",
            filter_rule_version="v0.5",
            filter_evidence={
                "file_path": f"new/{record.function_name}.py",
                "function_name": record.function_name,
                "start_line": 3,
                "end_line": 4,
                "similarity": 0.95,
            },
        )
        for record in _sample_records(count)
    ]


def test_excluded_to_json_dict_is_flat_extraction_row_plus_filter_metadata():
    """excluded 행은 추출 행 필드를 감싸지 않고 그대로 펼친 뒤 filter_* 3개 키만 더한다."""
    item = _sample_excluded(1)[0]

    payload = extract_module.excluded_to_json_dict(item)
    base = extract_module.to_json_dict(item.record)

    assert set(payload) == set(base) | _FILTER_KEYS
    assert len(payload) == 16 + 3
    assert {key: payload[key] for key in base} == base  # 원본 필드를 감싸지 않고 그대로
    assert "record" not in payload
    assert "reason" not in payload  # §4.4 reason(이유 분류)과 충돌하는 이름을 쓰지 않는다
    assert payload["filter_status"] == "NOISE_MOVE"
    assert payload["filter_rule_version"] == "v0.5"
    assert payload["filter_evidence"] == {
        "file_path": "new/fn0.py",
        "function_name": "fn0",
        "start_line": 3,
        "end_line": 4,
        "similarity": 0.95,
    }


def test_excluded_to_json_dict_carries_added_hunks_from_record():
    """Issue #102: excluded JSONL은 `to_json_dict(record)`를 그대로 펼치므로 새 필드가
    별도 로직 없이 같은 모양으로 들어가고, 옛 키는 없다."""
    item = dataclasses.replace(_sample_excluded(1)[0], record=_record_with_hunks())

    payload = extract_module.excluded_to_json_dict(item)

    base = extract_module.to_json_dict(item.record)
    assert payload["added_hunks_same_file"] == base["added_hunks_same_file"]
    assert len(payload["added_hunks_same_file"]) == 2
    assert "added_hunk_same_file" not in payload


def test_to_json_dict_still_has_no_filter_fields_for_excluded_record():
    """같은 레코드라도 추출 JSONL 직렬화에는 filter 필드가 들어가지 않는다."""
    item = _sample_excluded(1)[0]

    assert not _FILTER_KEYS & set(extract_module.to_json_dict(item.record))


def test_excluded_row_from_partition_moved_has_move_evidence():
    """filter.partition_moved가 만든 실제 ExcludedRecord를 직렬화해 NOISE_MOVE 증거 구조를
    고정한다."""
    from pipeline import filter as filter_module
    from pipeline.parsers.base import Function

    record = _sample_records(1)[0]  # a.py fn0, FULL_FUNCTION, 2줄
    destination = Function(
        name="fn0",
        start_line=10,
        end_line=11,
        body="def fn0():\n    return 0",
        signature="def fn0():",
    )

    _kept, excluded = filter_module.partition_moved([record], {"b.py": [destination]}, {})
    payload = extract_module.excluded_to_json_dict(excluded[0])

    assert payload["filter_status"] == "NOISE_MOVE"
    assert payload["filter_rule_version"] == filter_module.FILTER_RULE_VERSION
    assert payload["filter_evidence"] == {
        "file_path": "b.py",
        "function_name": "fn0",
        "start_line": 10,
        "end_line": 11,
        "similarity": 1.0,
    }
    assert payload["file_path"] == "a.py"  # 최상위는 삭제 쪽(원본) 값 그대로


def test_excluded_row_from_partition_trivial_has_line_count_and_added_hunks():
    """filter.partition_trivial이 만든 NOISE_TRIVIAL ExcludedRecord도 같은 flat 구조(16 + 3
    키)로 직렬화되고, `filter_evidence`는 `{"line_count": n}`, #102 `added_hunks_same_file`은
    레코드에 있던 그대로다."""
    from pipeline import filter as filter_module

    record = dataclasses.replace(_record_with_hunks(), deletion_kind="PARTIAL")

    _kept, excluded = filter_module.partition_trivial([record])
    payload = extract_module.excluded_to_json_dict(excluded[0])

    assert len(payload) == 16 + 3
    assert payload["filter_status"] == "NOISE_TRIVIAL"
    assert payload["filter_rule_version"] == "v0.5"
    assert payload["filter_evidence"] == {"line_count": 2}
    assert payload["deleted_body"] == record.deleted_hunk
    assert (
        payload["added_hunks_same_file"]
        == extract_module.to_json_dict(record)["added_hunks_same_file"]
    )
    assert len(payload["added_hunks_same_file"]) == 2


def test_write_excluded_jsonl_writes_one_flat_object_per_line_in_order(tmp_path: Path):
    out = tmp_path / "run_excluded.jsonl"
    items = _sample_excluded(4)

    extract_module.write_excluded_jsonl(items, out)

    content = out.read_text(encoding="utf-8")
    assert not content.lstrip().startswith("[")
    lines = content.splitlines()
    assert len(lines) == 4
    rows = [json.loads(line) for line in lines]
    assert [row["id"] for row in rows] == [item.record.id for item in items]
    assert all(row["filter_status"] == "NOISE_MOVE" for row in rows)
    assert rows[0] == extract_module.excluded_to_json_dict(items[0])


def test_write_excluded_jsonl_creates_empty_file_when_nothing_excluded(tmp_path: Path):
    """0건이어도 파일은 만든다 — "제외 없음"과 "excluded 출력 안 함"을 구분한다."""
    out = tmp_path / "nested" / "run_excluded.jsonl"
    assert not out.parent.exists()

    extract_module.write_excluded_jsonl([], out)

    assert out.exists()
    assert out.read_bytes() == b""


def test_write_excluded_jsonl_round_trips_korean_as_utf8(tmp_path: Path):
    """한국어 커밋 메시지는 \\u 이스케이프 없이 UTF-8로 기록되고 그대로 다시 읽힌다."""
    out = tmp_path / "run_excluded.jsonl"
    korean_message = "함수를 새 모듈로 옮김. Refs #97"
    item = _sample_excluded(1)[0]
    item = dataclasses.replace(
        item, record=dataclasses.replace(item.record, commit_message=korean_message)
    )

    extract_module.write_excluded_jsonl([item], out)

    raw_bytes = out.read_bytes()
    assert "새 모듈".encode() in raw_bytes
    assert b"\\u" not in raw_bytes
    assert json.loads(out.read_text(encoding="utf-8"))["commit_message"] == korean_message


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


def test_record_id_is_deterministic():
    """같은 레코드는 언제 뽑아도 같은 id 여야 라벨이 안 떨어져 나간다 (가이드 §7.2, §11-16)."""
    args = ("a/b", "sha1", "src/x.py", "f", 10)
    assert extract_module.make_record_id(*args) == extract_module.make_record_id(*args)


def test_record_id_differs_per_function_instance():
    """한 파일에 같은 이름 함수가 여럿이면 start_line 으로 갈린다."""
    ids = {
        extract_module.make_record_id("a/b", "sha1", "src/x.py", "__init__", line)
        for line in (10, 50)
    }
    assert len(ids) == 2


def test_record_id_namespace_is_pinned():
    """네임스페이스가 바뀌면 기존 라벨이 전부 무효가 된다. 값을 고정한다."""
    assert str(extract_module.RECORD_ID_NAMESPACE) == "6f4c2b18-1c3a-5e7d-9a0b-2d8e4f1a7c63"
    assert (
        extract_module.make_record_id("a/b", "sha1", "src/x.py", "f", 10)
        == "6b4203d5-ced6-5848-971f-0746dbf7723e"
    )
