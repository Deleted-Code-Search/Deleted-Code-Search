"""search.index 색인과 search.api 검색 엔드포인트 (#191)."""

import gzip
import json

import pytest
from fastapi.testclient import TestClient

from search import api, index


def _record(
    rid,
    repo,
    *,
    name="f",
    body="def f():\n    pass",
    message="m",
    kind="FULL_FUNCTION",
    source="HUMAN",
    label="DEAD",
    grade="EXPLICIT",
    year="2021",
    policy="CODE_INCLUDED",
    pr_title=None,
    issue_titles=None,
    comments=None,
):
    reason = None if label is None else {"label": label, "evidence_grade": grade}
    return {
        "id": rid,
        "repo": repo,
        "author_date": f"{year}-03-01T00:00:00+00:00",
        "file_path": f"pkg/{name}.py",
        "function_name": name,
        "function_signature": None if policy == "LINK_ONLY" else f"def {name}():",
        "deleted_body": None if policy == "LINK_ONLY" else body,
        "deletion_kind": kind,
        "is_test_code": False,
        "filter_status": "KEPT",
        "context": {
            "commit_message": message,
            "pr_title": pr_title,
            "issue_titles": issue_titles,
            "review_comments": comments,
        },
        "replacement": None,
        "reason": reason,
        "source_url": f"https://github.com/{repo}/commit/abc",
        "code_policy": policy,
        "classification": {"source": source},
    }


RECORDS_A = [
    _record(
        "a1",
        "o/a",
        name="retry_backoff",
        body="def retry_backoff():\n    time.sleep(2 ** n)",
        message="Replace hand-rolled retry with tenacity\n\nrace condition in sleep",
        label="LIB",
        pr_title="Use tenacity for retries",
        issue_titles=["Flaky retry"],
        comments=[{"body": "lgtm"}, {"body": "ok"}],
    ),
    _record("a2", "o/a", name="old_parser", message="Remove unused parser", label="DEAD"),
    _record(
        "a3",
        "o/a",
        name="partial_bit",
        body="x = retry_count + 1",
        kind="PARTIAL",
        source="NOT_CLASSIFIED",
        label=None,
        year="2014",
    ),
]
RECORDS_B = [
    _record(
        "b1",
        "o/b",
        name="secret_algo",
        body="def secret_algo():\n    return retry_magic_token",
        message="Drop retry helper",
        source="UNDISTINGUISHED",
        label=None,
        policy="LINK_ONLY",
        year="2023",
    ),
    _record(
        "b2",
        "o/b",
        name="cache_get",
        message="Speed up cache",
        source="MODEL_ONLY",
        label="PERF",
        grade="INFERRED",
        year="2024",
    ),
]


def _write_gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "export"
    data.mkdir()
    _write_gz(data / "o__a-00000-of-00001.jsonl.gz", RECORDS_A)
    _write_gz(data / "o__b-00000-of-00001.jsonl.gz", RECORDS_B)
    # 확정 500건 사본 - shard 가 아니라 읽지 않는다
    _write_gz(data / "main500-v0.9.jsonl.gz", RECORDS_A[:1])
    out = tmp_path / "search" / "index.sqlite"
    info = index.build(data, out, log=lambda _: None)
    assert info["records"] == 5
    assert info["repos"] == ["o/a", "o/b"]
    assert (info["year_min"], info["year_max"]) == (2014, 2024)
    monkeypatch.setenv(api.INDEX_ENV, str(out))
    return TestClient(api.app)


def _ids(response):
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["results"]]


def test_keyword_search_ranks_name_match_first_and_marks_snippet(client):
    body = client.get("/api/search", params={"q": "retry"}).json()
    ids = [row["id"] for row in body["results"]]
    # 함수 이름에 맞은 a1 이 본문에만 맞은 a3 보다 앞이다. b1 은 LINK_ONLY 라 커밋 메시지로만 맞는다
    assert ids[0] == "a1"
    assert set(ids) == {"a1", "a3", "b1"}
    assert body["total"] == 3 and body["total_capped"] is False
    top = body["results"][0]
    assert api.MARK_OPEN + "retry" in top["snippet"].lower()
    assert top["commit_first_line"] == "Replace hand-rolled retry with tenacity"
    assert top["year"] == 2021 and top["reason_label"] == "LIB" and top["source"] == "HUMAN"


def test_keyword_search_hits_pr_and_issue_titles(client):
    assert _ids(client.get("/api/search", params={"q": "tenacity"})) == ["a1"]
    assert _ids(client.get("/api/search", params={"q": "flaky"})) == ["a1"]


