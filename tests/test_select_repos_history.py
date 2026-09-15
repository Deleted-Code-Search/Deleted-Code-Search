"""선정 기준 v2 (#56) — 최초 커밋 연도·연도별 층화 PR 연결 비율. 네트워크 없음."""

import email.message
import urllib.error
from pathlib import Path

import pytest

from pipeline import select_repos as sr

# --------------------------------------------------------------------------------------
# 순수 함수
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_year", "expected"),
    [
        (2009, (True, 2015)),
        (2014, (True, 2015)),
        (2015, (False, 2015)),
        (2019, (False, 2019)),
        (2024, (False, 2024)),  # 2020년 이후 시작도 거르지 않고 전체 구간을 쓴다
    ],
)
def test_mining_window_flags_old_repos_and_keeps_new_ones(first_year, expected):
    assert sr.mining_window(first_year) == expected


def test_mining_window_custom_cutoff():
    assert sr.mining_window(2016, cutoff_year=2018) == (True, 2018)


def test_spread_pick_spreads_evenly_and_handles_edges():
    items = list(range(10))
    assert sr.spread_pick(items, 5) == [0, 2, 4, 7, 9]
    assert sr.spread_pick(items, 1) == [5]
    assert sr.spread_pick(items, 0) == []
    assert sr.spread_pick([], 3) == []
    assert sr.spread_pick([1, 2], 5) == [1, 2]


def test_spread_pick_never_repeats_an_item():
    for size in range(1, 30):
        for k in range(1, size + 1):
            picked = sr.spread_pick(list(range(size)), k)
            assert len(picked) == k
            assert len(set(picked)) == k


def _sample(via_pr):
    return sr.CommitSample(sha="x", via_pr=via_pr, issue_refs=())


def test_history_ratios_pools_all_years_and_separates_window():
    by_year = {
        2011: [_sample(False), _sample(False)],
        2012: [_sample(True), _sample(False)],
        2016: [_sample(True), _sample(True)],
        2017: [_sample(True)],
    }
    history, window, history_n, window_n = sr.history_ratios(by_year, since_year=2015)
    assert history == pytest.approx(4 / 7)
    assert window == 1.0
    assert (history_n, window_n) == (7, 3)


def test_history_ratios_without_samples_returns_none():
    assert sr.history_ratios({2020: []}, since_year=2020) == (None, None, 0, 0)


def test_format_pr_by_year_skips_empty_years():
    by_year = {2011: [_sample(False), _sample(False)], 2012: [], 2013: [_sample(True)]}
    assert sr.format_pr_by_year(by_year) == "2011:0/2|2013:1/1"


def test_parse_license_overrides():
    assert sr.parse_license_overrides(["celery/celery=BSD-3-Clause"]) == {
        "celery/celery": "BSD-3-Clause"
    }
    assert sr.parse_license_overrides(None) == {}


@pytest.mark.parametrize("bad", ["celery", "celery=BSD-3-Clause", "a/b=", "=MIT"])
def test_parse_license_overrides_rejects_malformed(bad):
    with pytest.raises(ValueError):
        sr.parse_license_overrides([bad])


def test_sort_rows_by_score_v2_puts_missing_v2_last_among_candidates():
    rows = [
        sr.RepoRow(repo="a/a", score=0.9, score_v2=0.5),
        sr.RepoRow(repo="b/b", score=0.5, score_v2=0.8),
        sr.RepoRow(repo="c/c", score=0.7, score_v2=None),
        sr.RepoRow(repo="z/z", exclude_reason="FORK"),
    ]
    by_v2 = [row.repo for row in sr.sort_rows(rows, by_score_v2=True)]
    assert by_v2 == ["b/b", "a/a", "c/c", "z/z"]
    assert [row.repo for row in sr.sort_rows(rows)] == ["a/a", "c/c", "b/b", "z/z"]


V1_COLUMNS = (
    "repo",
    "license",
    "commits",
    "pr_ratio",
    "issue_ref_ratio",
    "stars",
    "score",
    "default_branch",
    "size_kb",
    "sampled_commits",
    "pr_gate_pass",
    "selection_status",
    "exclude_reason",
)


def test_csv_v1_columns_are_unchanged_and_v2_extends_them():
    assert sr.CSV_COLUMNS == V1_COLUMNS
    assert sr.CSV_COLUMNS_V2[: len(V1_COLUMNS)] == V1_COLUMNS
    for column in ("first_commit_year", "recent_only", "window_pr_ratio", "score_v2"):
        assert column in sr.CSV_COLUMNS_V2
        assert column not in sr.CSV_COLUMNS


