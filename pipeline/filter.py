"""의미 있는 삭제 필터 (이동·리네임·포맷·대량·생성코드·사소한 부분 삭제 제외). 담당: 재헌

Issue #63 구현: NOISE_TRIVIAL(§4.2②, ADR-015) — PARTIAL 중 `len(deleted_hunk.splitlines())`
가 4줄 이하인 것을 제외한다(`partition_trivial`). 아래 "NOISE_TRIVIAL" 절 참고.

Issue #52 구현: NOISE_MOVE(§4.2②) — 같은 커밋에서 삭제된 함수와 실제로 추가된 함수의
정규화 본문을 비교해, 이동인 삭제를 최종 레코드에서 제외한다. 정규화·유사도·후보 범위는
ADR-014(`docs/adr/014-move-detection-rules.md`)가 최종 기준이다 — 이 모듈은 그 결정
1·2·3을 그대로 구현한다. `docs/filter_rules.md` NOISE_MOVE 절은 ADR-014와 아래 §4.2②
자체는 정하지 않는 #52 구현 세부사항(같은 위치 판정 방법, 1:1 매칭 알고리즘)을 함께 담는다.

**최우선 원칙(코드 리뷰 BLOCKER/HIGH 수정 라운드에서 팀이 확정, ADR-014 예정)**: 오탐
(실제 삭제를 이동으로 잘못 제외)보다 미탐(이동 노이즈를 못 걸러냄)이 낫다. 미탐은 사람
라벨링 단계에서 걸러낼 수 있지만, 오탐은 데이터셋에서 그 레코드를 영구히 지운다. 애매한
경우 삭제 레코드를 **유지**하는 쪽으로 판단한다 — 아래 후보 판정·1:1 매칭이 전부 이
원칙을 구현한 것이다.

정규화(`normalize_function_body`, ADR-014 결정 1): AST(tree-sitter) 기반. 반환값은
**정규화된 줄의 리스트**(`list[str]`)다 — 원본 줄 경계를 유지한 채 각 줄 안의 공백·
들여쓰기만 정규화한다(토큰을 그 줄 안에서 공백 하나로 이어 붙임). 결정 2의 "줄 목록"
비교가 이 표현을 그대로 쓴다.
    - 일반 식별자(변수명·파라미터명) → `VAR`
    - 문자열 리터럴 → `STR`, 숫자 리터럴(정수·부동소수점·복소수) → `NUM`
    - 다른 함수의 본문에 포함된 **중첩 함수 선언의 이름** → `VAR`(일반 식별자와 동일하게
      취급) — 그 중첩 함수의 나머지(매개변수·본문)는 건너뛰지 않고 보통 규칙대로 재귀
      정규화한다(정규화가 중첩 함수 서브트리를 통째로 들어내지 않는다: outer가
      이동했는지와 nested가 이동했는지는 서로 다른 질문이고, 본문을 빼면 서로 다른
      outer 함수가 같아 보여 오탐이 늘어난다). 별도로 추출된 그 중첩 함수 자신을
      정규화할 때는(즉 `normalize_function_body`가 그 함수의 본문으로 다시 불릴 때는)
      그때는 "현재 비교 대상 함수의 이름"이 되어 보존된다 — ADR-014 "중첩 함수 이름의
      범위" 참고, `_find_root_function`.
    - 보존: 현재 비교 대상 함수 자신의 이름(`function_definition`의 최상위 노드),
      attribute 이름, 호출 대상 이름
    - 제거: 주석, docstring
    - `bool`/`None` 리터럴은 치환하지 않고 원문 그대로 남긴다(원래 의미 유지 — 과도한
      치환을 피하는 정밀도 우선 원칙, 팀장 최종 확인). ADR-014 결정 1 본문에는 아직
      없지만 팀이 확정했고, ADR-014 반영은 팀장이 별도로 한다.
    regex/text 치환이 아니라 AST 노드 종류·위치로 판단한다(아래 `_normalize_node`).

    정규화 결과는 이동 탐지 계산에만 쓰고 JSONL/DB에 새 필드로 내보내지 않는다.

유사도(`_move_similarity`, ADR-014 결정 2·3): 큰 이동 커밋(파일 수백~수천 개)에서
삭제 함수 × 추가 함수 전수 SequenceMatcher 호출을 피하려고 ADR-014가 정한 순서를
그대로 따른다:
    0. 정규화 줄 목록이 어느 한쪽이라도 0줄이면 후보가 아니다(`None`) — ADR-014 결정 3
       "정규화 본문이 0줄인 함수는 이동 후보에서 제외한다".
    1. 정규화 본문의 **줄 수** 차이가 20%를 **넘으면**(`(긴 쪽 − 짧은 쪽) / 긴 쪽 > 0.2`)
       비교 자체를 건너뛴다(정확히 20%는 비교 대상으로 남긴다). ADR-014 결정 3
       "1차 거르기" — `ratio() = 2M/(a+b)`이고 `M ≤` 짧은 쪽이므로, 차이가 긴 쪽의
       20%를 넘으면 ratio 상한이 약 0.889로 0.9에 못 미친다.
    2. 정규화 줄 목록이 완전히 같으면(해시로 먼저 비교) similarity 1.0.
    3. 아니면 `difflib.SequenceMatcher(None, 삭제_줄목록, 추가_줄목록, autojunk=False).ratio()`
       — 비교 단위는 **정규화 본문의 줄 목록**(문자열 전체가 아니다), 인자 순서는
       (삭제, 추가)로 고정, `autojunk=False`(기본값 `True`는 200줄 이상에서 자주
       나오는 줄을 무시해 긴 함수의 비율을 실제보다 낮게 낸다 — ADR-014 결정 2).
    4. ratio < 0.9면 짝 후보가 아니다(`None`). threshold는 고정이다.
    해시는 exact 비교를 빠르게 하려는 내부 최적화일 뿐 외부에 노출하지 않는다.

이동 후보(added-side) 판정 — `pipeline.extract.collect_added_functions()`가 이미
"실제 added line과 겹치는 함수"로 걸러서 준다(Issue #52 B-1 수정, `extract.py` 모듈
독스트링 참고). 부모 커밋부터 있었고 이번 diff와 전혀 안 겹치는 함수(이번 커밋에서
손 안 댐)는 애초에 이 dict에 들어있지 않다 — 이 모듈은 그걸 다시 걸러내지 않는다.
ADR-014는 이 세부 규칙을 정하지 않았고(#52 구현에 위임), 결정 3의 "같은 커밋에서
추가된 함수"라는 후보 풀 자체와 모순되지 않는다.

삭제 후보(deleted-side) — `deletion_kind == "FULL_FUNCTION"`만 후보로 삼는다. PARTIAL은
함수 일부만 지워진 것이라 "함수가 통째로 옮겨졌다"는 이동의 정의 자체와 맞지 않고,
`deleted_hunk`도 PARTIAL이면 실제로 지워진 조각일 뿐 함수 전체 원문이 아니어서 비교
대상으로 쓸 수 없다(§4.4 `deleted_body`의 뜻, `extract.py` 모듈 독스트링 "JSONL 저장"
절 참고) — PARTIAL 필터링 정책 자체를 새로 만드는 게 아니라(그건 범위 밖), FULL_FUNCTION
만 이 필터가 다룬다는 뜻이다. FULL_FUNCTION이면 `deleted_hunk`가 부모 파일의 함수 본문
전체와 같다(모든 줄이 삭제 줄이므로).

같은 경로 후보 처리(§4.2②의 "다른 파일/위치에 추가됨"에서 "위치"도 실제로 본다):
    - 다른 `file_path` → 항상 후보
    - 같은 `file_path` + **다른 위치**(같은 파일 안에서 다른 자리로 옮겨감) → 후보
    - 같은 `file_path` + **같은 위치**(제자리 수정) → 후보에서 제외. 10줄짜리 함수를
      1줄만 고쳐도 SequenceMatcher ratio가 0.9를 넘을 수 있어서, 이 구분을 유사도 계산
      *전에*, 짝(pair) 자체를 만들기 전에 해야 한다(`_is_same_position` 참고).

    "같은 위치"는 diff 헝크 매핑으로 판정한다(`pipeline.extract.collect_same_file_hunks()`,
    `_is_same_position`) — 단순 `start_line == start_line` 비교는 함수 위쪽에 무관한
    삽입·삭제가 있으면 줄 번호가 밀려 틀리기 때문에 쓰지 않는다. 판정 방식의 근거·한계는
    `docs/filter_rules.md` NOISE_MOVE 절에 기록했다. added 후보 자체가 이제 "실제 added
    line과 겹치는 함수"로 제한되지만, same-position 판정과 겹치거나 충돌하지 않는다 —
    둘은 서로 다른 질문에 답한다("이게 진짜 추가된 함수인가" vs "그 추가가 제자리인가").
    실제로 이동한 함수(같은 위치든 다른 위치든)는 정의상 그 새 위치의 줄들이 added
    line이므로 두 조건 모두에서 자연히 candidate로 남는다.

1:1 greedy 매칭(`find_moved`, 팀 추가 결정): 이동은 정의상 삭제 함수 1개 : 추가 함수
1개 관계다. 하나의 added function이 여러 deleted function을 동시에 설명하면 안 된다
(코드 리뷰 HIGH — 동일/유사 함수가 같은 커밋에 여러 번 지워지고 진짜 이동 후보는
하나뿐인데 전부 이동으로 판정되던 문제). 최적 bipartite matching은 과하므로, 유사도
내림차순 greedy로 충분하다고 팀이 확정했다:
    1. (삭제, 추가) 유효한 pair를 전부 만든다 — same-position pair는 여기서 제외(위 참고).
    2. 각 pair의 유사도를 `_move_similarity`로 계산, `None`(20% 프리필터 탈락 또는
       ratio < 0.9)인 pair는 버린다.
    3. 남은 pair를 유사도 내림차순으로 정렬한다. 정규화 유사도가 같으면 원문 유사도
       (`_raw_similarity` — 줄마다 strip·빈 줄 제거한 원문 줄 목록의 SequenceMatcher,
       Issue #80) 내림차순. 이것은 threshold가 아니라 정렬 순서만 정한다. 그것까지 같으면
       deterministic 하게: 삭제 쪽 key(`_record_key` — `commit_sha, file_path,
       start_line, end_line`) 오름차순, 그다음 추가 쪽 key(`_candidate_key` —
       `file_path, start_line, end_line`) 오름차순 — 입력 순서(dict 순회 순서 등)에
       결과가 흔들리지 않게 한다.
    4. 정렬된 순서대로, 삭제·추가 양쪽 다 아직 안 쓰였으면 그 pair를 확정하고 둘 다
       "소비(consumed)" 처리한다. 둘 중 하나라도 이미 쓰였으면 건너뛴다.
    5. 짝을 찾은 삭제만 NOISE_MOVE로 제외 대상이다. 짝을 못 찾은 삭제는 KEPT.

filter_status: CHARTER §4.4에 이미 정의된 enum(NOISE_MOVE 포함)을 그대로 쓴다. 새
enum이나 스키마를 만들지 않는다. 최종 `DeletionRecord` 조립(context·reason·filter_status를
한 레코드로 합치는 단계)은 아직 어디에도 없고 이 모듈도 만들지 않는다.

제외 레코드 보존(Issue #97, `docs/filter_rules.md` "제외 레코드 보존" 절): 제외된
레코드를 버리지 않는다. `partition_moved`가 (남은 레코드, 제외 레코드)를 함께 돌려주고,
제외 레코드는 `pipeline.extract.ExcludedRecord`로 `filter_status`(`NOISE_MOVE`)·
`filter_rule_version`(`FILTER_RULE_VERSION`)·`filter_evidence`(이동 목적지와 유사도)를
붙여 둔다. 추출 JSONL에는 이 세 필드를 넣지 않는다 — 제외 레코드는 호출자가 별도
excluded JSONL(`pipeline.extract.write_excluded_jsonl`)로 쓴다. `find_moved`·
`exclude_moved`는 Issue #97 이전 공개 API를 그대로 유지하는 래퍼이고, 셋 다 같은 매칭
본체(`_match_moved`)를 한 번만 부른다 — 판정 결과는 Issue #97 이전과 같다.

NOISE_TRIVIAL(Issue #63, ADR-015, `partition_trivial`): `deletion_kind == "PARTIAL"`이고
`len(deleted_hunk.splitlines()) <= 4`면 제외한다. FULL_FUNCTION은 대상이 아니다. 줄 수는
보정 없이 `splitlines()` 그대로라 마지막 삭제 줄이 빈 줄이면 1 적게 센다(팀 결정,
`docs/filter_rules.md` NOISE_TRIVIAL 절 "한계"). 제외 레코드는 NOISE_MOVE와 같은
`ExcludedRecord`에 `filter_evidence={"line_count": 줄 수}`로 담는다. 이동 판정 3개 API
(`find_moved`·`partition_moved`·`exclude_moved`)는 NOISE_MOVE만 다루는 계약 그대로다 —
두 필터를 함께 적용하는 곳은 `pipeline.extract.extract_repo_with_excluded`다. NOISE_MOVE는
FULL_FUNCTION만, NOISE_TRIVIAL은 PARTIAL만 보므로 제외 대상이 겹치지 않는다.

**ADR-014 정합 라운드에서 코드로 먼저 구현하고 팀장이 최종 확인한 사항** (ADR-014
본문에는 아직 없지만 확정됐고, ADR-014 반영은 팀장이 별도로 한다):
- `bool`/`None` 리터럴은 치환하지 않고 원문 보존, 복소수는 숫자 리터럴이라 `NUM` — 위
  "정규화" 절 그대로.
- 중첩 함수를 "바깥 함수 본문 안"과 "별도로 추출된 자기 자신"으로 구분하는 기준으로
  `_find_root_function`(정규화 대상 텍스트의 최상위 `function_definition`)을 썼다.
  outer normalization은 중첩 함수 서브트리를 제외하지 않는다(이름만 VAR로 치환하고
  본문은 재귀 정규화) — ADR-014 결정 1의 "별도로 추출된 중첩 함수를 비교할 때: 자신의
  이름을 대상 함수 이름으로 취급해 보존한다"를 그대로 구현한 것이다.

규칙 변경 시 테스트 + 정밀도 재측정 필수 (§8.4).
기준: CHARTER.md §4.2 ②, ADR-014, docs/filter_rules.md
"""

