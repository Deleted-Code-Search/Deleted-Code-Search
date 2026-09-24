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
from typing import Any

from classify.baseline_keyword import classify_message
from classify.baselines import UNKNOWN_LABEL, commit_message_of
from pipeline.context import CLOSING_WORDS, CODE_FENCE_RE, INLINE_CODE_RE

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
    """삭제된 함수를 이름으로 가리키나 - 가이드 §6.1.1 E2-(가). 파일 이름은 보지 않고
    (`target_names`), 평범한 단어 모양 이름은 코드 표시가 있어야 한다 (`mentions`)."""


def split_sentences(text: str | None) -> list[str]:
    """맥락 텍스트를 문장으로 쪼갠다. 코드 펜스(```` ``` ````)와 HTML 주석은 먼저 지운다.

    코드 펜스는 재현 코드라 문장이 아니고, 그 안의 단어가 이유 키워드로 잡힌다. HTML 주석은
    PR 템플릿 안내문이라 작성자 글이 아니다. 둘 다 보통 제 줄에 따로 있어 지워도 문장이
    이어 붙지 않는다.

    **인라인 코드(`` `x` ``)는 지우지 않는다.** 문장이 곧 인용문이라 지우면 원문이 깨진다 -
    예비 200건에서 174건 중 63건의 인용문이 원문에 없었다 (``Support `Field(repr=False)` in``
    이 ``Support   in`` 이 됐다). 게다가 함수 이름은 보통 백틱 안에 쓴다. 지우면 "이 함수를
    가리킨다" 를 못 본다. 인라인 코드는 라벨을 고를 때만 뺀다 (`find_reason_sentences`).
    """
    if not text:
        return []
    cleaned = CODE_FENCE_RE.sub(" ", HTML_COMMENT_RE.sub(" ", text))
    sentences: list[str] = []
    for piece in SENTENCE_BOUNDARY_RE.split(cleaned):
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
        """텍스트를 문장으로 쪼개 같은 출처·위치를 붙여 쌓는다."""
        found.extend(Passage(sentence, source, locator) for sentence in split_sentences(text))

    add(commit_message_of(record), "commit", "commit:message")

    pr_number = context.get("pr_number")
    if pr_number is not None:
        add(context.get("pr_title"), "pr", f"pr:#{pr_number}#title")
        add(context.get("pr_body"), "pr", f"pr:#{pr_number}#body")

    numbers = context.get("issue_numbers") or []
    titles = context.get("issue_titles") or []
    bodies = context.get("issue_bodies") or []
    # 길이가 번호 목록과 같을 때만 짝을 짓는다. 앞에서부터 짝을 지으면 하나가 빠진 목록에서
    # 뒤쪽 제목이 모두 앞 번호에 붙는다.
    if len(titles) == len(numbers):
        for number, title in zip(numbers, titles, strict=True):
            add(title, "issue", f"issue:#{number}#title")
    if len(bodies) == len(numbers):
        for number, body in zip(numbers, bodies, strict=True):
            add(body, "issue", f"issue:#{number}#body")

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
    """이 레코드의 삭제 대상을 가리키는 이름 - 함수 이름 하나.

    가이드 §6.1.1 E2-(가)는 "**삭제된** 함수·클래스·파일을 이름이나 지시로" 가리키는 것이다.
    지시어("this helper")는 규칙으로 못 잡고, 이름만 본다.

    **파일 이름은 쓰지 않는다.** 함수가 지워졌을 뿐 파일은 남아 있어 "삭제된 파일" 이 아니고,
    `dataclasses`·`fields`·`generics` 같은 모듈 이름은 흔한 단어라 아무 문장에나 걸린다.
    처음에 넣었더니 예비 200건의 EXPLICIT 10건 중 9건이 파일 이름으로만 나왔고, 내용도 틀렸다 -
    `"fix dataclasses and docs"` 가 `dataclass` 함수의 EXPLICIT 1.0 근거가 됐다. EXPLICIT 은 가장
    강한 주장이라 잘못 주는 것이 놓치는 것보다 나쁘다.

    **던더 이름(`__init__` 등)도 쓰지 않는다.** 클래스마다 있어서 이름만으로는 어느 함수인지
    가리키지 못한다.
    """
    function_name = (record.get("function_name") or "").strip()
    if function_name.startswith("__") and function_name.endswith("__"):
        return ()
    if len(function_name.strip("_")) < MIN_TARGET_NAME_CHARS:
        return ()
    return (function_name,)


