"""pipeline/run.py 테스트 (Issue #81).

`tmp_path`에 실제 git 저장소를 만들어 "원격"으로 쓰고, 로컬 경로를 clone_url로 넘긴다
(`tests/test_clone.py`와 같은 방식, 네트워크 없음). 재시도 대기는 `sleep`을 주입해 실제로
기다리지 않는다. 실패 주입(`monkeypatch`)은 같은 프로세스에서 도는 `workers=1`에서만 쓰고,
`workers=2`는 존재하지 않는 clone 경로처럼 입력으로 실패를 만든다 — spawn된 워커에는
monkeypatch가 전달되지 않는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import clone as clone_module
from pipeline import extract as extract_module
from pipeline import run as run_module
from pipeline.filter import FILTER_RULE_VERSION, NOISE_MOVE, NOISE_TRIVIAL
from pipeline.run import CodeState, RepoJob, RunSettings
from pipeline.walk import walk_commits

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

_REPO = "acme/widgets"
_ROOT = Path(__file__).resolve().parent.parent
_CODE_STATE = CodeState("f" * 40, False)
# `is_completed` 판정용 job. 판정은 repo·default_branch·ref만 보고 clone_url은 쓰지 않는다.
_MAIN_JOB = RepoJob(_REPO, "main", "")

_METADATA_KEYS = {
    "repo",
    "default_branch",
    "ref",
    "status",
    "started_at",
    "finished_at",
    "elapsed_seconds",
    "retry_wait_seconds",
    "attempt_count",
    "retry_count",
    "commit_count",
    "kept_count",
    "excluded_count",
    "noise_move_count",
    "noise_trivial_count",
    "failure_stage",
    "failure_type",
    "failure_reason",
    "repository_head_sha",
    "filter_rule_version",
    "pipeline_code_sha",
    "pipeline_dirty",
}

_GIT_MISSING = shutil.which("git") is None
requires_git = pytest.mark.skipif(_GIT_MISSING, reason="git CLI가 필요하다")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """테스트용 git 실행. 작성자 정보를 고정해 커밋 SHA 외에는 환경에 따라 달라지지 않는다."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _init_repo(path: Path) -> Path:
    """`main` 브랜치로 시작하는 빈 저장소 — CSV의 `default_branch`와 맞춘다."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    return path


def _write(repo: Path, rel_path: str, content: str) -> None:
    """저장소 안 상대 경로에 UTF-8로 쓴다. 중간 디렉터리는 만든다."""
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
    """작업 트리 전체를 커밋하고 그 SHA를 돌려준다."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _make_source(path: Path) -> Path:
    """kept 2 (FULL `gone`, 5줄 PARTIAL `keep`) + NOISE_TRIVIAL 1 (`shrink`) + NOISE_MOVE 1
    (`moved_func`)이 나오는 커밋과, 삭제 없는 커밋 하나. root 제외 diff 대상 커밋은 2개다.
    (`tests/test_extract.py`의 NOISE_TRIVIAL + NOISE_MOVE 통합 테스트와 같은 내용)"""
    repo = _init_repo(path)
    _write(
        repo, "b.py", "def shrink():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    return 0\n"
    )
    _write(repo, "c.py", "def gone():\n    return 2\n")
    _write(repo, "old/a.py", "def moved_func():\n    return 1\n")
    _write(
        repo,
        "q.py",
        "def keep():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    return 0\n",
    )
    _commit_all(repo, "add functions")
    _write(repo, "b.py", "def shrink():\n    return 0\n\n\ndef extra():\n    return [1]\n")
    _write(repo, "c.py", "")
    (repo / "old" / "a.py").unlink()
    _write(repo, "new/a.py", "def moved_func():\n    return 1\n")
    _write(repo, "q.py", "def keep():\n    return 0\n")
    _commit_all(repo, "shrink, delete gone, move moved_func, trim keep")
    _write(repo, "d.py", "def added():\n    return 3\n")
    _commit_all(repo, "add d")
    return repo


def _make_kept_only_source(path: Path) -> Path:
    """제외 레코드가 0건인 저장소 — FULL 삭제 하나뿐."""
    repo = _init_repo(path)
    _write(repo, "c.py", "def gone():\n    return 2\n")
    _commit_all(repo, "add gone")
    _write(repo, "c.py", "")
    _commit_all(repo, "delete gone")
    return repo


def _settings(tmp_path: Path, **overrides) -> RunSettings:
    """`tmp_path` 아래 out/repos 디렉터리를 쓰는 설정. 나머지는 기본값이거나 `overrides`."""
    return RunSettings(out_dir=tmp_path / "out", repos_dir=tmp_path / "repos", **overrides)


def _job(source: Path | str, repo: str = _REPO, branch: str = "main") -> RepoJob:
    """로컬 경로를 clone_url로 쓰는 job — 네트워크 없이 실제 `git clone`을 탄다."""
    return RepoJob(repo, branch, str(source))


def _run(jobs, settings, sleeps=None, **kwargs):
    """코드 상태를 고정하고 `sleep`을 기록만 하도록 바꿔 `run_batch`를 부른다."""
    recorded = [] if sleeps is None else sleeps
    return run_module.run_batch(
        jobs,
        settings,
        code_state=_CODE_STATE,
        sleep=recorded.append,
        log=lambda message: None,
        **kwargs,
    )


