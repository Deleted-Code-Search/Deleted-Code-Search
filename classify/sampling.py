"""예비 200건 샘플 추출 + 라벨링 파일·3인 배분 생성 (이슈 #33). 담당: 희수 (hs)

무엇을:
    #5 채굴 출력(§4.4 `DeletionRecord` JSONL)에서 저장소별 층화 샘플을 뽑아, 세 사람이 바로
    라벨링을 시작할 수 있는 파일 묶음을 만든다. 게이트 1(이유 회수율, §9 2주차)의 입력이다.

왜 이 스크립트가 라벨 값을 만들지 않나:
    라벨은 사람이 붙인다 (ADR-005). 이 스크립트는 **빈 칸을 만드는 것**까지다. 특히 `reason.*`
    는 분류기·기준선·LLM 출력이 들어가는 자리라, 라벨러에게 보이면 라벨이 그쪽으로 끌려가
    (anchoring) §10.2 평가가 자기 참조로 무너진다. 그래서 아래 `LABELER_FIELDS` 에 없는 것은
    아예 담지 않는다 (라벨 가이드 §2.2 — 이 문서가 #7/#33 의 완료 조건으로 명시한 항목).

산출물 (라벨 가이드 §7.1 "파일 두 층"):
    datasets/labels/pre200_records.jsonl   # 라벨러가 보는 레코드 정보 (공용, 1개)
    datasets/labels/sj_pre200.jsonl        # 개인 라벨 빈 틀 (사람당 1개, 총 3개)
    datasets/labels/jh_pre200.jsonl
    datasets/labels/hs_pre200.jsonl

실행:
    python -m classify.sampling --input deletions.jsonl --out-dir datasets/labels

경로·필드명은 아직 팀 확정 전이다 (라벨 가이드 §11-5 "가장 먼저 확정할 항목"). 확정되면
고칠 자리를 한 곳에 모아 두려고 이름을 전부 이 파일 상단 상수로 뺐다.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------------------
# 팀 확정 전 이름들 (라벨 가이드 §11-5). 확정되면 여기만 고친다.
# --------------------------------------------------------------------------------------

BATCH = "pre200"
SAMPLE_SIZE = 200
DEFAULT_SEED = 20260913

LABELERS = ("sj", "jh", "hs")
# 라벨 가이드 §8.1 — 전건 2인 독립. 3인이 쌍을 돌려 맡아야 쌍별 kappa 3개가 나온다.
BLOCK_PAIRS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("A", ("sj", "jh")),
    ("B", ("jh", "hs")),
    ("C", ("hs", "sj")),
)

RECORDS_FILENAME = f"{BATCH}_records.jsonl"
LABEL_FILENAME_TEMPLATE = "{labeler}_" + f"{BATCH}.jsonl"
GUIDE_VERSION = "v1"

# 라벨 파일의 `record_id` 는 §4.4 의 `DeletionRecord.id` 다 — 두 문서가 이름을 다르게 쓴다
# (가이드 §7.2가 "record_id = DeletionRecord.id"로 연결해 둔다). 옮길 때 이름을 바꿔 준다.
RECORD_ID_SOURCE = "id"

# 라벨러에게 보여줄 필드 (라벨 가이드 §2.1 표 그대로). 여기 없는 것은 담지 않는다.
# `record_id` 는 이름이 바뀌므로 위 상수로 따로 다룬다.
LABELER_FIELDS: tuple[str, ...] = (
    "repo",
    "file_path",
    "function_name",
    "function_signature",
    "deleted_body",
    "is_test_code",
    "source_url",
)
# 중첩 필드는 따로. (레코드 키, 하위 키들)
LABELER_REPLACEMENT_FIELDS: tuple[str, ...] = ("code", "match_method")
LABELER_CONTEXT_FIELDS: tuple[str, ...] = (
    "commit_message",
    "pr_title",
    "pr_body",
    "issue_titles",
    "review_comments",
)
# §4.4 `context` 에는 칸이 없지만 #6 이 이미 모으는 값. 이슈 본문에만 이유가 있는 건이 있어
# 성제가 #7 코멘트에서 "함께 보여주자"고 제안했다. 스키마 추가 여부는 #24 (§13 절차).
# 확정 전이므로 기본은 끄고 `--with-extra-context` 로만 켠다.
EXTRA_CONTEXT_FIELDS: tuple[str, ...] = ("issue_bodies", "pr_labels")

# 1차 대상 (ADR-003, 라벨 가이드 §2)
TARGET_DELETION_KIND = "FULL_FUNCTION"
TARGET_FILTER_STATUS = "KEPT"


# --------------------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------------------


def load_records(lines: Iterable[str]) -> list[dict[str, Any]]:
    """#5 출력 JSONL 을 읽는다. 빈 줄은 건너뛴다."""
    records: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if line:
            records.append(json.loads(line))
    return records


