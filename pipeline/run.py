"""저장소 목록 배치 채굴 — 저장소 단위 병렬, 재시도, 실행 기록, 이어 하기. 담당: 재헌

CHARTER.md §4.2 ① "병렬화: 저장소 단위로 워커 분배. 실패한 저장소는 재시도 큐" +
"저장소당 처리 시간·실패율을 기록 (§10 시스템 평가용)", Issue #81. 새 채굴 로직은 없다 —
기존 단계를 저장소 하나 단위로 묶어 돌릴 뿐이다:

    clone.clone → (ref를 커밋 SHA로 고정) → walk.walk_commits(커밋 수)
    → extract.extract_repo_with_excluded(추출 + 이동·사소한 부분 삭제 필터)
    → extract.write_jsonl / write_excluded_jsonl

`extract_repo_with_excluded`는 그대로 부른다 — 커밋당 `git diff` 1회(Issue #64)는 그 함수
안의 보장이고, 이 모듈은 커밋 루프 바깥(저장소 단위)만 다룬다.

입력: `select_repos.py`가 만든 CSV(`docs/repo_final20_v2.csv`)의 `repo`·`default_branch`
두 열만 쓴다. clone URL은 `https://github.com/{repo}.git` — 기존 클론의 origin과 같은
문자열이라야 `clone.py`의 재사용 검사를 통과한다.

ref: `origin/{default_branch}`를 커밋 SHA로 한 번 풀어(`repository_head_sha`) walk·추출
모두 그 SHA로 돈다. 기록한 SHA와 실제로 훑은 이력이 어긋날 수 없게 하려는 것이다. 로컬
브랜치가 아니라 원격 추적 브랜치를 쓰는 이유: 새 클론에서 로컬 브랜치는 체크아웃된 하나만
있고, 재사용한 클론의 로컬 브랜치에는 사람이 만든 커밋이 섞여 있을 수 있다.

출력 (`out_dir` 아래, 저장소마다 `stem = repo.replace("/", "__")`):
    <stem>.jsonl            필터 통과 레코드 (`write_jsonl`)
    <stem>_excluded.jsonl   필터 제외 레코드 (`write_excluded_jsonl`, 0건이면 빈 파일)
    <stem>_run.json         저장소별 실행 기록 (아래 "실행 기록")
    batch_summary.json      배치 하나의 요약과 끝내 실패한 저장소 목록
기본 `out_dir`은 `data/pipeline_run`, 기본 `repos_dir`은 `repos` — 둘 다 `.gitignore` 대상이다.

실행 기록과 이어 하기:
    이어 하기의 기준은 **SUCCESS 실행 기록 하나뿐이다** — 출력 파일이 있다는 것만으로 끝난
    것으로 보지 않는다. 쓰는 순서가 그 전제를 지킨다: 시작하면 먼저 기록을 RUNNING으로
    덮어쓴다 → 두 JSONL을 임시 파일에 다 쓴 뒤 `os.replace`로 하나씩 확정 → 마지막에 기록을
    SUCCESS로 바꾼다(기록도 임시 파일 → `os.replace`). 중간에 끊기면 기록은 RUNNING으로 남는다.
    이전 실행의 kept/excluded 파일은 미리 지우지 않는다 — 새 실행이 실패하면 임시 파일만
    치우고 이전 파일은 바이트 그대로 남는다(버전이 달라도 둔다. 버전 불일치는 조립 단계
    #101이 거른다). 교체 도중 예외가 나도 쌍이 섞이지 않게 이전 kept를 `.bak`으로 옮겨 두고
    되돌린다(`_publish_outputs`). 교체 사이에 프로세스가 강제 종료되는 경우까지 다루는
    다중 파일 트랜잭션은 아니다 — 그때는 기록이 RUNNING이라 완료로 보지 않고, 이전 kept는
    `.bak`에 남는다. 건너뛰려면(`is_completed`) SUCCESS이고, 기록의 `repo`·`default_branch`·
    `ref`가 지금 job과 같고, `filter_rule_version`이 지금 `FILTER_RULE_VERSION`과 같고, 두
    출력 파일의 줄 수가 기록된 건수와 같아야 한다. 선정 CSV의 브랜치나 규칙 버전이 바뀌면
    다시 돈다. 코드 SHA나 upstream HEAD가 바뀐 것만으로는 다시 돌지 않는다 — 그 값은
    "무엇으로 무엇을 처리했나"를 남기는 용도다.

재시도:
    어느 단계에서 났는가 + 예외 종류로 가른다(`is_retryable`). 재시도하는 것은 **새로
    클론하던 중의** `CalledProcessError`(네트워크 등)와 `TimeoutExpired`뿐이다. 기존 디렉터리
    재사용 검사 실패, 잘못된 ref, 로컬 git의 walk·추출 실패, `UnicodeDecodeError`, 그 밖의
    예외는 다시 돌려도 같은 결과라 바로 실패로 기록한다. 첫 시도 포함 최대 `max_attempts`회,
    n번째 재시도 전에 `retry_delays[n-1]`초 쉰다(기본 3회, 10초 → 60초). `Exception`만
    잡는다 — `KeyboardInterrupt`·`SystemExit`는 그대로 올라간다.

clone timeout: 새로 클론하는 `git clone`에만 건다(기본 1800초). 추출에는 걸지 않고 걸린
시간만 남긴다. 끊겼거나 실패한 clone이 디렉터리를 남기면, **이번 시도 시작 때 그 경로가
없었을 때만** 지운다 — 이미 있던 디렉터리(사람이 둔 정상 클론 포함)는 건드리지 않는다.

병렬: `workers == 1`이면 풀 없이 입력 순서대로 이 프로세스에서 돈다. 2 이상이면
`ProcessPoolExecutor`에 저장소 하나를 job 하나로 넘긴다. 워커는 자기 저장소의 파일만 쓰고
실행 기록을 돌려준다. 요약과 실패 목록은 부모만 쓴다. 풀은 `run_batch()` 안에서만 만든다.

실행 디렉터리는 writer 하나를 전제한다: 같은 `out_dir`·`repos_dir`로 배치를 동시에 둘 이상
돌리는 것은 지원하지 않는다(잠금 없음). 두 배치는 같은 클론 경로·임시 파일 이름·실행 기록·
요약을 서로 덮어쓰고, 한쪽의 clone 실패 정리가 다른 쪽이 받는 중인 클론을 지울 수도 있다.
병렬은 배치 하나 안에서 `--workers`로 한다.

범위 밖: 결과 조립·`filter_status` 부여(#101), 맥락 결합(`context.py`), 끝난 클론 삭제.

사용:
    python -m pipeline.run --input docs/repo_final20_v2.csv --workers 2
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.clone import clone, repo_dir
from pipeline.extract import (
    DeletedFunction,
    ExcludedRecord,
    extract_repo_with_excluded,
    write_excluded_jsonl,
    write_jsonl,
)
from pipeline.filter import FILTER_RULE_VERSION, NOISE_MOVE, NOISE_TRIVIAL
from pipeline.walk import walk_commits

DEFAULT_OUT_DIR = Path("data/pipeline_run")
DEFAULT_REPOS_DIR = Path("repos")
DEFAULT_WORKERS = 1
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAYS: tuple[float, ...] = (10.0, 60.0)
DEFAULT_CLONE_TIMEOUT_SECONDS = 1800.0

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

KEPT_SUFFIX = ".jsonl"
EXCLUDED_SUFFIX = "_excluded.jsonl"
RUN_SUFFIX = "_run.json"
SUMMARY_FILENAME = "batch_summary.json"

# 실패가 난 단계. 실행 기록의 `failure_stage` 값이고 재시도 판정의 절반이다.
STAGE_INPUT = "input"  # repo 형식 검사 (`clone.repo_dir`)
STAGE_CLONE = "clone"  # 이번 시도에서 새로 `git clone`
STAGE_REUSE_CLONE = "reuse_clone"  # 이미 있던 디렉터리의 재사용 검사
STAGE_CLONE_CLEANUP = "clone_cleanup"  # 실패한 새 clone이 남긴 디렉터리 정리
STAGE_RESOLVE_REF = "resolve_ref"
STAGE_WALK = "walk"
STAGE_EXTRACT = "extract"
STAGE_COUNT = "count"
STAGE_WRITE = "write"
STAGE_WORKER = "worker"  # 워커 프로세스 자체가 죽음 (부모가 기록)

# 이 모듈이 속한 저장소 루트. `pipeline_code_sha`를 구하는 곳이다.
_PIPELINE_ROOT = Path(__file__).resolve().parent.parent

_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}

# 실패 사유에 붙이는 git stderr 꼬리 길이. 실행 기록이 커지지 않게 자른다.
_STDERR_TAIL_CHARS = 1000


@dataclass(frozen=True)
class RepoJob:
    """저장소 하나의 입력. `clone_url`은 CSV에서 읽을 때 GitHub URL로 채운다."""

    repo: str  # "owner/name"
    default_branch: str
    clone_url: str

    @property
    def stem(self) -> str:
        """출력 파일 이름의 stem (`repo_stem`)."""
        return repo_stem(self.repo)

    @property
    def ref(self) -> str:
        """처리할 ref. 로컬 브랜치가 아니라 원격 추적 브랜치다 (모듈 독스트링 "ref")."""
        return f"origin/{self.default_branch}"


@dataclass(frozen=True)
class RunSettings:
    """배치 실행 옵션. `validate()`가 조합의 모호함을 막는다."""

    out_dir: Path = DEFAULT_OUT_DIR
    repos_dir: Path = DEFAULT_REPOS_DIR
    workers: int = DEFAULT_WORKERS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS
    clone_timeout_seconds: float = DEFAULT_CLONE_TIMEOUT_SECONDS

    def validate(self) -> None:
        """`retry_delays`는 재시도 횟수(`max_attempts - 1`)와 정확히 같은 개수여야 한다 —
        남거나 모자라면 어떤 값을 쓸지 추측해야 하므로 받지 않는다."""
        if self.workers < 1:
            raise ValueError(f"workers는 1 이상이어야 한다: {self.workers}")
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts는 1 이상이어야 한다: {self.max_attempts}")
        if len(self.retry_delays) != self.max_attempts - 1:
            raise ValueError(
                f"retry_delays는 max_attempts - 1 = {self.max_attempts - 1}개여야 한다: "
                f"{list(self.retry_delays)}"
            )
        for delay in self.retry_delays:
            if not math.isfinite(delay) or delay < 0:
                raise ValueError(f"retry_delays 값은 0 이상의 유한한 수여야 한다: {delay}")
        timeout = self.clone_timeout_seconds
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"clone_timeout_seconds는 0보다 커야 한다: {timeout}")


@dataclass(frozen=True)
class CodeState:
    """파이프라인 코드의 git 상태. 구할 수 없으면 둘 다 `None`이다 (`False`로 추측하지 않는다)."""

    sha: str | None
    dirty: bool | None


class _StageFailure(Exception):
    """시도 하나가 `stage`에서 `error`로 끝났다."""

    def __init__(self, stage: str, error: BaseException) -> None:
        """`stage`는 `STAGE_*` 값, `error`는 그 단계에서 난 원래 예외다."""
        super().__init__(stage, error)
        self.stage = stage
        self.error = error


def repo_stem(repo: str) -> str:
    """출력 파일 이름의 stem. `"pydantic/pydantic"` → `"pydantic__pydantic"`."""
    return repo.replace("/", "__")


def github_clone_url(repo: str) -> str:
    """CSV 입력의 clone URL. 기존 클론의 origin과 같은 문자열이라야 재사용 검사를 통과한다."""
    return f"https://github.com/{repo}.git"


def output_paths(out_dir: str | Path, repo: str) -> tuple[Path, Path, Path]:
    """(추출 JSONL, 제외 JSONL, 실행 기록) 경로."""
    base = Path(out_dir)
    stem = repo_stem(repo)
    return (
        base / f"{stem}{KEPT_SUFFIX}",
        base / f"{stem}{EXCLUDED_SUFFIX}",
        base / f"{stem}{RUN_SUFFIX}",
    )


def validate_jobs(jobs: Sequence[RepoJob], repos_dir: str | Path) -> None:
    """repo 형식(`clone.repo_dir`의 검사 그대로)·빈 브랜치·중복 repo·stem 충돌을 거른다.

    같은 repo가 두 번 있으면 두 워커가 같은 클론 디렉터리와 같은 출력 파일을 동시에 쓴다.
    `a__b/c`와 `a/b__c`처럼 다른 repo가 같은 stem이 되는 경우도 같은 이유로 막는다.
    """
    seen_repos: set[str] = set()
    seen_stems: dict[str, str] = {}
    for job in jobs:
        repo_dir(repos_dir, job.repo)
        if not job.default_branch:
            raise ValueError(f"default_branch가 비어 있다: {job.repo}")
        if job.repo in seen_repos:
            raise ValueError(f"같은 repo가 두 번 있다: {job.repo}")
        seen_repos.add(job.repo)
        other = seen_stems.setdefault(job.stem, job.repo)
        if other != job.repo:
            raise ValueError(f"출력 stem이 겹친다: {other} / {job.repo} → {job.stem}")


def load_repo_jobs(csv_path: str | Path, repos_dir: str | Path) -> list[RepoJob]:
    """저장소 선정 CSV에서 `repo`·`default_branch` 두 열만 읽는다. 다른 열은 보지 않는다."""
    path = Path(csv_path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"repo", "default_branch"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: 필요한 열이 없다: {sorted(missing)}")
        jobs: list[RepoJob] = []
        for line_no, row in enumerate(reader, start=2):
            repo = (row.get("repo") or "").strip()
            branch = (row.get("default_branch") or "").strip()
            if not repo or not branch:
                raise ValueError(f"{path}:{line_no}: repo와 default_branch가 모두 있어야 한다")
            jobs.append(RepoJob(repo, branch, github_clone_url(repo)))
    validate_jobs(jobs, repos_dir)
    return jobs


def parse_retry_delays(text: str) -> tuple[float, ...]:
    """`"10,60"` → `(10.0, 60.0)`. 빈 문자열은 재시도 없음(`()`)이다."""
    if not text.strip():
        return ()
    try:
        delays = tuple(float(part) for part in text.split(","))
    except ValueError as error:
        raise ValueError(f"retry delays는 쉼표로 나눈 초 단위 숫자여야 한다: {text!r}") from error
    for delay in delays:
        if not math.isfinite(delay) or delay < 0:
            raise ValueError(f"retry delays 값은 0 이상의 유한한 수여야 한다: {text!r}")
    return delays


def is_retryable(stage: str, error: BaseException) -> bool:
    """다시 시도할 만한 실패인가. 예외 종류만으로 정하지 않는다 — 같은
    `CalledProcessError`라도 새 clone(네트워크)이면 일시적이고, 이미 받아 둔 저장소에서 돈
    walk·추출·ref 확인이면 다시 돌려도 같다."""
    return stage == STAGE_CLONE and isinstance(
        error, subprocess.CalledProcessError | subprocess.TimeoutExpired
    )


def _run_git(repo_path: Path, *args: str) -> str:
    """`git -C repo_path ...`의 stdout. 실패하면 `CalledProcessError`가 그대로 올라간다."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout


