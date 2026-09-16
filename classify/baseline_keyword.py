"""기준선 A: 커밋 메시지 키워드 규칙 (§10.2). 담당: 희수 (hs)

무엇을:
    커밋 메시지 **하나만** 보고 §4.2 ③ 8종 중 하나를 고른다. 우리 방식이 이겨야 하는 하한선이고,
    게이트 2(§9 6주차)의 "기준선 대비 우위"가 이 숫자와의 비교다.

왜 일부러 단순하게 두나:
    기준선은 "우리가 이기려고 만든 허수아비"가 되어도 안 되고, 정교하게 만들어 우리 방식과
    구분이 안 돼서도 안 된다. 그래서 규칙을 문서에 있는 단서(§4.2 ③ "주요 단서" 열)에서만
    출발시키고, 메시지 밖 신호(이슈 참조·테스트 추가·대체 코드)는 쓰지 않는다. 그 신호들을
    쓰는 것이 우리 방식(§4.2 ③ "맥락 결합 + 규칙 + 분류기")이고, 차이가 곧 기여다.

    라벨 가이드 §9-2 도 같은 말을 한다. 라벨러가 커밋 메시지 키워드만 보고 라벨하면 "우리 라벨이
    기준선 A 와 같아져" 비교가 무의미해진다고. 이 파일은 그 기준선 A 자체다.

실행:
    python -m classify.baseline_keyword --input records.jsonl --out predictions.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from classify.baselines import (
    METHOD_KEYWORD,
    UNKNOWN_LABEL,
    Prediction,
    commit_message_of,
    record_id_of,
    summarize,
    write_predictions,
)

# 규칙을 바꾸면 올린다. 예측 파일에 함께 적히므로 나중에 "어느 규칙으로 낸 숫자인지" 알 수 있다.
KEYWORD_RULES_VERSION = "a1"

# 순서가 곧 우선순위다. 여러 라벨이 매칭되면 **먼저 나온 것**을 쓴다.
#
# 이 순서는 라벨 가이드 §11-1 이 v1 으로 제안한 우선순위(SEC > LIB > DEAD > FEAT > BUG > PERF >
# DESIGN)를 그대로 따랐다. 새 순서를 만들면 팀이 정할 것이 하나 더 늘고, 사람 라벨과 기준선이
# 다른 규칙으로 판정하게 된다. 가이드에서 `[팀 확정 필요]` 가 확정되면 여기도 같이 고친다.
#
# 구체적인 단서가 앞에 온다는 뜻이기도 하다. "security" 는 그 자체로 이유를 말하지만 "fix" 는
# 거의 모든 커밋에 붙는다. 흔한 단어가 앞에 있으면 그것만 뽑힌다.
KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "SEC",  # §4.2 ③ 단서: "CVE", "security", "injection"
        (
            r"\bcve\b",
            r"\bcve-\d",
            r"\bsecurit",
            r"\bvulnerab",
            r"\binjection\b",
            r"\bxss\b",
            r"\bcsrf\b",
            r"\bsaniti[sz]",
            r"\bexploit",
            r"\bmalicious\b",
        ),
    ),
    (
        "LIB",  # §4.2 ③ 단서: 직접 구현을 외부·표준 라이브러리로 대체
        (
            # "use urllib3 Retry instead" 처럼 사이에 단어가 여럿 낀다. 같은 줄로만 제한한다 -
            # 줄을 넘기면 본문 저 아래의 "instead" 와 붙어 엉뚱하게 매칭된다.
            r"\buse\b[^\n]{0,60}\binstead\b",
            r"\bswitch(?:ed|ing)?\s+to\b",
            r"\bmigrat\w*\s+to\b",
            r"\breplace\w*\s+(?:\w+\s+)?with\b",
            r"\bstd(?:lib|.library)\b",
            r"\bbuilt-?in\b",
            r"\bin\s+favou?r\s+of\b",
            r"\bvendor\w*\b",
        ),
    ),
    (
        "DEAD",  # §4.2 ③ 단서: 호출자 없음, "unused", "dead"
        (
            r"\bunused\b",
            r"\bdead\s+code\b",
            r"\bno\s+longer\s+used\b",
            r"\bnot\s+used\b",
            r"\bnever\s+used\b",
            r"\bunreachable\b",
            r"\bobsolete\b",
            r"\bleftover\b",
        ),
    ),
    (
        "FEAT",  # §4.2 ③ 단서: "deprecate", "remove support"
        (
            r"\bdeprecat",
            r"\b(?:remove|drop|end)\w*\s+support\b",
            r"\bno\s+longer\s+support",
            r"\bunsupported\b",
            r"\bsunset\b",
            r"\bretire[sd]?\b",
        ),
    ),
    (
        "BUG",  # §4.2 ③ 단서: "fix"
        (
            r"\bfix(?:e[sd])?\b",
            r"\bbug(?:fix)?\b",
            r"\bcrash",
            r"\bregression\b",
            r"\bbroken\b",
            r"\bincorrect\w*\b",
            r"\bwrong\b",
            r"\btraceback\b",
            r"\bexception\b",
        ),
    ),
    (
        "PERF",  # §4.2 ③ 단서: "perf", "slow", 벤치마크
        (
            r"\bperf(?:ormance)?\b",
            r"\bslow(?:er|ness)?\b",
            r"\bspeed\s*-?\s*up\b",
            r"\bfaster\b",
            r"\boptimi[sz]",
            r"\bbenchmark",
            r"\blatency\b",
            r"\bmemory\s+leak\b",
            r"\boverhead\b",
        ),
    ),
    (
        "DESIGN",  # §4.2 ③ 단서: "refactor", 구조·추상화 변경
        (
            r"\brefactor",
            r"\brestructur",
            r"\breorgani[sz]",
            r"\bsimplif",
            r"\bclean(?:\s*-?\s*up|up)?\b",
            r"\bconsolidat",
            r"\brework\w*\b",
            r"\bextract\w*\s+(?:in)?to\b",
            r"\btidy\b",
        ),
    ),
)

_COMPILED: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (label, tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns))
    for label, patterns in KEYWORD_RULES
)


def classify_message(message: str) -> tuple[str, tuple[str, ...]]:
    """커밋 메시지 → (라벨, 매칭된 문자열들).

    여러 라벨이 매칭되면 `KEYWORD_RULES` 순서에서 **앞선 라벨**을 쓴다. 매칭이 하나도 없으면
    `UNK` 다 - 억지로 고르면 §4.2 ③ "불명" 의 뜻이 사라지고, 게이트 1 이 재려는 "이유가 있나"
    가 가려진다.

    반환하는 매칭 문자열은 예측 파일의 `evidence` 로 들어간다. 왜 그 라벨이 나왔는지 사람이
    바로 볼 수 있어야 규칙을 고칠 수 있다.
    """
    if not message or not message.strip():
        return UNKNOWN_LABEL, ()

    for label, patterns in _COMPILED:
        matched = tuple(
            match.group(0).strip()
            for pattern in patterns
            if (match := pattern.search(message)) is not None
        )
        if matched:
            return label, matched
    return UNKNOWN_LABEL, ()


def predict(record: dict[str, Any]) -> Prediction:
    """레코드 하나를 분류한다. 정답 필드는 읽지 않는다 (라벨 없이 동작해야 한다)."""
    label, matched = classify_message(commit_message_of(record))
    return Prediction(
        record_id=record_id_of(record),
        predicted_label=label,
        method=METHOD_KEYWORD,
        version=KEYWORD_RULES_VERSION,
        evidence=matched,
        note="" if matched else "매칭된 키워드 없음",
    )


def predict_all(records: Sequence[dict[str, Any]]) -> list[Prediction]:
    return [predict(record) for record in records]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m classify.baseline_keyword",
        description="기준선 A - 커밋 메시지 키워드 규칙으로 이유 8종 예측 (#44).",
    )
    parser.add_argument("--input", type=Path, required=True, help="레코드 JSONL")
    parser.add_argument("--out", type=Path, default=None, help="예측 JSONL 저장 경로")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    records = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        print("레코드가 없다.", file=sys.stderr)
        return 1

    predictions = predict_all(records)
    if args.out:
        written = write_predictions(args.out, predictions)
        print(f"예측: {args.out} ({written}건)", file=sys.stderr)

    print(f"기준선 A (규칙 {KEYWORD_RULES_VERSION})")
    for line in summarize(predictions).format_lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
