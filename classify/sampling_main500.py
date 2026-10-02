"""본 라벨링 500건 샘플 추출 + 블록 배분 + train/val/test 분할 (이슈 #85). 담당: 희수 (hs)

예비 200건(`classify.sampling`)과 같은 산출물(레코드 파일 1개 + 개인 빈 틀 3개)을 만들되,
규칙이 다르다 (#85, 2026-10-03 조장 정리):

    - **저장소마다 25건씩 균등.** 모자란 저장소는 가진 만큼만 받고, 남는 몫은 나머지에 고르게
      나눈다 (`allocate_equal`). FULL_FUNCTION 수가 저장소마다 20배 넘게 차이 나서, 예비 200건의
      비례 배분이면 transformers 한 곳이 몇십 %를 차지한다
    - **recent_only 저장소는 `author_date` 가 채굴 구간(2015-01-01) 이후인 것만.** 구간 기준은
      선정 CSV(`docs/repo_final20_v2.csv`)의 `recent_only`·`mining_since_year` 를 그대로 쓴다.
      #148 이전에 추출한 저장소가 구간 밖 커밋을 담고 있어 여기서도 거른다
    - **예비 200건과 겹치는 레코드는 뺀다** (`commit_sha`·`file_path`·`function_name`,
      가이드 §1·§11-16).
      그중 합의 102건(두 라벨러 `reason_label` 일치)이 몇 건인지 따로 센다 - 개발에 쓴 건이
      test 에 섞이면 안 된다 (`docs/evaluation.md` 게이트 2 사전 등록)
    - **블록당 처음 50건을 파일 앞에** (가이드 §8.4.1 중간 점검)
    - **train 300 / val 100 / test 100** 을 블록·저장소에 고르게 (`assign_splits`). 분할은 라벨러가
      보는 레코드 파일이 아니라 따로 `main500_assignment.jsonl` 에 둔다

입력이 13GB 를 넘어 한 번에 올릴 수 없다. 두 번 훑는다: 처음엔 대상의 `id`·저장소만 모으고,
뽑은 500건만 두 번째에 통째로 읽는다.

산출물 (`--out-dir`, 기본 `datasets/labels`):
    main500_records.jsonl        라벨러가 보는 레코드 (`sampling.build_labeling_record`)
    {sj,jh,hs}_main500.jsonl     개인 빈 틀. 맡은 두 블록의 처음 50건씩이 맨 앞
    main500_assignment.jsonl     record_id → 저장소·블록·블록 안 순번·split

실행:
    python -m classify.sampling_main500 --input data/assembled/records_v0.8.jsonl --dry-run
    python -m classify.sampling_main500 --input data/assembled/records_v0.8.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from classify.sampling import (
    BLOCK_PAIRS,
    LABELERS,
    RECORDS_FILENAME,
    TARGET_DELETION_KIND,
    TARGET_FILTER_STATUS,
    Assignment,
    assign_blocks,
    build_labeling_record,
    is_eligible,
    label_rows_by_labeler,
    record_id_of,
    write_jsonl,
)

BATCH = "main500"
DEFAULT_SEED = 20261003
# test·val 을 먼저 고르고 남는 것이 train 이다 (§10.2). test 는 #86 이 마지막에 한 번만 쓴다.
SPLITS: tuple[tuple[str, int], ...] = (("test", 100), ("val", 100), ("train", 300))
SAMPLE_SIZE = sum(size for _split, size in SPLITS)
# 가이드 §8.4.1 - 블록마다 처음 50건을 두 라벨러가 끝내면 kappa 중간 점검.
INTERIM_UNIT = 50

RECORDS_OUT = f"{BATCH}_records.jsonl"
LABEL_OUT_TEMPLATE = "{labeler}_" + f"{BATCH}.jsonl"
ASSIGNMENT_OUT = f"{BATCH}_assignment.jsonl"

DEFAULT_REPOS_CSV = Path("docs/repo_final20_v2.csv")
DEFAULT_PRE200_DIR = Path("datasets/labels")
PRE200_MERGED = "gate1_pre200_merged.jsonl"

Key = tuple[str, str, str]


def overlap_key(commit_sha: str, file_path: Any, function_name: Any) -> Key:
    """레코드 겹침 판정 키 (`docs/evaluation.md` - `commit_sha` + `file_path` + `function_name`).

    `id` 로 맞추지 않는 이유: 예비 200건 일부는 #75 이전 규칙으로 만든 `id` 다.
    """
    return (str(commit_sha), str(file_path), str(function_name))


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """JSONL 을 줄 단위로 (`splitlines()` 금지, #146)."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def pre200_keys(labels_dir: Path) -> tuple[set[Key], set[Key]]:
    """예비 200건 전체의 키와, 그중 합의(두 라벨러 `reason_label` 일치) 건의 키."""
    by_id: dict[str, Key] = {}
    for row in _jsonl(labels_dir / RECORDS_FILENAME):
        sha = str(row.get("source_url", "")).rstrip("/").rsplit("/", 1)[-1]
        by_id[row["record_id"]] = overlap_key(sha, row.get("file_path"), row.get("function_name"))
    consensus: set[Key] = set()
    for row in _jsonl(labels_dir / PRE200_MERGED):
        reasons = {label.get("reason_label") for label in row.get("labels") or []}
        if len(row.get("labels") or []) >= 2 and len(reasons) == 1 and row["record_id"] in by_id:
            consensus.add(by_id[row["record_id"]])
    return set(by_id.values()), consensus


def read_selection(repos_csv: Path) -> tuple[frozenset[str], dict[str, datetime]]:
    """선정 CSV 의 저장소 목록과, recent_only 저장소 → 채굴 구간 시작 (UTC).

    목록도 여기서 읽는 이유: 입력에 선정 밖 저장소가 섞이면 균등 배분이 그 저장소에도 25건을
    준다 - 실행은 성공하는데 "20개 저장소 균등 표본" 이 아니게 된다 (#85 코드래빗). 구간 값은
    #148 과 같은 출처(`recent_only`·`mining_since_year`)를 그대로 쓴다.
    """
    repos: set[str] = set()
    windows: dict[str, datetime] = {}
    with repos_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            repos.add(row["repo"])
            if str(row.get("recent_only", "")).strip().lower() == "true":
                windows[row["repo"]] = datetime(int(row["mining_since_year"]), 1, 1, tzinfo=UTC)
    return frozenset(repos), windows


def in_window(author_date: Any, since: datetime) -> bool:
    """`author_date` 가 구간 시작 이후인가. 읽을 수 없는 날짜는 구간 밖으로 본다."""
    try:
        moment = datetime.fromisoformat(str(author_date))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment >= since


class Candidate(NamedTuple):
    """뽑을 수 있는 레코드 하나. 첫 번째 훑기에서는 이것만 들고 있는다."""

    record_id: str
    repo: str


def scan_candidates(
    lines: Iterator[str],
    selected: Collection[str],
    windows: Mapping[str, datetime],
    excluded: set[Key],
    consensus: set[Key],
) -> tuple[list[Candidate], Counter[str]]:
    """대상(FULL_FUNCTION·KEPT)을 거르고 그 이유별 건수를 센다.

    선정 CSV 에 없는 저장소의 대상은 빼고 센다 (`read_selection`). 맥락이 없는 대상도 따로 센다 -
    조립 결과에 맥락을 붙이기(#142) 전 파일을 넣은 것이다.

    예비 200건 겹침은 행 수(`pre200_overlap`)와 맞은 키 수(`pre200_keys`·`consensus_keys`)를 둘
    다 센다. 한 파일에서 같은 이름 함수(`__init__` 등)가 여럿 지워지면 키 하나에 행이 여럿이라,
    행 수만 보면 "200건과 겹친 것이 318건" 처럼 읽힌다. 그 행은 모두 뺀다 (보수적인 쪽).
    """
    candidates: list[Candidate] = []
    stats: Counter[str] = Counter()
    matched: set[Key] = set()
    for line in lines:
        stats["rows"] += 1
        # 싼 거르기. 값이 줄에 없으면 대상일 수 없다 - 있으면 파싱해서 제대로 본다.
        if TARGET_DELETION_KIND not in line or TARGET_FILTER_STATUS not in line:
            continue
        record = json.loads(line)
        if not is_eligible(record):
            continue
        stats["eligible"] += 1
        repo = str(record.get("repo", ""))
        if repo not in selected:
            stats["not_selected"] += 1
            continue
        since = windows.get(repo)
        if since is not None and not in_window(record.get("author_date"), since):
            stats["before_window"] += 1
            continue
        key = overlap_key(
            record.get("commit_sha"), record.get("file_path"), record.get("function_name")
        )
        if key in excluded:
            stats["pre200_overlap"] += 1
            matched.add(key)
            continue
        if not isinstance(record.get("context"), dict):
            stats["missing_context"] += 1
            continue
        candidates.append(Candidate(record_id_of(record), repo))
    stats["pre200_keys"] = len(matched)
    stats["consensus_keys"] = len(matched & consensus)
    return candidates, stats


def allocate_equal(counts: Mapping[str, int], total: int) -> dict[str, int]:
    """저장소마다 같은 수. 모자란 저장소는 가진 만큼만 받고 남는 몫은 나머지에 고르게 나눈다.

    나눠떨어지지 않는 나머지는 저장소 이름순으로 1건씩 - 시드와 무관하게 재현된다.
    """
    quota: dict[str, int] = {}
    remaining = {repo: count for repo, count in counts.items() if count > 0}
    left = total
    while remaining and left > 0:
        share = left // len(remaining)
        short = {repo: count for repo, count in remaining.items() if count <= share}
        if not short:
            break
        for repo, count in short.items():
            quota[repo] = count
            left -= count
            del remaining[repo]
    if remaining and left > 0:
        share, extra = divmod(left, len(remaining))
        for index, repo in enumerate(sorted(remaining)):
            quota[repo] = share + (1 if index < extra else 0)
    return quota


def sample_equal(
    candidates: Sequence[Candidate], size: int = SAMPLE_SIZE, seed: int = DEFAULT_SEED
) -> list[Candidate]:
    """저장소별 균등 샘플. 같은 시드·같은 입력이면 같은 결과.

    뽑은 뒤 섞는다 - 블록 배분이 이 순서를 3등분하므로 저장소별로 묶인 채면 블록마다 저장소가
    갈린다 (`sampling.stratified_sample` 와 같은 이유).
    """
    by_repo: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_repo.setdefault(candidate.repo, []).append(candidate)
    quota = allocate_equal({repo: len(items) for repo, items in by_repo.items()}, size)
    rng = random.Random(seed)
    picked: list[Candidate] = []
    for repo in sorted(quota):
        pool = sorted(by_repo[repo], key=lambda candidate: candidate.record_id)
        picked.extend(rng.sample(pool, quota[repo]))
    rng.shuffle(picked)
    return picked


def _largest_remainder(
    sizes: Mapping[Any, int], total: int, order: Callable[[Any], Any]
) -> dict[Any, int]:
    """`total` 을 `sizes` 에 비례해 나눈다. 소수점 몫이 큰 칸부터, 같으면 `order` 순으로 1씩."""
    population = sum(sizes.values())
    if population <= 0 or total <= 0:
        return {key: 0 for key in sizes}
    exact = {key: total * size / population for key, size in sizes.items()}
    quota = {key: int(value) for key, value in exact.items()}
    left = total - sum(quota.values())
    for key in sorted(sizes, key=lambda k: (quota[k] - exact[k], order(k)))[:left]:
        quota[key] += 1
    return quota


def assign_splits(
    assignments: Sequence[Assignment],
    repo_of: Mapping[str, str],
    splits: Sequence[tuple[str, int]] = SPLITS,
    seed: int = DEFAULT_SEED,
) -> dict[str, str]:
    """record_id → split. test·val 을 저장소에 먼저 비례로 나누고, 저장소 안에서 블록에 나눈다.

    두 단계로 나누는 이유: (저장소, 블록) 60칸에 한 번에 나누면 칸마다 몫이 1.67 로 같아
    나머지가 무작위로 몰려 저장소별 test 수가 들쭉날쭉해진다. 저장소별로 먼저 정하면 25건씩일 때
    정확히 5건씩이다. 블록에 나눌 때 같은 몫이면 지금까지 그 split 을 덜 받은 블록이 먼저다.
    """
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[str]] = {}
    for assignment in assignments:
        for record_id in assignment.record_ids:
            cells.setdefault((repo_of[record_id], assignment.block), []).append(record_id)
    for cell in sorted(cells):
        cells[cell].sort()
        rng.shuffle(cells[cell])

    result: dict[str, str] = {}
    *chosen, (rest, _rest_size) = splits
    for split, size in chosen:
        per_block: Counter[str] = Counter()
        repo_sizes: Counter[str] = Counter()
        for (repo, _block), ids in cells.items():
            repo_sizes[repo] += len(ids)
        repo_quota = _largest_remainder(repo_sizes, size, order=lambda repo: repo)
        for repo in sorted(repo_quota):
            blocks = {block: len(ids) for (r, block), ids in cells.items() if r == repo}
            block_quota = _largest_remainder(
                blocks,
                repo_quota[repo],
                order=lambda block, given=per_block: (given[block], block),
            )
            for block, count in block_quota.items():
                taken, cells[(repo, block)] = (
                    cells[(repo, block)][:count],
                    cells[(repo, block)][count:],
                )
                result.update(dict.fromkeys(taken, split))
                per_block[block] += count
    for ids in cells.values():
        result.update(dict.fromkeys(ids, rest))
    return result


