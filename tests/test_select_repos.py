"""저장소 선정 스크립트 테스트 (이슈 #1).

네트워크를 타지 않는다. 비율·점수 계산은 순수 함수로, API 경로는 가짜 클라이언트로 본다.
"""

import email.message
import urllib.error
from pathlib import Path

import pytest

from pipeline import select_repos as sr

# --------------------------------------------------------------------------------------
# 라이선스 필터 (§4.3 ②, ADR-007)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spdx",
    ["MIT", "mit", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", " MIT "],
)
def test_permissive_licenses_pass(spdx):
    assert sr.is_permissive_license(spdx) is True


@pytest.mark.parametrize("spdx", ["GPL-3.0", "AGPL-3.0", "LGPL-2.1", "NOASSERTION", "", None])
def test_non_permissive_licenses_fail(spdx):
    assert sr.is_permissive_license(spdx) is False


# --------------------------------------------------------------------------------------
# 커밋 수 (Link 헤더)
# --------------------------------------------------------------------------------------


def test_commit_count_reads_last_page_from_link_header():
    link = (
        '<https://api.github.com/repositories/1/commits?per_page=1&page=2>; rel="next", '
        '<https://api.github.com/repositories/1/commits?per_page=1&page=32041>; rel="last"'
    )
    assert sr.commit_count_from_link(link, 1) == 32041


def test_commit_count_without_link_header_uses_page_count():
    # 커밋이 한 페이지뿐이면 Link 가 없다 → 받은 개수가 곧 총 개수
    assert sr.commit_count_from_link(None, 1) == 1
    assert sr.commit_count_from_link("", 0) == 0


def test_commit_count_ignores_malformed_link():
    assert sr.commit_count_from_link('<https://api.github.com/x>; rel="next"', 1) == 1


# --------------------------------------------------------------------------------------
# 이슈 참조 추출 (§4.3 ④)
# --------------------------------------------------------------------------------------


def test_extract_issue_refs_collects_numbers():
    assert sr.extract_issue_refs("fix crash\n\nCloses #12, refs #7") == (7, 12)


def test_extract_issue_refs_ignores_merge_commit_pr_number():
    message = "Merge pull request #4321 from user/branch"
    assert sr.extract_issue_refs(message) == ()


def test_extract_issue_refs_ignores_squash_suffix_pr_number():
    message = "feat: add adapter (#5555)"
    assert sr.extract_issue_refs(message) == ()


def test_extract_issue_refs_keeps_issue_when_squash_suffix_present():
    message = "fix: guard None path (#5555)\n\nFixes #4444"
    assert sr.extract_issue_refs(message) == (4444,)


def test_extract_issue_refs_honours_exclude_list():
    assert sr.extract_issue_refs("see #10 and #11", exclude=[10]) == (11,)


def test_extract_issue_refs_on_empty_text():
    assert sr.extract_issue_refs(None) == ()
    assert sr.extract_issue_refs("") == ()


# --------------------------------------------------------------------------------------
# 커밋 한 건 판정
# --------------------------------------------------------------------------------------


def test_direct_push_commit_is_not_via_pr():
    sample = sr.summarize_commit("abc", "chore: bump version", [])
    assert sample.via_pr is False
    assert sample.refs_issue is False


def test_merged_pr_marks_commit_as_via_pr():
    pulls = [{"number": 99, "merged_at": "2026-01-02T00:00:00Z", "title": "fix", "body": None}]
    sample = sr.summarize_commit("abc", "fix: something", pulls)
    assert sample.via_pr is True


def test_open_pr_does_not_count_as_via_pr():
    pulls = [{"number": 99, "merged_at": None, "title": "wip", "body": "Fixes #3"}]
    sample = sr.summarize_commit("abc", "wip", pulls)
    assert sample.via_pr is False
    # 머지되지 않은 PR 본문은 근거로 쓰지 않는다
    assert sample.issue_refs == ()