def record_id_of(record: dict[str, Any]) -> str:
    """레코드의 라벨 키. 공용 레코드 파일과 개인 빈 틀이 **같은 값**을 쓰게 하는 단일 경로.

    두 곳에서 따로 꺼내면 값이 갈린다. 실제로 한쪽은 `record.get("id")`, 다른 쪽은
    `str(record.get("id", ""))` 였고, `id` 가 없는 레코드에서 `None` 과 `""` 로 어긋났다.
    """
    raw = record.get(RECORD_ID_SOURCE)
    return "" if raw is None else str(raw).strip()


def find_id_problems(records: Sequence[dict[str, Any]]) -> list[str]:
    """`id` 계약 위반을 사람이 읽을 문장으로. 비어 있으면 문제 없음.

    왜 미리 막나:
        `id` 는 라벨과 레코드를 잇는 유일 키다 (가이드 §7.2). 비어 있으면 두 파일의 키가
        어긋나고, 빈 값이 여러 건이면 서로 다른 레코드가 같은 키를 갖는다. 그 상태로
        라벨링을 마치면 #34 병합에서 라벨이 엉뚱한 레코드에 붙는데, 그때는 아무 에러도
        나지 않아 게이트 1 숫자를 끝까지 믿게 된다. 라벨링 전에 멈추는 편이 훨씬 싸다.
    """
    problems: list[str] = []
    blank = [index for index, record in enumerate(records) if not record_id_of(record)]
    if blank:
        shown = ", ".join(str(index) for index in blank[:5])
        more = f" 외 {len(blank) - 5}건" if len(blank) > 5 else ""
        problems.append(f"`{RECORD_ID_SOURCE}` 가 비어 있다: {len(blank)}건 (줄 {shown}{more})")

    seen: dict[str, int] = {}
    duplicated: list[str] = []
    for record in records:
        key = record_id_of(record)
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            duplicated.append(key)
    if duplicated:
        shown = ", ".join(duplicated[:3])
        more = f" 외 {len(duplicated) - 3}개" if len(duplicated) > 3 else ""
        problems.append(f"`{RECORD_ID_SOURCE}` 가 중복된다: {len(duplicated)}개 ({shown}{more})")
    return problems


def is_eligible(record: dict[str, Any]) -> bool:
    """예비 라벨 대상인가.

    함수 전체 삭제(ADR-003)이고 필터를 통과한(§4.2 ②) 것만. 노이즈로 걸러진 레코드를
    라벨하면 게이트 1 숫자가 "우리가 쓰지도 않을 데이터"로 오염된다.
    """
    return (
        record.get("deletion_kind") == TARGET_DELETION_KIND
        and record.get("filter_status") == TARGET_FILTER_STATUS
    )


# --------------------------------------------------------------------------------------
# 층화 샘플링
# --------------------------------------------------------------------------------------


