"""맥락 결합 테스트 (이슈 #6).

네트워크를 타지 않는다. 참조 파싱은 순수 함수로, API 경로는 라우팅 가짜 fetcher 로 본다.
"""

import email.message
import json
import urllib.error
import urllib.parse

import pytest

from pipeline import context as ctx
from pipeline import extract as extract_module
from tests.test_extract import _REPO, _commit_all, _init_repo, _last_commit_pair, _write

# --------------------------------------------------------------------------------------
# 이슈 참조 파싱 (§4.2 맥락 결합 — "#번호, fixes #, closes #")
# --------------------------------------------------------------------------------------


def numbers(refs):
    return tuple(ref.number for ref in refs)


@pytest.mark.parametrize(
    "text",
    [
        "fix crash\n\nFixes #12",
        "fix crash\n\ncloses #12",
        "fix crash\n\nResolved #12",
        "fix crash\n\nfixes: #12",
        "fix crash\n\nCLOSE #12",
    ],
)
def test_closing_keywords_are_marked_closing(text):
    refs = ctx.extract_issue_references(text, "a/b")
    assert refs == (ctx.IssueRef(12, True),)


def test_plain_reference_is_not_closing():
    assert ctx.extract_issue_references("see #12 for context", "a/b") == (ctx.IssueRef(12, False),)


def test_closing_refs_come_before_plain_refs():
    refs = ctx.extract_issue_references("refs #99\n\nFixes #12", "a/b")
    assert refs == (ctx.IssueRef(12, True), ctx.IssueRef(99, False))


def test_same_repo_cross_reference_is_kept():
    refs = ctx.extract_issue_references("dup of psf/requests#77", "psf/requests")
    assert numbers(refs) == (77,)


def test_other_repo_cross_reference_is_dropped():
    # django/django#1 은 우리 저장소 이슈가 아니다. 번호만 떼어 #1 로 세면 안 된다.
    assert ctx.extract_issue_references("see django/django#1", "psf/requests") == ()


def test_issue_url_is_parsed():
    text = "context: https://github.com/psf/requests/issues/6432"
    assert numbers(ctx.extract_issue_references(text, "psf/requests")) == (6432,)


def test_issue_url_of_other_repo_is_dropped():
    text = "see https://github.com/django/django/issues/1"
    assert ctx.extract_issue_references(text, "psf/requests") == ()


@pytest.mark.parametrize(
    "text",
    ["follow-up to GH-66165", "see GH#66165", "closes GH 66165", "gh-66165 regression"],
)
def test_gh_prefixed_references_are_parsed(text):
    """pandas 는 `#숫자` 대신 `GH-숫자` 만 쓴다 (실제 PR 3건에서 확인)."""
    assert numbers(ctx.extract_issue_references(text, "pandas-dev/pandas")) == (66165,)


@pytest.mark.parametrize(
    "text",
    [
        "Fixes GH-12",
        "closes GH#12",
        "Resolved a/b#12",
        "Fixes https://github.com/a/b/issues/12",
        "fixes: #12",
    ],
)
def test_closing_keyword_is_detected_in_every_reference_format(text):
    """`#12` 외의 형식에도 닫기 키워드를 붙여야 한다.

    안 붙이면 정렬에서 뒤로 밀려 max_issues 에 잘린다. pandas 가 `closes GH-12345` 를 쓴다.
    """
    assert ctx.extract_issue_references(text, "a/b") == (ctx.IssueRef(12, True),)


def test_qualified_reference_without_closing_keyword_stays_plain():
    assert ctx.extract_issue_references("see GH-12 and a/b#13", "a/b") == (
        ctx.IssueRef(12, False),
        ctx.IssueRef(13, False),
    )


def test_gh_prefix_does_not_match_words_containing_gh():
    assert ctx.extract_issue_references("high 5 through 3 nightly", "a/b") == ()


def test_real_pandas_pr_body_yields_issue_reference():
    """실제 pandas PR #68480 본문. `#숫자` 가 없고 GH- 만 있는 형태."""
    body = (
        "`HDFStore.append` accepted a mismatched index.\n\n"
        "3.1.0 is unreleased, so its existing entry covers this — as with GH-66165, "
        "the earlier follow-up to the same PR.\n"
    )
    assert numbers(ctx.extract_issue_references(body, "pandas-dev/pandas", [68480])) == (66165,)


def test_references_inside_code_blocks_are_ignored():
    text = "cleanup\n\n```python\nx = 1  # 1234 rows\n```\nFixes #12"
    assert numbers(ctx.extract_issue_references(text, "a/b")) == (12,)


def test_references_inside_inline_code_are_ignored():
    assert ctx.extract_issue_references("use `#404` literal", "a/b") == ()


def test_merge_commit_pr_number_is_not_an_issue():
    message = "Merge pull request #4321 from user/branch"
    assert ctx.extract_issue_references(message, "a/b", strip_markers=True) == ()


def test_squash_suffix_pr_number_is_not_an_issue():
    refs = ctx.extract_issue_references("feat: add adapter (#5555)", "a/b", strip_markers=True)
    assert refs == ()


def test_pr_title_keeps_parenthesised_reference():
    """마커 제거는 커밋 메시지 전용. PR 제목에 걸면 `(#42)` 참조가 통째로 날아간다."""
    assert numbers(ctx.extract_issue_references("Follow-up to (#42)", "a/b")) == (42,)


def test_collect_finds_reference_in_pr_title_with_parentheses(tmp_path):
    routes = {
        "/repos/a/b/commits/sha1/pulls": [
            {
                "number": 100,
                "title": "Follow-up to (#42)",
                "body": "",
                "merged_at": "2026-01-02T00:00:00Z",
            }
        ],
        "/repos/a/b/issues/42": {"number": 42, "title": "original bug", "body": ""},
        "/repos/a/b/pulls/100/comments": [],
    }
    result = make_collector(tmp_path, routes).collect("a/b", "sha1", commit_message="msg")

    assert result.issue_numbers == (42,)


def test_exclude_drops_pr_own_number():
    refs = ctx.extract_issue_references("fixes #10 and refs #11", "a/b", exclude=[10])
    assert numbers(refs) == (11,)


def test_empty_text_returns_nothing():
    assert ctx.extract_issue_references(None, "a/b") == ()
    assert ctx.extract_issue_references("", "a/b") == ()


def test_merge_refs_prefers_closing_over_plain():
    merged = ctx.merge_refs([ctx.IssueRef(5, False)], [ctx.IssueRef(5, True)])
    assert merged == (ctx.IssueRef(5, True),)


def test_merge_refs_orders_closing_first_then_number():
    merged = ctx.merge_refs(
        [ctx.IssueRef(9, False), ctx.IssueRef(3, False)], [ctx.IssueRef(8, True)]
    )
    assert merged == (ctx.IssueRef(8, True), ctx.IssueRef(3, False), ctx.IssueRef(9, False))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Merge pull request #123 from user/x", 123),
        ("feat: thing (#456)", 456),
        ("fix: plain commit", None),
        ("fix: refs #12 in body\n\nFixes #12", None),
    ],
)
def test_pr_number_from_message(message, expected):
    assert ctx.pr_number_from_message(message) == expected