def pipeline_code_state(root: str | Path | None = None) -> CodeState:
    """`root`(기본: 이 저장소 루트)의 HEAD SHA와 tracked 파일 수정 여부.

    dirty 판정은 `git status --porcelain --untracked-files=no`가 한 줄이라도 내면 참이다 —
    staged·unstaged 수정만 보고, untracked 파일과 `.gitignore` 대상(`data/`·`repos/`의 실행
    결과)은 보지 않는다. `root`가 git 작업 트리의 루트가 아니거나(설치된 패키지 등) git을
    못 부르면 `CodeState(None, None)` — 값을 모르는 것이지 깨끗한 것이 아니다.
    """
    base = Path(root) if root is not None else _PIPELINE_ROOT
    try:
        toplevel = _run_git(base, "rev-parse", "--show-toplevel").strip()
        if not toplevel or Path(toplevel).resolve() != base.resolve():
            return CodeState(None, None)
        sha = _run_git(base, "rev-parse", "HEAD").strip()
        status = _run_git(base, "status", "--porcelain", "--untracked-files=no")
    except (subprocess.CalledProcessError, OSError):
        return CodeState(None, None)
    return CodeState(sha, bool(status.strip()))


def _count_lines(path: Path) -> int:
    """줄바꿈 개수 = JSONL 행 수 (writer가 행마다 줄바꿈을 붙인다). 큰 파일도 조각으로 센다."""
    count = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            count += chunk.count(b"\n")
    return count