def allocate(counts: dict[str, int], total: int) -> dict[str, int]:
    """저장소별 표본 수를 정한다. 비례 배분 + 레코드가 있으면 최소 1건.

    왜 비례 배분인가:
        게이트 1의 주 지표는 **전체** 이유 회수율(§15)이다. 전체 모집단을 대표해야 하므로
        저장소 크기에 비례해 뽑는다. 균등 배분은 작은 저장소를 과대표집해 전체 숫자를 왜곡한다.

    왜 최소 1건인가:
        "저장소별 회수율"도 함께 보기 때문이다(§10.5). 반올림으로 0건이 되면 그 저장소는
        표에서 사라진다. 다만 1건짜리 저장소의 비율은 신뢰구간이 무의미하다는 점은
        보고할 때 감안해야 한다.

    잔여는 레코드가 많은 저장소부터 한 건씩 준다 (같으면 이름순 — 시드와 무관하게 재현된다).
    """
    available = {repo: count for repo, count in counts.items() if count > 0}
    if not available or total <= 0:
        return {}

    population = sum(available.values())
    if total >= population:
        return dict(available)

    # 최소 1건을 먼저 깔면 저장소 수가 목표를 넘을 수 있다. 그때는 큰 저장소부터 자른다.
    order = sorted(available, key=lambda repo: (-available[repo], repo))
    if len(order) >= total:
        return {repo: 1 for repo in order[:total]}

    quota = {repo: 1 for repo in order}
    remaining = total - len(order)
    shares = {
        repo: (available[repo] - 1) * remaining / (population - len(order))
        for repo in order
        if population > len(order)
    }
    for repo, share in shares.items():
        take = min(int(share), available[repo] - quota[repo])
        quota[repo] += take
        remaining -= take

    for repo in order:
        if remaining <= 0:
            break
        room = available[repo] - quota[repo]
        if room > 0:
            quota[repo] += 1
            remaining -= 1
    return quota


def stratified_sample(
    records: Sequence[dict[str, Any]], size: int = SAMPLE_SIZE, seed: int = DEFAULT_SEED
) -> list[dict[str, Any]]:
    """저장소별 층화 샘플. 같은 시드·같은 입력이면 항상 같은 결과 (#33 완료 조건).

    뽑은 뒤 한 번 더 섞는 이유:
        블록 배분(§8.1)이 이 순서를 그대로 3등분한다. 저장소별로 묶인 채 자르면 블록 A 는
        django 만, 블록 C 는 flask 만 받는 식이 된다. 저장소마다 맥락이 붙는 정도가 크게
        다르므로(`docs/evaluation.md` 2026-09-12) 블록 난이도가 갈리고, 쌍별 kappa 를
        서로 비교할 수 없게 된다.
    """
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_repo.setdefault(record.get("repo", ""), []).append(record)

    counts = {repo: len(items) for repo, items in by_repo.items()}
    quota = allocate(counts, size)

    rng = random.Random(seed)
    picked: list[dict[str, Any]] = []
    for repo in sorted(quota):  # 정렬해야 시드가 같을 때 뽑는 순서도 같다
        pool = sorted(by_repo[repo], key=record_id_of)
        picked.extend(rng.sample(pool, quota[repo]))

    rng.shuffle(picked)
    return picked


# --------------------------------------------------------------------------------------
# 라벨링 파일
# --------------------------------------------------------------------------------------


def build_labeling_record(
    record: dict[str, Any], *, with_extra_context: bool = False
) -> dict[str, Any]:
    """라벨러가 볼 1줄. 가이드 §2.1 에 적힌 필드만 담는다.

    화이트리스트로 만든다. 원본에서 `reason.*` 를 지우는 방식이면 #5 가 필드를 추가했을 때
    조용히 새어 나간다. 담을 것을 나열하는 편이 안전하다.
    """
    out: dict[str, Any] = {"record_id": record_id_of(record)}
    out.update({key: record.get(key) for key in LABELER_FIELDS})

    replacement = record.get("replacement") or {}
    out["replacement"] = {key: replacement.get(key) for key in LABELER_REPLACEMENT_FIELDS}

    context = record.get("context") or {}
    picked_context = {key: context.get(key) for key in LABELER_CONTEXT_FIELDS}
    if with_extra_context:
        picked_context.update({key: context.get(key) for key in EXTRA_CONTEXT_FIELDS})
    out["context"] = picked_context
    return out


