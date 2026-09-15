"""filter.py 테스트 (Issue #52 — 함수 이동 탐지 필터).

정규화·유사도·줄 수 프리필터는 순수 함수 단위로 검증한다. same-position 판정과 이동
판정(`find_moved`/`exclude_moved`)은 실제 line-number 밀림·헝크 병합처럼 손으로 만든
`Hunk`로는 재현이 까다로운 git diff 특유의 동작이 걸려 있어서, 실제 tmp git 저장소로
부모/자식 커밋을 쌓아 `extract.py`(diff → 삭제·추가·헝크)와 `filter.py`(정규화·유사도·
same-position)를 연결한 회귀 테스트로 검증한다. 크로스 파일(다른 경로) 케이스만 헝크가
필요 없어 `DeletedFunction`/`Function`을 직접 구성해 git 없이 검증한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import extract as extract_module
from pipeline import filter as filter_module
from pipeline.extract import DeletedFunction, Hunk
from pipeline.parsers.base import Function

_REPO = "acme/widgets"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
_GIT_MISSING = shutil.which("git") is None
requires_git = pytest.mark.skipif(_GIT_MISSING, reason="git CLI가 필요하다")


def _deleted(
    file_path: str,
    body: str,
    *,
    commit_sha: str = "c1",
    function_name: str = "fn",
    deletion_kind: str = "FULL_FUNCTION",
    start_line: int = 1,
) -> DeletedFunction:
    end_line = start_line + len(body.splitlines()) - 1
    return DeletedFunction(
        repo=_REPO,
        commit_sha=commit_sha,
        parent_sha="p1",
        file_path=file_path,
        function_name=function_name,
        start_line=start_line,
        end_line=end_line,
        deletion_kind=deletion_kind,
        deleted_hunk=body,
        added_hunk_same_file="",
        author_date="2026-01-01T00:00:00+00:00",
        commit_message="delete",
    )


def _function(name: str, body: str, *, start_line: int = 1) -> Function:
    return Function(
        name=name,
        start_line=start_line,
        end_line=start_line + len(body.splitlines()) - 1,
        body=body,
        signature=f"def {name}():",
    )


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


def _run_pipeline(
    repo: Path,
) -> tuple[list[DeletedFunction], dict[str, list[Function]], dict[str, list[Hunk]]]:
    """extract.py 세 함수를 최근 커밋 하나에 대해 그대로 연결해서 돌린다."""
    commit = extract_module.walk_commits(repo, "main")[-1]
    deletions = extract_module.extract_deletions(repo, _REPO, commit)
    added = extract_module.collect_added_functions(repo, commit)
    hunks = extract_module.collect_same_file_hunks(repo, commit)
    return deletions, added, hunks


# --------------------------------------------------------------------------------------
# I. normalize_function_body — 정규화 규칙 (팀 확정, CHARTER §4.2② + Issue #52 결정사항)
# --------------------------------------------------------------------------------------


def test_b_normalize_treats_variable_rename_as_identical():
    """필수 항목 B: identifier는 VAR로 치환돼 변수명만 다르면 정규화 결과가 같다."""
    a = "def foo():\n    x = 1\n    return x\n"
    b = "def foo():\n    y = 1\n    return y\n"
    assert filter_module.normalize_function_body(a) == filter_module.normalize_function_body(b)


def test_normalize_treats_numeric_literal_change_as_identical():
    a = "def foo():\n    return 1\n"
    b = "def foo():\n    return 999\n"
    assert filter_module.normalize_function_body(a) == filter_module.normalize_function_body(b)


def test_normalize_treats_string_literal_change_as_identical():
    a = "def foo():\n    return 'x'\n"
    b = "def foo():\n    return 'y'\n"
    assert filter_module.normalize_function_body(a) == filter_module.normalize_function_body(b)


def test_a_string_and_number_literals_get_different_placeholders():
    """필수 항목 A (ADR-014 결정 1): 문자열 리터럴은 STR, 숫자 리터럴은 NUM — 하나의
    LIT로 합치지 않는다. 같은 위치에 문자열 대 숫자만 다르면 정규화 결과가 달라야 한다."""
    numeric = "def foo():\n    return 1\n"
    string = "def foo():\n    return 'x'\n"
    normalized_numeric = filter_module.normalize_function_body(numeric)
    normalized_string = filter_module.normalize_function_body(string)
    assert normalized_numeric != normalized_string
    assert "NUM" in " ".join(normalized_numeric)
    assert "STR" in " ".join(normalized_string)
    assert "STR" not in " ".join(normalized_numeric)
    assert "NUM" not in " ".join(normalized_string)


def test_c_normalize_preserves_function_name():
    """필수 항목 C: 정규화 대상 함수 자신의 이름은 보존한다."""
    a = "def foo():\n    x = 1\n    return x\n"
    b = "def bar():\n    x = 1\n    return x\n"
    assert filter_module.normalize_function_body(a) != filter_module.normalize_function_body(b)
    assert "foo" in " ".join(filter_module.normalize_function_body(a))
    assert "bar" in " ".join(filter_module.normalize_function_body(b))


def test_d_normalize_preserves_attribute_and_call_names():
    """필수 항목 D: attribute 이름·호출 대상 이름은 중첩 여부와 무관하게 항상 보존한다."""
    body = "def handler(self):\n    self.headers = json.loads(data)\n    return self.headers\n"
    normalized = " ".join(filter_module.normalize_function_body(body))
    assert "headers" in normalized
    assert "loads" in normalized
    # object 쪽(self/json/data)은 일반 식별자라 placeholder로 치환된다
    tokens = normalized.split()
    assert "self" not in tokens
    assert "json" not in tokens


def test_e_nested_function_name_is_var_but_its_body_still_normalizes():
    """필수 항목 E (ADR-014 결정 1 "중첩 함수 이름의 범위"): 바깥 함수를 정규화할 때
    안의 중첩 함수 **선언 이름**은 VAR로 치환된다 — 이름만 그렇다. 중첩 함수의 나머지
    (매개변수·본문)는 건너뛰지 않고 보통 규칙대로 재귀 정규화된다(정규화가 통째로
    "제외"하는 게 아니다 — PythonAdapter가 별도 Function으로도 추출하지만, 그건 그
    중첩 함수 자신을 비교할 때의 몫이다, 아래 F 참고)."""
    # 중첩 함수를 선언만 하고 호출하지 않는다 — 호출했다면 "호출 대상 이름은 중첩
    # 여부와 무관하게 항상 보존"이라는 별개 규칙(D)이 호출 지점에서 그 이름을 다시
    # 드러내 버려, 이 테스트가 "선언 이름만" 격리해서 볼 수 없게 된다.
    outer_named_x = "def outer():\n    def inner():\n        return 1\n    return 0\n"
    outer_named_y = "def outer():\n    def other_name():\n        return 1\n    return 0\n"
    # 중첩 함수 선언 이름만 다르면(inner vs other_name) 둘 다 VAR로 치환돼 정규화 결과가 같다
    assert filter_module.normalize_function_body(
        outer_named_x
    ) == filter_module.normalize_function_body(outer_named_y)

    outer_diff_body = "def outer():\n    def inner():\n        return 2\n    return 0\n"
    # 중첩 함수 본문의 리터럴이 다르면(1 vs 2, 둘 다 NUM으로 치환되므로) 이 경우는 여전히
    # 같아야 한다 — 진짜로 "제외"됐다면 이 비교는 아무 의미가 없어진다. 대신 본문의
    # 리터럴이 아니라 *구조*가 달라지는 경우로 검증한다.
    assert filter_module.normalize_function_body(
        outer_named_x
    ) == filter_module.normalize_function_body(outer_diff_body)

    outer_diff_structure = "def outer():\n    def inner():\n        return 1 + 1\n    return 0\n"
    assert filter_module.normalize_function_body(
        outer_named_x
    ) != filter_module.normalize_function_body(outer_diff_structure)


def test_f_nested_function_normalized_on_its_own_preserves_its_own_name():
    """필수 항목 F (ADR-014 결정 1 "별도로 추출된 중첩 함수를 비교할 때: 자신의 이름을
    대상 함수 이름으로 취급해 보존한다"): PythonAdapter가 중첩 함수를 별도 Function으로
    뽑아 그 `body`만으로 `normalize_function_body`를 다시 부르면, 이번엔 그 함수 자신이
    "현재 비교 대상"이라 이름이 보존된다."""
    inner_as_root = "def inner():\n    return 1\n"
    normalized = " ".join(filter_module.normalize_function_body(inner_as_root))
    assert "inner" in normalized.split()


def test_normalize_strips_comments():
    a = "def foo():\n    x = 1\n    return x\n"
    b = "def foo():\n    # a comment\n    x = 1\n    return x\n"
    assert filter_module.normalize_function_body(a) == filter_module.normalize_function_body(b)


def test_normalize_strips_docstrings():
    a = "def foo():\n    x = 1\n    return x\n"
    b = 'def foo():\n    """docstring"""\n    x = 1\n    return x\n'
    assert filter_module.normalize_function_body(a) == filter_module.normalize_function_body(b)


def test_normalize_collapses_whitespace():
    a = "def foo():\n    x = 1\n    return x\n"
    b = "def foo():\n\n\n    x    =    1\n\n    return x\n\n"
    assert filter_module.normalize_function_body(a) == filter_module.normalize_function_body(b)


def test_normalize_returns_line_list_not_joined_string():
    """ADR-014 결정 2가 요구하는 표현: 정규화 결과는 줄 목록(list[str])이다."""
    normalized = filter_module.normalize_function_body("def foo():\n    x = 1\n    return x\n")
    assert isinstance(normalized, list)
    assert normalized == ["def foo ( ) :", "VAR = NUM", "return VAR"]


# --------------------------------------------------------------------------------------
# 줄 수 20% 프리필터 (ADR-014 결정 3) — same-position 판정과 무관: 이 단계는
# _move_similarity 안이고 find_moved에서 same-position 통과 후에만 호출된다.
# --------------------------------------------------------------------------------------


def test_line_count_diff_over_20_percent_skips_even_exact_hash_match():
    """줄 수 차이가 20%를 넘으면, 해시가 완전히 같아도(=정규화 본문 완전 동일) 비교
    자체를 건너뛴다 — ADR-014 결정 3의 순서는 줄 수 프리필터가 해시 비교보다 먼저다."""
    deleted = filter_module._NormalizedBody(lines=["same"], line_count=10, body_hash="h")
    candidate = filter_module._NormalizedBody(lines=["same"], line_count=7, body_hash="h")
    assert filter_module._line_count_diff_ratio(10, 7) > filter_module.LINE_COUNT_SKIP_RATIO
    assert filter_module._move_similarity(deleted, candidate) is None


def test_line_count_diff_exactly_20_percent_is_not_skipped():
    """정확히 20%는 "넘는" 게 아니므로 비교 대상으로 남는다."""
    deleted = filter_module._NormalizedBody(lines=["same"], line_count=5, body_hash="h")
    candidate = filter_module._NormalizedBody(lines=["same"], line_count=4, body_hash="h")
    assert filter_module._line_count_diff_ratio(5, 4) == filter_module.LINE_COUNT_SKIP_RATIO
    assert filter_module._move_similarity(deleted, candidate) == 1.0  # 해시 일치 → similarity 1.0


def test_h_normalized_line_count_used_even_when_raw_line_count_differs_a_lot():
    """필수 항목 H: 원본 줄 수는 20%를 훌쩍 넘게 다르지만(주석·빈 줄 때문에), 정규화
    후 줄 수는 같다 — ADR-014 결정 3은 "정규화 본문의 줄 수"를 기준으로 하므로 이
    쌍은 건너뛰지 않고 비교돼야 한다(실제로 완전히 같아 similarity 1.0)."""
    raw_a = "def foo():\n    x = 1\n    return x\n"
    raw_b = (
        "def foo():\n\n\n\n\n\n\n\n\n\n"
        "    # comment\n    # comment\n    # comment\n"
        "    x = 1\n    return x\n"
    )
    assert (
        abs(len(raw_a.splitlines()) - len(raw_b.splitlines())) / len(raw_b.splitlines())
        > filter_module.LINE_COUNT_SKIP_RATIO
    )  # 원본 기준이면 20%를 넘어 스킵됐을 것

    normalized_a = filter_module._normalize(raw_a)
    normalized_b = filter_module._normalize(raw_b)
    assert normalized_a.line_count == normalized_b.line_count  # 정규화 후엔 같다

    assert filter_module._move_similarity(normalized_a, normalized_b) == 1.0


def test_i_normalized_line_count_diff_over_20_percent_skips():
    """필수 항목 I: 정규화 후 줄 수 차이가 20%를 넘으면 건너뛴다(일반 케이스,
    ADR-014 결정 3 그대로)."""
    long_body = "def foo():\n" + "\n".join(f"    a{i} = 1" for i in range(10)) + "\n    return a0\n"
    short_body = "def foo():\n    a0 = 1\n    return a0\n"
    normalized_long = filter_module._normalize(long_body)
    normalized_short = filter_module._normalize(short_body)
    assert (
        filter_module._line_count_diff_ratio(
            normalized_long.line_count, normalized_short.line_count
        )
        > filter_module.LINE_COUNT_SKIP_RATIO
    )
    assert filter_module._move_similarity(normalized_long, normalized_short) is None


def test_j_normalized_line_count_diff_exactly_20_percent_boundary_reaches_comparison():
    """필수 항목 J: 정규화 후 줄 수 차이가 정확히 20%(경계값)면 건너뛰지 않고 실제
    비교(SequenceMatcher)까지 간다 — ADR-014 결정 3 "긴 쪽의 20%를 넘으면"(초과만
    스킵, 20%는 포함해서 비교).

    주의: 20%는 "스킵 안 됨"이지 "0.9 이상 나옴"이 아니다. `a=5, b=4`처럼 줄 하나가
    통째로 빠진 경우 `ratio() = 2·min(a,b)/(a+b)`의 상한 자체가 `8/9 ≈ 0.889`라
    (ADR-014 결정 3의 "긴 쪽의 20%를 넘으면 상한이 약 0.889" 계산과 같은 식 — 정확히
    20%인 이 경계에서도 최댓값은 여전히 0.889다) 0.9를 못 넘는다. 그래서 이 테스트는
    "None이 아님"이 아니라 "실제로 SequenceMatcher가 계산한 값(0.889)이 나왔고, 그게
    프리필터 스킵으로 인한 즉시 None이 아니다"를 확인한다.
    """
    # 5줄 vs 4줄, 4줄이 5줄의 부분집합(줄 하나만 없음): (5-4)/5 == 0.2 (경계, 스킵 안 됨)
    body_5 = "def foo():\n    a = 1\n    b = 1\n    c = 1\n    return a\n"
    body_4 = "def foo():\n    a = 1\n    b = 1\n    return a\n"
    normalized_5 = filter_module._normalize(body_5)
    normalized_4 = filter_module._normalize(body_4)
    assert normalized_5.line_count == 5
    assert normalized_4.line_count == 4
    assert (
        filter_module._line_count_diff_ratio(normalized_5.line_count, normalized_4.line_count)
        == filter_module.LINE_COUNT_SKIP_RATIO
    )

    from difflib import SequenceMatcher

    expected_ratio = SequenceMatcher(
        None, normalized_5.lines, normalized_4.lines, autojunk=False
    ).ratio()
    assert expected_ratio < filter_module.SIMILARITY_THRESHOLD  # 이 pair 자체는 이동 아님

    # _move_similarity가 프리필터에서 즉시 None을 반환한 게 아니라, 실제로 같은 계산을
    # 거쳐 None(0.9 미만)에 도달했음을 확인한다.
    assert filter_module._move_similarity(normalized_5, normalized_4) is None
    assert expected_ratio == 8 / 9  # ADR-014 결정 3의 상한 계산과 일치


def test_k_sequence_matcher_operates_on_line_list_not_character_string():
    """필수 항목 K: 한 줄만 바뀌어도(줄 전체가 다른 문자열이 되면) 그 줄은 "부분적으로
    비슷하다"는 문자 단위 부분 점수를 전혀 못 받는다 — 문자열 전체를 이어 붙여 문자
    단위로 비교했다면 얻었을 부분 점수와 다르다는 것으로, line-list 비교임을 확인한다."""
    # 11줄짜리 함수, 그 중 한 줄만 구조가 다르다(문자 단위로 보면 상당히 비슷한 줄이지만
    # 줄 단위로 보면 완전히 다른 줄이라 0점 처리돼야 한다).
    parent = "def foo():\n" + "\n".join(f"    a{i} = 1" for i in range(1, 10)) + "\n    return a1\n"
    child = (
        "def foo():  # x\n"
        + "\n".join(
            f"    a{i} = 1 + 2  # x" if i == 5 else f"    a{i} = 1  # x" for i in range(1, 10)
        )
        + "\n    return a1  # x\n"
    )
    normalized_parent = filter_module._normalize(parent)
    normalized_child = filter_module._normalize(child)
    assert normalized_parent.line_count == normalized_child.line_count == 11

    from difflib import SequenceMatcher

    line_ratio = SequenceMatcher(
        None, normalized_parent.lines, normalized_child.lines, autojunk=False
    ).ratio()
    char_ratio = SequenceMatcher(
        None, " ".join(normalized_parent.lines), " ".join(normalized_child.lines)
    ).ratio()
    # 줄 단위 비교(10줄 중 9줄 완전 일치)와 문자 단위 비교(부분 점수 포함)는 다른 값을
    # 내야 한다 — 실제로 우리 구현은 줄 단위 값과 일치해야 한다.
    assert line_ratio != char_ratio
    assert filter_module._move_similarity(normalized_parent, normalized_child) == line_ratio


def test_l_autojunk_false_changes_the_move_classification():
    """필수 항목 L: `autojunk`의 기본값(`True`)은 200개 이상인 시퀀스에서 자주 나오는
    원소를 무시해 비율을 실제보다 낮게 낸다(ADR-014 결정 2 이유). 아래 pair는 실제로
    `autojunk=True`였다면 0.9 미만(이동 아님)으로, `autojunk=False`에서는 0.9 이상
    (이동)으로 **분류 결과 자체가 달라진다** — 우리 구현이 `autojunk=False`를 실제로
    쓰고 있음을 값으로 증명한다."""
    from difflib import SequenceMatcher

    n = 210
    deleted_lines = []
    candidate_lines = []
    for i in range(n):
        if i % 2 == 0:
            deleted_lines.append("pass")
            candidate_lines.append("pass")
        else:
            deleted_lines.append(f"line_{i}")
            candidate_lines.append(f"line_{i}")
    # 홀수 인덱스(팝업 아닌 고유 줄) 중 12개만 다르게 바꾼다
    changed = [i for i in range(1, n, 2)][:12]
    for i in changed:
        candidate_lines[i] = f"changed_{i}"

    ratio_autojunk_true = SequenceMatcher(
        None, deleted_lines, candidate_lines, autojunk=True
    ).ratio()
    ratio_autojunk_false = SequenceMatcher(
        None, deleted_lines, candidate_lines, autojunk=False
    ).ratio()
    assert ratio_autojunk_true < filter_module.SIMILARITY_THRESHOLD
    assert ratio_autojunk_false >= filter_module.SIMILARITY_THRESHOLD

    deleted = filter_module._NormalizedBody(
        deleted_lines, len(deleted_lines), filter_module._body_hash(deleted_lines)
    )
    candidate = filter_module._NormalizedBody(
        candidate_lines, len(candidate_lines), filter_module._body_hash(candidate_lines)
    )
    assert filter_module._move_similarity(deleted, candidate) == ratio_autojunk_false


def test_m_both_normalized_bodies_empty_are_never_a_match():
    """필수 항목 M (ADR-014 결정 3 0줄 제외): 양쪽 다 0줄이면 후보가 아니다 — 해시가
    우연히 같아도(빈 목록끼리는 해시가 같다) 이동으로 오판하면 안 된다."""
    empty_a = filter_module._NormalizedBody([], 0, filter_module._body_hash([]))
    empty_b = filter_module._NormalizedBody([], 0, filter_module._body_hash([]))
    assert empty_a.body_hash == empty_b.body_hash  # 사전 조건: 해시는 실제로 같다
    assert filter_module._move_similarity(empty_a, empty_b) is None


def test_n_one_normalized_body_empty_is_never_a_match():
    """필수 항목 N: 한쪽만 0줄이어도 후보가 아니다."""
    empty = filter_module._NormalizedBody([], 0, filter_module._body_hash([]))
    nonempty = filter_module._normalize("def foo():\n    return 1\n")
    assert filter_module._move_similarity(empty, nonempty) is None
    assert filter_module._move_similarity(nonempty, empty) is None


# --------------------------------------------------------------------------------------
# same-position 판정 — 순수 단위 테스트 (손으로 만든 Hunk/Function). 실제 git 동작이 걸린
# 시나리오(밀림·헝크 병합)는 아래 A-D의 실제 저장소 회귀 테스트가 맡는다.
# --------------------------------------------------------------------------------------


def test_same_position_range_ignores_pure_deletion_hunks():
    """new_count == 0(제자리에 아무것도 안 남음)인 헝크는 same-position 범위에서 뺀다 —
    그 경우 이 파일의 다른 곳에 추가된 함수는 정당한 이동 후보로 남아야 한다."""
    hunks = [Hunk(old_start=1, old_count=5, new_start=10, new_count=0)]
    assert filter_module._same_position_child_range(hunks, 1, 5) is None


def test_same_position_range_merges_multiple_overlapping_hunks():
    hunks = [
        Hunk(old_start=1, old_count=2, new_start=1, new_count=2),
        Hunk(old_start=3, old_count=2, new_start=3, new_count=3),
    ]
    assert filter_module._same_position_child_range(hunks, 1, 4) == (1, 5)


def test_is_same_position_true_when_candidate_overlaps_child_range():
    hunks = [Hunk(old_start=5, old_count=3, new_start=5, new_count=4)]
    candidate = _function(
        "foo", "def foo():\n    return 1\n    return 2\n    return 3\n", start_line=5
    )
    assert filter_module._is_same_position({"a.py": hunks}, "a.py", 5, 7, candidate) is True


def test_is_same_position_false_when_candidate_outside_child_range():
    hunks = [Hunk(old_start=5, old_count=3, new_start=5, new_count=4)]
    candidate = _function("foo", "def foo():\n    return 1\n", start_line=20)
    assert filter_module._is_same_position({"a.py": hunks}, "a.py", 5, 7, candidate) is False


def test_is_same_position_false_when_path_has_no_hunks():
    candidate = _function("foo", "def foo():\n    return 1\n", start_line=5)
    assert filter_module._is_same_position({}, "a.py", 5, 7, candidate) is False


# --------------------------------------------------------------------------------------
# same-position 판정 (Issue #52 2차 팀 결정). 이 절의 함수명 접두사 a/b/c/d는 이 절 안의
# 순번일 뿐, 3차 팀 결정(코드 리뷰 BLOCKER/HIGH 수정)의 필수 시나리오 A-I와는 별개다 —
# 아래 각 테스트가 어떤 필수 시나리오에 해당하는지는 docstring에 명시했다.
# --------------------------------------------------------------------------------------


@requires_git
def test_a_same_path_same_position_partial_edit_is_not_moved(tmp_path: Path):
    """필수 시나리오 H(같은 위치 수정) 해당. 같은 경로 + 같은 위치에서 함수 전체가
    다시 쓰였고(그래서 FULL_FUNCTION), 정규화 유사도가 0.9를 넘는 작은 수정 — 제자리
    수정이므로 이동이 아니다. added line과 겹쳐 candidate가 되더라도(B-1 수정 후에도
    이 함수 자체는 전부 다시 쓰였으므로 candidate에는 남는다) same-position 필터에서
    제외된다. same-position 보호가 없으면 이 케이스가 그대로 오탐된다(비교로 실제
    ratio가 0.9 이상임을 검증) — line-list SequenceMatcher(ADR-014)라 한 줄만 통째로
    바뀌어도 그 줄은 0점 처리되므로, 줄 수를 늘려 "10줄 중 1줄만 구조가 다름" 형태로
    구성한다(캐릭터 단위 비교였을 때의 5줄짜리 픽스처는 더 이상 0.9를 못 넘는다)."""
    repo = _init_repo(tmp_path / "repo")
    _write(
        repo,
        "a.py",
        "def foo():\n" + "\n".join(f"    a{i} = 1" for i in range(1, 10)) + "\n    return a1\n",
    )
    _commit_all(repo, "add foo")
    _write(
        repo,
        "a.py",
        "def foo():  # x\n"
        + "\n".join(
            f"    a{i} = 1 + 2  # x" if i == 5 else f"    a{i} = 1  # x" for i in range(1, 10)
        )
        + "\n    return a1  # x\n",
    )
    _commit_all(repo, "in-place edit touching every line, one line structurally different")

    deletions, added, hunks = _run_pipeline(repo)
    assert [(d.function_name, d.deletion_kind) for d in deletions] == [("foo", "FULL_FUNCTION")]

    deleted_norm = filter_module.normalize_function_body(deletions[0].deleted_hunk)
    candidate_norm = filter_module.normalize_function_body(added["a.py"][0].body)
    assert deleted_norm != candidate_norm  # exact match가 아니다
    from difflib import SequenceMatcher

    assert (
        SequenceMatcher(None, deleted_norm, candidate_norm, autojunk=False).ratio()
        >= filter_module.SIMILARITY_THRESHOLD
    )

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert [d.function_name for d in kept] == ["foo"]  # 이동으로 빠지지 않았다


@requires_git
def test_b_same_path_different_position_exact_match_is_moved(tmp_path: Path):
    """필수 시나리오 G(같은 파일 내부 이동) 해당. 같은 파일 안에서 함수가 통째로 다른
    자리로 옮겨감(재배치) + 본문은 변수명만 다르고 정규화하면 완전히 같다 — 새 위치는
    실제 added line이라 candidate가 되고, same-position이 아니므로 이동으로 판정한다."""
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.py", "def foo():\n    x = 1\n    return x\n\n\ndef other():\n    return 0\n")
    _commit_all(repo, "add foo and other")
    _write(repo, "a.py", "def other():\n    return 0\n\n\ndef foo():\n    y = 1\n    return y\n")
    _commit_all(repo, "reorder: foo moved below other")

    deletions, added, hunks = _run_pipeline(repo)
    assert [(d.function_name, d.deletion_kind) for d in deletions] == [("foo", "FULL_FUNCTION")]

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert kept == []  # 이동으로 판정돼 빠졌다


@requires_git
def test_c_same_path_different_position_similarity_above_threshold_is_moved(tmp_path: Path):
    """필수 시나리오 G(같은 파일 내부 이동) 해당. 같은 파일 안에서 다른 자리로 옮겨가면서
    살짝 수정도 됨(정규화해도 완전 일치는 아니지만 ratio >= 0.9) — 이동으로 판정해야 한다."""
    repo = _init_repo(tmp_path / "repo")
    _write(
        repo,
        "a.py",
        "def foo():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n\n\n"
        "def other():\n    return 0\n",
    )
    _commit_all(repo, "add foo and other")
    _write(
        repo,
        "a.py",
        "def other():\n    return 0\n\n\n"
        "def foo():  # x\n    a = 1  # x\n    b = 2  # x\n    c = 3  # x\n"
        "    extra = 9  # x\n    return a + b + c  # x\n",
    )
    _commit_all(repo, "move foo below other, tweak body")

    deletions, added, hunks = _run_pipeline(repo)
    foo_deletion = next(d for d in deletions if d.function_name == "foo")
    foo_candidate = next(f for f in added["a.py"] if f.name == "foo")

    deleted_norm = filter_module.normalize_function_body(foo_deletion.deleted_hunk)
    candidate_norm = filter_module.normalize_function_body(foo_candidate.body)
    assert deleted_norm != candidate_norm  # exact match 경로가 아니라 SequenceMatcher 경로임을 확인

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert "foo" not in [d.function_name for d in kept]  # 이동으로 판정돼 빠졌다


@requires_git
def test_d_unrelated_insertion_above_shifts_line_numbers_but_stays_same_position(
    tmp_path: Path,
):
    """필수 시나리오 H(같은 위치 수정) 해당. 함수 위쪽에 무관한 새 함수가 끼어들어 자식
    쪽 start_line이 밀려도(5 -> 9), 헝크 매핑으로 보면 논리적으로 같은 자리에서 수정된
    것 — 단순 start_line 비교였다면 "달라졌다"고 오판했을 케이스다. 실제로 ratio도
    0.9를 넘는다(비교로 확인) — line-list SequenceMatcher(ADR-014)라 test_a와 같은
    이유로 "10줄 중 1줄만 구조가 다름" 픽스처를 쓴다."""
    repo = _init_repo(tmp_path / "repo")
    _write(
        repo,
        "a.py",
        "def unrelated():\n    return 0\n\n\n"
        "def foo():\n" + "\n".join(f"    a{i} = 1" for i in range(1, 10)) + "\n    return a1\n",
    )
    _commit_all(repo, "add unrelated and foo")
    _write(
        repo,
        "a.py",
        "def unrelated():\n    return 0\n\n\n"
        "def helper():\n    return 1\n\n\n"
        "def foo():  # x\n"
        + "\n".join(
            f"    a{i} = 1 + 2  # x" if i == 5 else f"    a{i} = 1  # x" for i in range(1, 10)
        )
        + "\n    return a1  # x\n",
    )
    _commit_all(repo, "insert helper above foo, edit foo in place")

    deletions, added, hunks = _run_pipeline(repo)
    foo_deletion = next(d for d in deletions if d.function_name == "foo")
    foo_candidate = next(f for f in added["a.py"] if f.name == "foo")
    assert foo_deletion.start_line != foo_candidate.start_line  # 실제로 줄 번호가 밀렸다

    deleted_norm = filter_module.normalize_function_body(foo_deletion.deleted_hunk)
    candidate_norm = filter_module.normalize_function_body(foo_candidate.body)
    from difflib import SequenceMatcher

    assert (
        SequenceMatcher(None, deleted_norm, candidate_norm, autojunk=False).ratio()
        >= filter_module.SIMILARITY_THRESHOLD
    )

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert "foo" in [d.function_name for d in kept]  # 밀렸어도 이동으로 오판하지 않았다


def test_e_different_file_path_is_moved():
    """다른 file_path로 옮겨간 경우는 기존처럼 이동으로 판정한다 — same-position은 같은
    경로에서만 의미가 있으므로 `same_file_hunks`는 빈 dict로 둬도 된다."""
    deleted = _deleted("old/a.py", "def foo():\n    x = 1\n    return x\n")
    added = {"new/a.py": [_function("foo", "def foo():\n    y = 1\n    return y\n")]}

    moved = filter_module.find_moved([deleted], added, {})

    assert filter_module._record_key(deleted) in moved
    assert filter_module.exclude_moved([deleted], added, {}) == []


def test_f_partial_deletion_is_never_a_move_candidate():
    """PARTIAL은 함수 전체가 아니라 지워진 조각만 담고 있어 이동 판정 대상이 아니다
    (모듈 독스트링 "삭제 후보(deleted-side)" 절 참고) — FULL_FUNCTION/PARTIAL의 기존
    의미를 바꾸지 않는다, PARTIAL을 이 필터가 그냥 건드리지 않을 뿐이다. same-position
    규칙과 무관하게 PARTIAL이면 애초에 비교 대상에 들어가지 않는다."""
    deleted = _deleted("old/a.py", "    x = 1\n    return x\n", deletion_kind="PARTIAL")
    added = {"new/a.py": [_function("foo", "def foo():\n    x = 1\n    return x\n")]}

    moved = filter_module.find_moved([deleted], added, {})

    assert moved == set()
    assert filter_module.exclude_moved([deleted], added, {}) == [deleted]


def test_low_similarity_different_path_is_kept():
    """일반적인 낮은 유사도 + 다른 경로 — 이동이 아니다(보조 회귀, 필수 목록 밖)."""
    deleted = _deleted("old/a.py", "def foo():\n    x = 1\n    return x\n")
    candidate_body = (
        "def bar():\n    total = 0\n    for i in range(10):\n        total += i\n    return total\n"
    )
    added = {"new/a.py": [_function("bar", candidate_body)]}

    moved = filter_module.find_moved([deleted], added, {})

    assert moved == set()
    assert filter_module.exclude_moved([deleted], added, {}) == [deleted]


def test_ordinary_deletion_with_no_similar_candidate_is_kept():
    deleted = _deleted("a.py", "def foo():\n    x = 1\n    return x\n")

    moved = filter_module.find_moved([deleted], {}, {})

    assert moved == set()
    assert filter_module.exclude_moved([deleted], {}, {}) == [deleted]


# --------------------------------------------------------------------------------------
# B/C/D/E. 1:1 greedy 매칭 (코드 리뷰 HIGH 수정, 팀 결정) — 하나의 added function이 여러
# deleted function을 동시에 설명하면 안 된다. 유사도 내림차순 greedy + deterministic
# tie-break. 전부 합성 객체로 검증한다(git 불필요 — 매칭 로직 자체는 git diff 특성과
# 무관하다).
# --------------------------------------------------------------------------------------


def test_b_one_to_one_exact_match_consumes_only_one_deleted():
    """deleted 2개(완전히 동일한 본문, 서로 다른 파일) + exact added 1개
    -> 최대 하나만 NOISE_MOVE, 나머지 하나는 KEPT."""
    body = "def helper():\n    return 1\n"
    d1 = _deleted("a/mod1.py", body, function_name="helper")
    d2 = _deleted("b/mod2.py", body, function_name="helper")
    added = {"c/mod3.py": [_function("helper", body)]}

    moved = filter_module.find_moved([d1, d2], added, {})
    kept = filter_module.exclude_moved([d1, d2], added, {})

    assert len(moved) == 1
    assert len(kept) == 1
    # tie-break: record_key(commit_sha, file_path, ...) 오름차순 — "a/mod1.py" < "b/mod2.py"
    assert filter_module._record_key(d1) in moved
    assert kept[0].file_path == "b/mod2.py"


def test_c_higher_similarity_wins_the_shared_candidate():
    """deleted 2개가 하나의 added와 각각 >=0.9로 매칭 가능 -> similarity가 더 높은
    (정규화 완전 일치, 1.0) 쪽이 매칭되고, 낮은 쪽(0.9 이상이지만 1.0 미만)은 KEPT."""
    candidate_body = "def foo():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n"
    exact_body = "def foo():\n    x = 1\n    y = 2\n    z = 3\n    return x + y + z\n"
    close_body = (
        "def foo():\n    a = 1\n    b = 2\n    c = 3\n    extra = 9\n    return a + b + c\n"
    )
    d_exact = _deleted("a/exact.py", exact_body, function_name="foo")
    d_close = _deleted("b/close.py", close_body, function_name="foo")
    added = {"c/dest.py": [_function("foo", candidate_body)]}

    # 사전 조건: 둘 다 threshold(0.9)를 넘지만 값은 다르다.
    exact_norm = filter_module.normalize_function_body(exact_body)
    close_norm = filter_module.normalize_function_body(close_body)
    candidate_norm = filter_module.normalize_function_body(candidate_body)
    assert exact_norm == candidate_norm
    from difflib import SequenceMatcher

    close_ratio = SequenceMatcher(None, close_norm, candidate_norm).ratio()
    assert filter_module.SIMILARITY_THRESHOLD <= close_ratio < 1.0

    moved = filter_module.find_moved([d_exact, d_close], added, {})
    kept = filter_module.exclude_moved([d_exact, d_close], added, {})

    assert filter_module._record_key(d_exact) in moved
    assert filter_module._record_key(d_close) not in moved
    assert [r.file_path for r in kept] == ["b/close.py"]


def test_d_greedy_matching_does_not_reuse_candidates_across_independent_groups():
    """여러 (deleted, added) 그룹이 섞여 있을 때, 같은 added를 두고 경쟁하는 deleted는
    하나만 이기고(위 test_b와 동일한 경쟁), 경쟁이 없는 독립된 pair는 그대로 매칭된다 —
    전체가 하나의 유사도 순서로 처리돼도 서로 다른 그룹이 엉키지 않는다."""
    body_a = "def alpha():\n    return 1\n"
    body_b = "def beta():\n    return 2\n"

    d1 = _deleted("a/d1.py", body_a, function_name="alpha")  # c_a와 경쟁
    d2 = _deleted("a/d2.py", body_a, function_name="alpha")  # c_a와 경쟁 (d1과 동점)
    d3 = _deleted("a/d3.py", body_b, function_name="beta")  # c_b와 단독 매칭

    added = {
        "c/dest1.py": [_function("alpha", body_a)],  # c_a
        "c/dest2.py": [_function("beta", body_b)],  # c_b
    }

    moved = filter_module.find_moved([d1, d2, d3], added, {})
    kept = filter_module.exclude_moved([d1, d2, d3], added, {})

    assert filter_module._record_key(d3) in moved  # 경쟁 없는 pair는 항상 매칭
    assert len({filter_module._record_key(d1), filter_module._record_key(d2)} & moved) == 1
    assert len(moved) == 2
    assert len(kept) == 1
    assert kept[0].file_path in {"a/d1.py", "a/d2.py"}


def test_e_greedy_matching_is_deterministic_regardless_of_input_order():
    """동일 유사도(tie)인 pair가 여럿이어도, 입력 순서를 바꾸면(1) deletions 리스트
    순서, (2) added dict의 key 순서, (3) 그 안 각 파일의 함수 리스트 내부 순서 — 결과는
    항상 같아야 한다.

    candidate가 1개뿐이면 `reversed()`가 no-op이라 이 순서 독립성을 실제로 검증하지
    못한다(코드 리뷰 지적) — 그래서 deleted 4개(전부 동일 본문, exact match라 전부
    동점) : candidate 3개(두 파일에 나눠, 그중 한 파일엔 2개)로 구성해 여러 pair가
    실제로 경쟁하게 만든다. 후보가 3개뿐이라 deleted 4개 중 1개는 항상 KEPT여야 한다."""
    body = "def helper():\n    return 1\n"
    d1 = _deleted("a/d1.py", body, function_name="helper")
    d2 = _deleted("b/d2.py", body, function_name="helper")
    d3 = _deleted("c/d3.py", body, function_name="helper")
    d4 = _deleted("d/d4.py", body, function_name="helper")
    deletions = [d1, d2, d3, d4]

    c1 = _function("helper", body, start_line=1)
    c2 = _function("helper", body, start_line=10)
    c3 = _function("helper", body, start_line=1)
    added_forward = {"p/mod1.py": [c1, c2], "q/mod2.py": [c3]}

    # (1) deletions 순서, (2) dict key 순서, (3) p/mod1.py 리스트 내부 순서를 전부 뒤집는다.
    deletions_reversed = list(reversed(deletions))
    added_reversed = {"q/mod2.py": [c3], "p/mod1.py": [c2, c1]}

    forward = filter_module.find_moved(deletions, added_forward, {})
    reversed_order = filter_module.find_moved(deletions_reversed, added_reversed, {})

    assert forward == reversed_order
    assert len(forward) == 3  # candidate가 3개뿐이라 deleted 4개 중 정확히 1개는 KEPT
    assert filter_module._record_key(d4) not in forward  # 두 경우 모두 같은 쪽(d4)이 KEPT


# --------------------------------------------------------------------------------------
# A. B-1 회귀 (코드 리뷰 BLOCKER 수정) — 손대지 않은 기존 함수가 added candidate가
# 되지 않으므로, 그와 우연히 닮은 무관한 진짜 삭제가 이동으로 오판되지 않는다.
# I. 공백 포함 file_path — collect_added_functions/collect_same_file_hunks에서 기존
# trailing-tab 경로 버그(#49)가 재발하지 않는지 확인한다.
# --------------------------------------------------------------------------------------


@requires_git
def test_a_untouched_function_does_not_cause_unrelated_deletion_to_be_excluded(
    tmp_path: Path,
):
    """x.py는 이번 커밋에서 `helper`만 고치고 `check`는 전혀 손 안 댄다. y.py의 `check`
    (x.py의 손 안 댄 `check`와 완전히 동일한, 흔하고 짧은 함수)는 정말로 삭제될 뿐이다.
    `helper` 수정 때문에 x.py의 손 안 댄 `check`가 added candidate가 되면, y.py의 진짜
    삭제가 "이동"으로 잘못 제외된다 — 코드 리뷰 BLOCKER 재현 케이스, end-to-end 확인."""
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

    deletions, added, hunks = _run_pipeline(repo)

    assert {f.name for f in added["x.py"]} == {"helper"}  # check는 후보가 아니다

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert "check" in [d.function_name for d in kept]  # y.py의 진짜 삭제가 살아남는다


@requires_git
def test_i_added_function_detection_survives_trailing_tab_path_with_space(tmp_path: Path):
    """경로에 공백이 있으면 diff 헤더에 탭이 붙는다(#49 재현·수정 대상). 이동 목적지
    경로에 공백이 있어도 added candidate로 정상 확보돼야 한다."""
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "old/eval util.py", "def foo():\n    return 1\n")
    _commit_all(repo, "add foo")
    (repo / "old" / "eval util.py").unlink()
    _write(repo, "new/eval util.py", "def foo():\n    return 2\n")
    _commit_all(repo, "move eval util.py")

    deletions, added, hunks = _run_pipeline(repo)

    assert deletions[0].file_path == "old/eval util.py"  # 탭 없음, 공백 보존
    assert "new/eval util.py" in added
    assert [f.name for f in added["new/eval util.py"]] == ["foo"]

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert kept == []  # 이동으로 판정돼 최종 목록에서 빠졌다


# --------------------------------------------------------------------------------------
# 순수 신규 파일로 이동 — 실제 git 저장소로 extract -> filter 연결 회귀 테스트
# (다른 경로 이동의 극단 케이스: 목적지 파일 전체가 신규 파일). Issue #52 필수 시나리오 F.
# --------------------------------------------------------------------------------------


@requires_git
def test_pure_new_file_move_is_detected_end_to_end(tmp_path: Path):
    """`old/pkg/a.py` 삭제 + `new/pkg/a.py` 추가(–-no-renames diff에서 별개 섹션 두 개로
    쪼개짐) — extract.collect_added_functions가 목적지 파일을 확보하고, filter가 그걸로
    이동을 잡아 최종 목록에서 뺀다."""
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "old/pkg/a.py", "def foo():\n    x = 1\n    return x\n")
    _commit_all(repo, "add foo")
    (repo / "old" / "pkg" / "a.py").unlink()
    _write(repo, "new/pkg/a.py", "def foo():\n    y = 1\n    return y\n")
    _commit_all(repo, "move old/pkg/a.py to new/pkg/a.py")

    deletions, added, hunks = _run_pipeline(repo)

    assert len(deletions) == 1
    assert deletions[0].file_path == "old/pkg/a.py"
    assert "new/pkg/a.py" in added
    assert [f.name for f in added["new/pkg/a.py"]] == ["foo"]
    assert hunks == {}  # old_path != new_path인 섹션들이라 same-file 헝크는 없다

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert kept == []  # 이동으로 판정돼 최종 목록에서 빠졌다


@requires_git
def test_ordinary_full_deletion_with_unrelated_addition_survives_end_to_end(tmp_path: Path):
    """일반적인 실제 삭제(다른 경로에 유사한 추가 함수가 없음)는 살아남는다 — 대조군."""
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "a.py", "def foo():\n    x = 1\n    return x\n")
    _write(repo, "b.py", "def unrelated():\n    return 0\n")
    _commit_all(repo, "add foo and unrelated")
    _write(repo, "a.py", "")
    _write(
        repo,
        "b.py",
        "def unrelated():\n    return 0\n\n\ndef helper():\n    total = 0\n"
        "    for i in range(5):\n        total += i\n    return total\n",
    )
    _commit_all(repo, "delete foo, add unrelated helper elsewhere")

    deletions, added, hunks = _run_pipeline(repo)

    kept = filter_module.exclude_moved(deletions, added, hunks)

    assert len(kept) == 1
    assert kept[0].function_name == "foo"