# --------------------------------------------------------------------------------------
# 가짜 GitHub (라우팅 fetcher)
# --------------------------------------------------------------------------------------


def _http_error(code):
    return urllib.error.HTTPError(
        "https://api.github.com/x", code, "err", email.message.Message(), None
    )


def route_fetcher(routes, calls=None):
    """경로 → 응답 본문. 쿼리스트링은 무시한다 (paginate 가 per_page/page 를 붙인다)."""

    def fetch(url, request_headers):
        if calls is not None:
            calls.append(url)
        path = url.removeprefix("https://api.github.com").split("?")[0]
        if path not in routes:
            raise _http_error(404)
        payload = json.dumps(routes[path]).encode("utf-8")
        return 200, {"x-ratelimit-remaining": "4999"}, payload

    return fetch


def make_collector(tmp_path, routes, calls=None, **kwargs):
    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=route_fetcher(routes, calls))
    return ctx.ContextCollector(client, **kwargs)


MERGED_PR = {
    "number": 100,
    "title": "Remove retry helper (#100)",
    "body": "The hand-rolled backoff raced on reconnect. Fixes #42\nsee also #43",
    "merged_at": "2026-01-02T00:00:00Z",
    "labels": [{"name": "bug"}, {"name": "type: cleanup"}],
}

FULL_ROUTES = {
    "/repos/a/b/commits/sha1/pulls": [MERGED_PR],
    "/repos/a/b/issues/42": {"number": 42, "title": "Retry storm on reconnect", "body": "detail"},
    "/repos/a/b/issues/43": {"number": 43, "title": "Flaky test", "body": "detail43"},
    "/repos/a/b/pulls/100/comments": [
        {
            "body": "why not use urllib3 Retry?",
            "path": "src/net.py",
            "line": 12,
            "user": {"login": "rev"},
        },
        {"body": "unrelated nit", "path": "docs/x.md", "line": 3, "user": {"login": "rev2"}},
    ],
}


# --------------------------------------------------------------------------------------
# 수집 (§4.4 context 매핑)
# --------------------------------------------------------------------------------------


def test_collect_attaches_pr_issue_and_review(tmp_path):
    collector = make_collector(tmp_path, FULL_ROUTES)
    result = collector.collect("a/b", "sha1", commit_message="remove retry helper")

    assert result.pr_number == 100
    assert result.pr_title == MERGED_PR["title"]
    assert result.issue_numbers == (42, 43)
    assert result.issue_titles == ("Retry storm on reconnect", "Flaky test")
    assert result.has_any_context is True
    # 닫기 키워드로 참조된 42 가 앞
    assert result.issue_refs[0] == ctx.IssueRef(42, True)


def test_schema_context_has_exactly_charter_fields(tmp_path):
    """§4.4 `context` 필드와 이름·개수가 정확히 일치해야 한다 (§13 — 임의 확장 금지)."""
    collector = make_collector(tmp_path, FULL_ROUTES)
    schema = collector.collect("a/b", "sha1", commit_message="msg").to_schema_context()

    assert set(schema) == {
        "commit_message",
        "pr_number",
        "pr_title",
        "pr_body",
        "pr_labels",
        "issue_numbers",
        "issue_titles",
        "issue_bodies",
        "review_comments",
    }
    assert schema["issue_numbers"] == [42, 43]
    assert [comment["body"] for comment in schema["review_comments"]] == [
        "why not use urllib3 Retry?",
        "unrelated nit",
    ]


def test_labels_and_issue_bodies_reach_the_schema(tmp_path):
    """ADR-018 로 §4.4 에 들어왔다 (#24). 그전에는 모으기만 하고 버렸다."""
    collector = make_collector(tmp_path, FULL_ROUTES)
    result = collector.collect("a/b", "sha1", commit_message="msg")

    assert result.pr_labels == ("bug", "type: cleanup")
    assert result.issue_bodies == ("detail", "detail43")
    assert result.to_schema_context()["pr_labels"] == ["bug", "type: cleanup"]
    assert result.to_schema_context()["issue_bodies"] == ["detail", "detail43"]


def test_pr_number_referenced_in_body_is_not_counted_as_issue(tmp_path):
    """`/issues/{n}` 는 PR 도 돌려준다. `pull_request` 키가 있으면 이슈가 아니다."""
    routes = {
        "/repos/a/b/commits/sha1/pulls": [
            {
                "number": 100,
                "title": "t",
                "body": "supersedes #77",
                "merged_at": "2026-01-02T00:00:00Z",
            }
        ],
        "/repos/a/b/issues/77": {
            "number": 77,
            "title": "a pull request",
            "pull_request": {"url": "x"},
        },
        "/repos/a/b/pulls/100/comments": [],
    }
    result = make_collector(tmp_path, routes).collect("a/b", "sha1", commit_message="msg")

    assert result.issue_numbers == ()
    assert result.issue_titles == ()


def test_unresolvable_issue_number_is_dropped(tmp_path):
    """없는 번호(404)는 issue_numbers 에 남기지 않는다 — titles 와 길이가 어긋나면 안 된다."""
    routes = {
        "/repos/a/b/commits/sha1/pulls": [],
        "/repos/a/b/issues/42": {"number": 42, "title": "real", "body": ""},
    }
    result = make_collector(tmp_path, routes).collect(
        "a/b", "sha1", commit_message="Fixes #42 and refs #999"
    )

    assert result.issue_numbers == (42,)
    assert len(result.issue_numbers) == len(result.issue_titles)


def test_pr_is_found_from_commit_message_when_pulls_endpoint_is_empty(tmp_path):
    """스쿼시 머지로 SHA 가 바뀌면 `/pulls` 가 빈다. 메시지 마커가 폴백."""
    routes = {
        "/repos/a/b/commits/sha1/pulls": [],
        "/repos/a/b/pulls/321": {
            "number": 321,
            "title": "drop legacy parser",
            "body": "Fixes #9",
            "merged_at": "2026-01-02T00:00:00Z",
        },
        "/repos/a/b/issues/9": {"number": 9, "title": "legacy parser breaks", "body": ""},
        "/repos/a/b/pulls/321/comments": [],
    }
    result = make_collector(tmp_path, routes).collect(
        "a/b", "sha1", commit_message="drop legacy parser (#321)"
    )

    assert result.pr_number == 321
    assert result.issue_numbers == (9,)


def test_open_pull_request_is_ignored(tmp_path):
    """머지되지 않은 PR 은 이 삭제가 들어온 경로가 아니다."""
    routes = {"/repos/a/b/commits/sha1/pulls": [{"number": 5, "title": "wip", "merged_at": None}]}
    result = make_collector(tmp_path, routes).collect("a/b", "sha1", commit_message="msg")

    assert result.pr_number is None
    assert result.has_pr is False


