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

구간 제한(`since`, Issue #148): `recent_only` 저장소(`select_repos.py`, #56)는 채굴 구간이
`mining_since_year`-01-01T00:00:00Z부터다. `since`를 주면 **커미터 날짜**가 그 시각 이상인 커밋만
남긴다. 커미터 날짜를 쓰는 이유: rebase·squash로 들어온 커밋은 작성일이 오래돼도 커미터
날짜가 기본 브랜치에 들어온 시점이고, `git log --since`도 커미터 날짜를 쓴다. 선정 단계의
구간 커밋 수(`window_commits`)는 GitHub commits API의 `since`로 셌다 — 그쪽 날짜 기준과 병합
커밋 포함 여부는 여기서 확인하지 않았으므로 두 수가 같을 것으로 기대하지 않는다.
`git log --since`를 쓰지 않고 커밋마다 Python에서 비교한다 — git은 구간 밖 커밋을
UNINTERESTING으로 표시해 그 조상까지 빼므로, 시계가 어긋나 부모가 자식보다 늦은 커밋이 있으면
구간 안 커밋도 빠진다. 비교는 커밋 오브젝트에 저장된 Unix 초(`%ct`)로 한다 — ISO 형식(`%cI`)은
시간대가 깨진 커밋(requests `5e6ecdad`의 `+051800`)에서 `+518:00`처럼 나와 파싱되지 않는다.
커밋 목록(`git log`)은 전체를 받지만 비싼 것은 커밋당 `git diff`(extract.py)이고, 구간 밖
커밋은 여기서 빠져 diff를 뜨지 않는다.

pygit2 대신 git CLI(subprocess)를 쓴다 — CHARTER §7이 "pygit2 또는 git CLI"로 열어뒀고,
새 네이티브 의존성을 추가하지 않는 쪽을 택했다.

범위 밖: 저장소 클론(`clone.py`), diff·헝크 추출(`extract.py`, 다음 단계), 병렬화·재시도
(`pipeline/run.py`, Issue #81).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# git log 출력에서 필드를 나누는 구분자. 메시지(%B)가 마지막 필드이고 `split(_FIELD_SEP, 4)`
# 으로 자르므로, 메시지 안에 이 문자가 있어도 메시지 필드에 그대로 남는다.
_FIELD_SEP = "\x1f"
# 레코드(커밋) 구분자는 NUL이다. `git log -z`가 tformat의 커밋 종결자를 줄바꿈 대신 NUL로
# 내보낸다. git은 NUL이 든 커밋 메시지를 만들지 않으므로(`git commit`이 거부) 메시지와 충돌할
# 수 없다. 예전 구분자 0x1e(RS)는 실제 메시지에 들어 있을 수 있어서, scikit-learn `27ae0488`
# 의 메시지에 든 0x1e 때문에 레코드가 잘못 쪼개져 순회 전체가 실패했다 (Issue #144).
_RECORD_SEP = "\x00"
_LOG_FORMAT = f"%H{_FIELD_SEP}%P{_FIELD_SEP}%aI{_FIELD_SEP}%ct{_FIELD_SEP}%B"

# git log 출력을 UTF-8로 읽다가 깨진 바이트를 만나면 U+FFFD로 바꾼다 (Issue #144). git은
# encoding 헤더가 없는 커밋의 메시지를 변환하지 않고 그대로 내보낸다. celery `18d2b79f`의
# 메시지는 잘린 UTF-8 바이트(`\xc3`)로 끝나서, 기본값 `errors="strict"`에서는 순회 전체가
# 실패했다. 정상 UTF-8 메시지는 그대로이고, 깨진 바이트 열마다 U+FFFD 1자가 된다(원래 바이트는
# 복원하지 않는다). SHA·부모·날짜 필드와 구분자는 ASCII라서 영향이 없다.
_GIT_DECODE_ERRORS = "replace"


@dataclass(frozen=True)
class LogEntry:
    """`git log` 한 줄을 그대로 옮긴 것. 머지·root 여부는 `len(parents)`로 판단한다."""

    sha: str
    parents: tuple[str, ...]
    author_date: str
    message: str
    committer_time: int = 0  # Unix 초. 구간 판정(`since`)에만 쓰고 CommitPair에는 옮기지 않는다


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
    """`-z`와 `_LOG_FORMAT`으로 뽑은 `git log` 출력을 레코드로 쪼갠다. (순수 함수, 테스트 대상)"""
    entries: list[LogEntry] = []
    for chunk in raw.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, parents_raw, author_date, committer_time, message = chunk.split(_FIELD_SEP, 4)
        parents = tuple(parents_raw.split())
        entries.append(
            LogEntry(
                sha, parents, author_date, message.strip("\n"), committer_time=int(committer_time)
            )
        )
    return entries


def within_window(entries: Iterable[LogEntry], since: datetime) -> list[LogEntry]:
    """커미터 시각(`committer_time`)이 `since` 이상인 커밋만 남긴다. (순수 함수, 테스트 대상)

    커밋마다 따로 판정한다 — 조상·자손 관계는 보지 않는다(모듈 독스트링 "구간 제한").
    `since`는 시간대가 있어야 한다. 없으면 로컬 시각으로 추측해야 하므로 받지 않는다.
    """
    if since.tzinfo is None:
        raise ValueError(f"since에 시간대가 없다: {since.isoformat()}")
    cutoff = since.timestamp()
    return [entry for entry in entries if entry.committer_time >= cutoff]


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
            "-z",
            f"--pretty=tformat:{_LOG_FORMAT}",
            ref,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors=_GIT_DECODE_ERRORS,
        env={**os.environ, "GIT_PAGER": "cat"},
        check=True,
    )
    return result.stdout


def walk_commits(
    repo_path: str | Path, ref: str, *, since: datetime | None = None
) -> list[CommitPair]:
    """`repo_path`의 `ref`에서 reachable한 전체 커밋을 훑어 diff 대상만 돌려준다.

    `since`를 주면 커미터 날짜가 그 시각 이상인 커밋만 남긴다(`within_window`). `None`이면
    전체 이력이다 — 구간 제한 이전과 같다.

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
    entries = parse_git_log(_run_git_log(Path(repo_path), ref))
    if since is not None:
        entries = within_window(entries, since)
    return to_commit_pairs(entries)