def test_write_csv_adds_v2_columns_only_with_history(tmp_path: Path):
    row = sr.RepoRow(repo="a/a", license_id="MIT", score=0.5, score_v2=0.6, first_commit_year=2019)

    plain = tmp_path / "v1.csv"
    sr.write_csv([row], plain)
    assert plain.read_text(encoding="utf-8").splitlines()[0].split(",") == list(sr.CSV_COLUMNS)

    rich = tmp_path / "v2.csv"
    sr.write_csv([row], rich, with_history=True)
    header, line = rich.read_text(encoding="utf-8").splitlines()[:2]
    assert header.split(",") == list(sr.CSV_COLUMNS_V2)
    assert dict(zip(header.split(","), line.split(","), strict=True))["first_commit_year"] == "2019"


def test_write_csv_keeps_license_manual_column_without_history(tmp_path: Path):
    row = sr.RepoRow(repo="c/c", license_id="BSD-3-Clause", license_manual=True, score=0.5)
    out = tmp_path / "o.csv"
    sr.write_csv([row], out)
    assert out.read_text(encoding="utf-8").splitlines()[0].split(",") == list(sr.CSV_COLUMNS_V2)


# --------------------------------------------------------------------------------------
# 가짜 클라이언트로 evaluate_repo(history_per_year=...)
# --------------------------------------------------------------------------------------


class FakeHistoryClient:
    """evaluate_repo + 선정 기준 v2 가 쓰는 호출만 흉내 낸다."""

    def __init__(
        self,
        repo_item,
        *,
        total=6000,
        since_total=1500,
        oldest_date="2011-02-13T18:41:18Z",
        commits_by_year=None,
        recent=(),
        pulls=None,
    ):
        self.repo_item = repo_item
        self.total = total
        self.since_total = since_total
        self.oldest_date = oldest_date
        self.commits_by_year = commits_by_year or {}
        self.recent = list(recent)
        self.pulls = pulls or {}
        self.since_calls = []

    def get(self, path, params=None, *, allow_404=False):
        params = params or {}
        if "since" in params:
            self.since_calls.append(params["since"])
        count = self.since_total if "since" in params else self.total
        link = f'<https://api.github.com/x?per_page=1&page={count}>; rel="last"'
        return sr.Response(200, {"link": link}, [{"sha": "head"}])

    def get_json(self, path, params=None, *, allow_404=False):
        params = params or {}
        if path.endswith("/pulls"):
            return self.pulls.get(path.split("/")[-2], [])
        if path.startswith("/repos/") and path.count("/") == 3:
            return self.repo_item
        if path.endswith("/commits"):
            if "page" in params:
                return [{"sha": "oldest", "commit": {"author": {"date": self.oldest_date}}}]
            if "until" in params:
                return self.commits_by_year.get(int(params["since"][:4]), [])
        return []

    def paginate(self, path, params=None, *, max_items, max_pages=10):
        return self.recent[:max_items]


def _repo_item(**overrides):
    item = {
        "full_name": "acme/tool",
        "language": "Python",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": 10,
        "default_branch": "main",
        "size": 1000,
        "archived": False,
        "fork": False,
    }
    item.update(overrides)
    return item


def _commit(sha, parents=1, bot=False):
    commit = {
        "sha": sha,
        "commit": {"message": f"change {sha}"},
        "parents": [{"sha": f"p{index}"} for index in range(parents)],
    }
    if bot:
        commit["author"] = {"login": "dependabot[bot]", "type": "Bot"}
    return commit


def _merged(number):
    return [{"number": number, "merged_at": "2020-01-01T00:00:00Z", "title": "t", "body": ""}]


def test_old_repo_gets_recent_only_flag_and_window_ratio():
    client = FakeHistoryClient(
        _repo_item(),
        total=6000,
        since_total=1500,
        oldest_date="2011-02-13T18:41:18Z",
        commits_by_year={
            2011: [_commit("a"), _commit("b")],
            2012: [_commit("c")],
            2015: [_commit("d"), _commit("e")],
            2016: [_commit("f")],
        },
        recent=[_commit("r")],
        pulls={"c": _merged(1), "d": _merged(2), "e": _merged(3), "r": _merged(4)},
    )

    row = sr.evaluate_repo(
        client,
        "acme/tool",
        since="2016-01-01T00:00:00Z",
        sample_size=30,
        history_per_year=5,
        current_year=2016,
    )

    assert row.selected is True  # 제외하지 않는다
    assert row.first_commit_date == "2011-02-13"
    assert row.first_commit_year == 2011
    assert row.recent_only is True
    assert row.mining_since_year == 2015
    assert row.window_commits == 1500
    assert client.since_calls == ["2015-01-01T00:00:00Z"]
    assert row.history_pr_ratio == pytest.approx(3 / 6)
    assert row.window_pr_ratio == pytest.approx(2 / 3)
    assert (row.history_sampled, row.window_sampled) == (6, 3)
    assert row.pr_by_year == "2011:0/2|2012:1/1|2015:2/2|2016:0/1"
    assert row.score == sr.score_candidate(1.0, 0.0, 6000)  # v1 점수는 그대로
    assert row.score_v2 == sr.score_candidate(2 / 3, 0.0, 1500)


