"""게이트 1 측정 — 이유 회수율 3구간 + 쌍별 kappa + 판정 (이슈 #37). 담당: 성제 (sj)

왜 라벨링 전에 커밋하나:
    라벨링이 시작되기 전에 측정 코드가 커밋되어 있어야 한다. 라벨 결과를 보고 나서 임계값이나
    집계 방식을 정하면 "60%가 나오도록 맞춘 것 아니냐"에 방어할 수 없다. 커밋 날짜가 그 증거다.
    그래서 판정에 쓰는 숫자는 전부 아래 상수 한 곳에 모았다. 바꾸는 커밋은 diff 에서 바로 보이고,
    바꾸려면 회의 결정과 근거를 커밋 본문에 적는다 (docs/evaluation.md "게이트 1 측정 방법").

왜 회수율을 3구간으로 내나:
    회수율(§15 EXPLICIT+INFERRED)은 INFERRED 를 어디까지 믿느냐에 따라 움직인다. 숫자 하나만
    내면 경계 근처에서 신뢰도를 어떻게 매겼는지가 판정을 좌우하는데 그게 보이지 않는다.
    EXPLICIT만 / +INFERRED(≥0.5) / +INFERRED(≥0.8) 세 판정이 같으면 판정이 튼튼한 것이고,
    갈리면 사람이 회의에서 정한다. 가이드 §6.2 는 0.5 미만 INFERRED 를 금지하므로 규칙을 지킨
    라벨에서는 2번째 구간이 §15 정의 그대로의 회수율이다.

#34 (classify/labels.py) 와의 관계:
    병합, 등급 확정 규칙(`resolved_grade`), 쌍별 kappa(`agreement_for`)는 labels.py 것을 그대로
    쓴다. kappa 를 두 번 구현하면 두 숫자가 갈릴 수 있다. 여기서 더하는 것은 (1) 3구간 회수율
    (2) 구간 판정이 갈릴 때의 처리 (3) 이유·등급 분포와 교차표 (4) JSON·마크다운 리포트다.

실행:
    python -m eval.gate1 datasets/labels/sj_pre200.jsonl datasets/labels/jh_pre200.jsonl \\
        datasets/labels/hs_pre200.jsonl --json-out gate1.json --md-out gate1.md
    python -m eval.gate1 datasets/labeled_500.jsonl          # 토론 확정이 들어간 병합 파일
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from classify import labels
from classify.sampling import BATCH, LABELERS

# --------------------------------------------------------------------------------------
# 사전 확정 상수. 판정에 쓰는 숫자는 여기에만 있다. 경계는 전부 "이상(≥)"을 포함한다.
# --------------------------------------------------------------------------------------

# 가이드 §6.2 신뢰도 구간 "0.5 미만은 INFERRED 를 쓰지 않는다" (가이드 §11-2 [팀 확정 필요])
INFERRED_MIN_CONFIDENCE = 0.5
# 가이드 §6.2 신뢰도 구간 "0.8 ~ 1.0 대체 코드가 이유를 거의 증명한다" (가이드 §11-2 [팀 확정 필요])
INFERRED_STRONG_CONFIDENCE = 0.8
# CHARTER §9 2주차 게이트 1 "EXPLICIT+INFERRED ≥ 60% → 진행"
GATE1_PROCEED = 0.60
# CHARTER §9 2주차 게이트 1 "40~60% → 저장소 기준 강화 / <40% → 대체 코드 중심으로 축 이동"
GATE1_TIGHTEN = 0.40
# CHARTER §11 라벨 품질 리스크 "kappa < 0.6 → 라벨 가이드 개정, 애매 클래스 병합(DESIGN/FEAT 등)"
KAPPA_RISK = 0.60

# 판정 문구 (CHARTER §9 문구 그대로)
VERDICT_PROCEED = "진행"
VERDICT_TIGHTEN = "저장소 기준 강화"
VERDICT_PIVOT = "대체 코드 중심으로 축 이동"
VERDICT_UNDECIDABLE = "판정 불가 (등급이 확정된 건 없음)"
VERDICT_SPLIT = "판정 갈림 — 회의 필요"

# kappa 는 labels.py 가 float 로 계산한다. 수학적으로 정확히 0.6 인 값이 0.59999… 로 나와
# 경고가 뜨는 일만 막는 허용 오차다. 판정 기준이 아니다.
_KAPPA_FLOAT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Tier:
    """회수율 구간 하나."""

    key: str
    title: str
    min_inferred_confidence: float | None
    """INFERRED 를 회수로 셀 최소 신뢰도(이상). None 이면 INFERRED 를 세지 않는다."""


TIERS: tuple[Tier, ...] = (
    Tier("explicit_only", "EXPLICIT만", None),
    Tier(
        "inferred_min",
        f"EXPLICIT + INFERRED(conf ≥ {INFERRED_MIN_CONFIDENCE})",
        INFERRED_MIN_CONFIDENCE,
    ),
    Tier(
        "inferred_strong",
        f"EXPLICIT + INFERRED(conf ≥ {INFERRED_STRONG_CONFIDENCE})",
        INFERRED_STRONG_CONFIDENCE,
    ),
)


def _exact(value: float) -> Fraction:
    """0.6 을 3/5 로. 경계 비교를 부동소수 오차 없이 하려고 쓴다."""
    return Fraction(str(value))


def _as_float(value: object) -> float | None:
    """JSON 숫자만 신뢰도로 인정한다. bool 은 int 의 하위형이라 따로 막는다."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


