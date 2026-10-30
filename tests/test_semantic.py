"""search.embed 임베딩 생성·search.semantic, /api/search?mode=semantic (#193)."""

import hashlib
import json
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from eval.search_relevance import QUERIES as QUERY_KEYWORDS
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


@pytest.mark.parametrize("chunk", [7, 100, 5000])
def test_top_k_ties_are_stable_across_pages(chunk):
    # 같은 텍스트 = 같은 벡터 = 같은 점수 (#194 희수 리뷰: 벡터 50개 × 40번 복제를 섞은 2,000행)
    rng = np.random.default_rng(0)
    base = embed.normalize(rng.normal(size=(50, DIM)).astype(np.float32))
    vectors = base[rng.permutation(np.repeat(np.arange(50), 40))]
    engine = SemanticIndex({}, vectors, np.arange(len(vectors)).astype("U8"), None, None)
    query = base[3]
    scores = vectors @ query
    expected = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:20]
    first = engine.top_k(query, 10, chunk=chunk)
    second = engine.top_k(query, 20, chunk=chunk)[10:]
    assert [row for row, _ in first + second] == expected
    assert not {row for row, _ in first} & {row for row, _ in second}


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


def test_api_starts_even_if_semantic_load_crashes(client, monkeypatch):
    def broken(manifest):
        raise RuntimeError("모델 내려받기 실패")

    monkeypatch.setattr(api, "encoder_factory", broken)
    api._semantic.clear()
    with TestClient(api.app) as started:  # lifespan 이 예외를 삼키고 뜬다
        assert started.get("/api/search", params={"q": "retry"}).status_code == 200
        assert started.get("/api/health").status_code == 200


def _fake_relevance_search(params):
    # 키워드는 3건, 의미는 10건. 의미 0·1번은 키워드 0·1번과 같은 레코드(두 모드에 나옴)
    if params["mode"] == "keyword":
        ids = [f"{params['q']}-k{i}" for i in range(3)]
    else:
        kw = dict(QUERY_KEYWORDS)[params["q"]]
        ids = [f"{kw}-k0", f"{kw}-k1"] + [f"{kw}-s{i}" for i in range(8)]
    first = '=HYPERLINK("http://x")'  # 커밋 메시지가 수식으로 시작하는 경우
    return {
        "results": [
            {"id": rid, "repo": "o/a", "file_path": "-p.py", "function_name": "@f",
             "commit_first_line": first}
            for rid in ids
        ]
    }  # fmt: skip


def _fill(rows, judge, good):
    for row in rows:
        row[f"relevant_{judge}"] = "1" if good(row["record_id"]) else "0"


def test_relevance_export_is_blind_and_deduplicated(tmp_path):
    from eval import search_relevance as rel

    rows, key = rel.export(_fake_relevance_search)
    # 질의마다 키워드 3 + 의미 10 - 겹치는 2 = 11 행, 모드·순위 칸 없음, 판정 칸은 비움
    assert len(rows) == len(rel.QUERIES) * 11
    assert "mode" not in rel.COLUMNS and "rank" not in rel.COLUMNS
    assert all(row[c] == "" for row in rows for c in rel.JUDGE_COLUMNS)
    assert len({(r["query_id"], r["record_id"]) for r in rows}) == len(rows)
    # 섞였다 - 의미 1위가 늘 첫 행이 아니다, 같은 시드면 같은 순서
    q01 = [r["record_id"] for r in rows if r["query_id"] == "q01"]
    assert q01 != key["queries"][0]["ranked"]["semantic"][: len(q01)]
    assert [r["record_id"] for r in rel.export(_fake_relevance_search)[0]] == [
        r["record_id"] for r in rows
    ]
    # 키에는 모드별 순위
    assert key["queries"][0]["ranked"]["keyword"] == ["retry backoff-k0", "retry backoff-k1",
                                                      "retry backoff-k2"]  # fmt: skip
    # load_config(시연 예시)는 평가 질의에서 뺐다
    assert not any("load_config" in q for q, _ in rel.QUERIES)