def test_underscore_name_and_fts_syntax_are_safe(client):
    assert _ids(client.get("/api/search", params={"q": "retry_backoff"})) == ["a1"]
    assert _ids(client.get("/api/search", params={"q": "retr*"}))[0] == "a1"
    # FTS5 문법 문자를 그대로 넣어도 400 이 아니라 구문 검색이다
    for q in ['"unbalanced', "NEAR(retry", "a:b OR -", "()"]:
        assert client.get("/api/search", params={"q": q}).status_code == 200


def test_link_only_body_is_not_indexed(client):
    # b1 본문에만 있는 단어 - LINK_ONLY 라 본문이 없으니 맞지 않는다
    assert _ids(client.get("/api/search", params={"q": "retry_magic_token"})) == []
    detail = client.get("/api/records/b1").json()["record"]
    assert detail["code_policy"] == "LINK_ONLY"
    assert detail["deleted_body"] is None


def test_filters_with_keyword(client):
    assert _ids(client.get("/api/search", params={"q": "retry", "repo": "o/b"})) == ["b1"]
    assert _ids(client.get("/api/search", params={"q": "retry", "kind": "PARTIAL"})) == ["a3"]
    assert _ids(client.get("/api/search", params={"q": "retry", "year_to": 2020})) == ["a3"]
    assert _ids(client.get("/api/search", params={"q": "retry", "reason": "LIB"})) == ["a1"]


def test_empty_query_with_filters_only(client):
    assert _ids(client.get("/api/search")) == ["a1", "a2", "a3", "b1", "b2"]
    assert _ids(client.get("/api/search", params={"source": "MODEL_ONLY"})) == ["b2"]
    assert _ids(client.get("/api/search", params={"grade": "EXPLICIT", "repo": "o/a"})) == [
        "a1",
        "a2",
    ]
    assert _ids(client.get("/api/search", params={"year_from": 2023, "year_to": 2023})) == ["b1"]
    # 기호만 있는 q 는 비어 있는 것과 같다
    body = client.get("/api/search", params={"q": "  ** ", "reason": "DEAD"}).json()
    assert body["match"] is None and [r["id"] for r in body["results"]] == ["a2"]
    assert body["results"][0]["snippet"] is None


def test_paging_and_unknown_filter_value(client):
    page = client.get("/api/search", params={"limit": 2, "offset": 2}).json()
    assert [r["id"] for r in page["results"]] == ["a3", "b1"]
    assert page["total"] == 5
    assert client.get("/api/search", params={"reason": "BOGUS"}).status_code == 422
    assert client.get("/api/search", params={"limit": 0}).status_code == 422


def test_meta_lists_filter_choices(client):
    meta = client.get("/api/search/meta").json()
    assert meta["repos"] == ["o/a", "o/b"]
    assert meta["reasons"] == ["BUG", "PERF", "SEC", "LIB", "DEAD", "DESIGN", "FEAT", "UNK"]
    assert meta["grades"] == ["EXPLICIT", "INFERRED", "UNKNOWN"]
    assert "UNDISTINGUISHED" in meta["sources"] and "NOT_CLASSIFIED" in meta["sources"]
    assert (meta["year_min"], meta["year_max"]) == (2014, 2024)
    assert meta["records"] == 5


def test_record_detail_is_original_record(client):
    body = client.get("/api/records/a1").json()
    assert body["record"] == RECORDS_A[0]
    assert body["review_comment_count"] == 2
    assert client.get("/api/records/a2").json()["review_comment_count"] is None
    assert client.get("/api/records/nope").status_code == 404


def test_missing_index_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv(api.INDEX_ENV, str(tmp_path / "missing.sqlite"))
    client = TestClient(api.app)
    for path in ("/api/search", "/api/search/meta", "/api/records/a1"):
        response = client.get(path)
        assert response.status_code == 503
        assert "python -m search.index" in response.json()["detail"]
    assert client.get("/api/health").json()["search_index"] is False


def test_missing_shards_is_error(tmp_path):
    with pytest.raises(index.SearchIndexError):
        index.build(tmp_path, tmp_path / "out.sqlite", log=lambda _: None)


def test_keyword_does_not_hit_facet_tokens(client):
    # facets 칼럼의 토큰을 그대로 쳐도 맞지 않는다 - 검색어는 FTS_COLUMNS 로만 제한된다
    token = index.facet_token("repo", "o/a")
    assert _ids(client.get("/api/search", params={"q": token})) == []
    assert _ids(client.get("/api/search", params={"q": "year2021"})) == []
    # 키워드 + 연도 범위가 비면 0건
    assert _ids(client.get("/api/search", params={"q": "retry", "year_from": 2030})) == []
    body = client.get("/api/search", params={"q": "retry", "source": "HUMAN"}).json()
    assert [r["id"] for r in body["results"]] == ["a1"]
    assert index.FACET_COLUMN not in body["results"][0]["snippet"]