def test_issue_ref_can_come_from_merged_pr_body():
    pulls = [
        {
            "number": 99,
            "merged_at": "2026-01-02T00:00:00Z",
            "title": "fix: guard None (#99)",
            "body": "Fixes #42",
        }
    ]
    sample = sr.summarize_commit("abc", "fix: guard None", pulls)
    assert sample.via_pr is True
    assert sample.issue_refs == (42,)


def test_pr_own_number_is_not_counted_as_issue_ref():
    pulls = [{"number": 99, "merged_at": "2026-01-02T00:00:00Z", "title": "fix", "body": "#99"}]
    sample = sr.summarize_commit("abc", "Merge pull request #99 from u/b", pulls)
    assert sample.issue_refs == ()


# --------------------------------------------------------------------------------------
# 비율·점수
# --------------------------------------------------------------------------------------


def _sample(via_pr, refs=()):
    return sr.CommitSample(sha="x", via_pr=via_pr, issue_refs=tuple(refs))


def test_compute_ratios():
    samples = [_sample(True, [1]), _sample(True), _sample(False, [2]), _sample(False)]
    pr_ratio, issue_ratio = sr.compute_ratios(samples)
    assert pr_ratio == 0.5
    assert issue_ratio == 0.5


def test_compute_ratios_on_empty_sample():
    assert sr.compute_ratios([]) == (0.0, 0.0)


def test_scale_score_bounds():
    assert sr.scale_score(500) == 0.0
    assert sr.scale_score(sr.MIN_COMMITS) == 0.0
    assert sr.scale_score(sr.SCALE_SATURATION_COMMITS) == pytest.approx(1.0)
    assert sr.scale_score(500_000) == 1.0
    assert 0.0 < sr.scale_score(5_000) < 1.0


def test_score_is_weighted_sum():
    expected = 0.5 * 0.8 + 0.3 * 0.6 + 0.2 * sr.scale_score(10_000)
    assert sr.score_candidate(0.8, 0.6, 10_000) == pytest.approx(expected, abs=1e-4)


def test_score_rewards_pr_culture_over_scale():
    pr_heavy = sr.score_candidate(0.95, 0.9, 1_200)
    big_but_no_pr = sr.score_candidate(0.10, 0.1, 50_000)
    assert pr_heavy > big_but_no_pr


def test_score_bounds():
    assert sr.score_candidate(0.0, 0.0, sr.MIN_COMMITS) == 0.0
    assert sr.score_candidate(1.0, 1.0, sr.SCALE_SATURATION_COMMITS) == pytest.approx(1.0)


# --------------------------------------------------------------------------------------
# .env 파싱 (§8.4 — 토큰은 .env 에만)
# --------------------------------------------------------------------------------------


def test_parse_dotenv_handles_comments_quotes_and_export():
    text = "\n".join(
        [
            "# 주석",
            "",
            "GITHUB_TOKEN=ghp_example",
            'DATABASE_URL="postgresql://u:p@localhost/db"',
            "export DATA_DIR='./data'",
            "BROKEN_LINE",
        ]
    )
    values = sr.parse_dotenv(text)
    assert values == {
        "GITHUB_TOKEN": "ghp_example",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "DATA_DIR": "./data",
    }


def test_load_env_file_does_not_override_existing_environ(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=from_file\nDATA_DIR=./from_file\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "from_shell")
    monkeypatch.delenv("DATA_DIR", raising=False)

    sr.load_env_file(env_file)

    assert sr.os.environ["GITHUB_TOKEN"] == "from_shell"
    assert sr.os.environ["DATA_DIR"] == "./from_file"


def test_load_env_file_missing_is_noop(tmp_path):
    assert sr.load_env_file(tmp_path / "nope.env") == {}


# --------------------------------------------------------------------------------------
# 클라이언트: 캐시·레이트리밋 (§7 한도 5,000회/시간)
# --------------------------------------------------------------------------------------


def _fetcher(payload=b'{"ok": true}', headers=None, calls=None):
    def fetch(url, request_headers):
        if calls is not None:
            calls.append(url)
        return 200, dict(headers or {"x-ratelimit-remaining": "4999"}), payload

    return fetch


