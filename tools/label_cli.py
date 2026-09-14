"""라벨링 CLI — 레코드 1건을 보여주고 가이드 §7.2 개인 라벨 1줄을 채운다 (이슈 #37). 담당: 성제 (sj)

왜 만드나:
    200건(이후 500건)을 에디터로 JSONL 을 고쳐 가며 라벨하면 오타·대소문자·필드 누락이 난다.
    #36 리뷰에서 오타 4건이 kappa 를 0.091 → 0.268 로 부풀리는 것을 확인했다. 병합 단계에서
    멈추는 것보다 입력하는 순간 8종·3등급 밖의 값과 가이드 §6 규칙 위반을 막는 편이 싸다.

무엇을 보여주고 무엇을 안 보여주나:
    가이드 §2.1 필드만 화이트리스트로 꺼낸다. `reason.*`(분류기·LLM 출력)는 레코드 파일에 들어
    있더라도 읽지 않는다 (§2.2 anchoring). 맥락(커밋·PR·이슈·리뷰)을 코드보다 **먼저** 보여준다 —
    §3 판정 순서가 "명시 → 추론"이고, 대체 코드가 먼저 눈에 들어오면 명시된 이유를 덮어쓴다.

파일 (#33 산출물):
    읽기  datasets/labels/pre200_records.jsonl    라벨러가 보는 레코드
    쓰기  datasets/labels/{labeler}_pre200.jsonl  빈 틀의 줄을 한 줄씩 채운다

    끝에 덧붙이지(append) 않고 **제자리에서** 채운다. 같은 record_id 에 줄이 두 개 생기면 #34
    병합이 그 레코드를 3인 라벨로 보고 쌍별 kappa 에서 조용히 뺀다. 되돌리기도 제자리 수정이다.
    저장은 건마다 임시 파일 → 교체라, 도중에 꺼져도 앞서 채운 줄은 남는다.

실행:
    python -m tools.label_cli --labeler sj
    입력 칸 어디서나:  :q 종료(지금 건은 저장 안 함)  ·  :u 직전 건 수정  ·  :r 지금 건 처음부터
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from classify.labels import EVIDENCE_GRADES, REASON_LABELS, UNKNOWN_CAUSE_TAGS, is_filled
from classify.sampling import (
    EXTRA_CONTEXT_FIELDS,
    GUIDE_VERSION,
    LABEL_FILENAME_TEMPLATE,
    LABELER_CONTEXT_FIELDS,
    LABELER_FIELDS,
    LABELER_REPLACEMENT_FIELDS,
    LABELERS,
    RECORDS_FILENAME,
    build_labeling_record,
)
from eval.gate1 import INFERRED_MIN_CONFIDENCE, INFERRED_STRONG_CONFIDENCE

# 가이드 §7.2 개인 라벨 1줄 (필드 순서 그대로). 이 CLI 가 쓰는 줄은 정확히 이 키만 갖는다.
LABEL_FIELDS: tuple[str, ...] = (
    "record_id",
    "labeler",
    "reason_label",
    "evidence_grade",
    "evidence_text",
    "evidence_source",
    "evidence_locator",
    "confidence",
    "note",
    "labeled_at",
    "guide_version",
)

# CHARTER §4.2 ③ 이유 8종 (화면 표시용 이름)
REASON_NAMES = {
    "BUG": "버그 수정",
    "PERF": "성능",
    "SEC": "보안",
    "LIB": "라이브러리 교체",
    "DEAD": "죽은 코드",
    "DESIGN": "설계 변경",
    "FEAT": "기능 제거",
    "UNK": "불명",
}
# 가이드 §7.2 evidence_source
EVIDENCE_SOURCES = ("commit", "pr", "issue", "review", "diff")
# 가이드 §6.1 "EXPLICIT 에서 diff 는 쓰지 않는다 — diff 는 문장이 아니다"
EXPLICIT_SOURCES = ("commit", "pr", "issue", "review")
# 가이드 §6.1·§7.2 예시 위치. pr·issue·review 는 번호가 레코드 파일에 없어 직접 적는다
DEFAULT_LOCATORS = {"commit": "commit:message", "diff": "diff:replacement"}
# 가이드 §7.2 note 태그
NOTE_TAGS = (
    "needs-discussion",
    "multi-reason",
    "priority-rule",
    "off-record-evidence",
    "filter-miss",
    "anchored",
    *UNKNOWN_CAUSE_TAGS,
)
# 가이드 §6.1 "EXPLICIT 이면 1.0 으로 고정" (가이드 §11-3 [팀 확정 필요])
EXPLICIT_CONFIDENCE = 1.0
# 가이드 §6.3 "UNKNOWN: confidence = 0.0"
UNKNOWN_CONFIDENCE = 0.0
# 가이드 §6.1 "목표 500자 이내" (가이드 §11-4 [팀 확정 필요]). 넘으면 안내만 한다
EVIDENCE_TEXT_SOFT_LIMIT = 500
# 가이드 §8.1 "1건 상한 5분" (가이드 §11-8 [팀 확정 필요]). 넘으면 안내만 한다
RECORD_SOFT_LIMIT_SECONDS = 300

RULE = "-" * 72
COMMAND_HELP = (
    "명령 (입력 칸 어디서나): :q 종료(지금 건 저장 안 함) · :u 직전 건 수정 · :r 지금 건 처음부터"
)
REASON_PROMPT = (
    "이유 ["
    + " ".join(f"{index}={code}" for index, code in enumerate(REASON_LABELS, start=1))
    + "]: "
)
EXPLICIT_GUIDE = (
    "EXPLICIT — 이유가 적힌 원문을 그대로 복사한다. 요약·번역·다듬기 금지 (가이드 §6.1).\n"
    "  여러 곳에 있으면 가장 구체적인 한 곳. 앞뒤를 자르면 … 로 표시. confidence 는 1.0 고정."
)
INFERRED_GUIDE = (
    "INFERRED — 신뢰도는 라벨러의 확신도가 아니라 근거의 강도다 (가이드 §6.2)\n"
    f"  {INFERRED_STRONG_CONFIDENCE} 이상          대체 코드가 이유를 거의 증명한다 "
    "(삭제된 일을 무엇이 이어받았는지 diff 에서 특정된다)\n"
    f"  {INFERRED_MIN_CONFIDENCE} 이상 {INFERRED_STRONG_CONFIDENCE} 미만  정황이 한 방향으로 "
    "일치하지만 다른 설명도 가능하다 (테스트 추가, 호출자 소멸)\n"
    f"  {INFERRED_MIN_CONFIDENCE} 미만          INFERRED 를 쓰지 않는다 → UNK + UNKNOWN"
)

Ask = Callable[[str], str]
Say = Callable[[str], None]


class QuitRequested(Exception):
    """:q — 지금 건은 버리고 끝낸다."""


class UndoRequested(Exception):
    """:u — 직전에 저장한 건을 다시 연다."""


class RestartRequested(Exception):
    """:r — 지금 건을 처음부터 다시 입력한다."""


COMMANDS: dict[str, type[Exception]] = {
    ":q": QuitRequested,
    ":u": UndoRequested,
    ":r": RestartRequested,
}


# --------------------------------------------------------------------------------------
# 입력값 해석. 틀리면 ValueError 의 문장이 그대로 라벨러에게 보인다.
# --------------------------------------------------------------------------------------


def parse_reason(raw: str) -> str:
    if raw.isdigit() and 1 <= int(raw) <= len(REASON_LABELS):
        return REASON_LABELS[int(raw) - 1]
    code = raw.upper()
    if code in REASON_LABELS:
        return code
    raise ValueError(
        f"{raw!r} 은 이유 8종이 아니다 — 1~{len(REASON_LABELS)} 또는 "
        f"{'/'.join(REASON_LABELS)} (CHARTER §4.2 ③)"
    )


def parse_grade(raw: str) -> str:
    code = raw.upper()
    if code in ("E", "EXPLICIT"):
        return "EXPLICIT"
    if code in ("I", "INFERRED"):
        return "INFERRED"
    if code in ("U", "UNKNOWN"):
        raise ValueError("UNKNOWN 등급은 이유 UNK 와만 쓴다 (가이드 §6.3). 이유부터 다시: :r")
    raise ValueError(f"{raw!r} — e(EXPLICIT) 또는 i(INFERRED)")


def parse_confidence(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{raw!r} 은 숫자가 아니다 — 0~1 사이 소수") from None
    if math.isnan(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{raw} — 신뢰도는 0~1 사이다")
    return value


def source_parser(allowed: Sequence[str], default: str | None = None) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if not raw and default:
            return default
        value = raw.lower()
        if value in allowed:
            return value
        if value == "diff":
            raise ValueError(
                "EXPLICIT 에서 diff 는 쓰지 않는다 — diff 는 문장이 아니다 (가이드 §6.1)"
            )
        raise ValueError(f"{raw!r} — {'/'.join(allowed)} 중 하나")

    return parse


def required_text(message: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if not raw:
            raise ValueError(message)
        return raw

    return parse


def found_in_context(evidence: str, view: dict[str, Any]) -> bool:
    """EXPLICIT 인용이 수집된 맥락에 실제로 있나. 공백 차이와 `…` 생략은 허용한다 (§6.1).

    못 찾았다고 막지는 않는다. GitHub 원본에서 가져온 문장일 수 있다 (§2.3 off-record-evidence).
    """
    parts: list[str] = []
    for value in (view.get("context") or {}).values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
    haystack = " ".join("\n".join(parts).split())
    pieces = [" ".join(piece.split()) for piece in re.split(r"…|\.\.\.", evidence)]
    pieces = [piece for piece in pieces if piece]
    return bool(pieces) and all(piece in haystack for piece in pieces)


# --------------------------------------------------------------------------------------
# 레코드 보여주기
# --------------------------------------------------------------------------------------


def labeler_view(row: dict[str, Any]) -> dict[str, Any]:
    """라벨러에게 보여줄 필드만 남긴 사본. `reason` 은 여기서 떨어진다 (가이드 §2.2).

    #33 레코드 파일(`record_id`)이든 §4.4 원본(`id`, 개발용 픽스처)이든 같은 모양으로 만든다.
    이슈 본문·PR 라벨(#24 확정 전)은 #33 이 `--with-extra-context` 로 담았을 때만 보인다.
    """
    if "record_id" not in row:
        return build_labeling_record(row)

    view: dict[str, Any] = {"record_id": str(row.get("record_id") or "").strip()}
    view.update({key: row.get(key) for key in LABELER_FIELDS})
    replacement = row.get("replacement") or {}
    view["replacement"] = {key: replacement.get(key) for key in LABELER_REPLACEMENT_FIELDS}
    context = row.get("context") or {}
    picked = {key: context.get(key) for key in LABELER_CONTEXT_FIELDS}
    picked.update({key: context[key] for key in EXTRA_CONTEXT_FIELDS if key in context})
    view["context"] = picked
    return view


def _block(name: str, value: object) -> list[str]:
    if value is None or value == "" or value == []:
        return [f"{name}: (없음)"]
    lines = [f"{name}:"]
    if isinstance(value, list):
        for item in value:
            first, *rest = str(item).splitlines() or [""]
            lines.append(f"  - {first}")
            lines.extend(f"    {line}" for line in rest)
    else:
        lines.extend(f"  {line}" for line in str(value).splitlines())
    return lines


def render_record(view: dict[str, Any]) -> str:
    context = view.get("context") or {}
    replacement = view.get("replacement") or {}
    test_code = "예 — 가이드 §5 테스트 코드 특례" if view.get("is_test_code") else "아니오"
    lines = [
        RULE,
        f"record_id  {view.get('record_id')}",
        f"repo       {view.get('repo')}",
        f"file       {view.get('file_path')}",
        f"function   {view.get('function_signature') or view.get('function_name')}",
        f"test code  {test_code}",
        f"source     {view.get('source_url')}",
        "",
        "== 맥락 — 먼저 읽는다 (가이드 §3: 명시 → 추론 → UNK) ==",
    ]
    for key in LABELER_CONTEXT_FIELDS:
        lines.extend(_block(key, context.get(key)))
    for key in EXTRA_CONTEXT_FIELDS:
        if key in context:
            lines.extend(_block(key, context.get(key)))
    lines += [
        "",
        "== 삭제된 코드 (deleted_body) ==",
        str(view.get("deleted_body") or "(없음)").rstrip("\n"),
        "",
        f"== 대체 코드 (replacement, match_method={replacement.get('match_method')}) ==",
        str(replacement.get("code") or "(없음)").rstrip("\n"),
        RULE,
    ]
    return "\n".join(lines)


def format_summary(label: dict[str, Any]) -> str:
    reason = label["reason_label"]
    return "\n".join(
        [
            "-- 저장할 라벨 --",
            f"  reason_label     {reason} ({REASON_NAMES[reason]})",
            f"  evidence_grade   {label['evidence_grade']}",
            f"  evidence_text    {label['evidence_text']}",
            f"  evidence_source  {label['evidence_source']}",
            f"  evidence_locator {label['evidence_locator']}",
            f"  confidence       {label['confidence']}",
            f"  note             {label['note']}",
        ]
    )


def _clock_text(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# --------------------------------------------------------------------------------------
# 파일
# --------------------------------------------------------------------------------------


@dataclass
class LabelFile:
    """개인 라벨 파일 (가이드 §7.1). #33 이 만든 빈 틀을 채운다."""

    path: Path
    rows: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> LabelFile:
        text = path.read_text(encoding="utf-8")
        return cls(path, [json.loads(line) for line in text.splitlines() if line.strip()])

    def save(self) -> None:
        """임시 파일에 다 쓴 뒤 교체한다. 쓰는 도중 꺼져도 기존 파일은 온전하다."""
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for row in self.rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(temporary, self.path)

    def next_unlabeled(self) -> int | None:
        """이미 라벨한 record_id 는 건너뛴다 — 중단 후 재개가 이것으로 된다."""
        return next((index for index, row in enumerate(self.rows) if not is_filled(row)), None)

    def labeled_count(self) -> int:
        return sum(1 for row in self.rows if is_filled(row))


def load_records(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """record_id → 라벨러용 레코드. 문제는 사람이 읽을 문장으로."""
    views: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            problems.append(f"{path}:{line_number}: JSON 이 아니다 ({error.msg})")
            continue
        view = labeler_view(row)
        record_id = view["record_id"]
        if not record_id:
            problems.append(f"{path}:{line_number}: record_id 가 없다")
        elif record_id in views:
            problems.append(f"{path}:{line_number}: record_id {record_id} 가 중복된다")
        else:
            views[record_id] = view
    return views, problems


def find_label_file_problems(
    rows: Sequence[dict[str, Any]], labeler: str, record_ids: Collection[str]
) -> list[str]:
    """라벨을 시작하기 전에 파일이 계약(가이드 §7.2)을 지키는지 본다."""
    problems: list[str] = []
    seen: dict[str, int] = {}
    for line_number, row in enumerate(rows, 1):
        record_id = row.get("record_id")
        where = f"{line_number}번째 줄"
        if row.get("labeler") != labeler:
            problems.append(
                f"{where}: labeler={row.get('labeler')!r} — {labeler} 의 라벨 파일이어야 한다"
            )
        if not record_id or record_id not in record_ids:
            problems.append(
                f"{where}: record_id={record_id!r} 가 레코드 파일에 없다 — 엉뚱한 레코드에 붙는다"
            )
        if record_id in seen:
            problems.append(
                f"{where}: record_id={record_id!r} 가 {seen[record_id]}번째 줄과 중복 — "
                "#34 병합이 이 레코드를 kappa 에서 뺀다"
            )
        elif record_id:
            seen[record_id] = line_number
        if is_filled(row) and (
            row.get("reason_label") not in REASON_LABELS
            or row.get("evidence_grade") not in EVIDENCE_GRADES
        ):
            problems.append(
                f"{where}: 채워진 값 {row.get('reason_label')!r}/{row.get('evidence_grade')!r} 가 "
                "8종·3등급 밖이다 — 손으로 고친 흔적. 고치고 다시 실행한다"
            )
    return problems


# --------------------------------------------------------------------------------------
# 세션
# --------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now().astimezone()


class LabelSession:
    """한 사람이 자기 라벨 파일을 채우는 한 번의 실행."""

    def __init__(
        self,
        labeler: str,
        records: dict[str, dict[str, Any]],
        label_file: LabelFile,
        *,
        ask: Ask | None = None,
        say: Say = print,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.labeler = labeler
        self.records = records
        self.file = label_file
        self.ask = ask or input
        self.say = say
        self.clock = clock
        self.now = now
        self.started = clock()
        self.saved = 0
        self.history: list[int] = []

    # ---- 흐름 ----

    def run(self) -> int:
        """이번 실행에서 새로 라벨한 건수를 돌려준다."""
        self.say(COMMAND_HELP)
        target: int | None = None
        while True:
            editing = target is not None
            index = target if target is not None else self.file.next_unlabeled()
            target = None
            if index is None:
                self.say(f"배분된 {len(self.file.rows)}건을 모두 라벨했다.")
                target = self.ask_after_finish()
                if target is None:
                    return self.saved
                continue

            row = self.file.rows[index]
            view = self.records[row["record_id"]]
            self.say("")
            self.say(self.progress_line())
            self.say(render_record(view))
            if editing:
                self.say(
                    f"[직전 건 수정] 저장된 값: {row.get('reason_label')} / "
                    f"{row.get('evidence_grade')} / confidence {row.get('confidence')} / "
                    f"note {row.get('note')!r}"
                )

            try:
                label = self.collect_label(view, started=self.clock())
            except QuitRequested:
                self.say("종료한다. 지금 건은 저장하지 않았다. 다시 실행하면 여기서 이어진다.")
                return self.saved
            except UndoRequested:
                previous = self.previous_index(current=index)
                if previous is None:
                    self.say("수정할 직전 건이 없다.")
                    target = index if editing else None
                else:
                    target = previous
                continue
            except RestartRequested:
                target = index if editing else None
                continue

            self.save(index, label)
            if not editing:
                self.saved += 1

    def ask_after_finish(self) -> int | None:
        """끝내기 전에 한 번 묻는다. 마지막 건을 저장하자마자 끝내면 그 건을 고칠 길이 없다.

        다시 실행해도 남은 건이 없으면 곧바로 이 질문으로 온다 — 다 끝난 파일의 마지막 건도
        `:u` 로 고칠 수 있다.
        """
        try:
            self.ask_until("직전 건을 고치려면 :u, 끝내려면 Enter: ", lambda raw: raw)
        except UndoRequested:
            previous = self.previous_index(current=-1)
            if previous is None:
                self.say("수정할 직전 건이 없다.")
            return previous
        except (QuitRequested, RestartRequested):
            return None
        return None

    def progress_line(self) -> str:
        done = self.file.labeled_count()
        total = len(self.file.rows)
        elapsed = self.clock() - self.started
        parts = [
            f"[{done}/{total}] {self.labeler}",
            f"이번 실행 {self.saved}건",
            f"경과 {_clock_text(elapsed)}",
        ]
        if self.saved:
            average = elapsed / self.saved
            parts.append(f"건당 평균 {_clock_text(average)}")
            parts.append(f"남은 예상 {_clock_text(average * (total - done))}")
        return " · ".join(parts)

    def previous_index(self, current: int) -> int | None:
        """이번 실행에서 저장한 순서를 거슬러 간다. 없으면 파일에서 가장 최근에 라벨한 줄.

        labeled_at 은 같은 사람이 같은 기계에서 쓴 ISO 8601 시각이라 문자열 순서가 시간 순서다.
        """
        while self.history:
            candidate = self.history.pop()
            if candidate != current:
                return candidate
        stamped = [
            (row.get("labeled_at") or "", index)
            for index, row in enumerate(self.file.rows)
            if index != current and is_filled(row)
        ]
        return max(stamped)[1] if stamped else None

    def save(self, index: int, label: dict[str, Any]) -> None:
        row = self.file.rows[index]
        filled = {
            **label,
            "record_id": row["record_id"],
            "labeler": self.labeler,
            "labeled_at": self.now().isoformat(timespec="seconds"),
            "guide_version": GUIDE_VERSION,
        }
        self.file.rows[index] = {key: filled.get(key) for key in LABEL_FIELDS}
        self.file.save()
        if index in self.history:
            self.history.remove(index)
        self.history.append(index)
        self.say(f"저장했다: {filled['reason_label']} / {filled['evidence_grade']}")

    # ---- 한 건 입력 ----

    def collect_label(self, view: dict[str, Any], started: float) -> dict[str, Any]:
        """가이드 §3 순서: 이유 → 근거 등급 → 등급별 근거 → note → 확인."""
        reason = self.ask_until(REASON_PROMPT, parse_reason)
        if reason == "UNK":
            self.say("UNK 는 항상 UNKNOWN 등급과 함께 간다 (가이드 §4 UNK).")
            evidence = self.unknown_evidence()
        else:
            grade = self.ask_until("근거 등급 [e=EXPLICIT / i=INFERRED]: ", parse_grade)
            if grade == "EXPLICIT":
                evidence = self.explicit_evidence(view)
            else:
                evidence = self.inferred_evidence()
        if evidence["evidence_grade"] == "UNKNOWN":
            reason = "UNK"

        note = self.collect_note(evidence["evidence_grade"])
        label = {"reason_label": reason, **evidence, "note": note}
        self.say(format_summary(label))
        if self.clock() - started > RECORD_SOFT_LIMIT_SECONDS and "needs-discussion" not in note:
            self.say(
                "이 건에 5분 넘게 썼다 (가이드 §8.1 상한). 애매하면 :r 로 돌아가 "
                "note 에 needs-discussion 을 달고 넘긴다."
            )
        if not self.confirm("저장할까? [Y/n] (n = 이 건 처음부터): ", default=True):
            raise RestartRequested
        return label

    def explicit_evidence(self, view: dict[str, Any]) -> dict[str, Any]:
        self.say(EXPLICIT_GUIDE)
        while True:
            text = self.ask_until(
                "evidence_text (원문 복사): ",
                required_text(
                    "EXPLICIT 이면 evidence_text 가 필수다 — "
                    "이유가 적힌 원문을 복사한다 (가이드 §6.1)"
                ),
            )
            if len(text) > EVIDENCE_TEXT_SOFT_LIMIT:
                self.say(
                    f"  {len(text)}자 — 목표 {EVIDENCE_TEXT_SOFT_LIMIT}자 이내. "
                    "이유가 담긴 문장 단위로 자르고 … 로 표시한다 (§6.1)"
                )
            if found_in_context(text, view):
                break
            if self.confirm(
                "  수집된 맥락에서 이 문장을 찾지 못했다. 요약·번역했다면 원문을 다시 복사한다.\n"
                "  GitHub 원본에서 가져왔다면 그대로 두고 note 에 off-record-evidence (§2.3).\n"
                "  그대로 둘까? [y/N]: ",
                default=False,
            ):
                break
        source = self.ask_until(
            f"evidence_source [{'/'.join(EXPLICIT_SOURCES)}]: ", source_parser(EXPLICIT_SOURCES)
        )
        return {
            "evidence_grade": "EXPLICIT",
            "evidence_text": text,
            "evidence_source": source,
            "evidence_locator": self.ask_locator(source),
            "confidence": EXPLICIT_CONFIDENCE,
        }

    def inferred_evidence(self) -> dict[str, Any]:
        self.say(INFERRED_GUIDE)
        while True:
            confidence = self.ask_until("confidence (0~1): ", parse_confidence)
            if confidence >= INFERRED_MIN_CONFIDENCE:
                break
            if self.confirm(
                f"신뢰도 {confidence} < {INFERRED_MIN_CONFIDENCE} 은 INFERRED 로 쓰지 않는다 "
                "(가이드 §6.2). UNK + UNKNOWN 으로 바꿀까? [Y/n]: ",
                default=True,
            ):
                self.say("UNK + UNKNOWN 으로 기록한다.")
                return self.unknown_evidence()
            self.say(f"INFERRED 로 두려면 신뢰도가 {INFERRED_MIN_CONFIDENCE} 이상이어야 한다.")

        text = self.ask_until(
            "evidence_text (추론 근거 한 문장 — 무엇을 보고 판단했나): ",
            required_text(
                "INFERRED 면 evidence_text 가 필수다 — "
                "추론 근거를 직접 한 문장으로 쓴다 (가이드 §6.2)"
            ),
        )
        source = self.ask_until(
            f"evidence_source [Enter=diff / {'/'.join(EVIDENCE_SOURCES)}]: ",
            source_parser(EVIDENCE_SOURCES, default="diff"),
        )
        return {
            "evidence_grade": "INFERRED",
            "evidence_text": text,
            "evidence_source": source,
            "evidence_locator": self.ask_locator(source),
            "confidence": confidence,
        }

    def unknown_evidence(self) -> dict[str, Any]:
        """가이드 §6.3: evidence_text = null, confidence = 0.0."""
        return {
            "evidence_grade": "UNKNOWN",
            "evidence_text": None,
            "evidence_source": None,
            "evidence_locator": None,
            "confidence": UNKNOWN_CONFIDENCE,
        }

    def ask_locator(self, source: str) -> str | None:
        default = DEFAULT_LOCATORS.get(source)
        hint = (
            f"Enter={default}"
            if default
            else "예: pr:#1043#body · issue:#233#title · review:comment_1234567 · 비우면 null"
        )
        return self.ask_until(f"evidence_locator [{hint}]: ", lambda raw: raw or default)

    def collect_note(self, grade: str) -> str:
        if grade == "UNKNOWN":
            self.say(
                "UNKNOWN — 무엇이 없어서 판단하지 못했는지 한 줄 필수 (가이드 §6.3). 원인 태그: "
                + " ".join(UNKNOWN_CAUSE_TAGS)
            )
            note = self.ask_until(
                "note: ",
                required_text("UNKNOWN 이면 note 가 필수다 — 예: no-context vague-message"),
            )
            if not any(tag in note for tag in UNKNOWN_CAUSE_TAGS):
                self.say("  원인 태그가 없다. 집계(§6.3)에서 '(태그 없음)'으로 잡힌다.")
            return note
        self.say("note 태그: " + " ".join(NOTE_TAGS))
        return self.ask_until("note (없으면 Enter): ", lambda raw: raw)

    # ---- 입력 한 칸 ----

    def confirm(self, prompt: str, default: bool) -> bool:
        def parse(raw: str) -> bool:
            if not raw:
                return default
            if raw.lower() in ("y", "yes", "예", "ㅇ"):
                return True
            if raw.lower() in ("n", "no", "아니오", "ㄴ"):
                return False
            raise ValueError("y 또는 n")

        return self.ask_until(prompt, parse)

    def ask_until[T](self, prompt: str, parse: Callable[[str], T]) -> T:
        """맞는 값이 들어올 때까지 다시 묻는다. 틀린 값은 저장되지 않는다."""
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


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.label_cli",
        description="라벨링 CLI — 한 건씩 보여주고 가이드 §7.2 개인 라벨을 채운다 (#37).",
    )
    parser.add_argument("--labeler", required=True, choices=LABELERS)
    parser.add_argument("--labels-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument(
        "--records", type=Path, default=None, help=f"기본: <labels-dir>/{RECORDS_FILENAME}"
    )
    parser.add_argument(
        "--labels-file",
        type=Path,
        default=None,
        help="기본: <labels-dir>/<labeler>_pre200.jsonl (#33 이 만든 빈 틀)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # stdin 도 맞춘다. 파이프로 넣은 한국어 근거 문장이 Windows 기본 코드페이지로 깨지지 않게.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")

    records_path = args.records or args.labels_dir / RECORDS_FILENAME
    label_path = args.labels_file or args.labels_dir / LABEL_FILENAME_TEMPLATE.format(
        labeler=args.labeler
    )
    for path in (records_path, label_path):
        if not path.is_file():
            print(
                f"파일이 없다: {path} — 먼저 python -m classify.sampling 으로 만든다 (#33)",
                file=sys.stderr,
            )
            return 2

    records, problems = load_records(records_path)
    try:
        label_file = LabelFile.load(label_path)
    except json.JSONDecodeError as error:
        print(f"{label_path}: JSON 이 아니다 ({error.msg}, 줄 {error.lineno})", file=sys.stderr)
        return 2
    problems += find_label_file_problems(label_file.rows, args.labeler, records.keys())
    if problems:
        print("라벨을 시작하기 전에 고칠 것:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    LabelSession(args.labeler, records, label_file).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
