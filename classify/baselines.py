"""기준선 공통 출력 형식 (§10.2 기준선 비교). 담당: 희수 (hs)

왜 공통 형식이 필요한가:
    기준선 A(키워드)·B(LLM)·우리 방식의 정확도를 같은 코드로 재려면 세 결과가 같은 모양이어야
    한다. 형식이 갈리면 비교 스크립트를 세 벌 쓰게 되고, 그러면 "우리 방식이 Δ만큼 낫다"를
    말할 때 계산이 달라서 그런 게 아니라고 증명하기 어려워진다.

왜 `predicted_label` 인가:
    사람이 붙인 라벨은 `reason_label` 이다 (라벨 가이드 §7.2). 기준선 출력은 **정답이 아니라
    예측**이므로 이름을 다르게 둔다. 특히 LLM 출력을 라벨 자리에 쓰면 §10.2 평가가 자기 참조로
    무너진다 (ADR-005). 필드 이름이 다르면 섞여 들어가도 바로 보인다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classify.labels import REASON_LABELS

UNKNOWN_LABEL = "UNK"

METHOD_KEYWORD = "keyword"
METHOD_LLM = "llm"


@dataclass(frozen=True)
class Prediction:
    """기준선 하나가 레코드 하나에 대해 내놓은 예측."""

    record_id: str
    predicted_label: str
    method: str
    version: str
    evidence: tuple[str, ...] = ()
    """왜 그 라벨인지 - A 는 매칭된 키워드, B 는 모델이 준 근거 문장."""
    note: str = ""
    """실패·보정 사유. 조용히 UNK 로 만들지 않기 위한 자리다."""

    def __post_init__(self) -> None:
        if self.predicted_label not in REASON_LABELS:
            raise ValueError(
                f"{self.predicted_label!r} 은 §4.2 ③ 8종이 아니다 ({'|'.join(REASON_LABELS)})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "predicted_label": self.predicted_label,
            "method": self.method,
            "version": self.version,
            "evidence": list(self.evidence),
            "note": self.note,
        }


def record_id_of(record: dict[str, Any]) -> str:
    """§4.4 는 `id`, 라벨 쪽은 `record_id` 로 부른다. 둘 다 받아 준다."""
    raw = record.get("id") or record.get("record_id")
    return "" if raw is None else str(raw).strip()


def commit_message_of(record: dict[str, Any]) -> str:
    """레코드에서 커밋 메시지를 꺼낸다. #5 출력과 #6 맥락 결합 결과 양쪽을 받는다."""
    context = record.get("context") or {}
    return context.get("commit_message") or record.get("commit_message") or ""


def write_predictions(path: Path, predictions: Iterable[Prediction]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    return count


@dataclass
class LabelCounts:
    """예측 분포. 정확도가 아니라 "무엇을 얼마나 골랐나" 만 센다.

    정확도·F1·혼동 행렬은 수동 라벨 500건이 있어야 한다 (§10.2, 5~6주차). 여기서는 범위 밖이다.
    다만 한 라벨로 쏠렸는지는 지금도 봐야 한다 - 전부 UNK 를 내는 기준선은 비교 대상이 못 된다.
    """

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, prediction: Prediction) -> None:
        label = prediction.predicted_label
        self.counts[label] = self.counts.get(label, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def format_lines(self) -> list[str]:
        if not self.total:
            return ["예측 없음"]
        lines = [f"예측 {self.total}건"]
        for label in REASON_LABELS:
            count = self.counts.get(label, 0)
            if count:
                lines.append(f"  {label:<7} {count:>5} ({count / self.total:.1%})")
        return lines


def summarize(predictions: Sequence[Prediction]) -> LabelCounts:
    counts = LabelCounts()
    for prediction in predictions:
        counts.add(prediction)
    return counts