def fetch_records(lines: Iterator[str], wanted: set[str]) -> dict[str, dict[str, Any]]:
    """두 번째 훑기 - 뽑은 `id` 의 레코드만 통째로 읽는다."""
    found: dict[str, dict[str, Any]] = {}
    for line in lines:
        if TARGET_DELETION_KIND not in line:
            continue
        record = json.loads(line)
        record_id = record_id_of(record)
        if record_id in wanted:
            found[record_id] = record
    return found


def assignment_rows(
    assignments: Sequence[Assignment], repo_of: Mapping[str, str], split_of: Mapping[str, str]
) -> Iterator[dict[str, Any]]:
    """`main500_assignment.jsonl` 한 줄씩. `interim` 은 블록 안 처음 50건 (중간 점검 대상)."""
    for assignment in assignments:
        for position, record_id in enumerate(assignment.record_ids, start=1):
            yield {
                "record_id": record_id,
                "batch": BATCH,
                "repo": repo_of[record_id],
                "block": assignment.block,
                "labelers": list(assignment.labelers),
                "block_position": position,
                "interim": position <= INTERIM_UNIT,
                "split": split_of[record_id],
            }


def summarize(
    stats: Counter[str],
    sample: Sequence[Candidate],
    assignments: Sequence[Assignment],
    split_of: Mapping[str, str],
) -> list[str]:
    """거른 건수와 배분 결과. 사람이 눈으로 확인하고 PR·이슈에 옮겨 적는다."""
    lines = [
        f"입력 {stats['rows']:,}줄 → 대상({TARGET_DELETION_KIND}·{TARGET_FILTER_STATUS}) "
        f"{stats['eligible']:,}건",
        f"  선정 CSV 밖 저장소 제외       {stats['not_selected']:>8,}건",
        f"  recent_only 구간 밖 제외      {stats['before_window']:>8,}건",
        f"  예비 200건과 겹쳐 제외        {stats['pre200_overlap']:>8,}건 "
        f"(맞은 키: 예비 200건 중 {stats['pre200_keys']}개, 그중 합의 102건 "
        f"{stats['consensus_keys']}개)",
        f"  맥락 없음                     {stats['missing_context']:>8,}건",
    ]
    by_repo = Counter(candidate.repo for candidate in sample)
    lines.append(f"표본 {len(sample)}건 / 저장소 {len(by_repo)}개")
    split_by_repo: dict[str, Counter[str]] = {}
    for candidate in sample:
        split_by_repo.setdefault(candidate.repo, Counter())[split_of[candidate.record_id]] += 1
    for repo in sorted(by_repo):
        splits = split_by_repo[repo]
        lines.append(
            f"  {repo:<34} {by_repo[repo]:>3}건  "
            + " ".join(f"{name} {splits[name]}" for name, _ in SPLITS)
        )
    lines.append("블록 (가이드 §8.1 - 전건 2인 독립, 앞 50건 중간 점검)")
    for assignment in assignments:
        splits = Counter(split_of[record_id] for record_id in assignment.record_ids)
        lines.append(
            f"  {assignment.block} {len(assignment.record_ids):>4}건  "
            f"{' + '.join(assignment.labelers)}  "
            + " ".join(f"{name} {splits[name]}" for name, _ in SPLITS)
        )
    return lines