from __future__ import annotations

import hashlib
import itertools
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import tree_sitter as ts
import tree_sitter_python as tspython

from pipeline.extract import DeletedFunction, ExcludedRecord, Hunk
from pipeline.parsers.base import Function

_LANGUAGE = ts.Language(tspython.language())
_PARSER = ts.Parser(_LANGUAGE)

# CHARTER §4.2② 확정값. 바꾸지 않는다 (Issue #52 팀 결정).
SIMILARITY_THRESHOLD = 0.9
LINE_COUNT_SKIP_RATIO = 0.20

# excluded JSONL의 `filter_rule_version` (Issue #97). `docs/filter_rules.md` 첫 줄의
# "버전:"과 항상 같아야 한다 — 문서 버전을 올리면 이 값도 같은 PR에서 올린다
# (`tests/test_filter.py`가 둘이 같은지 확인한다).
FILTER_RULE_VERSION = "v0.7"

# ADR-015 확정값: PARTIAL의 `deleted_hunk` 줄 수가 이보다 작으면(4줄 이하) NOISE_TRIVIAL.
PARTIAL_MIN_LINES = 5

# CHARTER §4.4 `filter_status` enum 값. 새 이름을 만들지 않는다.
NOISE_MOVE = "NOISE_MOVE"
NOISE_TRIVIAL = "NOISE_TRIVIAL"