def _read_metadata(settings: RunSettings, repo: str = _REPO) -> dict:
    """`repo`의 `<stem>_run.json`을 읽는다."""
    run_path = run_module.output_paths(settings.out_dir, repo)[2]
    return json.loads(run_path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    """JSONL 파일을 행별 dict 목록으로 읽는다."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _called_process_error() -> subprocess.CalledProcessError:
    """네트워크 실패를 흉내 낸 `git clone` 오류 (stderr 포함)."""
    return subprocess.CalledProcessError(
        128, ["git", "clone"], stderr="fatal: unable to access remote\n"
    )


# --------------------------------------------------------------------------------------
# 입력·이름·설정 (순수)
# --------------------------------------------------------------------------------------


def test_repo_stem_replaces_slash_with_double_underscore():
    """출력 stem 규칙은 `/` → `__` 하나뿐이다."""
    assert run_module.repo_stem("pydantic/pydantic") == "pydantic__pydantic"


def test_output_paths_names():
    """저장소별 세 파일 이름(kept·excluded·실행 기록)이 고정돼 있다."""
    kept, excluded, run_path = run_module.output_paths(Path("out"), "pydantic/pydantic")
    assert kept == Path("out/pydantic__pydantic.jsonl")
    assert excluded == Path("out/pydantic__pydantic_excluded.jsonl")
    assert run_path == Path("out/pydantic__pydantic_run.json")


def test_load_repo_jobs_reads_only_repo_and_default_branch(tmp_path: Path):
    """선정 CSV에서 두 열만 쓰고 나머지 열은 무시하며, clone URL은 GitHub 형식으로 채운다."""
    csv_path = tmp_path / "repos.csv"
    csv_path.write_text(
        "repo,license,default_branch\npydantic/pydantic,MIT,main\napache/superset,Apache-2.0,master\n",
        encoding="utf-8",
    )
    jobs = run_module.load_repo_jobs(csv_path, tmp_path / "repos")
    assert jobs == [
        RepoJob("pydantic/pydantic", "main", "https://github.com/pydantic/pydantic.git"),
        RepoJob("apache/superset", "master", "https://github.com/apache/superset.git"),
    ]
    assert jobs[0].ref == "origin/main"


def test_load_repo_jobs_reads_selection_csv():
    """저장소 선정 산출물(`docs/repo_final20_v2.csv`)을 그대로 읽는다."""
    jobs = run_module.load_repo_jobs(_ROOT / "docs" / "repo_final20_v2.csv", Path("repos"))
    assert len(jobs) == 20
    assert RepoJob("pydantic/pydantic", "main", "https://github.com/pydantic/pydantic.git") in jobs


@pytest.mark.parametrize(
    "content",
    [
        "repo\npydantic/pydantic\n",  # default_branch 열 없음
        "repo,default_branch\npydantic/pydantic,\n",  # 빈 브랜치
        "repo,default_branch\npydantic,main\n",  # owner/name 형식 아님 (clone.repo_dir 검사)
        "repo,default_branch\na/b,main\na/b,main\n",  # 중복
        "repo,default_branch\na__b/c,main\na/b__c,main\n",  # stem 충돌
    ],
)
def test_load_repo_jobs_rejects_invalid_input(tmp_path: Path, content: str):
    """열 누락·빈 브랜치·형식 오류·중복·stem 충돌은 실행 전에 거부한다."""
    csv_path = tmp_path / "repos.csv"
    csv_path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        run_module.load_repo_jobs(csv_path, tmp_path / "repos")


def test_parse_retry_delays():
    """쉼표 구분 초 값만 받고, 빈 문자열은 재시도 없음이며 음수·무한대는 거부한다."""
    assert run_module.parse_retry_delays("10,60") == (10.0, 60.0)
    assert run_module.parse_retry_delays(" 5 ") == (5.0,)
    assert run_module.parse_retry_delays("") == ()
    for bad in ("a,b", "-1", "10,,60", "inf"):
        with pytest.raises(ValueError):
            run_module.parse_retry_delays(bad)


def test_default_settings_match_team_decision():
    """기본값: workers 1, 최대 3회, 대기 10·60초, clone timeout 1800초."""
    settings = RunSettings()
    assert settings.workers == 1
    assert settings.max_attempts == 3
    assert settings.retry_delays == (10.0, 60.0)
    assert settings.clone_timeout_seconds == 1800.0
    settings.validate()


@pytest.mark.parametrize(
    "overrides",
    [
        {"workers": 0},
        {"max_attempts": 0, "retry_delays": ()},
        {"max_attempts": 3, "retry_delays": (10.0,)},  # 개수 부족
        {"max_attempts": 2, "retry_delays": (10.0, 60.0)},  # 개수 초과
        {"max_attempts": 2, "retry_delays": (-1.0,)},
        {"clone_timeout_seconds": 0},
        {"clone_timeout_seconds": float("inf")},
    ],
)
def test_settings_validate_rejects_ambiguous_values(overrides):
    """대기 개수가 `max_attempts - 1`과 다르거나 값이 범위 밖이면 추측하지 않고 거부한다."""
    with pytest.raises(ValueError):
        RunSettings(**overrides).validate()


def test_settings_validate_accepts_single_attempt_without_delays():
    """재시도 없는 설정은 대기 목록이 비어야 한다."""
    RunSettings(max_attempts=1, retry_delays=()).validate()


@pytest.mark.parametrize(
    ("stage", "error", "expected"),
    [
        ("clone", _called_process_error(), True),
        ("clone", subprocess.TimeoutExpired(["git", "clone"], 1800), True),
        ("clone", ValueError("x"), False),
        ("clone", FileNotFoundError("git"), False),
        ("reuse_clone", _called_process_error(), False),
        ("reuse_clone", RuntimeError("shallow"), False),
        ("clone_cleanup", RuntimeError("x"), False),
        ("input", ValueError("x"), False),
        ("resolve_ref", _called_process_error(), False),
        ("walk", _called_process_error(), False),
        ("extract", _called_process_error(), False),
        ("extract", UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), False),
        ("extract", RecursionError(), False),
        ("write", OSError("disk full"), False),
    ],
)
def test_is_retryable_uses_stage_and_exception_type(stage, error, expected):
    """새 clone 단계의 git 오류·timeout만 재시도한다. 같은 예외라도 다른 단계면 즉시 실패다."""
    assert run_module.is_retryable(stage, error) is expected


# --------------------------------------------------------------------------------------
# 성공 실행: 출력·실행 기록·건수·SHA
# --------------------------------------------------------------------------------------


@requires_git
def test_success_writes_outputs_and_metadata(tmp_path: Path):
    """성공 실행의 실행 기록 전 필드(시각·건수·SHA·버전)와 두 출력 파일·요약을 확인한다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)

    summary = _run([_job(source)], settings)

    metadata = _read_metadata(settings)
    assert set(metadata) == _METADATA_KEYS
    assert metadata["repo"] == _REPO
    assert metadata["default_branch"] == "main"
    assert metadata["ref"] == "origin/main"
    assert metadata["status"] == "SUCCESS"
    assert metadata["attempt_count"] == 1
    assert metadata["retry_count"] == 0
    assert metadata["retry_wait_seconds"] == 0.0
    assert metadata["failure_stage"] is None
    assert metadata["failure_type"] is None
    assert metadata["failure_reason"] is None
    started = datetime.fromisoformat(metadata["started_at"])
    finished = datetime.fromisoformat(metadata["finished_at"])
    assert started.tzinfo is not None and finished.tzinfo is not None
    assert started <= finished
    assert isinstance(metadata["elapsed_seconds"], float) and metadata["elapsed_seconds"] >= 0

    # 건수: kept 2, 제외 = NOISE_MOVE 1 + NOISE_TRIVIAL 1
    assert metadata["kept_count"] == 2
    assert metadata["excluded_count"] == 2
    assert metadata["noise_move_count"] == 1
    assert metadata["noise_trivial_count"] == 1
    assert metadata["excluded_count"] == (
        metadata["noise_move_count"] + metadata["noise_trivial_count"]
    )

    # commit_count는 walk_commits 결과 수 그대로 (root 제외 2개)
    clone_path = settings.repos_dir / "acme" / "widgets"
    assert metadata["commit_count"] == len(walk_commits(clone_path, "origin/main")) == 2

    # 처리한 SHA = 원본 HEAD, 규칙 버전·코드 상태
    assert metadata["repository_head_sha"] == _git(source, "rev-parse", "HEAD").stdout.strip()
    assert metadata["filter_rule_version"] == FILTER_RULE_VERSION
    assert metadata["pipeline_code_sha"] == _CODE_STATE.sha
    assert metadata["pipeline_dirty"] is False

    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    kept_rows = _read_jsonl(kept_path)
    excluded_rows = _read_jsonl(excluded_path)
    assert [row["function_name"] for row in kept_rows] == ["gone", "keep"]
    assert sorted(row["filter_status"] for row in excluded_rows) == sorted(
        [NOISE_MOVE, NOISE_TRIVIAL]
    )
    assert "filter_status" not in kept_rows[0]  # 추출 JSONL 계약 그대로
    assert not list(settings.out_dir.glob("*.tmp"))

    assert summary["total_repos"] == 1
    assert summary["success_count"] == 1
    assert summary["failed_count"] == 0
    assert summary["failure_rate"] == 0.0
    assert summary["failed_repos"] == []
    assert (
        json.loads((settings.out_dir / "batch_summary.json").read_text(encoding="utf-8")) == summary
    )