# --------------------------------------------------------------------------------------
# 판정
# --------------------------------------------------------------------------------------


def gate1_verdict(recovered: int, resolved: int) -> str:
    """CHARTER §9 게이트 1. 정확히 60% → 진행, 정확히 40% → 저장소 기준 강화.

    비율을 분수로 비교한다. 120/200 같은 값이 부동소수 오차로 경계 아래로 떨어지지 않게.
    """
    if resolved <= 0:
        return VERDICT_UNDECIDABLE
    rate = Fraction(recovered, resolved)
    if rate >= _exact(GATE1_PROCEED):
        return VERDICT_PROCEED
    if rate >= _exact(GATE1_TIGHTEN):
        return VERDICT_TIGHTEN
    return VERDICT_PIVOT


def counts_as_recovered(grade: str | None, confidence: float | None, tier: Tier) -> bool:
    """이 구간에서 회수로 세는가. EXPLICIT 은 항상, INFERRED 는 신뢰도가 구간 기준 이상일 때만."""
    if grade == "EXPLICIT":
        return True
    if grade != "INFERRED" or tier.min_inferred_confidence is None or confidence is None:
        return False
    return _exact(confidence) >= _exact(tier.min_inferred_confidence)


@dataclass(frozen=True)
class ResolvedRecord:
    """레코드 1건의 확정 상태. None 은 "아직 사람이 정하지 않았다"는 뜻이다."""

    record_id: str
    reason: str | None
    grade: str | None
    confidence: float | None


def resolve_record(row: dict[str, Any]) -> ResolvedRecord:
    """병합 1줄 → 확정 상태. 등급은 labels.resolved_grade 규칙 그대로 (#34).

    INFERRED 의 신뢰도 [팀 확정 필요]:
        - 사람이 토론으로 정한 `final`(DISCUSSED·THIRD_PARTY)이 있으면 그 값
        - 없으면 2인 신뢰도 중 **작은 값**. 한 사람 0.9, 다른 사람 0.7 이면 "≥0.8 구간"에
          넣을지 CHARTER·가이드에 규칙이 없다. 작은 쪽을 쓰면 두 사람 모두 그 강도를 인정한 건만
          강한 구간에 들어간다.
        - `AGREED` final 은 labels.build_final 이 첫 라벨러의 신뢰도만 옮긴 것이라 쓰지 않고
          개별 라벨에서 다시 계산한다.
    """
    record_id = str(row.get("record_id") or "")
    grade = labels.resolved_grade(row)
    final = row.get("final") or {}
    label_rows = row.get("labels") or []

    if final.get("evidence_grade") and final.get("method") != "AGREED":
        return ResolvedRecord(
            record_id, final.get("reason_label"), grade, _as_float(final.get("confidence"))
        )

    same_reason, _ = labels.agreement_of(label_rows)
    reason = label_rows[0].get("reason_label") if same_reason else None
    confidence = None
    if grade is not None and label_rows:
        values = [_as_float(label.get("confidence")) for label in label_rows]
        known = [value for value in values if value is not None]
        if known and len(known) == len(values):
            confidence = min(known)
    return ResolvedRecord(record_id, reason, grade, confidence)


