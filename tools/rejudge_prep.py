"""v3 재판정 준비 — 갈린 69건의 v2 라벨 칸을 비워 label_cli 가 다시 띄우게 한다 (#164).

담당: 성제 (sj)

왜 만드나:
    가이드 v3 §8.4.1.1 은 중간 점검에서 갈린 69건을 두 라벨러가 v3 기준으로 각자 다시 판정하게 한다.
    label_cli 에는 특정 `record_id` 를 다시 여는 방법이 없고(선행 조건 9), 이어하기는 파일 순서로
    **라벨 칸이 빈 첫 줄**부터 연다. 그래서 대상 줄의 라벨 칸을 빈 틀(`empty_label_row`)로 되돌리면
    label_cli 가 그 줄들을 1~50번 자리에서 먼저 띄우고, 저장은 제자리 덮어쓰기라 같은
    `record_id`·`labeler` 에 줄이 두 개 생기지 않는다(선행 조건 6). 지워지는 v2 라벨은
    `datasets/labels/snapshots/v2_interim/` 에 바이트 그대로 남아 있어야 한다(선행 조건 12).

무엇을 고치고 무엇을 안 고치나:
    대상은 `docs/reports/interim_90_rejudge.md` 의 라벨러 절(`## sj — 48건` 등) 표에 있는
    `record_id` 뿐이다. 대상 줄은 `record_id`·`labeler` 만 남기고 빈 틀로 바꾸고, 나머지 줄은
    **바이트 단위로** 그대로 둔다 — 쓰기 전에 계획을, 쓴 뒤에 파일을 다시 읽어 원본과 줄마다
    비교한다.
    지우는 줄의 이전 라벨 값은 화면에 찍지 않는다. 재판정하면서 이전 라벨을 보지 않는다
    (가이드 §9-11) — 자기 것도.

    이미 비어 있는 대상 줄은 건너뛴다. 그래서 두 번 돌려도 같다. 비운 뒤 v3 로 다시 저장한 줄은
    채워진 줄이라 다시 돌리면 지울 대상이 되지만, 스냅샷의 v2 줄과 바이트가 달라 `--apply` 가
    거부한다 — 재판정한 라벨을 실수로 지우지 않는다.

실행 (각자 자기 파일에. 기본은 보기만 한다):
    python -m tools.rejudge_prep --labeler sj            # 무엇을 비울지 보여주기만 (--dry-run)
    python -m tools.rejudge_prep --labeler sj --apply    # 실제로 쓴다
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classify import sampling_main500
from classify.labels import is_filled, read_jsonl
from classify.sampling import LABELERS, empty_label_row

DEFAULT_LABELS_DIR = Path("datasets/labels")
DEFAULT_REPORT = Path("docs/reports/interim_90_rejudge.md")
SNAPSHOT_SUBDIR = Path("snapshots/v2_interim")

# 라벨러 절 머리 줄 — `## sj — 48건 (A 21 · C 27)`. 숫자는 표의 행 수와 맞춰 본다.
SECTION_HEADING = re.compile(r"^## (?P<labeler>[a-z]+) — (?P<count>\d+)건")
# 표 행 — `| A01 | `92620e7f-…` | 저장소 | …`. 첫 칸이 건 이름, 둘째 칸이 record_id.
TARGET_ROW = re.compile(r"^\|\s*(?P<case>[A-Z]\d+)\s*\|\s*`(?P<record_id>[0-9a-f-]{36})`\s*\|")


class PrepError(ValueError):
    """준비를 멈춰야 하는 문제. 메시지가 그대로 사람에게 보인다. 파일에는 아무것도 쓰지 않았다."""


# --------------------------------------------------------------------------------------
# 대상 목록
# --------------------------------------------------------------------------------------


def parse_targets(text: str) -> dict[str, list[tuple[str, str]]]:
    """재판정 문서 → 라벨러 → [(건 이름, record_id)].

    라벨러 절 밖의 표(입력 sha256 표 등)와 본문에 적힌 record_id(블록 A 51번 등)는 읽지 않는다.
    절 머리 줄의 건수와 표의 행 수가 다르거나 한 절에 같은 id 가 두 번 나오면 멈춘다 — 문서를
    고치다 행이 빠졌을 수 있다.
    """
    targets: dict[str, list[tuple[str, str]]] = {}
    expected: dict[str, int] = {}
    current: str | None = None
    for line in text.split("\n"):
        heading = SECTION_HEADING.match(line)
        if heading:
            current = heading["labeler"]
            if current not in LABELERS:
                raise PrepError(f"재판정 문서의 절 {current!r} 은 라벨러가 아니다")
            if current in targets:
                raise PrepError(f"재판정 문서에 {current} 절이 두 번 있다")
            targets[current] = []
            expected[current] = int(heading["count"])
            continue
        if line.startswith("## "):
            current = None
            continue
        row = TARGET_ROW.match(line)
        if current is not None and row:
            targets[current].append((row["case"], row["record_id"]))

    problems = []
    for labeler, rows in targets.items():
        if len(rows) != expected[labeler]:
            problems.append(f"{labeler} 절은 {expected[labeler]}건인데 표에 {len(rows)}행")
        ids = [record_id for _, record_id in rows]
        if len(set(ids)) != len(ids):
            problems.append(f"{labeler} 절에 같은 record_id 가 두 번 있다")
    if problems:
        raise PrepError(" / ".join(problems))
    return targets


def check_against_assignment(
    labeler: str, targets: Sequence[tuple[str, str]], assignment: Mapping[str, Mapping[str, Any]]
) -> None:
    """대상이 이 라벨러가 맡은 블록의 처음 50건(`interim`)인가. 재판정은 그 안에서만 한다."""
    problems = []
    for case, record_id in targets:
        row = assignment.get(record_id)
        if row is None:
            problems.append(f"{case} {record_id}: 배분 파일에 없다")
        elif labeler not in (row.get("labelers") or ()):
            problems.append(f"{case} {record_id}: 블록 {row.get('block')} 은 {labeler} 몫이 아니다")
        elif not row.get("interim"):
            problems.append(f"{case} {record_id}: 처음 50건(interim)이 아니다")
    if problems:
        raise PrepError("재판정 문서가 배분 파일과 맞지 않는다: " + " / ".join(problems[:5]))


# --------------------------------------------------------------------------------------
# 줄 단위 계획·검증
# --------------------------------------------------------------------------------------


def split_lines(data: bytes) -> list[bytes]:
    """줄 끝을 붙인 채로 `\\n` 에서만 나눈다. `b"".join(split_lines(x)) == x` 가 항상 성립한다.

    `splitlines()` 를 쓰지 않는다 — U+2028 같은 문자에서 레코드를 자른다 (#146).
    """
    parts = data.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _record_id(line: bytes) -> str | None:
    """줄의 record_id. 빈 줄이면 None. JSON 이 아니면 `PrepError`."""
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError as error:
        raise PrepError(f"JSON 이 아닌 줄이 있다 ({error.msg})") from None
    if not isinstance(row, dict):
        raise PrepError("JSON 객체가 아닌 줄이 있다")
    return str(row.get("record_id"))


def blank_line(record_id: str, labeler: str, original: bytes) -> bytes:
    """빈 틀 1줄. 직렬화는 label_cli 저장(`LabelFile.save`)과 같고, 줄 끝은 원래 줄을 따른다."""
    ending = b"\r\n" if original.endswith(b"\r\n") else b"\n" if original.endswith(b"\n") else b""
    row = empty_label_row(record_id, labeler)
    return json.dumps(row, ensure_ascii=False).encode("utf-8") + ending


@dataclass
class Plan:
    """한 라벨 파일에 대한 계획. `lines` 는 쓸 내용, 나머지는 보고용."""

    lines: list[bytes]
    cleared: list[str] = field(default_factory=list)
    """이번에 비울 record_id (파일 순서)."""
    already_blank: list[str] = field(default_factory=list)
    """대상이지만 이미 비어 있는 record_id. 건너뛴다."""

    @property
    def data(self) -> bytes:
        return b"".join(self.lines)


def plan_clear(data: bytes, labeler: str, targets: Collection[str]) -> Plan:
    """대상 줄만 빈 틀로 바꾼 계획. 파일 계약(§7.2)이 깨져 있으면 `PrepError`.

    대상이 파일에 없거나 두 번 있거나, 그 줄의 `labeler` 가 파일 주인이 아니면 멈춘다 — 엉뚱한
    파일에 돌렸거나 손으로 고친 흔적이다.
    """
    original = split_lines(data)
    plan = Plan(lines=list(original))
    seen: set[str] = set()
    problems: list[str] = []
    for index, line in enumerate(original):
        record_id = _record_id(line)
        if record_id is None or record_id not in targets:
            continue
        if record_id in seen:
            problems.append(f"{index + 1}번째 줄: 대상 {record_id} 가 파일에 두 번 있다")
            continue
        seen.add(record_id)
        row = json.loads(line)
        if row.get("labeler") != labeler:
            problems.append(
                f"{index + 1}번째 줄: labeler={row.get('labeler')!r} — {labeler} 의 파일이 아니다"
            )
            continue
        if not is_filled(row):
            plan.already_blank.append(record_id)
            continue
        plan.lines[index] = blank_line(record_id, labeler, line)
        plan.cleared.append(record_id)
    missing = sorted(set(targets) - seen)
    if missing:
        problems.append(f"대상 {len(missing)}건이 파일에 없다: {missing[:3]}")
    if problems:
        raise PrepError(" / ".join(problems))
    return plan


def verify(original: bytes, written: bytes, labeler: str, targets: Collection[str]) -> list[str]:
    """`written` 이 `original` 에서 대상 줄만 빈 틀로 바꾼 것인지. 빈 목록이면 통과.

    계획을 만든 코드와 따로 다시 나눠 본다. 대상 밖의 줄은 바이트가 같아야 하고, 대상 줄은
    원본 그대로(이미 비어 있던 줄)이거나 정확히 빈 틀이어야 한다.
    """
    before, after = split_lines(original), split_lines(written)
    if len(before) != len(after):
        return [f"줄 수가 바뀌었다: {len(before)} → {len(after)}"]
    problems = []
    for number, (old, new) in enumerate(zip(before, after, strict=True), start=1):
        if old == new:
            continue
        record_id = _record_id(old)
        if record_id not in targets:
            problems.append(f"{number}번째 줄: 대상이 아닌데 바뀌었다")
        elif new != blank_line(record_id, labeler, old):
            problems.append(f"{number}번째 줄: 대상인데 빈 틀이 아니다")
    return problems


def check_snapshot(data: bytes, snapshot: bytes, cleared: Collection[str]) -> list[str]:
    """비울 줄이 스냅샷에 바이트 그대로 있나. 빈 목록이면 통과.

    파일 전체가 같을 필요는 없다 — 스냅샷 뒤에 51번 이후를 더 라벨했을 수 있다. 지워지는 줄만
    보존돼 있으면 v2 라벨은 잃지 않는다(선행 조건 12). 다르면 그 줄은 스냅샷 뒤에 다시 저장된
    것(예: v3 재판정 라벨)이라 지우면 안 된다.

    지울 record_id 가 스냅샷에 두 번 있으면 거부한다 (`plan_clear` 와 같다). 사전으로 모으면
    마지막 줄만 남아, 앞 줄이 지울 줄과 다른데도 보존 검사를 통과할 수 있다.
    """
    kept: dict[str | None, bytes] = {}
    problems = []
    for number, line in enumerate(split_lines(snapshot), start=1):
        record_id = _record_id(line)
        if record_id in cleared and record_id in kept:
            problems.append(f"스냅샷 {number}번째 줄: 대상 {record_id} 가 두 번 있다")
            continue
        kept[record_id] = line
    if problems:
        return problems
    for line in split_lines(data):
        record_id = _record_id(line)
        if record_id in cleared and kept.get(record_id) != line:
            what = "스냅샷에 없다" if record_id not in kept else "스냅샷과 다르다"
            problems.append(f"{record_id}: {what}")
    return problems


def write_atomically(path: Path, data: bytes) -> None:
    """임시 파일에 다 쓴 뒤 교체한다. 쓰는 도중 꺼져도 기존 파일은 온전하다 (label_cli 와 같다)."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.rejudge_prep",
        description="v3 재판정 대상 줄의 라벨 칸을 비운다 (#164, 가이드 §8.4.1.1). 기본은 보기만.",
    )
    parser.add_argument("--labeler", required=True, choices=LABELERS)
    parser.add_argument("--labels-dir", type=Path, default=DEFAULT_LABELS_DIR)
    parser.add_argument(
        "--labels-file", type=Path, default=None, help="기본: <labels-dir>/<labeler>_main500.jsonl"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help=f"기본: <labels-dir>/{SNAPSHOT_SUBDIR.as_posix()}/<labeler>_main500.jsonl",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="재판정 대상 문서")
    parser.add_argument(
        "--assignment",
        type=Path,
        default=None,
        help=f"기본: <labels-dir>/{sampling_main500.ASSIGNMENT_OUT}",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="보여주기만 한다 (기본)")
    mode.add_argument("--apply", action="store_true", help="라벨 파일에 실제로 쓴다")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """정상이면 0, 멈추면 2."""
    # 인자 해석보다 먼저 — `--help` 도 한국어라 Windows 기본 코드페이지(cp949)로는 못 찍는다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)

    file_name = sampling_main500.LABEL_OUT_TEMPLATE.format(labeler=args.labeler)
    label_path = args.labels_file or args.labels_dir / file_name
    snapshot_path = args.snapshot or args.labels_dir / SNAPSHOT_SUBDIR / file_name
    assignment_path = args.assignment or args.labels_dir / sampling_main500.ASSIGNMENT_OUT
    for path in (label_path, args.report, assignment_path):
        if not path.is_file():
            print(f"파일이 없다: {path}", file=sys.stderr)
            return 2

    try:
        targets = parse_targets(args.report.read_text(encoding="utf-8")).get(args.labeler)
        if not targets:
            raise PrepError(f"재판정 문서 {args.report} 에 {args.labeler} 절이 없다")
        assignment = {row["record_id"]: row for row in read_jsonl(assignment_path)}
        check_against_assignment(args.labeler, targets, assignment)
        target_ids = {record_id for _, record_id in targets}
        original = label_path.read_bytes()
        plan = plan_clear(original, args.labeler, target_ids)
    except PrepError as error:
        print(f"멈춘다: {error}", file=sys.stderr)
        return 2

    problems = verify(original, plan.data, args.labeler, target_ids)
    if problems:
        print("계획 검증 실패 — 쓰지 않았다:", *problems[:10], sep="\n  ", file=sys.stderr)
        return 2

    case_of = {record_id: case for case, record_id in targets}
    print(f"{label_path}: 대상 {len(targets)}건 ({args.report})")
    print(f"- 비울 줄 {len(plan.cleared)}건, 이미 빈 줄 {len(plan.already_blank)}건")
    print("- 대상 밖 줄은 바이트 그대로다 (검증 통과)")
    if plan.cleared:
        print("- 비울 건: " + " ".join(case_of[record_id] for record_id in plan.cleared))

    if not args.apply:
        print("dry-run — 아무것도 쓰지 않았다. 쓰려면 --apply 를 붙인다.")
        return 0
    if not plan.cleared:
        print("비울 줄이 없다 — 쓰지 않았다.")
        return 0

    if not snapshot_path.is_file():
        print(
            f"스냅샷이 없다: {snapshot_path} — 지울 v2 라벨이 보존돼 있지 않아 쓰지 않았다",
            file=sys.stderr,
        )
        return 2
    problems = check_snapshot(original, snapshot_path.read_bytes(), set(plan.cleared))
    if problems:
        print(
            "지울 줄이 스냅샷과 같지 않다 — 쓰지 않았다 (스냅샷 뒤에 다시 저장한 줄일 수 있다):",
            *problems[:10],
            sep="\n  ",
            file=sys.stderr,
        )
        return 2

    write_atomically(label_path, plan.data)
    problems = verify(original, label_path.read_bytes(), args.labeler, target_ids)
    if problems:
        write_atomically(label_path, original)
        print("쓴 뒤 검증 실패, 되돌렸다:", *problems[:10], sep="\n  ", file=sys.stderr)
        return 2
    print(f"썼다: {len(plan.cleared)}건을 비웠다.")
    print(f"다음: python -m tools.label_cli --labeler {args.labeler}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
