"""저장소 전체 히스토리 클론. 담당: 재헌

CHARTER.md §4.2 ① 채굴 파이프라인의 "클론" 단계. `REPOS_DIR/{owner}/{name}`에 얕은
클론이 아닌 전체 히스토리 clone을 만들고 그 경로를 돌려준다.

기존 디렉터리 재사용 정책 (Issue #5 설계 논의, jh 담당 범위 안에서 최소 정책으로 결정):
    `target_dir/.git`이 있다고 바로 재사용하지 않는다. 아래 세 가지를 순서대로 확인한
    뒤에만 재사용한다.
    1. git 저장소가 맞는가 (`git rev-parse --git-dir`) — 아니면 예외. 사람이 정리한다.
    2. shallow clone이 아닌가 (`git rev-parse --is-shallow-repository`) — shallow면
       CHARTER §4.2① "얕은 클론 금지"를 어긴 상태다. 조용히 unshallow/fetch 하지 않고
       예외를 던진다. 자동으로 고쳐 버리면 그 저장소가 언제 어떻게 shallow가 됐는지
       (사람이 실수로 `--depth`를 썼는지) 추적할 수 없게 된다.
    3. origin이 기대한 `clone_url`과 같은가 (`git remote get-url origin`) — 다르면
       디렉터리 이름은 같지만 다른 저장소이거나 origin이 바뀐 상태일 수 있다. 삭제·
       덮어쓰기 대신 예외.
    셋 다 통과하면 fetch 없이 그대로 재사용한다 — 재실행마다 최신화하면 실행 시점에
    따라 채굴 결과가 달라져 재현성이 떨어진다 (증분 갱신이 필요해지면 별도 이슈).
    위 셋 중 하나라도 실패하면 아무것도 지우거나 덮어쓰지 않고 예외를 던진다.

default branch: clone.py는 반환하지도 추측하지도 않는다. `git clone`이 로컬에 원격
기본 브랜치를 체크아웃해 두긴 하지만, 어떤 브랜치를 "기본"으로 볼지는 이미
`select_repos.py`가 만든 CSV(`default_branch` 컬럼, `docs/repo_final20.md`)에 있다.
walk.py가 이미 "ref는 호출자가 항상 명시" 원칙을 세워뒀으므로(`walk_commits`
docstring), clone.py가 같은 정보를 다시 추측해서 반환하면 소스가 두 개로 갈라진다.

디렉터리 이름: `REPOS_DIR/{owner}/{name}` — repo(`"owner/name"`, §4.4 스키마의 `repo`
필드)를 그대로 중첩 경로로 쓴다. GitHub `full_name`은 이미 유일하므로 별도 인코딩이
필요 없다. owner/name 문자 자체의 유효성(GitHub 이름 규칙)은 검증하지 않는다 — 그건
이미 GitHub API를 거친 `select_repos.py` 산출물의 책임이다. clone.py가 보는 건 오직
경로 이탈 여부뿐이다 (`repo_dir` docstring 참고).

pygit2 대신 git CLI(subprocess)를 쓴다 — walk.py와 같은 이유 (CHARTER §7, 새 네이티브
의존성 회피).

범위 밖: 커밋 순회(`walk.py`), diff·헝크 추출(`extract.py`), 증분 갱신(fetch/pull),
병렬화·재시도(`pipeline/run.py`, Issue #81), 손상된 디렉터리 자동 정리.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# git 명령이 자격 증명 프롬프트로 멈추지 않게 한다 (무인 실행 대비). GIT_PAGER는
# walk.py와 같은 이유(파이프 없이 실행돼도 페이저가 뜨지 않게).
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}


def _run_git(
    args: list[str], *, check: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_GIT_ENV,
        check=check,
        timeout=timeout,
    )


def repo_dir(repos_dir: str | Path, repo: str) -> Path:
    """`REPOS_DIR/{owner}/{name}` 경로를 계산한다 (순수 함수, I/O 없음).

    `repo`는 §4.4 스키마의 `"owner/name"` 형식이어야 한다. GitHub owner/repo 이름
    규칙(허용 문자·길이·하이픈 위치 등)은 여기서 재현하지 않는다 — 그건 이미 GitHub
    API를 거친 `select_repos.py` 산출물의 책임이고, clone.py가 다시 검증하면 두
    구현이 어긋날 여지만 생긴다. 여기서 보는 건 오직 "이 문자열로 REPOS_DIR 밑에
    경로를 만들어도 REPOS_DIR을 벗어나지 않는가" 하나뿐이다.

    검증 순서:
    1. `/` 기준 정확히 두 조각(owner, name)이어야 한다.
    2. 두 조각 다 비어 있으면 안 된다.
    3. 조각이 `.`이나 `..`이면 안 된다 (경로 구성 요소로서 특수하게 해석된다).
    4. 조각에 `\\`(Windows 구분자)가 섞여 있으면 안 된다. `/`는 위 1에서 이미 걸러진다.
    5. 위 넷을 다 통과해도, 최종적으로 `(REPOS_DIR / owner / name).resolve()`가
       `REPOS_DIR.resolve()` 하위에 있는지 한 번 더 확인한다 — 문자 단위 검사로
       놓친 이탈 방식이 있어도 여기서 잡히게 하는 마지막 방어선.
    """
    parts = repo.split("/")
    if len(parts) != 2:
        raise ValueError(f"repo는 'owner/name' 형식이어야 한다: {repo!r}")
    owner, name = parts
    for part in (owner, name):
        if not part:
            raise ValueError(f"repo는 'owner/name' 형식이어야 한다: {repo!r}")
        if part in (".", ".."):
            raise ValueError(f"repo 조각이 '.' 또는 '..'일 수 없다: {repo!r}")
        if "\\" in part:
            raise ValueError(f"repo 조각에 경로 구분자를 쓸 수 없다: {repo!r}")

    base = Path(repos_dir).resolve()
    target = (base / owner / name).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"repo가 REPOS_DIR 밖을 가리킨다: {repo!r}")

    return Path(repos_dir) / owner / name


def is_git_repo(path: Path) -> bool:
    """`path`가 git 저장소의 작업 트리 **루트**인가. 존재하지 않거나 디렉터리가 아니면 False.

    `git rev-parse --git-dir` 성공 여부만 보지 않는다 — git은 `.git`을 찾을 때까지 상위로
    올라가므로, git 저장소 안의 평범한 하위 디렉터리(예: `<repo>/nested/`)도 그것만으로는
    True가 나온다. `clone()`의 재사용 판정이 그 하위 디렉터리를 저장소 자체로 착각하면
    엉뚱한(상위) 저장소를 기준으로 shallow·origin을 확인하게 된다. `--show-toplevel`로 실제
    작업 트리 루트를 구해 `path` 자신과 일치할 때만 True로 판정한다.
    """
    if not path.is_dir():
        return False
    result = _run_git(["-C", str(path), "rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return False
    toplevel = result.stdout.strip()
    if not toplevel:
        return False
    return Path(toplevel).resolve() == path.resolve()


def is_shallow_clone(path: Path) -> bool:
    """`path`가 shallow clone인가 (CHARTER §4.2① "얕은 클론 금지" 확인용).

    `path`가 git 저장소라는 것은 호출 전에 이미 확인돼 있어야 한다.
    """
    result = _run_git(["-C", str(path), "rev-parse", "--is-shallow-repository"])
    return result.stdout.strip() == "true"


def origin_url(path: Path) -> str:
    """기존 clone의 origin 리모트 URL. origin이 없으면 예외 (예상 못한 상태)."""
    result = _run_git(["-C", str(path), "remote", "get-url", "origin"], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{path} 에 origin 리모트가 없다: {result.stderr.strip()}")
    return result.stdout.strip()


def _verify_reusable(path: Path, repo: str, clone_url: str) -> None:
    """기존 `path`를 fetch 없이 그대로 재사용해도 되는지 확인한다.

    안 되면 아무것도 지우거나 덮어쓰지 않고 예외를 던진다 — 정리는 사람이 한다.
    순서: git 저장소인가 → shallow가 아닌가 → origin이 기대한 clone_url과 같은가.
    """
    if not is_git_repo(path):
        raise RuntimeError(
            f"{path} 이(가) 이미 있지만 git 저장소가 아니다 ({repo}). "
            "자동으로 지우지 않는다 — 직접 확인하고 정리해라."
        )
    if is_shallow_clone(path):
        raise RuntimeError(
            f"{path} 은(는) shallow clone이다 ({repo}). CHARTER §4.2① 위반 상태다. "
            "자동으로 unshallow/fetch 하지 않는다 — 직접 다시 클론해라."
        )
    existing_origin = origin_url(path)
    if existing_origin != clone_url:
        raise RuntimeError(
            f"{path} 의 origin({existing_origin!r})이 기대한 clone_url({clone_url!r})과 "
            f"다르다 ({repo}). 자동으로 덮어쓰지 않는다 — 직접 확인하고 정리해라."
        )


def clone(
    repo: str, clone_url: str, repos_dir: str | Path, *, timeout: float | None = None
) -> Path:
    """`repo`의 전체 히스토리를 `REPOS_DIR/{owner}/{name}`에 클론하고 경로를 돌려준다.

    이미 유효하게(git 저장소·non-shallow·origin 일치) 클론돼 있으면 fetch 없이 그대로
    재사용한다 — 모듈 docstring "기존 디렉터리 재사용 정책" 참고. 새로 클론할 때는
    shallow/single-branch 제한 옵션을 쓰지 않는다 (CHARTER §4.2① 얕은 클론 금지).

    default branch는 반환하지 않는다 — 호출자가 저장소 선정 단계 산출물(CSV의
    `default_branch`, `docs/repo_final20.md`)에서 가져와 `walk_commits(path, ref)`에
    직접 넘긴다.

    `timeout`(초)은 새로 클론하는 `git clone` 한 번에만 건다 (Issue #81 — 멈춘 clone을
    끊는 안전장치). 넘으면 `subprocess.TimeoutExpired`가 그대로 올라간다. 끊긴 clone이
    남긴 디렉터리는 여기서 지우지 않는다 — 이번 호출이 만든 것인지는 호출자만 안다
    (`pipeline/run.py`). 기본값 `None`은 예전처럼 제한이 없다.
    """
    path = repo_dir(repos_dir, repo)
    if path.exists():
        _verify_reusable(path, repo, clone_url)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", clone_url, str(path)], timeout=timeout)
    return path
