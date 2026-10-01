"""필터 정밀도 평가 표본 추출 — 통과 100 + 이동 50 + 사소한 삭제 50 (이슈 #89, §10.1).
담당: 성제 (sj)

무엇을:
    #82 최종 조립 결과(`pipeline/assemble.py` 출력, `filter_status` 가 붙은 JSONL)에서 층마다
    무작위로 뽑는다 — 통과(KEPT) 100, 제외는 사유별로 NOISE_MOVE 50 + NOISE_TRIVIAL 50.
    200건을 섞어 두 파일로 낸다.

        <out-dir>/records.jsonl   판정용. 판정 CLI(`tools/filter_judge_cli.py`)가 읽는다.
                                  `filter_status`·`filter_rule_version`·`filter_evidence` 가 없다
        <out-dir>/key.json        정답 대조용. 표본 각 건의 `filter_status` 와 모집단 크기.
                                  집계(`eval/filter_precision.py`)만 읽는다. 판정자는 열지 않는다

사전 등록(`docs/evaluation.md` "필터 정밀도 사전 등록")의 모집단·표본 규칙을 구현한다:
    - 모집단은 조립 결과 전체. 다만 선정 CSV 에서 `recent_only=true` 인 저장소는
      `author_date` ≥ 2015-01-01T00:00:00Z 레코드만 넣는다 (#85 와 같은 기준). 채굴 구간은 커미터
      날짜로 잘렸으므로(`pipeline/walk.py`, #148) 구간 안 커밋에도 그보다 이른 author_date 가 있다
    - 층(`STRATUM_SIZES`)마다 저수지 표본 추출(Algorithm R). 조립 결과가 10GB 를 넘어 한 번에
      올리지 않는다. 제외를 사유로 층화하는 이유: 단순 무작위면 제외의 약 83%가 NOISE_TRIVIAL 이라
      이동이 20건 안팎만 뽑히고, #80 이 #89 결과로 미룬 이동 판단(이동+리네임 0.9 미만, 커밋 간
      이동)의 근거가 되지 못한다
    - 층에 없는 `filter_status`(아직 구현되지 않은 NOISE_RENAME 등)가 나오면 멈춘다. 사전 등록이
      다루지 않은 층을 조용히 빼거나 섞지 않는다
    - 섞기는 같은 난수 생성기로 저수지 다음에 한다. 시드가 같고 입력 파일이 같으면 같은 표본이다.
      입력이 같은지는 `key.json` 의 `assembled_sha256` 으로 확인한다 (읽으면서 같이 계산한다)

판정용 레코드에서 필터 판정을 가리는 방법:
    - `filter_status`·`filter_rule_version`·`filter_evidence` 키를 넣지 않는다
    - `deleted_line_count` 는 **모든** 건에 넣는다. NOISE_TRIVIAL 의 `filter_evidence.line_count`
      를 그 건에만 보여 주면 그 자체가 표시가 된다. 값은 `filter_rules.md` 와 같은
      `len(deleted_body.splitlines())` 다
    - `similar_function` 은 NOISE_MOVE 의 `filter_evidence`(같은 커밋의 비슷한 함수)다. 이것은
      제외 건에만 있어 가릴 수 없다 — 사전 등록 "알려진 한계"에 적었다

사용:
    python -m eval.filter_precision_sample --assembled data/assembled/filtered.jsonl \\
        --selection-csv docs/repo_final20_v2.csv --out-dir data/filter_precision
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 사전 등록 값 (docs/evaluation.md "필터 정밀도 사전 등록"). 바꾸면 그 절을 먼저 고친다.
DEFAULT_SEED = 20261001
# #85 와 같은 기준. 선정 단계 `pipeline/select_repos.py:RECENT_ONLY_BEFORE_YEAR` 의 1월 1일 UTC
RECENT_ONLY_SINCE = datetime(2015, 1, 1, tzinfo=UTC)

KEPT = "KEPT"
NOISE_MOVE = "NOISE_MOVE"
NOISE_TRIVIAL = "NOISE_TRIVIAL"
# 층 = filter_status. 층별 표본 크기 (사전 등록)
STRATUM_SIZES: dict[str, int] = {KEPT: 100, NOISE_MOVE: 50, NOISE_TRIVIAL: 50}
STRATA = tuple(STRATUM_SIZES)
EXCLUDED_STRATA = tuple(stratum for stratum in STRATA if stratum != KEPT)

RECORDS_FILENAME = "records.jsonl"
KEY_FILENAME = "key.json"
# 판정용 파일에 절대 들어가면 안 되는 키. 판정 CLI 도 같은 목록으로 입력을 거부한다.
HIDDEN_KEYS = frozenset({"filter_status", "filter_rule_version", "filter_evidence"})
# NOISE_MOVE `filter_evidence` 에서 판정자에게 보여 줄 키 (docs/filter_rules.md "제외 레코드 보존")
SIMILAR_FUNCTION_KEYS = ("file_path", "function_name", "start_line", "end_line", "similarity")
# 판정용 레코드의 키 (순서 그대로). 사전 등록 "판정자가 보는 것"
JUDGE_FIELDS = (
    "sample_id",
    "record_id",
    "repo",
    "commit_sha",
    "source_url",
    "file_path",
    "function_name",
    "function_signature",
    "deletion_kind",
    "deleted_line_count",
    "deleted_body",
    "commit_message",
    "similar_function",
)


class SampleError(ValueError):
    """표본을 만들 수 없다. 출력 파일은 쓰지 않았다."""


def stratum_of(filter_status: object) -> str:
    """층 이름은 `filter_status` 그대로다. 사전 등록 층(`STRATA`) 밖의 값은 거부한다."""
    if isinstance(filter_status, str) and filter_status in STRATUM_SIZES:
        return filter_status
    raise SampleError(
        f"filter_status {filter_status!r} 는 사전 등록 층({', '.join(STRATA)})이 아니다"
    )


def load_recent_only(path: Path) -> dict[str, bool]:
    """선정 CSV 의 repo → recent_only. `true`·`false`·빈 값만 받는다 (`pipeline/run.py` 와 같다)."""
    result: dict[str, bool] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for line_no, row in enumerate(csv.DictReader(handle), start=2):
            repo = (row.get("repo") or "").strip()
            if not repo:
                raise SampleError(f"{path}:{line_no}: repo 가 비었다")
            text = (row.get("recent_only") or "").strip().lower()
            if text not in ("true", "false", ""):
                raise SampleError(f"{path}:{line_no}: recent_only 는 true·false·빈 값이어야 한다")
            result[repo] = text == "true"
    if not result:
        raise SampleError(f"{path}: 저장소가 없다")
    return result


def parse_author_date(value: object) -> datetime:
    """ISO 8601 → aware datetime. 시간대가 없거나 읽을 수 없으면 거부한다 (추측하지 않는다)."""
    if not isinstance(value, str):
        raise SampleError(f"author_date {value!r} 가 문자열이 아니다")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SampleError(f"author_date {value!r} 를 읽을 수 없다") from error
    if parsed.tzinfo is None:
        raise SampleError(f"author_date {value!r} 에 시간대가 없다")
    return parsed


def in_population(record: Mapping[str, Any], recent_only: Mapping[str, bool]) -> bool:
    """사전 등록 모집단에 드나. 선정 CSV 에 없는 저장소는 거부한다."""
    repo = record.get("repo")
    if repo not in recent_only:
        raise SampleError(f"repo {repo!r} 가 선정 CSV 에 없다")
    if not recent_only[repo]:
        return True
    return parse_author_date(record.get("author_date")) >= RECENT_ONLY_SINCE


def deleted_line_count(record: Mapping[str, Any]) -> int:
    """`docs/filter_rules.md` NOISE_TRIVIAL 과 같은 줄 수 — 모든 건에 같은 식으로 붙인다."""
    return len(str(record.get("deleted_body") or "").splitlines())


def similar_function(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """NOISE_MOVE 의 이동 목적지 함수. 다른 사유·통과 건은 없다(None)."""
    if record.get("filter_status") != NOISE_MOVE:
        return None
    evidence = record.get("filter_evidence") or {}
    return {key: evidence.get(key) for key in SIMILAR_FUNCTION_KEYS}


def judge_record(record: Mapping[str, Any], sample_id: str) -> dict[str, Any]:
    """판정용 레코드 1건. `HIDDEN_KEYS` 는 여기서 떨어진다."""
    values = {
        **{key: record.get(key) for key in JUDGE_FIELDS},
        "sample_id": sample_id,
        "record_id": record.get("id"),
        "deleted_line_count": deleted_line_count(record),
        "similar_function": similar_function(record),
    }
    return {key: values[key] for key in JUDGE_FIELDS}


@dataclass
class Reservoir:
    """크기 `size` 의 저수지 (Algorithm R). `seen` 은 지금까지 넣으려 한 건수다."""

    size: int
    items: list[dict[str, Any]] = field(default_factory=list)
    seen: int = 0

    def offer(self, item: dict[str, Any], rng: random.Random) -> None:
        if self.seen < self.size:
            self.items.append(item)
        else:
            slot = rng.randrange(self.seen + 1)
            if slot < self.size:
                self.items[slot] = item
        self.seen += 1


@dataclass
class SampleResult:
    """표본과 모집단 집계. `records` 는 섞인 순서, `key` 는 `key.json` 내용이다."""

    records: list[dict[str, Any]]
    key: dict[str, Any]


def iter_lines(path: Path, digest: Any) -> Iterable[tuple[int, dict[str, Any]]]:
    """(줄 번호, 객체). 바이트로 읽어 `b"\\n"` 에서만 나눈다 (#146 — U+2028 에서 자르지 않는다)."""
    with path.open("rb") as handle:
        for line_no, raw in enumerate(handle, start=1):
            digest.update(raw)
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError as error:
                raise SampleError(f"{path}:{line_no}: JSON 이 아니다: {error}") from error
            if not isinstance(row, dict):
                raise SampleError(f"{path}:{line_no}: JSON 객체가 아니다")
            yield line_no, row


def draw_sample(
    assembled: Path,
    recent_only: Mapping[str, bool],
    seed: int = DEFAULT_SEED,
    sizes: Mapping[str, int] = STRATUM_SIZES,
) -> SampleResult:
    """조립 결과를 한 번 훑어 층마다 `sizes[층]` 건을 뽑고 섞는다.

    난수 생성기 하나를 모든 층이 파일 순서대로 나눠 쓴다. 입력·시드가 같으면 결과가 같다.
    """
    if set(sizes) != set(STRATA):
        raise SampleError(f"층은 {STRATA} 이어야 한다: {sorted(sizes)}")
    rng = random.Random(seed)
    reservoirs = {stratum: Reservoir(sizes[stratum]) for stratum in STRATA}
    out_of_window: Counter[str] = Counter()
    versions: set[str] = set()
    digest = hashlib.sha256()

    for line_no, row in iter_lines(assembled, digest):
        try:
            stratum = stratum_of(row.get("filter_status"))
            if not in_population(row, recent_only):
                out_of_window[str(row.get("repo"))] += 1
                continue
        except SampleError as error:
            raise SampleError(f"{assembled}:{line_no}: {error}") from None
        if not row.get("id"):
            raise SampleError(f"{assembled}:{line_no}: id 가 없다")
        versions.add(str(row.get("filter_rule_version")))
        reservoirs[stratum].offer(row, rng)

    for stratum, reservoir in reservoirs.items():
        if reservoir.seen < reservoir.size:
            raise SampleError(
                f"{stratum} 모집단이 {reservoir.seen}건 — 표본 {reservoir.size}건보다 적다"
            )
    if len(versions) != 1:
        raise SampleError(f"filter_rule_version 이 섞였다: {sorted(versions)}")

    picked = [(stratum, row) for stratum in STRATA for row in reservoirs[stratum].items]
    rng.shuffle(picked)
    width = len(str(len(picked)))
    records: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for position, (stratum, row) in enumerate(picked, start=1):
        sample_id = f"fp-{position:0{width}d}"
        records.append(judge_record(row, sample_id))
        items.append(
            {
                "sample_id": sample_id,
                "record_id": row["id"],
                "repo": row.get("repo"),
                "stratum": stratum,
                "filter_status": row["filter_status"],
                "deletion_kind": row.get("deletion_kind"),
            }
        )

    population = {stratum: reservoirs[stratum].seen for stratum in STRATA}
    key = {
        "issue": 89,
        "seed": seed,
        "sample_sizes": {stratum: sizes[stratum] for stratum in STRATA},
        "assembled": str(assembled),
        "assembled_sha256": digest.hexdigest(),
        "filter_rule_version": next(iter(versions)),
        "recent_only_since": RECENT_ONLY_SINCE.isoformat(),
        "population": population,
        "out_of_window_by_repo": dict(sorted(out_of_window.items())),
        "items": items,
    }
    return SampleResult(records, key)


def write_outputs(result: SampleResult, out_dir: Path, *, overwrite: bool = False) -> list[Path]:
    """판정용 파일과 키 파일을 쓴다. 이미 있으면 `overwrite` 없이는 거부한다.

    판정이 시작된 뒤 표본을 다시 뽑아 덮어쓰면 판정 파일이 다른 표본을 가리키게 된다.
    """
    records_path = out_dir / RECORDS_FILENAME
    key_path = out_dir / KEY_FILENAME
    existing = [path for path in (records_path, key_path) if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise SampleError(f"이미 있다: {names} — 다시 뽑으려면 --overwrite")
    out_dir.mkdir(parents=True, exist_ok=True)
    with records_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in result.records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    key_path.write_text(
        json.dumps(result.key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return [records_path, key_path]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.filter_precision_sample",
        description="필터 정밀도 평가 표본 — 통과 100 + 이동 50 + 사소한 삭제 50 (#89, §10.1).",
    )
    parser.add_argument("--assembled", type=Path, required=True, help="조립 결과 JSONL (#101)")
    parser.add_argument(
        "--selection-csv",
        type=Path,
        default=Path("docs/repo_final20_v2.csv"),
        help="저장소 선정 CSV (recent_only 열)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out-dir", type=Path, default=Path("data/filter_precision"))
    parser.add_argument("--overwrite", action="store_true", help="기존 표본 파일을 덮어쓴다")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8")
    try:
        result = draw_sample(args.assembled, load_recent_only(args.selection_csv), args.seed)
        paths = write_outputs(result, args.out_dir, overwrite=args.overwrite)
    except (SampleError, OSError) as error:
        print(f"표본 추출 실패: {error}", file=sys.stderr)
        return 1
    key = result.key
    print(
        f"표본 {len(result.records)}건 (시드 {key['seed']}) — 모집단 {key['population']}, "
        f"층별 표본 {key['sample_sizes']}, filter_rule_version {key['filter_rule_version']}",
        file=sys.stderr,
    )
    print(f"recent_only 구간 밖 제외: {key['out_of_window_by_repo']}", file=sys.stderr)
    for path in paths:
        print(f"썼다: {path}", file=sys.stderr)
    print("key.json 은 판정이 끝날 때까지 판정자에게 보이지 않는다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
