"""임베딩 모델 후보 비교 (#8). 담당: 성제

표본: Release `dataset-v0.9` 에서 저장소 25개(시드)를 고르고 저장소마다 FULL_FUNCTION·CODE_INCLUDED
2건씩(같은 시드, 저수지 표집) = 50건. 질의 10개는 QUERIES (자연어, 표본을 보고 쓴 것 - 아래).
후보마다 같은 텍스트(`search.embed.doc_text`)·같은 토큰 상한으로 임베딩한다.

잰다:
    모델 크기(디스크), 차원, 질의 10개 상위 5개 (정성 비교용 표 - 판정은 사람이 한다)
    CPU 시간/함수: 같은 저장소 25개에서 40건씩 뽑은 처리량 표본 1,000건을 `search.embed` 와 같은
    방식(길이순 묶음)으로 한 번 임베딩한 시간 ÷ 1,000 (모델 로드 제외). 처음 잰 50건 × 3회(길이순
    아님)는 72만 건 시간을 2~3배 크게 잡았다 - 묶음 안 패딩 때문이다
    72만 건 예상 시간 = 723,087 × 시간/함수

질의 쓰는 법: 표본 50건 함수를 읽고, 그중 한 건이 하는 일을 이름·경로 낱말을 되도록 피해
자연어로 적었다. `target` 은 그 질의를 쓸 때 떠올린 함수다 - "의도한 함수가 몇 위인가" 는
참고 지표일 뿐 정답 라벨이 아니다
(다른 함수가 더 맞을 수 있다. 상위 5개 관련성 판정은 사람이 한다).

실행:
    python -m eval.embedding_bench --sample-only        # 표본만 뽑아 보기
    python -m eval.embedding_bench                      # 후보 전부
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from search.embed import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_DIR,
    FULL_FUNCTION,
    FastEmbedEncoder,
    doc_text,
    encode_sorted,
)
from search.index import iter_records, shard_files
from search.stats import DEFAULT_DATA

SEED = 20261030
REPOS = 25
PER_REPO = 2
THROUGHPUT_PER_REPO = 40
FULL_TOTAL = 723_087
CANDIDATES = (
    "jinaai/jina-embeddings-v2-base-code",
    "ibm-granite/granite-embedding-small-english-r2",
    "BAAI/bge-small-en-v1.5",
)
DEFAULT_OUT = Path("docs") / "reports" / "embedding_model_choice.json"
SAMPLE_OUT = Path("data") / "bench" / "embedding_sample.jsonl"
# (질의, 의도한 함수 id). 표본(SEED)을 뽑은 뒤 썼다 - 모듈 설명 참고.
QUERIES: tuple[tuple[str, str], ...] = (
    (
        "send a signal to stop a background daemon using the pid stored in a file",
        "b115917a-47c7-57d4-8a4b-017c2bbdaac7",
    ),
    (
        "reject a proxy host that points to a private or internal network address",
        "5e36720b-292a-563a-9b95-44681d87038c",
    ),
    ("linear blend between two points by a fraction", "1acfdab1-0d9d-5e0c-aa92-1f270765dbf4"),
    (
        "read the request history csv file and return its text",
        "701b9b21-b3ae-5d2b-b8ce-0629d4fbb4bc",
    ),
    (
        "keep only the tasks that match the include labels and drop excluded ones",
        "01f411da-9792-5ed3-acd7-9c5113bdc294",
    ),
    (
        "rename pretrained checkpoint weight keys to our model's parameter names",
        "6a140be2-640d-53fd-84b1-6a12ee997b63",
    ),
    (
        "build SQL that pulls the year or month out of a date column",
        "bd96082d-a926-549a-a274-0c93d5fbc216",
    ),
    (
        "turn on activation recomputation for the encoder to save memory",
        "0c7d2208-f1e1-5bf1-b05d-08d4c3c5f71f",
    ),
    (
        "learn a dictionary of atoms for sparse coding from data in mini batches",
        "7d0ff5fd-88fc-5963-809e-39e7e7571111",
    ),
    (
        "download a package archive once and reuse a locally cached copy during install",
        "098d6c04-cf2e-513a-a717-41db11b37c66",
    ),
)


def sample(data_dir: Path, seed: int = SEED) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(질의 표본 50건, 처리량 표본 1,000건). 같은 저장소 25개에서 저수지 표집 두 개를 같이 돈다.
    처리량 표본은 LINK_ONLY(본문 없음)도 넣는다 - 전체 실행과 같은 길이 분포로 재려고."""
    rng = random.Random(seed)
    # 처리량 표본은 따로 돌린다 - 같은 rng 를 쓰면 질의 표본이 바뀌어 QUERIES 의 target 이 빠진다
    big_rng = random.Random(seed + 1)
    shards = rng.sample(shard_files(data_dir), REPOS)
    picked: list[dict[str, Any]] = []
    throughput: list[dict[str, Any]] = []
    for shard in sorted(shards):
        small: list[dict[str, Any]] = []
        big: list[dict[str, Any]] = []
        seen_small = seen_big = 0
        for record in iter_records(shard):
            if record["deletion_kind"] != FULL_FUNCTION:
                continue
            seen_big += 1
            if len(big) < THROUGHPUT_PER_REPO:
                big.append(record)
            elif (j := big_rng.randrange(seen_big)) < THROUGHPUT_PER_REPO:
                big[j] = record
            if not record.get("deleted_body"):
                continue
            seen_small += 1
            if len(small) < PER_REPO:
                small.append(record)
            elif (j := rng.randrange(seen_small)) < PER_REPO:
                small[j] = record
        picked.extend(small)
        throughput.extend(big)
    return picked, throughput