@dataclass
class TierResult:
    tier: Tier
    recovered: int
    resolved: int

    @property
    def rate(self) -> float | None:
        return self.recovered / self.resolved if self.resolved else None

    @property
    def verdict(self) -> str:
        return gate1_verdict(self.recovered, self.resolved)


@dataclass
class Recovery:
    """3구간 회수율. 세 구간은 분모가 같다 — 등급이 확정된 건만 센다 (#34 와 같은 규칙)."""

    name: str
    tiers: list[TierResult]
    unresolved: int

    @property
    def resolved(self) -> int:
        return self.tiers[0].resolved if self.tiers else 0

    @property
    def decision(self) -> str:
        """세 구간 판정이 같을 때만 그 판정. 하나라도 다르면 사람이 정한다."""
        verdicts = {result.verdict for result in self.tiers}
        if VERDICT_UNDECIDABLE in verdicts:
            return VERDICT_UNDECIDABLE
        if len(verdicts) == 1:
            return verdicts.pop()
        return VERDICT_SPLIT


def recovery_tiers(records: Sequence[ResolvedRecord], name: str = "전체") -> Recovery:
    decided = [record for record in records if record.grade is not None]
    tiers = [
        TierResult(
            tier,
            sum(
                1
                for record in decided
                if counts_as_recovered(record.grade, record.confidence, tier)
            ),
            len(decided),
        )
        for tier in TIERS
    ]
    return Recovery(name, tiers, unresolved=len(records) - len(decided))


def recovery_by_repo(records: Sequence[ResolvedRecord], repo_of: dict[str, str]) -> list[Recovery]:
    grouped: dict[str, list[ResolvedRecord]] = {}
    for record in records:
        repo = repo_of.get(record.record_id)
        if repo:
            grouped.setdefault(repo, []).append(record)
    return [recovery_tiers(items, repo) for repo, items in sorted(grouped.items())]


# --------------------------------------------------------------------------------------
# 분포·교차표
# --------------------------------------------------------------------------------------


@dataclass
class Distribution:
    """이유 × 근거 등급 교차표. 행 합계가 이유 분포, 열 합계가 등급 분포다."""

    crosstab: dict[str, dict[str, int]]

    @property
    def total(self) -> int:
        return sum(sum(row.values()) for row in self.crosstab.values())

    @property
    def by_reason(self) -> dict[str, int]:
        return {reason: sum(row.values()) for reason, row in self.crosstab.items()}

    @property
    def by_grade(self) -> dict[str, int]:
        return {
            grade: sum(row[grade] for row in self.crosstab.values())
            for grade in labels.EVIDENCE_GRADES
        }


def distribution(pairs: Iterable[tuple[str, str]]) -> Distribution:
    counts = Counter(pairs)
    return Distribution(
        {
            reason: {grade: counts[(reason, grade)] for grade in labels.EVIDENCE_GRADES}
            for reason in labels.REASON_LABELS
        }
    )


# --------------------------------------------------------------------------------------
# 일치도 경고
# --------------------------------------------------------------------------------------


