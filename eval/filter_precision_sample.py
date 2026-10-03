"""필터 정밀도 평가 표본 추출 — 층별 무작위 200건 (이슈 #89, §10.1).
담당: 성제 (sj)

무엇을:
    최종 조립 결과(`filter_status` 가 붙은 JSONL)에서 층(= `filter_status`)마다 무작위로 뽑는다.
    층과 크기는 측정 차수(`ROUNDS`)마다 사전 등록으로 정해져 있다.

        1차 (2026-10-01)  `filtered.jsonl`(v0.7)  KEPT 100 + NOISE_MOVE 50 + NOISE_TRIVIAL 50
        2차 (2026-10-03)  `records_v0.8.jsonl`    KEPT 100 + NOISE_MOVE 34 + NOISE_TRIVIAL 33
                                                  + NOISE_FORMAT 33, #90 본 라벨 500건은 뺀다

    200건을 섞어 두 파일로 낸다.

        <out-dir>/records.jsonl   판정용. 판정 CLI(`tools/filter_judge_cli.py`)가 읽는다.
                                  `filter_status`·`filter_rule_version`·`filter_evidence` 가 없다
        <out-dir>/key.json        정답 대조용. 표본 각 건의 `filter_status` 와 모집단 크기.
                                  집계(`eval/filter_precision.py`)만 읽는다. 판정자는 열지 않는다

사전 등록(`docs/evaluation.md` "필터 정밀도 사전 등록", "2차 재측정 사전 등록")의 모집단·표본
규칙을 구현한다:
    - 모집단은 조립 결과 전체. 다만 선정 CSV 에서 `recent_only=true` 인 저장소는
      `author_date` ≥ 2015-01-01T00:00:00Z 레코드만 넣는다 (#85 와 같은 기준). 채굴 구간은 커미터
      날짜로 잘렸으므로(`pipeline/walk.py`, #148) 구간 안 커밋에도 그보다 이른 author_date 가 있다
    - 층(`STRATUM_SIZES`)마다 저수지 표본 추출(Algorithm R). 조립 결과가 10GB 를 넘어 한 번에
      올리지 않는다. 제외를 사유로 층화하는 이유: 단순 무작위면 제외의 약 83%가 NOISE_TRIVIAL 이라
      이동이 20건 안팎만 뽑히고, #80 이 #89 결과로 미룬 이동 판단(이동+리네임 0.9 미만, 커밋 간
      이동)의 근거가 되지 못한다
    - 층에 없는 `filter_status`(아직 구현되지 않은 NOISE_RENAME 등)가 나오면 멈춘다. 사전 등록이
      다루지 않은 층을 조용히 빼거나 섞지 않는다. 1차 층으로 v0.8 을 읽으면 NOISE_FORMAT 에서 멈춘다
    - 2차는 #90 본 라벨 500건(`datasets/labels/main500_assignment.jsonl` 의 `record_id`)을
      모집단에서 뺀다 — 같은 판정자가 같은 레코드를 두 번 보지 않게. 뺀 건수는 `key.json` 에 남긴다.
      제외 확인은 난수를 쓰지 않으므로, 빼는 목록이 없으면 1차와 같은 표본이 나온다
    - 섞기는 같은 난수 생성기로 저수지 다음에 한다. 시드가 같고 입력 파일이 같으면 같은 표본이다.
      입력이 같은지는 `key.json` 의 `assembled_sha256` 으로 확인한다 (읽으면서 같이 계산한다)

판정용 레코드에서 필터 판정을 가리는 방법:
    - `filter_status`·`filter_rule_version`·`filter_evidence` 키를 넣지 않는다
    - `deleted_line_count` 는 **모든** 건에 넣는다. NOISE_TRIVIAL 의 `filter_evidence.line_count`
      를 그 건에만 보여 주면 그 자체가 표시가 된다. 값은 `filter_rules.md` 와 같은
      `len(deleted_body.splitlines())` 다
    - `similar_function` 은 NOISE_MOVE 의 `filter_evidence`(같은 커밋의 비슷한 함수)다. 이것은
      제외 건에만 있어 가릴 수 없다 — 사전 등록 "알려진 한계"에 적었다
    - `added_hunks_same_file`(같은 커밋·같은 파일의 추가 헝크)은 조립 결과의 값을 그대로
      **모든** 건에 넣는다 (#155). 판정을 diff 로 하게 하려는 것이다 (가이드 §6.3.3)

사용:
    python -m eval.filter_precision_sample            # 2차 (기본값이 모두 2차 사전 등록 값)
    python -m eval.filter_precision_sample --round 1  # 1차 재현 (data/filter_precision)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# #85 와 같은 기준. 선정 단계 `pipeline/select_repos.py:RECENT_ONLY_BEFORE_YEAR` 의 1월 1일 UTC
RECENT_ONLY_SINCE = datetime(2015, 1, 1, tzinfo=UTC)

KEPT = "KEPT"
NOISE_MOVE = "NOISE_MOVE"
NOISE_TRIVIAL = "NOISE_TRIVIAL"
NOISE_FORMAT = "NOISE_FORMAT"  # v0.8 사후 필터 (#152, `pipeline/postfilter.py`)


@dataclass(frozen=True)
class Round:
    """측정 한 차수의 사전 등록 값. 층 = filter_status, `sizes` 는 층별 표본 크기."""

    number: int
    seed: int
    sizes: Mapping[str, int]
    assembled: Path
    out_dir: Path
    exclude: Path | None  # 모집단에서 뺄 record_id 목록 (JSONL, `record_id` 키)
    # 제외 목록의 고유 record_id 수. 정해져 있으면 출력 전에 검증한다 (`check_exclusion`)
    exclude_count: int | None = None


# 사전 등록 값 (docs/evaluation.md). 바꾸면 그 절을 먼저 고친다. 지난 차수는 재현용으로 둔다.
ROUNDS: dict[int, Round] = {
    1: Round(
        number=1,
        seed=20261001,
        sizes={KEPT: 100, NOISE_MOVE: 50, NOISE_TRIVIAL: 50},
        assembled=Path("data/assembled/filtered.jsonl"),
        out_dir=Path("data/filter_precision"),
        exclude=None,
    ),
    # 2차 재측정 (2026-10-03 등록). 1차와 시드를 다르게, NOISE_FORMAT 층 추가, #90 500건 제외
    2: Round(
        number=2,
        seed=20261004,
        sizes={KEPT: 100, NOISE_MOVE: 34, NOISE_TRIVIAL: 33, NOISE_FORMAT: 33},
        assembled=Path("data/assembled/records_v0.8.jsonl"),
        out_dir=Path("data/filter_precision_v2"),
        exclude=Path("datasets/labels/main500_assignment.jsonl"),
        exclude_count=500,
    ),
}
CURRENT_ROUND = 2
DEFAULT_SEED = ROUNDS[CURRENT_ROUND].seed
# 층 = filter_status. 층별 표본 크기 (사전 등록)
STRATUM_SIZES: Mapping[str, int] = ROUNDS[CURRENT_ROUND].sizes
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
    # 같은 커밋·같은 파일의 추가 헝크 — "대신 들어간 코드" (#155). 1차 판정은 이게 없어 커밋
    # 메시지로 짐작했다 (#151). 층과 무관하게 모든 건에 같은 모양으로 들어간다
    "added_hunks_same_file",
    "commit_message",
    "similar_function",
)


class SampleError(ValueError):
    """표본을 만들 수 없다. 출력 파일은 쓰지 않았다."""


def stratum_of(filter_status: object, sizes: Mapping[str, int] = STRATUM_SIZES) -> str:
    """층 이름은 `filter_status` 그대로다. 사전 등록 층(`sizes`) 밖의 값은 거부한다."""
    if isinstance(filter_status, str) and filter_status in sizes:
        return filter_status
    raise SampleError(
        f"filter_status {filter_status!r} 는 사전 등록 층({', '.join(sizes)})이 아니다"
    )


def load_exclude_ids(path: Path) -> frozenset[str]:
    """모집단에서 뺄 `record_id` (JSONL, 줄마다 `record_id`). 비었거나 키가 없으면 거부한다."""
    ids: set[str] = set()
    for line_no, row in iter_lines(path, hashlib.sha256()):
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise SampleError(f"{path}:{line_no}: record_id 가 없다")
        ids.add(record_id)
    if not ids:
        raise SampleError(f"{path}: record_id 가 없다")
    return frozenset(ids)


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
    exclude_ids: Collection[str] = frozenset(),
    *,
    round_number: int = CURRENT_ROUND,
) -> SampleResult:
    """조립 결과를 한 번 훑어 층마다 `sizes[층]` 건을 뽑고 섞는다.

    난수 생성기 하나를 모든 층이 파일 순서대로 나눠 쓴다. 입력·시드가 같으면 결과가 같다.
    `exclude_ids` 의 레코드는 모집단에서 빠진다 (난수를 쓰지 않는다).
    """
    if KEPT not in sizes or NOISE_MOVE not in sizes:
        raise SampleError(f"층에 {KEPT}·{NOISE_MOVE} 가 있어야 한다: {sorted(sizes)}")
    strata = tuple(sizes)
    rng = random.Random(seed)
    reservoirs = {stratum: Reservoir(sizes[stratum]) for stratum in strata}
    out_of_window: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    excluded_seen: set[str] = set()
    versions: set[str] = set()
    digest = hashlib.sha256()

    for line_no, row in iter_lines(assembled, digest):
        record_id = row.get("id")
        if not record_id:
            raise SampleError(f"{assembled}:{line_no}: id 가 없다")
        try:
            stratum = stratum_of(row.get("filter_status"), sizes)
            if record_id in exclude_ids:
                excluded_seen.add(record_id)
            if not in_population(row, recent_only):
                out_of_window[str(row.get("repo"))] += 1
                continue
        except SampleError as error:
            raise SampleError(f"{assembled}:{line_no}: {error}") from None
        if record_id in exclude_ids:
            excluded[stratum] += 1
            continue
        versions.add(str(row.get("filter_rule_version")))
        reservoirs[stratum].offer(row, rng)

    for stratum, reservoir in reservoirs.items():
        if reservoir.seen < reservoir.size:
            raise SampleError(
                f"{stratum} 모집단이 {reservoir.seen}건 — 표본 {reservoir.size}건보다 적다"
            )
    if len(versions) != 1:
        raise SampleError(f"filter_rule_version 이 섞였다: {sorted(versions)}")

    picked = [(stratum, row) for stratum in strata for row in reservoirs[stratum].items]
    rng.shuffle(picked)
    width = len(str(len(picked)))
    # 1차 `fp-001`, 2차 `fp2-001` — 판정 파일이 다른 차수 표본에 섞이지 않게
    prefix = "fp" if round_number == 1 else f"fp{round_number}"
    records: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for position, (stratum, row) in enumerate(picked, start=1):
        sample_id = f"{prefix}-{position:0{width}d}"
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

    population = {stratum: reservoirs[stratum].seen for stratum in strata}
    key = {
        "issue": 89,
        "round": round_number,
        "seed": seed,
        "sample_sizes": {stratum: sizes[stratum] for stratum in strata},
        "assembled": str(assembled),
        "assembled_sha256": digest.hexdigest(),
        "filter_rule_version": next(iter(versions)),
        "recent_only_since": RECENT_ONLY_SINCE.isoformat(),
        "population": population,
        "out_of_window_by_repo": dict(sorted(out_of_window.items())),
        # 모집단에서 뺀 레코드 (2차: #90 본 라벨 500건). 구간 밖에 있던 것은 위에서 이미 빠졌다
        "excluded_ids": {
            "count": len(exclude_ids),
            "in_population_by_stratum": {stratum: excluded[stratum] for stratum in strata},
            "out_of_window": len(excluded_seen) - sum(excluded.values()),
            "not_in_assembled": len(set(exclude_ids) - excluded_seen),
        },
        "items": items,
    }
    return SampleResult(records, key)


def check_exclude_count(exclude_ids: Collection[str], expected: int) -> None:
    """제외 목록의 고유 record_id 가 사전 등록 수(2차: 500)와 같은지. 조립 결과를 훑기 전에 본다."""
    if len(exclude_ids) != expected:
        raise SampleError(
            f"제외 목록의 고유 record_id 가 {len(exclude_ids)}개 — {expected}개여야 한다"
        )


def check_exclusion_found(key: Mapping[str, Any]) -> None:
    """제외 목록의 모든 record_id 가 조립 결과에 있었는지. 출력 전에 본다.

    recent_only 구간 밖 레코드도 조립 결과에는 있으므로 허용한다. 그래서 모집단 안에서 뺀 건수를
    목록 크기로 강제하지 않는다 — 없는 id 만 거부한다 (다른 조립 결과나 잘못된 목록).
    """
    missing = key["excluded_ids"]["not_in_assembled"]
    if missing:
        raise SampleError(f"제외 목록의 record_id {missing}개가 조립 결과에 없다")


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
        description="필터 정밀도 평가 표본 — 층별 무작위 200건 (#89, §10.1). "
        "--assembled·--seed·--out-dir·--exclude 의 기본값은 --round 의 사전 등록 값이다.",
    )
    parser.add_argument(
        "--round", type=int, choices=sorted(ROUNDS), default=CURRENT_ROUND, help="측정 차수"
    )
    parser.add_argument("--assembled", type=Path, default=None, help="조립 결과 JSONL")
    parser.add_argument(
        "--selection-csv",
        type=Path,
        default=Path("docs/repo_final20_v2.csv"),
        help="저장소 선정 CSV (recent_only 열)",
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--exclude", type=Path, default=None, help="모집단에서 뺄 record_id JSONL (2차: main500)"
    )
    parser.add_argument("--overwrite", action="store_true", help="기존 표본 파일을 덮어쓴다")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8")
    plan = ROUNDS[args.round]
    assembled = args.assembled or plan.assembled
    seed = plan.seed if args.seed is None else args.seed
    out_dir = args.out_dir or plan.out_dir
    exclude = args.exclude or plan.exclude
    try:
        exclude_ids = load_exclude_ids(exclude) if exclude else frozenset()
        # 2차: 제외 목록이 사전 등록 크기인지 먼저, 모두 조립 결과에 있는지 출력 전에 본다
        if plan.exclude_count is not None:
            check_exclude_count(exclude_ids, plan.exclude_count)
        result = draw_sample(
            assembled,
            load_recent_only(args.selection_csv),
            seed,
            plan.sizes,
            exclude_ids,
            round_number=plan.number,
        )
        if plan.exclude_count is not None:
            check_exclusion_found(result.key)
        result.key["excluded_ids"]["source"] = str(exclude) if exclude else None
        paths = write_outputs(result, out_dir, overwrite=args.overwrite)
    except (SampleError, OSError) as error:
        print(f"표본 추출 실패: {error}", file=sys.stderr)
        return 1
    key = result.key
    print(
        f"{plan.number}차 표본 {len(result.records)}건 (시드 {key['seed']}) — "
        f"모집단 {key['population']}, 층별 표본 {key['sample_sizes']}, "
        f"filter_rule_version {key['filter_rule_version']}",
        file=sys.stderr,
    )
    print(f"recent_only 구간 밖 제외: {key['out_of_window_by_repo']}", file=sys.stderr)
    print(f"제외 목록: {key['excluded_ids']}", file=sys.stderr)
    for path in paths:
        print(f"썼다: {path}", file=sys.stderr)
    print("key.json 은 판정이 끝날 때까지 판정자에게 보이지 않는다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