@requires_git
def test_outputs_match_direct_extract_call(tmp_path: Path):
    """오케스트레이터는 추출·필터를 다시 구현하지 않는다 — 같은 SHA로 부른
    `extract_repo_with_excluded` 결과를 같은 writer로 쓴 것과 바이트 단위로 같다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)

    clone_path = settings.repos_dir / "acme" / "widgets"
    head = _read_metadata(settings)["repository_head_sha"]
    kept, excluded = extract_module.extract_repo_with_excluded(clone_path, _REPO, head)
    extract_module.write_jsonl(kept, tmp_path / "direct.jsonl")
    extract_module.write_excluded_jsonl(excluded, tmp_path / "direct_excluded.jsonl")

    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    assert kept_path.read_bytes() == (tmp_path / "direct.jsonl").read_bytes()
    assert excluded_path.read_bytes() == (tmp_path / "direct_excluded.jsonl").read_bytes()


@requires_git
def test_batch_keeps_single_git_diff_per_commit(tmp_path: Path, monkeypatch):
    """#64 회귀: 배치 경로에서도 커밋당 `_run_git_diff`는 한 번이다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    calls: list[tuple[str, str]] = []
    original = extract_module._run_git_diff

    def recording(repo_path, parent_sha, commit_sha):
        calls.append((parent_sha, commit_sha))
        return original(repo_path, parent_sha, commit_sha)

    monkeypatch.setattr(extract_module, "_run_git_diff", recording)
    _run([_job(source)], settings)

    commits = walk_commits(settings.repos_dir / "acme" / "widgets", "origin/main")
    assert calls == [(c.parent_sha, c.commit_sha) for c in commits]


@requires_git
def test_empty_excluded_output_is_success_and_resumable(tmp_path: Path):
    """제외 0건이면 빈 excluded 파일이 정상 결과이고 이어 하기에서도 완료로 본다."""
    source = _make_kept_only_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)

    metadata = _read_metadata(settings)
    assert metadata["status"] == "SUCCESS"
    assert metadata["kept_count"] == 1
    assert metadata["excluded_count"] == 0
    assert metadata["noise_move_count"] == 0
    assert metadata["noise_trivial_count"] == 0
    _, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    assert excluded_path.read_bytes() == b""
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is True


# --------------------------------------------------------------------------------------
# 재시도·즉시 실패
# --------------------------------------------------------------------------------------


@requires_git
def test_retryable_clone_failure_is_retried_then_listed_as_failed(tmp_path: Path, monkeypatch):
    """재시도 가능한 clone 실패는 10·60초를 쉬며 3회까지 시도하고, 끝내 실패하면 실패 목록에
    남는다."""
    calls: list[str] = []

    def failing_clone(repo, clone_url, repos_dir, *, timeout=None):
        calls.append(repo)
        raise _called_process_error()

    monkeypatch.setattr(run_module, "clone", failing_clone)
    settings = _settings(tmp_path)
    sleeps: list[float] = []

    summary = _run([_job(tmp_path / "unused")], settings, sleeps)

    assert len(calls) == 3
    assert sleeps == [10.0, 60.0]
    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["attempt_count"] == 3
    assert metadata["retry_count"] == 2
    assert metadata["retry_wait_seconds"] == 70.0  # 10초 + 60초, 실제로는 기다리지 않았다
    assert metadata["failure_stage"] == "clone"
    assert metadata["failure_type"] == "CalledProcessError"
    assert "fatal: unable to access remote" in metadata["failure_reason"]
    assert metadata["kept_count"] is None
    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    assert not kept_path.exists() and not excluded_path.exists()

    assert summary["failed_count"] == 1
    assert summary["failure_rate"] == 1.0
    assert summary["failed_repos"] == [
        {
            "repo": _REPO,
            "failure_stage": "clone",
            "failure_type": "CalledProcessError",
            "failure_reason": metadata["failure_reason"],
            "attempt_count": 3,
        }
    ]
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False