def _is_count(value: Any) -> bool:
    """실행 기록의 건수 필드로 믿을 수 있는 값인가 — 0 이상의 int (`bool`은 제외)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_completed(out_dir: str | Path, job: RepoJob) -> bool:
    """`job`을 건너뛰어도 되는가. 하나라도 어긋나면 `False`(다시 돈다).

    SUCCESS 기록 · 기록의 `repo`·`default_branch`·`ref`가 `job`과 같음 · 지금의
    `FILTER_RULE_VERSION` · 건수 필드가 정상이고 `excluded_count == noise_move_count +
    noise_trivial_count` · 두 출력 파일이 있고 줄 수가 기록된 건수와 같음. 제외 0건이면 빈 제외
    파일이 정상이다. 처리한 SHA(`repository_head_sha`)는 비교하지 않는다 — 같은 ref의 upstream이
    움직였다는 것만으로는 다시 돌지 않는다.
    """
    kept_path, excluded_path, run_path = output_paths(out_dir, job.repo)
    try:
        metadata = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(metadata, dict):
        return False
    if metadata.get("status") != STATUS_SUCCESS:
        return False
    identity = (metadata.get("repo"), metadata.get("default_branch"), metadata.get("ref"))
    if identity != (job.repo, job.default_branch, job.ref):
        return False
    if metadata.get("filter_rule_version") != FILTER_RULE_VERSION:
        return False
    counts = [
        metadata.get(key)
        for key in ("kept_count", "excluded_count", "noise_move_count", "noise_trivial_count")
    ]
    if not all(_is_count(value) for value in counts):
        return False
    kept_count, excluded_count, move_count, trivial_count = counts
    if excluded_count != move_count + trivial_count:
        return False
    try:
        return (
            _count_lines(kept_path) == kept_count and _count_lines(excluded_path) == excluded_count
        )
    except OSError:
        return False


def _now() -> str:
    """시간대가 붙은 UTC ISO 8601 시각 (초 단위)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """임시 파일에 다 쓴 뒤 `os.replace`로 바꾼다 — 읽는 쪽은 이전 내용이나 새 내용만 본다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_outputs(
    out_dir: Path, repo: str, kept: Iterable[DeletedFunction], excluded: Iterable[ExcludedRecord]
) -> None:
    """두 JSONL을 임시 파일에 다 쓴 뒤에만 최종 경로로 바꾼다 — 쓰다 끊긴 파일이 최종 경로에
    남지 않고, 쓰기나 교체가 실패하면 임시 파일만 지워져 이전 실행의 최종 파일 쌍은 그대로다
    (`_publish_outputs`). 형식은 기존 writer 그대로다."""
    kept_path, excluded_path, _run_path = output_paths(out_dir, repo)
    kept_tmp = kept_path.with_name(kept_path.name + ".tmp")
    excluded_tmp = excluded_path.with_name(excluded_path.name + ".tmp")
    try:
        write_jsonl(kept, kept_tmp)
        write_excluded_jsonl(excluded, excluded_tmp)
        _publish_outputs(kept_tmp, kept_path, excluded_tmp, excluded_path)
    finally:
        kept_tmp.unlink(missing_ok=True)
        excluded_tmp.unlink(missing_ok=True)


def _publish_outputs(
    kept_tmp: Path, kept_path: Path, excluded_tmp: Path, excluded_path: Path
) -> None:
    """임시 파일 두 개를 최종 경로로 바꾼다. 예외가 나면 이전 kept/excluded 쌍을 되돌려 둔다.

    excluded는 마지막에 한 번 `os.replace`하므로 실패하면 이전 파일이 그대로다. kept는 그보다
    먼저 바뀌므로, 이전 kept를 같은 디렉터리의 `.bak`으로 옮겨 두고(이름 바꾸기라 복사 비용이
    없다) 뒤 단계가 실패하면 되돌린다. 이전 kept가 없었으면 새 kept를 지운다. `.bak`은 성공했을
    때만 지운다. 한계: 되돌리기 자체가 OS 오류로 실패하거나 교체 사이에 프로세스가 강제
    종료되면 이전 kept는 `.bak`에 남는다 — 그때도 실행 기록이 FAILED/RUNNING이라 완료로 보지
    않는다.
    """
    kept_backup = kept_path.with_name(kept_path.name + ".bak")
    had_previous = kept_path.exists()
    if had_previous:
        os.replace(kept_path, kept_backup)
    try:
        os.replace(kept_tmp, kept_path)
        os.replace(excluded_tmp, excluded_path)
    except BaseException:
        if had_previous:
            os.replace(kept_backup, kept_path)
        else:
            kept_path.unlink(missing_ok=True)
        raise
    kept_backup.unlink(missing_ok=True)


def _make_writable_and_retry(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Windows에서 git 오브젝트 파일이 읽기 전용이라 `rmtree`가 실패하는 것을 피한다."""
    del exc
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _count_records(excluded: Sequence[ExcludedRecord]) -> tuple[int, int]:
    """(NOISE_MOVE 수, NOISE_TRIVIAL 수). 다른 `filter_status`가 오면 건수 불변식이 깨지므로
    조용히 넘기지 않고 실패로 올린다."""
    move = sum(1 for item in excluded if item.filter_status == NOISE_MOVE)
    trivial = sum(1 for item in excluded if item.filter_status == NOISE_TRIVIAL)
    if move + trivial != len(excluded):
        unknown = sorted({item.filter_status for item in excluded} - {NOISE_MOVE, NOISE_TRIVIAL})
        raise RuntimeError(f"알 수 없는 filter_status: {unknown}")
    return move, trivial