_VAR_PLACEHOLDER = "VAR"
_STR_PLACEHOLDER = "STR"
_NUM_PLACEHOLDER = "NUM"

# ADR-014 결정 1: 문자열 리터럴 → STR, 숫자 리터럴 → NUM. concatenated_string("a" "b"
# 같은 인접 문자열 리터럴)도 문자열 리터럴이라 STR. f-string 내부(string_start/
# string_content/interpolation/string_end)는 "string" 노드에서 재귀를 멈추므로 따로
# 다루지 않는다 — f-string 전체가 STR 하나로 치환된다.
_STRING_LITERAL_NODE_TYPES = frozenset({"string", "concatenated_string"})
# complex(허수)는 ADR-014에 이름은 없지만 "숫자 리터럴"에 속한다(정수·부동소수점과
# 같은 카테고리) — 새 분류를 만드는 게 아니라 기존 NUM 범주를 적용하는 것이다.
_NUMBER_LITERAL_NODE_TYPES = frozenset({"integer", "float", "complex"})
# true/false/none(bool/None)은 STR·NUM 어느 쪽으로도 치환하지 않는다 — 원래 의미를
# 유지하는 쪽이 과도한 치환보다 안전하다는 팀 결정(모듈 독스트링 "ADR-014 정합 라운드
# 팀장 최종 확인" 참고). 이 노드 타입에는 아예 손대지 않는다(자식 없는 리프로 원문
# 그대로 남는다).
_DOCSTRING_PARENT_TYPES = frozenset({"block", "module"})