def kappa_warnings(agreements: dict[str, list[labels.Agreement]]) -> list[str]:
    """kappa < 0.6 (CHARTER §11). 정확히 0.6 은 경고하지 않는다."""
    action = "라벨 가이드 개정(§5 보강), 애매 클래스 병합 검토(DESIGN/FEAT 등, 병합은 §13 절차)"
    warnings: list[str] = []
    for field_name, results in agreements.items():
        for result in results:
            if result.kappa is not None and result.kappa < KAPPA_RISK - _KAPPA_FLOAT_TOLERANCE:
                warnings.append(
                    f"{field_name} {' + '.join(result.pair)}: kappa {result.kappa:.3f} "
                    f"< {KAPPA_RISK} — CHARTER §11 라벨 품질 리스크. 대응: {action}"
                )
        mean = labels.mean_kappa(results)
        if mean is not None and mean < KAPPA_RISK - _KAPPA_FLOAT_TOLERANCE:
            warnings.append(
                f"{field_name} 평균 kappa {mean:.3f} < {KAPPA_RISK} — CHARTER §11. 대응: {action}"
            )
    return warnings


# --------------------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------------------


def check_label(row: dict[str, Any], where: str) -> list[str]:
    """개인 라벨 1줄의 값 검사. 틀린 값으로 숫자를 내느니 멈춘다 (#36 과 같은 이유)."""
    problems: list[str] = []
    reason = row.get("reason_label")
    grade = row.get("evidence_grade")
    if row.get("labeler") not in LABELERS:
        problems.append(f"{where}: labeler={row.get('labeler')!r} ({'|'.join(LABELERS)})")
    if not row.get("record_id"):
        problems.append(f"{where}: record_id 가 비었다")
    if reason not in labels.REASON_LABELS:
        problems.append(f"{where}: reason_label={reason!r} ({'|'.join(labels.REASON_LABELS)})")
    if grade not in labels.EVIDENCE_GRADES:
        problems.append(f"{where}: evidence_grade={grade!r} ({'|'.join(labels.EVIDENCE_GRADES)})")
    both_valid = reason in labels.REASON_LABELS and grade in labels.EVIDENCE_GRADES
    if both_valid and (reason == "UNK") != (grade == "UNKNOWN"):
        problems.append(
            f"{where}: {reason}/{grade} — UNK 와 UNKNOWN 은 항상 함께 간다 (가이드 §4 UNK, §6.3)"
        )
    if grade == "INFERRED":
        problems.extend(_check_inferred_confidence(row.get("confidence"), where))
    return problems


def _check_inferred_confidence(value: object, where: str) -> list[str]:
    confidence = _as_float(value)
    if confidence is None or not 0.0 <= confidence <= 1.0:
        return [
            f"{where}: INFERRED 인데 confidence={value!r} — 0~1 숫자가 필수다 (가이드 §6.2). "
            "없으면 3구간을 나눌 수 없다"
        ]
    return []


@dataclass
class Inputs:
    merged: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def load_inputs(paths: Sequence[Path], batch: str = BATCH) -> Inputs:
    """개인 라벨 파일과 병합 파일을 섞어 받는다.

    `labels` 키가 있는 줄은 병합 파일(가이드 §7.3)이다. 개인 라벨이 함께 들어오면 labels.py 로
    다시 병합하면서 병합 파일의 토론 확정(`final`)을 이어받는다.
    """
    result = Inputs()
    personal: dict[str, list[dict[str, Any]]] = {labeler: [] for labeler in LABELERS}
    existing: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}

    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path}:{line_number}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                result.problems.append(f"{where}: JSON 이 아니다 ({error.msg})")
                continue

            if "labels" in row:
                existing.append(row)
                for index, label in enumerate(row.get("labels") or []):
                    result.problems.extend(check_label(label, f"{where} labels[{index}]"))
                final = row.get("final") or {}
                if final.get("evidence_grade") == "INFERRED":
                    result.problems.extend(
                        _check_inferred_confidence(final.get("confidence"), f"{where} final")
                    )
                continue

            if not labels.is_filled(row):
                continue  # #33 빈 틀. 아직 사람이 안 채웠다
            result.problems.extend(check_label(row, where))
            key = (str(row.get("record_id")), str(row.get("labeler")))
            if key in seen:
                # 같은 사람의 같은 레코드가 두 줄이면 labels.pair_labels 가 그 레코드를 3인 라벨로
                # 보고 kappa 에서 조용히 뺀다.
                result.problems.append(
                    f"{where}: {key[1]} 의 record_id {key[0]} 가 {seen[key]} 에도 있다 (중복)"
                )
            seen[key] = where
            personal.setdefault(str(row.get("labeler")), []).append(row)

    if any(personal.values()):
        result.merged = labels.merge_labels(personal, batch=batch, existing=existing)
        merged_ids = {row["record_id"] for row in result.merged}
        result.merged.extend(row for row in existing if row.get("record_id") not in merged_ids)
    else:
        result.merged = existing
    return result