def _attempt(job: RepoJob, settings: RunSettings, progress: dict[str, Any]) -> None:
    """시도 한 번. 결과 건수와 SHA는 `progress`에 채운다(실패해도 거기까지 구한 값은 남는다).
    실패는 단계와 함께 `_StageFailure`로 올린다."""
    stage = STAGE_INPUT
    try:
        path = repo_dir(settings.repos_dir, job.repo)
        existed = path.exists()
        stage = STAGE_REUSE_CLONE if existed else STAGE_CLONE
        try:
            clone(
                job.repo,
                job.clone_url,
                settings.repos_dir,
                timeout=settings.clone_timeout_seconds,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as clone_error:
            # 이번 시도가 만들다 만 디렉터리만 지운다. 시작할 때 있던 것은 사람 몫이다.
            if not existed and path.exists():
                try:
                    shutil.rmtree(path, onexc=_make_writable_and_retry)
                except OSError as cleanup_error:
                    stage = STAGE_CLONE_CLEANUP
                    raise RuntimeError(
                        f"실패한 clone이 남긴 {path} 을(를) 지우지 못했다: {cleanup_error} "
                        f"(clone 실패: {_describe(clone_error)}). 직접 정리해라."
                    ) from clone_error
            raise

        stage = STAGE_RESOLVE_REF
        head_sha = _run_git(path, "rev-parse", "--verify", f"{job.ref}^{{commit}}").strip()
        progress["repository_head_sha"] = head_sha

        # walk·추출 모두 같은 SHA로 돈다 — 기록한 SHA와 처리한 이력이 같다.
        stage = STAGE_WALK
        progress["commit_count"] = len(walk_commits(path, head_sha))

        stage = STAGE_EXTRACT
        kept, excluded = extract_repo_with_excluded(path, job.repo, head_sha)

        stage = STAGE_COUNT
        move_count, trivial_count = _count_records(excluded)

        stage = STAGE_WRITE
        _write_outputs(settings.out_dir, job.repo, kept, excluded)
    except Exception as error:
        raise _StageFailure(stage, error) from error
    progress.update(
        kept_count=len(kept),
        excluded_count=len(excluded),
        noise_move_count=move_count,
        noise_trivial_count=trivial_count,
    )


def _describe(error: BaseException) -> str:
    """실패 사유 문자열. git 오류면 stderr 꼬리(`_STDERR_TAIL_CHARS`)를 붙인다."""
    text = str(error) or type(error).__name__
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, str) and stderr.strip():
        text += f" | stderr: {stderr.strip()[-_STDERR_TAIL_CHARS:]}"
    return text


