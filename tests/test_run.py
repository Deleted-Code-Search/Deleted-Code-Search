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
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    return path


def _write(repo: Path, rel_path: str, content: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
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
    return RunSettings(out_dir=tmp_path / "out", repos_dir=tmp_path / "repos", **overrides)


def _job(source: Path | str, repo: str = _REPO, branch: str = "main") -> RepoJob:
    return RepoJob(repo, branch, str(source))


def _run(jobs, settings, sleeps=None, **kwargs):
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
    run_path = run_module.output_paths(settings.out_dir, repo)[2]
    return json.loads(run_path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _called_process_error() -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(
        128, ["git", "clone"], stderr="fatal: unable to access remote\n"
    )


# --------------------------------------------------------------------------------------
# 입력·이름·설정 (순수)
# --------------------------------------------------------------------------------------


def test_repo_stem_replaces_slash_with_double_underscore():
    assert run_module.repo_stem("pydantic/pydantic") == "pydantic__pydantic"


def test_output_paths_names():
    kept, excluded, run_path = run_module.output_paths(Path("out"), "pydantic/pydantic")
    assert kept == Path("out/pydantic__pydantic.jsonl")
    assert excluded == Path("out/pydantic__pydantic_excluded.jsonl")
    assert run_path == Path("out/pydantic__pydantic_run.json")


def test_load_repo_jobs_reads_only_repo_and_default_branch(tmp_path: Path):
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
    csv_path = tmp_path / "repos.csv"
    csv_path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        run_module.load_repo_jobs(csv_path, tmp_path / "repos")


def test_parse_retry_delays():
    assert run_module.parse_retry_delays("10,60") == (10.0, 60.0)
    assert run_module.parse_retry_delays(" 5 ") == (5.0,)
    assert run_module.parse_retry_delays("") == ()
    for bad in ("a,b", "-1", "10,,60", "inf"):
        with pytest.raises(ValueError):
            run_module.parse_retry_delays(bad)


def test_default_settings_match_team_decision():
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
    with pytest.raises(ValueError):
        RunSettings(**overrides).validate()


def test_settings_validate_accepts_single_attempt_without_delays():
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
    assert run_module.is_retryable(stage, error) is expected


# --------------------------------------------------------------------------------------
# 성공 실행: 출력·실행 기록·건수·SHA
# --------------------------------------------------------------------------------------


@requires_git
def test_success_writes_outputs_and_metadata(tmp_path: Path):
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
    assert run_module.is_completed(settings.out_dir, _REPO) is True


# --------------------------------------------------------------------------------------
# 재시도·즉시 실패
# --------------------------------------------------------------------------------------


@requires_git
def test_retryable_clone_failure_is_retried_then_listed_as_failed(tmp_path: Path, monkeypatch):
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
    assert run_module.is_completed(settings.out_dir, _REPO) is False


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
    def interrupted_clone(repo, clone_url, repos_dir, *, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "clone", interrupted_clone)
    with pytest.raises(KeyboardInterrupt):
        _run([_job(tmp_path / "unused")], _settings(tmp_path))


# --------------------------------------------------------------------------------------
# clone timeout
# --------------------------------------------------------------------------------------


def test_default_and_overridden_clone_timeout_reach_clone(tmp_path: Path, monkeypatch):
    timeouts: list[float | None] = []

    def recording_clone(repo, clone_url, repos_dir, *, timeout=None):
        timeouts.append(timeout)
        raise ValueError("stop here")  # 재시도하지 않는 실패로 바로 끝낸다

    monkeypatch.setattr(run_module, "clone", recording_clone)
    _run([_job(tmp_path / "unused")], _settings(tmp_path / "a"))
    _run([_job(tmp_path / "unused")], _settings(tmp_path / "b", clone_timeout_seconds=42.0))

    assert timeouts == [1800.0, 42.0]


def test_clone_timeout_is_retried_and_partial_clone_is_removed(tmp_path: Path, monkeypatch):
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
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    run_path = run_module.output_paths(settings.out_dir, _REPO)[2]
    metadata = json.loads(run_path.read_text(encoding="utf-8"))
    metadata["filter_rule_version"] = "v0.0"
    run_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert run_module.is_completed(settings.out_dir, _REPO) is False

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
    ],
)
def test_invalid_state_is_not_completed_and_reruns(tmp_path: Path, how: str):
    source = _make_source(tmp_path / "source")
    settings = _settings(tmp_path)
    _run([_job(source)], settings)
    assert run_module.is_completed(settings.out_dir, _REPO) is True

    _break_state(settings, how)
    assert run_module.is_completed(settings.out_dir, _REPO) is False

    summary = _run([_job(source)], settings)
    assert summary["success_count"] == 1 and summary["skipped_count"] == 0
    assert run_module.is_completed(settings.out_dir, _REPO) is True


