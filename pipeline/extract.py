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

added_hunk_same_file: 함수 단위가 아니라 **커밋 내 그 파일** 단위다. CHARTER §4.2①
필드명 자체가 "같은 파일"이지 "같은 위치"가 아니다. ±N줄 근접도로 특정 함수와 매칭시켜
대체 코드 후보를 뽑는 정교한 작업은 "맥락 결합"(`context.py`, 희수 담당,
`replacement.match_method/confidence`)의 몫이고, 여기서는 그 원재료(파일 전체의 추가
줄)만 만든다. 같은 파일에서 나온 모든 record가 이 값을 동일하게 공유한다. 추가 줄이
없으면 `""`.

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
의존성 회피). `_run_git_diff`/`_read_parent_file`은 그 두 모듈의 자체 subprocess
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
    `added_hunk_same_file`, `author_date`, `commit_message`. 이 단계에서 알 수 없는
    §4.4 필드(`repo_license`, `context`, `replacement`, `reason`, `embedding` 등)는
    `None`이나 빈 값으로 채워 넣지 않는다 — 아직 없는 값을 있는 것처럼 보이게 하지
    않는다는 뜻이고, 그 필드들은 각자 담당 단계(필터·맥락 결합·분류·임베딩)에서 채운다.

    `write_jsonl()`은 `context.py`의 기존 출력 루프(`main()`의 `--out` 처리)와 같은
    관례를 그대로 따른다: UTF-8, `ensure_ascii=False`, 한 줄에 객체 1개, `indent` 없음,
    줄 끝 `\n`, 부모 디렉터리는 자동 생성. JSON array로 쓰지 않는다.

    출력 경로 정책(어느 디렉터리에, 어떤 파일 이름으로)은 여기서 정하지 않는다 —
    `write_jsonl(records, out_path)`는 호출자가 준 경로에 쓰기만 한다.

`extract_repo(repo_path, repo, ref)`: 저장소 하나를 처음부터 끝까지 훑는 최소 순차
루프. `walk_commits()`로 얻은 `CommitPair`마다 `extract_deletions()`를 그대로 돌려
누적한다. `ref`는 clone.py·walk.py와 같은 이유로 호출자가 명시한다 — default branch를
이 함수가 추측하지 않는다. 병렬화·재시도는 넣지 않는다(4주차 범위).

범위 밖: 필터(§4.2②, `filter.py`), 맥락 결합(`context.py`), 분류, DB 적재, 병렬화·
재시도 큐(4주차), 출력 경로/파일명 정책.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.parsers.python_adapter import PythonAdapter
from pipeline.walk import CommitPair, walk_commits

# git 명령이 자격 증명 프롬프트로 멈추지 않게 하고, 페이저가 뜨지 않게 한다 — walk.py·
# clone.py와 같은 이유.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}

# "@@ -old[,count] +new[,count] @@ ..." — old 시작 줄 번호만 있으면 된다(삭제 줄
# 커서를 여기서 seed한다). 뒤에 붙는 컨텍스트(`def foo(x):` 등)는 무시한다.
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")

_ADAPTER = PythonAdapter()


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
    added_hunk_same_file: str
    author_date: str
    commit_message: str


@dataclass
class _FileDiff:
    """diff 한 파일 섹션을 파싱한 중간 결과 (모듈 내부용)."""

    deleted_lines: dict[int, str] = field(default_factory=dict)  # 부모 파일 줄 번호 -> 텍스트
    added_lines: list[str] = field(default_factory=list)  # 등장 순서


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


def _read_parent_file(repo_path: str | Path, parent_sha: str, file_path: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "-C",
            str(repo_path),
            "show",
            f"{parent_sha}:{file_path}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout


def _old_path(spec: str) -> str | None:
    """`--- a/<path>` 줄의 `<path>` 부분. 신규 파일(`--- /dev/null`)이면 None."""
    if spec == "/dev/null":
        return None
    return spec[2:] if spec.startswith("a/") else spec


def parse_file_diffs(diff_text: str) -> dict[str, _FileDiff]:
    """`git diff --unified=0` 출력을 파일별로 나눈다 (순수 함수, 테스트 대상).

    삭제 줄이 하나도 없는 파일(순수 신규 파일 포함)은 결과에 없다 — 그런 파일에서는
    어차피 `DeletedFunction` record가 나올 수 없다.
    """
    files: dict[str, _FileDiff] = {}
    current_path: str | None = None
    current: _FileDiff | None = None
    old_cursor = 0

    def flush() -> None:
        if current_path is not None and current is not None and current.deleted_lines:
            files[current_path] = current

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current_path = None
            current = None
            continue
        if line.startswith("--- "):
            current_path = _old_path(line[4:])
            current = _FileDiff() if current_path is not None else None
            continue
        if line.startswith("+++ "):
            continue  # 파일 식별은 옛(부모) 경로로만 한다 — 모듈 독스트링 참고
        if line.startswith("\\"):
            continue  # "\ No newline at end of file" 등 메타 줄
        if current is None:
            continue  # 신규 파일 섹션이거나 아직 파일 헤더 전
        header = _HUNK_HEADER_RE.match(line)
        if header:
            old_cursor = int(header.group(1))
            continue
        if line.startswith("-"):
            current.deleted_lines[old_cursor] = line[1:]
            old_cursor += 1
            continue
        if line.startswith("+"):
            current.added_lines.append(line[1:])
            continue
        # --unified=0이라 컨텍스트(공백 시작) 줄은 없어야 하지만, 있어도 무시한다.

    flush()
    return files


def _build_records(
    repo: str, commit: CommitPair, file_path: str, source: str, file_diff: _FileDiff
) -> list[DeletedFunction]:
    added_hunk_same_file = "\n".join(file_diff.added_lines)
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
                added_hunk_same_file=added_hunk_same_file,
                author_date=commit.author_date,
                commit_message=commit.commit_message,
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
    file_diffs = parse_file_diffs(diff_text)

    records: list[DeletedFunction] = []
    for file_path, file_diff in file_diffs.items():
        source = _read_parent_file(repo_path, commit.parent_sha, file_path)
        records.extend(_build_records(repo, commit, file_path, source, file_diff))
    return records


def extract_repo(repo_path: str | Path, repo: str, ref: str) -> list[DeletedFunction]:
    """저장소 하나를 처음부터 끝까지 순차로 훑는다 (Issue #5 "저장소 1개 끝까지 통과").

    `walk_commits(repo_path, ref)`가 내놓는 `CommitPair`마다 `extract_deletions()`를
    그대로 돌려 결과를 순서대로 누적한다. `ref`는 walk.py와 같은 이유로 호출자가
    명시한다 — default branch를 이 함수가 추측하지 않는다. 병렬화·재시도는 넣지
    않는다(4주차 범위, 모듈 독스트링 참고).
    """
    records: list[DeletedFunction] = []
    for commit in walk_commits(repo_path, ref):
        records.extend(extract_deletions(repo_path, repo, commit))
    return records


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
        "added_hunk_same_file": record.added_hunk_same_file,
        "author_date": record.author_date,
        "commit_message": record.commit_message,
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