def empty_label_row(record_id: str, labeler: str) -> dict[str, Any]:
    """개인 라벨 빈 틀 1줄. 가이드 §7.2 필드 순서·이름 그대로.

    `labeled_at` 은 사람이 라벨할 때 채운다. 지금 시각을 넣으면 "언제 라벨했나"가 거짓이 된다.
    """
    return {
        "record_id": record_id,
        "labeler": labeler,
        "reason_label": None,
        "evidence_grade": None,
        "evidence_text": None,
        "evidence_source": None,
        "evidence_locator": None,
        "confidence": None,
        "note": "",
        "labeled_at": None,
        "guide_version": GUIDE_VERSION,
    }


@dataclass
class Assignment:
    """블록 배분 결과. 블록 하나 = 레코드 묶음 + 그 묶음을 맡는 두 사람."""

    block: str
    labelers: tuple[str, str]
    record_ids: list[str] = field(default_factory=list)


def assign_blocks(
    record_ids: Sequence[str],
    pairs: Sequence[tuple[str, tuple[str, str]]] = BLOCK_PAIRS,
) -> list[Assignment]:
    """샘플을 순서대로 블록으로 3등분한다 (가이드 §8.1).

    200건이면 A 67 / B 67 / C 66. 나머지가 생기면 앞 블록부터 한 건씩 더 가져간다.
    """
    total = len(record_ids)
    base, extra = divmod(total, len(pairs))
    assignments: list[Assignment] = []
    start = 0
    for index, (block, labelers) in enumerate(pairs):
        size = base + (1 if index < extra else 0)
        assignments.append(Assignment(block, labelers, list(record_ids[start : start + size])))
        start += size
    return assignments


def label_rows_by_labeler(assignments: Sequence[Assignment]) -> dict[str, list[dict[str, Any]]]:
    """사람별 빈 틀 묶음. 한 사람이 블록 2개를 맡으므로 파일은 1인당 하나다 (§7.1)."""
    rows: dict[str, list[dict[str, Any]]] = {labeler: [] for labeler in LABELERS}
    for assignment in assignments:
        for labeler in assignment.labelers:
            rows.setdefault(labeler, []).extend(
                empty_label_row(record_id, labeler) for record_id in assignment.record_ids
            )
    return rows


# --------------------------------------------------------------------------------------
# 출력
# --------------------------------------------------------------------------------------