@requires_git
def test_real_clone_failure_is_retried(tmp_path: Path):
    """monkeypatch 없이 실제 `git clone` 실패(없는 경로)도 clone 단계 재시도 대상이다."""
    settings = _settings(tmp_path, retry_delays=(0.0, 0.0))
    _run([_job(tmp_path / "does-not-exist")], settings)

    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == "clone"
    assert metadata["failure_type"] == "CalledProcessError"
    assert metadata["attempt_count"] == 3
    assert not (settings.repos_dir / "acme" / "widgets").exists()


@requires_git
def test_retry_then_success(tmp_path: Path, monkeypatch):
    """첫 clone만 실패하면 한 번 쉬고 두 번째 시도에서 성공으로 기록된다."""
    source = _make_source(tmp_path / "source")
    calls: list[str] = []
    real_clone = clone_module.clone

    def flaky_clone(repo, clone_url, repos_dir, *, timeout=None):
        calls.append(repo)
        if len(calls) == 1:
            raise _called_process_error()
        return real_clone(repo, clone_url, repos_dir, timeout=timeout)

    monkeypatch.setattr(run_module, "clone", flaky_clone)
    settings = _settings(tmp_path)
    sleeps: list[float] = []

    summary = _run([_job(source)], settings, sleeps)

    assert sleeps == [10.0]
    metadata = _read_metadata(settings)
    assert metadata["status"] == "SUCCESS"
    assert metadata["attempt_count"] == 2
    assert metadata["retry_count"] == 1
    assert metadata["retry_wait_seconds"] == 10.0
    assert metadata["failure_stage"] is None
    assert metadata["kept_count"] == 2
    assert summary["success_count"] == 1 and summary["failed_count"] == 0


@requires_git
def test_existing_non_git_directory_fails_once_and_is_not_deleted(tmp_path: Path):
    """재사용 검사 실패는 재시도하지 않고, 사람이 둔 디렉터리는 지우지 않는다."""
    settings = _settings(tmp_path)
    target = settings.repos_dir / "acme" / "widgets"
    target.mkdir(parents=True)
    (target / "note.txt").write_text("keep me", encoding="utf-8")
    sleeps: list[float] = []

    _run([_job(tmp_path / "unused")], settings, sleeps)

    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["attempt_count"] == 1
    assert metadata["retry_count"] == 0
    assert metadata["failure_stage"] == "reuse_clone"
    assert metadata["failure_type"] == "RuntimeError"
    assert sleeps == []
    assert (target / "note.txt").read_text(encoding="utf-8") == "keep me"


@requires_git
def test_bad_ref_fails_immediately(tmp_path: Path):
    """없는 브랜치는 다시 돌려도 같으므로 한 번에 실패하고, 받은 클론은 둔다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    sleeps: list[float] = []

    _run([_job(source, branch="no-such-branch")], settings, sleeps)

    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == "resolve_ref"
    assert metadata["attempt_count"] == 1
    assert metadata["ref"] == "origin/no-such-branch"
    assert sleeps == []
    assert (settings.repos_dir / "acme" / "widgets" / ".git").exists()  # 받은 클론은 둔다


@requires_git
def test_extract_error_fails_immediately_and_keeps_clone(tmp_path: Path, monkeypatch):
    """추출 단계 오류는 재시도하지 않고, 실패 전까지 구한 SHA·커밋 수는 기록에 남는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)

    def broken_extract(repo_path, repo, ref):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(run_module, "extract_repo_with_excluded", broken_extract)
    sleeps: list[float] = []

    _run([_job(source)], settings, sleeps)

    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == "extract"
    assert metadata["failure_type"] == "UnicodeDecodeError"
    assert metadata["attempt_count"] == 1
    assert sleeps == []
    # 실패 전까지 구한 값은 남는다
    assert metadata["repository_head_sha"] == _git(source, "rev-parse", "HEAD").stdout.strip()
    assert metadata["commit_count"] == 2
    assert metadata["kept_count"] is None
    assert (settings.repos_dir / "acme" / "widgets" / ".git").exists()


def test_keyboard_interrupt_is_not_swallowed(tmp_path: Path, monkeypatch):
    """`KeyboardInterrupt`는 실패 기록으로 삼키지 않고 올린다."""

    def interrupted_clone(repo, clone_url, repos_dir, *, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "clone", interrupted_clone)
    with pytest.raises(KeyboardInterrupt):
        _run([_job(tmp_path / "unused")], _settings(tmp_path))


# --------------------------------------------------------------------------------------
# clone timeout
# --------------------------------------------------------------------------------------


def test_default_and_overridden_clone_timeout_reach_clone(tmp_path: Path, monkeypatch):
    """clone timeout 기본 1800초와 바꾼 값이 `clone()`까지 전달된다."""
    timeouts: list[float | None] = []

    def recording_clone(repo, clone_url, repos_dir, *, timeout=None):
        timeouts.append(timeout)
        raise ValueError("stop here")  # 재시도하지 않는 실패로 바로 끝낸다

    monkeypatch.setattr(run_module, "clone", recording_clone)
    _run([_job(tmp_path / "unused")], _settings(tmp_path / "a"))
    _run([_job(tmp_path / "unused")], _settings(tmp_path / "b", clone_timeout_seconds=42.0))

    assert timeouts == [1800.0, 42.0]