def test_earliest_merged_pr_wins_over_backport(tmp_path):
    routes = {
        "/repos/a/b/commits/sha1/pulls": [
            {"number": 200, "title": "backport", "merged_at": "2026-03-01T00:00:00Z"},
            {"number": 100, "title": "original", "merged_at": "2026-01-01T00:00:00Z"},
        ],
        "/repos/a/b/pulls/100/comments": [],
    }
    result = make_collector(tmp_path, routes).collect("a/b", "sha1", commit_message="msg")

    assert result.pr_number == 100


def test_commit_message_is_fetched_when_not_given(tmp_path):
    routes = {
        "/repos/a/b/commits/sha1": {"commit": {"message": "remove dead branch\n\nFixes #42"}},
        "/repos/a/b/commits/sha1/pulls": [],
        "/repos/a/b/issues/42": {"number": 42, "title": "dead branch", "body": ""},
    }
    result = make_collector(tmp_path, routes).collect("a/b", "sha1")

    assert result.commit_message.startswith("remove dead branch")
    assert result.issue_numbers == (42,)


def test_missing_commit_yields_empty_context(tmp_path):
    result = make_collector(tmp_path, {}).collect("a/b", "nope")

    assert result.commit_message == ""
    assert result.has_any_context is False
    assert result.to_schema_context()["pr_number"] is None


def test_max_issues_caps_api_calls(tmp_path):
    routes = {
        "/repos/a/b/commits/sha1/pulls": [],
        **{f"/repos/a/b/issues/{n}": {"number": n, "title": f"t{n}"} for n in range(1, 10)},
    }
    collector = make_collector(tmp_path, routes, max_issues=2)
    message = "refs " + ", ".join(f"#{n}" for n in range(1, 10))
    result = collector.collect("a/b", "sha1", commit_message=message)

    assert len(result.issue_numbers) == 2


# --------------------------------------------------------------------------------------
# 리뷰 코멘트 (§4.2 "해당 파일·라인 근처")
# --------------------------------------------------------------------------------------


def test_review_comments_are_filtered_to_the_deleted_file(tmp_path):
    collector = make_collector(tmp_path, FULL_ROUTES)
    result = collector.collect("a/b", "sha1", file_path="src/net.py", commit_message="msg")

    assert [comment.path for comment in result.review_comments] == ["src/net.py"]
    assert [c["body"] for c in result.to_schema_context()["review_comments"]] == [
        "why not use urllib3 Retry?"
    ]


def test_review_comments_fall_back_to_whole_pr_when_file_has_none(tmp_path):
    collector = make_collector(tmp_path, FULL_ROUTES)
    result = collector.collect("a/b", "sha1", file_path="src/absent.py", commit_message="msg")

    assert len(result.review_comments) == 2  # 빈손보다 PR 전체가 낫다


def test_target_file_comment_on_second_page_is_found(tmp_path):
    """상한을 파일 필터 **전**에 걸면 2페이지의 대상 파일 코멘트를 영영 못 본다.

    paginate 는 max_items 를 채우면 다음 페이지를 요청하지 않는다. 예전처럼 max_items 를
    50 으로 주면 첫 페이지 50건에서 끊기고, 폴백이 무관한 50건을 통째로 돌려줬다.
    """
    page1 = [{"body": f"noise {i}", "path": "other.py"} for i in range(100)]
    page2 = [{"body": "왜 이 함수를 지웠나", "path": "src/net.py", "line": 7}]

    def fetch(url, request_headers):
        if "/pulls/100/comments" in url:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            body = {1: page1, 2: page2}.get(int(query.get("page", ["1"])[0]), [])
            return 200, {"x-ratelimit-remaining": "4999"}, json.dumps(body).encode()
        return route_fetcher(FULL_ROUTES)(url, request_headers)

    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=fetch)
    result = ctx.ContextCollector(client).collect(
        "a/b", "sha1", file_path="src/net.py", commit_message="msg"
    )

    assert [comment.body for comment in result.review_comments] == ["왜 이 함수를 지웠나"]


def test_review_comments_are_capped_after_filtering(tmp_path):
    many = [{"body": f"c{i}", "path": "src/net.py"} for i in range(120)]
    routes = {
        "/repos/a/b/commits/sha1/pulls": [
            {"number": 100, "title": "t", "body": "", "merged_at": "2026-01-02T00:00:00Z"}
        ],
        "/repos/a/b/pulls/100/comments": many,
    }
    result = make_collector(tmp_path, routes).collect(
        "a/b", "sha1", file_path="src/net.py", commit_message="msg"
    )

    assert len(result.review_comments) == ctx.MAX_REVIEW_COMMENTS


def test_blank_review_comments_are_dropped(tmp_path):
    routes = {
        "/repos/a/b/commits/sha1/pulls": [
            {"number": 100, "title": "t", "body": "", "merged_at": "2026-01-02T00:00:00Z"}
        ],
        "/repos/a/b/pulls/100/comments": [
            {"body": "   ", "path": "src/net.py"},
            {"body": "real", "path": "src/net.py"},
        ],
    }
    result = make_collector(tmp_path, routes).collect("a/b", "sha1", commit_message="msg")

    assert [comment.body for comment in result.review_comments] == ["real"]


# --------------------------------------------------------------------------------------
# 캐시 (완료 조건: 같은 입력 재실행이면 API 0회)
# --------------------------------------------------------------------------------------


def test_rerun_with_same_input_makes_no_api_calls(tmp_path):
    calls = []
    first = make_collector(tmp_path, FULL_ROUTES, calls)
    first.collect("a/b", "sha1", commit_message="msg")
    first_call_count = len(calls)
    assert first_call_count > 0

    # 새 프로세스를 흉내 내 같은 캐시 디렉터리로 다시 만든다
    second = make_collector(tmp_path, FULL_ROUTES, calls)
    second.collect("a/b", "sha1", commit_message="msg")

    assert len(calls) == first_call_count
    assert second.client.api_calls == 0
    assert second.client.cache_hits > 0


# --------------------------------------------------------------------------------------
# 입력·리포트
# --------------------------------------------------------------------------------------


def test_parse_targets_reads_pipeline_output_keys():
    lines = [
        json.dumps({"repo": "a/b", "commit_sha": "s1", "file_path": "x.py", "deleted_hunk": "..."}),
        "",
        json.dumps({"repo": "a/b", "commit_sha": "s2"}),
        json.dumps({"repo": "a/b"}),  # commit_sha 없음 → 버린다
    ]
    targets = ctx.parse_targets(lines)

    assert [target.commit_sha for target in targets] == ["s1", "s2"]
    assert targets[0].file_path == "x.py"