# --------------------------------------------------------------------------------------
# 리포트
# --------------------------------------------------------------------------------------


@dataclass
class Gate1Report:
    generated_at: str
    inputs: list[str]
    batch: str
    records: int
    label_count: int
    recovery: Recovery
    per_repo: list[Recovery]
    inferred_below_min: int
    labels_distribution: Distribution
    records_distribution: Distribution
    agreements: dict[str, list[labels.Agreement]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        def recovery_dict(recovery: Recovery) -> dict[str, Any]:
            return {
                "name": recovery.name,
                "resolved": recovery.resolved,
                "unresolved": recovery.unresolved,
                "decision": recovery.decision,
                "tiers": [
                    {
                        "key": result.tier.key,
                        "title": result.tier.title,
                        "min_inferred_confidence": result.tier.min_inferred_confidence,
                        "recovered": result.recovered,
                        "resolved": result.resolved,
                        "rate": result.rate,
                        "verdict": result.verdict,
                    }
                    for result in recovery.tiers
                ],
            }

        def distribution_dict(dist: Distribution) -> dict[str, Any]:
            return {
                "total": dist.total,
                "by_reason": dist.by_reason,
                "by_grade": dist.by_grade,
                "crosstab": dist.crosstab,
            }

        return {
            "generated_at": self.generated_at,
            "inputs": self.inputs,
            "batch": self.batch,
            "constants": {
                "INFERRED_MIN_CONFIDENCE": INFERRED_MIN_CONFIDENCE,
                "INFERRED_STRONG_CONFIDENCE": INFERRED_STRONG_CONFIDENCE,
                "GATE1_PROCEED": GATE1_PROCEED,
                "GATE1_TIGHTEN": GATE1_TIGHTEN,
                "KAPPA_RISK": KAPPA_RISK,
            },
            "records": self.records,
            "labels": self.label_count,
            "recovery": recovery_dict(self.recovery),
            "per_repo": [recovery_dict(item) for item in self.per_repo],
            "inferred_below_min": self.inferred_below_min,
            "distribution": {
                "labels": distribution_dict(self.labels_distribution),
                "records": distribution_dict(self.records_distribution),
            },
            "agreement": {
                field_name: {
                    "mean_kappa": labels.mean_kappa(results),
                    "pairs": [
                        {
                            "pair": list(result.pair),
                            "total": result.total,
                            "agreed": result.agreed,
                            "kappa": result.kappa,
                            "p_o": result.observed,
                            "p_e": result.expected,
                            "confusions": [list(item) for item in result.confusions],
                        }
                        for result in results
                    ],
                }
                for field_name, results in self.agreements.items()
            },
            "warnings": self.warnings,
        }


def build_report(
    merged: Sequence[dict[str, Any]],
    *,
    inputs: Sequence[str] = (),
    batch: str = BATCH,
    repo_of: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Gate1Report:
    resolved = [resolve_record(row) for row in merged]
    recovery = recovery_tiers(resolved)
    all_labels = [label for row in merged for label in row.get("labels") or []]

    below_min = sum(
        1
        for label in all_labels
        if label.get("evidence_grade") == "INFERRED"
        and (_as_float(label.get("confidence")) or 0.0) < INFERRED_MIN_CONFIDENCE
    )
    agreements = {
        "reason_label": labels.agreement_for(merged, "reason_label", labels.REASON_LABELS),
        "evidence_grade": labels.agreement_for(merged, "evidence_grade", labels.EVIDENCE_GRADES),
    }

    warnings: list[str] = []
    if recovery.decision == VERDICT_SPLIT:
        verdicts = " / ".join(f"{r.tier.title} → {r.verdict}" for r in recovery.tiers)
        warnings.append(f"{VERDICT_SPLIT}: {verdicts}. 판정은 사람이 게이트 회의에서 정한다")
    if below_min:
        warnings.append(
            f"INFERRED 인데 신뢰도 < {INFERRED_MIN_CONFIDENCE} 인 라벨 {below_min}건 — 가이드 §6.2 "
            "위반이다. 어느 구간에서도 회수로 세지 않았다. UNKNOWN 으로 고치거나 신뢰도를 다시 본다"
        )
    if recovery.unresolved:
        warnings.append(
            f"등급이 확정되지 않은 {recovery.unresolved}건은 분모에서 뺐다 "
            "(2인 등급 불일치 또는 1인만 라벨). 토론 확정 후 다시 돌린다"
        )
    warnings.extend(kappa_warnings(agreements))

    return Gate1Report(
        generated_at=(now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        inputs=list(inputs),
        batch=batch,
        records=len(merged),
        label_count=len(all_labels),
        recovery=recovery,
        per_repo=recovery_by_repo(resolved, repo_of or {}),
        inferred_below_min=below_min,
        labels_distribution=distribution(
            (str(label.get("reason_label")), str(label.get("evidence_grade")))
            for label in all_labels
        ),
        records_distribution=distribution(
            (record.reason, record.grade)
            for record in resolved
            if record.reason is not None and record.grade is not None
        ),
        agreements=agreements,
        warnings=warnings,
    )


def _percent(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.1%}"


def _crosstab_lines(dist: Distribution) -> list[str]:
    grades = labels.EVIDENCE_GRADES
    lines = [
        f"| 이유 | {' | '.join(grades)} | 합계 |",
        "|---|" + "---|" * (len(grades) + 1),
    ]
    for reason in labels.REASON_LABELS:
        row = dist.crosstab[reason]
        cells = " | ".join(str(row[grade]) for grade in grades)
        lines.append(f"| {reason} | {cells} | {dist.by_reason[reason]} |")
    totals = " | ".join(str(dist.by_grade[grade]) for grade in grades)
    lines.append(f"| **합계** | {totals} | {dist.total} |")
    return lines


def format_markdown(report: Gate1Report) -> list[str]:
    """터미널 출력이자 docs/evaluation.md 에 옮겨 붙일 형태 (§8.7 "측정 결과는 측정마다")."""
    recovery = report.recovery
    date = report.generated_at[:10]
    lines = [
        f"### {date} — 게이트 1 측정 (batch={report.batch}, "
        f"레코드 {report.records}건 / 라벨 {report.label_count}건)",
        "",
        f"> `eval/gate1.py` (#37). 기준: INFERRED ≥ {INFERRED_MIN_CONFIDENCE} / "
        f"≥ {INFERRED_STRONG_CONFIDENCE}, 게이트 ≥ {GATE1_PROCEED:.0%} 진행 · "
        f"≥ {GATE1_TIGHTEN:.0%} 저장소 기준 강화 · 그 아래 축 이동, kappa < {KAPPA_RISK} 경고. "
        "경계는 전부 이상(≥) 포함.",
        "",
        f"**판정: {recovery.decision}**",
        "",
        "#### 이유 회수율 3구간",
        "| 구간 | 회수 | 분모(등급 확정) | 회수율 | 판정 |",
        "|---|---|---|---|---|",
    ]
    for result in recovery.tiers:
        lines.append(
            f"| {result.tier.title} | {result.recovered} | {result.resolved} | "
            f"{_percent(result.rate)} | {result.verdict} |"
        )
    lines.append("")
    lines.append(
        f"- 등급 미확정 {recovery.unresolved}건은 분모에서 뺐다 (2인 등급 불일치 또는 1인만 라벨)"
    )
    lines.append(
        "- §15 정의(EXPLICIT+INFERRED)는 가이드 §6.2 규칙에서 2번째 구간과 같다. "
        "세 구간 판정이 같을 때만 판정이 확정된다"
    )

    if report.per_repo:
        lines += [
            "",
            "#### 저장소별 (§10.5)",
            "| 저장소 | 분모 | 미확정 | " + " | ".join(t.title for t in TIERS) + " |",
            "|---|---|---|" + "---|" * len(TIERS),
        ]
        for item in report.per_repo:
            rates = " | ".join(_percent(result.rate) for result in item.tiers)
            lines.append(f"| {item.name} | {item.resolved} | {item.unresolved} | {rates} |")
        lines.append("")
        lines.append("건수가 적은 저장소의 비율은 신뢰구간이 넓다. 비율만 보지 말 것.")

    for field_name, results in report.agreements.items():
        classes = labels.REASON_LABELS if field_name == "reason_label" else labels.EVIDENCE_GRADES
        lines += [
            "",
            f"#### 일치도 — {field_name} ({len(classes)}클래스, 2인 중복 라벨 건만)",
        ]
        if not results:
            lines.append("- 2인이 모두 라벨한 건이 없다")
            continue
        lines += ["| 쌍 | 건수 | kappa | p_o | p_e |", "|---|---|---|---|---|"]
        for result in results:
            kappa = "정의 불가" if result.kappa is None else f"{result.kappa:.3f}"
            lines.append(
                f"| {' + '.join(result.pair)} | {result.total} | {kappa} | "
                f"{result.observed:.1%} | {result.expected:.3f} |"
            )
        mean = labels.mean_kappa(results)
        lines.append(f"- 단순 평균 kappa: {'—' if mean is None else f'{mean:.3f}'}")
        confusions = sorted(
            ((f"{a} vs {b}", n) for result in results for a, b, n in result.confusions),
            key=lambda item: -item[1],
        )[:3]
        if confusions:
            lines.append("- 혼동 쌍 상위: " + ", ".join(f"{name} {n}건" for name, n in confusions))

    lines += ["", f"#### 분포 — 개별 라벨 {report.labels_distribution.total}건"]
    lines += _crosstab_lines(report.labels_distribution)
    lines += ["", f"#### 분포 — 이유·등급이 확정된 레코드 {report.records_distribution.total}건"]
    lines += _crosstab_lines(report.records_distribution)

    if report.warnings:
        lines += ["", "#### 경고"]
        lines += [f"- {warning}" for warning in report.warnings]
    return lines


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.gate1",
        description="게이트 1 측정 — 이유 회수율 3구간 + 쌍별 kappa + 판정 (#37).",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help=(
            "개인 라벨 JSONL({labeler}_{batch}.jsonl) 또는 병합 파일(labeled_500.jsonl). "
            "섞어도 된다"
        ),
    )
    parser.add_argument(
        "--records",
        type=Path,
        default=None,
        help=(
            "저장소별 회수율용 레코드 파일 (기본: 첫 입력 폴더의 pre200_records.jsonl, 없으면 생략)"
        ),
    )
    parser.add_argument("--batch", default=BATCH)
    parser.add_argument("--json-out", type=Path, default=None, help="JSON 리포트 경로")
    parser.add_argument("--md-out", type=Path, default=None, help="마크다운 리포트 경로")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")

    missing = [path for path in args.inputs if not path.is_file()]
    if missing:
        for path in missing:
            print(f"입력 파일이 없다: {path}", file=sys.stderr)
        return 2

    inputs = load_inputs(args.inputs, args.batch)
    if inputs.problems:
        print("라벨 값에 문제가 있어 측정하지 않는다:", file=sys.stderr)
        for problem in inputs.problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        if len(inputs.problems) > 20:
            print(f"  ... 외 {len(inputs.problems) - 20}건", file=sys.stderr)
        return 2
    if not inputs.merged:
        print("라벨된 줄이 없다. 사람이 채운 뒤 다시 돌려라.", file=sys.stderr)
        return 1

    report = build_report(
        inputs.merged,
        inputs=[str(path) for path in args.inputs],
        batch=args.batch,
        repo_of=labels.repo_index(args.records, args.inputs[0].parent),
    )
    markdown = "\n".join(format_markdown(report)) + "\n"
    print(markdown, end="")

    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(markdown, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