def test_clone_timeout_is_retried_and_partial_clone_is_removed(tmp_path: Path, monkeypatch):
    """timeout은 재시도 대상이고, 매 시도가 남긴 부분 클론은 다음 시도 전에 지워진다."""
    settings = _settings(tmp_path, retry_delays=(0.0, 0.0))
    target = settings.repos_dir / "acme" / "widgets"
    existed_at_start: list[bool] = []

    def hanging_clone(repo, clone_url, repos_dir, *, timeout=None):
        existed_at_start.append(target.exists())
        (target / ".git").mkdir(parents=True)
        (target / ".git" / "partial").write_text("x", encoding="utf-8")
        raise subprocess.TimeoutExpired(["git", "clone"], timeout)

    monkeypatch.setattr(run_module, "clone", hanging_clone)
    _run([_job(tmp_path / "unused")], settings)

    # 매 시도가 앞 시도의 부분 클론 없이 시작했고, 마지막에도 남지 않았다
    assert existed_at_start == [False, False, False]
    assert not target.exists()
    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == "clone"
    assert metadata["failure_type"] == "TimeoutExpired"
    assert metadata["attempt_count"] == 3


@requires_git
def test_clone_failure_on_preexisting_directory_does_not_delete_it(tmp_path: Path, monkeypatch):
    """시작할 때 이미 있던 정상 클론은 clone 오류가 나도 지우지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    target = clone_module.clone(_REPO, str(source), settings.repos_dir)  # 사람이 둔 정상 클론

    def failing_clone(repo, clone_url, repos_dir, *, timeout=None):
        raise _called_process_error()

    monkeypatch.setattr(run_module, "clone", failing_clone)
    _run([_job(source)], settings)

    metadata = _read_metadata(settings)
    assert metadata["failure_stage"] == "reuse_clone"
    assert metadata["attempt_count"] == 1
    assert (target / ".git").exists()


def test_clone_passes_timeout_only_to_git_clone(tmp_path: Path, monkeypatch):
    """clone.py 변경: `timeout`은 새 clone의 `git clone`에만 전달되고, 넘으면
    `TimeoutExpired`가 그대로 올라간다. 기본값은 예전처럼 제한 없음(`None`)."""
    seen: list[tuple[list[str], float | None]] = []

    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs.get("timeout")))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(clone_module.subprocess, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        clone_module.clone(_REPO, "https://example.invalid/x.git", tmp_path / "repos", timeout=5)
    with pytest.raises(subprocess.TimeoutExpired):
        clone_module.clone(_REPO, "https://example.invalid/x.git", tmp_path / "repos")

    assert [(cmd[1], timeout) for cmd, timeout in seen] == [("clone", 5), ("clone", None)]


# --------------------------------------------------------------------------------------
# 이어 하기·원자적 출력
# --------------------------------------------------------------------------------------


@requires_git
def test_resume_skips_completed_repo_without_clone_or_extract(tmp_path: Path, monkeypatch):
    """완료된 저장소는 clone·추출을 부르지 않고 건너뛰며 실행 기록도 건드리지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    run_path = run_module.output_paths(settings.out_dir, _REPO)[2]
    before = run_path.read_bytes()

    calls: list[str] = []
    monkeypatch.setattr(run_module, "clone", lambda *a, **k: calls.append("clone"))
    monkeypatch.setattr(
        run_module, "extract_repo_with_excluded", lambda *a, **k: calls.append("extract")
    )
    summary = _run([_job(source)], settings)

    assert calls == []
    assert run_path.read_bytes() == before
    assert summary["skipped_count"] == 1
    assert summary["skipped_repos"] == [_REPO]
    assert summary["success_count"] == 0
    assert summary["failed_count"] == 0


@requires_git
def test_filter_rule_version_mismatch_reruns(tmp_path: Path, monkeypatch):
    """이전 SUCCESS라도 필터 규칙 버전이 다르면 다시 돌아 현재 버전으로 기록한다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    run_path = run_module.output_paths(settings.out_dir, _REPO)[2]
    metadata = json.loads(run_path.read_text(encoding="utf-8"))
    metadata["filter_rule_version"] = "v0.0"
    run_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False

    calls: list[str] = []
    original = run_module.extract_repo_with_excluded

    def counting(repo_path, repo, ref):
        calls.append(ref)
        return original(repo_path, repo, ref)

    monkeypatch.setattr(run_module, "extract_repo_with_excluded", counting)
    summary = _run([_job(source)], settings)

    assert len(calls) == 1
    assert summary["success_count"] == 1 and summary["skipped_count"] == 0
    assert _read_metadata(settings)["filter_rule_version"] == FILTER_RULE_VERSION


def _break_state(settings: RunSettings, how: str) -> None:
    """성공한 실행의 출력·기록 하나를 `how` 방식으로 망가뜨린다."""
    kept_path, excluded_path, run_path = run_module.output_paths(settings.out_dir, _REPO)
    metadata = json.loads(run_path.read_text(encoding="utf-8"))
    if how == "missing_kept":
        kept_path.unlink()
    elif how == "missing_excluded":
        excluded_path.unlink()
    elif how == "missing_metadata":
        run_path.unlink()
    elif how == "corrupt_metadata":
        run_path.write_text('{"status": "SUCC', encoding="utf-8")
    elif how == "kept_line_count_mismatch":
        with kept_path.open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
    elif how == "excluded_line_count_mismatch":
        excluded_path.write_text("", encoding="utf-8")
    else:
        if how == "failed_status":
            metadata["status"] = "FAILED"
        elif how == "running_status":
            metadata["status"] = "RUNNING"
        elif how == "count_invariant_broken":
            metadata["noise_move_count"] += 1
        elif how == "count_not_int":
            metadata["kept_count"] = "2"
        elif how == "other_repo":
            metadata["repo"] = "other/repo"
        elif how == "other_branch":
            metadata["default_branch"] = "dev"
            metadata["ref"] = "origin/dev"
        elif how == "other_ref":
            metadata["ref"] = "origin/dev"
        run_path.write_text(json.dumps(metadata), encoding="utf-8")


@requires_git
@pytest.mark.parametrize(
    "how",
    [
        "missing_kept",
        "missing_excluded",
        "missing_metadata",
        "corrupt_metadata",
        "kept_line_count_mismatch",
        "excluded_line_count_mismatch",
        "failed_status",
        "running_status",
        "count_invariant_broken",
        "count_not_int",
        "other_repo",
        "other_branch",
        "other_ref",
    ],
)
def test_invalid_state_is_not_completed_and_reruns(tmp_path: Path, how: str):
    """완료 조건이 하나라도 깨지면 완료로 보지 않고 다시 돌아 정상 상태로 돌아온다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is True

    _break_state(settings, how)
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False

    summary = _run([_job(source)], settings)
    assert summary["success_count"] == 1 and summary["skipped_count"] == 0
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is True


