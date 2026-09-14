"""walk.py 테스트 (이슈 #5).

`tmp_path`에 작은 git 저장소를 실제로 만들어(subprocess) 검증한다. 네트워크 없음.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline.walk import CommitPair, LogEntry, parse_git_log, to_commit_pairs, walk_commits

# subprocess.run(["git", "--version"]) 대신 shutil.which를 쓴다 — git 실행 파일 자체가
# 없으면 subprocess.run이 FileNotFoundError를 던져서 skip 마커가 적용되기 전에 테스트
# 수집(collection) 자체가 실패할 수 있다 (CodeRabbit 리뷰).
_GIT_MISSING = shutil.which("git") is None
requires_git = pytest.mark.skipif(_GIT_MISSING, reason="git CLI가 필요하다")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    """기본 브랜치 이름을 환경(git 전역 설정 `init.defaultBranch`)에 기대지 않고
    `main`으로 고정한다."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    return path


def _commit(path: Path, filename: str, content: str, message: str) -> str:
    (path / filename).write_text(content, encoding="utf-8")
    _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD")


# --------------------------------------------------------------------------------------
# 순수 함수: parse_git_log / to_commit_pairs
# --------------------------------------------------------------------------------------


def test_parse_git_log_splits_records_and_parents():
    raw = "\x1e".join(
        [
            "a1\x1f\x1f2024-01-01T00:00:00+00:00\x1froot\n",
            "\na2\x1fa1\x1f2024-01-02T00:00:00+00:00\x1fsecond\n",
            "\na3\x1fa2 a1b\x1f2024-01-03T00:00:00+00:00\x1fmerge\n",
            "\n",
        ]
    )
    entries = parse_git_log(raw)

    assert entries == [
        LogEntry("a1", (), "2024-01-01T00:00:00+00:00", "root"),
        LogEntry("a2", ("a1",), "2024-01-02T00:00:00+00:00", "second"),
        LogEntry("a3", ("a2", "a1b"), "2024-01-03T00:00:00+00:00", "merge"),
    ]


def test_to_commit_pairs_skips_root_and_merge():
    entries = [
        LogEntry("root", (), "d1", "root commit"),
        LogEntry("mid", ("root",), "d2", "second"),
        LogEntry("merge", ("mid", "other"), "d3", "merge commit"),
    ]
    pairs = to_commit_pairs(entries)

    assert pairs == [CommitPair("mid", "root", "d2", "second")]


def test_to_commit_pairs_deduplicates_by_sha():
    """실제 git log는 중복을 안 내지만, 방어적으로 sha 기준 중복을 제거한다."""
    entries = [
        LogEntry("a", ("root",), "d1", "msg"),
        LogEntry("a", ("root",), "d1", "msg"),  # 중복
    ]
    pairs = to_commit_pairs(entries)

    assert [pair.commit_sha for pair in pairs] == ["a"]


# --------------------------------------------------------------------------------------
# 실제 git 저장소 (tmp_path)
# --------------------------------------------------------------------------------------


