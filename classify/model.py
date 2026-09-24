"""이유 분류의 학습 부분 - 맥락 텍스트로 이유별 확률을 낸다 (#84). 담당: 희수

무엇을:
    레코드의 맥락 문장(`classify.rules.passages`)과 함수·파일 이름을 텍스트로 이어, TF-IDF +
    로지스틱 회귀로 이유 7종(UNK 제외)의 확률을 낸다. CHARTER §7 의 "규칙 + scikit-learn" 중
    scikit-learn 쪽이다. GPU 없이 노트북에서 도는 크기만 쓴다.

왜 UNK 를 학습하지 않나:
    UNK 는 "근거가 없다" 는 판정이고, 그건 분류기(`classify.classifier`)가 **근거가 있는지**로
    정한다. 모델이 텍스트 모양만 보고 UNK 를 고르게 하면 근거가 있는데도 UNK 가 나오거나 그
    반대가 된다. 모델은 근거가 있을 때 "어느 이유인가" 만 답한다.

지금 이 모델로 말할 수 있는 것:
    거의 없다. 개발용 합의 102건(#84)은 PERF 1건·SEC 0건이고 저장소가 pydantic 하나다. 여기서는
    **학습·예측이 끝까지 돈다**는 것만 확인한다. 특징·하이퍼파라미터는 train 300 / val 100 으로
    정한다 (#86).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from classify.baselines import UNKNOWN_LABEL
from classify.rules import passages

# 특징·모델을 바꾸면 올린다. 분류기 버전 문자열에 들어간다.
MODEL_VERSION = "m1"


def record_text(record: dict[str, Any]) -> str:
    """모델이 보는 텍스트. 규칙이 보는 맥락 문장 전부 + 함수·파일 이름.

    파일 이름은 규칙의 "이 함수를 가리키나" 판정에서는 뺐지만 (`rules.target_names`) 여기서는
    둔다. 모델에는 어느 모듈에서 지워졌는지가 쓸모 있는 신호일 수 있고, 인용문이 아니라서
    잘못 걸려도 EXPLICIT 을 만들지 않는다.

    삭제된 코드 본문은 넣지 않았다. 넣으면 근거 ⑥(가이드 §6.2.1 "삭제된 코드 자체")을 모델이
    배우는 셈인데, 모델이 본문에서 무엇을 보고 골랐는지는 인용할 수 없다. 넣을지는 val 로 본다.
    """
    lines = [passage.text for passage in passages(record)]
    lines.append(record.get("function_name") or "")
    lines.append(Path(record.get("file_path") or "").stem)
    return "\n".join(line for line in lines if line)


@dataclass
class ReasonModel:
    """TF-IDF + 로지스틱 회귀. 학습 전이거나 학습할 수 없으면 빈 확률을 낸다."""

    pipeline: Pipeline | None = None

    def fit(self, records: Sequence[dict[str, Any]], labels: Sequence[str]) -> ReasonModel:
        """학습한다. UNK 는 뺀다 (모듈 독스트링). 이유가 두 종류 미만이면 학습하지 않는다.

        한 종류만으로는 로지스틱 회귀가 학습되지 않는다. 그때 예외를 내면 분류기 전체가
        멈추는데, 모델은 세 구성요소 중 하나라 빠져도 규칙만으로 돈다. 빈 확률을 내고 계속한다.
        """
        pairs = [
            (record_text(record), label)
            for record, label in zip(records, labels, strict=True)
            if label != UNKNOWN_LABEL
        ]
        if len({label for _text, label in pairs}) < 2:
            self.pipeline = None
            return self
        pipeline = Pipeline(
            [
                ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
                # 클래스가 크게 치우쳐 있다 (DESIGN 37 : PERF 1). 가중치 없이 두면 DESIGN 만 낸다.
                ("clf", LogisticRegression(class_weight="balanced", max_iter=1000)),
            ]
        )
        try:
            pipeline.fit([text for text, _label in pairs], [label for _text, label in pairs])
        except ValueError:
            # 텍스트에 토큰이 하나도 없으면 TF-IDF 가 "empty vocabulary" 로 실패한다 (맥락이 전부
            # 비었거나 한 글자 토큰뿐인 레코드). 위와 같은 이유로 멈추지 않고 모델 없이 간다.
            self.pipeline = None
            return self
        self.pipeline = pipeline
        return self

    @property
    def trained(self) -> bool:
        """학습됐나. 안 됐으면 `predict_proba` 가 빈 확률을 낸다."""
        return self.pipeline is not None

    @property
    def classes(self) -> tuple[str, ...]:
        """학습에 나온 이유들. 학습 데이터에 없던 이유(지금은 SEC)는 확률이 0 이다."""
        if self.pipeline is None:
            return ()
        return tuple(str(label) for label in self.pipeline.classes_)

    def predict_proba(self, record: dict[str, Any]) -> dict[str, float]:
        """이유별 확률. 학습 전이면 빈 dict - 분류기는 이 구성요소 없이 진행한다."""
        if self.pipeline is None:
            return {}
        probabilities = self.pipeline.predict_proba([record_text(record)])[0]
        return {label: float(p) for label, p in zip(self.classes, probabilities, strict=True)}