def test_sample_targets_skips_merge_and_bot_commits(tmp_path):
    routes = {
        "/repos/a/b/commits": [
            {
                "sha": "m1",
                "parents": [{"sha": "p1"}, {"sha": "p2"}],
                "commit": {"message": "merge"},
            },
            {
                "sha": "b1",
                "parents": [{"sha": "p"}],
                "author": {"type": "Bot"},
                "commit": {"message": "bump"},
            },
            {"sha": "h1", "parents": [{"sha": "p"}], "commit": {"message": "human"}},
        ]
    }
    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=route_fetcher(routes))
    targets = ctx.sample_targets(client, "a/b", 5)

    assert [target.commit_sha for target in targets] == ["h1"]


def test_attachment_report_counts_and_rates(tmp_path):
    collector = make_collector(tmp_path, FULL_ROUTES)
    targets = [ctx.CommitTarget("a/b", "sha1", commit_message="msg")]
    contexts, report = ctx.run_targets(collector, targets)

    assert len(contexts) == 1
    data = report.as_dict()
    assert data["total"] == 1
    assert data["with_pr"] == data["with_issue"] == data["with_review"] == 1
    assert data["any_rate"] == 1.0
    assert report.format_lines()[0] == "표본 1건"


def test_attachment_report_handles_empty_sample():
    assert ctx.AttachmentReport().as_dict()["any_rate"] == 0.0


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP 403: https://api.github.com/x", True),
        ("HTTP 429: https://api.github.com/x", True),
        ("HTTP 404: https://api.github.com/x", False),
        ("네트워크 오류: https://api.github.com/x", False),
    ],
)
def test_rate_limit_error_is_recognised_by_status(message, expected):
    """GitHubClient 의 메시지 포맷이 바뀌면 이 테스트가 먼저 깨져야 한다."""
    assert ctx.is_rate_limit_error(RuntimeError(message)) is expected


def test_rate_limit_stops_the_batch_instead_of_recording_empty_contexts(tmp_path):
    """한도 소진을 개별 실패로 묻으면 남은 커밋이 전부 "맥락 없음"으로 기록된다.

    그 JSONL 은 멀쩡한 결과처럼 보이고 이유 회수율이 실제보다 낮게 나온다.
    """
    seen = []

    def fetch(url, request_headers):
        if "sha_boom" in url:
            raise _http_error(403)
        seen.append(url)
        return route_fetcher(FULL_ROUTES)(url, request_headers)

    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=lambda _: None)
    collector = ctx.ContextCollector(client)
    targets = [
        ctx.CommitTarget("a/b", "sha1", commit_message="msg"),
        ctx.CommitTarget("a/b", "sha_boom", commit_message="msg"),
        ctx.CommitTarget("a/b", "sha1", commit_message="msg"),
    ]
    contexts, report = ctx.run_targets(collector, targets)

    assert len(contexts) == 1  # 한도에 걸린 건과 그 뒤는 기록하지 않는다
    assert report.total == 1
    assert "한도 소진" in report.stopped_reason
    assert "2건 남기고" in report.stopped_reason
    assert any("중단" in line for line in report.format_lines())


# --------------------------------------------------------------------------------------
# 부분 실패 허용 (#50) — 조회 하나가 죽어도 나머지 맥락은 남는다
# --------------------------------------------------------------------------------------


def _fetcher_failing_on(fragment, code):
    """`fragment` 가 든 URL 만 실패시키고 나머지는 FULL_ROUTES 대로."""

    def fetch(url, request_headers):
        if fragment in url:
            raise _http_error(code)
        return route_fetcher(FULL_ROUTES)(url, request_headers)

    return fetch


@pytest.mark.parametrize("code", [410, 451, 500, 502])
def test_issue_failure_keeps_the_pull_request(tmp_path, code):
    """삭제된 이슈는 404 가 아니라 410 이다. 그 한 건 때문에 PR 을 잃으면 안 된다.

    실측에서 httpx 표본 50건 중 21건(42%)이 이 경로로 날아갔다 (#47).
    """
    client = ctx.GitHubClient(
        "t",
        tmp_path / "cache",
        fetcher=_fetcher_failing_on("/issues/42", code),
        sleep=lambda _: None,
    )
    collector = ctx.ContextCollector(client)
    result = collector.collect("a/b", "sha1", commit_message="remove retry helper")

    assert result.pr_number == 100
    assert result.pr_title == MERGED_PR["title"]
    assert result.has_any_context is True
    assert collector.skipped["issue"] >= 1


def test_issue_failure_still_collects_other_issues(tmp_path):
    """한 이슈가 죽어도 같은 커밋의 다른 이슈는 살린다."""
    client = ctx.GitHubClient(
        "t",
        tmp_path / "cache",
        fetcher=_fetcher_failing_on("/issues/42", 410),
        sleep=lambda _: None,
    )
    result = ctx.ContextCollector(client).collect("a/b", "sha1", commit_message="msg")

    assert result.issue_numbers == (43,)
    assert result.issue_titles == ("Flaky test",)


def test_review_comment_failure_keeps_pr_and_issues(tmp_path):
    client = ctx.GitHubClient(
        "t",
        tmp_path / "cache",
        fetcher=_fetcher_failing_on("/pulls/100/comments", 500),
        sleep=lambda _: None,
    )
    collector = ctx.ContextCollector(client)
    result = collector.collect("a/b", "sha1", commit_message="msg")

    assert result.pr_number == 100
    assert result.issue_numbers == (42, 43)
    assert result.review_comments == ()
    assert collector.skipped["review"] >= 1


def test_commit_message_failure_still_finds_the_pr(tmp_path):
    def fetch(url, request_headers):
        # 커밋 단건 조회만 실패시킨다 (`/commits/sha1/pulls` 는 살려 둔다)
        if url.endswith("/commits/sha1"):
            raise _http_error(500)
        return route_fetcher(FULL_ROUTES)(url, request_headers)

    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=lambda _: None)
    collector = ctx.ContextCollector(client)
    result = collector.collect("a/b", "sha1")  # commit_message 를 주지 않아 조회한다

    assert result.commit_message == ""
    assert result.pr_number == 100
    assert collector.skipped["commit"] >= 1


def test_rate_limit_still_stops_even_though_failures_are_tolerated(tmp_path):
    """한도 소진까지 삼키면 남은 커밋이 전부 "맥락 없음"이 된다 (#33 보호 유지)."""
    client = ctx.GitHubClient(
        "t",
        tmp_path / "cache",
        fetcher=_fetcher_failing_on("/issues/42", 403),
        sleep=lambda _: None,
    )
    collector = ctx.ContextCollector(client)
    contexts, report = ctx.run_targets(
        collector,
        [
            ctx.CommitTarget("a/b", "sha1", commit_message="msg"),
            ctx.CommitTarget("a/b", "sha1", commit_message="msg"),
        ],
    )

    assert contexts == []
    assert "한도 소진" in report.stopped_reason
    assert "issue" not in collector.skipped  # 삼키지 않고 올려보냈다