def test_outputs_without_metadata_are_not_completed(tmp_path: Path):
    kept_path, excluded_path, _ = run_module.output_paths(tmp_path, _REPO)
    kept_path.write_text("", encoding="utf-8")
    excluded_path.write_text("", encoding="utf-8")
    assert run_module.is_completed(tmp_path, _REPO) is False


@requires_git
def test_write_failure_leaves_no_final_output_and_no_success(tmp_path: Path, monkeypatch):
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
    assert run_module.is_completed(settings.out_dir, _REPO) is False


def _force_rerun_with_marked_outputs(settings: RunSettings) -> tuple[bytes, bytes]:
    """이전 SUCCESS를 규칙 버전만 다르게 만들고(재실행을 일으킨다), 최종 두 파일 내용을
    이번 실행이 만들 수 없는 값으로 바꿔 둔다 — 보존·교체 여부를 바이트로 구분하려는 것이다."""
    kept_path, excluded_path, run_path = run_module.output_paths(settings.out_dir, _REPO)
    metadata = json.loads(run_path.read_text(encoding="utf-8"))
    metadata["filter_rule_version"] = "v0.0"
    run_path.write_text(json.dumps(metadata), encoding="utf-8")
    kept_path.write_bytes(b'{"previous": "kept"}\n')
    excluded_path.write_bytes(b'{"previous": "excluded"}\n')
    assert run_module.is_completed(settings.out_dir, _REPO) is False
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
    def failing(repo, clone_url, repos_dir, *, timeout=None):
        raise ValueError("clone 실패 주입")

    monkeypatch.setattr(run_module, "clone", failing)


def _fail_extract(monkeypatch) -> None:
    def failing(repo_path, repo, ref):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(run_module, "extract_repo_with_excluded", failing)


def _fail_writing_excluded(monkeypatch) -> None:
    """kept 임시 파일은 다 쓰고, excluded 임시 파일을 절반 쓴 채로 실패한다."""

    def failing(excluded, out_path):
        Path(out_path).write_text("half", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(run_module, "write_excluded_jsonl", failing)


@requires_git
@pytest.mark.parametrize(
    ("inject", "stage"),
    [
        (_fail_clone, "reuse_clone"),  # 첫 실행이 받아 둔 클론을 재사용하는 단계
        (_fail_extract, "extract"),
        (_fail_writing_excluded, "write"),
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
    metadata = _read_metadata(settings)
    assert metadata["status"] == "FAILED"
    assert metadata["failure_stage"] == stage
    assert metadata["filter_rule_version"] == FILTER_RULE_VERSION
    assert summary["failed_count"] == 1
    assert run_module.is_completed(settings.out_dir, _REPO) is False


@requires_git
def test_successful_rerun_replaces_previous_outputs(tmp_path: Path):
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
    assert _read_metadata(settings)["status"] == "SUCCESS"
    assert run_module.is_completed(settings.out_dir, _REPO) is True


# --------------------------------------------------------------------------------------
# 코드 SHA·dirty
# --------------------------------------------------------------------------------------


@requires_git
def test_pipeline_code_state_ignores_untracked_and_ignored_files(tmp_path: Path):
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
    plain = tmp_path / "plain"
    plain.mkdir()
    assert run_module.pipeline_code_state(plain) == CodeState(None, None)

    repo = _init_repo(tmp_path / "code")
    _write(repo, "sub/a.py", "x = 1\n")
    _commit_all(repo, "init")
    assert run_module.pipeline_code_state(repo / "sub") == CodeState(None, None)


@requires_git
def test_pipeline_code_state_defaults_to_this_repository():
    root = Path(run_module.__file__).resolve().parent.parent
    state = run_module.pipeline_code_state()
    assert state.sha == _git(root, "rev-parse", "HEAD").stdout.strip()
    assert isinstance(state.dirty, bool)


@requires_git
def test_run_batch_records_current_code_state_by_default(tmp_path: Path):
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
    path = tmp_path / "repos.csv"
    path.write_text("repo,default_branch\npydantic/pydantic,main\n", encoding="utf-8")
    return path


def _capture_run_batch(monkeypatch, failed_count: int = 0) -> list:
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
    captured = _capture_run_batch(monkeypatch)
    assert run_module.main(["--input", str(_csv(tmp_path))]) == 0
    jobs, settings = captured[0]
    assert jobs == [
        RepoJob("pydantic/pydantic", "main", "https://github.com/pydantic/pydantic.git")
    ]
    assert settings == RunSettings()


def test_cli_overrides_every_option(tmp_path: Path, monkeypatch):
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
    captured = _capture_run_batch(monkeypatch)
    with pytest.raises(SystemExit) as info:
        run_module.main(["--input", str(_csv(tmp_path)), *extra])
    assert info.value.code == 2
    assert captured == []


def test_cli_returns_1_when_any_repo_failed(tmp_path: Path, monkeypatch):
    _capture_run_batch(monkeypatch, failed_count=1)
    assert run_module.main(["--input", str(_csv(tmp_path))]) == 1
