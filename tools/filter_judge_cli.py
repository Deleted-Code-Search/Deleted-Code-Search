"""필터 정밀도 판정 CLI — 표본 1건을 보여주고 "의미 있는 삭제인가" 예/아니오를 받는다 (이슈 #89).
담당: 성제 (sj)

사전 등록: `docs/evaluation.md` "필터 정밀도 사전 등록". 판정 질문과 경계는 라벨 가이드 §6.3.3 이다.

무엇을 보여주고 무엇을 안 보여주나:
    `eval/filter_precision_sample.py` 가 만든 판정용 파일(`records.jsonl`)만 읽는다. 그 파일에는
    `filter_status` 가 없고, 들어 있으면 시작하지 않는다 — 키 파일이나 조립 결과를 잘못 넣은 것이다.
    NOISE_MOVE 근거는 "참고: 같은 커밋의 비슷한 함수"로, 삭제 줄 수는 모든 건에 같은 모양으로
    보여 준다. "필터가 이동으로 봤다" 같은 말은 쓰지 않는다. 같은 커밋·같은 파일의 추가 헝크도
    모든 건에 삭제된 본문 바로 아래 보여 준다 (#155) — 1차 판정은 이게 없어 커밋 메시지로 짐작했다.

    표시는 `tools/label_cli.py` 의 것을 그대로 쓴다 (#109·#110). 레코드에서 온 텍스트는 모두
    `escape_control` 을 거친다 — 커밋 메시지·코드는 남의 텍스트다 (CWE-150).

파일:
    읽기  <records>                       판정용 레코드 (기본 data/filter_precision/records.jsonl)
    쓰기  <records 폴더>/{judge}_judgments.jsonl   판정자별. 한 줄 = 한 건의 판정

    건마다 파일 전체를 임시 파일 → 교체로 다시 쓴다. 도중에 꺼져도 앞서 저장한 판정은 남고,
    다시 실행하면 판정하지 않은 첫 건부터 이어진다.

실행:
    python -m tools.filter_judge_cli --judge sj
    입력 칸 어디서나:  :q 종료(지금 건은 저장 안 함)  ·  :u 직전 건 다시 판정
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from classify.sampling import LABELERS
from eval.filter_precision_sample import HIDDEN_KEYS, RECORDS_FILENAME
from tools.label_cli import (
    ADDED_HUNKS_FIELD,
    RULE,
    QuitRequested,
    UndoRequested,
    _code,
    _one_line,
    _replace_atomically,
    _text_lines,
    added_hunks_lines,
)

JUDGMENT_FILENAME_TEMPLATE = "{judge}_judgments.jsonl"
# 판정 1줄 (필드 순서 그대로)
JUDGMENT_FIELDS = ("sample_id", "record_id", "judge", "meaningful", "note", "judged_at")

COMMAND_HELP = "명령 (입력 칸 어디서나): :q 종료(지금 건 저장 안 함) · :u 직전 건 다시 판정"
QUESTION_PROMPT = "배울 게 있는 의미 있는 삭제인가? [y/n]: "
START_SCREEN = "\n".join(
    [
        RULE,
        "필터 정밀도 판정 (#89, CHARTER §10.1)",
        "",
        "  ** 판정자끼리 서로의 판정 파일을 보지 않는다. 판정이 끝날 때까지 이야기하지 않는다. **",
        "  ** key.json 은 열지 않는다. **",
        "",
        "질문: 배울 게 있는 의미 있는 삭제인가?  y = 예 / n = 아니오",
        "기준 (라벨 가이드 §6.3.3, #155). n 은 아래 다섯 가지뿐이고 나머지는 전부 y:",
        "  n1  순수 이동·리네임 — 본문이 거의 그대로 다른 곳으로 갔다",
        "      (유사도 0.9에 조금 못 미쳐도, 가까운 다른 커밋에서 다시 나타나도 — note 에 SHA)",
        "  n2  기계적 포맷·스타일 변환 — 포매터, 따옴표·줄바꿈·import 정렬, 문법만 최신으로",
        "  n3  생성·벤더링 코드 — 자동 생성 파일, 다른 프로젝트 코드를 복사해 넣은 것",
        "  n4  다른 저장소로 분리 — 코드가 통째로 다른 레포로 옮겨 갔다",
        "  n5  사소한 다듬기 (부분 삭제 PARTIAL 에만) — 지운 몇 줄이 같은 자리에서 거의 같은",
        "      뜻으로 바뀌었다. 오타, 변수 이름, 인자 순서, 로그 문구 등 (ADR-015 와 같은 취지)",
        "      FULL_FUNCTION 에는 쓰지 않는다. 부분 삭제는 n5 까지 보고 판단한다",
        "헷갈렸던 경우:",
        "  같은 로직이 다른 모양으로 — 포매터만이면 n2, 부분 삭제에서 거의 같은 뜻이면 n5,",
        "    구조·알고리즘·인터페이스가 바뀌었으면 y",
        "  revert 로 지워짐 y · 폴더·모듈 통째 삭제 y (다른 레포로 옮겼으면 n4) · 테스트 삭제 y",
        "",
        "** 판정은 '추가 헝크'(diff)를 보고 한다. 커밋 메시지만으로 판정하지 않는다. **",
        "추가 헝크는 같은 파일만 담는다. 다른 파일·저장소로 갔는지는 source 링크로 본다.",
        "n 이면 note 에 몇 번인지와 근거를 적는다. '참고' 항목은 판단 재료일 뿐 정답이 아니다.",
        RULE,
    ]
)

Ask = Callable[[str], str]
Say = Callable[[str], None]

COMMANDS: dict[str, type[Exception]] = {":q": QuitRequested, ":u": UndoRequested}


class JudgeFileError(ValueError):
    """판정용 레코드나 판정 파일을 쓸 수 없다. 메시지에 파일과 줄 번호가 있다."""


def parse_answer(raw: str) -> bool:
    """y/n 만 받는다. 빈 값은 받지 않는다 — 기본값이 있으면 Enter 연타가 판정이 된다."""
    value = raw.strip().lower()
    if value in ("y", "yes", "예", "ㅇ"):
        return True
    if value in ("n", "no", "아니오", "ㄴ"):
        return False
    raise ValueError(f"{raw!r} — y(예) 또는 n(아니오)")


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """(줄 번호, 객체). `"\\n"` 으로만 나눈다 (#146)."""
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise JudgeFileError(f"{path}:{line_no}: JSON 이 아니다 ({error.msg})") from None
        if not isinstance(row, dict):
            raise JudgeFileError(f"{path}:{line_no}: 한 줄은 JSON 객체여야 한다")
        rows.append((line_no, row))
    return rows


def load_records(path: Path) -> list[dict[str, Any]]:
    """판정용 레코드. 필터 판정 키가 있거나 sample_id 가 비었거나 겹치면 거부한다."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, row in _read_jsonl(path):
        leaked = sorted(HIDDEN_KEYS & row.keys())
        if leaked:
            raise JudgeFileError(
                f"{path}:{line_no}: {', '.join(leaked)} 가 들어 있다 — 판정용 파일이 아니다"
                " (eval.filter_precision_sample 이 만든 records.jsonl 을 쓴다)"
            )
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise JudgeFileError(f"{path}:{line_no}: sample_id 가 없다")
        if sample_id in seen:
            raise JudgeFileError(f"{path}:{line_no}: sample_id {sample_id} 가 중복된다")
        seen.add(sample_id)
        records.append(row)
    if not records:
        raise JudgeFileError(f"{path}: 레코드가 없다")
    return records


def load_judgments(
    path: Path, judge: str, records: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """sample_id → 저장된 판정. 없는 파일은 빈 사전. 계약을 어긴 줄이 있으면 거부한다."""
    if not path.exists():
        return {}
    record_ids = {record["sample_id"]: record.get("record_id") for record in records}
    judgments: dict[str, dict[str, Any]] = {}
    for line_no, row in _read_jsonl(path):
        where = f"{path}:{line_no}"
        sample_id = row.get("sample_id")
        if row.get("judge") != judge:
            raise JudgeFileError(f"{where}: judge={row.get('judge')!r} — {judge} 의 파일이 아니다")
        if sample_id not in record_ids:
            raise JudgeFileError(f"{where}: sample_id={sample_id!r} 가 판정용 레코드에 없다")
        if row.get("record_id") != record_ids[sample_id]:
            raise JudgeFileError(f"{where}: record_id 가 판정용 레코드와 다르다 — 표본이 바뀌었다")
        if sample_id in judgments:
            raise JudgeFileError(f"{where}: sample_id={sample_id} 가 중복된다")
        if not isinstance(row.get("meaningful"), bool):
            raise JudgeFileError(f"{where}: meaningful 은 true/false 여야 한다")
        judgments[sample_id] = row
    return judgments


def render_record(record: Mapping[str, Any]) -> str:
    """판정용 레코드 1건 화면. 레코드에서 온 텍스트는 모두 이스케이프한다 (CWE-150)."""
    function = record.get("function_signature") or record.get("function_name")
    lines = [
        RULE,
        f"sample     {_one_line(record.get('sample_id'))}",
        f"repo       {_one_line(record.get('repo'))}",
        f"file       {_one_line(record.get('file_path'))}",
        f"function   {_one_line(function)}",
        f"kind       {_one_line(record.get('deletion_kind'))}",
        f"source     {_one_line(record.get('source_url'))}",
        "",
        "== 커밋 메시지 ==",
        *(f"  {line}" for line in _text_lines(str(record.get("commit_message") or "(없음)"))),
        "",
        f"== 삭제된 본문 (deleted_body, {_one_line(record.get('deleted_line_count'))}줄) ==",
        _code(record.get("deleted_body")),
        "",
        *added_hunks_lines(record.get(ADDED_HUNKS_FIELD)),
    ]
    similar = record.get("similar_function")
    if isinstance(similar, Mapping):
        lines += [
            "",
            "참고: 같은 커밋의 비슷한 함수",
            f"  경로 {_one_line(similar.get('file_path'))} · "
            f"이름 {_one_line(similar.get('function_name'))} · "
            f"줄 {_one_line(similar.get('start_line'))}-{_one_line(similar.get('end_line'))} · "
            f"유사도 {_one_line(similar.get('similarity'))}",
        ]
    lines.append(RULE)
    return "\n".join(lines)


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class JudgeSession:
    """한 판정자가 자기 판정 파일을 채우는 한 번의 실행."""

    judge: str
    records: list[dict[str, Any]]
    path: Path
    judgments: dict[str, dict[str, Any]] = field(default_factory=dict)
    ask: Ask = input
    say: Say = print
    now: Callable[[], datetime] = _now
    history: list[str] = field(default_factory=list)
    saved: int = 0

    def next_index(self) -> int | None:
        """판정하지 않은 첫 건 — 중단 후 이어하기가 이것으로 된다."""
        return next(
            (
                i
                for i, record in enumerate(self.records)
                if record["sample_id"] not in self.judgments
            ),
            None,
        )

    def run(self) -> int:
        """이번 실행에서 새로 판정한 건수."""
        self.say(START_SCREEN)
        self.say(COMMAND_HELP)
        target: int | None = None
        while True:
            index = target if target is not None else self.next_index()
            editing = target is not None
            target = None
            if index is None:
                self.say(f"{len(self.records)}건을 모두 판정했다. 판정 파일: {self.path}")
                return self.saved
            record = self.records[index]
            self.say("")
            self.say(f"[{len(self.judgments)}/{len(self.records)}] {self.judge}")
            self.say(render_record(record))
            if editing:
                previous = self.judgments[record["sample_id"]]
                answer = "y" if previous["meaningful"] else "n"
                self.say(f"[다시 판정] 저장된 값: {answer} / note {previous.get('note')!r}")
            try:
                meaningful = self.ask_until(QUESTION_PROMPT, parse_answer)
                note = self.ask_until("note (없으면 Enter): ", lambda raw: raw or None)
            except QuitRequested:
                self.say("종료한다. 지금 건은 저장하지 않았다. 다시 실행하면 여기서 이어진다.")
                return self.saved
            except UndoRequested:
                previous_index = self.previous_index(record["sample_id"])
                if previous_index is None:
                    self.say("다시 판정할 직전 건이 없다.")
                    target = index if editing else None
                else:
                    target = previous_index
                continue
            if not editing:
                self.saved += 1
            self.save(record, meaningful, note)

    def previous_index(self, current: str) -> int | None:
        """이번 실행에서 저장한 순서를 거슬러 간다. 없으면 파일에서 가장 나중에 판정한 건."""
        while self.history:
            sample_id = self.history.pop()
            if sample_id != current:
                return self._index_of(sample_id)
        stamped = [
            (row.get("judged_at") or "", sample_id)
            for sample_id, row in self.judgments.items()
            if sample_id != current
        ]
        return self._index_of(max(stamped)[1]) if stamped else None

    def _index_of(self, sample_id: str) -> int:
        return next(i for i, r in enumerate(self.records) if r["sample_id"] == sample_id)

    def save(self, record: Mapping[str, Any], meaningful: bool, note: str | None) -> None:
        """판정 1건을 넣고 파일 전체를 다시 쓴다. 줄 순서는 판정용 레코드 순서다."""
        sample_id = record["sample_id"]
        self.judgments[sample_id] = {
            "sample_id": sample_id,
            "record_id": record.get("record_id"),
            "judge": self.judge,
            "meaningful": meaningful,
            "note": note,
            "judged_at": self.now().isoformat(timespec="seconds"),
        }
        ordered = [
            self.judgments[r["sample_id"]] for r in self.records if r["sample_id"] in self.judgments
        ]
        text = "".join(
            json.dumps({key: row.get(key) for key in JUDGMENT_FIELDS}, ensure_ascii=False) + "\n"
            for row in ordered
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _replace_atomically(self.path, text)
        if sample_id in self.history:
            self.history.remove(sample_id)
        self.history.append(sample_id)
        self.say(f"저장했다: {'y' if meaningful else 'n'}")

    def ask_until[T](self, prompt: str, parse: Callable[[str], T]) -> T:
        """맞는 값이 들어올 때까지 다시 묻는다."""
        while True:
            try:
                raw = self.ask(prompt)
            except (EOFError, KeyboardInterrupt):
                raise QuitRequested from None
            command = COMMANDS.get(raw.strip().lower())
            if command:
                raise command
            try:
                return parse(raw.strip())
            except ValueError as error:
                self.say(f"  ! {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.filter_judge_cli",
        description="필터 정밀도 판정 CLI — 의미 있는 삭제인가 예/아니오 (#89, §10.1).",
    )
    parser.add_argument("--judge", required=True, choices=LABELERS, help="판정자 이니셜")
    parser.add_argument(
        "--records", type=Path, default=Path("data/filter_precision") / RECORDS_FILENAME
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="기본: <records 폴더>/<judge>_judgments.jsonl",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    out = args.out or args.records.parent / JUDGMENT_FILENAME_TEMPLATE.format(judge=args.judge)
    if not args.records.is_file():
        print(
            f"파일이 없다: {args.records} — 먼저 python -m eval.filter_precision_sample",
            file=sys.stderr,
        )
        return 2
    try:
        records = load_records(args.records)
        judgments = load_judgments(out, args.judge, records)
    except JudgeFileError as error:
        print(error, file=sys.stderr)
        return 2
    JudgeSession(args.judge, records, out, judgments).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
