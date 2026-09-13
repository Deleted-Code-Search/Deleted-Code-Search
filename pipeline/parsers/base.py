"""파서 계층 인터페이스 (인터페이스 파일 — 변경은 CHARTER.md §13 절차).

CHARTER.md §4.2 "파서 계층"에서 확정된 계약:

    extract_functions(source_text) -> [Function(name, start_line, end_line, body, signature)]

이 모듈은 그 계약이 쓰는 자료형 `Function`만 정의한다. 언어별 어댑터(`PythonAdapter` 등)는
별도 클래스로 구현하고 `extract_functions(self, source_text: str) -> list[Function]` 메서드를
제공한다. 이 시그니처가 곧 공개 API 규약이다 — 어댑터를 새로 추가할 때 이 메서드 이름·인자·
반환 타입을 그대로 지켜야 CHARTER §4.2와 어긋나지 않는다. 공통 추상 클래스·Protocol 같은 새
공개 구조는 CHARTER에 없으므로 이 파일에 임의로 추가하지 않는다 — 필요하다고 판단되면 §13
절차(이슈 → 회의 → ADR → CHARTER 갱신)를 먼저 거친다.

## 사용법
    adapter = PythonAdapter()
    functions = adapter.extract_functions(source_text)
    for fn in functions:
        fn.name, fn.start_line, fn.end_line, fn.signature, fn.body

## Function 필드
- name: 함수/메서드 이름. 클래스 한정자(`Class.method`)는 붙이지 않는다 — 필요하면 호출자가
  조합한다.
- start_line, end_line: **1-indexed, inclusive.** 데코레이터가 있으면 데코레이터 첫 줄부터
  포함한다. 이유: git diff에서 데코레이터도 함께 삭제되므로, §4.2 "삭제 헝크가 함수 경계와
  겹치면 함수 삭제로 태깅" 판정에서 라인이 어긋나면 안 된다.
- body: start_line~end_line 원문 그대로(데코레이터·시그니처·콜론·본문 전체 포함). 변수명·리터럴을
  치환한 정규화 본문은 이 인터페이스의 범위 밖이다 (3주차 AST 정규화 이슈에서 별도로 만든다).
- signature: `def`/`async def`부터 매개변수·반환 타입 애너테이션과 끝의 `:`까지. 콜론은
  Python 함수 선언문의 일부로 보고 포함한다. 데코레이터는 signature에 넣지 않는다(넣는 건
  body 쪽).

## 구문 오류 정책
`extract_functions`는 어떤 입력에도 예외를 던지지 않는다 — 항상 `list[Function]`을 반환한다
(문제가 있으면 빈 리스트일 수 있다). 소스에 구문 오류가 있어도 전체를 포기하지 않고, **오류와
겹치지 않는 함수만** 신뢰 가능한 부분 결과로 반환한다. 오류 구간과 겹치는 함수는 그 함수 자체는
물론 그 안의 중첩 함수도 포함하지 않는다 — 경계 자체가 깨졌을 수 있는 구간은 통째로 버리는 쪽이,
잘못된 라인 번호를 내놓는 쪽보다 낫다는 판단이다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Function:
    """소스에서 추출한 함수 하나. 필드는 CHARTER.md §4.2 그대로 — 임의로 늘리지 않는다.

    각 필드의 정확한 의미(라인 번호 규칙, 데코레이터·콜론 포함 여부 등)는 위 모듈
    독스트링의 "Function 필드" 절이 기준이다. 아래는 그 요약이다.

    Attributes:
        name: 함수/메서드 이름. 클래스 한정자(`Class.method`)는 붙이지 않는다.
        start_line: 함수 시작 줄 번호. 1-indexed. 데코레이터가 있으면 그 첫 줄부터다.
        end_line: 함수 끝 줄 번호. 1-indexed, inclusive(이 줄까지 포함).
        body: start_line~end_line 원문 그대로(데코레이터·시그니처·콜론·본문 전체 포함).
            변수명·리터럴을 치환한 정규화 본문은 포함하지 않는다(범위 밖).
        signature: `def`/`async def`부터 매개변수·반환 타입 애너테이션과 끝의 `:`까지.
            데코레이터는 포함하지 않는다.
    """

    name: str
    start_line: int
    end_line: int
    body: str
    signature: str