# --------------------------------------------------------------------------------------
# 정규화 — AST 기반, tree-sitter-python 재사용 (pipeline/parsers/base.py 인터페이스는
# 건드리지 않는다 — 여기서는 PythonAdapter의 Function 경계 추출 계약이 아니라 tree-sitter
# 파서 자체만 직접 쓴다)
# --------------------------------------------------------------------------------------


def _same_node(a: ts.Node | None, b: ts.Node) -> bool:
    """두 노드가 같은 위치를 가리키나. tree-sitter 노드 wrapper 객체 동일성 대신 바이트
    범위로 비교한다(바인딩 버전에 덜 의존적)."""
    return a is not None and a.start_byte == b.start_byte and a.end_byte == b.end_byte


def _is_docstring_statement(node: ts.Node) -> bool:
    """`node`(`expression_statement`)가 그 블록/모듈의 첫 statement이면서 문자열 리터럴
    하나로만 이루어진 docstring인가."""
    if node.type != "expression_statement" or len(node.children) != 1:
        return False
    if node.children[0].type != "string":
        return False
    parent = node.parent
    if parent is None or parent.type not in _DOCSTRING_PARENT_TYPES:
        return False
    for sibling in parent.children:
        if sibling.type == "comment":
            continue  # 주석은 건너뛰고 그다음 실제 statement가 첫 줄인지 본다
        return _same_node(sibling, node)
    return False


def _find_root_function(node: ts.Node) -> ts.Node | None:
    """`node`(파싱 트리의 최상위, 보통 `module`)의 직계 자식 중 "정규화 대상 함수 자신"의
    `function_definition`을 찾는다 (ADR-014 결정 1 "중첩 함수 이름의 범위").

    `normalize_function_body`에 넘기는 `source`는 항상 함수 하나(데코레이터 포함 가능)의
    텍스트이므로, 최상위에서 처음 만나는 `function_definition`이 곧 "현재 비교 대상
    함수"다. 그 함수 본문 "안"에 있는 다른 `function_definition`(중첩 함수)은 이 탐색
    대상이 아니다 — 얕은 탐색(직계 자식만)으로 충분하고, `python_adapter.py`의
    `_function_definition_child`와 같은 패턴이다.
    """
    for child in node.children:
        if child.type == "function_definition":
            return child
        if child.type == "decorated_definition":
            for grandchild in child.children:
                if grandchild.type == "function_definition":
                    return grandchild
    return None


def _normalize_node(
    node: ts.Node, root_function: ts.Node | None, entries: list[tuple[int, str]]
) -> None:
    """`node`를 정규화 토큰으로 펼쳐 `(원본 줄 번호, 토큰)`을 `entries`에 이어붙인다
    (모듈 독스트링 "정규화" 참고, ADR-014 결정 1).

    "현재 비교 대상 함수"(`root_function` — `_find_root_function`이 찾은, 이 정규화
    호출 전체가 다루는 그 함수 자신) 자신의 이름만 보존한다. 그 안에 중첩된 다른
    `function_definition`의 이름은 이 규칙에서 빠지므로 일반 identifier와 마찬가지로
    `_normalize_node`의 `identifier` 분기를 타 `VAR`가 된다 — 중첩 함수의 나머지
    부분(매개변수·본문)은 그대로 재귀해 보통 규칙을 적용한다(중첩 함수를 통째로
    건너뛰지 않는다). attribute 이름·직접 호출 이름은 중첩 여부와 무관하게 항상 보존한다.
    나머지 identifier는 `VAR`, 문자열 리터럴은 `STR`, 숫자 리터럴은 `NUM`, 주석과
    docstring은 통째로 건너뛴다(토큰을 만들지 않음). 그 외 리프(키워드·연산자·구두점,
    `true`/`false`/`none`(bool/None) 포함 — 치환하지 않고 원문 유지, 모듈 독스트링
    참고)는 원문 그대로.
    """
    node_type = node.type

    if node_type == "comment":
        return

    if node_type in _STRING_LITERAL_NODE_TYPES:
        entries.append((node.start_point[0], _STR_PLACEHOLDER))
        return

    if node_type in _NUMBER_LITERAL_NODE_TYPES:
        entries.append((node.start_point[0], _NUM_PLACEHOLDER))
        return

    if node_type == "expression_statement" and _is_docstring_statement(node):
        return

    if node_type == "function_definition":
        is_root = _same_node(root_function, node)
        name = node.child_by_field_name("name") if is_root else None
        for child in node.children:
            if is_root and _same_node(name, child):
                entries.append((child.start_point[0], child.text.decode("utf-8")))
            else:
                _normalize_node(child, root_function, entries)
        return

    if node_type == "attribute":
        attr = node.child_by_field_name("attribute")
        for child in node.children:
            if _same_node(attr, child):
                entries.append((child.start_point[0], child.text.decode("utf-8")))
            else:
                _normalize_node(child, root_function, entries)
        return

    if node_type == "call":
        func = node.child_by_field_name("function")
        direct_call_name = func is not None and func.type == "identifier"
        for child in node.children:
            if direct_call_name and _same_node(func, child):
                entries.append((child.start_point[0], child.text.decode("utf-8")))
            else:
                _normalize_node(child, root_function, entries)
        return

    if node_type == "identifier":
        entries.append((node.start_point[0], _VAR_PLACEHOLDER))
        return

    if node.child_count == 0:
        text = node.text.decode("utf-8")
        if text:
            entries.append((node.start_point[0], text))
        return

    for child in node.children:
        _normalize_node(child, root_function, entries)


