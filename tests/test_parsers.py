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
# 회귀: 데코레이터가 붙은 클래스의 메서드 (Issue #131, #80 filter-miss 조사 중 발견)
#
# `@dataclass class` 는 tree-sitter에서 `decorated_definition` 아래 `class_definition` 으로
# 온다. `_walk_children` 이 `decorated_definition` 안에서 `function_definition` 만 찾고, 없으면
# 서브트리를 통째로 버려 클래스 안 메서드가 하나도 나오지 않았다. 클래스 자체는 여전히
# 추출 대상이 아니다. 메서드의 줄 범위는 메서드 자신의 데코레이터부터이고 클래스
# 데코레이터는 들어가지 않는다.
# --------------------------------------------------------------------------------------


def test_decorated_class_method_extraction():
    src = source(
        "@dataclass",
        "class A:",
        "    def method(self, x):",
        "        return x",
    )
    functions = PythonAdapter().extract_functions(src)

    assert len(functions) == 1
    fn = functions[0]
    assert fn.name == "method"
    assert fn.start_line == 3  # 클래스 데코레이터(1행)는 메서드 범위에 들어가지 않는다
    assert fn.end_line == 4
    assert fn.signature == "def method(self, x):"
    assert fn.body == "    def method(self, x):\n        return x"


def test_multiple_decorators_on_class_with_async_method():
    src = source(
        "@deco1",
        "@deco2(arg=1)",
        "class A:",
        "    def first(self):",
        "        return 1",
        "",
        "    async def second(self) -> None:",
        "        await get()",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["first", "second"]
    assert (functions[0].start_line, functions[0].end_line) == (4, 5)
    assert (functions[1].start_line, functions[1].end_line) == (7, 8)
    assert functions[1].signature == "async def second(self) -> None:"


def test_decorated_method_inside_decorated_class_is_found_once():
    src = source(
        "@dataclass",
        "class A:",
        "    @staticmethod",
        "    def helper():",
        "        return 1",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["helper"]  # decorated_definition 과 function_definition 중복 없음
    fn = functions[0]
    assert fn.start_line == 3  # 메서드 자신의 데코레이터부터, 클래스 데코레이터는 제외
    assert fn.end_line == 5
    assert fn.signature == "def helper():"
    assert fn.body == "    @staticmethod\n    def helper():\n        return 1"


def test_decorated_method_inside_plain_class_is_found_once():
    src = source(
        "class A:",
        "    @property",
        "    def value(self):",
        "        return 1",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["value"]
    assert functions[0].start_line == 2
    assert functions[0].end_line == 4
    assert functions[0].signature == "def value(self):"


def test_methods_of_nested_plain_and_decorated_classes():
    src = source(
        "class Outer:",
        "    class Plain:",
        "        def a(self):",
        "            return 1",
        "",
        "    @dataclass",
        "    class Decorated:",
        "        def b(self):",
        "            return 2",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["a", "b"]
    assert (functions[0].start_line, functions[0].end_line) == (3, 4)
    assert (functions[1].start_line, functions[1].end_line) == (8, 9)


def test_method_of_decorated_class_defined_inside_function():
    src = source(
        "def factory():",
        "    @dataclass(frozen=True)",
        "    class A:",
        "        def method(self):",
        "            return 1",
        "    return A",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["factory", "method"]
    assert (functions[0].start_line, functions[0].end_line) == (1, 6)
    assert (functions[1].start_line, functions[1].end_line) == (4, 5)


def test_decorated_class_in_test_function_pydantic_137d4d8393_shape():
    """pydantic 137d4d8393 `tests/test_examples.py`(부모)·`tests/test_annotated.py`(자식) 축약.

    삭제 쪽: 테스트 함수 안의 중첩 함수 `my_validator_function`(record 1b0b6cb9)과 같은
    함수 안 `@dataclass(frozen=True)` 클래스의 메서드. 자식 쪽 대응 코드
    `MyDatetimeValidator.tz_constraint_validator`는 그 클래스의 메서드라 수정 전에는 이동
    목적지 후보에 없었다. 이 커밋은 본문을 다시 쓴 것이라 이동 판정은 여전히 아니다 —
    여기서는 추출 여부만 본다.
    """
    parent = source(
        "def test_tzinfo_validator_example_pattern() -> None:",
        "    def my_validator_function(",
        "        tz_constraint: Union[str, None],",
        "        value: dt.datetime,",
        "        handler: Callable,",
        "    ):",
        "        return handler(value)",
        "",
        "    @dataclass(frozen=True)",
        "    class MyDatetimeValidator:",
        "        tz_constraint: Optional[str] = None",
        "",
        "        def __get_pydantic_core_schema__(",
        "            self,",
        "            source_type: Any,",
        "            handler: GetCoreSchemaHandler,",
        "        ) -> CoreSchema:",
        "            return handler(source_type)",
    )
    child = source(
        "def test_tzinfo_validator_example_pattern() -> None:",
        "    @dataclass(frozen=True)",
        "    class MyDatetimeValidator:",
        "        tz_constraint: Optional[str] = None",
        "",
        "        def tz_constraint_validator(",
        "            self,",
        "            value: dt.datetime,",
        "            handler: Callable,  # (1)!",
        "        ):",
        "            return handler(value)",
    )

    parent_functions = PythonAdapter().extract_functions(parent)
    assert names(parent_functions) == [
        "test_tzinfo_validator_example_pattern",
        "my_validator_function",
        "__get_pydantic_core_schema__",
    ]
    assert (parent_functions[1].start_line, parent_functions[1].end_line) == (2, 7)
    assert (parent_functions[2].start_line, parent_functions[2].end_line) == (13, 18)

    child_functions = PythonAdapter().extract_functions(child)
    assert names(child_functions) == [
        "test_tzinfo_validator_example_pattern",
        "tz_constraint_validator",
    ]
    target = child_functions[1]
    assert (target.start_line, target.end_line) == (6, 11)
    assert target.signature == (
        "def tz_constraint_validator(\n"
        "            self,\n"
        "            value: dt.datetime,\n"
        "            handler: Callable,  # (1)!\n"
        "        ):"
    )


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


def test_syntax_error_in_one_method_of_decorated_class_keeps_other_methods():
    """데코레이터 클래스도 일반 클래스처럼 메서드 단위로 오류를 판정한다 (Issue #131).

    클래스는 `Function`이 아니므로 클래스 전체를 버리지 않는다 — 깨진 메서드만 빠진다.
    """
    src = source(
        "@dataclass",
        "class A:",
        "    def good_before(self):",
        "        return 1",
        "",
        "    def broken(self, :",
        "        return 2",
        "",
        "    def good_after(self):",
        "        return 3",
    )
    functions = PythonAdapter().extract_functions(src)

    assert names(functions) == ["good_before", "good_after"]


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
