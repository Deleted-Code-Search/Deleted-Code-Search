"""clone.py 테스트 (이슈 #5).

`tmp_path`에 실제 git 저장소를 만들어(subprocess) "원격"처럼 쓰고, 로컬 경로를
clone_url로 넘겨 검증한다. 네트워크 없음.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pipeline import clone as clone_module

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _init_source_repo(path: Path) -> Path:
    """ "원격"으로 쓸 저장소를 만든다. 기본 브랜치는 `main`으로 고정한다."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("a", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "initial commit")
    return path


def _commit_count(path: Path) -> int:
    result = _git(path, "rev-list", "--count", "HEAD")
    return int(result.stdout.strip())


def _on_rmtree_error(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Windows에서 git 오브젝트 파일이 읽기 전용이라 rmtree가 실패하는 것을 피한다."""
    del exc
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _force_rmtree(path: Path) -> None:
    shutil.rmtree(path, onexc=_on_rmtree_error)


# subprocess.run(["git", "--version"]) 대신 shutil.which를 쓴다 — git 실행 파일 자체가
# 없으면 subprocess.run이 FileNotFoundError를 던져서 skip 마커가 적용되기 전에 테스트
# 수집(collection) 자체가 실패할 수 있다 (CodeRabbit 리뷰).
_GIT_MISSING = shutil.which("git") is None
requires_git = pytest.mark.skipif(_GIT_MISSING, reason="git CLI가 필요하다")


# --------------------------------------------------------------------------------------
# repo_dir: 순수 함수, owner/name 경로 보존
# --------------------------------------------------------------------------------------


def test_repo_dir_nests_owner_and_name():
    result = clone_module.repo_dir("/repos", "django/django")
    assert result == Path("/repos") / "django" / "django"


def test_repo_dir_preserves_hyphen_dot_underscore():
    result = clone_module.repo_dir("/repos", "scikit-learn/scikit-learn")
    assert result == Path("/repos") / "scikit-learn" / "scikit-learn"


@pytest.mark.parametrize("repo", ["no-slash", "a/b/c", "a/../b", "a//b", ""])
def test_repo_dir_rejects_malformed_repo(repo):
    with pytest.raises(ValueError):
        clone_module.repo_dir("/repos", repo)


# --------------------------------------------------------------------------------------
# repo_dir: 경로 이탈 방지 (REPOS_DIR 밖을 가리키는 owner/name 거부)
# --------------------------------------------------------------------------------------


def test_repo_dir_rejects_dotdot_owner(tmp_path: Path):
    # "../evil" -> owner=".." — 이전 버전의 문자 화이트리스트는 이걸 통과시켰다.
    with pytest.raises(ValueError):
        clone_module.repo_dir(tmp_path, "../evil")


def test_repo_dir_rejects_dotdot_name(tmp_path: Path):
    with pytest.raises(ValueError):
        clone_module.repo_dir(tmp_path, "owner/..")


def test_repo_dir_rejects_dot_segment(tmp_path: Path):
    with pytest.raises(ValueError):
        clone_module.repo_dir(tmp_path, "./repo")


def test_repo_dir_rejects_backslash_in_segment(tmp_path: Path):
    with pytest.raises(ValueError):
        clone_module.repo_dir(tmp_path, "acme/wid\\gets")
    with pytest.raises(ValueError):
        clone_module.repo_dir(tmp_path, "ac\\me/widgets")


def test_repo_dir_normal_input_still_nests_under_repos_dir(tmp_path: Path):
    result = clone_module.repo_dir(tmp_path, "acme/widgets")
    assert result == tmp_path / "acme" / "widgets"


# --------------------------------------------------------------------------------------
# is_git_repo: 저장소 "루트"만 True (nested 하위 디렉터리 오인 방지, CodeRabbit 리뷰)
#
# `git rev-parse --git-dir`만 보던 이전 구현은 git이 상위로 올라가 `.git`을 찾는 동작
# 때문에, 저장소 안의 평범한 하위 디렉터리도 True로 잘못 판정했다(재현 확인됨).
# --------------------------------------------------------------------------------------


@requires_git
class TestIsGitRepo:
    def test_repo_root_is_true(self, tmp_path: Path):
        repo = _init_source_repo(tmp_path / "repo")
        assert clone_module.is_git_repo(repo) is True

    def test_nested_directory_inside_repo_is_false(self, tmp_path: Path):
        repo = _init_source_repo(tmp_path / "repo")
        nested = repo / "nested"
        nested.mkdir()

        assert clone_module.is_git_repo(nested) is False

    def test_plain_non_git_directory_is_false(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert clone_module.is_git_repo(plain) is False

    def test_nonexistent_path_is_false(self, tmp_path: Path):
        assert clone_module.is_git_repo(tmp_path / "does-not-exist") is False


# --------------------------------------------------------------------------------------
# clone(): 실제 git 저장소
# --------------------------------------------------------------------------------------


@requires_git
class TestClone:
    def test_new_repo_full_clone_succeeds(self, tmp_path: Path):
        source = _init_source_repo(tmp_path / "source")
        _git(source, "commit", "--allow-empty", "-q", "-m", "second commit")
        repos_dir = tmp_path / "repos"

        result = clone_module.clone("acme/widgets", str(source), repos_dir)

        assert result == repos_dir / "acme" / "widgets"
        assert clone_module.is_git_repo(result)
        assert clone_module.is_shallow_clone(result) is False
        assert _commit_count(result) == 2

    def test_existing_valid_clone_is_reused_without_fetch(self, tmp_path: Path):
        source = _init_source_repo(tmp_path / "source")
        repos_dir = tmp_path / "repos"
        clone_url = str(source)

        first = clone_module.clone("acme/widgets", clone_url, repos_dir)
        # 원본에는 없는, 클론에만 있는 커밋을 만들어 둔다. fetch/재clone이 일어나면
        # 사라지거나(재clone) origin이 사라진 채로 fetch를 시도해 예외가 났을 것이다.
        _git(first, "commit", "--allow-empty", "-q", "-m", "local-only marker")
        marker_count = _commit_count(first)
        # source를 지워서, 재사용 시 조금이라도 네트워크/원격 접근(fetch 등)을
        # 시도하면 실패하게 만든다.
        _force_rmtree(source)

        second = clone_module.clone("acme/widgets", clone_url, repos_dir)

        assert second == first
        assert _commit_count(second) == marker_count  # 그대로 재사용됐다 (덮어쓰기 없음)

    def test_existing_non_git_directory_is_rejected(self, tmp_path: Path):
        source = _init_source_repo(tmp_path / "source")
        repos_dir = tmp_path / "repos"
        target = repos_dir / "acme" / "widgets"
        target.mkdir(parents=True)
        (target / "note.txt").write_text("not a git repo", encoding="utf-8")

        with pytest.raises(RuntimeError):
            clone_module.clone("acme/widgets", str(source), repos_dir)

        # 아무것도 지우거나 덮어쓰지 않았다.
        assert (target / "note.txt").read_text(encoding="utf-8") == "not a git repo"
        assert not (target / ".git").exists()

    def test_existing_shallow_clone_is_rejected(self, tmp_path: Path):
        source = _init_source_repo(tmp_path / "source")
        _git(source, "commit", "--allow-empty", "-q", "-m", "second commit")
        repos_dir = tmp_path / "repos"
        target = repos_dir / "acme" / "widgets"
        target.parent.mkdir(parents=True)
        # 로컬 경로(하드링크 최적화)로는 --depth가 무시된다("--depth is ignored in
        # local clones") — file:// URL을 써야 실제로 shallow clone이 된다.
        _git(repos_dir, "clone", "-q", "--depth", "1", source.as_uri(), str(target))
        assert clone_module.is_shallow_clone(target) is True

        with pytest.raises(RuntimeError, match="shallow"):
            clone_module.clone("acme/widgets", str(source), repos_dir)

        # 자동으로 unshallow/재클론되지 않았다.
        assert clone_module.is_shallow_clone(target) is True
        assert _commit_count(target) == 1

    def test_existing_clone_with_different_origin_is_rejected(self, tmp_path: Path):
        source_a = _init_source_repo(tmp_path / "source_a")
        source_b = _init_source_repo(tmp_path / "source_b")
        repos_dir = tmp_path / "repos"
        target = repos_dir / "acme" / "widgets"
        target.parent.mkdir(parents=True)
        _git(repos_dir, "clone", "-q", str(source_a), str(target))
        assert clone_module.origin_url(target) == str(source_a)

        with pytest.raises(RuntimeError, match="origin"):
            clone_module.clone("acme/widgets", str(source_b), repos_dir)

        # origin도, 내용도 그대로다 (덮어쓰기 없음).
        assert clone_module.origin_url(target) == str(source_a)

    def test_repo_layout_preserves_owner_and_name_under_repos_dir(self, tmp_path: Path):
        source = _init_source_repo(tmp_path / "source")
        repos_dir = tmp_path / "repos"

        result = clone_module.clone("my-org/my-repo.py", str(source), repos_dir)

        assert result == repos_dir / "my-org" / "my-repo.py"
        assert result.parent.parent == repos_dir
