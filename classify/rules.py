"""이유 분류의 규칙 부분 - 맥락 문장에서 이유를 찾고 그 위치를 붙인다 (#84). 담당: 희수

무엇을:
    레코드의 맥락(커밋 메시지·PR·이슈·리뷰 코멘트)을 문장으로 쪼개고, 각 문장에 출처와
    위치(가이드 §7.2 `evidence_locator`)를 붙인다. 그중 이유를 말하는 문장을 골라 어느 라벨을
    가리키는지, 삭제된 함수를 직접 가리키는지를 표시한다. 분류기(`classify.classifier`)가
    이것으로 근거 등급을 정한다.

기준선 A 와 무엇이 다른가:
    라벨 단서는 기준선 A 의 키워드 규칙(`baseline_keyword.KEYWORD_RULES`)을 **그대로** 쓴다.
    새 키워드 목록을 만들면 "규칙을 더 잘 짜서 이겼다" 가 되어 기여가 흐려진다. 다른 것은 두
    가지다 - 커밋 메시지 한 줄이 아니라 **맥락 전체**를 읽고, 문장마다 **어디서 왔는지**를
    남긴다. 이유를 인용할 수 있어야 EXPLICIT 을 말할 수 있다 (가이드 §6.1).

이 파일이 하지 않는 것:
    - 라벨을 **결정**하지 않는다. 문장별 후보만 낸다. 여러 문장·모델·LLM 을 합치는 것은
      `classify.classifier` 다
    - 문장이 이유를 "말하는지"(가이드 §6.1.1 E1)를 키워드 매칭으로만 본다. 키워드 없이 이유를
      말하는 문장은 놓친다. 규칙 정밀도·재현율은 500건 val 로 잰다 (#86)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from classify.baseline_keyword import classify_message
from classify.baselines import UNKNOWN_LABEL, commit_message_of
from pipeline.context import CLOSING_WORDS, strip_code

# 규칙을 바꾸면 올린다. 분류기 버전 문자열에 들어간다.
RULES_VERSION = "r1"

# PR 템플릿의 안내문(`<!-- ... -->`). 작성자가 쓴 글이 아니다. pydantic 템플릿에는
# `please use "fix #123" style references` 가 들어 있어, 지우지 않으면 PR 마다 BUG 가 잡힌다
# (예비 200건에서 13건).
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# 이슈 닫기 참조(`Fixes #12`, `closes GH-3`, 이슈 URL). "이 이슈를 닫는다" 는 **연결**이지 이유
# 서술이 아니다 - 이유는 그 이슈에 있고, 이슈 제목·본문은 따로 읽는다. `fix` 가 들어 있어 그대로
# 두면 이슈가 기능 요청이어도 BUG 가 된다. 라벨을 고를 때만 빼고, 인용문은 원문을 둔다.
CLOSING_REFERENCE_RE = re.compile(
    rf"\b{CLOSING_WORDS}\s*:?\s*"
    r"(?:#\d+|GH-\d+|[\w.-]+/[\w.-]+#\d+|https?://github\.com/\S+)",
    re.IGNORECASE,
)

# 문장 경계. 마침표·물음표·느낌표 뒤 공백, 또는 줄바꿈. PR 본문은 목록·제목이 줄 단위라
# 줄바꿈을 경계로 보는 편이 맞다.
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# 마크다운 목록·제목·인용 기호. 문장 앞에 붙은 것만 벗긴다.
LEADING_MARKUP = " \t-*#>"
# 이보다 짧은 조각은 문장으로 보지 않는다 (`- [x]` 같은 체크박스 잔해).
MIN_SENTENCE_CHARS = 8

# 이름으로 삭제 대상을 가리키는지 볼 때, 너무 짧은 이름은 쓰지 않는다. `get`·`run` 같은 이름은
# 아무 문장에나 걸려 "이 함수를 가리킨다" 가 거짓이 된다.
MIN_TARGET_NAME_CHARS = 4


@dataclass(frozen=True)
class Passage:
    """맥락 안의 문장 하나와 그 위치."""

    text: str
    source: str
    """가이드 §7.2 `evidence_source`: `commit` | `pr` | `issue` | `review`."""
    locator: str
    """가이드 §7.2 `evidence_locator`: `commit:message`, `pr:#12#body`, `review:comment_34` 등."""


@dataclass(frozen=True)
class ReasonSentence:
    """이유를 말하는 것으로 보이는 문장 하나."""

    passage: Passage
    label: str
    keywords: tuple[str, ...]
    names_target: bool
    """삭제된 함수·파일을 이름으로 가리키나 - 가이드 §6.1.1 E2-(가)."""


def split_sentences(text: str | None) -> list[str]:
    """맥락 텍스트를 문장으로 쪼갠다. 코드 블록은 먼저 지운다.

    코드 블록을 지우는 이유는 `pipeline.context.strip_code` 와 같다 - 코드 예시 안의 단어가
    이유 키워드로 잡힌다. PR 본문에 재현 코드를 붙이는 일이 흔하다. HTML 주석(PR 템플릿
    안내문)도 같은 이유로 먼저 지운다.
    """
    if not text:
        return []
    sentences: list[str] = []
    for piece in SENTENCE_BOUNDARY_RE.split(strip_code(HTML_COMMENT_RE.sub(" ", text))):
        sentence = piece.strip().lstrip(LEADING_MARKUP).strip()
        if len(sentence) >= MIN_SENTENCE_CHARS:
            sentences.append(sentence)
    return sentences


def passages(record: dict[str, Any]) -> list[Passage]:
    """레코드 맥락을 출처·위치가 붙은 문장 목록으로. 순서는 커밋 → PR → 이슈 → 리뷰.

    이슈 번호·제목·본문은 같은 순서로 쌓인다 - `pipeline.context` 가 조회에 성공한 이슈만
    셋에 함께 넣는다. 그래도 길이가 다르면(손으로 만든 레코드 등) 짝이 확실한 것만 쓴다.
    위치가 틀린 로케이터는 로케이터가 없는 것보다 나쁘다 - 다른 이슈를 가리키게 된다.
    """
    context = record.get("context") or {}
    found: list[Passage] = []

    def add(text: str | None, source: str, locator: str) -> None:
        found.extend(Passage(sentence, source, locator) for sentence in split_sentences(text))

    add(commit_message_of(record), "commit", "commit:message")

    pr_number = context.get("pr_number")
    if pr_number is not None:
        add(context.get("pr_title"), "pr", f"pr:#{pr_number}#title")
        add(context.get("pr_body"), "pr", f"pr:#{pr_number}#body")

    numbers = context.get("issue_numbers") or []
    titles = context.get("issue_titles") or []
    bodies = context.get("issue_bodies") or []
    for index, number in enumerate(numbers):
        if index < len(titles):
            add(titles[index], "issue", f"issue:#{number}#title")
        if index < len(bodies):
            add(bodies[index], "issue", f"issue:#{number}#body")

    for comment in context.get("review_comments") or []:
        if isinstance(comment, dict):
            comment_id = comment.get("comment_id")
            locator = f"review:comment_{comment_id}" if comment_id is not None else "review:unknown"
            add(comment.get("body"), "review", locator)
        else:
            # ADR-018 전 형식 - 본문 문자열만 있고 식별자가 없다 (가이드 §7.2 `review:unknown`).
            add(str(comment), "review", "review:unknown")
    return found


def target_names(record: dict[str, Any]) -> tuple[str, ...]:
    """이 레코드의 삭제 대상을 가리키는 이름들 - 함수 이름과 파일 이름.

    가이드 §6.1.1 E2-(가)는 "삭제된 함수·클래스·파일을 이름이나 지시로" 가리키는 것이다.
    지시어("this helper")는 규칙으로 못 잡고, 이름만 본다.
    """
    names = []
    function_name = (record.get("function_name") or "").strip()
    if len(function_name.strip("_")) >= MIN_TARGET_NAME_CHARS:
        names.append(function_name)
    stem = Path(record.get("file_path") or "").stem
    if len(stem) >= MIN_TARGET_NAME_CHARS and stem != "__init__":
        names.append(stem)
    return tuple(names)


def mentions(sentence: str, names: tuple[str, ...]) -> bool:
    """문장이 이름 중 하나를 **단어로** 포함하나. `parse` 가 `parser` 에 걸리지 않게 한다."""
    return any(
        re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", sentence, re.IGNORECASE) for name in names
    )


def find_reason_sentences(record: dict[str, Any]) -> list[ReasonSentence]:
    """맥락에서 이유를 말하는 문장들. 문장마다 기준선 A 규칙으로 라벨을 붙인다.

    한 문장에 여러 라벨 단서가 있으면 기준선 A 와 같은 우선순위(가이드 §11-1)로 하나를 고른다.
    같은 커밋 메시지를 기준선 A 가 통째로 읽을 때와 문장 단위로 읽을 때 답이 다를 수 있다 -
    그 차이는 의도한 것이다. 이유가 어느 문장에 있는지가 근거 등급을 정한다.

    이슈 닫기 참조는 라벨을 고를 때만 뺀다 (`CLOSING_REFERENCE_RE`). 인용문은 원문 그대로
    둔다 - EXPLICIT 근거는 원문 인용이어야 한다 (가이드 §6.1).
    """
    names = target_names(record)
    found: list[ReasonSentence] = []
    for passage in passages(record):
        label, keywords = classify_message(CLOSING_REFERENCE_RE.sub(" ", passage.text))
        if label == UNKNOWN_LABEL:
            continue
        found.append(ReasonSentence(passage, label, keywords, mentions(passage.text, names)))
    return found