def _metadata(
    job: RepoJob,
    code_state: CodeState,
    *,
    status: str,
    started_at: str | None,
    finished_at: str | None = None,
    elapsed_seconds: float | None = None,
    retry_wait_seconds: float | None = None,
    attempt_count: int | None = None,
    progress: dict[str, Any] | None = None,
    failure: _StageFailure | None = None,
) -> dict[str, Any]:
    """저장소별 실행 기록. 키는 상태와 무관하게 늘 같고, 해당 없는 값은 `null`이다.

    `elapsed_seconds`는 재시도 대기를 포함한 wall-clock이고, `retry_wait_seconds`는 그중
    재시도 전에 쉬기로 한 대기(`retry_delays` 값)의 합이다 — 재시도가 없으면 0.0."""
    progress = progress or {}
    return {
        "repo": job.repo,
        "default_branch": job.default_branch,
        "ref": job.ref,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds,
        "retry_wait_seconds": retry_wait_seconds,
        "attempt_count": attempt_count,
        "retry_count": attempt_count - 1 if attempt_count is not None else None,
        "commit_count": progress.get("commit_count"),
        "kept_count": progress.get("kept_count"),
        "excluded_count": progress.get("excluded_count"),
        "noise_move_count": progress.get("noise_move_count"),
        "noise_trivial_count": progress.get("noise_trivial_count"),
        "failure_stage": failure.stage if failure else None,
        "failure_type": type(failure.error).__name__ if failure else None,
        "failure_reason": _describe(failure.error) if failure else None,
        "repository_head_sha": progress.get("repository_head_sha"),
        "filter_rule_version": FILTER_RULE_VERSION,
        "pipeline_code_sha": code_state.sha,
        "pipeline_dirty": code_state.dirty,
    }


