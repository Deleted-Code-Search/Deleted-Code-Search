"""walk.py 테스트 (이슈 #5).

`tmp_path`에 작은 git 저장소를 실제로 만들어(subprocess) 검증한다. 네트워크 없음.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.walk import (
    CommitPair,
    LogEntry,
    parse_git_log,
    to_commit_pairs,
    walk_commits,
    within_window,
)

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
    # `git log -z --pretty=tformat:...` 출력 모양: 커밋마다 NUL로 끝난다.
    raw = (
        "a1\x1f\x1f2024-01-01T00:00:00+00:00\x1f1704067200\x1froot\n\x00"
        "a2\x1fa1\x1f2024-01-02T00:00:00+00:00\x1f1704153600\x1fsecond\n\x00"
        "a3\x1fa2 a1b\x1f2024-01-03T00:00:00+00:00\x1f1704240000\x1fmerge\n\x00"
    )
    entries = parse_git_log(raw)

    assert entries == [
        LogEntry("a1", (), "2024-01-01T00:00:00+00:00", "root", 1704067200),
        LogEntry("a2", ("a1",), "2024-01-02T00:00:00+00:00", "second", 1704153600),
        LogEntry("a3", ("a2", "a1b"), "2024-01-03T00:00:00+00:00", "merge", 1704240000),
    ]


def test_parse_git_log_keeps_control_chars_inside_message():
    """메시지 안의 0x1e(예전 레코드 구분자)·0x1f(필드 구분자)는 레코드를 쪼개지 않는다.

    Issue #144.
    """
    raw = (
        "a1\x1f\x1f2024-01-01T00:00:00+00:00\x1f1704067200\x1froot\n\x00"
        "a2\x1fa1\x1f2024-01-02T00:00:00+00:00\x1f1704153600"
        "\x1fsubject\n\nurl://x\x1e:@y \x1f z\n\x00"
    )
    entries = parse_git_log(raw)

    assert entries == [
        LogEntry("a1", (), "2024-01-01T00:00:00+00:00", "root", 1704067200),
        LogEntry(
            "a2",
            ("a1",),
            "2024-01-02T00:00:00+00:00",
            "subject\n\nurl://x\x1e:@y \x1f z",
            1704153600,
        ),
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

    def test_non_utf8_commit_message_is_replaced_not_fatal(self, tmp_path: Path):
        """encoding 헤더 없이 깨진 UTF-8 바이트가 든 메시지도 순회를 멈추지 않는다 (Issue #144).

        celery `18d2b79f`처럼 메시지가 잘린 멀티바이트(`\\xc3`)로 끝나는 경우를 재현한다.
        깨진 바이트만 U+FFFD가 되고, 앞뒤 커밋과 정상 UTF-8 메시지는 그대로다.
        """
        repo = _init_repo(tmp_path / "non_utf8_message")
        root = _commit(repo, "a.txt", "a", "root commit")
        # `git commit`은 깨진 메시지를 latin-1로 보고 고쳐 저장하므로, 커밋 객체를 직접 쓴다.
        tree = _git(repo, "rev-parse", "HEAD^{tree}")
        raw_commit = (
            f"tree {tree}\nparent {root}\n"
            "author Test <test@example.com> 1700000000 +0000\n"
            "committer Test <test@example.com> 1700000000 +0000\n\n"
        ).encode() + b"99% Coverage for celery.backends.amqp\xc3\n"
        broken = (
            subprocess.run(
                ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
                cwd=repo,
                input=raw_commit,
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        _git(repo, "reset", "-q", "--hard", broken)
        after = _commit(repo, "c.txt", "c", "café 한글 commit")

        pairs = walk_commits(repo, "main")

        assert [p.commit_sha for p in pairs] == [broken, after]
        assert pairs[0].commit_message == "99% Coverage for celery.backends.amqp�"
        assert pairs[1].commit_message == "café 한글 commit"

    def test_record_separator_byte_in_commit_message_does_not_split_record(self, tmp_path: Path):
        """메시지에 literal 0x1e가 든 커밋도 한 레코드로 읽힌다 (Issue #144).

        scikit-learn `27ae0488`의 본문에는 `sql://\\ufffd\\ufffd\\x1e:@...` 처럼 0x1e가 실제로
        들어 있어, 0x1e를 레코드 구분자로 쓰던 때는 순회 전체가 ValueError로 실패했다.
        그 모양을 커밋 객체로 직접 써서 재현한다.
        """
        repo = _init_repo(tmp_path / "rs_in_message")
        root = _commit(repo, "a.txt", "a", "root commit")
        before = _commit(repo, "b.txt", "b", "normal commit before")
        tree = _git(repo, "rev-parse", "HEAD^{tree}")
        message = (
            "Update StatLib database URL\n\n"
            "Root URL redirect: sql://��\x1e:@localhost\n"
            "user '��\x1e'@ end\n"
        )
        raw_commit = (
            f"tree {tree}\nparent {before}\n"
            "author Test <test@example.com> 1700000000 +0000\n"
            "committer Test <test@example.com> 1700000000 +0000\n\n"
        ).encode() + message.encode("utf-8")
        with_rs = (
            subprocess.run(
                ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
                cwd=repo,
                input=raw_commit,
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        # 커밋 객체에 0x1e가 실제로 들어갔는지 확인한다 (mock이 아니라 진짜 재현인지).
        stored = subprocess.run(
            ["git", "cat-file", "commit", with_rs], cwd=repo, capture_output=True, check=True
        ).stdout
        assert stored.count(b"\x1e") == 2
        _git(repo, "reset", "-q", "--hard", with_rs)
        after = _commit(repo, "c.txt", "c", "normal commit after")

        pairs = walk_commits(repo, "main")

        assert [p.commit_sha for p in pairs] == [before, with_rs, after]
        assert [p.parent_sha for p in pairs] == [root, before, with_rs]
        assert pairs[0].commit_message == "normal commit before"
        assert pairs[1].commit_message == message.strip("\n")
        assert pairs[2].commit_message == "normal commit after"

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


# --------------------------------------------------------------------------------------
# 채굴 구간 제한 (`since`, Issue #148)
# --------------------------------------------------------------------------------------

_SINCE_2015 = datetime(2015, 1, 1, tzinfo=UTC)


def _commit_at(
    path: Path, filename: str, content: str, message: str, committed: str, authored: str = ""
) -> str:
    """커미터·작성 날짜를 고정한 커밋. `authored`를 비우면 커미터 날짜와 같다."""
    (path / filename).write_text(content, encoding="utf-8")
    env = {
        **os.environ,
        **_GIT_ENV,
        "GIT_COMMITTER_DATE": committed,
        "GIT_AUTHOR_DATE": authored or committed,
    }
    for args in (["add", filename], ["commit", "-q", "-m", message], ["rev-parse", "HEAD"]):
        result = subprocess.run(
            ["git", *args], cwd=path, env=env, capture_output=True, text=True, check=True
        )
    return result.stdout.strip()


def test_within_window_compares_committer_time():
    """커미터 시각(Unix 초)으로 비교한다. 경계 시각은 구간 안이다. 작성일은 보지 않는다."""
    cutoff = int(_SINCE_2015.timestamp())  # 1420070400
    entries = [
        LogEntry("before", ("p",), "2016-01-01T00:00:00+00:00", "m", cutoff - 1),
        LogEntry("edge", ("p",), "2010-01-01T00:00:00+00:00", "m", cutoff),
        LogEntry("after", ("p",), "2010-01-01T00:00:00+00:00", "m", cutoff + 3600),
    ]

    assert [entry.sha for entry in within_window(entries, _SINCE_2015)] == ["edge", "after"]


@requires_git
def test_since_handles_commit_with_broken_timezone(tmp_path: Path):
    """시간대가 깨진 커밋(requests `5e6ecdad`의 `+051800`)도 Unix 초로 판정한다.
    `%cI`로는 `+518:00`이 나와 `datetime.fromisoformat`이 실패했다."""
    repo = _init_repo(tmp_path / "broken_tz")
    _commit_at(repo, "a.txt", "a", "root", "2013-05-01T00:00:00+00:00")
    root = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    # `git commit`은 이런 시간대를 만들지 않으므로 커밋 객체를 직접 쓴다.
    raw_commit = (
        f"tree {tree}\nparent {root}\n"
        "author T <t@example.com> 1313584730 +051800\n"
        "committer T <t@example.com> 1313584730 +051800\n\nbroken tz\n"
    ).encode()
    broken = (
        subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
            cwd=repo,
            input=raw_commit,
            capture_output=True,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    _git(repo, "reset", "-q", "--hard", broken)
    newer = _commit_at(repo, "b.txt", "b", "newer", "2016-01-01T00:00:00+00:00")

    assert [p.commit_sha for p in walk_commits(repo, "main", since=_SINCE_2015)] == [newer]
    assert [p.commit_sha for p in walk_commits(repo, "main")] == [broken, newer]


def test_within_window_rejects_naive_since():
    """시간대 없는 `since`는 로컬 시각으로 추측하지 않고 거부한다."""
    with pytest.raises(ValueError):
        within_window([], datetime(2015, 1, 1))


@requires_git
class TestWalkCommitsSince:
    def test_since_skips_commits_before_window(self, tmp_path: Path):
        """구간 밖 커밋은 결과에 없고, since가 없으면 전체 이력 그대로다."""
        repo = _init_repo(tmp_path / "window")
        _commit_at(repo, "a.txt", "a", "root", "2013-05-01T00:00:00+00:00")
        old = _commit_at(repo, "b.txt", "b", "old", "2014-06-01T00:00:00+00:00")
        new1 = _commit_at(repo, "c.txt", "c", "new1", "2015-03-01T00:00:00+00:00")
        new2 = _commit_at(repo, "d.txt", "d", "new2", "2020-01-01T00:00:00+00:00")

        windowed = walk_commits(repo, "main", since=_SINCE_2015)
        full = walk_commits(repo, "main")

        assert [p.commit_sha for p in windowed] == [new1, new2]
        assert windowed[0].parent_sha == old  # 부모는 구간 밖이어도 diff 기준으로 그대로다
        assert [p.commit_sha for p in full] == [old, new1, new2]

    def test_since_uses_committer_date_not_author_date(self, tmp_path: Path):
        """작성일이 오래됐어도 커미터 날짜가 구간 안이면 포함한다(rebase·squash로 들어온 커밋)."""
        repo = _init_repo(tmp_path / "rebased")
        _commit_at(repo, "a.txt", "a", "root", "2013-05-01T00:00:00+00:00")
        rebased = _commit_at(
            repo, "b.txt", "b", "rebased", "2016-01-01T00:00:00+00:00", "2012-01-01T00:00:00+00:00"
        )
        _commit_at(
            repo, "c.txt", "c", "old", "2014-01-01T00:00:00+00:00", "2016-01-01T00:00:00+00:00"
        )

        assert [p.commit_sha for p in walk_commits(repo, "main", since=_SINCE_2015)] == [rebased]

    def test_since_judges_each_commit_even_with_clock_skew(self, tmp_path: Path):
        """자손의 커미터 날짜가 구간 밖(시계 어긋남)이어도 그 조상 중 구간 안 커밋은 남는다.
        `git log --since`는 구간 밖 커밋의 조상을 함께 빼서 `inside`를 놓친다."""
        repo = _init_repo(tmp_path / "skew")
        _commit_at(repo, "a.txt", "a", "root", "2013-05-01T00:00:00+00:00")
        inside = _commit_at(repo, "b.txt", "b", "inside", "2016-01-01T00:00:00+00:00")
        _commit_at(repo, "c.txt", "c", "skewed", "2014-01-01T00:00:00+00:00")
        latest = _commit_at(repo, "d.txt", "d", "latest", "2017-01-01T00:00:00+00:00")

        assert [p.commit_sha for p in walk_commits(repo, "main", since=_SINCE_2015)] == [
            inside,
            latest,
        ]
