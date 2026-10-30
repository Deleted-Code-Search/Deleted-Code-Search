"""검색 2단계 임베딩 생성 - FULL_FUNCTION 레코드를 벡터 파일로 (#193, #8, ADR-023). 담당: 성제

기준: CHARTER.md §4.2 ⑤ 2단계. 모델은 #8 비교로 정했다 (`docs/reports/embedding_model_choice.md`).
벡터는 PostgreSQL + pgvector(ADR-009) 대신 numpy 파일로 둔다 (ADR-023).

대상: Release `dataset-v0.9` 의 KEPT 행 중 FULL_FUNCTION (723,087건). PARTIAL 은 넣지 않는다.
임베딩할 텍스트 (`doc_text`):
    본문 있음 (CODE_INCLUDED)  function_name + file_path + deleted_body
    본문 없음 (LINK_ONLY)      function_name + file_path + 커밋 메시지 첫 줄
    어느 쪽인지는 메타 `text_kind` 칸에 남긴다 (TEXT_BODY / TEXT_LINK).
    길이는 모델 토큰 상한(`--max-tokens`)에서 자른다. 그 전에 글자 수(MAX_CHARS)로 한 번 자른다 -
    수만 줄짜리 함수를 통째로 토크나이저에 넣지 않게.

이어 하기: 입력 jsonl.gz 파일 하나 = 조각 하나 (`parts/<파일 이름>.npz`). 조각에 입력 파일
sha256·모델·토큰 상한을 적어 두고, 다시 돌리면 같은 값의 조각은 건너뛴다. 조각은 임시 파일에
쓰고 바꿔 끼운다 - 중간에 끊겨도 반쯤 된 조각이 남지 않는다. 마지막에 조각을 합친다 (`merge`).

출력 (`data/search/semantic/`, gitignore):
    vectors.npy    float16 (N, dim), L2 정규화 - 내적 = 코사인
    ids.npy        레코드 id (고정 폭 문자열)
    meta.npy       int16 (N, len(META_COLUMNS)) - 필터용 작은 정수 (선택지 목록은 manifest)
    manifest.json  모델·차원·건수·생성 시각·입력 sha·선택지 목록

실행:
    python -m search.embed                         # 전체 (이어 하기)
    python -m search.embed --repos 3b1b/manim,HKUDS/LightRAG --out data/search/semantic-sample
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from classify.labels import EVIDENCE_GRADES, REASON_LABELS
from search.index import LINK_ONLY, first_line, iter_records, shard_files
from search.stats import DATASET_VERSION, DEFAULT_DATA, SOURCES

SCHEMA_VERSION = "semantic_index/1"
# #8 비교로 고른 모델 (ADR-023). 바꾸면 전체를 다시 만든다 - 조각의 model 이 달라 건너뛰지 않는다.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_MAX_TOKENS = 256
DEFAULT_OUT = Path("data") / "search" / "semantic"
DEFAULT_MODEL_DIR = Path("data") / "models"
FULL_FUNCTION = "FULL_FUNCTION"
CODE_POLICIES = ("CODE_INCLUDED", LINK_ONLY)
TEXT_BODY = 0
TEXT_LINK = 1
# 토크나이저에 넣기 전 글자 상한. 코드는 토큰 하나가 대개 3글자 이상이라 256 토큰에는 넉넉하다.
MAX_CHARS = 4000
# meta.npy 칸 순서. 값은 manifest 의 선택지 목록 위치(없음 = -1), 연도는 그대로(없음 = -1).
META_COLUMNS = ("repo", "year", "reason", "grade", "source", "code_policy", "text_kind")
BATCH = 32


class EmbedError(RuntimeError):
    """입력이 없거나 조각·manifest 가 기대와 다르다."""


class Encoder(Protocol):
    """임베딩 모델. 테스트는 결정적인 가짜를 넣는다."""

    name: str
    dim: int
    max_tokens: int

    def passages(self, texts: Sequence[str]) -> np.ndarray: ...

    def query(self, text: str) -> np.ndarray: ...


class FastEmbedEncoder:
    """fastembed(ONNX Runtime) 모델. torch 는 쓰지 않는다 (스마트 앱 컨트롤)."""

    def __init__(
        self,
        name: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_dir: Path = DEFAULT_MODEL_DIR,
        threads: int | None = None,
    ) -> None:
        from fastembed import TextEmbedding  # 선택 의존성 "embed"

        cache_dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.max_tokens = max_tokens
        self._model = TextEmbedding(name, cache_dir=str(cache_dir), threads=threads)
        # 모델 기본 상한(jina 8192)은 CPU 에서 너무 느리다 - 토크나이저 자르기를 줄인다.
        # parallel 을 쓰면 하위 프로세스가 모델을 새로 읽어 이 설정을 잃는다 - 쓰지 않는다.
        self._model.model.tokenizer.enable_truncation(max_length=max_tokens)
        self.dim = int(self._model.embedding_size)

    def passages(self, texts: Sequence[str]) -> np.ndarray:
        vecs = np.asarray(list(self._model.passage_embed(list(texts), batch_size=BATCH)))
        return normalize(vecs.astype(np.float32))

    def query(self, text: str) -> np.ndarray:
        vec = np.asarray(next(iter(self._model.query_embed(text))), dtype=np.float32)
        return normalize(vec[None, :])[0]


def normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1.0, norms)


def doc_text(record: dict[str, Any]) -> tuple[str, int]:
    """레코드 → (임베딩할 텍스트, TEXT_BODY | TEXT_LINK). 모듈 설명 참고."""
    head = f"{record.get('function_name') or ''}\n{record['file_path']}\n"
    body = record.get("deleted_body")
    if record.get("code_policy") == LINK_ONLY or not body:
        commit = first_line((record.get("context") or {}).get("commit_message")) or ""
        return head + commit, TEXT_LINK
    return (head + body)[:MAX_CHARS], TEXT_BODY


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(functools.partial(handle.read, 1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _index(values: Sequence[str], value: str | None) -> int:
    return values.index(value) if value in values else -1


def meta_row(record: dict[str, Any], text_kind: int) -> tuple[int, ...]:
    """repo 칸은 조각 안에서는 0 이다 - 합칠 때 저장소 목록 위치로 바꾼다 (조각 = 저장소 하나)."""
    reason = record.get("reason") or {}
    year = (record.get("author_date") or "")[:4]
    return (
        0,
        int(year) if year.isdigit() else -1,
        _index(REASON_LABELS, reason.get("label")),
        _index(EVIDENCE_GRADES, reason.get("evidence_grade")),
        _index(SOURCES, (record.get("classification") or {}).get("source")),
        _index(CODE_POLICIES, record.get("code_policy")),
        text_kind,
    )


def part_path(out: Path, shard: Path) -> Path:
    return out / "parts" / (shard.name.removesuffix(".jsonl.gz") + ".npz")


def read_part_info(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with np.load(path) as part:
            return json.loads(str(part["info"]))
    except (OSError, ValueError, KeyError):
        return None  # 깨진 조각은 다시 만든다


def encode_sorted(encoder: Encoder, texts: Sequence[str]) -> np.ndarray:
    """길이순으로 묶어 넣고 원래 순서로 돌려준다 - 묶음 안 패딩이 줄어 빨라진다. float16."""
    vecs = np.zeros((len(texts), encoder.dim), dtype=np.float16)
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    step = BATCH * 8
    for start in range(0, len(order), step):
        chunk = order[start : start + step]
        vecs[chunk] = encoder.passages([texts[i] for i in chunk]).astype(np.float16)
    return vecs


def embed_shard(shard: Path, encoder: Encoder, sha: str, out: Path) -> dict[str, Any]:
    """jsonl.gz 하나 → 조각 하나."""
    ids: list[str] = []
    texts: list[str] = []
    metas: list[tuple[int, ...]] = []
    repos: set[str] = set()
    for record in iter_records(shard):
        if record.get("deletion_kind") != FULL_FUNCTION:
            continue
        text, kind = doc_text(record)
        ids.append(record["id"])
        texts.append(text)
        metas.append(meta_row(record, kind))
        repos.add(record["repo"])
    if len(repos) > 1:
        raise EmbedError(f"{shard.name} 에 저장소가 여러 개다: {sorted(repos)}")
    vecs = encode_sorted(encoder, texts)
    info = {
        "shard": shard.name,
        "sha256": sha,
        "model": encoder.name,
        "max_tokens": encoder.max_tokens,
        "dim": encoder.dim,
        "repo": next(iter(repos), None),
        "count": len(ids),
    }
    path = part_path(out, shard)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(
        tmp,
        vectors=vecs,
        ids=np.array(ids, dtype="U64"),
        meta=np.array(metas, dtype=np.int16).reshape(-1, len(META_COLUMNS)),
        info=np.array(json.dumps(info)),
    )
    os.replace(tmp, path)
    return info


def expected_counts(dataset_dir: Path) -> dict[str, int]:
    """repos.csv 의 full_function - 진행률·남은 시간 계산용. 없으면 빈 dict."""
    import csv

    path = dataset_dir / "repos.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["repo"]: int(row["full_function"]) for row in csv.DictReader(handle)}


def shard_repo(shard: Path) -> str:
    """`owner__name-00000-of-00001.jsonl.gz` → `owner/name`."""
    stem = shard.name.removesuffix(".jsonl.gz").rsplit("-", 3)[0]
    return stem.replace("__", "/", 1)


def build(
    data_dir: Path,
    out: Path,
    encoder: Encoder,
    repos: Iterable[str] | None = None,
    counts: dict[str, int] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """조각을 만들고(끝난 것은 건너뜀) 합친다. 반환값은 manifest."""
    shards = shard_files(data_dir)
    if repos is not None:
        wanted = set(repos)
        shards = [s for s in shards if shard_repo(s) in wanted]
        missing = wanted - {shard_repo(s) for s in shards}
        if missing:
            raise EmbedError(f"{data_dir} 에 없는 저장소: {sorted(missing)}")
    counts = counts or {}
    total = sum(counts.get(shard_repo(s), 0) for s in shards) or None
    started = time.perf_counter()
    done = embedded = 0
    shas: dict[str, str] = {}
    for number, shard in enumerate(shards, 1):
        sha = shas[shard.name] = file_sha256(shard)
        info = read_part_info(part_path(out, shard))
        fresh = info is not None and (info["sha256"], info["model"], info["max_tokens"]) == (
            sha,
            encoder.name,
            encoder.max_tokens,
        )
        if fresh:
            done += info["count"]
            log(f"[{number}/{len(shards)}] {shard.name} 건너뜀 ({info['count']:,}건, 이미 있음)")
            continue
        t0 = time.perf_counter()
        info = embed_shard(shard, encoder, sha, out)
        done += info["count"]
        embedded += info["count"]
        took = time.perf_counter() - t0
        log(_progress(number, len(shards), shard, info["count"], took, done, embedded, total,
                      time.perf_counter() - started))  # fmt: skip
    manifest = merge(out, shards, shas, encoder)
    manifest["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    return manifest


def _progress(
    number: int,
    shards: int,
    shard: Path,
    count: int,
    took: float,
    done: int,
    embedded: int,
    total: int | None,
    elapsed: float,
) -> str:
    rate = count / took if took else 0.0
    line = f"[{number}/{shards}] {shard.name} {count:,}건 {took:.0f}초 ({rate:.1f}건/초)"
    if total:
        line += f" | 누적 {done:,}/{total:,} ({done / total:.1%})"
        if embedded and done < total:
            remaining = (total - done) * elapsed / embedded
            line += f" | 남은 시간 약 {_duration(remaining)}"
    return line


def _duration(seconds: float) -> str:
    return f"{seconds / 3600:.1f}시간" if seconds >= 3600 else f"{seconds / 60:.0f}분"


def merge(out: Path, shards: Sequence[Path], shas: dict[str, str], encoder: Encoder) -> dict:
    """조각을 shards 순서로 합친다. 다른 모델·상한의 조각이 섞여 있으면 멈춘다."""
    parts = []
    for shard in shards:
        path = part_path(out, shard)
        info = read_part_info(path)
        if info is None or info["sha256"] != shas[shard.name]:
            raise EmbedError(f"{path} 가 없거나 입력과 다르다 - 다시 돌린다")
        if (info["model"], info["max_tokens"]) != (encoder.name, encoder.max_tokens):
            raise EmbedError(f"{path} 의 모델·상한이 다르다: {info['model']} {info['max_tokens']}")
        parts.append((path, info))
    repos = sorted({info["repo"] for _, info in parts if info["repo"]})
    total = sum(info["count"] for _, info in parts)
    tmp = {name: out / f"{name}.tmp.npy" for name in ("vectors", "ids", "meta")}
    vectors = np.lib.format.open_memmap(
        tmp["vectors"], mode="w+", dtype=np.float16, shape=(total, encoder.dim)
    )
    ids = np.empty(total, dtype="U64")
    meta = np.empty((total, len(META_COLUMNS)), dtype=np.int16)
    row = 0
    for path, info in parts:
        with np.load(path) as part:
            n = info["count"]
            vectors[row : row + n] = part["vectors"]
            ids[row : row + n] = part["ids"]
            meta[row : row + n] = part["meta"]
            if info["repo"]:
                meta[row : row + n, 0] = repos.index(info["repo"])
            row += n
    vectors.flush()
    del vectors
    np.save(tmp["ids"], ids)
    np.save(tmp["meta"], meta)
    for name, path in tmp.items():
        os.replace(path, out / f"{name}.npy")
    input_sha = hashlib.sha256(
        "".join(f"{name}:{shas[name]}\n" for name in sorted(shas)).encode()
    ).hexdigest()
    years = meta[:, 1][meta[:, 1] >= 0]
    manifest = {
        "schema": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "model": encoder.name,
        "dim": encoder.dim,
        "max_tokens": encoder.max_tokens,
        "count": total,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "input_sha256": input_sha,
        "input_files": len(shas),
        "meta_columns": list(META_COLUMNS),
        "repos": repos,
        "reasons": list(REASON_LABELS),
        "grades": list(EVIDENCE_GRADES),
        "sources": list(SOURCES),
        "code_policies": list(CODE_POLICIES),
        "year_min": int(years.min()) if years.size else None,
        "year_max": int(years.max()) if years.size else None,
        "text_link_count": int((meta[:, 6] == TEXT_LINK).sum()),
        "bytes": sum((out / f"{n}.npy").stat().st_size for n in tmp),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검색 2단계 임베딩 생성 (#193, 이어 하기)")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="jsonl.gz 폴더")
    parser.add_argument("--dataset", type=Path, default=Path("datasets") / DATASET_VERSION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="출력 폴더")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--threads", type=int, default=None, help="ONNX 스레드 (기본 전부)")
    parser.add_argument("--repos", default=None, help="쉼표로 구분한 저장소만 (표본 검증용)")
    args = parser.parse_args(argv)
    log = functools.partial(print, flush=True)
    try:
        encoder = FastEmbedEncoder(args.model, args.max_tokens, args.model_dir, args.threads)
        repos = [r.strip() for r in args.repos.split(",")] if args.repos else None
        manifest = build(
            args.data, args.out, encoder, repos, expected_counts(args.dataset), log=log
        )
    except EmbedError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    print(
        f"완료: {args.out} - {manifest['count']:,}건, {manifest['dim']}차원, "
        f"{manifest['bytes'] / 1e9:.2f} GB, {manifest['elapsed_seconds'] / 60:.1f}분"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