def looks_like_identifier(name: str) -> bool:
    """코드 식별자 모양인가 - 밑줄, 첫 글자 뒤의 대문자, 숫자 중 하나가 있다.

    `legacy_backoff`·`parseAll`·`v2_schema` 는 식별자 모양이라 문장에 나오면 그 함수다.
    `update`·`host`·`validate` 는 평범한 영어 단어와 모양이 같아 구별이 안 된다.
    """
    core = name.strip("_")
    return "_" in core or any(ch.isupper() for ch in core[1:]) or any(ch.isdigit() for ch in core)


def mentions(sentence: str, names: tuple[str, ...]) -> bool:
    """문장이 삭제된 함수를 이름으로 가리키나.

    - **단어로** 찾는다. `parse` 가 `parser` 에 걸리지 않게 한다
    - **대소문자를 가린다.** 식별자는 대소문자를 가린다
    - **평범한 단어 모양 이름은 코드 표시가 있을 때만** 인정한다 - 백틱 안이거나(`` `update` ``)
      괄호가 붙을 때(`update()`). 그렇지 않으면 `update` 함수에 "Update docs to fix typo." 가
      EXPLICIT 1.0 근거가 된다. 파일 이름을 대상에서 뺀 것(`target_names`)과 같은 이유다 -
      흔한 단어는 아무 문장에나 걸리고, EXPLICIT 은 잘못 주는 것이 놓치는 것보다 나쁘다.
      예비 200건에서 `host` 함수가 "Fix host required enforcement ..." 로 EXPLICIT 이 됐다
    """
    for name in names:
        word = rf"(?<![\w]){re.escape(name)}(?![\w])"
        if looks_like_identifier(name):
            if re.search(word, sentence):
                return True
            continue
        in_code = any(re.search(word, span) for span in INLINE_CODE_RE.findall(sentence))
        called = re.search(rf"(?<![\w]){re.escape(name)}\(", sentence)
        if in_code or called:
            return True
    return False


def find_reason_sentences(record: dict[str, Any]) -> list[ReasonSentence]:
    """맥락에서 이유를 말하는 문장들. 문장마다 기준선 A 규칙으로 라벨을 붙인다.

    한 문장에 여러 라벨 단서가 있으면 기준선 A 와 같은 우선순위(가이드 §11-1)로 하나를 고른다.
    같은 커밋 메시지를 기준선 A 가 통째로 읽을 때와 문장 단위로 읽을 때 답이 다를 수 있다 -
    그 차이는 의도한 것이다. 이유가 어느 문장에 있는지가 근거 등급을 정한다.

    이슈 닫기 참조와 인라인 코드는 라벨을 고를 때만 뺀다. 인라인 코드 안의 단어(`` `fix_x` ``
    같은 식별자)가 키워드로 잡히면 안 되기 때문이다. 인용문과 이름 대조는 원문 그대로 한다 -
    EXPLICIT 근거는 원문 인용이어야 하고 (가이드 §6.1), 함수 이름은 보통 백틱 안에 있다.
    """
    names = target_names(record)
    found: list[ReasonSentence] = []
    for passage in passages(record):
        for_label = INLINE_CODE_RE.sub(" ", CLOSING_REFERENCE_RE.sub(" ", passage.text))
        label, keywords = classify_message(for_label)
        if label == UNKNOWN_LABEL:
            continue
        found.append(ReasonSentence(passage, label, keywords, mentions(passage.text, names)))
    return found
