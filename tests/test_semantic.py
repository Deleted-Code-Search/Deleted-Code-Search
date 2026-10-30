"""search.embed 임베딩 생성·search.semantic, /api/search?mode=semantic (#193)."""

import hashlib
import json
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from search import api, embed, index
from search.semantic import SemanticIndex, SemanticUnavailable
from tests.test_search import RECORDS_A, RECORDS_B, _record, _write_gz

DIM = 16


class FakeEncoder:
    """낱말마다 해시로 정한 축에 1 을 더하는 결정적 모델. 같은 낱말이 많을수록 가깝다."""

    name = "fake/bag-of-words"
    dim = DIM

    def __init__(self, max_tokens=64):
        self.max_tokens = max_tokens
        self.calls = 0

    def _vec(self, text):
        vec = np.zeros(DIM, dtype=np.float32)
        for word in re.findall(r"[a-z]+", text.lower()):
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1
        return vec

    def passages(self, texts):
        self.calls += len(texts)
        return embed.normalize(np.stack([self._vec(t) for t in texts]))

    def query(self, text):
        return embed.normalize(self._vec(text)[None, :])[0]


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "export"
    folder.mkdir()
    _write_gz(folder / "o__a-00000-of-00001.jsonl.gz", RECORDS_A)
    _write_gz(folder / "o__b-00000-of-00001.jsonl.gz", RECORDS_B)
    return folder


def _build(data, out, encoder=None, **kwargs):
    encoder = encoder or FakeEncoder()
    return embed.build(data, out, encoder, log=lambda _: None, **kwargs), encoder


def test_doc_text_body_and_link_only():
    text, kind = embed.doc_text(RECORDS_A[0])
    assert kind == embed.TEXT_BODY
    assert text.startswith("retry_backoff\npkg/retry_backoff.py\ndef retry_backoff")
    text, kind = embed.doc_text(RECORDS_B[0])  # LINK_ONLY - 본문 대신 커밋 첫 줄
    assert kind == embed.TEXT_LINK
    assert text == "secret_algo\npkg/secret_algo.py\nDrop retry helper"
    long = _record("x", "o/a", body="y" * (embed.MAX_CHARS * 2))
    assert len(embed.doc_text(long)[0]) == embed.MAX_CHARS


def test_build_skips_partial_and_merges(data, tmp_path):
    out = tmp_path / "semantic"
    manifest, encoder = _build(data, out)
    # PARTIAL(a3) 은 빠진다
    assert manifest["count"] == 4 == encoder.calls
    assert manifest["repos"] == ["o/a", "o/b"]
    assert manifest["text_link_count"] == 1
    assert (manifest["year_min"], manifest["year_max"]) == (2021, 2024)
    ids = np.load(out / "ids.npy")
    assert list(ids) == ["a1", "a2", "b1", "b2"]
    vectors = np.load(out / "vectors.npy")
    assert vectors.dtype == np.float16 and vectors.shape == (4, DIM)
    assert np.allclose(np.linalg.norm(vectors.astype(np.float32), axis=1), 1, atol=1e-2)
    meta = np.load(out / "meta.npy")
    cols = embed.META_COLUMNS
    assert list(meta[:, cols.index("repo")]) == [0, 0, 1, 1]
    assert meta[2, cols.index("code_policy")] == embed.CODE_POLICIES.index("LINK_ONLY")
    assert meta[2, cols.index("text_kind")] == embed.TEXT_LINK
    assert meta[2, cols.index("reason")] == -1  # 이유 없음
    saved = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert saved["model"] == "fake/bag-of-words" and saved["dim"] == DIM


def test_resume_skips_done_parts_and_redoes_changed(data, tmp_path):
    out = tmp_path / "semantic"
    _build(data, out)
    _, again = _build(data, out)
    assert again.calls == 0  # 조각 둘 다 건너뜀
    # 입력 하나가 바뀌면 그 조각만 다시
    _write_gz(data / "o__b-00000-of-00001.jsonl.gz", RECORDS_B[:1])
    manifest, changed = _build(data, out)
    assert changed.calls == 1 and manifest["count"] == 3
    # 토큰 상한이 다르면 전부 다시
    _, other = _build(data, out, FakeEncoder(max_tokens=32))
    assert other.calls == 3


def test_interrupted_part_is_redone(data, tmp_path):
    out = tmp_path / "semantic"
    _build(data, out)
    part = embed.part_path(out, data / "o__a-00000-of-00001.jsonl.gz")
    part.write_bytes(b"broken")  # 끊긴 조각
    _, again = _build(data, out)
    assert again.calls == 2


