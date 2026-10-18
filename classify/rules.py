"""이유 분류의 규칙 부분 - 맥락 문장에서 이유를 찾고 그 위치를 붙인다 (#84). 담당: 희수

무엇을:
    레코드의 맥락(커밋 메시지·PR·이슈·리뷰 코멘트)을 문장으로 쪼개고, 각 문장에 출처와
    위치(가이드 §7.2 `evidence_locator`)를 붙인다. 그중 이유를 말하는 문장을 골라 어느 라벨을
    가리키는지 표시하고, EXPLICIT 인용문이 될 문장(`explicit_passage`)을 찾는다. 분류기
    (`classify.classifier`)가 이것으로 근거 등급을 정한다.

기준선 A 와 무엇이 다른가:
    라벨 단서는 기준선 A 의 키워드 규칙(`baseline_keyword.KEYWORD_RULES`)을 **그대로** 쓴다.
    새 키워드 목록을 만들면 "규칙을 더 잘 짜서 이겼다" 가 되어 기여가 흐려진다. 다른 것은 두
    가지다 - 커밋 메시지 한 줄이 아니라 **맥락 전체**를 읽고, 문장마다 **어디서 왔는지**를
    남긴다. 이유를 인용할 수 있어야 EXPLICIT 을 말할 수 있다 (가이드 §6.1).

이 파일이 하지 않는 것:
    - 라벨을 **결정**하지 않는다. 문장별 후보만 낸다. 여러 문장·모델·LLM 을 합치는 것은
      `classify.classifier` 다
    - 문장이 이유를 "말하는지"(가이드 §6.1.1 E1)를 키워드 매칭으로만 본다. 키워드 없이 이유를
      말하는 문장은 놓친다. E3("커밋 전체 목표만 말하는가")는 보지 않는다
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from classify.baseline_keyword import KEYWORD_RULES, classify_message
from classify.baselines import UNKNOWN_LABEL, commit_message_of
from pipeline.context import CLOSING_WORDS, CODE_FENCE_RE, INLINE_CODE_RE

# 규칙을 바꾸면 올린다. 분류기 버전 문자열에 들어간다. r2 (#178): EXPLICIT 을 가이드 v3 의
# E1·E2 로 (`explicit_passage`). 이유 라벨은 r1 과 같다 - 규칙은 라벨을 고르지 않는다.
RULES_VERSION = "r2"

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
    found: list[ReasonSentence] = []
    for passage in passages(record):
        label, keywords = classify_message(_for_keywords(passage.text))
        if label == UNKNOWN_LABEL:
            continue
        found.append(ReasonSentence(passage, label, keywords))
    return found


def _for_keywords(text: str) -> str:
    """키워드를 찾을 때만 쓰는 문장 - 이슈 닫기 참조와 인라인 코드를 뺀다."""
    return INLINE_CODE_RE.sub(" ", CLOSING_REFERENCE_RE.sub(" ", text))


# --------------------------------------------------------------------------------------
# EXPLICIT 인용문 (r2, #178) - 가이드 v3 §6.1.1 E1·E2, §6.4.5
# --------------------------------------------------------------------------------------

# 경로·시그니처 이름에서 빼는 흔한 낱말. 거의 모든 저장소에 있는 디렉터리·파일 이름이라 문장에
# 나와도 이 레코드를 가리키지 않는다 ("Fix tests", "core cleanup").
GENERIC_PATH_WORDS = frozenset(
    {
        "src", "lib", "libs", "test", "tests", "core", "utils", "util", "common", "base",
        "internal", "_internal", "main", "init", "__init__", "conftest", "helpers", "helper",
        "models", "model", "unit", "unit_tests", "integration", "integration_tests", "examples",
        "example", "docs", "scripts", "tools", "app", "api", "backend", "frontend",
        "components", "component",
    }
)  # fmt: skip
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 수단·결과만 말하는 낱말. 이것만 있으면 이유 서술이 아니다 (가이드 §6.1.1 E1 "`fix: cleanup`,
# `refactor` 처럼 키워드만 있고 무엇이 왜 잘못됐는지가 없으면 명시가 아니다", §3.2 수단 vs 이유).
# 기준선 A 의 DESIGN 단서 전부(refactor·cleanup·simplify ...)와 BUG 의 `fix` 다.
FIX_PATTERN = r"\bfix(?:e[sd])?\b"
MEANS_ONLY_PATTERNS = frozenset(
    pattern for label, patterns in KEYWORD_RULES if label == "DESIGN" for pattern in patterns
) | {FIX_PATTERN}
REASON_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for _label, patterns in KEYWORD_RULES
    for pattern in patterns
    if pattern not in MEANS_ONLY_PATTERNS
)


def record_names(record: dict[str, Any]) -> tuple[str, ...]:
    """가이드 v3 E2(§6.4.5)의 이름 중 함수 이름 밖의 것 - 파일 이름, 디렉터리·모듈 낱말, 시그니처의
    식별자. 함수 이름은 `target_names`·`mentions` 가 따로 본다.

    짧은 이름(`MIN_TARGET_NAME_CHARS` 미만)과 흔한 경로 낱말(`GENERIC_PATH_WORDS`)은 뺀다.
    시그니처는 식별자 모양(`looks_like_identifier`)인 것만 - `self`·`value` 같은 매개변수 이름은
    아무 문장에나 걸린다. v1 은 파일 이름을 아예 뺐는데(`target_names`), v3 가 E2 에 넣었다.
    """
    path = PurePosixPath(record.get("file_path") or "")
    function = record.get("function_name") or ""
    signature = record.get("function_signature") or ""
    words = [path.stem, *path.parts[:-1]]
    words += [name for name in IDENTIFIER_RE.findall(signature) if looks_like_identifier(name)]
    return tuple(
        dict.fromkeys(
            word
            for word in words
            if word != function
            and len(word.strip("_")) >= MIN_TARGET_NAME_CHARS
            and word.lower() not in GENERIC_PATH_WORDS
        )
    )


def names_record(sentence: str, record: dict[str, Any]) -> bool:
    """E2 - 문장이 이 레코드를 이름으로 가리키나 (가이드 v3 §6.4.5).

    함수 이름은 `mentions` 그대로(평범한 낱말 모양이면 코드 표시가 있어야 한다). 경로·시그니처
    이름(`record_names`)은 대소문자를 가리지 않는 낱말 일치다 - "Remove the MELCloud integration"
    처럼 모듈 이름은 보통 백틱 없이 쓴다.
    """
    if mentions(sentence, target_names(record)):
        return True
    return any(
        re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", sentence, re.IGNORECASE)
        for name in record_names(record)
    )


def states_reason(sentence: str) -> bool:
    """E1 의 근사 - 수단·결과 낱말(`MEANS_ONLY_PATTERNS`)이 아닌 이유 단서가 있나.

    어느 이유의 단서인지는 보지 않는다. 학습 300건의 정답 EXPLICIT 인용문 65건 중 22건은 단서
    키워드의 이유가 정답 이유와 달랐다 (키워드 라벨은 잡음이 크다, `classify.classifier._scores`).
    """
    cleaned = _for_keywords(sentence)
    return any(pattern.search(cleaned) for pattern in REASON_PATTERNS)


def explicit_passage(record: dict[str, Any]) -> Passage | None:
    """EXPLICIT 인용문 - E1(`states_reason`)과 E2(`names_record`)를 넘는 첫 문장 (r2, #178).

    순서는 `passages` 그대로(커밋 → PR → 이슈 → 리뷰). E3 는 보지 않는다 - 커밋 전체 목표를
    말하면서 모듈 이름이 나오는 문장도 걸린다. 학습 300건 교차 검증 예측 라벨로 EXPLICIT
    precision 0.50 / recall 0.34 였고, 두 라벨러가 EXPLICIT 에서 서로 맞는 비율도 0.50 이다
    (`docs/reports/classifier_grade_v3.md`).
    """
    return next(
        (p for p in passages(record) if names_record(p.text, record) and states_reason(p.text)),
        None,
    )