def test_skipped_counts_reach_the_report(tmp_path):
    """조용히 삼키면 무엇을 잃었는지 모른다 — 리포트에 보여야 한다."""
    client = ctx.GitHubClient(
        "t",
        tmp_path / "cache",
        fetcher=_fetcher_failing_on("/issues/42", 410),
        sleep=lambda _: None,
    )
    _, report = ctx.run_targets(
        ctx.ContextCollector(client), [ctx.CommitTarget("a/b", "sha1", commit_message="msg")]
    )

    assert report.as_dict()["skipped"]["issue"] >= 1
    assert any("건너뛴 조회" in line for line in report.format_lines())


def test_network_error_is_still_isolated_per_commit(tmp_path):
    """네트워크 오류는 배치를 멈추지 않는다 (한도 소진과 구분)."""

    def fetch(url, request_headers):
        if "boom" in url:
            raise urllib.error.URLError("network down")
        return route_fetcher(FULL_ROUTES)(url, request_headers)

    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=lambda _: None)
    contexts, report = ctx.run_targets(
        ctx.ContextCollector(client),
        [
            ctx.CommitTarget("a/b", "boom", commit_message="msg"),
            ctx.CommitTarget("a/b", "sha1", commit_message="msg"),
        ],
    )

    assert len(contexts) == 2
    assert report.stopped_reason == ""


def test_output_record_keeps_original_pipeline_fields():
    """#5 레코드를 그대로 두고 context 만 얹는다. 버리면 §4.4 레코드를 못 만든다."""
    target = ctx.CommitTarget(
        repo="a/b",
        commit_sha="s1",
        file_path="src/net.py",
        record={"repo": "a/b", "commit_sha": "s1", "file_path": "src/net.py", "deleted_hunk": "-x"},
    )
    context = ctx.CommitContext(repo="a/b", commit_sha="s1", commit_message="msg", pr_number=7)
    record = ctx.build_output_record(target, context)

    assert record["deleted_hunk"] == "-x"
    assert record["file_path"] == "src/net.py"
    assert record["context"]["pr_number"] == 7


def test_output_record_without_original_still_has_identity():
    target = ctx.CommitTarget(repo="a/b", commit_sha="s1")
    context = ctx.CommitContext(repo="a/b", commit_sha="s1")
    record = ctx.build_output_record(target, context)

    assert record["repo"] == "a/b"
    assert record["commit_sha"] == "s1"
    assert "context" in record


def test_parse_targets_keeps_the_whole_record():
    line = json.dumps({"repo": "a/b", "commit_sha": "s1", "deleted_hunk": "-x", "extra": 1})
    target = ctx.parse_targets([line])[0]

    assert target.record["deleted_hunk"] == "-x"
    assert target.record["extra"] == 1


def test_one_failing_commit_does_not_abort_the_batch(tmp_path):
    """한 건이 터져도 나머지를 잃지 않는다 (한도를 다시 쓰게 되므로)."""

    def fetch(url, request_headers):
        if "boom" in url:
            raise urllib.error.URLError("network down")
        return route_fetcher(FULL_ROUTES)(url, request_headers)

    client = ctx.GitHubClient("t", tmp_path / "cache", fetcher=fetch, sleep=lambda _: None)
    collector = ctx.ContextCollector(client)
    targets = [
        ctx.CommitTarget("a/b", "boom", commit_message="msg"),
        ctx.CommitTarget("a/b", "sha1", commit_message="msg"),
    ]
    contexts, report = ctx.run_targets(collector, targets)

    assert len(contexts) == 2
    assert contexts[0].has_any_context is False
    assert contexts[1].pr_number == 100
    assert report.as_dict()["any_rate"] == 0.5


# --------------------------------------------------------------------------------------
# 대체 코드 매칭 (§4.4 replacement, Issue #67)
# --------------------------------------------------------------------------------------

DELETED_PARSE = "def parse(raw):\n    return raw.split(',')\n"


def _record(**overrides):
    record = {
        "function_name": "parse",
        "deleted_body": DELETED_PARSE,
        "added_hunk_same_file": "",
    }
    record.update(overrides)
    return record


def _hunks(*bodies):
    """#102 형식의 헝크 목록. 좌표는 이 테스트들이 보지 않으므로 형태만 맞춘다."""
    return [
        {
            "old_start": 10 * index + 1,
            "old_count": 0,
            "new_start": 10 * index + 1,
            "new_count": len(body.splitlines()),
            "added_body": body,
        }
        for index, body in enumerate(bodies)
    ]


def test_no_added_lines_is_a_positive_none_verdict():
    """추가 줄이 0개면 대체가 없다고 단정할 수 있다 — 판정 불가와 다르다."""
    result = ctx.match_replacement(_record(added_hunk_same_file=""))

    assert result.match_method == ctx.MATCH_NONE
    assert result.code is None


def test_missing_field_is_undetermined_not_none():
    """필드 자체가 없으면 "모른다"다. `NONE`으로 적으면 없는 근거가 생긴다 (가이드 §6.2.3)."""
    record = _record()
    del record["added_hunk_same_file"]

    assert ctx.match_replacement(record) == ctx.UNDETERMINED


def test_same_name_addition_is_a_same_location_replacement():
    added = "def parse(raw):\n    return [p.strip() for p in raw.split(',')]\nPARSERS = [parse]\n"
    record = _record(start_line=1, end_line=2, added_hunks_same_file=_hunks(added))
    result = ctx.match_replacement(record)

    assert result.match_method == ctx.MATCH_SAME_LOCATION
    assert result.confidence == ctx.SAME_NAME_CONFIDENCE
    assert "p.strip()" in result.code


def test_addition_under_another_name_is_left_undetermined():
    """이름이 다른 후보를 고르려면 위치가 필요하다. 억지로 고르면 없는 근거가 생긴다."""
    added = "def parse_all(raw):\n    return [p.strip() for p in raw.split(',')]\n"

    assert ctx.match_replacement(_record(added_hunks_same_file=_hunks(added))) == ctx.UNDETERMINED


def test_partial_edits_without_a_whole_function_are_undetermined():
    """흩어진 수정만 있으면 완결된 함수가 안 나온다 — 대체 코드 후보가 아니다."""
    added = "    raw = raw.strip()\n        return None\n"

    assert ctx.match_replacement(_record(added_hunks_same_file=_hunks(added))) == ctx.UNDETERMINED


def test_several_same_name_additions_pick_the_closest_body_with_lower_confidence():
    """한 파일에 같은 이름이 여럿 추가될 수 있다 (`__init__` 등). 본문이 가까운 쪽을 고른다."""
    hunks = _hunks(
        "def parse(raw):\n    raise NotImplementedError\nA = 1\n",
        "def parse(raw):\n    return raw.split(',')\nB = 2\n",
    )
    result = ctx.match_replacement(_record(added_hunks_same_file=hunks))

    assert result.match_method == ctx.MATCH_SAME_LOCATION
    assert result.confidence == ctx.AMBIGUOUS_NAME_CONFIDENCE
    assert "raise NotImplementedError" not in result.code