def test_second_run_uses_cache_instead_of_calling_api(tmp_path):
    calls = []
    client = sr.GitHubClient("t", tmp_path / "cache", fetcher=_fetcher(calls=calls))
    first = client.get("/repos/a/b")
    second = client.get("/repos/a/b")

    assert first.body == second.body == {"ok": True}
    assert len(calls) == 1
    assert client.api_calls == 1
    assert client.cache_hits == 1

    # 새 클라이언트(= 재실행)도 같은 캐시 디렉터리를 읽는다
    fresh = sr.GitHubClient("t", tmp_path / "cache", fetcher=_fetcher(calls=calls))
    assert fresh.get("/repos/a/b").body == {"ok": True}
    assert len(calls) == 1


def test_refresh_flag_bypasses_cache(tmp_path):
    calls = []
    sr.GitHubClient("t", tmp_path / "cache", fetcher=_fetcher(calls=calls)).get("/repos/a/b")
    client = sr.GitHubClient("t", tmp_path / "cache", refresh=True, fetcher=_fetcher(calls=calls))
    client.get("/repos/a/b")
    assert len(calls) == 2


def _http_error(code, headers):
    message = email.message.Message()
    for key, value in headers.items():
        message[key] = value
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", message, None)


def test_404_with_allow_404_returns_none_and_is_cached(tmp_path):
    calls = []

    def fetch(url, request_headers):
        calls.append(url)
        raise _http_error(404, {})

    client = sr.GitHubClient("t", tmp_path / "cache", fetcher=fetch)
    assert client.get("/repos/a/gone", allow_404=True) is None
    assert client.get("/repos/a/gone", allow_404=True) is None
    assert len(calls) == 1  # 없는 저장소를 두 번 묻지 않는다


def test_rate_limit_403_waits_and_retries(tmp_path):
    slept = []
    attempts = {"n": 0}

    def fetch(url, request_headers):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _http_error(403, {"Retry-After": "3"})
        return 200, {"x-ratelimit-remaining": "10"}, b'{"ok": true}'

    client = sr.GitHubClient(
        "t", tmp_path / "cache", fetcher=fetch, sleep=lambda seconds: slept.append(seconds)
    )
    assert client.get("/repos/a/b").body == {"ok": True}
    assert slept == [3]


def test_paginate_stops_on_short_page(tmp_path):
    def fetch(url, request_headers):
        return 200, {}, b'[{"sha": "a"}, {"sha": "b"}]'

    client = sr.GitHubClient("t", tmp_path / "cache", fetcher=fetch)
    items = client.paginate("/repos/a/b/commits", max_items=100)
    assert len(items) == 2


# --------------------------------------------------------------------------------------
# 저장소 평가 (가짜 클라이언트)
# --------------------------------------------------------------------------------------


class FakeClient:
    """evaluate_repo 가 쓰는 세 메서드만 흉내 낸다."""

    def __init__(self, repo_item=None, commit_total=5000, commits=(), pulls=None):
        self.repo_item = repo_item
        self.commit_total = commit_total
        self.commits = list(commits)
        self.pulls = pulls or {}

    def get_json(self, path, params=None, *, allow_404=False):
        if path.endswith("/pulls"):
            return self.pulls.get(path.split("/")[-2], [])
        if path.startswith("/repos/") and path.count("/") == 3:
            return self.repo_item
        return []

    def get(self, path, params=None, *, allow_404=False):
        link = (
            f'<https://api.github.com/x?per_page=1&page={self.commit_total}>; rel="last"'
            if self.commit_total > 1
            else None
        )
        return sr.Response(200, {"link": link} if link else {}, [{"sha": "head"}])

    def paginate(self, path, params=None, *, max_items, max_pages=10):
        return self.commits[:max_items]


def _repo_item(**overrides):
    item = {
        "full_name": "acme/tool",
        "language": "Python",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 4200,
        "default_branch": "main",
        "size": 120_000,
        "archived": False,
        "fork": False,
    }
    item.update(overrides)
    return item


def _commit(sha, message, parents=1):
    return {
        "sha": sha,
        "commit": {"message": message},
        "parents": [{"sha": f"p{index}"} for index in range(parents)],
    }