def normalize_function_body(source: str) -> list[str]:
    """함수 본문(또는 임의의 파이썬 소스 조각)을 이동 탐지용으로 정규화한다.

    ADR-014 결정 2가 요구하는 **줄 목록**을 돌려준다: tree-sitter로 파싱한 뒤
    `_normalize_node`가 만든 `(원본 줄 번호, 토큰)`을 원본 줄 번호로 묶어, 그 줄에서
    나온 토큰들을 공백 하나로 이어 붙인 문자열을 줄 순서대로 리스트에 담는다. 토큰을
    하나도 만들지 않은 줄(빈 줄, 주석·docstring만 있던 줄)은 리스트에 아예 없다 —
    "공백·들여쓰기 정규화"의 결과다. 빈 입력이면 빈 리스트를 돌려준다.
    """
    if not source or not source.strip():
        return []
    tree = _PARSER.parse(source.encode("utf-8"))
    root_function = _find_root_function(tree.root_node)
    entries: list[tuple[int, str]] = []
    _normalize_node(tree.root_node, root_function, entries)
    lines: dict[int, list[str]] = {}
    for row, token in entries:
        lines.setdefault(row, []).append(token)
    return [" ".join(lines[row]) for row in sorted(lines)]


# --------------------------------------------------------------------------------------
# 유사도 — ADR-014 결정 2·3 순서 (모듈 독스트링 "유사도" 참고)
# --------------------------------------------------------------------------------------


def _line_count_diff_ratio(a_lines: int, b_lines: int) -> float:
    """ADR-014 결정 3 "1차 거르기" 식: `(긴 쪽 − 짧은 쪽) / 긴 쪽`.

    `a_lines`/`b_lines`는 **정규화 본문의 줄 수**다(원본 줄 수가 아니다). 둘 다 0이면
    호출자가 먼저 0줄 후보를 걸러낸다는 전제라(`_move_similarity`) 여기서는 방어적으로만
    0.0을 돌려준다 — ADR-014 결정 3 "0줄 본문은 위 규칙으로 먼저 제외하므로 이 식의
    분모(긴 쪽 줄 수)는 0이 되지 않는다"에 따라 실제로는 이 분기를 안 타야 정상이다.
    """
    longer = max(a_lines, b_lines)
    if longer == 0:
        return 0.0
    return abs(a_lines - b_lines) / longer


def _body_hash(lines: list[str]) -> str:
    """정규화 줄 목록의 exact-match를 빠르게 비교하기 위한 내부 해시. 외부에 노출하지
    않는다. 정규화된 각 줄은 개행 문자를 포함할 수 없으므로(토큰을 공백으로만 이어
    붙임) `\\n`으로 합쳐도 줄 경계가 모호해지지 않는다."""
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _NormalizedBody:
    """정규화 결과(ADR-014 결정 1의 줄 목록) + 비교에 필요한 부가값. 이동 판정 내부
    계산에만 쓴다."""

    lines: list[str]
    line_count: int
    body_hash: str


def _normalize(raw_body: str) -> _NormalizedBody:
    lines = normalize_function_body(raw_body)
    return _NormalizedBody(lines, len(lines), _body_hash(lines))


def _move_similarity(deleted: _NormalizedBody, candidate: _NormalizedBody) -> float | None:
    """ADR-014 결정 2·3 순서: 0줄 제외 → 정규화 줄 수 20% 프리필터 → 해시 exact(1.0)
    → line-list SequenceMatcher(autojunk=False) → threshold.

    짝(pair) 후보가 아니면(0줄, 프리필터 탈락, 또는 ratio < 0.9) `None` — 1:1 greedy
    매칭(`find_moved`)이 정렬할 실제 유사도 값이 필요해서 bool이 아니라 `float | None`을
    돌려준다."""
    if deleted.line_count == 0 or candidate.line_count == 0:
        return None  # ADR-014 결정 3: 정규화 본문 0줄 함수는 이동 후보에서 제외
    if _line_count_diff_ratio(deleted.line_count, candidate.line_count) > LINE_COUNT_SKIP_RATIO:
        return None
    if deleted.body_hash == candidate.body_hash:
        return 1.0
    ratio = SequenceMatcher(None, deleted.lines, candidate.lines, autojunk=False).ratio()
    return ratio if ratio >= SIMILARITY_THRESHOLD else None


def _raw_lines(raw_body: str) -> list[str]:
    """원문 유사도(Issue #80)용 줄 목록: 줄마다 `strip()`, 빈 줄 제거. 정규화하지 않는다."""
    return [stripped for line in raw_body.splitlines() if (stripped := line.strip())]


def _raw_similarity(deleted_lines: list[str], candidate_lines: list[str]) -> float:
    """정규화 유사도 동점을 깨는 2차 정렬 key (Issue #80). 이동 판정 기준이 아니다.

    `_move_similarity`와 같은 방식(줄 목록, 인자 순서 (삭제, 추가), `autojunk=False`)으로
    원문 줄 목록을 비교한다. `_match_moved`가 정규화 유사도 동점 그룹 안에서 아직 안 쓰인
    삭제·추가를 실제로 공유해 경쟁하는 pair에만 필요할 때 부른다 — 경쟁이 없는 pair의
    순서는 greedy 결과를 바꾸지 않기 때문이다.
    """
    return SequenceMatcher(None, deleted_lines, candidate_lines, autojunk=False).ratio()