def test_candidates_never_span_two_hunks():
    """서로 떨어진 헝크의 조각을 이어 붙이면 존재한 적 없는 함수가 만들어진다.

    예비 200건에서 평탄한 문자열을 파싱했을 때 35건 중 5건(14%)이 이 형태였다.
    아래는 그중 `any_schema` 를 줄인 것이다 - 시그니처 헝크와 무관한 한 줄이 붙어
    자식 파일에 없는 코드가 `replacement.code` 로 나갔다.
    """
    hunks = _hunks(
        "def parse(raw):\n    return dict_not_none(type='any')\nSCHEMAS = [parse]\n",
        "    serialization: SerSchema\n",
    )
    result = ctx.match_replacement(_record(added_hunks_same_file=hunks))

    assert "serialization" not in result.code


TRUNCATED_HUNK = "def parse(raw, sep=','):\n    items = raw.split(sep)\n"
WHOLE_PARSE = (
    "def parse(raw, sep=','):\n    items = raw.split(sep)\n    return [i.strip() for i in items]\n"
)


def test_a_function_cut_off_by_the_hunk_does_not_become_code():
    """헝크에 추가 줄만 있어서 뒷부분이 빠진 채로도 구문상 완결돼 보일 수 있다.

        -def parse(raw):
        -    items = raw.split(',')
        +def parse(raw, sep=','):
        +    items = raw.split(sep)
             return [i.strip() for i in items]

    `return` 이 빠진 본문을 내보내면 존재한 적 없는 코드가 근거가 된다. 예비 200건에서
    39건 중 3건이 이 형태였고 `dataclasses.py::wrap` 은 시그니처 한 줄만 나갔다.
    """
    result = ctx.match_replacement(_record(added_hunks_same_file=_hunks(TRUNCATED_HUNK)))

    assert result.code is None
    # "같은 이름 함수가 추가됐다" 는 사실은 그대로라 판정은 남긴다.
    assert result.match_method == ctx.MATCH_SAME_LOCATION


def test_child_source_recovers_the_whole_function():
    """자식 파일 원문을 주면 잘린 부분까지 복원한다 - 헝크 좌표로 자리를 찾는다."""
    record = _record(start_line=1, end_line=2, added_hunks_same_file=_hunks(TRUNCATED_HUNK))
    result = ctx.match_replacement(record, child_source=WHOLE_PARSE)

    assert result.code == WHOLE_PARSE.rstrip("\n")
    assert result.confidence == ctx.SAME_NAME_CONFIDENCE


def test_child_source_without_that_function_falls_back_to_the_safe_rule():
    """원문을 줬는데 그 자리에 없으면(좌표가 안 맞으면) 채우지 않는다."""
    record = _record(added_hunks_same_file=_hunks(TRUNCATED_HUNK))

    assert ctx.match_replacement(record, child_source="x = 1\n").code is None


def _hunk_at(old_start, old_count, body):
    """삭제 자리 판정에 쓰는 부모 좌표를 직접 준 헝크."""
    return {
        "old_start": old_start,
        "old_count": old_count,
        "new_start": old_start,
        "new_count": len(body.splitlines()),
        "added_body": body,
    }


WRAP_DELETED = (
    "def wrap_val(value, handler):\n"
    "    if isinstance(value, source):\n"
    "        return value\n"
    "    return handler(value)\n"
)
# 이름만 다르고 구조가 삭제된 것과 똑같은 형제 - 정규화하면 삭제된 함수와 구별이 안 된다.
WRAP_FAR_SIBLING = (
    "def wrap_val(v, h):\n    if isinstance(v, source):\n        return v\n    return h(v)\nX = 1\n"
)
# 삭제 자리에 들어온 진짜 대체 - 구조가 조금 달라 유사도로는 밀린다.
WRAP_AT_SITE = (
    "def wrap_val(v, h):\n"
    "    if isinstance(v, other):\n"
    "        return str(v)\n"
    "    return h(v)\n"
    "Y = 2\n"
)


def test_position_beats_body_similarity_among_same_name_siblings():
    """같은 이름 형제 중 하나를 고를 때 위치가 먼저다 (#107).

    유사도는 식별자를 `VAR` 로 지운 뒤 비교해서, 형제를 가르는 이름이 같아 보인다.
    예비 200건의 `networks.py::wrap_val` 이 이 형태였다 - 유사도는 `_BaseUrl` 쪽 다른
    클래스 메서드를, 위치는 삭제 자리의 `_BaseMultiHostUrl` 쪽을 골랐다.
    """
    record = _record(
        function_name="wrap_val",
        deleted_body=WRAP_DELETED,
        start_line=421,
        end_line=424,
        added_hunks_same_file=[
            _hunk_at(257, 9, WRAP_FAR_SIBLING),
            _hunk_at(419, 6, WRAP_AT_SITE),
        ],
    )
    result = ctx.match_replacement(record)

    assert "str(v)" in result.code
    # 위치로 하나로 좁혀졌으니 모호함이 없다 - 후보가 처음부터 하나였던 것과 같다.
    assert result.confidence == ctx.SAME_NAME_CONFIDENCE


def test_several_candidates_at_the_site_still_fall_back_to_similarity():
    """삭제 자리에 같은 이름이 둘 이상 걸치면 위치로 못 가른다 - 유사도, 신뢰도 낮춤."""
    record = _record(
        function_name="wrap_val",
        deleted_body=WRAP_DELETED,
        start_line=421,
        end_line=424,
        added_hunks_same_file=[
            _hunk_at(420, 3, WRAP_FAR_SIBLING),
            _hunk_at(423, 3, WRAP_AT_SITE),
        ],
    )
    result = ctx.match_replacement(record)

    assert result.confidence == ctx.AMBIGUOUS_NAME_CONFIDENCE
    # 두 후보가 공유하는 줄(`return h(v)`)로는 어느 쪽을 골랐는지 모른다. 본문으로 가른다 -
    # 삭제된 것과 구조가 같은 먼 형제가 골라져야 한다.
    assert "isinstance(v, source)" in result.code
    assert "str(v)" not in result.code


def test_similarity_compares_tokens_not_whole_lines():
    """줄을 통째로 비교하면 한 줄짜리 스텁은 시그니처가 조금만 달라도 0 이 된다 (#107).

    예비 200건 `_decorators_v1.py::__call__` 이 이 형태였다 - 삭제된 것과 시그니처가
    같은 후보도 0.000 이라, 선택이 줄 번호 순서로 정해지고 있었다.
    """
    deleted = "def __call__(self, value, *, values) -> Any:\n    ...\n"
    same = ctx._added_functions("def __call__(self, value, *, values) -> Any: ...\n")[0]
    other = ctx._added_functions("def __call__(self, value) -> Any: ...\n")[0]

    assert ctx._similarity(deleted, same) > 0.8
    assert ctx._similarity(deleted, same) > ctx._similarity(deleted, other)