def test_evaluate_repo_scores_a_healthy_candidate():
    commits = [_commit("c1", "fix: a\n\nFixes #10"), _commit("c2", "feat: b")]
    pulls = {
        "c1": [{"number": 1, "merged_at": "2026-01-01T00:00:00Z", "title": "fix: a", "body": ""}],
        "c2": [{"number": 2, "merged_at": "2026-01-02T00:00:00Z", "title": "feat: b", "body": ""}],
    }
    client = FakeClient(_repo_item(), commit_total=12_000, commits=commits, pulls=pulls)

    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)

    assert row.selected is True
    assert row.commits == 12_000
    assert row.pr_ratio == 1.0
    assert row.issue_ref_ratio == 0.5
    assert row.pr_gate_pass is True
    assert row.sampled_commits == 2
    assert row.score == sr.score_candidate(1.0, 0.5, 12_000)
    assert row.stars == 4200  # 점수에는 안 들어가지만 CSV 에는 남는다 (ADR-006)


def test_evaluate_repo_excludes_non_permissive_license_before_counting_commits():
    client = FakeClient(_repo_item(license={"spdx_id": "GPL-3.0"}), commit_total=99_999)
    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)

    assert row.selected is False
    assert row.exclude_reason == "LICENSE_NOT_PERMISSIVE:GPL-3.0"
    assert row.commits is None  # 라이선스에서 걸리면 커밋 수를 세지 않는다


def test_evaluate_repo_excludes_small_repo():
    client = FakeClient(_repo_item(), commit_total=300)
    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)
    assert row.exclude_reason == "COMMITS_BELOW_1000:300"


def test_evaluate_repo_excludes_non_python_and_archived_and_fork():
    since = "2026-01-01T00:00:00Z"
    other_language = sr.evaluate_repo(
        FakeClient(_repo_item(language="Go")), "a/b", since=since, sample_size=5
    )
    archived = sr.evaluate_repo(
        FakeClient(_repo_item(archived=True)), "a/b", since=since, sample_size=5
    )
    fork = sr.evaluate_repo(FakeClient(_repo_item(fork=True)), "a/b", since=since, sample_size=5)

    assert other_language.exclude_reason == "LANGUAGE_NOT_PYTHON:Go"
    assert archived.exclude_reason == "ARCHIVED"
    assert fork.exclude_reason == "FORK"


def test_evaluate_repo_marks_missing_repo():
    client = FakeClient(repo_item=None)
    row = sr.evaluate_repo(client, "acme/gone", since="2026-01-01T00:00:00Z", sample_size=5)
    assert row.exclude_reason == "NOT_FOUND"


def test_is_bot_commit():
    assert sr.is_bot_commit({"author": {"login": "dependabot[bot]", "type": "Bot"}}) is True
    assert sr.is_bot_commit({"author": {"login": "latest-changes[bot]"}}) is True
    assert sr.is_bot_commit({"author": {"login": "davidism", "type": "User"}}) is False
    assert sr.is_bot_commit({"author": None}) is False
    assert sr.is_bot_commit({}) is False


def test_evaluate_repo_skips_bot_commits_in_sample():
    # 봇이 PR 없이 main 에 직접 미는 커밋이 표본을 채우면 PR 경유 비율이 실제보다 낮게 나온다
    bot = _commit("b1", "📝 Update release notes")
    bot["author"] = {"login": "latest-changes[bot]", "type": "Bot"}
    human = _commit("c1", "fix: guard None\n\nFixes #7")
    human["author"] = {"login": "someone", "type": "User"}
    pulls = {"c1": [{"number": 5, "merged_at": "2026-01-02T00:00:00Z", "title": "fix", "body": ""}]}
    client = FakeClient(_repo_item(), commit_total=2_000, commits=[bot, human], pulls=pulls)

    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)

    assert row.sampled_commits == 1
    assert row.pr_ratio == 1.0