def run_repo_job(
    job: RepoJob,
    settings: RunSettings,
    code_state: CodeState,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """저장소 하나를 재시도까지 포함해 끝까지 돌리고 실행 기록을 쓴 뒤 돌려준다.

    워커 프로세스의 진입점이라 모듈 최상위에 있고, 인자·반환값은 pickle 가능하다.
    `started_at`~`finished_at`·`elapsed_seconds`는 첫 시도 시작부터 마지막 시도 끝까지이고
    재시도 대기 시간을 포함한다. 성공한 경우의 건수는 성공한 시도의 값이다.
    """
    run_path = output_paths(settings.out_dir, job.repo)[2]
    started_at = _now()
    started = time.monotonic()
    # SUCCESS 기록부터 무효로 만든다 — 이 뒤로 끊기면 RUNNING이 남는다. 이전 kept/excluded
    # 출력은 지우지 않는다: 새 결과가 끝까지 만들어졌을 때만 `_write_outputs`가 교체한다.
    _write_json_atomic(
        run_path,
        _metadata(
            job, code_state, status=STATUS_RUNNING, started_at=started_at, retry_wait_seconds=0.0
        ),
    )

    attempt = 0
    retry_wait = 0.0
    while True:
        attempt += 1
        progress: dict[str, Any] = {}
        try:
            _attempt(job, settings, progress)
        except _StageFailure as failure:
            if attempt < settings.max_attempts and is_retryable(failure.stage, failure.error):
                delay = settings.retry_delays[attempt - 1]
                sleep(delay)
                retry_wait += delay
                continue
            status, final_failure = STATUS_FAILED, failure
        else:
            status, final_failure = STATUS_SUCCESS, None
        break

    metadata = _metadata(
        job,
        code_state,
        status=status,
        started_at=started_at,
        finished_at=_now(),
        elapsed_seconds=round(time.monotonic() - started, 3),
        retry_wait_seconds=retry_wait,
        attempt_count=attempt,
        progress=progress,
        failure=final_failure,
    )
    _write_json_atomic(run_path, metadata)
    return metadata


def _worker_crash_metadata(
    job: RepoJob, settings: RunSettings, code_state: CodeState, error: BaseException
) -> dict[str, Any]:
    """워커 프로세스가 기록을 돌려주지 못하고 죽었을 때 부모가 남기는 FAILED 기록.
    몇 번 시도했는지는 알 수 없어 `attempt_count`는 `null`이다."""
    metadata = _metadata(
        job,
        code_state,
        status=STATUS_FAILED,
        started_at=None,
        finished_at=_now(),
        failure=_StageFailure(STAGE_WORKER, error),
    )
    _write_json_atomic(output_paths(settings.out_dir, job.repo)[2], metadata)
    return metadata


def _log_stderr(message: str) -> None:
    """기본 진행 로그. 결과 파일과 섞이지 않게 stderr로 보낸다."""
    print(message, file=sys.stderr, flush=True)


def run_batch(
    jobs: Sequence[RepoJob],
    settings: RunSettings,
    *,
    code_state: CodeState | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _log_stderr,
) -> dict[str, Any]:
    """저장소 목록을 돌리고 `batch_summary.json`을 써서 그 내용을 돌려준다.

    이미 끝난 저장소(`is_completed`)는 건너뛴다. `workers == 1`이면 입력 순서대로 이
    프로세스에서, 2 이상이면 프로세스 풀에서 돈다 — `sleep`은 순차 경로에서만 쓰인다(워커는
    `time.sleep`). 요약의 `failure_rate`는 `failed_count / total_repos`이고, 건너뛴 저장소는
    이전 실행에서 성공한 것이라 분모에 들어간다.
    """
    settings.validate()
    validate_jobs(jobs, settings.repos_dir)
    if code_state is None:
        code_state = pipeline_code_state()
        if code_state.sha is None:
            log("경고: 파이프라인 코드의 git SHA를 구하지 못했다 — null로 기록한다")

    batch_started_at = _now()
    batch_started = time.monotonic()
    skipped = [job.repo for job in jobs if is_completed(settings.out_dir, job)]
    skipped_set = set(skipped)
    pending = [job for job in jobs if job.repo not in skipped_set]
    for repo in skipped:
        log(f"{repo}: 이미 끝남 — 건너뛴다")

    results: dict[str, dict[str, Any]] = {}

    def record(metadata: dict[str, Any]) -> None:
        """저장소 하나의 최종 실행 기록을 모으고 진행 로그를 남긴다."""
        results[metadata["repo"]] = metadata
        log(
            f"{metadata['repo']}: {metadata['status']} "
            f"(시도 {metadata['attempt_count']}회, {metadata['elapsed_seconds']}초)"
        )

    if settings.workers == 1 or not pending:
        for job in pending:
            record(run_repo_job(job, settings, code_state, sleep=sleep))
    else:
        executor = ProcessPoolExecutor(max_workers=min(settings.workers, len(pending)))
        try:
            futures = {
                executor.submit(run_repo_job, job, settings, code_state): job for job in pending
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    metadata = future.result()
                except Exception as error:  # 워커 프로세스가 죽음 (BrokenProcessPool 등)
                    metadata = _worker_crash_metadata(job, settings, code_state, error)
                record(metadata)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown()

    failed = [results[job.repo] for job in pending if results[job.repo]["status"] != STATUS_SUCCESS]
    summary = {
        "started_at": batch_started_at,
        "finished_at": _now(),
        "elapsed_seconds": round(time.monotonic() - batch_started, 3),
        "total_repos": len(jobs),
        "success_count": len(pending) - len(failed),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "failure_rate": len(failed) / len(jobs) if jobs else 0.0,
        "failed_repos": [
            {
                key: item[key]
                for key in (
                    "repo",
                    "failure_stage",
                    "failure_type",
                    "failure_reason",
                    "attempt_count",
                )
            }
            for item in failed
        ],
        "skipped_repos": skipped,
        "filter_rule_version": FILTER_RULE_VERSION,
        "pipeline_code_sha": code_state.sha,
        "pipeline_dirty": code_state.dirty,
        "settings": {
            "workers": settings.workers,
            "max_attempts": settings.max_attempts,
            "retry_delays": list(settings.retry_delays),
            "clone_timeout_seconds": settings.clone_timeout_seconds,
        },
    }
    _write_json_atomic(Path(settings.out_dir) / SUMMARY_FILENAME, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    """배치 CLI 인자. 기본값은 모듈 상수(`DEFAULT_*`)와 같다."""
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run",
        description=(
            "저장소 목록을 저장소 단위로 병렬 채굴한다 (Issue #81). 같은 --out-dir/--repos-dir로 "
            "배치를 동시에 둘 이상 실행하지 않는다 — 병렬은 --workers로 한다."
        ),
    )
    parser.add_argument(
        "--input", type=Path, required=True, help="repo·default_branch 열이 있는 선정 CSV"
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--repos-dir", type=Path, default=DEFAULT_REPOS_DIR)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS, help="첫 시도 포함 최대 횟수"
    )
    parser.add_argument(
        "--retry-delays",
        default=",".join(f"{delay:g}" for delay in DEFAULT_RETRY_DELAYS),
        help="재시도 전 대기 초, 쉼표 구분. 개수는 max-attempts - 1 (재시도 없음은 '')",
    )
    parser.add_argument(
        "--clone-timeout",
        type=float,
        default=DEFAULT_CLONE_TIMEOUT_SECONDS,
        help="새 git clone 한 번의 제한 시간(초)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """끝내 실패한 저장소가 있으면 1, 없으면 0을 돌려준다."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = RunSettings(
            out_dir=args.out_dir,
            repos_dir=args.repos_dir,
            workers=args.workers,
            max_attempts=args.max_attempts,
            retry_delays=parse_retry_delays(args.retry_delays),
            clone_timeout_seconds=args.clone_timeout,
        )
        settings.validate()
        jobs = load_repo_jobs(args.input, args.repos_dir)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    summary = run_batch(jobs, settings)
    _log_stderr(
        f"완료: 전체 {summary['total_repos']} / 성공 {summary['success_count']} / "
        f"건너뜀 {summary['skipped_count']} / 실패 {summary['failed_count']}"
    )
    return 1 if summary["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