# --------------------------------------------------------------------------------------
# same-position 판정 — 같은 file_path 후보 중 "제자리 수정"을 걸러낸다 (팀 추가 결정).
# 근거·한계는 docs/filter_rules.md NOISE_MOVE 절에도 기록돼 있다.
# --------------------------------------------------------------------------------------


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _same_position_child_range(
    hunks: list[Hunk], start_line: int, end_line: int
) -> tuple[int, int] | None:
    """부모 `[start_line, end_line]`과 겹치는 헝크들의 자식 쪽 범위를 하나로 합친다.

    `new_count == 0`(순수 삭제 — 그 자리에 아무것도 안 남음)인 헝크는 뺀다: 겹치는
    헝크가 전부 이런 경우라면(함수가 제자리에 아무 대체 없이 통째로 사라짐) same-position
    범위가 없다는 뜻이고, 그러면 이 파일의 다른 곳에 추가된 동일 함수는 정당한 이동
    후보로 남아야 한다. 겹치는 헝크가 하나도 없으면(이론상 FULL_FUNCTION이면 항상
    있어야 하지만, 방어적으로) `None`.
    """
    overlapping = [
        hunk
        for hunk in hunks
        if hunk.new_count > 0
        and _ranges_overlap(
            hunk.old_start, hunk.old_start + hunk.old_count - 1, start_line, end_line
        )
    ]
    if not overlapping:
        return None
    lo = min(hunk.new_start for hunk in overlapping)
    hi = max(hunk.new_start + hunk.new_count - 1 for hunk in overlapping)
    return (lo, hi)


def _is_same_position(
    same_file_hunks: dict[str, list[Hunk]],
    file_path: str,
    start_line: int,
    end_line: int,
    candidate: Function,
) -> bool:
    """삭제된 함수(부모 `[start_line, end_line]`)와 같은 경로의 `candidate`가, 헝크
    매핑으로 봤을 때 "같은 자리"에서 나온 것인가(§4.2② "위치").

    단순 `start_line == candidate.start_line` 비교를 쓰지 않는다 — 함수 위쪽에 무관한
    삽입·삭제가 있으면 자식 쪽 줄 번호가 밀리기 때문이다. 대신 부모 범위와 겹치는
    헝크의 `new_start`(그 헝크 앞의 모든 순증감이 이미 반영된 값, `Hunk` 독스트링
    참고)로 "제자리라면 있어야 할 자식 범위"를 계산하고, 후보 함수의 범위가 거기 겹치는지
    본다(정확히 같은 시작 줄일 필요는 없다 — 헝크 경계가 함수 경계와 한두 줄 어긋나도
    된다).
    """
    hunks = same_file_hunks.get(file_path)
    if not hunks:
        return False
    child_range = _same_position_child_range(hunks, start_line, end_line)
    if child_range is None:
        return False
    lo, hi = child_range
    return _ranges_overlap(candidate.start_line, candidate.end_line, lo, hi)


# --------------------------------------------------------------------------------------
# 이동 판정
# --------------------------------------------------------------------------------------

# (commit_sha, file_path, start_line, end_line) — extract.py 모듈 독스트링의 "record 단위"
# 절이 정의한, 같은 부모 파일 좌표계 안에서 함수 인스턴스를 구분하는 최소 key에 commit_sha를
# 더한 것(커밋을 넘나드는 유일성까지 필요하므로).
_RecordKey = tuple[str, str, int, int]

# (file_path, start_line, end_line) — 한 커밋의 added_functions 안에서 함수 인스턴스를
# 구분하는 key. extract.py의 "record 단위" 절과 같은 논리: 자식 파일 좌표계 안에서
# 함수 인스턴스가 겹칠 수 없으므로 이거면 충분하다(commit_sha는 호출자가 이미 한 커밋으로
# 좁혔다는 전제라 key에 넣지 않는다 — 아래 find_moved 독스트링의 호출 계약 참고).
_CandidateKey = tuple[str, int, int]


def _record_key(record: DeletedFunction) -> _RecordKey:
    return (record.commit_sha, record.file_path, record.start_line, record.end_line)


def _candidate_key(path: str, function: Function) -> _CandidateKey:
    return (path, function.start_line, function.end_line)


@dataclass(frozen=True)
class _MoveMatch:
    """greedy 매칭으로 확정된 짝 하나의 목적지와 유사도 (Issue #97).

    예전 `find_moved`가 이미 계산하고 버리던 값을 그대로 담는다 — excluded JSONL의
    `filter_evidence`에 쓴다. 판정에는 아무 영향이 없다.
    """

    file_path: str  # 목적지(자식 커밋) 경로
    function: Function  # 목적지 함수, 자식 커밋 좌표계
    similarity: float  # `_move_similarity` 값 그대로 (정규화 완전 일치면 1.0)