def test_evaluate_repo_skips_merge_commits_in_sample():
    commits = [_commit("m1", "Merge pull request #1 from u/b", parents=2), _commit("c1", "fix")]
    pulls = {"c1": []}
    client = FakeClient(_repo_item(), commit_total=2_000, commits=commits, pulls=pulls)

    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)

    assert row.sampled_commits == 1  # 병합 커밋은 표본에서 뺀다 (§4.2 ①)
    assert row.pr_ratio == 0.0


def test_enforce_pr_gate_turns_low_pr_ratio_into_exclusion():
    commits = [_commit("c1", "fix"), _commit("c2", "fix")]
    client = FakeClient(_repo_item(), commit_total=2_000, commits=commits, pulls={})
    row = sr.evaluate_repo(
        client,
        "acme/tool",
        since="2026-01-01T00:00:00Z",
        sample_size=30,
        enforce_pr_gate=True,
    )
    assert row.exclude_reason == "PR_RATIO_BELOW_GATE:0.000"


def test_pr_gate_is_only_a_flag_by_default():
    commits = [_commit("c1", "fix")]
    client = FakeClient(_repo_item(), commit_total=2_000, commits=commits, pulls={})
    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)
    assert row.selected is True
    assert row.pr_gate_pass is False


# --------------------------------------------------------------------------------------
# 정렬·CSV
# --------------------------------------------------------------------------------------


def test_sort_rows_puts_high_score_first_and_excluded_last():
    rows = [
        sr.RepoRow(repo="c/c", score=0.4),
        sr.RepoRow(repo="z/z", exclude_reason="ARCHIVED"),
        sr.RepoRow(repo="a/a", score=0.9),
        sr.RepoRow(repo="b/b", exclude_reason="FORK"),
    ]
    assert [row.repo for row in sr.sort_rows(rows)] == ["a/a", "c/c", "b/b", "z/z"]


def test_write_csv_has_charter_columns_and_reasons(tmp_path: Path):
    rows = [
        sr.RepoRow(
            repo="acme/tool",
            license_id="MIT",
            commits=12_000,
            pr_ratio=0.9,
            issue_ref_ratio=0.5,
            stars=4200,
            score=0.8123,
            default_branch="main",
            size_kb=120_000,
            sampled_commits=30,
        ),
        sr.RepoRow(repo="gnu/thing", license_id="GPL-3.0", exclude_reason="LICENSE_NOT_PERMISSIVE"),
    ]
    out = tmp_path / "docs" / "repo_candidates.csv"
    sr.write_csv(rows, out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == list(sr.CSV_COLUMNS)
    assert lines[1].startswith("acme/tool,MIT,12000,0.900,0.500,4200,0.8123,main,120000,30,true,")
    assert lines[1].endswith("CANDIDATE,")
    assert "EXCLUDED,LICENSE_NOT_PERMISSIVE" in lines[2]


def test_since_iso_is_utc_midnight_one_year_back():
    # 자정으로 잘라야 같은 날 재실행이 같은 URL 을 만들어 캐시에 맞는다
    from datetime import UTC, datetime

    now = datetime(2026, 9, 10, 12, 34, 56, tzinfo=UTC)
    assert sr.since_iso(365, now=now) == "2025-09-10T00:00:00Z"
    later_same_day = datetime(2026, 9, 10, 23, 59, 59, tzinfo=UTC)
    assert sr.since_iso(365, now=later_same_day) == sr.since_iso(365, now=now)


def test_charter_seed_repos_are_evaluated_so_examples_appear_in_csv():
    # §4.3 초기 후보 예시 10개는 검색 결과와 무관하게 CSV 에 있어야 한다 (이슈 #1 완료 조건)
    assert "django/django" in sr.CHARTER_SEED_REPOS
    assert len(sr.CHARTER_SEED_REPOS) == 10
    assert all("/" in name for name in sr.CHARTER_SEED_REPOS)


def test_build_query_covers_charter_language_and_public_only():
    query = sr.build_query(1000)
    assert "language:Python" in query
    assert "archived:false" in query
    assert "stars:>=1000" in query


def test_main_without_token_exits_with_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    exit_code = sr.main(["--env-file", str(tmp_path / "missing.env"), "--out", str(tmp_path / "o")])
    assert exit_code == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