def test_truncated_zip_part_is_redone(data, tmp_path):
    out = tmp_path / "semantic"
    _build(data, out)
    part = embed.part_path(out, data / "o__a-00000-of-00001.jsonl.gz")
    raw = part.read_bytes()
    for cut in (len(raw) // 2, len(raw) - 10, 30):  # 압축 중간·끝·머리에서 끊김
        part.write_bytes(raw[:cut])
        assert embed.read_part_info(part) is None
    _, again = _build(data, out)
    assert again.calls == 2


def test_layout_change_redoes_parts_and_merge_refuses_old(data, tmp_path, monkeypatch):
    out = tmp_path / "semantic"
    manifest, _ = _build(data, out)
    assert manifest["doc_format"] == embed.DOC_FORMAT
    assert manifest["max_chars"] == embed.MAX_CHARS
    monkeypatch.setattr(embed, "MAX_CHARS", embed.MAX_CHARS // 2)
    # 합치기만 하면 옛 조각을 거부한다
    shards = index.shard_files(data)
    shas = {s.name: embed.file_sha256(s) for s in shards}
    with pytest.raises(embed.EmbedError, match="레이아웃"):
        embed.merge(out, shards, shas, FakeEncoder())
    # build 는 조각을 다시 만든다
    _, again = _build(data, out)
    assert again.calls == 4
    monkeypatch.setattr(embed, "META_COLUMNS", tuple(reversed(embed.META_COLUMNS)))
    _, reordered = _build(data, out)
    assert reordered.calls == 4


def test_build_selected_repos_and_unknown_repo(data, tmp_path):
    manifest, _ = _build(data, tmp_path / "s", repos=["o/b"])
    assert manifest["count"] == 2 and manifest["repos"] == ["o/b"]
    with pytest.raises(embed.EmbedError):
        _build(data, tmp_path / "s2", repos=["o/zzz"])


@pytest.fixture
def engine(data, tmp_path):
    out = tmp_path / "semantic"
    _build(data, out)
    return SemanticIndex.load(out, lambda manifest: FakeEncoder(manifest["max_tokens"]))


def test_mask_filters(engine):
    ids = list(engine.ids)

    def picked(**params):
        mask = engine.mask(params)
        return ids if mask is None else [i for i, keep in zip(ids, mask, strict=True) if keep]

    assert picked() == ids
    assert picked(repo="o/b") == ["b1", "b2"]
    assert picked(reason="LIB") == ["a1"]
    assert picked(grade="INFERRED") == ["b2"]
    assert picked(source="UNDISTINGUISHED") == ["b1"]
    assert picked(code_policy="LINK_ONLY") == ["b1"]
    assert picked(year_from=2023) == ["b1", "b2"]
    assert picked(year_to=2021, repo="o/a") == ["a1", "a2"]
    assert picked(repo="o/not-embedded") == []


def test_top_k_matches_brute_force_in_small_chunks(engine):
    query = engine.embed_query("retry backoff sleep")
    scores = engine.vectors.astype(np.float32) @ query
    expected = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    for k in (1, 2, 4, 10):
        got = engine.top_k(query, k, chunk=1)
        assert [row for row, _ in got] == expected[:k]
        assert got == sorted(got, key=lambda h: -h[1])
    mask = np.array([False, True, True, False])
    assert [row for row, _ in engine.top_k(query, 4, mask, chunk=3)] == [
        i for i in expected if mask[i]
    ]
    assert engine.top_k(query, 0) == []


def test_load_errors(tmp_path, data):
    with pytest.raises(SemanticUnavailable):
        SemanticIndex.load(tmp_path / "nothing")
    out = tmp_path / "semantic"
    _build(data, out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["count"] = 99
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SemanticUnavailable):
        SemanticIndex.load(out, lambda m: FakeEncoder())


@pytest.fixture
def client(data, tmp_path, monkeypatch):
    sqlite = tmp_path / "index.sqlite"
    index.build(data, sqlite, log=lambda _: None)
    semantic = tmp_path / "semantic"
    _build(data, semantic)
    monkeypatch.setenv(api.INDEX_ENV, str(sqlite))
    monkeypatch.setenv(api.SEMANTIC_ENV, str(semantic))
    monkeypatch.setattr(api, "encoder_factory", lambda m: FakeEncoder(m["max_tokens"]))
    api._semantic.clear()
    yield TestClient(api.app)
    api._semantic.clear()


def test_api_semantic_ranks_by_similarity(client):
    response = client.get("/api/search", params={"q": "retry backoff sleep", "mode": "semantic"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "semantic" and body["total"] == 4
    results = body["results"]
    assert results[0]["id"] == "a1" and results[0]["function_name"] == "retry_backoff"
    scores = [row["score"] for row in results]
    assert scores == sorted(scores, reverse=True)
    assert all(row["snippet"] is None for row in results)


def test_api_semantic_filters_paging_and_kind(client):
    def ids(**params):
        response = client.get("/api/search", params={"q": "cache", "mode": "semantic", **params})
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()["results"]], response.json()["total"]

    assert ids(repo="o/b") == (["b2", "b1"], 2)
    assert ids(reason="DEAD", repo="o/a")[1] == 1
    first, _ = ids(limit=2)
    second, _ = ids(limit=2, offset=2)
    assert len(first) == len(second) == 2 and not set(first) & set(second)
    assert ids(kind="PARTIAL") == ([], 0)
    assert ids(kind="FULL_FUNCTION")[1] == 4
    response = client.get("/api/search", params={"q": "x", "mode": "semantic", "repo": "o/zzz"})
    assert response.status_code == 422


def test_api_semantic_needs_query_and_valid_mode(client):
    assert client.get("/api/search", params={"mode": "semantic"}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "mode": "fuzzy"}).status_code == 422


def test_api_default_mode_is_keyword(client):
    body = client.get("/api/search", params={"q": "retry"}).json()
    assert body["mode"] == "keyword"
    assert body["match"] == '"retry"'
    assert body["results"][0]["snippet"] is not None
    assert "score" not in body["results"][0]


def test_api_semantic_missing_files_is_503_keyword_still_works(client, tmp_path, monkeypatch):
    monkeypatch.setenv(api.SEMANTIC_ENV, str(tmp_path / "nothing"))
    response = client.get("/api/search", params={"q": "retry", "mode": "semantic"})
    assert response.status_code == 503
    assert "search.embed" in response.json()["detail"]
    assert client.get("/api/search", params={"q": "retry"}).status_code == 200
    meta = client.get("/api/search/meta").json()
    assert meta["semantic"] is None
    assert client.get("/api/health").json()["semantic_index"] is False


def test_api_loads_model_once(client, monkeypatch):
    loads = []

    def factory(manifest):
        loads.append(1)
        return FakeEncoder(manifest["max_tokens"])

    monkeypatch.setattr(api, "encoder_factory", factory)
    api._semantic.clear()
    with TestClient(api.app) as started:  # lifespan 에서 한 번
        assert loads == [1]
        for q in ("retry", "cache", "parser"):
            assert (
                started.get("/api/search", params={"q": q, "mode": "semantic"}).status_code == 200
            )
    assert loads == [1]
    meta = client.get("/api/search/meta").json()
    assert meta["semantic"]["model"] == "fake/bag-of-words"
    assert meta["semantic"]["count"] == 4


def test_relevance_export_leaves_judgement_blank_and_scores(tmp_path):
    from eval import search_relevance as rel

    def fake_search(params):
        n = 3 if params["mode"] == "keyword" else 10
        return {
            "results": [
                {"id": f"{params['mode']}{i}", "repo": "o/a", "function_name": "f",
                 "commit_first_line": "c"}
                for i in range(n)
            ]
        }  # fmt: skip

    rows = rel.export_rows(fake_search)
    assert len(rows) == len(rel.QUERIES) * 13
    assert all(row["relevant"] == "" for row in rows)
    # 키워드 쪽에는 낱말을, 의미 쪽에는 문장을 넣는다
    sheet = tmp_path / "sheet.csv"
    rel.write_sheet(sheet, rows)
    with pytest.raises(rel.RelevanceError):
        rel.score(sheet)  # 빈칸이 있으면 멈춘다
    for row in rows:
        # 의미: 1위와 3위만 관련, 키워드: 2위만
        good = {"semantic": {1, 3}, "keyword": {2}}[row["mode"]]
        row["relevant"] = "1" if row["rank"] in good else "0"
    rel.write_sheet(sheet, rows)
    result = rel.score(sheet)
    assert result["modes"]["semantic"]["p_at_k"] == 0.2
    assert result["modes"]["semantic"]["mrr"] == 1.0
    # 결과가 3개뿐이어도 분모는 10
    assert result["modes"]["keyword"]["p_at_k"] == 0.1
    assert result["modes"]["keyword"]["mrr"] == 0.5


def test_mmap_and_in_memory_give_same_hits(data, tmp_path):
    out = tmp_path / "semantic"
    _build(data, out)
    factory = lambda manifest: FakeEncoder(manifest["max_tokens"])  # noqa: E731
    mem = SemanticIndex.load(out, factory)
    mapped = SemanticIndex.load(out, factory, in_memory=False)
    assert mem.vectors.dtype == np.float32 and mapped.vectors.dtype == np.float16
    query = mem.embed_query("cache parser")
    assert mem.top_k(query, 4) == mapped.top_k(query, 4)