def test_outputs_without_metadata_are_not_completed(tmp_path: Path):
    """출력 파일만 있고 실행 기록이 없으면 완료가 아니다."""
    kept_path, excluded_path, _ = run_module.output_paths(tmp_path, _REPO)
    kept_path.write_text("", encoding="utf-8")
    excluded_path.write_text("", encoding="utf-8")
    assert run_module.is_completed(tmp_path, _MAIN_JOB) is False


@requires_git
def test_write_failure_leaves_no_final_output_and_no_success(tmp_path: Path, monkeypatch):
    """첫 실행의 쓰기 실패는 최종 경로와 임시 파일 어디에도 흔적을 남기지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)

    def broken_writer(excluded, out_path):
        Path(out_path).write_text("half", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(run_module, "write_excluded_jsonl", broken_writer)
    _run([_job(source)], settings)

    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == "write"
    assert metadata["failure_type"] == "OSError"
    assert metadata["attempt_count"] == 1
    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    assert not kept_path.exists() and not excluded_path.exists()
    assert not list(settings.out_dir.glob("*.tmp"))
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False


def _force_rerun_with_marked_outputs(settings: RunSettings) -> tuple[bytes, bytes]:
    """이전 SUCCESS를 규칙 버전만 다르게 만들고(재실행을 일으킨다), 최종 두 파일 내용을
    이번 실행이 만들 수 없는 값으로 바꿔 둔다 — 보존·교체 여부를 바이트로 구분하려는 것이다."""
    kept_path, excluded_path, run_path = run_module.output_paths(settings.out_dir, _REPO)
    metadata = json.loads(run_path.read_text(encoding="utf-8"))
    metadata["filter_rule_version"] = "v0.0"
    run_path.write_text(json.dumps(metadata), encoding="utf-8")
    kept_path.write_bytes(b'{"previous": "kept"}\n')
    excluded_path.write_bytes(b'{"previous": "excluded"}\n')
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False
    return kept_path.read_bytes(), excluded_path.read_bytes()


@requires_git
def test_rerun_keeps_previous_outputs_while_running(tmp_path: Path, monkeypatch):
    """재실행이 시작되면 기록은 먼저 RUNNING이 되지만 이전 kept/excluded는 지우지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    previous = _force_rerun_with_marked_outputs(settings)
    kept_path, excluded_path, run_path = run_module.output_paths(settings.out_dir, _REPO)
    observed: list[tuple[str, float, bytes, bytes]] = []
    original = run_module.extract_repo_with_excluded

    def observing(repo_path, repo, ref):
        metadata = json.loads(run_path.read_text(encoding="utf-8"))
        observed.append(
            (
                metadata["status"],
                metadata["retry_wait_seconds"],
                kept_path.read_bytes(),
                excluded_path.read_bytes(),
            )
        )
        return original(repo_path, repo, ref)

    monkeypatch.setattr(run_module, "extract_repo_with_excluded", observing)
    _run([_job(source)], settings)

    assert observed == [("RUNNING", 0.0, *previous)]
    assert _read_metadata(settings)["status"] == "SUCCESS"


def _fail_clone(monkeypatch) -> None:
    """clone을 재시도하지 않는 예외로 실패시킨다."""

    def failing(repo, clone_url, repos_dir, *, timeout=None):
        raise ValueError("clone 실패 주입")

    monkeypatch.setattr(run_module, "clone", failing)


def _fail_extract(monkeypatch) -> None:
    """추출을 `UnicodeDecodeError`로 실패시킨다."""

    def failing(repo_path, repo, ref):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(run_module, "extract_repo_with_excluded", failing)