@requires_git
class TestWalkCommitsOnRealRepo:
    def test_linear_history_order_and_root_exclusion(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "linear")
        c1 = _commit(repo, "a.txt", "a", "root commit")
        c2 = _commit(repo, "b.txt", "b", "second commit")
        c3 = _commit(repo, "c.txt", "c", "third commit")

        pairs = walk_commits(repo, "main")

        # root(c1)는 diff 대상에서 빠지고, c2/c3만 부모와 함께 순서대로 남는다.
        assert [p.commit_sha for p in pairs] == [c2, c3]
        assert pairs[0].parent_sha == c1
        assert pairs[1].parent_sha == c2

    def test_merge_commit_is_excluded_but_branch_commits_are_included(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "merged")
        root = _commit(repo, "a.txt", "a", "root commit")
        second = _commit(repo, "b.txt", "b", "second commit")
        _git(repo, "checkout", "-q", "-b", "feature")
        feature_commit = _commit(repo, "c.txt", "c", "feature commit")
        _git(repo, "checkout", "-q", "-")
        main_commit = _commit(repo, "d.txt", "d", "main commit")
        merge_sha = _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge feature")

        pairs = walk_commits(repo, "main")
        shas = [p.commit_sha for p in pairs]

        assert merge_sha not in shas  # 병합 커밋 자체는 제외
        assert root not in shas  # root 커밋은 제외
        assert feature_commit in shas  # 곁가지 개별 커밋은 reachable하므로 포함
        assert second in shas
        assert main_commit in shas

    def test_result_order_is_topological_reverse(self, tmp_path: Path):
        """부모가 결과에 있으면 항상 자신보다 앞에 나온다 (merge 유무와 무관)."""
        repo = _init_repo(tmp_path / "topo")
        _commit(repo, "a.txt", "a", "root")
        second = _commit(repo, "b.txt", "b", "second")
        _git(repo, "checkout", "-q", "-b", "feature")
        feature_commit = _commit(repo, "c.txt", "c", "feature")
        _git(repo, "checkout", "-q", "-")
        main_commit = _commit(repo, "d.txt", "d", "main")
        _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge")

        pairs = walk_commits(repo, "main")
        index = {pair.commit_sha: position for position, pair in enumerate(pairs)}

        for pair in pairs:
            if pair.parent_sha in index:
                assert index[pair.parent_sha] < index[pair.commit_sha]
        # 곁가지·본선 둘 다 second 다음에 온다.
        assert index[feature_commit] > index[second]
        assert index[main_commit] > index[second]

    def test_no_duplicate_commit_sha_in_result(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "dedup")
        _commit(repo, "a.txt", "a", "root")
        _commit(repo, "b.txt", "b", "second")
        _git(repo, "checkout", "-q", "-b", "feature")
        _commit(repo, "c.txt", "c", "feature")
        _git(repo, "checkout", "-q", "-")
        _commit(repo, "d.txt", "d", "main")
        _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge")

        pairs = walk_commits(repo, "main")
        shas = [p.commit_sha for p in pairs]

        assert len(shas) == len(set(shas))

    def test_commit_message_and_author_date_are_carried_through(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "fields")
        _commit(repo, "a.txt", "a", "root commit")
        _commit(repo, "b.txt", "b", "second commit\n\nbody line")

        pairs = walk_commits(repo, "main")

        assert pairs[0].commit_message == "second commit\n\nbody line"
        assert pairs[0].author_date  # ISO 8601 문자열, 비어있지 않음

    def test_checked_out_feature_branch_does_not_affect_explicit_ref(self, tmp_path: Path):
        """저장소가 feature branch에 checkout돼 있어도 ref="main"을 넘기면 main만 돈다.

        walk_commits가 기본값 없이 ref를 요구하는 이유를 직접 검증한다 — 예전처럼
        ref 기본값이 "HEAD"였다면 이 테스트는 feature의 커밋까지 결과에 섞여 실패했을 것이다.
        """
        repo = _init_repo(tmp_path / "checkout_independent")
        root = _commit(repo, "a.txt", "a", "root commit")
        main_only = _commit(repo, "b.txt", "b", "main only commit")
        _git(repo, "checkout", "-q", "-b", "feature")
        feature_only = _commit(repo, "c.txt", "c", "feature only commit")
        # 의도적으로 main으로 되돌아가지 않는다 — 저장소가 feature에 checkout된 채로 둔다.
        assert _git(repo, "branch", "--show-current") == "feature"

        pairs = walk_commits(repo, "main")
        shas = [p.commit_sha for p in pairs]

        assert shas == [main_only]  # feature 커밋은 안 섞이고, main 이력만 나온다
        assert feature_only not in shas
        assert root not in shas  # root는 여전히 제외
        # 호출이 끝난 뒤에도 워킹 디렉터리 checkout 상태 자체는 건드리지 않는다.
        assert _git(repo, "branch", "--show-current") == "feature"