def test_repo_started_after_2020_is_kept_with_full_window():
    client = FakeHistoryClient(
        _repo_item(),
        total=2500,
        oldest_date="2021-05-01T00:00:00Z",
        commits_by_year={2021: [_commit("x")], 2022: [_commit("y")]},
        recent=[_commit("y")],
        pulls={"x": _merged(1), "y": _merged(2)},
    )

    row = sr.evaluate_repo(
        client,
        "acme/tool",
        since="2022-01-01T00:00:00Z",
        sample_size=30,
        history_per_year=5,
        current_year=2022,
    )

    assert row.selected is True
    assert row.exclude_reason == ""
    assert row.recent_only is False
    assert row.mining_since_year == 2021
    assert row.window_commits == 2500
    assert client.since_calls == []  # 전체 구간이면 구간 커밋 수를 따로 세지 않는다
    assert row.window_pr_ratio == 1.0
    assert row.score_v2 == sr.score_candidate(1.0, 0.0, 2500)


def test_year_sample_skips_merge_and_bot_commits():
    client = FakeHistoryClient(
        _repo_item(),
        total=3000,
        oldest_date="2018-01-01T00:00:00Z",
        commits_by_year={2018: [_commit("m", parents=2), _commit("bot", bot=True), _commit("h")]},
        recent=[_commit("h")],
        pulls={"h": _merged(1)},
    )

    row = sr.evaluate_repo(
        client,
        "acme/tool",
        since="2018-01-01T00:00:00Z",
        sample_size=30,
        history_per_year=5,
        current_year=2018,
    )

    assert row.history_sampled == 1
    assert row.pr_by_year == "2018:1/1"


def test_history_is_off_by_default():
    client = FakeHistoryClient(_repo_item(), recent=[_commit("r")], pulls={"r": _merged(1)})
    row = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)
    assert row.score_v2 is None
    assert row.first_commit_year is None
    assert row.to_csv_row()["score_v2"] == ""
    assert row.to_csv_row()["recent_only"] == ""


def test_license_override_lets_manually_checked_repo_through():
    item = _repo_item(license={"spdx_id": "NOASSERTION"})
    client = FakeHistoryClient(item, recent=[_commit("r")], pulls={"r": _merged(1)})

    blocked = sr.evaluate_repo(client, "acme/tool", since="2026-01-01T00:00:00Z", sample_size=30)
    assert blocked.exclude_reason == "LICENSE_NOT_PERMISSIVE:NOASSERTION"

    row = sr.evaluate_repo(
        client,
        "acme/tool",
        since="2026-01-01T00:00:00Z",
        sample_size=30,
        license_override="BSD-3-Clause",
    )
    assert row.selected is True
    assert row.license_id == "BSD-3-Clause"
    assert row.to_csv_row()["license_manual"] == "true"


def test_main_rejects_malformed_license_override_without_network(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    exit_code = sr.main(
        [
            "--env-file",
            str(tmp_path / "missing.env"),
            "--out",
            str(tmp_path / "out.csv"),
            "--license-override",
            "celery",
        ]
    )
    assert exit_code == 2
    assert not (tmp_path / "out.csv").exists()


# --------------------------------------------------------------------------------------
# 일시적 서버 오류 재시도 (재평가 중 /commits/{sha}/pulls 간헐 500)
# --------------------------------------------------------------------------------------


def _server_error(code):
    return urllib.error.HTTPError(
        "https://api.github.com/x", code, "err", email.message.Message(), None
    )


def test_server_error_is_retried_then_succeeds(tmp_path: Path):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        if len(calls) == 1:
            raise _server_error(500)
        return 200, {}, b'[{"number": 1}]'

    slept = []
    client = sr.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=slept.append)

    assert client.get_json("/repos/a/b/commits/x/pulls") == [{"number": 1}]
    assert len(calls) == 2
    assert slept == [1]


def test_persistent_server_error_raises_after_three_retries(tmp_path: Path):
    def fetch(url, headers):
        raise _server_error(502)

    slept = []
    client = sr.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=slept.append)

    with pytest.raises(RuntimeError, match="HTTP 502"):
        client.get("/repos/a/b")
    assert slept == [1, 2, 4]


def test_server_error_is_not_cached(tmp_path: Path):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        raise _server_error(503)

    client = sr.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=lambda seconds: None)
    with pytest.raises(RuntimeError):
        client.get("/repos/a/b")
    with pytest.raises(RuntimeError):
        client.get("/repos/a/b")
    assert len(calls) == 8  # 두 번 모두 네트워크를 탔다 (캐시에 남지 않는다)