def output_paths(out_dir: Path) -> list[Path]:
    """이 실행이 쓰게 될 파일 전부. 미리 확인하려고 한 곳에 모아 둔다."""
    return [
        out_dir / RECORDS_FILENAME,
        *(out_dir / LABEL_FILENAME_TEMPLATE.format(labeler=labeler) for labeler in LABELERS),
    ]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]], *, overwrite: bool = False) -> int:
    """JSONL 한 파일. 기본은 **배타적 생성**이라 이미 있으면 FileExistsError 로 멈춘다.

    `"w"` 로 열면 여는 순간 내용이 날아간다. 이 파일들에는 사람이 몇 시간 들여 채운 라벨이
    들어 있을 수 있어(가이드 §8.1 기준 1인 약 6.7시간, 3인 20시간), 덮어쓰기는 `--force`
    로만 허용한다. 미리 검사해도 검사와 쓰기 사이에 생길 수 있으므로 모드로도 막는다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w" if overwrite else "x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def summarize(sample: Sequence[dict[str, Any]], assignments: Sequence[Assignment]) -> list[str]:
    """무엇이 몇 건 뽑혔는지. 배분이 의도대로 됐는지 사람이 눈으로 확인하는 용도."""
    by_repo: dict[str, int] = {}
    for record in sample:
        by_repo[record.get("repo", "")] = by_repo.get(record.get("repo", ""), 0) + 1

    lines = [f"표본 {len(sample)}건 / 저장소 {len(by_repo)}개"]
    for repo, count in sorted(by_repo.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"  {repo:<34} {count:>4}건")
    lines.append("블록 배분 (가이드 §8.1 — 전건 2인 독립)")
    for assignment in assignments:
        pair = " + ".join(assignment.labelers)
        lines.append(f"  {assignment.block} {len(assignment.record_ids):>4}건  {pair}")
    return lines


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m classify.sampling",
        description="예비 200건 층화 샘플 추출 + 라벨링 파일·3인 배분 생성 (#33).",
    )
    parser.add_argument("--input", type=Path, required=True, help="#5 출력 JSONL")
    parser.add_argument("--out-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument("--size", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="고정해야 재현된다")
    parser.add_argument(
        "--with-extra-context",
        action="store_true",
        help="이슈 본문·PR 라벨도 라벨러에게 보여준다 (#24 확정 전까지는 꺼 둔다)",
    )
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 요약만")
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 출력 파일을 덮어쓴다. 사람이 채운 라벨이 사라지므로 마지막 수단",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    records = load_records(args.input.read_text(encoding="utf-8").splitlines())
    eligible = [record for record in records if is_eligible(record)]
    print(
        f"입력 {len(records)}건 → 대상 {len(eligible)}건 "
        f"({TARGET_DELETION_KIND} & {TARGET_FILTER_STATUS})"
    )
    if not eligible:
        print("대상이 없다. #5 출력의 deletion_kind·filter_status 를 확인해라.", file=sys.stderr)
        return 1
    if len(eligible) < args.size:
        print(
            f"경고: 대상이 목표({args.size})보다 적다. {len(eligible)}건 전부를 쓴다.",
            file=sys.stderr,
        )

    problems = find_id_problems(eligible)
    if problems:
        print("`id` 가 라벨 키 계약(가이드 §7.2)을 지키지 않는다:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("라벨링을 시작한 뒤에는 되돌릴 수 없어 여기서 멈춘다.", file=sys.stderr)
        return 2

    # 파일을 하나라도 쓰기 전에 전부 확인한다. 중간까지 쓰고 멈추면 더 헷갈린다.
    existing = [path for path in output_paths(args.out_dir) if path.exists()]
    if existing and not (args.force or args.dry_run):
        print("이미 있는 파일을 덮어쓰려 한다:", file=sys.stderr)
        for path in existing:
            print(f"  - {path}", file=sys.stderr)
        print(
            "사람이 채운 라벨이 들어 있을 수 있다 (1인 약 6.7시간, 가이드 §8.1).\n"
            "정말 새로 만들려면 --force. 라벨을 살리려면 --out-dir 를 다른 곳으로.",
            file=sys.stderr,
        )
        return 2

    sample = stratified_sample(eligible, args.size, args.seed)
    assignments = assign_blocks([record_id_of(record) for record in sample])

    for line in summarize(sample, assignments):
        print(line)

    if args.dry_run:
        print("(dry-run: 파일을 쓰지 않았다)")
        return 0

    records_path = args.out_dir / RECORDS_FILENAME
    try:
        written = write_jsonl(
            records_path,
            (
                build_labeling_record(record, with_extra_context=args.with_extra_context)
                for record in sample
            ),
            overwrite=args.force,
        )
        print(f"레코드: {records_path} ({written}건)")

        for labeler, rows in label_rows_by_labeler(assignments).items():
            path = args.out_dir / LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
            count = write_jsonl(path, rows, overwrite=args.force)
            print(f"빈 틀: {path} ({count}건)")
    except FileExistsError as error:
        # 위 검사와 쓰기 사이에 누가 만든 경우. 배타적 생성 모드가 여기서 막는다.
        print(f"쓰는 도중 파일이 생겼다: {error.filename}. --force 로만 덮어쓴다.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
