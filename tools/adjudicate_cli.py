"""제3자 판정 CLI — 갈린 레코드에서 두 라벨 중 하나를 통째로 고른다 (이슈 #90). 담당: 성제 (sj)

왜 만드나:
    500건 본 라벨링(v3)은 불일치를 토론하지 않고 제3자가 두 라벨 중 하나를 고른다 (가이드 §8.3,
    `docs/evaluation.md` "500건 라벨링 v3 사전 등록"). 제3자는 새 라벨을 만들지 않고, 한쪽의
    이유와 다른 쪽의 등급을 섞지도 않는다. 그래서 입력은 1 또는 2 뿐이다.

대상:
    `classify.labels` 병합에서 `final` 이 비는 레코드 — `python -m classify.labels --no-write` 가
    "토론 대상"으로 세는 것과 같은 함수(`merge_labels`)로 뽑는다. 시작할 때 세 블록 합이 그
    리포트의 건수와 같은지, 그리고 `EXPECTED_TOTAL` 과 같은지 확인하고 다르면 멈춘다.

누가 어느 라벨을 붙였는지 가린다:
    화면에는 "라벨 1 / 라벨 2" 만 나온다. 1·2 순서는 record_id 해시로 정한다 — 실행마다 같고
    (이어하기·:u 에서 순서가 바뀌지 않는다), 라벨러 이름 순서와 무관하다. 고른 쪽이 누구였는지는
    파일(`chosen_labeler`)에만 남는다.

파일:
    읽기  datasets/labels/{sj,jh,hs}_main500.jsonl, main500_assignment.jsonl, main500_records.jsonl
    쓰기  datasets/labels/adjudication_main500.jsonl   판정 1건 = 1줄
          {"record_id", "batch", "block", "final": {...가이드 §7.3 final...}, "chosen_labeler"}

    개인 라벨 파일은 읽기만 한다 (§8.3 5번). `final.note` 는 고른 라벨의 note 그대로다 — 이 CLI 는
    "왜 그쪽을 골랐는지 한 줄"을 묻지 않는다. 판정은 건마다 임시 파일 → 교체로 바로 쓴다.
    쓰기 직전에 파일을 다시 읽어, 다른 블록 판정이 옆 터미널에서 돌아도 서로 덮어쓰지 않는다.

실행:
    python -m tools.adjudicate_cli --block A      # 판정자 hs
    python -m tools.adjudicate_cli --block B      # 판정자 sj
    python -m tools.adjudicate_cli --block C      # 판정자 jh
    입력: 1 또는 2  ·  :u 직전 건 다시  ·  :q 종료(판정한 건은 이미 저장됨)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from classify import sampling_main500
from classify.labels import (
    MERGED_FILENAME,
    find_label_problems,
    format_report,
    load_assignment,
    load_personal_labels,
    merge_labels,
    read_jsonl,
)
from tools.label_cli import (
    REASON_NAMES,
    RULE,
    _now,
    _replace_atomically,
    escape_control,
    load_records,
    render_record,
)

BATCH = sampling_main500.BATCH
OUT_FILENAME = f"adjudication_{BATCH}.jsonl"

# 가이드 §8.3 v3 표 — 블록 → (라벨러 쌍, 제3자). 고정이다.
BLOCK_ADJUDICATORS: dict[str, tuple[frozenset[str], str]] = {
    "A": (frozenset({"sj", "jh"}), "hs"),
    "B": (frozenset({"jh", "hs"}), "sj"),
    "C": (frozenset({"hs", "sj"}), "jh"),
}
# 2026-10-10 `python -m classify.labels --no-write` 의 "토론 대상" 건수. 개인 라벨이 바뀌어 이
# 수가 달라졌다면 판정 대상도 달라진 것이라 멈춘다.
EXPECTED_TOTAL = 150
METHOD = "THIRD_PARTY"

# 고른 라벨에서 `final` 로 그대로 옮기는 값 (가이드 §8.3, `classify.labels.build_final` 의 키 순서)
CHOSEN_FIELDS = (
    "reason_label",
    "evidence_grade",
    "evidence_text",
    "evidence_source",
    "evidence_locator",
    "confidence",
)
# 화면에 보이는 라벨 값. `labeler`·`labeled_at`·`guide_version` 은 누구 라벨인지 드러내 뺀다.
SHOWN_FIELDS = (
    "reason_label",
    "evidence_grade",
    "confidence",
    "evidence_text",
    "evidence_source",
    "evidence_locator",
    "note",
)
PROMPT = "어느 라벨? [1 / 2 · :u 직전 건 다시 · :q 종료]: "

Ask = Callable[[str], str]
Say = Callable[[str], None]


class TargetError(ValueError):
    """판정 대상을 정할 수 없다. 메시지를 그대로 보여주고 멈춘다."""


# --------------------------------------------------------------------------------------
# 대상 — labels.py 가 "토론 대상"으로 세는 레코드
# --------------------------------------------------------------------------------------


def discussion_total(merged: Sequence[dict[str, Any]]) -> int:
    """`classify.labels` 리포트가 찍는 "토론 대상 N건" 의 N. 리포트 줄에서 직접 읽는다."""
    for line in format_report(merged, batch=BATCH):
        found = re.search(r"토론 대상 (\d+)건", line)
        if found:
            return int(found.group(1))
    raise TargetError("classify.labels 리포트에서 '토론 대상' 줄을 찾지 못했다")


def targets_by_block(
    merged: Sequence[dict[str, Any]],
    assignment: Mapping[str, Mapping[str, Any]],
    *,
    expected_total: int = EXPECTED_TOTAL,
) -> dict[str, list[dict[str, Any]]]:
    """블록 → `final` 이 빈 병합 행 (블록 안 순서 = `block_position`). 어긋나면 `TargetError`."""
    blocks: dict[str, list[dict[str, Any]]] = {block: [] for block in BLOCK_ADJUDICATORS}
    problems: list[str] = []
    for row in merged:
        if row.get("final"):
            continue
        record_id = row["record_id"]
        assigned = assignment[record_id]
        block = assigned.get("block")
        labelers = sorted(str(label.get("labeler")) for label in row["labels"])
        if block not in blocks:
            problems.append(f"{record_id}: 블록 {block!r} 는 §8.3 표에 없다")
        elif len(labelers) != 2 or set(labelers) != BLOCK_ADJUDICATORS[block][0]:
            problems.append(
                f"{record_id}: 블록 {block} 인데 라벨이 {'+'.join(labelers) or '없음'} 이다 — "
                "쌍의 독립 라벨 2개가 있어야 고를 수 있다"
            )
        else:
            blocks[block].append(row)
    if problems:
        shown = " / ".join(problems[:5])
        more = f" 외 {len(problems) - 5}건" if len(problems) > 5 else ""
        raise TargetError(f"판정할 수 없는 대상이 있다: {shown}{more}")

    for rows in blocks.values():
        rows.sort(key=lambda row: assignment[row["record_id"]].get("block_position") or 0)
    total = sum(len(rows) for rows in blocks.values())
    reported = discussion_total(merged)
    if total != reported or total != expected_total:
        raise TargetError(
            f"대상 건수가 맞지 않는다: 세 블록 합 {total}건, classify.labels 토론 대상 "
            f"{reported}건, 기대 {expected_total}건"
        )
    return blocks


def load_targets(
    labels_dir: Path, merged_path: Path, *, expected_total: int = EXPECTED_TOTAL
) -> dict[str, list[dict[str, Any]]]:
    """`python -m classify.labels --no-write` 와 같은 입력·같은 병합으로 대상을 뽑는다."""
    personal = load_personal_labels(labels_dir, BATCH)
    problems = find_label_problems(personal)
    if problems:
        raise TargetError("정의되지 않은 라벨 값이 있다: " + " / ".join(problems[:5]))
    assignment = load_assignment(labels_dir, BATCH)
    if not assignment:
        raise TargetError(f"배분 파일이 없다: {labels_dir / sampling_main500.ASSIGNMENT_OUT}")
    existing = read_jsonl(merged_path)
    if {str(row.get("batch")) for row in existing} - {BATCH}:
        raise TargetError(f"{merged_path} 는 다른 묶음의 병합 파일이다")
    try:
        merged = merge_labels(personal, batch=BATCH, existing=existing, assignment=assignment)
    except ValueError as error:
        raise TargetError(str(error)) from None
    return targets_by_block(merged, assignment, expected_total=expected_total)


# --------------------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------------------


def display_order(record_id: str, labels: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """[라벨 1, 라벨 2]. record_id 해시의 한 비트로 뒤집어, 번호에서 라벨러를 알 수 없게 한다."""
    ordered = sorted(labels, key=lambda label: str(label.get("labeler")))
    if hashlib.sha256(record_id.encode("utf-8")).digest()[0] & 1:
        ordered.reverse()
    return ordered


def render_label(number: int, label: Mapping[str, Any]) -> str:
    """라벨 1개. 라벨러가 쓴 텍스트도 남의 맥락을 복사한 것이라 `escape_control` 을 거친다."""
    lines = [f"== 라벨 {number} =="]
    for name in SHOWN_FIELDS:
        value = label.get(name)
        if name == "reason_label" and value in REASON_NAMES:
            shown = f"{value} ({REASON_NAMES[value]})"
        elif value is None or value == "":
            shown = "(없음)"
        else:
            shown = escape_control(str(value)).replace("\n", "\n" + " " * 19)
        lines.append(f"  {name:<16} {shown}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# 세션
# --------------------------------------------------------------------------------------


class QuitRequested(Exception):
    """:q — 끝낸다. 판정한 건은 이미 파일에 있다."""


class UndoRequested(Exception):
    """:u — 직전에 판정한 건을 다시 연다."""


class AdjudicationSession:
    """한 블록의 제3자가 갈린 건을 차례로 고르는 한 번의 실행."""

    def __init__(
        self,
        block: str,
        targets: Sequence[dict[str, Any]],
        records: Mapping[str, dict[str, Any]],
        out_path: Path,
        *,
        ask: Ask | None = None,
        say: Say = print,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.block = block
        self.adjudicator = BLOCK_ADJUDICATORS[block][1]
        self.targets = {row["record_id"]: row for row in targets}
        self.records = records
        self.out_path = out_path
        self.ask = ask or input
        self.say = say
        self.now = now
        self.history: list[str] = []
        # 이어하기: 이 블록에서 이미 판정한 건. 파일 순서 = 판정한 순서 (다시 판정하면 끝으로 간다)
        self.done = [
            row["record_id"]
            for row in read_jsonl(out_path)
            if row.get("block") == block and row.get("record_id") in self.targets
        ]

    @property
    def header(self) -> str:
        return f"블록 {self.block} — 판정자 {self.adjudicator}"

    def run(self) -> int:
        """이번 실행에서 판정(다시 판정 포함)한 횟수를 돌려준다."""
        saved = 0
        target: str | None = None
        while True:
            record_id = target or next((rid for rid in self.targets if rid not in self.done), None)
            target = None
            try:
                if record_id is None:
                    self.say(f"{self.header}: {len(self.targets)}건을 모두 판정했다.")
                    self.choose("직전 건을 다시 하려면 :u, 끝내려면 Enter: ", finishing=True)
                    return saved
                self.say("")
                self.say(self.screen(record_id))
                choice = self.choose(PROMPT)
            except QuitRequested:
                self.say(f"종료한다. 판정 {len(self.done)}/{len(self.targets)}건이 저장돼 있다.")
                return saved
            except UndoRequested:
                target = self.previous(current=record_id)
                if target is None:
                    self.say("다시 할 직전 건이 없다.")
                    target = record_id if record_id in self.done else None
                continue
            self.save(record_id, choice)
            saved += 1

    def screen(self, record_id: str) -> str:
        first, second = display_order(record_id, self.targets[record_id]["labels"])
        if record_id in self.done:
            progress = f"[다시 판정 · 완료 {len(self.done)}/{len(self.targets)}]"
        else:
            progress = f"[{len(self.done) + 1}/{len(self.targets)}]"
        return "\n".join(
            [
                self.header,
                progress,
                render_record(self.records[record_id]),
                render_label(1, first),
                "",
                render_label(2, second),
                RULE,
            ]
        )

    def choose(self, prompt: str, *, finishing: bool = False) -> int:
        """1 또는 2 가 들어올 때까지 묻는다. `finishing` 이면 Enter 로 끝낸다."""
        while True:
            try:
                raw = self.ask(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                raise QuitRequested from None
            if raw == ":u":
                raise UndoRequested
            if raw == ":q" or (finishing and not raw):
                raise QuitRequested
            if raw in ("1", "2") and not finishing:
                return int(raw)
            self.say(
                "  ! :u 또는 Enter" if finishing else "  ! 1 또는 2 (:u 직전 건 다시 · :q 종료)"
            )

    def previous(self, current: str | None) -> str | None:
        """이번 실행에서 판정한 순서를 거슬러 간다. 없으면 파일에서 가장 나중에 판정한 건."""
        while self.history:
            candidate = self.history.pop()
            if candidate != current:
                return candidate
        return next((rid for rid in reversed(self.done) if rid != current), None)

    def save(self, record_id: str, choice: int) -> None:
        """판정 1건을 바로 파일에 쓴다. 같은 record_id 의 앞선 판정은 지우고 끝에 붙인다."""
        chosen = display_order(record_id, self.targets[record_id]["labels"])[choice - 1]
        row = {
            "record_id": record_id,
            "batch": BATCH,
            "block": self.block,
            "final": {
                **{name: chosen.get(name) for name in CHOSEN_FIELDS},
                "method": METHOD,
                "adjudicated_by": [self.adjudicator],
                "adjudicated_at": self.now().isoformat(timespec="seconds"),
                "note": chosen.get("note"),
            },
            "chosen_labeler": chosen.get("labeler"),
        }
        rows = [old for old in read_jsonl(self.out_path) if old.get("record_id") != record_id]
        rows.append(row)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        _replace_atomically(
            self.out_path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows)
        )
        self.done = [rid for rid in self.done if rid != record_id] + [record_id]
        self.history = [rid for rid in self.history if rid != record_id] + [record_id]
        self.say(f"저장했다: 라벨 {choice} ({len(self.done)}/{len(self.targets)})")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.adjudicate_cli",
        description="제3자 판정 CLI — 갈린 레코드의 두 라벨 중 하나를 고른다 (#90, 가이드 §8.3).",
    )
    parser.add_argument("--block", required=True, choices=tuple(BLOCK_ADJUDICATORS))
    parser.add_argument("--labels-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument("--out", type=Path, default=None, help=f"기본: <labels-dir>/{OUT_FILENAME}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")

    records_path = args.labels_dir / sampling_main500.RECORDS_OUT
    if not records_path.is_file():
        print(f"파일이 없다: {records_path}", file=sys.stderr)
        return 2
    records, problems = load_records(records_path)
    try:
        if problems:
            raise TargetError(" / ".join(problems[:5]))
        blocks = load_targets(args.labels_dir, Path("datasets") / MERGED_FILENAME)
        missing = [
            row["record_id"]
            for rows in blocks.values()
            for row in rows
            if row["record_id"] not in records
        ]
        if missing:
            raise TargetError(f"레코드 파일에 없는 대상 {len(missing)}건: {missing[:3]}")
    except TargetError as error:
        print(f"멈춘다 — {error}", file=sys.stderr)
        return 2

    out_path = args.out or args.labels_dir / OUT_FILENAME
    session = AdjudicationSession(args.block, blocks[args.block], records, out_path)
    print(session.header)
    counts = " · ".join(f"{block} {len(rows)}건" for block, rows in blocks.items())
    total = sum(len(rows) for rows in blocks.values())
    print(f"대상: {counts} · 합 {total}건 (classify.labels 토론 대상 {EXPECTED_TOTAL}건과 일치)")
    print(f"이 블록 판정 완료 {len(session.done)}/{len(session.targets)}건 → {out_path}")
    session.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