@pytest.mark.parametrize(
    ("hunk", "expected"),
    [
        ({"old_start": 118, "old_count": 24}, True),  # 제자리 교체 - 삭제 범위와 겹친다
        ({"old_start": 130, "old_count": 0}, True),  # 순수 추가 - 130 뒤에 끼워 넣었다
        ({"old_start": 300, "old_count": 5}, False),  # 딴 자리
        ({"old_count": 5}, False),  # 좌표 없음 - 모르는 것
        # ±2줄 (#111). 경계가 함수와 한두 줄 어긋난 헝크는 자리다
        ({"old_start": 143, "old_count": 1}, True),  # 끝에서 2줄 뒤
        ({"old_start": 144, "old_count": 1}, False),  # 3줄 뒤
        ({"old_start": 110, "old_count": 7}, True),  # 110-116, 시작 2줄 앞
        ({"old_start": 110, "old_count": 6}, False),  # 110-115, 3줄 앞
    ],
)
def test_deletion_site_uses_parent_coordinates(hunk, expected):
    """헝크의 옛 좌표가 삭제된 함수 범위(부모 118-141) ±2줄에 걸치는지로 판정한다 (#107, #111)."""
    assert ctx._at_deletion_site(hunk, 118, 141) is expected


ADDED_INIT = "def parse(raw):\n    return raw.split(';')\nX = 1\n"


def test_single_candidate_a_line_or_two_off_is_still_at_the_site():
    """헝크 경계가 함수와 한두 줄 어긋나도 제자리 교체다 (#111).

    예비 200건 `errors.py::__init__` 모양 - 부모 83-84, 헝크는 81 뒤에 끼워 넣었다.
    """
    record = _record(
        start_line=83, end_line=84, added_hunks_same_file=[_hunk_at(81, 0, ADDED_INIT)]
    )

    assert ctx.match_replacement(record).confidence == ctx.SAME_NAME_CONFIDENCE


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [
        (774, 780),  # 예비 200건 `mypy.py::to_var` 모양 - 헝크는 320줄, 450줄 넘게 떨어졌다
        (None, None),  # 삭제 위치를 모른다 - 자리를 확인하지 못했다
    ],
)
def test_single_candidate_not_confirmed_at_the_site_gets_lower_confidence(start_line, end_line):
    """후보가 하나여도 자리가 확인되지 않으면 0.9 가 아니다 (#111).

    버리지는 않는다 - "같은 커밋·같은 파일에 같은 이름 함수가 들어왔다"는 사실은 쓸모가 있다.
    가이드 §6.2.2 가 0.8 미만인 대체 코드만으로 근거 ① 을 0.8 이상에 넣지 않으므로, 라벨러는
    이 건에서 다른 근거로 받쳐야 한다.
    """
    record = _record(
        start_line=start_line,
        end_line=end_line,
        added_hunks_same_file=[_hunk_at(320, 0, ADDED_INIT)],
    )
    result = ctx.match_replacement(record)

    assert result.match_method == ctx.MATCH_SAME_LOCATION
    assert result.confidence == ctx.AMBIGUOUS_NAME_CONFIDENCE
    assert "split(';')" in result.code


def test_record_without_line_numbers_is_never_at_the_site():
    """옛 형식·픽스처처럼 삭제 위치가 없으면 위치를 모른다 - 유사도로 떨어진다."""
    assert ctx._at_deletion_site({"old_start": 1, "old_count": 3}, None, None) is False


def test_repo_path_recovers_a_function_the_hunk_cut_off(tmp_path):
    """실제 git 저장소로 끝까지: 추출 -> 자식 파일 원문 -> 함수 전체 (#107).

    시그니처와 첫 줄만 고친 수정이라 헝크에는 `return` 이 없다. 원문 없이는 본문을
    채우지 않고(잘린 본문을 내보내지 않는다), 클론 경로를 주면 전체를 되찾는다.
    """
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "m.py", "def parse(raw):\n    items = raw.split(',')\n    return items\n")
    _commit_all(repo, "v1")
    _write(repo, "m.py", "def parse(raw, sep=','):\n    items = raw.split(sep)\n    return items\n")
    sha = _commit_all(repo, "v2")
    deletions = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))
    record = extract_module.to_json_dict(next(r for r in deletions if r.function_name == "parse"))

    assert ctx.match_replacement(record).code is None

    source = ctx.read_child_source(repo, sha, "m.py")
    code = ctx.match_replacement(record, child_source=source).code
    assert code.startswith("def parse(raw, sep=','):")
    assert code.rstrip().endswith("return items")


def _run_main_on_cut_function(tmp_path, monkeypatch, *extra_args):
    """시그니처만 고친 커밋 하나를 추출해 `main --input --out` 으로 돌린 출력 한 줄.

    GitHub 호출은 `run_targets` 에서 막는다 - 여기서 보려는 것은 맥락 수집이 아니라
    `main` 이 `--repo-path` 를 `build_output_record` 까지 넘기는지다.
    """
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "m.py", "def parse(raw):\n    items = raw.split(',')\n    return items\n")
    _commit_all(repo, "v1")
    _write(repo, "m.py", "def parse(raw, sep=','):\n    items = raw.split(sep)\n    return items\n")
    sha = _commit_all(repo, "v2")
    deletions = extract_module.extract_deletions(repo, _REPO, _last_commit_pair(repo))
    record = extract_module.to_json_dict(next(r for r in deletions if r.function_name == "parse"))
    input_path, out_path = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    input_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setattr(ctx, "load_env_file", lambda _path: None)
    monkeypatch.setattr(
        ctx,
        "run_targets",
        lambda collector, targets, log=None: (
            [ctx.CommitContext(repo=target.repo, commit_sha=sha) for target in targets],
            ctx.AttachmentReport(),
        ),
    )
    args = ["--input", str(input_path), "--out", str(out_path)]
    args += ["--cache-dir", str(tmp_path / "cache"), *extra_args]
    assert ctx.main(args) == 0
    return json.loads(out_path.read_text(encoding="utf-8")), repo


def test_main_forwards_repo_path_to_the_replacement(tmp_path, monkeypatch):
    """`--repo-path` 가 `main` 을 거쳐 실제로 대체 코드 본문까지 닿는다 (#107).

    이 연결이 #107 의 본체다 - 원문을 안 넘기면 에러 없이 대체 코드가 크게 줄어든다
    (예비 200건 39 -> 8). `read_child_source`·`match_replacement` 를 직접 부르는 테스트로는
    `main` 이 인자를 빠뜨려도 통과하므로 CLI 를 끝까지 돌린다.
    """
    output, _repo = _run_main_on_cut_function(
        tmp_path, monkeypatch, "--repo-path", str(tmp_path / "repo")
    )

    code = output["replacement"]["code"]
    assert code.startswith("def parse(raw, sep=','):")
    assert code.rstrip().endswith("return items")


