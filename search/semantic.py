"""검색 2단계 의미 검색 - 질의 임베딩과 벡터 파일의 내적 (#193, ADR-023). 담당: 성제

`search.embed` 가 만든 `data/search/semantic/` 을 연다. 질의(자연어 또는 코드 조각)를 같은 모델로
임베딩하고, 벡터와 CHUNK 행씩 내적한 뒤 필터 마스크를 씌워 상위 K 를 고른다.

벡터 파일은 float16 이다 (72만 × 384 = 0.56 GB). 기본은 읽을 때 한 번 float32 로 바꿔 메모리에 둔다
(1.1 GB). 요청마다 float16 → float32 를 바꾸면 72만 행에 0.38초, 내적은 0.03초였다 (#193 측정,
합성 벡터). 메모리가 모자라면 `in_memory=False`(API: 환경 변수 SEARCH_SEMANTIC_MMAP=1) 로 메모리맵
그대로 청크마다 바꾼다 - 요청당 약 0.45초.

필터는 meta.npy (작은 정수) 로 푼다. 선택지 이름 → 정수는 manifest 의 목록 위치다.
결과 행의 목록 칼럼(함수 이름·경로·커밋 첫 줄 …)은 API 가 키워드 색인(SQLite)에서 id 로 가져온다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from search.embed import DEFAULT_MODEL_DIR, META_COLUMNS, Encoder, FastEmbedEncoder

CHUNK = 100_000
# 필터 이름 (search.api 파라미터) → (meta 칸, manifest 선택지 목록)
FILTER_COLUMNS = {
    "repo": ("repo", "repos"),
    "reason": ("reason", "reasons"),
    "grade": ("grade", "grades"),
    "source": ("source", "sources"),
    "code_policy": ("code_policy", "code_policies"),
}
FILES = ("manifest.json", "vectors.npy", "ids.npy", "meta.npy")


class SemanticUnavailable(RuntimeError):
    """임베딩 파일이 없거나 manifest 와 맞지 않는다."""


def default_encoder(manifest: dict[str, Any]) -> Encoder:
    return FastEmbedEncoder(manifest["model"], manifest["max_tokens"], DEFAULT_MODEL_DIR)


@dataclass
class SemanticIndex:
    manifest: dict[str, Any]
    vectors: np.ndarray  # float32 (N, dim) 메모리, 또는 float16 메모리맵 (모듈 설명)
    ids: np.ndarray
    meta: np.ndarray  # int16 (N, len(META_COLUMNS))
    encoder: Encoder

    @classmethod
    def load(
        cls,
        folder: Path,
        encoder_factory: Callable[[dict[str, Any]], Encoder] = default_encoder,
        in_memory: bool = True,
    ) -> SemanticIndex:
        missing = [name for name in FILES if not (folder / name).is_file()]
        if missing:
            raise SemanticUnavailable(
                f"{folder} 에 {', '.join(missing)} 가 없다. python -m search.embed 먼저 돌린다"
            )
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        vectors = np.load(folder / "vectors.npy", mmap_mode="r")
        ids = np.load(folder / "ids.npy")
        meta = np.load(folder / "meta.npy")
        if not (len(vectors) == len(ids) == len(meta) == manifest["count"]):
            raise SemanticUnavailable(f"{folder} 의 파일 건수가 manifest 와 다르다")
        if vectors.shape[1] != manifest["dim"] or meta.shape[1] != len(META_COLUMNS):
            raise SemanticUnavailable(f"{folder} 의 차원·메타 칸이 manifest 와 다르다")
        if in_memory:
            vectors = np.asarray(vectors, dtype=np.float32)
        encoder = encoder_factory(manifest)
        if encoder.dim != manifest["dim"]:
            raise SemanticUnavailable(f"모델 차원 {encoder.dim} ≠ manifest {manifest['dim']}")
        return cls(manifest, vectors, ids, meta, encoder)

    def choices(self, name: str) -> list[Any]:
        return self.manifest[FILTER_COLUMNS[name][1]]

    def mask(self, params: dict[str, Any]) -> np.ndarray | None:
        """필터 → 불리언 마스크. 필터가 없으면 None (전부).

        선택지에 없는 값은 0건이다 - 오타는 API 가 키워드 색인 선택지로 먼저 422 를 낸다. 여기까지
        오는 건 표본 임베딩에 없는 저장소 같은 경우다.
        """
        mask: np.ndarray | None = None

        def narrow(cond: np.ndarray) -> None:
            nonlocal mask
            mask = cond if mask is None else mask & cond

        for name, (column, _) in FILTER_COLUMNS.items():
            value = params.get(name)
            if value:
                choices = self.choices(name)
                code = choices.index(value) if value in choices else -2  # -1 은 "없음" 이다
                narrow(self.meta[:, META_COLUMNS.index(column)] == code)
        year = self.meta[:, META_COLUMNS.index("year")]
        if params.get("year_from") is not None:
            narrow(year >= params["year_from"])
        if params.get("year_to") is not None:
            narrow((year <= params["year_to"]) & (year >= 0))
        return mask

    def embed_query(self, text: str) -> np.ndarray:
        return self.encoder.query(text).astype(np.float32)

    def top_k(
        self, query: np.ndarray, k: int, mask: np.ndarray | None = None, chunk: int = CHUNK
    ) -> list[tuple[int, float]]:
        """(행 번호, 유사도) 상위 k 개, 유사도 내림차순. 동점은 행 번호 순."""
        if k <= 0:
            return []
        best_rows = np.empty(0, dtype=np.int64)
        best_scores = np.empty(0, dtype=np.float32)
        for start in range(0, len(self.vectors), chunk):
            stop = min(start + chunk, len(self.vectors))
            scores = np.asarray(self.vectors[start:stop], dtype=np.float32) @ query
            rows = np.arange(start, stop)
            if mask is not None:
                keep = mask[start:stop]
                scores, rows = scores[keep], rows[keep]
            if len(scores) > k:
                part = np.argpartition(-scores, k - 1)[:k]
                scores, rows = scores[part], rows[part]
            best_rows = np.concatenate([best_rows, rows])
            best_scores = np.concatenate([best_scores, scores])
            if len(best_scores) > k:
                part = np.argpartition(-best_scores, k - 1)[:k]
                best_rows, best_scores = best_rows[part], best_scores[part]
        order = np.lexsort((best_rows, -best_scores))
        return [(int(best_rows[i]), float(best_scores[i])) for i in order]

    def search(
        self, text: str, k: int, params: dict[str, Any]
    ) -> tuple[list[tuple[str, float]], int]:
        """질의 → ([(레코드 id, 유사도)], 필터를 통과한 건수)."""
        mask = self.mask(params)
        total = int(mask.sum()) if mask is not None else len(self.ids)
        hits = self.top_k(self.embed_query(text), k, mask)
        return [(str(self.ids[row]), score) for row, score in hits], total
