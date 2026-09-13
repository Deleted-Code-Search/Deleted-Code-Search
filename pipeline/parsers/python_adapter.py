"""PythonAdapter (tree-sitter-python). 담당: 재헌

CHARTER.md §4.2 파서 계층, ADR-001 기준. `pipeline/parsers/base.py`의 계약
(`extract_functions(source_text) -> list[Function]`)을 구현한다.

추출 대상(이슈 #4 완료 조건): 모듈 최상위 함수, 클래스 메서드, 중첩 함수, `async def`,
데코레이터가 붙은 함수. 본문 정규화는 하지 않는다 — 범위 밖(3주차 AST 정규화 이슈).

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
    """tree-sitter-python 기반 Python 함수 추출기."""

    def __init__(self) -> None:
        self._parser = ts.Parser(_LANGUAGE)

    def extract_functions(self, source_text: str) -> list[Function]:
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
    두 번 담지 않는다.
    """
    for child in node.children:
        if child.type == "decorated_definition":
            inner = _function_definition_child(child)
            if inner is not None and not child.has_error:
                out.append(_build_function(child, inner, source_bytes, lines))
                _walk_children(inner, source_bytes, lines, out)
            # has_error 인 경우: 경계를 신뢰할 수 없으므로 안(중첩 함수 포함)을 통째로 버린다
        elif child.type == "function_definition":
            if not child.has_error:
                out.append(_build_function(child, child, source_bytes, lines))
                _walk_children(child, source_bytes, lines, out)
            # has_error 인 경우 위와 동일하게 버린다
        else:
            _walk_children(child, source_bytes, lines, out)


def _function_definition_child(node: ts.Node) -> ts.Node | None:
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

    tree-sitter-python 문법에서 `function_definition`의 마지막 두 자식은 항상
    `: block` 순서다. has_error 노드는 이미 걸러졌으므로 이 가정이 깨지지 않는다.
    """
    children = header.children
    colon = children[children.index(body_node) - 1]
    assert colon.type == ":"  # 문법이 바뀌면 여기서 먼저 드러나야 한다
    return colon