def test_main_without_repo_path_does_not_invent_the_cut_body(tmp_path, monkeypatch):
    """원문 없이는 헝크에 잘린 함수를 채우지 않는다 - 같은 입력에서 위 테스트와 갈린다."""
    output, _repo = _run_main_on_cut_function(tmp_path, monkeypatch)

    assert output["replacement"]["code"] is None
    assert output["replacement"]["match_method"] == ctx.MATCH_SAME_LOCATION


def test_unreadable_child_file_is_none_not_a_crash(tmp_path):
    """파일이 그 커밋에서 통째로 지워졌거나 경로가 틀리면 원문이 없다 - 보수적 경로로 간다."""
    assert ctx.read_child_source(tmp_path, "deadbeef", "missing.py") is None


def test_flat_field_alone_never_fills_code():
    """옛 형식은 헝크 경계가 없어 후보를 안전하게 뽑을 수 없다 - `code` 를 채우지 않는다."""
    added = "def parse(raw):\n    return [p.strip() for p in raw.split(',')]\n"

    assert ctx.match_replacement(_record(added_hunk_same_file=added)) == ctx.UNDETERMINED


def test_new_hunk_field_wins_over_the_old_flat_field():
    """새 형식이 있으면 그것을 쓴다 (2026-09-23 팀 확정)."""
    record = _record(
        added_hunk_same_file="def parse(raw):\n    return 'old'\n",
        added_hunks_same_file=_hunks("def parse(raw):\n    return 'new'\nDONE = 1\n"),
    )

    assert "'new'" in ctx.match_replacement(record).code


def test_no_added_hunks_is_still_a_none_verdict():
    """새 형식은 추가가 없으면 빈 리스트다 (#102 - 추가 0줄 헝크는 넣지 않는다)."""
    record = _record(added_hunks_same_file=[])

    assert ctx.added_hunk_text(record) == ""
    assert ctx.match_replacement(record).match_method == ctx.MATCH_NONE


def test_a_blank_line_addition_is_not_dropped():
    """`new_count == 1` 인데 `added_body` 가 빈 헝크가 있다 - 빈 줄을 추가한 경우다 (#102).

    본문이 비었다고 건너뛰면 그 줄이 사라져, 복원한 텍스트가 원본과 달라진다.
    """
    record = _record(added_hunks_same_file=_hunks("", "def parse(raw):\n    return ()\nDONE = 1\n"))

    assert ctx.added_hunk_text(record).startswith("\n")
    assert ctx.match_replacement(record).match_method == ctx.MATCH_SAME_LOCATION


def test_output_record_carries_the_replacement_field():
    """라벨러가 보는 `replacement`가 여기서 채워진다 (§4.4, sampling.LABELER_REPLACEMENT_FIELDS)."""
    target = ctx.CommitTarget(
        repo="a/b",
        commit_sha="sha",
        record=_record(added_hunks_same_file=_hunks("def parse(raw):\n    return ()\nDONE = 1\n")),
    )
    built = ctx.build_output_record(target, ctx.CommitContext(repo="a/b", commit_sha="sha"))

    assert set(built["replacement"]) == {"code", "match_method", "confidence"}
    assert built["replacement"]["match_method"] == ctx.MATCH_SAME_LOCATION


# 리뷰 코멘트 식별자·좌표 (ADR-018, Issue #70)
# --------------------------------------------------------------------------------------


def _raw_comment(**overrides):
    raw = {
        "id": 1234567,
        "body": "이 헬퍼는 V2 에서 없어진다",
        "path": "src/net.py",
        "line": 42,
        "side": "LEFT",
        "user": {"login": "rev"},
    }
    raw.update(overrides)
    return raw


def test_comment_id_survives_into_the_schema():
    """식별자가 없어 예비 200건에서 17건이 `review:unknown` 으로 남았다 (가이드 §7.2)."""
    parsed = ctx._parse_review_comment(_raw_comment())

    assert parsed.comment_id == 1234567
    assert parsed.to_schema_comment()["comment_id"] == 1234567


def test_side_tells_which_file_the_line_belongs_to():
    """LEFT 는 부모(삭제 전) 파일, RIGHT 는 자식 파일. 없으면 줄 번호가 뜻을 잃는다."""
    assert ctx._parse_review_comment(_raw_comment()).side == "LEFT"
    assert ctx._parse_review_comment(_raw_comment(side="RIGHT")).side == "RIGHT"


def test_outdated_comment_falls_back_to_the_original_line_and_says_so():
    """GitHub 은 자리가 바뀌면 `line` 을 null 로 만들고 `original_line` 만 남긴다.

    조용히 바꿔치기하면 과거 좌표를 현재 좌표로 오해한다. 그래서 표시를 남긴다.
    """
    parsed = ctx._parse_review_comment(_raw_comment(line=None, original_line=88))

    assert parsed.line == 88
    assert parsed.outdated is True


def test_current_comment_is_not_marked_outdated():
    parsed = ctx._parse_review_comment(_raw_comment(line=42, original_line=88))

    assert (parsed.line, parsed.outdated) == (42, False)


def test_comment_without_any_line_is_not_marked_outdated():
    """줄이 아예 없는 코멘트(파일 단위 등)는 "자리가 밀린 것"이 아니다."""
    parsed = ctx._parse_review_comment(_raw_comment(line=None))

    assert parsed.line is None
    assert parsed.outdated is False


def test_missing_optional_fields_do_not_crash():
    """side·user·path 가 없는 응답도 있다. 터지지 않되, `side` 는 `""` 가 아니라 `None` 이다.

    §4.4 가 `side` 를 `enum | null` 로 정의하므로 빈 문자열은 스키마에 없는 세 번째 값이
    된다. `path`·`author` 는 `str` 로 정의돼 있어 `""` 가 맞다.
    """
    parsed = ctx._parse_review_comment({"id": 1, "body": "  hi  "})

    assert parsed.side is None
    assert (parsed.body, parsed.author, parsed.path) == ("hi", "", "")


def test_schema_comment_has_exactly_the_adr_fields():
    """§4.4 `context.review_comments[]` 한 칸의 필드 (§13 — 임의 확장 금지)."""
    schema = ctx._parse_review_comment(_raw_comment()).to_schema_comment()

    assert set(schema) == {"comment_id", "body", "path", "line", "side", "outdated", "author"}


def test_collected_comments_carry_ids_through_the_collector(tmp_path):
    """수집 경로 끝까지 id 가 살아 있어야 라벨러가 로케이터를 쓸 수 있다."""
    routes = dict(FULL_ROUTES)
    routes["/repos/a/b/pulls/100/comments"] = [_raw_comment(id=777, path="src/net.py")]
    result = make_collector(tmp_path, routes).collect(
        "a/b", "sha1", file_path="src/net.py", commit_message="msg"
    )

    comment = result.to_schema_context()["review_comments"][0]
    assert f"review:comment_{comment['comment_id']}" == "review:comment_777"
