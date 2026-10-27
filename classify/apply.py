"""100개 저장소에 분류기 적용 - 모델만 전체, LLM 포함은 저장소별 표본 (#180). 담당: 희수

무엇을:
    조립·맥락 결합·사후 필터를 거친 레코드 파일(여러 개)을 한 줄씩 읽어, 분류 대상(FULL_FUNCTION·
    KEPT, `classify.sampling.is_eligible` - 500건 라벨을 뽑은 것과 같은 범위)에 분류기 라벨을
    붙인다. 게이트 2 판정(`docs/meetings/gate-2.md` "바뀐 계획")대로 두 버전을 쓴다:

        모델만     전체 대상. 학습 300건으로 학습 - val 52.0% 를 잰 그 모델 (`r1+m2`)
        LLM 포함   저장소별 표본만. 학습+검증 400건으로 학습 - test 60.0% 를 잰 그 모델
                   (`r1+m2+c3+llm:<모델>`)

    성능은 버전마다 그 숫자로만 말한다. 모델만 라벨에 60.0% 를 붙이지 않는다.

한 줄씩 읽고 쓰는 이유:
    저장소 20개 조립 결과만 13.6GB 다. `classify.classifier` CLI 는 레코드를 전부 메모리에 읽는다.

표본 (`--sample-per-repo`):
    저장소마다 `sha256(시드:record_id)` 가 가장 작은 N건. 파일을 읽는 순서·파일 나눔과 상관없이
    같은 시드면 같은 표본이다. 사람 확정 500건은 뺀다 - 그 건은 데이터셋에서 사람 라벨을 쓴다.

나눠 돌리기 (`--shard K/N`):
    LLM 은 건당 10~50초라 표본 3,000건을 한 프로세스로 부르면 하루가 넘는다. 대상 중
    `sha256(record_id) mod N == K` 인 것만 처리한다. N 개 프로세스로 캐시를 채운 뒤, 나누지 않고 한
    번 더 돌리면 API 없이 파일 하나가 나온다. 모델만 버전도 같은 방식으로 나눌 수 있다.

실행:
    python -m classify.apply --input <records_v0.8.jsonl> --input <records100_*.jsonl> \\
        --out data/classified/model_only.jsonl
    python -m classify.apply --input ... --llm --sample-per-repo 30 \\
        --out data/classified/llm_sample.jsonl [--shard 0/6]
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import sys
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from classify.baseline_llm import DEFAULT_PROVIDER, PROVIDERS, LlmBaseline, caller_from_env
from classify.baselines import record_id_of
from classify.classifier import (
    CANDIDATE_PROMPT_VERSION,
    DEFAULT_CACHE_DIR,
    TRAIN_SPLITS,
    Classifier,
    LlmCandidate,
    load_final_labels,
    train_model,
)
from classify.labels import MERGED_FILENAME, read_jsonl
from classify.sampling import is_eligible
from pipeline.select_repos import load_env_file

DEFAULT_LABELS = Path("datasets") / MERGED_FILENAME
DEFAULT_TRAINING_RECORDS = Path("datasets") / "labels" / "main500_records.jsonl"
# 이슈 #180 에 적은 시드. 바꾸면 다른 표본이다.
DEFAULT_SEED = 20261027
# 모델만은 val 52.0% 를 잰 학습 split, LLM 포함은 test 60.0% 를 잰 학습 split (classifier.py).
MODEL_ONLY_TRAINING = TRAIN_SPLITS["val"]
WITH_LLM_TRAINING = TRAIN_SPLITS["test"]


def iter_records(paths: Sequence[Path]) -> Iterator[dict[str, Any]]:
    """파일들을 한 줄씩 읽는다. `splitlines()` 를 쓰지 않는다 (U+2028, #146)."""
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def stable_key(seed: int | str, record_id: str) -> int:
    """시드와 record_id 로 정해지는 순서 키. 파일 순서와 상관없이 같은 값이다."""
    return int(hashlib.sha256(f"{seed}:{record_id}".encode()).hexdigest(), 16)


def in_shard(record_id: str, shard: tuple[int, int] | None) -> bool:
    """`--shard K/N` 에 속하나. 없으면 모두 속한다."""
    if shard is None:
        return True
    index, count = shard
    return int(hashlib.sha256(record_id.encode()).hexdigest(), 16) % count == index


def sample_per_repo(
    records: Iterator[dict[str, Any]], size: int, seed: int | str, exclude: set[str]
) -> list[dict[str, Any]]:
    """저장소마다 `stable_key` 가 가장 작은 `size` 건. 대상(`is_eligible`)만, `exclude` 는 뺀다.

    한 번 읽으며 저장소마다 크기 `size` 의 힙만 들고 있는다 - 메모리는 저장소 수 x size 다.
    결과는 저장소 이름, 그 안에서 키 순서다.
    """
    # 저장소마다 키가 가장 작은 size 건을 남긴다. heapq 는 최소 힙이라 키에 - 를 붙여 가장 큰
    # 키가 맨 앞에 오게 하고, 새 키가 그보다 작으면 바꾼다.
    heaps: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
    for record in records:
        record_id = record_id_of(record)
        if not is_eligible(record) or record_id in exclude:
            continue
        heap = heaps.setdefault(str(record.get("repo")), [])
        item = (-stable_key(seed, record_id), record_id, record)
        if len(heap) < size:
            heapq.heappush(heap, item)
        elif item[0] > heap[0][0]:
            heapq.heapreplace(heap, item)
    return [
        record for repo in sorted(heaps) for _key, _rid, record in sorted(heaps[repo], reverse=True)
    ]


def output_row(record: dict[str, Any], classifier: Classifier) -> dict[str, Any]:
    """출력 한 줄 - `record_id`·`repo` + §4.4 `reason` 그대로 (#83 이 붙인다)."""
    result = classifier.classify(record)
    return {
        "record_id": record_id_of(record),
        "repo": record.get("repo"),
        **result.to_schema_reason(),
    }


def build_classifier(
    splits: Sequence[str], labels_path: Path, records_path: Path, llm: LlmCandidate | None
) -> Classifier:
    """확정 라벨 500건 중 `splits` 의 레코드로 모델을 학습한다."""
    label_rows = read_jsonl(labels_path)
    split_of = {str(row["record_id"]): row.get("split") for row in label_rows}
    training = [r for r in read_jsonl(records_path) if split_of.get(record_id_of(r)) in splits]
    if not training:
        raise ValueError(f"학습 레코드가 없다 (split {splits})")
    return Classifier(model=train_model(training, load_final_labels(label_rows)), llm=llm)


def parse_shard(text: str | None) -> tuple[int, int] | None:
    """`K/N` -> (K, N). 0 <= K < N 이어야 한다."""
    if not text:
        return None
    index, _, count = text.partition("/")
    shard = (int(index), int(count))
    if not 0 <= shard[0] < shard[1]:
        raise ValueError(f"--shard {text}: 0 <= K < N 이어야 한다")
    return shard


def write_sample(args: argparse.Namespace) -> int:
    """표본 레코드(원문 그대로)를 `--out` 에 쓴다.

    LLM 을 여러 프로세스(`--shard`)로 나눠 부를 때 프로세스마다 큰 입력 전체를 다시 읽지 않게,
    표본만 작은 파일로 먼저 뽑는다. 그 파일을 `--input` 으로 같은 시드·크기를 주면 같은 표본이
    다시 뽑힌다 (저장소마다 키가 가장 작은 N건은 그 안에서도 그대로다).
    """
    exclude = set(load_final_labels(read_jsonl(args.labels)))
    picked = sample_per_repo(iter_records(args.input), args.sample_per_repo, args.seed, exclude)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in picked:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary.replace(args.out)
    repos = Counter(str(record.get("repo")) for record in picked)
    print(f"표본 {len(picked):,}건, 저장소 {len(repos)}개 (시드 {args.seed}) -> {args.out}")
    short = {repo: n for repo, n in repos.items() if n < args.sample_per_repo}
    if short:
        print(f"  {args.sample_per_repo}건이 안 되는 저장소 {len(short)}개: {short}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자. 실행 방법은 모듈 독스트링 "실행" 절."""
    parser = argparse.ArgumentParser(
        prog="python -m classify.apply",
        description="100개 저장소에 분류기 적용 - 모델만 전체, LLM 포함 저장소별 표본 (#180).",
    )
    parser.add_argument("--input", type=Path, action="append", required=True, help="여러 번")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--llm", action="store_true", help="LLM 포함 버전 (표본에만)")
    parser.add_argument("--sample-per-repo", type=int, default=0, help="저장소별 표본 크기")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--shard", default=None, help="K/N - 대상의 N 분의 1 만 처리")
    parser.add_argument(
        "--sample-only",
        action="store_true",
        help="분류하지 않고 표본 레코드만 --out 에 쓴다 (큰 입력을 한 번만 읽으려고)",
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--training-records", type=Path, default=DEFAULT_TRAINING_RECORDS)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """분류하고 쓴 뒤 건수·분포·버전을 보고한다."""
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        shard = parse_shard(args.shard)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if args.llm and args.sample_per_repo <= 0:
        # 게이트 2 판정의 "바뀐 계획": LLM 포함 버전은 표본에만 쓴다.
        print(
            "--llm 은 --sample-per-repo 와 함께만 쓴다 (LLM 포함 버전은 표본에만).", file=sys.stderr
        )
        return 2
    missing = [path for path in args.input if not path.is_file()]
    if missing:
        print(f"입력 파일이 없다: {missing}", file=sys.stderr)
        return 2
    if args.sample_only:
        if args.sample_per_repo <= 0 or args.llm:
            print("--sample-only 는 --sample-per-repo 와 함께, --llm 없이 쓴다.", file=sys.stderr)
            return 2
        return write_sample(args)

    llm = None
    if args.llm:
        load_env_file(args.env_file)
        caller = caller_from_env(args.provider)
        if caller is None:
            return 2
        runner = LlmBaseline(
            caller,
            model=PROVIDERS[args.provider].model,
            cache_dir=args.cache_dir,
            prompt_version=CANDIDATE_PROMPT_VERSION,
        )
        llm = LlmCandidate(runner)
    splits = WITH_LLM_TRAINING if args.llm else MODEL_ONLY_TRAINING
    classifier = build_classifier(splits, args.labels, args.training_records, llm)

    if args.sample_per_repo > 0:
        exclude = set(load_final_labels(read_jsonl(args.labels)))
        targets: Iterator[dict[str, Any]] = iter(
            sample_per_repo(iter_records(args.input), args.sample_per_repo, args.seed, exclude)
        )
    else:
        targets = (record for record in iter_records(args.input) if is_eligible(record))

    started = time.monotonic()
    labels: Counter[str] = Counter()
    grades: Counter[str] = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in targets:
            if not in_shard(record_id_of(record), shard):
                continue
            row = output_row(record, classifier)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            labels[row["label"]] += 1
            grades[row["evidence_grade"]] += 1
    temporary.replace(args.out)

    total = sum(labels.values())
    print(f"분류기 {classifier.version} - {total:,}건 ({time.monotonic() - started:,.0f}초)")
    print(f"  학습 split: {'+'.join(splits)} / 출력: {args.out}")
    print("  라벨: " + ", ".join(f"{k} {v:,}" for k, v in labels.most_common()))
    print("  등급: " + ", ".join(f"{k} {v:,}" for k, v in grades.most_common()))
    if llm is not None:
        print(f"  LLM: API {llm.runner.calls}회 / 캐시 {llm.runner.cache_hits}회")
        if llm.failures:
            print("  LLM 실패: " + ", ".join(f"{k} {v}건" for k, v in llm.failures.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