def _fail_writing_excluded(monkeypatch) -> None:
    """kept 임시 파일은 다 쓰고, excluded 임시 파일을 절반 쓴 채로 실패한다."""

    def failing(excluded, out_path):
        Path(out_path).write_text("half", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(run_module, "write_excluded_jsonl", failing)


def _fail_replacing(monkeypatch, source_name: str) -> None:
    """`source_name` 임시 파일을 최종 경로로 옮기는 `os.replace`만 실패시킨다 — Windows에서
    대상 파일을 다른 프로세스가 열고 있을 때 나는 `PermissionError`와 같다."""
    real_replace = os.replace

    def failing(src, dst):
        if Path(src).name == source_name:
            raise PermissionError(f"{dst} is locked")
        return real_replace(src, dst)

    monkeypatch.setattr(run_module.os, "replace", failing)


def _fail_replacing_kept(monkeypatch) -> None:
    """kept 교체가 실패한다 — 이전 kept는 이미 `.bak`으로 옮겨진 뒤다."""
    _fail_replacing(monkeypatch, "acme__widgets.jsonl.tmp")


def _fail_replacing_excluded(monkeypatch) -> None:
    """kept 교체는 성공하고 excluded 교체가 실패한다 — kept를 되돌려야 쌍이 유지된다."""
    _fail_replacing(monkeypatch, "acme__widgets_excluded.jsonl.tmp")


@requires_git
@pytest.mark.parametrize(
    ("inject", "stage"),
    [
        (_fail_clone, "reuse_clone"),  # 첫 실행이 받아 둔 클론을 재사용하는 단계
        (_fail_extract, "extract"),
        (_fail_writing_excluded, "write"),
        (_fail_replacing_kept, "write"),
        (_fail_replacing_excluded, "write"),
    ],
)
def test_failed_rerun_preserves_previous_outputs(tmp_path: Path, monkeypatch, inject, stage):
    """재실행이 어느 단계에서 실패해도 이전 최종 파일은 바이트 그대로이고, 이번 실행의 임시
    파일은 남지 않으며, 기록은 FAILED다 (완료로 보지 않는다)."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    previous_kept, previous_excluded = _force_rerun_with_marked_outputs(settings)

    inject(monkeypatch)
    summary = _run([_job(source)], settings)

    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    assert kept_path.read_bytes() == previous_kept
    assert excluded_path.read_bytes() == previous_excluded
    assert not list(settings.out_dir.glob("*.tmp"))
    assert not list(settings.out_dir.glob("*.bak"))
    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == stage
    assert metadata["filter_rule_version"] == FILTER_RULE_VERSION
    assert summary["failed_count"] == 1
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False


@requires_git
def test_successful_rerun_replaces_previous_outputs(tmp_path: Path):
    """성공한 재실행은 두 최종 파일을 새 결과로 바꾸고 임시·백업 파일을 남기지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    fresh_kept, fresh_excluded = kept_path.read_bytes(), excluded_path.read_bytes()
    previous_kept, previous_excluded = _force_rerun_with_marked_outputs(settings)
    assert (previous_kept, previous_excluded) != (fresh_kept, fresh_excluded)

    summary = _run([_job(source)], settings)

    assert summary["success_count"] == 1 and summary["skipped_count"] == 0
    assert kept_path.read_bytes() == fresh_kept
    assert excluded_path.read_bytes() == fresh_excluded
    assert not list(settings.out_dir.glob("*.tmp"))
    assert not list(settings.out_dir.glob("*.bak"))
    assert _read_metadata(settings)["status"] == "SUCCESS"
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is True


@requires_git
def test_first_run_publish_failure_leaves_no_final_outputs(tmp_path: Path, monkeypatch):
    """이전 출력이 없는 첫 실행에서 excluded 교체가 실패하면, 먼저 옮겨진 새 kept도 지워
    최종 경로에 짝 없는 파일이 남지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _fail_replacing_excluded(monkeypatch)

    _run([_job(source)], settings)

    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, _REPO)
    assert not kept_path.exists() and not excluded_path.exists()
    assert not list(settings.out_dir.glob("*.tmp"))
    assert not list(settings.out_dir.glob("*.bak"))
    metadata = _read_metadata(settings)
    assert (metadata["status"], metadata["failure_stage"]) == ("FAILED", "write")
    assert metadata["failure_type"] == "PermissionError"


@requires_git
def test_is_completed_requires_same_branch_and_ref(tmp_path: Path):
    """같은 repo라도 job의 default_branch·ref가 기록과 다르면 완료가 아니다. 처리한 SHA는
    비교 대상이 아니다 — upstream이 움직였다는 것만으로 다시 돌지 않는다."""
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)

    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is True
    assert run_module.is_completed(settings.out_dir, RepoJob(_REPO, "dev", "")) is False

    _write(source, "e.py", "x = 1\n")
    _commit_all(source, "upstream moved")
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is True


@requires_git
def test_branch_change_reruns_and_records_new_ref(tmp_path: Path):
    """main으로 성공한 저장소를 dev job으로 돌리면 건너뛰지 않고 dev를 처리해, 기록에 dev와
    실제로 처리한 dev 끝 커밋을 남긴다."""
    source = _make_source(tmp_path / "source")
    _git(source, "checkout", "-q", "-b", "dev")
    _write(source, "d.py", "")
    dev_head = _commit_all(source, "delete added on dev")
    _git(source, "checkout", "-q", "main")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    assert _read_metadata(settings)["ref"] == "origin/main"

    summary = _run([_job(source, branch="dev")], settings)

    assert summary["skipped_count"] == 0 and summary["success_count"] == 1
    metadata = _read_metadata(settings)
    assert (metadata["default_branch"], metadata["ref"]) == ("dev", "origin/dev")
    assert metadata["repository_head_sha"] == dev_head
    kept_path = run_module.output_paths(settings.out_dir, _REPO)[0]
    assert "added" in [row["function_name"] for row in _read_jsonl(kept_path)]
    assert run_module.is_completed(settings.out_dir, RepoJob(_REPO, "dev", "")) is True
    assert run_module.is_completed(settings.out_dir, _MAIN_JOB) is False


# --------------------------------------------------------------------------------------
# 코드 SHA·dirty
# --------------------------------------------------------------------------------------


@requires_git
def test_pipeline_code_state_ignores_untracked_and_ignored_files(tmp_path: Path):
    """dirty는 tracked 변경(staged 포함)만 반영하고 ignored·untracked 파일은 보지 않는다."""
    repo = _init_repo(tmp_path / "code")
    _write(repo, ".gitignore", "data/\n")
    _write(repo, "a.py", "x = 1\n")
    head = _commit_all(repo, "init")

    _write(repo, "data/out.jsonl", "{}\n")  # 실행 결과 (ignored)
    _write(repo, "scratch.txt", "tmp\n")  # untracked
    assert run_module.pipeline_code_state(repo) == CodeState(head, False)

    _write(repo, "a.py", "x = 2\n")  # tracked 수정
    assert run_module.pipeline_code_state(repo) == CodeState(head, True)

    _git(repo, "checkout", "--", "a.py")
    _git(repo, "add", "scratch.txt")  # staged 추가도 tracked 변경이다
    assert run_module.pipeline_code_state(repo) == CodeState(head, True)


@requires_git
def test_pipeline_code_state_unknown_outside_worktree_root(tmp_path: Path):
    """작업 트리 루트가 아니면 SHA·dirty를 모름(`None`)으로 둔다."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert run_module.pipeline_code_state(plain) == CodeState(None, None)

    repo = _init_repo(tmp_path / "code")
    _write(repo, "sub/a.py", "x = 1\n")
    _commit_all(repo, "init")
    assert run_module.pipeline_code_state(repo / "sub") == CodeState(None, None)


@requires_git
def test_pipeline_code_state_defaults_to_this_repository():
    """기본 대상은 이 저장소 루트의 HEAD다."""
    root = Path(run_module.__file__).resolve().parent.parent
    state = run_module.pipeline_code_state()
    assert state.sha == _git(root, "rev-parse", "HEAD").stdout.strip()
    assert isinstance(state.dirty, bool)


@requires_git
def test_run_batch_records_current_code_state_by_default(tmp_path: Path):
    """코드 상태를 주지 않으면 실행 시점의 이 저장소 상태를 기록한다."""
    source = _make_kept_only_source(tmp_path / "source")
    settings = _settings(tmp_path)
    summary = run_module.run_batch([_job(source)], settings, log=lambda message: None)

    expected = run_module.pipeline_code_state()
    metadata = _read_metadata(settings)
    assert metadata["pipeline_code_sha"] == expected.sha == summary["pipeline_code_sha"]
    assert metadata["pipeline_dirty"] == expected.dirty


# --------------------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------------------


def test_workers_1_runs_sequentially_in_input_order(tmp_path: Path, monkeypatch):
    """workers=1은 같은 프로세스에서 입력 순서대로 돈다."""
    order: list[str] = []

    def recording_clone(repo, clone_url, repos_dir, *, timeout=None):
        order.append(repo)
        raise ValueError("stop")

    monkeypatch.setattr(run_module, "clone", recording_clone)
    repos = ["c/three", "a/one", "b/two"]
    summary = _run([_job(tmp_path, repo=repo) for repo in repos], _settings(tmp_path))

    assert order == repos
    assert [item["repo"] for item in summary["failed_repos"]] == repos


@requires_git
def test_workers_2_process_pool_matches_sequential_run(tmp_path: Path):
    """프로세스 풀 경로: 성공·실패가 섞여도 저장소별로 기록되고, 결과 파일은 workers=1과 같다.
    실패는 입력(없는 clone 경로)으로 만든다 — 워커에는 monkeypatch가 닿지 않는다."""
    source = _make_source(tmp_path / "source")
    jobs = [_job(source, repo="acme/good"), _job(tmp_path / "missing", repo="acme/bad")]
    parallel = _settings(tmp_path / "p", workers=2, retry_delays=(0.0, 0.0))
    sequential = _settings(tmp_path / "s", retry_delays=(0.0, 0.0))

    summary = _run(jobs, parallel)
    _run(jobs, sequential)

    assert summary["success_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["failure_rate"] == 0.5
    assert [item["repo"] for item in summary["failed_repos"]] == ["acme/bad"]
    bad = _read_metadata(parallel, "acme/bad")
    assert (bad["status"], bad["failure_stage"], bad["attempt_count"]) == ("FAILED", "clone", 3)
    for index in (0, 1):
        assert (
            run_module.output_paths(parallel.out_dir, "acme/good")[index].read_bytes()
            == run_module.output_paths(sequential.out_dir, "acme/good")[index].read_bytes()
        )


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _csv(tmp_path: Path) -> Path:
    """저장소 한 줄짜리 선정 CSV."""
    path = tmp_path / "repos.csv"
    path.write_text("repo,default_branch\npydantic/pydantic,main\n", encoding="utf-8")
    return path


def _capture_run_batch(monkeypatch, failed_count: int = 0) -> list:
    """`run_batch`를 가로채 CLI가 넘긴 jobs·설정을 기록한다."""
    captured: list = []

    def fake_run_batch(jobs, settings, **kwargs):
        captured.append((jobs, settings))
        return {
            "total_repos": 1,
            "success_count": 1,
            "skipped_count": 0,
            "failed_count": failed_count,
        }

    monkeypatch.setattr(run_module, "run_batch", fake_run_batch)
    return captured


def test_cli_defaults(tmp_path: Path, monkeypatch):
    """옵션 없이 부르면 `RunSettings()` 기본값 그대로다."""
    captured = _capture_run_batch(monkeypatch)
    assert run_module.main(["--input", str(_csv(tmp_path))]) == 0
    jobs, settings = captured[0]
    assert jobs == [
        RepoJob("pydantic/pydantic", "main", "https://github.com/pydantic/pydantic.git")
    ]
    assert settings == RunSettings()


def test_cli_overrides_every_option(tmp_path: Path, monkeypatch):
    """#82가 코드 수정 없이 바꿀 옵션이 모두 설정에 반영된다."""
    captured = _capture_run_batch(monkeypatch)
    argv = [
        "--input", str(_csv(tmp_path)),
        "--out-dir", str(tmp_path / "out"),
        "--repos-dir", str(tmp_path / "repos"),
        "--workers", "2",
        "--max-attempts", "4",
        "--retry-delays", "5,30,120",
        "--clone-timeout", "600",
    ]  # fmt: skip
    assert run_module.main(argv) == 0
    _jobs, settings = captured[0]
    assert settings == RunSettings(
        out_dir=tmp_path / "out",
        repos_dir=tmp_path / "repos",
        workers=2,
        max_attempts=4,
        retry_delays=(5.0, 30.0, 120.0),
        clone_timeout_seconds=600.0,
    )


def test_cli_single_attempt_with_empty_delays(tmp_path: Path, monkeypatch):
    """`--max-attempts 1 --retry-delays ''`로 재시도를 끌 수 있다."""
    captured = _capture_run_batch(monkeypatch)
    argv = ["--input", str(_csv(tmp_path)), "--max-attempts", "1", "--retry-delays", ""]
    assert run_module.main(argv) == 0
    assert captured[0][1].retry_delays == ()


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-attempts", "5"],  # 기본 delay 2개와 개수 불일치
        ["--retry-delays", "10"],
        ["--workers", "0"],
        ["--clone-timeout", "0"],
        ["--retry-delays", "ten,sixty"],
    ],
)
def test_cli_rejects_ambiguous_options(tmp_path: Path, monkeypatch, extra):
    """모호하거나 잘못된 옵션은 배치를 시작하기 전에 종료 코드 2로 거부한다."""
    captured = _capture_run_batch(monkeypatch)
    with pytest.raises(SystemExit) as info:
        run_module.main(["--input", str(_csv(tmp_path)), *extra])
    assert info.value.code == 2
    assert captured == []


def test_cli_returns_1_when_any_repo_failed(tmp_path: Path, monkeypatch):
    """끝내 실패한 저장소가 있으면 종료 코드 1이다."""
    _capture_run_batch(monkeypatch, failed_count=1)
    assert run_module.main(["--input", str(_csv(tmp_path))]) == 1
