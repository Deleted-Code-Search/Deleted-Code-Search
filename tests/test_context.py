"""맥락 결합 테스트 (이슈 #6).

네트워크를 타지 않는다. 참조 파싱은 순수 함수로, API 경로는 라우팅 가짜 fetcher 로 본다.
"""

import email.message
import json
import urllib.error
import urllib.parse

import pytest

from pipeline import context as ctx

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
        "issue_numbers",
        "issue_titles",
        "review_comments",
    }
    assert schema["issue_numbers"] == [42, 43]
    assert schema["review_comments"] == ["why not use urllib3 Retry?", "unrelated nit"]


def test_labels_and_issue_bodies_are_collected_but_not_in_schema(tmp_path):
    """스키마에 칸이 없는 값은 모으되 §4.4 매핑에는 넣지 않는다 (변경 제안 대상)."""
    collector = make_collector(tmp_path, FULL_ROUTES)
    result = collector.collect("a/b", "sha1", commit_message="msg")

    assert result.pr_labels == ("bug", "type: cleanup")
    assert result.issue_bodies == ("detail", "detail43")
    assert "pr_labels" not in result.to_schema_context()
    assert "issue_bodies" not in result.to_schema_context()


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
    assert result.to_schema_context()["review_comments"] == ["why not use urllib3 Retry?"]


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
