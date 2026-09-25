"""커밋별 부모 대비 diff에서 삭제 헝크를 함수 단위 레코드로 추출. 담당: 재헌

CHARTER.md §4.2 ① 채굴 파이프라인의 "diff → 삭제 헝크 추출" 단계. `walk.py`가 내놓은
`CommitPair`(root·merge 커밋은 이미 제외됨, §4.2① "병합 커밋 처리")를 받아 그 커밋이
부모 대비 `.py` 파일에서 지운 **함수**를 `DeletedFunction` 레코드로 만든다.

record 단위(Issue #5 설계 논의로 확정):
    hunk가 아니라 **함수**다. 한 함수가 diff hunk 여러 개에 걸쳐 지워져도 record는 1개,
    한 커밋에서 함수 N개가 지워지면 record는 N개다. 논리적 key 개념은
    `(commit_sha, file_path, function_name)`이지만, `Function.name`엔 클래스 한정자가
    없어(`pipeline/parsers/base.py`) 같은 파일·같은 커밋에 같은 이름의 함수가 둘 이상
    있을 수 있다(예: `ClassA.__init__`, `ClassB.__init__`). 이 구현은 그 key를 **유일성
    제약으로 쓰지 않는다** — 서로 다른 함수 인스턴스를 절대 병합하지 않는다. 내부적으로는
    최소 `(function_name, start_line, end_line)`로 인스턴스를 구분한다(부모 파일 좌표계
    안에서 함수 인스턴스가 겹칠 수 없으므로 이거면 충분하다).

중첩 함수: `PythonAdapter`는 중첩 함수도 별도 `Function`으로 뽑는다. 삭제된 줄 하나는
그 줄을 포함하는 **모든** `Function`에 배정한다(포함 관계면 바깥·안쪽 둘 다). 그 결과:
    - 안쪽만 통째로 지워짐 → 안쪽 FULL_FUNCTION, 바깥쪽 PARTIAL (바깥 몸통 중 일부만
      없어졌으므로)
    - 바깥까지 통째로 지워짐(당연히 안쪽도 포함) → 안쪽·바깥쪽 둘 다 FULL_FUNCTION
이 동작은 `tests/test_extract.py`에 고정돼 있다.

FULL_FUNCTION/PARTIAL 판정: 부모 파일 기준 `Function.start_line~end_line` 전체가
삭제 줄로 덮이면 FULL_FUNCTION, 일부만 덮이면 PARTIAL, 하나도 안 덮이면(그 함수엔
삭제가 없으면) record 자체를 만들지 않는다 — 이게 "함수 밖 삭제(import·모듈 변수·
빈 줄 등)는 record로 만들지 않는다"를 자동으로 구현한다: 그런 줄은 애초에 어떤
`Function` 범위에도 안 들어가므로 어느 record에도 배정되지 않는다.

deleted_hunk: 해당 함수에 배정된 삭제 줄을 부모 파일 줄 번호 오름차순으로 이어붙인
문자열. 헝크 경계 마커는 넣지 않는다 — "무엇이 지워졌나"만 필요하고 원본 재구성이
목적이 아니다.

added_hunks_same_file: 함수 단위가 아니라 **커밋 내 그 파일** 단위다. CHARTER §4.2①
필드명 자체가 "같은 파일"이지 "같은 위치"가 아니다. ±N줄 근접도로 특정 함수와 매칭시켜
대체 코드 후보를 뽑는 정교한 작업은 "맥락 결합"(`context.py`, 희수 담당,
`replacement.match_method/confidence`, #67)의 몫이고, 여기서는 그 원재료만 만든다 —
그 파일의 추가 헝크(`AddedHunk`) 목록, diff 순서 그대로. 헝크마다 헤더 좌표
(old_start/old_count/new_start/new_count)를 함께 남기는 이유는 #67의 SAME_LOCATION
판정에 삭제 위치와 추가 위치의 거리가 필요하기 때문이다(Issue #102 — 이전 필드
`added_hunk_same_file`은 추가 줄을 문자열 하나로 이어붙여 좌표와 헝크 경계를 잃었다).
`new_count == 0`인 순수 삭제 헝크는 넣지 않는다 — 추가된 줄이 없고, 삭제 위치는
`deleted_hunk`의 부모 좌표가 이미 가지고 있다. 같은 파일에서 나온 모든 record가 이
값을 동일하게 공유한다. 추가 헝크가 없으면 빈 tuple(JSONL에선 `[]`).
이전 문자열 값은 `"\\n".join(h.added_body for h in added_hunks_same_file)`로 정확히
복원된다(`tests/test_extract.py`가 고정). 그래서 두 형태를 함께 저장하지 않는다.

git diff 읽기:
    git -c core.quotepath=false -C <repo> diff --no-color --no-renames --unified=0
        <parent> <commit> -- *.py
    - `--unified=0`: 컨텍스트 줄 없이 변경 줄만. 헝크 헤더(`@@ -old,n +new,m @@`)가
      부모/자식 파일의 줄 번호를 정확히 알려주므로 직접 세지 않아도 된다.
    - `--no-renames`: 리네임을 delete+add 두 섹션으로 쪼갠다. "이동" 판정은 §4.2②
      필터 단계 몫이라 여기선 구분하지 않는다.
    - `-- *.py`: git 패스스펙(셸 glob 아님 — subprocess에 리스트로 넘겨 셸 확장을
      거치지 않는다)이 경로 깊이와 무관하게 `.py` 파일만 골라준다. §4.2① ".py만 대상"을
      파이썬 코드가 아니라 git이 처리한다.
    - `-c core.quotepath=false`: 비-ASCII 경로를 C-스타일로 이스케이프하지 않게 한다 —
      file_path를 별도 언이스케이프 없이 그대로 보존하기 위함 (§4.2① "repo-root-relative
      경로를 그대로 보존").
    - celery `docs/` 같은 경로 기반 추가 제외는 이번 단계에 넣지 않는다(#28 결정 전).
      file_path만 정확히 보존한다.

부모 파일 원문: `git show <parent_sha>:<file_path>`로 읽고 `PythonAdapter`(이슈 #4,
`pipeline/parsers/base.py` 계약)로 함수 경계를 구한다. diff의 옛 줄 번호와 같은
좌표계라서 그대로 비교할 수 있다.

pygit2 대신 git CLI를 쓴다 — walk.py·clone.py와 같은 이유(CHARTER §7, 새 네이티브
의존성 회피). `_run_git_diff`/`_read_file_at`은 그 두 모듈의 자체 subprocess
헬퍼와 같은 패턴을 반복한다 — 모듈 간 `_`-prefix 함수를 서로 import하지 않는 이
저장소 관례를 그대로 따른다.

JSONL 저장 (내부 모델과 외부 계약 분리):
    `DeletedFunction`은 채굴 단계 내부 표현이고, `deleted_hunk`라는 이름과 "지워진
    줄만 이어붙인 값"이라는 의미를 그대로 유지한다 — PARTIAL이면 함수 전체 원문이
    아니라 실제로 지워진 조각이라서 `deleted_body`(§4.4, "원문"이라는 뜻)라고 부르면
    오해를 산다. 대신 `to_json_dict()`가 직렬화 시점에만 §4.4 이름으로 바꿔 내보낸다
    (`deleted_hunk` → `deleted_body`, `deletion_type` 없이 `deletion_kind`만).
    이렇게 내부 필드명과 JSONL 바깥 계약을 분리해두면, 나중에 내부 구현이 바뀌어도
    `context.py`(§4.2① 출력의 `repo`/`commit_sha`/`file_path`/`commit_message` 4개
    키만 보고 나머지는 무시하는 소비자, `pipeline/context.py:parse_targets`)가 보는
    바깥 계약은 그대로 유지된다.

    `to_json_dict()`가 내보내는 키: `repo`, `commit_sha`, `parent_sha`, `file_path`,
    `function_name`, `start_line`, `end_line`, `deletion_kind`, `deleted_body`,
    `added_hunks_same_file`, `author_date`, `commit_message`, `id`, `function_signature`,
    `is_test_code`, `source_url` (Issue #75). `added_hunks_same_file`은 헝크마다
    `old_start`·`old_count`·`new_start`·`new_count`·`added_body` 5개 키를 가진 객체의
    JSON list다(Issue #102). 이 단계에서 알 수 없는 §4.4 필드
    (`repo_license`, `context`, `replacement`, `reason`, `embedding` 등)는 `None`이나
    빈 값으로 채워 넣지 않는다 — 아직 없는 값을 있는 것처럼 보이게 하지 않는다는 뜻이고,
    그 필드들은 각자 담당 단계(필터·맥락 결합·분류·임베딩)에서 채운다.

    Issue #75가 추가한 4개(`id`·`function_signature`·`is_test_code`·`source_url`)는 이
    원칙의 예외가 아니라, 이 단계에서 **이미 알 수 있는** 값이라 예외 목록에 없다:
    `id`는 `make_record_id()`(기존 규칙 그대로), `function_signature`는
    `PythonAdapter.extract_functions()`가 이미 주는 `Function.signature`, `source_url`은
    `repo`/`commit_sha`로 조합하는 GitHub 커밋 링크, `is_test_code`는 `file_path`만으로
    판정한다(아래 `_is_test_code`) — 전부 다른 단계(필터·맥락 결합·분류)를 기다릴 필요가
    없다.

    `filter_status`·`filter_rule_version`·`filter_evidence`는 **여기 포함하지 않는다.**
    추출 JSONL은 필터를 통과한 레코드만 담는 중간 산출물이고, 최종 `filter_status`는
    조립 단계가 이 파일과 excluded JSONL을 합쳐 만든다(`docs/filter_rules.md` "계약
    분리"·"제외 레코드 보존" 절, Issue #97).

    `write_jsonl()`은 `context.py`의 기존 출력 루프(`main()`의 `--out` 처리)와 같은
    관례를 그대로 따른다: UTF-8, `ensure_ascii=False`, 한 줄에 객체 1개, `indent` 없음,
    줄 끝 `\n`, 부모 디렉터리는 자동 생성. JSON array로 쓰지 않는다.

    제외 레코드(Issue #97): 필터가 제외한 레코드는 `ExcludedRecord`로 받아
    `write_excluded_jsonl()`로 별도 파일에 쓴다. 한 줄은 `to_json_dict()`의 키 전부 +
    `filter_status`·`filter_rule_version`·`filter_evidence` (flat, `excluded_to_json_dict`).
    관례는 `write_jsonl()`과 같고, 0건이어도 빈 파일을 만든다.

    출력 경로 정책(어느 디렉터리에, 어떤 파일 이름으로)은 여기서 정하지 않는다 —
    두 writer 모두 호출자가 준 경로에 쓰기만 한다.

`extract_repo_with_excluded(repo_path, repo, ref)`: 저장소 하나를 처음부터 끝까지
훑는 최소 순차 루프. `walk_commits()`로 얻은 `CommitPair`마다 `extract_deletions()`와
이동 필터(`filter.partition_moved`)·사소한 부분 삭제 필터(`filter.partition_trivial`,
#63)를 돌려 (남은 레코드, 제외 레코드)를 누적한다. 커밋마다 `git diff`는 한 번만 실행하고
세 단계(삭제 추출·추가 함수·같은 파일 헝크)가 그 diff 텍스트를 함께 쓴다(Issue #64) —
공개 함수 세 개는 각자 diff를 받는 wrapper로 남아 있고, 루프는 그 본체(`_..._from_diff`)를
직접 부른다.
`extract_repo(repo_path, repo, ref)`는 그 첫 번째 값만 돌려주는 기존 API다. `ref`는
clone.py·walk.py와 같은 이유로 호출자가 명시한다 — default branch를 이 함수가 추측하지
않는다. 병렬화·재시도는 넣지 않는다(4주차 범위).

`collect_added_functions(repo_path, commit)`: 이동 탐지 필터(§4.2②, Issue #52)가 쓰는
added-side 함수 후보를 모은다. `parse_file_diffs`는 옛(부모) 경로로만 섹션을 식별하고
순수 신규 파일 섹션(옛 경로 `/dev/null`)은 버리므로(위 "git diff 읽기" 참고) — 정확히
"파일 이동" 케이스(`--no-renames`가 옛 경로 삭제 섹션과 새 경로 신규 섹션으로 쪼갬)에서
목적지 파일을 놓친다. 그래서 `_parse_added_line_ranges()`는 같은 diff 텍스트를 독립적
으로 다시 훑어 **자식(새) 경로별 실제 added-line 범위**를 모은다 — `parse_file_diffs`의
기존 반환 형태·동작은 건드리지 않는다(그 함수를 쓰는 기존 테스트·호출부는 그대로).

후보 판정(Issue #52 B-1 수정, 코드 리뷰에서 발견된 버그의 수정): 경로별로 그 시점
(`commit_sha`) 파일 전체를 `git show`로 읽어 `PythonAdapter`로 함수 목록을 뽑되,
**그 함수의 `[start_line, end_line]`이 그 경로의 added-line 범위와 하나라도 겹칠 때만**
후보에 넣는다. 한 줄 전체가 아니라 "겹치는" 조건이라 함수 전체가 added line일 필요는
없다. 예전 구현은 "그 파일에 추가 줄이 하나라도 있으면 파일 전체 함수를 후보로" 삼았는데,
그러면 이번 커밋에서 **전혀 손대지 않은, 그 파일에 원래 있던 함수**까지 후보가 돼
다른 파일의 무관한 진짜 삭제가 우연히 비슷하다는 이유로 이동 오판될 수 있었다(§10.1
정밀도 목표와 충돌, "오탐보다 미탐이 낫다" 원칙 — `docs/filter_rules.md` 참고). 순수
신규 파일·이동 목적지는 함수 전체가 added line이라 이 조건이 자연히 통과된다. 같은
경로 후보 중 "제자리 수정"과 "같은 파일 안 이동"을 가르는 것은 `collect_same_file_hunks()`
+ 호출자(`filter.py`)의 몫이다.

`collect_same_file_hunks(repo_path, commit)`: 옛 경로 == 새 경로로 남은(제자리 수정)
파일마다 그 헝크(`Hunk`: old_start/old_count/new_start/new_count) 목록을 준다 —
same-position 판정(팀 추가 결정, Issue #52)의 재료. 부모 함수 범위와 겹치는 헝크의
`new_start`가 곧 "제자리 수정이라면 자식 파일에서 있어야 할 위치"다(`Hunk` 독스트링
참고). 리네임 조각(옛/새 경로가 다르거나 한쪽이 없음)은 담지 않는다 — 순수 이동·신규
파일에는 "제자리"라는 개념이 없고, 다른 경로는 항상 이동 후보이기 때문이다.

범위 밖: 이동 판정 로직 자체(정규화·유사도 계산·same-position 판정 적용, `filter.py`),
맥락 결합(`context.py`), 분류, DB 적재, 병렬화·재시도 큐(4주차), 출력 경로/파일명 정책.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.parsers.base import Function
from pipeline.parsers.python_adapter import PythonAdapter
from pipeline.walk import CommitPair, walk_commits

# git 명령이 자격 증명 프롬프트로 멈추지 않게 하고, 페이저가 뜨지 않게 한다 — walk.py·
# clone.py와 같은 이유.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}

# "@@ -old_start[,old_count] +new_start[,new_count] @@ ..." — count 생략 시 1
# (unified diff 관례). 네 값 모두 `parse_file_diffs`(삭제 줄 커서와 `AddedHunk`),
# added-line 범위(_parse_added_line_ranges), same-position 판정(_parse_same_file_hunks)
# 에서 쓴다. 뒤에 붙는 컨텍스트(`def foo(x):` 등)는 무시한다.
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_ADAPTER = PythonAdapter()

# §4.4 `DeletionRecord.id` 를 만드는 규칙. **이 값을 바꾸면 기존 라벨이 전부 떨어져 나간다.**
#
# uuid4(랜덤) 대신 uuid5(결정적)인 이유: 같은 레코드는 누가 언제 다시 뽑아도 같은 id 여야
# 한다. 라벨 가이드 §7.2 가 `record_id` 를 라벨과 레코드를 잇는 유일 키로 쓰고, §11-16 이
# 예비 200건을 본 500건에 포함한다고 했다. 랜덤이면 재추출할 때마다 값이 달라져 둘 다 깨진다.
#
# 키는 (repo, commit_sha, file_path, function_name, start_line) 이다. 한 파일에 같은 이름
# 함수가 여럿 있어도(`__init__` 등) start_line 으로 갈린다.
RECORD_ID_NAMESPACE = uuid.UUID("6f4c2b18-1c3a-5e7d-9a0b-2d8e4f1a7c63")


def make_record_id(
    repo: str, commit_sha: str, file_path: str, function_name: str, start_line: int
) -> str:
    """§4.4 `id`. 같은 함수 삭제는 언제 뽑아도 같은 값이다 (위 주석 참고)."""
    key = f"{repo}|{commit_sha}|{file_path}|{function_name}|{start_line}"
    return str(uuid.uuid5(RECORD_ID_NAMESPACE, key))


def _source_url(repo: str, commit_sha: str) -> str:
    """§4.4 `source_url` — GitHub 커밋 링크. `datasets/labels/pre200_records.jsonl`의
    실제 데이터와 `tests/test_sampling.py`·`tests/test_label_cli.py`가 이미 이 형식을
    계약으로 가정하고 있다(Issue #75 조사) — 새 형식을 만들지 않고 그대로 따른다."""
    return f"https://github.com/{repo}/commit/{commit_sha}"


def _is_test_code(file_path: str) -> bool:
    """§4.4 `is_test_code` — 테스트 코드는 제외하지 않고 플래그로만 보존한다
    (CHARTER §4.2②, `docs/filter_rules.md`). 판정 기준은 저장소에 근거가 있는 것만
    쓴다(Issue #75 조사: 기존 규칙 없음, 확인된 유일한 사례가 `tests/` 디렉터리) —
    `file_path`의 경로 구성요소 중 정확히 `tests`가 있으면 True. `test_*.py` 파일명이나
    `conftest.py` 같은 추가 휴리스틱은 근거가 없어 넣지 않는다. git diff 경로는 항상
    `/` 구분자라 OS와 무관하게 그대로 나눈다(`pathlib.Path`는 윈도우에서 다르게 해석할
    수 있어 쓰지 않는다)."""
    return "tests" in file_path.split("/")


@dataclass(frozen=True)
class AddedHunk:
    """추가 줄이 있는 diff 헝크 하나 (Issue #102). `DeletedFunction.added_hunks_same_file`의 원소.

    이동 탐지용 `Hunk`와 별개 타입이다 — `Hunk`는 `filter.py`의 same-position 판정
    인터페이스라 건드리지 않고, 이쪽은 대체 코드 매칭(#67)의 원재료다.

    좌표는 헝크 헤더 값 그대로다(count 생략 시 1). `old_start`/`new_start`는 부모·자식
    파일 기준 1-indexed. `new_count`는 항상 1 이상이다(순수 삭제 헝크는 만들지 않는다).
    `old_count == 0`(순수 삽입)이면 unified diff 관례상 `old_start`는 삽입 위치의 **바로
    앞** 부모 줄이다(파일 맨 앞이면 0).

    added_body: 그 헝크의 추가 줄을 `+` 없이 `"\\n"`으로 이어붙인 값. 빈 줄 하나만
    추가한 헝크는 `""`이면서 `new_count == 1`이다 — 헝크가 있는지는 `added_body`가
    아니라 `new_count`로 판단한다.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_body: str


@dataclass(frozen=True)
class DeletedFunction:
    """커밋 하나에서 함수 하나가 삭제된 기록. record 단위 = 함수 (모듈 독스트링 참고).

    §4.4 `DeletionRecord`와는 다른 타입이다 — 이건 그 최종 스키마의 일부(맥락·대체
    코드·이유·임베딩이 빠진 채굴 단계 산출물)일 뿐이다.

    start_line/end_line: 부모 커밋 시점 파일 기준, 1-indexed inclusive.
    `PythonAdapter`의 `Function.start_line`/`end_line`을 그대로 쓴다(데코레이터 포함).
    """

    repo: str
    commit_sha: str
    parent_sha: str
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    deletion_kind: str  # "FULL_FUNCTION" | "PARTIAL" — §4.4 DeletionRecord와 이름을 맞춘다
    deleted_hunk: str
    added_hunks_same_file: tuple[AddedHunk, ...]  # 파일 단위, diff 순서 (Issue #102)
    author_date: str
    commit_message: str
    id: str  # §4.4 `id`. `make_record_id()`로만 만든다 (Issue #75)
    function_signature: str  # §4.4 `function_signature`. `Function.signature` 그대로
    is_test_code: bool  # §4.4 `is_test_code`. `_is_test_code(file_path)` 판정
    source_url: str  # §4.4 `source_url`. `_source_url(repo, commit_sha)` 그대로


@dataclass(frozen=True)
class ExcludedRecord:
    """필터가 제외한 `DeletedFunction` 하나와 그 사유 (Issue #97).

    원본 레코드를 그대로 들고 있다 — 제외 레코드는 추출 JSONL에 없으므로 이게 원문을
    남기는 유일한 곳이다. 만드는 쪽은 `pipeline.filter`의 `partition_moved`(NOISE_MOVE)와
    `partition_trivial`(NOISE_TRIVIAL, #63)이다. 타입을 여기 두는 이유는 `Hunk`와 같다 —
    `filter.py`가 이 모듈을 import하므로 반대 방향 import(순환)를 만들지 않는다.

    filter_status: CHARTER §4.4 enum 값 그대로 (`"NOISE_MOVE"` 등). `KEPT`는 여기 오지
        않는다 — 제외된 레코드만 담는다.
    filter_rule_version: `pipeline.filter.FILTER_RULE_VERSION` (= `docs/filter_rules.md` 버전).
    filter_evidence: 사유별 판정 근거. 키는 사유마다 다르다(`docs/filter_rules.md`
        "제외 레코드 보존" 절).
    """

    record: DeletedFunction
    filter_status: str
    filter_rule_version: str
    filter_evidence: dict[str, Any]


@dataclass(frozen=True)
class Hunk:
    """diff 헝크 하나의 위치 정보. same-position 판정(`filter.py`, Issue #52)이 쓴다.

    `old_start`/`new_start`는 각각 부모·자식 파일 기준 1-indexed 시작 줄, `old_count`/
    `new_count`는 그 헝크가 차지하는 줄 수(0이면 그쪽엔 줄이 없다 — 순수 삽입이면
    `old_count == 0`, 순수 삭제면 `new_count == 0`). `new_start`는 git이 이미 그 앞의
    모든 헝크의 순증감을 반영해 계산해 준 값이라, 이 헝크보다 **앞쪽**의 무관한
    삽입·삭제가 있어도 별도로 오프셋을 누적 계산할 필요가 없다 — 그게 이 타입을 쓰는
    이유다.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass
class _FileDiff:
    """diff 한 파일 섹션을 파싱한 중간 결과 (모듈 내부용)."""

    deleted_lines: dict[int, str] = field(default_factory=dict)  # 부모 파일 줄 번호 -> 텍스트
    added_hunks: list[AddedHunk] = field(default_factory=list)  # diff 순서, new_count > 0만


def _run_git_diff(repo_path: str | Path, parent_sha: str, commit_sha: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "-C",
            str(repo_path),
            "diff",
            "--no-color",
            "--no-renames",
            "--unified=0",
            parent_sha,
            commit_sha,
            "--",
            "*.py",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout


def _read_file_at(repo_path: str | Path, sha: str, file_path: str) -> str:
    """`git show <sha>:<file_path>`. 부모 커밋 원문(`extract_deletions`)과 자식 커밋
    원문(`collect_added_functions`, Issue #52) 양쪽에 쓴다 — `sha`가 무엇이든 상관없다."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "-C",
            str(repo_path),
            "show",
            f"{sha}:{file_path}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout


def _old_path(spec: str) -> str | None:
    """`--- a/<path>` 줄의 `<path>` 부분. 신규 파일(`--- /dev/null`)이면 None.

    경로에 공백이 섞여 있으면 git이 헤더 줄 끝에 탭 하나를 덧붙인다(고전 unified diff의
    `path\\tdate` 필드 구분자 관례 — 직접 재현해서 확인: 공백 없는 경로엔 안 붙고, 공백이
    있으면 정확히 탭 1개만 붙는다). 그 탭 하나만 제거한다 — `.rstrip()`을 쓰지 않는 이유는
    경로 "안"의 공백(`gemini-2.0-flash copy.py`처럼 파일명의 일부)이나, 이론상 파일명이
    실제로 공백으로 끝나는 경우까지 건드리면 안 되기 때문이다. 딱 이 탭 하나만 git이 붙인
    메타데이터고, 나머지는 전부 실제 경로다.
    """
    if spec == "/dev/null":
        return None
    path = spec[2:] if spec.startswith("a/") else spec
    if path.endswith("\t"):
        path = path[:-1]
    return path


def _new_path(spec: str) -> str | None:
    """`+++ b/<path>` 줄의 `<path>` 부분. 파일이 삭제됐으면(`+++ /dev/null`) None.

    `_old_path`와 대칭. 탭 처리 규칙도 동일하다(모듈 독스트링 "git diff 읽기" 참고) —
    `_parse_added_line_ranges()`가 이동 목적지 경로를 얻는 데 쓴다.
    """
    if spec == "/dev/null":
        return None
    path = spec[2:] if spec.startswith("b/") else spec
    if path.endswith("\t"):
        path = path[:-1]
    return path


def parse_file_diffs(diff_text: str) -> dict[str, _FileDiff]:
    """`git diff --unified=0` 출력을 파일별로 나눈다 (순수 함수, 테스트 대상).

    삭제 줄이 하나도 없는 파일(순수 신규 파일 포함)은 결과에 없다 — 그런 파일에서는
    어차피 `DeletedFunction` record가 나올 수 없다.

    `--- `/`+++ `는 파일 헤더에서만 그렇게 해석한다. 헝크(`@@ ... @@`) 안에서 지워지거나
    추가되는 소스 줄 자체가 "-- x --"/"++ x ++"처럼 시작하면, diff 접두사 하나가 붙어
    "--- x --"/"+++ x ++"가 돼 파일 헤더 줄과 구별이 안 된다 — `in_hunk`로 헝크 진입
    여부를 추적해, 헝크 안에서는 `--- `/`+++ `도 무조건 삭제/추가 내용으로 취급한다.

    추가 줄은 헝크 단위 `AddedHunk`로 모은다(Issue #102). 헤더의 네 값을 그대로 남기고
    (count 생략 시 1), `new_count == 0`인 순수 삭제 헝크는 넣지 않는다.
    """
    files: dict[str, _FileDiff] = {}
    current_path: str | None = None
    current: _FileDiff | None = None
    old_cursor = 0
    in_hunk = False  # `@@ ... @@` 이후 다음 `diff --git `까지 True
    # 지금 읽는 헝크의 (old_start, old_count, new_start, new_count)와 그 추가 줄
    hunk_header: tuple[int, int, int, int] | None = None
    hunk_added: list[str] = []

    def finish_hunk() -> None:
        if current is not None and hunk_header is not None and hunk_header[3] > 0:
            current.added_hunks.append(AddedHunk(*hunk_header, "\n".join(hunk_added)))

    def flush() -> None:
        finish_hunk()
        if current_path is not None and current is not None and current.deleted_lines:
            files[current_path] = current

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current_path = None
            current = None
            in_hunk = False
            hunk_header = None
            hunk_added = []
            continue
        if not in_hunk and line.startswith("--- "):
            current_path = _old_path(line[4:])
            current = _FileDiff() if current_path is not None else None
            continue
        if not in_hunk and line.startswith("+++ "):
            continue  # 파일 식별은 옛(부모) 경로로만 한다 — 모듈 독스트링 참고
        if line.startswith("\\"):
            continue  # "\ No newline at end of file" 등 메타 줄
        if current is None:
            continue  # 신규 파일 섹션이거나 아직 파일 헤더 전
        header = _HUNK_HEADER_RE.match(line)
        if header:
            finish_hunk()
            old_start = int(header.group(1))
            old_count = int(header.group(2)) if header.group(2) is not None else 1
            new_start = int(header.group(3))
            new_count = int(header.group(4)) if header.group(4) is not None else 1
            hunk_header = (old_start, old_count, new_start, new_count)
            hunk_added = []
            old_cursor = old_start
            in_hunk = True
            continue
        if line.startswith("-"):
            current.deleted_lines[old_cursor] = line[1:]
            old_cursor += 1
            continue
        if line.startswith("+"):
            hunk_added.append(line[1:])
            continue
        # --unified=0이라 컨텍스트(공백 시작) 줄은 없어야 하지만, 있어도 무시한다.

    flush()
    return files


def _parse_added_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """diff에서 실제로 추가된 줄의 **자식(새) 경로**별 `(start, end)`(inclusive) 범위 목록.

    Issue #52 B-1 수정: 이동 후보(`collect_added_functions`)를 "그 파일에 줄이 하나라도
    추가됐으면 파일 전체 함수"가 아니라 **실제 added line과 겹치는 함수**로만 제한하는
    데 쓴다. 옛(부모) 경로는 보지 않는다 — 순수 신규 파일(옛 경로 없음)·제자리 수정(옛
    경로==새 경로)·이동 목적지(옛 경로 다름) 구분 없이 "이 새 경로에 실제로 추가된 줄이
    어디부터 어디까지인가"만 본다. `new_count == 0`인 헝크(순수 삭제 — 그 자리에 아무
    새 줄도 안 남음)는 범위에 넣지 않는다. `parse_file_diffs`의 기존 동작·반환 형태는
    이 함수와 무관하게 그대로 유지된다(같은 diff 텍스트를 독립적으로 다시 훑음).

    같은 파일 안에서 지워지거나 추가되는 소스 줄이 "-- x --"/"++ x ++"처럼 시작해 헤더
    줄과 헷갈릴 수 있는 문제는 `parse_file_diffs`와 같은 `in_hunk` 가드로 막는다.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    current_new_path: str | None = None
    ranges: list[tuple[int, int]] = []
    in_hunk = False

    def flush() -> None:
        if current_new_path is not None and ranges:
            result[current_new_path] = list(ranges)

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current_new_path = None
            ranges = []
            in_hunk = False
            continue
        if not in_hunk and line.startswith("+++ "):
            current_new_path = _new_path(line[4:])
            continue
        if not in_hunk and line.startswith("--- "):
            continue  # 옛 경로는 여기서 관심 없다 — 새 경로만 본다
        if line.startswith("\\"):
            continue
        match = _HUNK_HEADER_RE.match(line)
        if match:
            in_hunk = True
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            if new_count > 0:
                ranges.append((new_start, new_start + new_count - 1))
            continue

    flush()
    return result


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _parse_same_file_hunks(diff_text: str) -> dict[str, list[Hunk]]:
    """옛 경로와 새 경로가 같은(제자리 수정) 파일 섹션의 헝크를 경로별로 모은다.

    이동 탐지의 same-position 판정(`filter.py`, Issue #52)에 쓴다. 옛/새 경로가 다르거나
    (`--no-renames`가 쪼갠 리네임 조각) 어느 한쪽이 없는(신규 파일/파일 전체 삭제) 섹션은
    담지 않는다 — "제자리"라는 개념 자체가 그런 섹션엔 없다(다른 경로는 항상 이동 후보,
    §4.2②). `parse_file_diffs`/`_parse_added_line_ranges`와 마찬가지로 같은 diff 텍스트를
    독립적으로 다시 훑는다.
    """
    result: dict[str, list[Hunk]] = {}
    old_path: str | None = None
    new_path: str | None = None
    hunks: list[Hunk] = []
    in_hunk = False

    def flush() -> None:
        if old_path is not None and new_path is not None and old_path == new_path and hunks:
            result[old_path] = list(hunks)

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            old_path = None
            new_path = None
            hunks = []
            in_hunk = False
            continue
        if not in_hunk and line.startswith("--- "):
            old_path = _old_path(line[4:])
            continue
        if not in_hunk and line.startswith("+++ "):
            new_path = _new_path(line[4:])
            continue
        if line.startswith("\\"):
            continue
        match = _HUNK_HEADER_RE.match(line)
        if match:
            in_hunk = True
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            hunks.append(Hunk(int(match.group(1)), old_count, int(match.group(3)), new_count))
            continue

    flush()
    return result


def _build_records(
    repo: str, commit: CommitPair, file_path: str, source: str, file_diff: _FileDiff
) -> list[DeletedFunction]:
    """파일 하나의 삭제 줄을 부모 소스의 함수 범위에 귀속시켜 함수별 `DeletedFunction`을 만든다."""
    added_hunks_same_file = tuple(file_diff.added_hunks)
    records: list[DeletedFunction] = []
    for function in _ADAPTER.extract_functions(source):
        attributed = {
            line_no: text
            for line_no, text in file_diff.deleted_lines.items()
            if function.start_line <= line_no <= function.end_line
        }
        if not attributed:
            continue  # 이 함수엔 삭제가 없다 — record를 만들지 않는다

        span = function.end_line - function.start_line + 1
        deletion_kind = "FULL_FUNCTION" if len(attributed) == span else "PARTIAL"
        deleted_hunk = "\n".join(attributed[line_no] for line_no in sorted(attributed))

        records.append(
            DeletedFunction(
                repo=repo,
                commit_sha=commit.commit_sha,
                parent_sha=commit.parent_sha,
                file_path=file_path,
                function_name=function.name,
                start_line=function.start_line,
                end_line=function.end_line,
                deletion_kind=deletion_kind,
                deleted_hunk=deleted_hunk,
                added_hunks_same_file=added_hunks_same_file,
                author_date=commit.author_date,
                commit_message=commit.commit_message,
                id=make_record_id(
                    repo, commit.commit_sha, file_path, function.name, function.start_line
                ),
                function_signature=function.signature,
                is_test_code=_is_test_code(file_path),
                source_url=_source_url(repo, commit.commit_sha),
            )
        )
    return records


def extract_deletions(
    repo_path: str | Path, repo: str, commit: CommitPair
) -> list[DeletedFunction]:
    """`commit`이 부모(`commit.parent_sha`) 대비 지운 함수들을 `DeletedFunction`으로 뽑는다.

    `commit`은 `walk_commits()`가 내놓은 `CommitPair` 그대로 넘긴다 — root·merge 커밋
    제외는 이미 walk.py 단계에서 끝나 있다. `repo_path`(clone.py가 만든 로컬 경로)와
    `repo`(`"owner/name"`)는 호출자가 명시한다.
    """
    diff_text = _run_git_diff(repo_path, commit.parent_sha, commit.commit_sha)
    return _extract_deletions_from_diff(repo_path, repo, commit, diff_text)


def _extract_deletions_from_diff(
    repo_path: str | Path, repo: str, commit: CommitPair, diff_text: str
) -> list[DeletedFunction]:
    """`extract_deletions()`의 본체. `diff_text`는 `commit`의 부모 대비 `_run_git_diff()` 출력이어야
    한다 — `extract_repo_with_excluded()`가 커밋당 한 번 받은 diff를 세 단계에 나눠 주려고
    분리했다 (Issue #64)."""
    file_diffs = parse_file_diffs(diff_text)

    records: list[DeletedFunction] = []
    for file_path, file_diff in file_diffs.items():
        source = _read_file_at(repo_path, commit.parent_sha, file_path)
        records.extend(_build_records(repo, commit, file_path, source, file_diff))
    return records


def collect_added_functions(repo_path: str | Path, commit: CommitPair) -> dict[str, list[Function]]:
    """`commit`에서, 실제 added line과 겹치는 함수만 자식(새) 경로별로 모은다.

    이동 탐지 필터(§4.2②, `filter.py`, Issue #52)가 "같은 커밋에 실제로 추가된 함수"
    후보를 얻는 데 쓴다. 후보 판정 기준(Issue #52 B-1 수정, 모듈 독스트링
    "collect_added_functions" 절 참고): 함수의 `[start_line, end_line]`이 그 경로의
    added-line 범위(`_parse_added_line_ranges`) 중 **하나라도** 겹치면 후보다 — 함수
    전체가 added line일 필요는 없다. 부모 커밋부터 이미 있었고 이번 diff의 added line과
    전혀 안 겹치는 함수(이번 커밋에서 손 안 댐)는 후보에 넣지 않는다. 순수 신규 파일·
    이동 목적지는 함수 전체가 added line이라 자연히 후보가 된다. 같은 경로의 후보 중
    "제자리 수정"과 "같은 파일 안에서의 이동"을 가르는 것은 이 함수가 아니라
    `collect_same_file_hunks()` + 호출자(`filter.py`)의 몫이다.
    """
    diff_text = _run_git_diff(repo_path, commit.parent_sha, commit.commit_sha)
    return _collect_added_functions_from_diff(repo_path, commit, diff_text)


def _collect_added_functions_from_diff(
    repo_path: str | Path, commit: CommitPair, diff_text: str
) -> dict[str, list[Function]]:
    """`collect_added_functions()`의 본체. `diff_text` 조건은 `_extract_deletions_from_diff()`와
    같다 (Issue #64)."""
    added_functions: dict[str, list[Function]] = {}
    for path, ranges in _parse_added_line_ranges(diff_text).items():
        source = _read_file_at(repo_path, commit.commit_sha, path)
        touched = [
            function
            for function in _ADAPTER.extract_functions(source)
            if any(_overlaps(function.start_line, function.end_line, lo, hi) for lo, hi in ranges)
        ]
        if touched:
            added_functions[path] = touched
    return added_functions


def collect_same_file_hunks(repo_path: str | Path, commit: CommitPair) -> dict[str, list[Hunk]]:
    """`commit`에서 제자리 수정(옛 경로 == 새 경로)으로 남은 파일마다 그 헝크 목록.

    이동 탐지의 same-position 판정(§4.2②, `filter.py`, Issue #52)이 쓴다 — 삭제된
    함수의 부모 줄 범위와 헝크의 `old_start`~`old_start+old_count-1`이 겹치면, 그
    헝크의 `new_start`~`new_start+new_count-1`이 "이 함수가 제자리에서 수정됐다면
    자식 파일에서 있어야 할 범위"다. `Hunk` 독스트링 참고 — 헝크보다 앞쪽의 무관한
    삽입·삭제로 인한 줄 번호 밀림은 `new_start`에 이미 반영돼 있어 따로 계산할 게 없다.
    """
    diff_text = _run_git_diff(repo_path, commit.parent_sha, commit.commit_sha)
    return _parse_same_file_hunks(diff_text)


def extract_repo_with_excluded(
    repo_path: str | Path, repo: str, ref: str
) -> tuple[list[DeletedFunction], list[ExcludedRecord]]:
    """저장소 하나를 처음부터 끝까지 순차로 훑어 (남은 레코드, 제외 레코드)를 돌려준다.

    `walk_commits(repo_path, ref)`가 내놓는 `CommitPair`마다 `extract_deletions()`로
    삭제를 뽑고, 같은 커밋의 `collect_added_functions()`·`collect_same_file_hunks()`를
    구해 `filter.partition_moved()`로 이동(NOISE_MOVE, §4.2②, Issue #52)을, 그 나머지에
    `filter.partition_trivial()`로 사소한 부분 삭제(NOISE_TRIVIAL, ADR-015, Issue #63)를
    나눈 뒤 두 쪽 다 커밋 순서대로 누적한다 — 커밋 하나 안에서만 후보를 매칭해야 하므로
    (`filter._match_moved`의 "호출자가 이미 그 커밋 하나로 좁혀서 줘야 한다" 계약) 커밋별로
    따로 호출한다. NOISE_MOVE는 FULL_FUNCTION만, NOISE_TRIVIAL은 PARTIAL만 보므로 두
    필터의 적용 순서는 판정에 영향이 없다. 한 커밋의 제외 레코드는 두 사유가 섞여도
    `extract_deletions()` 순서를 유지한다. 세 단계는 커밋당 한 번 받은 같은 diff 텍스트를
    쓴다 — 공개 함수를 그대로 부르면 각자 `git diff`를 다시 실행해 커밋당 3회가 된다
    (Issue #64). 결과는 공개 함수 세 개를 따로 부른 것과 같다. 제외 레코드는 버리지
    않는다(Issue #97) — 파일로 쓰는 것은 호출자가 `write_jsonl`·`write_excluded_jsonl`로
    한다(경로 정책은 이 모듈이 정하지 않는다).
    `ref`는 walk.py와 같은 이유로 호출자가 명시한다 — default branch를 이 함수가
    추측하지 않는다. 병렬화·재시도는 넣지 않는다(4주차 범위, 모듈 독스트링 참고).

    `filter.py`를 함수 안에서(모듈 최상단이 아니라) import한다 — `filter.py`가 이미
    `from pipeline.extract import DeletedFunction, ExcludedRecord, Hunk`로 이 모듈을
    가져다 쓰므로, 최상단에서 서로 가져오면 순환 import가 된다(둘 다 아직 다 안 만들어진
    상태로 서로를 참조하려 들어서 `ImportError`가 난다). 함수 호출 시점까지 미루면 양쪽
    모듈이 이미 완전히 로드된 뒤라 문제없다.
    """
    from pipeline.filter import partition_moved, partition_trivial

    records: list[DeletedFunction] = []
    excluded: list[ExcludedRecord] = []
    for commit in walk_commits(repo_path, ref):
        # 세 단계가 같은 부모→자식 diff를 본다 — 커밋당 한 번만 받아 나눠 준다 (Issue #64)
        diff_text = _run_git_diff(repo_path, commit.parent_sha, commit.commit_sha)
        deletions = _extract_deletions_from_diff(repo_path, repo, commit, diff_text)
        added = _collect_added_functions_from_diff(repo_path, commit, diff_text)
        hunks = _parse_same_file_hunks(diff_text)
        after_move, moved = partition_moved(deletions, added, hunks)
        commit_kept, trivial = partition_trivial(after_move)
        # 두 필터의 제외 레코드를 커밋 안 입력 순서로 되돌린다 — 레코드는 `id`로 식별한다
        # (`docs/filter_rules.md` "제외 레코드 보존" 절). sorted는 안정 정렬이다.
        position = {record.id: index for index, record in enumerate(deletions)}
        records.extend(commit_kept)
        excluded.extend(sorted(moved + trivial, key=lambda item: position[item.record.id]))
    return records, excluded


def extract_repo(repo_path: str | Path, repo: str, ref: str) -> list[DeletedFunction]:
    """저장소 하나를 처음부터 끝까지 순차로 훑는다 (Issue #5 "저장소 1개 끝까지 통과").

    필터를 통과한 레코드만 돌려준다 — 시그니처와 반환 타입은 Issue #97 이전 그대로다.
    Issue #63부터 NOISE_MOVE에 더해 NOISE_TRIVIAL(4줄 이하 PARTIAL)도 빠진다. 동작은
    `extract_repo_with_excluded()`의 첫 번째 값과 같다. 제외 레코드까지 필요하면 그
    함수를 쓴다.
    """
    records, _excluded = extract_repo_with_excluded(repo_path, repo, ref)
    return records


def _added_hunk_to_json(hunk: AddedHunk) -> dict[str, Any]:
    """`AddedHunk` 하나를 JSON 객체로. 키는 필드 5개 그대로다 (Issue #102)."""
    return {
        "old_start": hunk.old_start,
        "old_count": hunk.old_count,
        "new_start": hunk.new_start,
        "new_count": hunk.new_count,
        "added_body": hunk.added_body,
    }


def to_json_dict(record: DeletedFunction) -> dict[str, Any]:
    """`DeletedFunction` 하나를 JSONL 한 줄로 내보낼 dict로 바꾼다.

    내부 필드명을 그대로 쓰지 않는다 — `deleted_hunk`는 §4.4 `DeletionRecord`의
    이름(`deleted_body`)으로 바꿔 내보낸다(모듈 독스트링 "JSONL 저장" 참고). 이
    단계에서 값을 모르는 §4.4 필드(`repo_license`, `context`, `replacement`, `reason`,
    `embedding` 등)는 넣지 않는다 — `None`으로라도 채우면 "이미 처리됨"처럼 보인다.
    """
    return {
        "repo": record.repo,
        "commit_sha": record.commit_sha,
        "parent_sha": record.parent_sha,
        "file_path": record.file_path,
        "function_name": record.function_name,
        "start_line": record.start_line,
        "end_line": record.end_line,
        "deletion_kind": record.deletion_kind,
        "deleted_body": record.deleted_hunk,
        "added_hunks_same_file": [_added_hunk_to_json(h) for h in record.added_hunks_same_file],
        "author_date": record.author_date,
        "commit_message": record.commit_message,
        "id": record.id,
        "function_signature": record.function_signature,
        "is_test_code": record.is_test_code,
        "source_url": record.source_url,
    }


def write_jsonl(records: Iterable[DeletedFunction], out_path: str | Path) -> None:
    """`records`를 JSONL로 쓴다 — `context.py`의 기존 `--out` 출력 루프와 같은 관례.

    UTF-8, `ensure_ascii=False`, 한 줄에 객체 1개(`indent` 없음), 줄 끝 `\\n`. JSON
    array로 감싸지 않는다. 부모 디렉터리가 없으면 만든다. 어느 디렉터리·어떤 파일
    이름을 쓸지는 정하지 않는다 — `out_path`는 호출자가 정한다.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(to_json_dict(record), ensure_ascii=False) + "\n")


def excluded_to_json_dict(excluded: ExcludedRecord) -> dict[str, Any]:
    """`ExcludedRecord` 하나를 excluded JSONL 한 줄로 내보낼 dict로 바꾼다 (Issue #97).

    flat 구조다 — `to_json_dict(excluded.record)`의 키 전부에 `filter_status`·
    `filter_rule_version`·`filter_evidence` 세 키를 더한다. 원본 레코드를 `{"record": ...}`
    로 감싸지 않는다: 추출 JSONL과 같은 키로 읽을 수 있어야 조립 단계와 #89 표본 추출이
    두 파일을 같은 방식으로 다룬다. `reason`이라는 이름은 쓰지 않는다 — §4.4의 `reason`은
    이유 분류(BUG/PERF…) 필드다.
    """
    return {
        **to_json_dict(excluded.record),
        "filter_status": excluded.filter_status,
        "filter_rule_version": excluded.filter_rule_version,
        "filter_evidence": dict(excluded.filter_evidence),
    }


def write_excluded_jsonl(excluded: Iterable[ExcludedRecord], out_path: str | Path) -> None:
    """필터 제외 레코드를 excluded JSONL로 쓴다 (Issue #97). 관례는 `write_jsonl`과 같다.

    UTF-8, `ensure_ascii=False`, 한 줄에 객체 1개, 입력 순서 유지, 부모 디렉터리 자동
    생성. **제외 레코드가 0건이어도 빈 파일을 만든다** — "제외 레코드 없음"과 "excluded
    출력을 아예 안 돌림"을 파일 존재 여부로 구분할 수 있어야 한다. 경로는 호출자가
    정한다(운영 시 추출 JSONL `<stem>.jsonl` 옆에 `<stem>_excluded.jsonl`,
    `docs/filter_rules.md` "제외 레코드 보존" 절).
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in excluded:
            handle.write(json.dumps(excluded_to_json_dict(item), ensure_ascii=False) + "\n")