def _match_moved(
    deletions: Sequence[DeletedFunction],
    added_functions: dict[str, list[Function]],
    same_file_hunks: dict[str, list[Hunk]],
) -> dict[_RecordKey, _MoveMatch]:
    """이동(§4.2② NOISE_MOVE)으로 판정된 레코드의 key → 확정된 짝. 이동 판정의 본체.

    `find_moved`·`partition_moved`가 둘 다 이 함수를 **한 번만** 부른다 — 증거
    (`filter_evidence`, Issue #97)를 얻으려고 매칭을 다시 계산하지 않는다. 판정 기준·
    정렬·greedy 순서는 Issue #97 이전 `find_moved`와 같다.

    `deletions`는 같은 커밋(들)의 `DeletedFunction` 목록. `added_functions`·
    `same_file_hunks`는 그 커밋의 `pipeline.extract.collect_added_functions()`·
    `collect_same_file_hunks()` 결과를 그대로 받는다 — **호출자가 이미 그 커밋 하나로
    좁혀서 줘야 한다**(`deletions`에 여러 커밋이 섞여 있어도 이 함수 자체는 안전하다
    — 항상 같은 `commit_sha`를 가진 레코드끼리만 key가 겹치지만, `added_functions`·
    `same_file_hunks`가 여러 커밋을 넘나들면 엉뚱한 커밋의 헝크·후보로 판정하게 된다).

    후보(`deletions`)는 `deletion_kind == "FULL_FUNCTION"`만 본다(모듈 독스트링 "삭제
    후보" 절 참고). `added_functions`는 이미 "실제 added line과 겹치는 함수"로 걸러져
    있다고 전제한다(`pipeline.extract.collect_added_functions()`가 보장, 모듈 독스트링
    "이동 후보(added-side) 판정" 절) — 여기서 다시 거르지 않는다. 같은 `file_path`의
    추가 함수는 무조건 제외하지 않는다 — 같은 위치(제자리 수정)일 때만 pair 생성 전에
    뺀다(`_is_same_position`, 모듈 독스트링 "같은 경로 후보 처리" 절).

    1:1 greedy 매칭(모듈 독스트링 참고): 유효한 (삭제, 추가) pair를 전부 만들고 유사도
    내림차순 정렬 후, 양쪽 다 아직 안 쓰인 pair만 순서대로 확정한다. 정규화 유사도 동점은
    원문 유사도(`_raw_similarity`) 내림차순으로 먼저 깨고(Issue #80), 그것까지 같으면
    `(record_key, candidate_key)` 오름차순으로 깨 입력 순서와 무관하게 결정된다.

    원문 유사도는 필요할 때만 계산한다: 정규화 유사도 동점 그룹마다, 앞(더 높은) 그룹에서
    이미 쓰인 삭제·추가가 낀 pair를 먼저 빼고, 남은 pair 중 삭제 또는 추가를 다른 pair와
    공유하는(실제로 경쟁하는) pair에만 계산한다. 경쟁이 없는 pair는 그룹 안 어느 위치에
    있어도 확정되고 다른 pair의 결과를 바꾸지 않으므로, 위 전체 정렬 순서로 greedy를 돈
    결과와 같다.
    """
    candidates: list[tuple[str, Function, _NormalizedBody]] = [
        (path, function, _normalize(function.body))
        for path, functions in added_functions.items()
        for function in functions
    ]

    # 정렬 key(`pairs`)는 예전 그대로 두고, 목적지 Function은 key로 되찾는다 — 정렬·
    # tie-break가 Issue #97 이전과 달라지지 않게 한다(Function 객체는 비교 대상이 아니다).
    # 원문 유사도에 쓸 본문도 key로 되찾아, 원문 줄 목록은 필요할 때만 만든다.
    destinations: dict[_CandidateKey, Function] = {}
    deleted_bodies: dict[_RecordKey, str] = {}
    pairs: list[tuple[float, _RecordKey, _CandidateKey]] = []
    for record in deletions:
        if record.deletion_kind != "FULL_FUNCTION":
            continue
        record_key = _record_key(record)
        deleted_norm = _normalize(record.deleted_hunk)
        for candidate_path, candidate_fn, candidate_norm in candidates:
            if candidate_path == record.file_path and _is_same_position(
                same_file_hunks, record.file_path, record.start_line, record.end_line, candidate_fn
            ):
                continue  # 제자리 수정 — pair 자체를 만들지 않는다
            similarity = _move_similarity(deleted_norm, candidate_norm)
            if similarity is None:
                continue
            candidate_key = _candidate_key(candidate_path, candidate_fn)
            destinations[candidate_key] = candidate_fn
            deleted_bodies[record_key] = record.deleted_hunk
            pairs.append((similarity, record_key, candidate_key))

    # 정규화 유사도 내림차순, 그다음 (record_key, candidate_key) 오름차순. 정규화 유사도가
    # 같은 pair는 아래에서 그룹으로 묶어 처리한다.
    pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))

    deleted_raw: dict[_RecordKey, list[str]] = {}
    candidate_raw: dict[_CandidateKey, list[str]] = {}

    def raw_similarity(record_key: _RecordKey, candidate_key: _CandidateKey) -> float:
        if record_key not in deleted_raw:
            deleted_raw[record_key] = _raw_lines(deleted_bodies[record_key])
        if candidate_key not in candidate_raw:
            candidate_raw[candidate_key] = _raw_lines(destinations[candidate_key].body)
        return _raw_similarity(deleted_raw[record_key], candidate_raw[candidate_key])

    matched: dict[_RecordKey, _MoveMatch] = {}
    consumed: set[_CandidateKey] = set()
    for similarity, group in itertools.groupby(pairs, key=lambda pair: pair[0]):
        # 앞 그룹에서 이미 쓰인 삭제·추가가 낀 pair는 어차피 건너뛰므로 뺀다.
        alive = [
            (record_key, candidate_key)
            for _similarity, record_key, candidate_key in group
            if record_key not in matched and candidate_key not in consumed
        ]
        record_counts = Counter(record_key for record_key, _ in alive)
        candidate_counts = Counter(candidate_key for _, candidate_key in alive)
        independent: list[tuple[_RecordKey, _CandidateKey]] = []
        competing: list[tuple[float, _RecordKey, _CandidateKey]] = []
        for record_key, candidate_key in alive:
            if record_counts[record_key] == 1 and candidate_counts[candidate_key] == 1:
                independent.append((record_key, candidate_key))
            else:
                raw = raw_similarity(record_key, candidate_key)
                competing.append((raw, record_key, candidate_key))
        # 경쟁 pair만 원문 유사도 내림차순(Issue #80) → (record_key, candidate_key) 오름차순.
        # 원문 유사도는 이 순서에만 쓰고 판정(threshold)·evidence의 similarity에는 쓰지 않는다.
        competing.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))
        ordered = independent + [
            (record_key, candidate_key) for _, record_key, candidate_key in competing
        ]
        for record_key, candidate_key in ordered:
            if record_key in matched or candidate_key in consumed:
                continue
            matched[record_key] = _MoveMatch(
                file_path=candidate_key[0],
                function=destinations[candidate_key],
                similarity=similarity,
            )
            consumed.add(candidate_key)
    return matched


def find_moved(
    deletions: Sequence[DeletedFunction],
    added_functions: dict[str, list[Function]],
    same_file_hunks: dict[str, list[Hunk]],
) -> set[_RecordKey]:
    """`deletions` 중 이동(§4.2② NOISE_MOVE)으로 판정된 레코드의 key 집합.

    인자·판정 기준은 `_match_moved` 독스트링 참고. Issue #97 이전의 공개 API를 그대로
    유지하는 래퍼다 — 짝의 목적지·유사도는 버리고 key만 돌려준다.
    """
    return set(_match_moved(deletions, added_functions, same_file_hunks))