def output_paths(out_dir: Path) -> list[Path]:
    """이 실행이 쓰게 될 파일 전부."""
    return [
        out_dir / RECORDS_OUT,
        out_dir / ASSIGNMENT_OUT,
        *(out_dir / LABEL_OUT_TEMPLATE.format(labeler=labeler) for labeler in LABELERS),
    ]


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자. 실행 방법은 모듈 독스트링."""
    parser = argparse.ArgumentParser(
        prog="python -m classify.sampling_main500",
        description="본 라벨링 500건 추출 + 블록 배분 + train/val/test 분할 (#85).",
    )
    parser.add_argument("--input", type=Path, required=True, help="맥락까지 붙은 조립 결과")
    parser.add_argument("--out-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument("--repos-csv", type=Path, default=DEFAULT_REPOS_CSV)
    parser.add_argument("--pre200-dir", type=Path, default=DEFAULT_PRE200_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="고정해야 재현된다")
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 요약만")
    parser.add_argument(
        "--force", action="store_true", help="기존 출력을 덮어쓴다. 채운 라벨이 사라진다"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """뽑고, 배분하고, 분할해서 쓴다. 거를 것이 남아 있으면 쓰기 전에 멈춘다."""
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    existing = [path for path in output_paths(args.out_dir) if path.exists()]
    if existing and not (args.force or args.dry_run):
        print("이미 있는 파일을 덮어쓰려 한다 (사람이 채운 라벨이 있을 수 있다):", file=sys.stderr)
        for path in existing:
            print(f"  - {path}", file=sys.stderr)
        return 2

    excluded, consensus = pre200_keys(args.pre200_dir)
    selected, windows = read_selection(args.repos_csv)
    with args.input.open(encoding="utf-8") as handle:
        candidates, stats = scan_candidates(handle, selected, windows, excluded, consensus)
    if stats["missing_context"]:
        print(
            f"맥락이 없는 대상이 {stats['missing_context']}건이다. 맥락을 붙이기(#142) 전의 조립 "
            "결과를 넣은 것 같다 - 라벨러가 볼 맥락이 없다.",
            file=sys.stderr,
        )
        return 2
    if len(candidates) < SAMPLE_SIZE:
        print(f"대상이 {len(candidates)}건으로 목표 {SAMPLE_SIZE}건보다 적다.", file=sys.stderr)
        return 1

    sample = sample_equal(candidates, SAMPLE_SIZE, args.seed)
    repo_of = {candidate.record_id: candidate.repo for candidate in sample}
    assignments = assign_blocks([candidate.record_id for candidate in sample], BLOCK_PAIRS)
    split_of = assign_splits(assignments, repo_of, SPLITS, args.seed)
    for line in summarize(stats, sample, assignments, split_of):
        print(line)
    if args.dry_run:
        print("(dry-run: 파일을 쓰지 않았다)")
        return 0

    with args.input.open(encoding="utf-8") as handle:
        records = fetch_records(handle, set(repo_of))
    missing = set(repo_of) - set(records)
    if missing:
        print(
            f"두 번째 훑기에서 {len(missing)}건을 찾지 못했다: {sorted(missing)[:3]}",
            file=sys.stderr,
        )
        return 2

    try:
        out = args.out_dir
        written = write_jsonl(
            out / RECORDS_OUT,
            (build_labeling_record(records[c.record_id]) for c in sample),
            overwrite=args.force,
        )
        print(f"레코드: {out / RECORDS_OUT} ({written}건)")
        rows = assignment_rows(assignments, repo_of, split_of)
        written = write_jsonl(out / ASSIGNMENT_OUT, rows, overwrite=args.force)
        print(f"배분·분할: {out / ASSIGNMENT_OUT} ({written}건)")
        for labeler, label_rows in label_rows_by_labeler(assignments, INTERIM_UNIT).items():
            path = out / LABEL_OUT_TEMPLATE.format(labeler=labeler)
            count = write_jsonl(path, label_rows, overwrite=args.force)
            print(f"빈 틀: {path} ({count}건)")
    except FileExistsError as error:
        print(f"쓰는 도중 파일이 생겼다: {error.filename}. --force 로만 덮어쓴다.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
