"""파서 인터페이스 테스트 (이슈 #4).

완료 조건: 모듈 함수/클래스 메서드/중첩 함수/async/데코레이터 함수 추출, 라인 정확성,
구문 오류 소스에서의 안정 동작(예외 없이 빈 목록 또는 신뢰 가능한 부분 결과).

라인 번호는 소스를 리스트로 만들어 그대로 대조한다 — 삼중따옴표 문자열의 들여쓰기·첫 줄
공백에 기대면 라인 번호를 착각하기 쉽다.
"""

from pipeline.parsers.base import Function
from pipeline.parsers.python_adapter import PythonAdapter


def source(*lines: str) -> str:
    return "\n".join(lines)


def names(functions: list[Function]) -> list[str]:
    return [fn.name for fn in functions]


# --------------------------------------------------------------------------------------
# 기본 추출 대상 (완료 조건: 모듈 함수 / 클래스 메서드 / 중첩 함수 / async / 데코레이터)
# --------------------------------------------------------------------------------------


def test_module_level_function_fields():
    src = source(
        "def add(x: int, y: int) -> int:",
        "    return x + y",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "add"
    assert fn.start_line == 1
    assert fn.end_line == 2
    assert fn.signature == "def add(x: int, y: int) -> int:"
    assert fn.body == src


def test_class_method_extraction():
    src = source(
        "class A:",
        "    def method(self, x):",
        "        return x",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "method"
    assert fn.start_line == 2
    assert fn.end_line == 3
    assert fn.signature == "def method(self, x):"
    # body 는 원문 그대로 — 들여쓰기를 포함해 해당 줄들을 그대로 잘라 붙인 것
    assert fn.body == "    def method(self, x):\n        return x"


def test_nested_function_extraction():
    src = source(
        "def outer():",
        "    def inner():",
        "        return 1",
        "    return inner",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["outer", "inner"]
    inner = functions[1]
    assert inner.start_line == 2
    assert inner.end_line == 3
    assert inner.signature == "def inner():"


def test_async_function_extraction():
    src = source(
        "async def fetch(url: str) -> None:",
        "    await get(url)",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "fetch"
    assert fn.start_line == 1
    assert fn.end_line == 2
    assert fn.signature == "async def fetch(url: str) -> None:"


def test_single_decorator_is_in_lines_and_body_but_not_signature_while_colon_is():
    src = source(
        "@staticmethod",
        "def helper():",
        "    return 1",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "helper"
    assert fn.start_line == 1  # 데코레이터 줄부터
    assert fn.end_line == 3
    assert fn.body == src
    assert fn.signature == "def helper():"  # 데코레이터는 없지만 콜론은 포함


def test_multiple_decorators_all_included_in_range():
    src = source(
        "@deco1",
        "@deco2(arg=1)",
        "def handler():",
        "    return None",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.start_line == 1
    assert fn.end_line == 4
    assert fn.signature == "def handler():"


def test_decorated_function_with_nested_function_is_still_found():
    src = source(
        "@deco",
        "def outer():",
        "    def inner():",
        "        return 1",
        "    return inner",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["outer", "inner"]
    outer = functions[0]
    assert outer.start_line == 1  # 데코레이터 포함
    assert outer.end_line == 5
    inner = functions[1]
    assert inner.start_line == 3
    assert inner.end_line == 4


def test_functions_are_returned_in_source_order():
    src = source(
        "def first():",
        "    return 1",
        "",
        "def second():",
        "    return 2",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["first", "second"]
    assert functions[0].start_line == 1
    assert functions[1].start_line == 4


# --------------------------------------------------------------------------------------
# 회귀: 함수 본문 첫 줄이 독립된 주석인 경우 (Issue #5 E2E, psf/requests `add_password` 재현)
#
# 함수 본문 첫 줄이 `#` 주석 하나뿐이면 tree-sitter-python이 그 주석을 `block`(본문) 밖,
# `:`와 `block` 사이에 형제 노드로 끼워 넣는다. "body 바로 앞 자식이 곧 `:`"라고 가정하던
# _colon_before_body가 이 경우 AssertionError를 던졌다(psf/requests 152번째 커밋에서 재현).
# --------------------------------------------------------------------------------------


def test_leading_comment_as_first_body_line_does_not_crash():
    src = source(
        "def add_password(self):",
        "    # uri could be a single URI or a sequence",
        "    return 1",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "add_password"
    assert fn.start_line == 1
    assert fn.end_line == 3
    assert fn.signature == "def add_password(self):"
    assert fn.body == src


def test_leading_comment_as_first_body_line_in_decorated_async_function():
    """async·데코레이터 조합에서도 같은 문제가 없는지 확인하는 대표 케이스 하나."""
    src = source(
        "@deco",
        "async def fetch(url: str) -> None:",
        "    # fetch and discard the result",
        "    await get(url)",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "fetch"
    assert fn.start_line == 1  # 데코레이터 포함
    assert fn.end_line == 4
    assert fn.signature == "async def fetch(url: str) -> None:"
    assert fn.body == src


# --------------------------------------------------------------------------------------
# 구문 오류 정책 (완료 조건: 구문 오류 소스에서도 안정적 동작)
# --------------------------------------------------------------------------------------


def test_syntax_error_in_one_function_does_not_raise_and_others_survive():
    src = source(
        "def good_before():",
        "    return 1",
        "",
        "def broken(:",
        "    return 2",
        "",
        "def good_after(x):",
        "    return x",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["good_before", "good_after"]


def test_syntax_error_inside_function_drops_its_nested_functions_too():
    """경계가 깨진 함수 안의 중첩 함수는, 그 경계 자체를 신뢰할 수 없으므로 함께 버린다."""
    src = source(
        "def broken(:",
        "    def inner():",
        "        return 1",
        "    return inner",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == []


def test_completely_invalid_source_returns_empty_list_without_raising():
    functions = PythonAdapter().extract_functions("@#$% not python at all (((")

    assert functions == []


def test_empty_source_returns_empty_list():
    assert PythonAdapter().extract_functions("") == []


def test_whitespace_only_source_returns_empty_list():
    assert PythonAdapter().extract_functions("   \n\n  ") == []


# --------------------------------------------------------------------------------------
# 라인 번호 규칙 (1-indexed, inclusive)
# --------------------------------------------------------------------------------------


def test_start_line_is_one_indexed_for_first_line_of_file():
    src = source(
        "def at_top():",
        "    pass",
    )
    fn = PythonAdapter().extract_functions(src)[0]

    assert fn.start_line == 1


def test_end_line_matches_last_line_of_multiline_body():
    src = source(
        "def multi():",
        "    x = 1",
        "    y = 2",
        "    return x + y",
    )
    fn = PythonAdapter().extract_functions(src)[0]

    assert fn.start_line == 1
    assert fn.end_line == 4