def _move_evidence(match: _MoveMatch) -> dict[str, Any]:
    """NOISE_MOVE의 `filter_evidence` — #89 판정자가 목적지를 바로 찾아볼 수 있게 한다.

    `file_path`·`function_name`·`start_line`·`end_line`은 **목적지**(자식 커밋) 함수의
    값이다(삭제 쪽 값은 excluded 행의 최상위 필드에 이미 있다). 이름에 클래스 한정자가
    없어(`pipeline/parsers/base.py`) 같은 파일에 같은 이름이 여럿일 수 있으므로 줄 범위를
    함께 남긴다. `similarity`는 `_move_similarity` 값 그대로다(반올림하지 않는다).
    """
    return {
        "file_path": match.file_path,
        "function_name": match.function.name,
        "start_line": match.function.start_line,
        "end_line": match.function.end_line,
        "similarity": match.similarity,
    }


def partition_moved(
    deletions: Sequence[DeletedFunction],
    added_functions: dict[str, list[Function]],
    same_file_hunks: dict[str, list[Hunk]],
) -> tuple[list[DeletedFunction], list[ExcludedRecord]]:
    """`deletions`를 (이동 아님, 이동으로 제외)로 나눈다 (Issue #97).

    제외된 레코드를 버리지 않고 `ExcludedRecord`(`filter_status="NOISE_MOVE"`,
    `FILTER_RULE_VERSION`, 목적지·유사도 증거)로 돌려준다 — §10.1 "제외 100건" 표본과
    최종 조립 단계의 `filter_status` 재료다. 두 리스트 모두 `deletions`의 입력 순서를
    유지하고, 모든 레코드는 정확히 한쪽에만 들어간다. 인자·판정 기준은 `_match_moved`와
    같다.
    """
    matches = _match_moved(deletions, added_functions, same_file_hunks)
    kept: list[DeletedFunction] = []
    excluded: list[ExcludedRecord] = []
    for record in deletions:
        match = matches.get(_record_key(record))
        if match is None:
            kept.append(record)
            continue
        excluded.append(
            ExcludedRecord(
                record=record,
                filter_status=NOISE_MOVE,
                filter_rule_version=FILTER_RULE_VERSION,
                filter_evidence=_move_evidence(match),
            )
        )
    return kept, excluded


def exclude_moved(
    deletions: Sequence[DeletedFunction],
    added_functions: dict[str, list[Function]],
    same_file_hunks: dict[str, list[Hunk]],
) -> list[DeletedFunction]:
    """`deletions`에서 이동으로 판정된 레코드를 뺀 나머지 (§4.2② "최종 삭제 레코드에서 제외").

    인자·판정 기준은 `find_moved`와 같다. Issue #97 이전 공개 API를 유지하는 래퍼 —
    `partition_moved`의 kept와 같다. 제외 레코드까지 필요하면 `partition_moved`를 쓴다.
    """
    kept, _excluded = partition_moved(deletions, added_functions, same_file_hunks)
    return kept


# --------------------------------------------------------------------------------------
# 사소한 부분 삭제 — NOISE_TRIVIAL (ADR-015, Issue #63)
# --------------------------------------------------------------------------------------


def _deleted_line_count(record: DeletedFunction) -> int:
    """NOISE_TRIVIAL 판정에 쓰는 줄 수: `len(deleted_hunk.splitlines())` 그대로 (Issue #63).

    `deleted_hunk`는 삭제 줄을 `"\\n"`으로 이어 붙인 값이라(끝 개행 없음) 보통 삭제 줄
    수와 같다. 마지막 삭제 줄이 빈 줄이면 `splitlines()`가 그 줄을 세지 않아 1 적게
    나온다 — 보정하지 않는다(팀 결정, `docs/filter_rules.md` NOISE_TRIVIAL 절 "한계").
    """
    return len(record.deleted_hunk.splitlines())


def partition_trivial(
    deletions: Sequence[DeletedFunction],
) -> tuple[list[DeletedFunction], list[ExcludedRecord]]:
    """`deletions`를 (유지, NOISE_TRIVIAL로 제외)로 나눈다 (ADR-015, Issue #63).

    `deletion_kind == "PARTIAL"`이고 줄 수(`_deleted_line_count`)가 `PARTIAL_MIN_LINES`
    미만(4줄 이하)인 레코드만 제외한다. FULL_FUNCTION은 줄 수와 무관하게 유지한다.
    제외 레코드는 `ExcludedRecord`(`filter_status="NOISE_TRIVIAL"`, `FILTER_RULE_VERSION`,
    `filter_evidence={"line_count": 줄 수}`)로 원본 레코드를 그대로 들고 간다. 두
    리스트 모두 입력 순서를 유지하고, 모든 레코드는 정확히 한쪽에만 들어간다.

    NOISE_MOVE(`partition_moved`)는 FULL_FUNCTION만, 이 함수는 PARTIAL만 보므로 둘의
    제외 대상은 겹치지 않고 적용 순서가 판정에 영향을 주지 않는다. git이 필요 없는 순수
    함수다.
    """
    kept: list[DeletedFunction] = []
    excluded: list[ExcludedRecord] = []
    for record in deletions:
        line_count = _deleted_line_count(record)
        if record.deletion_kind != "PARTIAL" or line_count >= PARTIAL_MIN_LINES:
            kept.append(record)
            continue
        excluded.append(
            ExcludedRecord(
                record=record,
                filter_status=NOISE_TRIVIAL,
                filter_rule_version=FILTER_RULE_VERSION,
                filter_evidence={"line_count": line_count},
            )
        )
    return kept, excluded
