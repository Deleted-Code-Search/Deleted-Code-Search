"""기본 브랜치 커밋 시간순 순회, 병합 커밋 제외. 담당: 재헌

CHARTER.md §4.2 ① 채굴 파이프라인의 "커밋 순회" 단계. 호출자가 명시한 `ref`(기본
브랜치 이름 등)에서 reachable한 전체 커밋을 `git log`(subprocess)로 훑어, diff를 만들
대상만 추려 낸다. `ref`에 기본값을 두지 않는다 — 저장소가 어떤 브랜치로 checkout돼
있는지와 무관하게 항상 명시적으로 지정하게 해서, "무엇을 기본 브랜치로 볼지"를 이
모듈이 조용히 추측하지 않게 한다 (`walk_commits` 독스트링 참고).

병합 커밋(부모 2개 이상)과 root 커밋(부모 0개)은 diff 대상에서 뺀다. 병합 커밋 자체를
빼는 것이지 그 브랜치 안의 개별 커밋을 빼는 게 아니다 — `git log <ref>`는 기본적으로
merge로 들어온 곁가지 커밋까지 포함해 reachable한 전체 커밋을 내놓으므로, 여기서
`--first-parent`를 쓰지 않는다. 그걸 쓰면 PR로 들어온 커밋 대부분을 놓친다.

순회 순서: `--topo-order --reverse`로 부모가 항상 자식보다 먼저 나오게 한다(§4.2
"시간순 순회"). `author_date`는 출력 필드일 뿐 정렬 기준이 아니다 — CHARTER·Issue #5
어디에도 정렬 키로 쓰라는 명시가 없어 임의로 정하지 않았다.

pygit2 대신 git CLI(subprocess)를 쓴다 — CHARTER §7이 "pygit2 또는 git CLI"로 열어뒀고,
새 네이티브 의존성을 추가하지 않는 쪽을 택했다.

범위 밖: 저장소 클론(`clone.py`), diff·헝크 추출(`extract.py`, 다음 단계), 병렬화·재시도
큐(4주차).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# git log 출력에서 필드/레코드를 나누는 구분자. 커밋 메시지에 나타날 일이 없는 제어 문자.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = f"%H{_FIELD_SEP}%P{_FIELD_SEP}%aI{_FIELD_SEP}%B{_RECORD_SEP}"


@dataclass(frozen=True)
class LogEntry:
    """`git log` 한 줄을 그대로 옮긴 것. 머지·root 여부는 `len(parents)`로 판단한다."""

    sha: str
    parents: tuple[str, ...]
    author_date: str
    message: str


@dataclass(frozen=True)
class CommitPair:
    """diff 대상이 되는 논-머지 커밋 하나와 그 유일한 부모.

    §4.2 ① 출력 튜플의 (commit_sha, parent_sha, author_date, commit_message) 부분.
    """

    commit_sha: str
    parent_sha: str
    author_date: str
    commit_message: str


def parse_git_log(raw: str) -> list[LogEntry]:
    """`_LOG_FORMAT`으로 뽑은 `git log` 출력을 레코드로 쪼갠다. (순수 함수, 테스트 대상)"""
    entries: list[LogEntry] = []
    for chunk in raw.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, parents_raw, author_date, message = chunk.split(_FIELD_SEP, 3)
        parents = tuple(parents_raw.split())
        entries.append(LogEntry(sha, parents, author_date, message.strip("\n")))
    return entries


def to_commit_pairs(entries: Iterable[LogEntry]) -> list[CommitPair]:
    """머지(부모≥2)·root(부모 0) 커밋을 빼고, commit_sha 중복을 제거해 diff 대상만 남긴다.

    (순수 함수, 테스트 대상). 부모가 정확히 1개인 커밋만 diff 가능한 대상이다.
    """
    seen: set[str] = set()
    pairs: list[CommitPair] = []
    for entry in entries:
        if entry.sha in seen:
            continue
        seen.add(entry.sha)
        if len(entry.parents) != 1:
            continue
        pairs.append(CommitPair(entry.sha, entry.parents[0], entry.author_date, entry.message))
    return pairs


def _run_git_log(repo_path: Path, ref: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "log",
            "--topo-order",
            "--reverse",
            f"--pretty=tformat:{_LOG_FORMAT}",
            ref,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GIT_PAGER": "cat"},
        check=True,
    )
    return result.stdout


def walk_commits(repo_path: str | Path, ref: str) -> list[CommitPair]:
    """`repo_path`의 `ref`에서 reachable한 전체 커밋을 훑어 diff 대상만 돌려준다.

    `ref`는 필수다. 기본값을 두지 않는 이유: walk.py는 그 저장소의 "기본 브랜치"가
    무엇인지 알 방법이 없다(로컬 clone만으로는 알 수 없고, 그건 clone.py/호출자의
    몫이다 — 모듈 상단 독스트링 "범위 밖" 참고). 기본값으로 `"HEAD"`를 뒀다면, 그
    워킹 디렉터리가 우연히 다른 브랜치로 checkout돼 있을 때 조용히 엉뚱한 브랜치를
    도는 버그가 생긴다. 호출자가 항상 원하는 브랜치/커밋을 명시하게 강제한다.

    현재 checkout 상태와 무관하게 동작한다 — `git log <ref>`는 워킹 디렉터리가
    다른 브랜치에 있어도 `ref`가 가리키는 이력을 그대로 따라간다.

    반환 순서는 topological reverse(부모 → 자식). 병합 커밋과 root 커밋은 결과에
    없다 — 병합 브랜치 안의 개별 커밋은 reachable하면 그대로 포함된다.
    """
    raw = _run_git_log(Path(repo_path), ref)
    return to_commit_pairs(parse_git_log(raw))
