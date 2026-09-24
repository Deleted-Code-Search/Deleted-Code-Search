"""우리 방식 - 규칙 + scikit-learn + LLM 을 합쳐 이유와 근거 등급을 낸다 (#84). 담당: 희수

무엇을:
    §4.4 레코드 하나를 받아 `reason`(이유 8종 + 근거 등급 + 근거 문장 + 신뢰도)을 낸다.
    CHARTER §4.2 ③ "우리 방식" 이고, 게이트 2(§10.2)에서 기준선 A/B 를 이겨야 하는 쪽이다.

근거가 먼저다:
    1. `classify.rules` 로 맥락에서 **이유를 말하는 문장**을 찾는다. 대체 코드(`replacement.code`)
       가 있는지도 본다
    2. 둘 다 없으면 **UNK / UNKNOWN** 이다. 모델·LLM 이 무엇을 고르든 뒤집지 않는다
    3. 있으면 이유 7종을 규칙·모델·LLM 점수로 고른다. 동점이면 이유 문장이 받치는 라벨이 이긴다
    4. 고른 라벨의 가장 강한 근거로 등급을 정한다 (`Classifier._grade`)

    "근거가 있나" 와 "어느 이유인가" 를 가른다. 앞은 UNK 를 정하는 문이고, 뒤는 점수다. 문장의
    키워드 라벨로 후보를 자르면 키워드 잡음이 정답을 후보에서 빼 버린다 (`Classifier._scores`).
    가이드 §6.2.1 의 "근거 6종 중 무엇인지 말할 수 없으면 UNKNOWN" 은 1·2 가 지킨다.

LLM 은 후보와 근거 문장만 (ADR-005):
    LLM 답은 라벨 점수에 한 표를 더할 뿐이다. 근거가 하나도 없는 레코드에서는 LLM 이 무엇을
    골라도 UNK 이고, 동점에서는 이유 문장이 받치는 라벨이 LLM 표를 이긴다. 근거 문장을 써 줄
    수는 있다 - ADR-005 가 허락한 "근거 작성" 이다. EXPLICIT 의 근거 문장은 LLM 이 쓰지 않는다.
    원문 인용이어야 한다 (가이드 §6.1).

자리표시자인 것:
    구성요소 가중치(`WEIGHT_*`)와 "이유 문장이 이 함수를 가리키지 않을 때" 의 신뢰도는 개발용
    합의 102건(가이드 v1 라벨)에 맞춘 출발점이다. 500건 val 100 으로 다시 정한다 (#86).
    근거 ②~⑥(가이드 §6.2.1)은 아직 없다 - ②③은 레코드에 필드가 없고, ④⑤⑥은 규칙을 새로
    설계해야 한다.

실행:
    python -m classify.classifier --records records.jsonl --labels merged.jsonl --out out.jsonl
    python -m classify.classifier ... --llm        # LLM 후보도 쓴다 (ANTHROPIC_API_KEY, #59)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classify.baseline_keyword import KEYWORD_RULES
from classify.baseline_llm import (
    DEFAULT_MODEL,
    LABEL_DEFINITIONS,
    MAX_DIFF_CHARS,
    LlmBaseline,
    anthropic_caller,
    parse_answer,
)
from classify.baselines import UNKNOWN_LABEL, record_id_of
from classify.labels import EVIDENCE_GRADES, REASON_LABELS
from classify.model import MODEL_VERSION, ReasonModel
from classify.rules import RULES_VERSION, ReasonSentence, find_reason_sentences, passages
from pipeline.select_repos import load_env_file, resolve_cache_dir

METHOD_OURS = "ours"
CANDIDATE_PROMPT_VERSION = "c1"
CLASSIFIER_VERSION = f"{RULES_VERSION}+{MODEL_VERSION}+{CANDIDATE_PROMPT_VERSION}"

EXPLICIT, INFERRED, UNKNOWN = EVIDENCE_GRADES
REASONS: tuple[str, ...] = tuple(label for label in REASON_LABELS if label != UNKNOWN_LABEL)
# 점수가 같을 때의 순서. 기준선 A 규칙 순서 = 가이드 §11-1 우선순위 (SEC > LIB > ... > DESIGN).
PRIORITY: tuple[str, ...] = tuple(label for label, _patterns in KEYWORD_RULES)

# 구성요소 가중치 - 자리표시자 (모듈 독스트링). 셋 다 [0, 1] 점수라 같은 무게로 시작한다.
WEIGHT_RULE = 1.0
WEIGHT_MODEL = 1.0
WEIGHT_LLM = 1.0

# EXPLICIT 은 1.0 고정 (가이드 §6.1, §11-3).
EXPLICIT_CONFIDENCE = 1.0
# 이유 문장은 있는데 이 함수를 이름으로 가리키지 않는다 - 가이드 §6.1.1 E2 를 못 넘어 INFERRED.
# 0.5~0.8 구간(§6.2.2)의 가운데. 개발 102건의 사람 라벨도 0.55~0.6 에 몰려 있다.
UNREACHED_SENTENCE_CONFIDENCE = 0.6
# INFERRED 는 1.0 을 쓰지 않는다 (§6.2.2). 대체 코드 근거는 이 값과 `replacement.confidence`
# 중 작은 쪽 - 파이프라인이 0.7 만 확신하는 대체 코드로 0.8+ 를 줄 수 없다.
INFERRED_CONFIDENCE_CAP = 0.9
# 이 아래는 INFERRED 가 아니라 UNKNOWN 이다 (§6.2.2).
INFERRED_FLOOR = 0.5

MAX_CONTEXT_CHARS = 6000
MAX_REPLACEMENT_CHARS = 2000


@dataclass(frozen=True)
class Classification:
    """레코드 하나에 대한 우리 방식의 답."""

    record_id: str
    label: str
    evidence_grade: str
    evidence_text: str
    evidence_source: str
    evidence_locator: str
    confidence: float
    version: str = CLASSIFIER_VERSION
    scores: dict[str, float] = field(default_factory=dict)
    """허락된 라벨별 합산 점수. 왜 그 라벨인지 사람이 볼 수 있어야 가중치를 고칠 수 있다."""
    note: str = ""

    def __post_init__(self) -> None:
        if self.label not in REASON_LABELS:
            raise ValueError(f"{self.label!r} 은 §4.2 ③ 8종이 아니다")
        if self.evidence_grade not in EVIDENCE_GRADES:
            raise ValueError(f"{self.evidence_grade!r} 은 근거 3등급이 아니다")

    def to_schema_reason(self) -> dict[str, Any]:
        """§4.4 `reason` 그대로. 필드를 늘리거나 이름을 바꾸지 않는다 (§13).

        `evidence_source`·`evidence_locator` 는 §4.4 `reason` 에 칸이 없어 여기 넣지 않는다.
        예측 파일(`to_dict`)에만 남는다 - 평가 때 근거 위치를 대조하는 데 쓴다.
        """
        return {
            "label": self.label,
            "evidence_grade": self.evidence_grade,
            "evidence_text": self.evidence_text or None,
            "confidence": self.confidence,
            "classifier_version": self.version,
        }

    def to_dict(self) -> dict[str, Any]:
        """예측 파일 한 줄. 기준선 예측(`classify.baselines.Prediction`)과 같은 키로 시작한다.

        `predicted_label` 인 이유도 기준선과 같다 - 사람 라벨(`reason_label`)과 이름을 갈라
        섞여 들어가면 바로 보이게 한다 (ADR-005).
        """
        return {
            "record_id": self.record_id,
            "predicted_label": self.label,
            "method": METHOD_OURS,
            "version": self.version,
            "evidence_grade": self.evidence_grade,
            "evidence_text": self.evidence_text,
            "evidence_source": self.evidence_source,
            "evidence_locator": self.evidence_locator,
            "confidence": self.confidence,
            "scores": {label: round(score, 4) for label, score in self.scores.items()},
            "note": self.note,
        }


# --------------------------------------------------------------------------------------
# LLM 후보 (ADR-005 - 후보 생성·근거 작성만)
# --------------------------------------------------------------------------------------


def build_candidate_prompt(record: dict[str, Any]) -> str:
    """LLM 에 줄 본문. 기준선 B 와 달리 **맥락 전체와 대체 코드**를 준다 - 그 차이가 우리 방식이다.

    맥락은 `classify.rules.passages` 가 만든 문장을 위치와 함께 준다. 같은 문장을 규칙과 LLM
    이 함께 보므로, LLM 이 근거로 든 문장이 어디 있었는지 대조할 수 있다.
    """
    definitions = "\n".join(f"- {code}: {meaning}" for code, meaning in LABEL_DEFINITIONS)
    context_lines: list[str] = []
    used = 0
    for passage in passages(record):
        line = f"[{passage.locator}] {passage.text}"
        if used + len(line) > MAX_CONTEXT_CHARS:
            context_lines.append("... (이하 생략)")
            break
        context_lines.append(line)
        used += len(line)
    context = "\n".join(context_lines) or "(맥락 없음)"
    deleted = (record.get("deleted_body") or "(삭제 코드 없음)")[:MAX_DIFF_CHARS]
    replacement = ((record.get("replacement") or {}).get("code") or "(없음)")[
        :MAX_REPLACEMENT_CHARS
    ]
    return (
        "아래 함수가 왜 삭제됐는지 한 가지로 분류하라.\n\n"
        f"분류 체계:\n{definitions}\n\n"
        f"맥락 (줄 앞 [ ] 는 출처):\n{context}\n\n"
        f"삭제된 코드:\n```\n{deleted}\n```\n\n"
        f"같은 자리에 들어온 대체 코드:\n```\n{replacement}\n```\n\n"
        "답은 정확히 한 줄로, `라벨|근거` 형식으로만 쓴다. 라벨은 위 8개 중 하나이고, 근거는 "
        "맥락이나 코드에서 이유를 드러내는 부분을 한 문장으로 쓴다. 근거가 없으면 UNK 를 고른다."
    )


@dataclass
class LlmCandidate:
    """LLM 에게 라벨 후보와 근거 문장을 묻는다. 캐시·호출기는 기준선 B 것을 그대로 쓴다."""

    runner: LlmBaseline
    failures: dict[str, int] = field(default_factory=dict)

    def propose(self, record: dict[str, Any]) -> tuple[str, str] | None:
        """(라벨, 근거 문장). 호출이 실패하거나 답을 못 읽으면 `None` - 분류는 LLM 없이 계속한다.

        LLM 은 세 구성요소 중 하나다. 한 건의 호출 실패로 배치 전체가 멈추면 안 되고, 실패를
        UNK 로 바꿔 한 표를 주면 UNK 쪽으로 기운다. 표를 주지 않는 것이 맞다.
        """
        try:
            text = self.runner.ask(build_candidate_prompt(record))
        except (urllib.error.URLError, OSError, ValueError) as error:
            self._fail(f"호출 실패: {type(error).__name__}")
            return None
        label, reason, problem = parse_answer(text)
        if problem:
            self._fail(problem.split(":")[0])
            return None
        return label, reason

    def _fail(self, reason: str) -> None:
        self.failures[reason] = self.failures.get(reason, 0) + 1


# --------------------------------------------------------------------------------------
# 분류
# --------------------------------------------------------------------------------------


@dataclass
class Classifier:
    """규칙 + 모델 + (선택) LLM."""

    model: ReasonModel = field(default_factory=ReasonModel)
    llm: LlmCandidate | None = None

    def classify(self, record: dict[str, Any]) -> Classification:
        """레코드 하나. 순서는 모듈 독스트링 "근거가 먼저다"."""
        record_id = record_id_of(record)
        sentences = find_reason_sentences(record)
        replacement = _usable_replacement(record)

        if not sentences and replacement is None:
            return Classification(
                record_id, UNKNOWN_LABEL, UNKNOWN, "", "", "", 0.0, note="근거 없음"
            )

        candidate = self.llm.propose(record) if self.llm is not None else None
        scores = self._scores(record, sentences, candidate)
        backed = {sentence.label for sentence in sentences}
        label = max(
            scores, key=lambda reason: (scores[reason], reason in backed, -_priority(reason))
        )
        return self._grade(record_id, label, sentences, replacement, candidate, scores)

    def _scores(
        self,
        record: dict[str, Any],
        sentences: Sequence[ReasonSentence],
        candidate: tuple[str, str] | None,
    ) -> dict[str, float]:
        """이유 7종 모두의 점수. 규칙은 그 라벨을 가리키는 이유 문장의 비율, 모델은 확률,
        LLM 은 그 라벨을 골랐으면 1. 셋 다 [0, 1] 이다.

        후보를 문장의 키워드 라벨로 **자르지 않는다.** 처음엔 잘랐는데, 키워드 라벨은 잡음이
        커서 정답을 후보에서 빼 버렸다. 예비 200건에서 사람이 DEAD 라 한 23건의 문장 키워드
        라벨은 DESIGN 72 · BUG 18 · PERF 10 · DEAD 3 이었다 (PR 본문의 `clean`·`simplify`
        같은 단어). 모델은 그 23건에 DEAD 확률을 평균 0.54 줬는데도 DEAD 가 후보에 없어 200건
        중 DEAD 가 2건만 나왔다. "근거가 있나" 는 UNK 를 가르는 데만 쓰고, "어느 이유인가" 는
        세 구성요소가 점수로 정한다.
        """
        votes = Counter(sentence.label for sentence in sentences)
        total = sum(votes.values())
        probabilities = self.model.predict_proba(record)
        llm_label = candidate[0] if candidate else None
        return {
            reason: WEIGHT_RULE * (votes[reason] / total if total else 0.0)
            + WEIGHT_MODEL * probabilities.get(reason, 0.0)
            + WEIGHT_LLM * (1.0 if reason == llm_label else 0.0)
            for reason in REASONS
        }

    def _grade(
        self,
        record_id: str,
        label: str,
        sentences: Sequence[ReasonSentence],
        replacement: tuple[str, float] | None,
        candidate: tuple[str, str] | None,
        scores: dict[str, float],
    ) -> Classification:
        """고른 라벨의 가장 강한 근거로 등급을 정한다.

        1. 그 라벨의 이유 문장이 삭제된 함수·파일을 이름으로 가리킨다 -> EXPLICIT (원문 인용)
        2. 그 라벨의 이유 문장은 있는데 이 함수까지 닿지 않는다 -> INFERRED (가이드 §6.1.1 E2)
        3. 대체 코드가 있다 -> INFERRED, 근거 ① (`diff:replacement`)
        4. 이유 문장은 있는데 키워드가 다른 이유를 가리켰고, 라벨은 모델·LLM 이 골랐다 ->
           INFERRED, 하한 0.5. 가장 약한 경우라 신뢰도를 올리지 않는다

        EXPLICIT 은 **같은 라벨**의 문장으로만 준다 - 인용문이 그 이유를 말해야 한다 (§6.1).
        """
        own = [sentence for sentence in sentences if sentence.label == label]
        named = [sentence for sentence in own if sentence.names_target]
        if named:
            passage = named[0].passage
            return Classification(
                record_id,
                label,
                EXPLICIT,
                passage.text,
                passage.source,
                passage.locator,
                EXPLICIT_CONFIDENCE,
                scores=scores,
            )
        if own:
            passage = own[0].passage
            return Classification(
                record_id,
                label,
                INFERRED,
                passage.text,
                passage.source,
                passage.locator,
                UNREACHED_SENTENCE_CONFIDENCE,
                scores=scores,
                note="이유 문장이 삭제된 함수를 이름으로 가리키지 않는다 (가이드 §6.1.1 E2)",
            )
        if replacement is not None:
            code, confidence = replacement
            if candidate is not None and candidate[0] == label and candidate[1]:
                text, note = candidate[1], "근거 문장: LLM 작성 (ADR-005)"
            else:
                text, note = f"같은 자리에 대체 코드가 들어왔다: {_first_line(code)}", ""
            return Classification(
                record_id,
                label,
                INFERRED,
                text,
                "diff",
                "diff:replacement",
                confidence,
                scores=scores,
                note=note,
            )
        # 근거가 없으면 `classify` 가 이미 UNK 로 돌려보냈다 - 여기 오면 이유 문장이 있다.
        passage = sentences[0].passage
        return Classification(
            record_id,
            label,
            INFERRED,
            passage.text,
            passage.source,
            passage.locator,
            INFERRED_FLOOR,
            scores=scores,
            note=f"문장 키워드는 {sentences[0].label} 을 가리켰고 라벨은 모델·LLM 이 골랐다",
        )


def _usable_replacement(record: dict[str, Any]) -> tuple[str, float] | None:
    """근거 ① 로 쓸 수 있는 대체 코드와 그 신뢰도. 못 쓰면 `None`.

    `code` 가 `null` 이면 근거 ① 을 쓸 수 없다 (가이드 §6.2.3). 신뢰도가 0.5 아래로 떨어지면
    INFERRED 가 아니라 UNKNOWN 이다 (§6.2.2).
    """
    replacement = record.get("replacement") or {}
    code = replacement.get("code")
    if not code:
        return None
    raw = replacement.get("confidence")
    confidence = min(INFERRED_CONFIDENCE_CAP, float(raw) if isinstance(raw, int | float) else 0.0)
    return (code, confidence) if confidence >= INFERRED_FLOOR else None


def _priority(label: str) -> int:
    return PRIORITY.index(label) if label in PRIORITY else len(PRIORITY)


def _first_line(code: str) -> str:
    return next((line.strip() for line in code.splitlines() if line.strip()), "")


# --------------------------------------------------------------------------------------
# 입력·학습
# --------------------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_final_labels(rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    """병합 라벨 파일(`eval/gate1_merge.py` 출력) -> `{record_id: final.reason_label}`.

    확정된 것만 쓴다. 두 사람이 갈려 확정되지 않은 건(`final.reason_label` 이 `null`)을 한쪽
    라벨로 채우면 그 사람의 판단을 정답으로 학습하는 셈이다.
    """
    labels: dict[str, str] = {}
    for row in rows:
        label = (row.get("final") or {}).get("reason_label")
        record_id = str(row.get("record_id") or "").strip()
        if label in REASON_LABELS and record_id:
            labels[record_id] = label
    return labels


def train_model(records: Sequence[dict[str, Any]], labels: dict[str, str]) -> ReasonModel:
    """레코드와 확정 라벨을 `id` 로 이어 모델을 학습한다."""
    pairs = [(record, labels[key]) for record in records if (key := record_id_of(record)) in labels]
    return ReasonModel().fit([record for record, _ in pairs], [label for _, label in pairs])


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자. 실행 방법은 모듈 독스트링 "실행" 절."""
    parser = argparse.ArgumentParser(
        prog="python -m classify.classifier",
        description="우리 방식 - 규칙 + scikit-learn + LLM 으로 이유와 근거 등급 (#84).",
    )
    parser.add_argument("--records", type=Path, required=True, help="§4.4 레코드 JSONL")
    parser.add_argument(
        "--labels", type=Path, required=True, help="확정 라벨 (병합 파일, 모델 학습용)"
    )
    parser.add_argument("--out", type=Path, default=None, help="예측 JSONL 저장 경로")
    parser.add_argument("--llm", action="store_true", help="LLM 후보도 쓴다 (API 호출, #59)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM 모델 (--llm 일 때)")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """분류하고 분포를 보고한다. **정확도는 내지 않는다** - 정확도는 #86 이 test 로 잰다."""
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    records = load_jsonl(args.records)
    if not records:
        print("레코드가 없다.", file=sys.stderr)
        return 1
    labels = load_final_labels(load_jsonl(args.labels))
    model = train_model(records, labels)

    llm = None
    if args.llm:
        load_env_file(args.env_file)
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            print("--llm 에는 ANTHROPIC_API_KEY 가 필요하다 (.env, §8.4).", file=sys.stderr)
            return 2
        cache_dir = args.cache_dir or resolve_cache_dir(None) / "llm_classifier"
        runner = LlmBaseline(
            anthropic_caller(api_key),
            model=args.model,
            cache_dir=cache_dir,
            prompt_version=CANDIDATE_PROMPT_VERSION,
        )
        llm = LlmCandidate(runner)

    classifier = Classifier(model=model, llm=llm)
    results = [classifier.classify(record) for record in records]

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        print(f"예측: {args.out} ({len(results)}건)", file=sys.stderr)

    print(f"우리 방식 ({CLASSIFIER_VERSION}) - {len(results)}건")
    print(f"  모델 학습: {'예' if model.trained else '아니오'} (확정 라벨 {len(labels)}건과 이음)")
    print("  학습에 쓴 레코드도 예측에 들어간다. 이 분포로 정확도를 말하지 않는다 (#86).")
    for name, counter in (
        ("라벨", Counter(result.label for result in results)),
        ("등급", Counter(result.evidence_grade for result in results)),
    ):
        detail = ", ".join(f"{key} {count}" for key, count in counter.most_common())
        print(f"  {name}: {detail}")
    if llm is not None:
        print(f"  LLM: API {llm.runner.calls}회 / 캐시 {llm.runner.cache_hits}회")
        if llm.failures:
            print(f"  LLM 실패: {', '.join(f'{k} {v}건' for k, v in llm.failures.items())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
