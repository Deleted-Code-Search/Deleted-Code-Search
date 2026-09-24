"""PythonAdapter (tree-sitter-python). 담당: 재헌

CHARTER.md §4.2 파서 계층, ADR-001 기준. `pipeline/parsers/base.py`의 계약
(`extract_functions(source_text) -> list[Function]`)을 구현한다.

추출 대상(이슈 #4 완료 조건): 모듈 최상위 함수, 클래스 메서드(데코레이터가 붙은 클래스의
메서드 포함, 이슈 #131), 중첩 함수, `async def`, 데코레이터가 붙은 함수. 클래스 자체는
추출하지 않는다. 본문 정규화는 하지 않는다 — 범위 밖(3주차 AST 정규화 이슈).

라인 번호(1-indexed, inclusive, 데코레이터 포함)와 구문 오류 처리 정책은 `base.py` 독스트링
참조. 이 모듈은 그 정책을 tree-sitter의 `has_error`로 구현한다: 오류와 겹치는 함수 노드는
통째로 버리고(중첩 함수 포함), 그 외에는 부분 결과를 반환한다.
"""

from __future__ import annotations

import tree_sitter as ts
import tree_sitter_python as tspython

from pipeline.parsers.base import Function

_LANGUAGE = ts.Language(tspython.language())


class PythonAdapter:
    """tree-sitter-python 기반 Python 함수 추출기.

    `pipeline/parsers/base.py`에 정의된 공개 계약
    (`extract_functions(source_text) -> list[Function]`)을 구현하는 언어 어댑터다.
    """

    def __init__(self) -> None:
        """tree-sitter Python 파서를 준비한다."""
        self._parser = ts.Parser(_LANGUAGE)

    def extract_functions(self, source_text: str) -> list[Function]:
        """`source_text`에서 함수 정의를 모두 찾아 소스 등장 순서대로 반환한다.

        모듈 최상위 함수, 클래스 메서드, 중첩 함수, `async def`, 데코레이터가 붙은 함수를
        모두 대상으로 한다.

        라인 번호는 1-indexed, inclusive이며 데코레이터가 있으면 그 첫 줄부터 포함한다.
        `body`는 원본 소스를 줄 단위로 그대로 잘라 붙인 것(들여쓰기 포함)이고, `signature`는
        `def`/`async def`부터 끝의 `:`까지이며 데코레이터는 포함하지 않는다. 필드별 정확한
        규칙은 `pipeline/parsers/base.py`의 `Function` 독스트링을 따른다.

        구문 오류가 있는 소스에도 예외를 던지지 않는다: 오류와 겹치는 함수(와 그 안의 중첩
        함수)는 결과에서 제외하고, 오류와 무관한 나머지 함수는 부분 결과로 반환한다. 빈
        문자열이거나 공백만 있으면 빈 리스트를 반환한다.
        """
        if not source_text or not source_text.strip():
            return []
        source_bytes = source_text.encode("utf-8")
        tree = self._parser.parse(source_bytes)
        lines = source_text.split("\n")
        functions: list[Function] = []
        _walk_children(tree.root_node, source_bytes, lines, functions)
        return functions


def _walk_children(
    node: ts.Node, source_bytes: bytes, lines: list[str], out: list[Function]
) -> None:
    """`node`의 자식들을 훑어 함수 정의를 찾는다. 매치된 노드 자체는 재귀하지 않고,

    (데코레이터가 있으면) 안쪽 `function_definition`의 자식들만 이어서 훑어 같은 노드를
    두 번 담지 않는다. 데코레이터가 붙은 클래스는 클래스 자체를 담지 않고, 일반 클래스와
    똑같이 안쪽 `class_definition`을 훑어 그 안의 메서드를 찾는다 (Issue #131).
    """
    for child in node.children:
        if child.type == "decorated_definition":
            inner = _function_definition_child(child)
            if inner is not None:
                if not child.has_error:
                    out.append(_build_function(child, inner, source_bytes, lines))
                    _walk_children(inner, source_bytes, lines, out)
                # has_error 인 경우: 경계를 신뢰할 수 없으므로 안(중첩 함수 포함)을 통째로 버린다
            else:
                definition = child.child_by_field_name("definition")
                if definition is not None and definition.type == "class_definition":
                    _walk_children(definition, source_bytes, lines, out)
        elif child.type == "function_definition":
            if not child.has_error:
                out.append(_build_function(child, child, source_bytes, lines))
                _walk_children(child, source_bytes, lines, out)
            # has_error 인 경우 위와 동일하게 버린다
        else:
            _walk_children(child, source_bytes, lines, out)


def _function_definition_child(node: ts.Node) -> ts.Node | None:
    """`decorated_definition` 노드의 직계 자식 중 실제 `function_definition`을 찾는다."""
    for child in node.children:
        if child.type == "function_definition":
            return child
    return None


def _build_function(
    outer: ts.Node, header: ts.Node, source_bytes: bytes, lines: list[str]
) -> Function:
    """`outer`는 라인 범위·body용(데코레이터 포함 가능), `header`는 이름·signature용
    (`function_definition`).

    body 는 바이트 오프셋이 아니라 `lines`에서 통째로 잘라 붙인다 — tree-sitter 노드의
    `start_byte`는 들여쓰기 공백 앞이 아니라 `def`/`@` 토큰부터 시작하므로, 바이트 슬라이스로는
    첫 줄의 들여쓰기가 잘려나가 diff의 삭제 헝크(줄 단위, 들여쓰기 포함)와 어긋난다.
    """
    name_node = header.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")

    body_node = header.child_by_field_name("body")
    colon = _colon_before_body(header, body_node)
    signature = source_bytes[header.start_byte : colon.end_byte].decode("utf-8").strip()

    start_line = outer.start_point[0] + 1
    end_line = outer.end_point[0] + 1
    body = "\n".join(lines[start_line - 1 : end_line])

    return Function(
        name=name,
        start_line=start_line,
        end_line=end_line,
        body=body,
        signature=signature,
    )


def _colon_before_body(header: ts.Node, body_node: ts.Node) -> ts.Node:
    """`function_definition`의 직계 자식 중 body(block) 바로 앞 `:` 토큰을 찾는다.

    body 바로 앞 자식이 곧 `:`라고 가정하지 않는다 — 함수 본문 첫 줄이 독립된 `#` 주석이면
    tree-sitter-python이 그 주석을 `block` 밖, `:`와 `block` 사이에 형제 노드로 끼워 넣는다
    (Issue #5 E2E, psf/requests `add_password` 재현: `def`/`identifier`/`parameters`/`:`/
    `comment`/`block` 순서). 그래서 body보다 앞쪽 자식들을 역순으로 훑어 **가장 가까운**
    `:`를 signature 종료 지점으로 삼는다 — 이 함수의 목적이 그거다. 전체 자식 중 첫 `:`를
    고르지 않는 이유: 매개변수 기본값 등에 `:`가 더 나올 일은 없지만(타입 애너테이션 콜론은
    `parameters` 서브트리 안에 있어 여기 안 섞인다), "가장 가까운"이 의도를 더 정확히 담는다.
    """
    children = header.children
    body_index = children.index(body_node)
    for child in reversed(children[:body_index]):
        if child.type == ":":
            return child
    raise AssertionError(f"function_definition에 body 앞 ':' 토큰이 없다: {header}")