def model_bytes(model_dir: Path, name: str) -> int:
    """fastembed 캐시 폴더 이름에 모델 이름 조각이 들어간다 (`models--org--name`)."""
    key = name.split("/")[-1].lower()
    return sum(
        p.stat().st_size
        for d in model_dir.iterdir()
        if key in d.name.lower()
        for p in d.rglob("*")
        if p.is_file() and not p.is_symlink()
    )


def bench(
    name: str,
    records: list[dict[str, Any]],
    throughput: list[dict[str, Any]],
    model_dir: Path,
    max_tokens: int,
) -> dict:
    t0 = time.perf_counter()
    encoder = FastEmbedEncoder(name, max_tokens, model_dir)
    load = time.perf_counter() - t0
    texts = [doc_text(r)[0] for r in throughput]
    t0 = time.perf_counter()
    encode_sorted(encoder, texts)
    per = (time.perf_counter() - t0) / len(texts)
    docs = encode_sorted(encoder, [doc_text(r)[0] for r in records]).astype("float32")
    ids = [r["id"] for r in records]
    queries = []
    for query, target in QUERIES:
        scores = docs @ encoder.query(query)
        order = scores.argsort()[::-1]
        ranked = [ids[i] for i in order]
        queries.append(
            {
                "query": query,
                "target": target,
                "target_rank": ranked.index(target) + 1 if target in ranked else None,
                "top5": [{"id": ids[i], "score": round(float(scores[i]), 4)} for i in order[:5]],
            }
        )
    ranks = [q["target_rank"] for q in queries if q["target_rank"]]
    return {
        "model": name,
        "dim": encoder.dim,
        "max_tokens": max_tokens,
        "bytes": model_bytes(model_dir, name),
        "load_seconds": round(load, 2),
        "seconds_per_function": round(per, 5),
        "estimated_hours_full": round(per * FULL_TOTAL / 3600, 2),
        "target_mrr": round(sum(1 / r for r in ranks) / len(QUERIES), 3) if QUERIES else None,
        "target_top1": sum(r == 1 for r in ranks),
        "target_top5": sum(r <= 5 for r in ranks),
        "queries": queries,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="임베딩 모델 후보 비교 (#8)")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--max-tokens", type=int, nargs="+", default=[DEFAULT_MAX_TOKENS])
    parser.add_argument("--models", nargs="+", default=list(CANDIDATES))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sample-only", action="store_true", help="표본만 뽑아 data/bench 에")
    args = parser.parse_args(argv)
    records, throughput = sample(args.data)
    SAMPLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLE_OUT.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"표본 {len(records)}건 → {SAMPLE_OUT}, 처리량 표본 {len(throughput)}건", flush=True)
    if args.sample_only:
        return 0
    results = []
    for name in args.models:
        for max_tokens in args.max_tokens:
            print(f"{name} (상한 {max_tokens} 토큰)…", flush=True)
            result = bench(name, records, throughput, args.model_dir, max_tokens)
            print(
                f"  {result['dim']}차원 {result['bytes'] / 1e6:.0f}MB "
                f"{result['seconds_per_function'] * 1000:.1f}ms/함수 "
                f"전체 약 {result['estimated_hours_full']}시간 MRR {result['target_mrr']}",
                flush=True,
            )
            results.append(result)
    payload = {
        "seed": SEED,
        "sample": [
            {
                "id": r["id"],
                "repo": r["repo"],
                "function_name": r["function_name"],
                "file_path": r["file_path"],
            }
            for r in records
        ],  # fmt: skip
        "full_total": FULL_TOTAL,
        "throughput_sample": len(throughput),
        "results": results,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