def test_relevance_sheet_neutralizes_formulas(tmp_path):
    import csv

    from eval import search_relevance as rel

    rows, _ = rel.export(_fake_relevance_search)
    sheet = tmp_path / "sheet.csv"
    rel.write_sheet(sheet, rows)
    with sheet.open(encoding="utf-8-sig", newline="") as handle:
        first = next(csv.DictReader(handle))
    assert first["commit_first_line"].startswith("'=")
    assert first["file_path"] == "'-p.py" and first["function_name"] == "'@f"
    assert first["record_id"] == rows[0]["record_id"]  # uuid 는 그대로


def test_relevance_score_two_judges_and_agreement(tmp_path):
    from eval import search_relevance as rel

    rows, key = rel.export(_fake_relevance_search)
    sheet, key_path = tmp_path / "sheet.csv", tmp_path / "key.json"
    rel.write_key(key_path, key)
    rel.write_sheet(sheet, rows)
    with pytest.raises(rel.RelevanceError, match="판정이 하나도"):
        rel.score(sheet, key_path)
    # 성제: 키워드 1위(k0)와 의미 3·5위(s0, s2)만 관련
    _fill(rows, "sj", lambda rid: rid.endswith(("-k0", "-s0", "-s2")))
    rows[0]["relevant_sj"] = ""
    rel.write_sheet(sheet, rows)
    with pytest.raises(rel.RelevanceError, match="빈칸이 1개"):
        rel.score(sheet, key_path)  # 일부만 채움
    _fill(rows, "sj", lambda rid: rid.endswith(("-k0", "-s0", "-s2")))
    rel.write_sheet(sheet, rows)
    one = rel.score(sheet, key_path)
    assert list(one["judges"]) == ["sj"] and "agreement" not in one
    sem = one["by_judge"]["sj"]["semantic"]
    # 의미 순위: k0, k1, s0, s1, s2 ... → 관련 1·3·5위
    assert sem["p_at_k"] == 0.3 and sem["mrr"] == 1.0
    # 결과가 3개뿐이어도 분모는 10
    assert one["by_judge"]["sj"]["keyword"]["p_at_k"] == 0.1
    # 희수: k0 만 관련 → 키워드는 같고 의미는 1위만
    _fill(rows, "hs", lambda rid: rid.endswith("-k0"))
    rel.write_sheet(sheet, rows)
    both = rel.score(sheet, key_path)
    assert both["by_judge"]["hs"]["semantic"]["p_at_k"] == 0.1
    assert both["mean"]["semantic"]["p_at_k"] == 0.2
    # 질의당 11행 중 s0·s2 두 행만 다르다
    assert both["agreement"]["items"] == len(rows)
    assert both["agreement"]["percent"] == round(9 / 11, 3)
    assert 0 < both["agreement"]["cohen_kappa"] < 1


def test_relevance_score_refuses_missing_query(tmp_path):
    from eval import search_relevance as rel

    rows, key = rel.export(_fake_relevance_search)
    _fill(rows, "sj", lambda rid: False)
    sheet, key_path = tmp_path / "sheet.csv", tmp_path / "key.json"
    rel.write_sheet(sheet, rows)
    key["queries"] = key["queries"][:-1]  # 질의 하나가 빠진 키
    rel.write_key(key_path, key)
    with pytest.raises(rel.RelevanceError, match="q10"):
        rel.score(sheet, key_path)
    rel.write_key(key_path, rel.export(_fake_relevance_search)[1])
    rel.write_sheet(sheet, [r for r in rows if r["query_id"] != "q03"])  # 시트에서 질의가 빠짐
    with pytest.raises(rel.RelevanceError, match="시트에 키의 레코드"):
        rel.score(sheet, key_path)


def test_agreement_kappa():
    from eval.search_relevance import agreement

    assert agreement([(True, True), (False, False)]) == {
        "items": 2, "percent": 1.0, "cohen_kappa": 1.0
    }  # fmt: skip
    assert agreement([(True, False), (False, True)])["cohen_kappa"] == -1.0


def test_mmap_and_in_memory_give_same_hits(data, tmp_path):
    out = tmp_path / "semantic"
    _build(data, out)
    factory = lambda manifest: FakeEncoder(manifest["max_tokens"])  # noqa: E731
    mem = SemanticIndex.load(out, factory)
    mapped = SemanticIndex.load(out, factory, in_memory=False)
    assert mem.vectors.dtype == np.float32 and mapped.vectors.dtype == np.float16
    query = mem.embed_query("cache parser")
    assert mem.top_k(query, 4) == mapped.top_k(query, 4)
