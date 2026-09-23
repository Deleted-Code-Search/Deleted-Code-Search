"""라벨 병합 + 일치도(kappa) + 이유 회수율 → 게이트 1 판정 (이슈 #34). 담당: 희수 (hs)

무엇을:
    세 사람이 각자 채운 개인 라벨 파일을 레코드 단위로 합치고(가이드 §7.3), 쌍별 Cohen's
    kappa(§8.4)와 이유 회수율(§15)을 내서 **게이트 1** 판정에 쓸 숫자를 만든다.

이 스크립트가 라벨을 만들지 않는 지점:
    2인이 같은 라벨을 붙인 건만 `AGREED` 로 확정한다. 불일치는 `final` 을 비워 두고 목록으로
    뽑아 준다 — 확정은 두 사람이 근거를 먼저 말하고 나서 하는 토론이다 (§8.3). 기계가
    다수결로 고르거나 한쪽을 택하지 않는다.

왜 기존 병합 파일의 `final` 을 이어받나:
    `final` 에는 사람이 토론해서 정한 결과와 "왜 그렇게 정했는지" 한 줄이 들어 있다 (§8.3 3번).
    라벨을 더 채우고 다시 병합할 때 그걸 덮어쓰면 토론을 다시 해야 한다. 그래서 재병합은
    새 `labels[]` 로 갱신하되 기존 `final` 은 그대로 옮긴다.

실행:
    python -m classify.labels --labels-dir datasets/labels
    python -m classify.labels --labels-dir datasets/labels --no-write     # 리포트만

    `--records` 를 주면 그 파일의 `repo` 로 저장소별 회수율까지 낸다 (기본: 라벨 폴더의
    `pre200_records.jsonl`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classify.sampling import (
    BATCH,
    GUIDE_VERSION,
    LABEL_FILENAME_TEMPLATE,
    LABELERS,
    RECORDS_FILENAME,
)

# 병합·확정 파일 (가이드 §7.1). 경로·파일명은 팀 확정 전이라 sampling 과 같이 상수로 둔다 (§11-5).
MERGED_FILENAME = "labeled_500.jsonl"

REASON_LABELS = ("BUG", "PERF", "SEC", "LIB", "DEAD", "DESIGN", "FEAT", "UNK")
EVIDENCE_GRADES = ("EXPLICIT", "INFERRED", "UNKNOWN")
RECOVERED_GRADES = frozenset({"EXPLICIT", "INFERRED"})  # §15 이유 회수율의 분자

# 라벨러가 무언가에 끌려 라벨했다고 스스로 표시한 건. 독립 라벨이 아니므로 일치도에서 뺀다
# (#7 코멘트 5번). 일치해도 "둘이 같은 것을 봤다"는 뜻이라 kappa 를 부풀린다.
ANCHORED_TAG = "anchored"

# UNKNOWN 의 원인 태그 4종 (가이드 v2 §6.3.2). 분포가 게이트 1 대응 방향을 가른다 —
# 맥락이 안 붙어서 낮으면 저장소 재선정, 맥락은 붙는데 이유가 없어서 낮으면 축 이동.
# `tools/label_cli.py` 가 이 목록을 그대로 강제한다 (#88). 철자·개수는 가이드와 함께만 바꾼다.
UNKNOWN_CAUSE_TAGS = ("no-context", "vague-message", "no-replacement", "no-caller-info")
# 원인 태그 자리를 대신할 수 있는 유일한 태그 (가이드 v2 §6.3.1 3번, §6.3.2 "최소 1개 필수").
# 원인 태그 4종에는 들어가지 않는다 — 필터 정밀도(§10.1)를 재는 관찰 태그다.
FILTER_MISS_TAG = "filter-miss"

# 게이트 1 (§9 2주차, §11) / kappa 해석 (§8.4)
GATE1_PASS = 0.60
GATE1_TIGHTEN = 0.40
KAPPA_TARGET = 0.70
KAPPA_WARN = 0.60


# --------------------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_personal_labels(labels_dir: Path, batch: str = BATCH) -> dict[str, list[dict[str, Any]]]:
    """`{labeler}_{batch}.jsonl` 을 사람별로 읽는다. 없는 파일은 빈 목록."""
    return {
        labeler: read_jsonl(labels_dir / LABEL_FILENAME_TEMPLATE.format(labeler=labeler))
        for labeler in LABELERS
    }


def is_filled(row: dict[str, Any]) -> bool:
    """사람이 실제로 라벨한 줄인가. 빈 틀(#33이 만든 상태)은 제외한다."""
    return bool(row.get("reason_label")) and bool(row.get("evidence_grade"))


def has_tag(row: dict[str, Any], tag: str) -> bool:
    """`note` 는 "태그 + 자유 서술" 형식이라 부분 문자열로 본다 (가이드 §7.2)."""
    return tag in (row.get("note") or "")


def find_label_problems(personal: dict[str, list[dict[str, Any]]]) -> list[str]:
    """§4.2 ③ 8종·근거 3등급에 없는 값을 찾는다. 비어 있으면 문제 없음.

    왜 경고가 아니라 중단인가:
        `cohens_kappa` 는 `p_o` 를 모든 쌍으로, `p_e` 를 **선언된 클래스로만** 계산한다.
        정의되지 않은 값이 섞이면 그 값은 `p_e` 에 기여하지 못해 `p_e` 가 실제보다 낮게
        잡히고, kappa 가 부풀려진다. 10건 중 4건이 오타인 경우로 재 보니 0.091 이 나와야 할
        자리에 0.268 이 나왔다. §10.2 목표(≥0.7)와 §8.4 대응 분기(0.7 / 0.6)를 잘못된 쪽으로
        넘길 수 있는 크기다. 경고만 띄우고 집계하면 그 숫자가 그대로 리포트에 실린다.

    라벨 파일은 사람이 텍스트 에디터로 채우는 JSONL 이라(가이드 §8.2) 오타·대소문자·공백이
    충분히 난다. 게이트 1 판정에 쓰는 값이므로 틀린 값을 내느니 멈추는 편이 낫다.
    """
    problems: list[str] = []
    for labeler in LABELERS:
        for line_number, row in enumerate(personal.get(labeler, []), start=1):
            if not is_filled(row):
                continue
            checks = (
                ("reason_label", row.get("reason_label"), REASON_LABELS),
                ("evidence_grade", row.get("evidence_grade"), EVIDENCE_GRADES),
            )
            for field_name, value, allowed in checks:
                if value not in allowed:
                    problems.append(
                        f"{labeler} {line_number}번째 줄: {field_name}={value!r} 은 "
                        f"허용값이 아니다 ({'|'.join(allowed)})"
                    )
    return problems


# --------------------------------------------------------------------------------------
# 병합 (가이드 §7.3)
# --------------------------------------------------------------------------------------


def agreement_of(labels: Sequence[dict[str, Any]]) -> tuple[bool, bool]:
    """(이유가 같은가, 근거 등급이 같은가). 라벨이 2건 미만이면 둘 다 False."""
    if len(labels) < 2:
        return False, False
    reasons = {row.get("reason_label") for row in labels}
    grades = {row.get("evidence_grade") for row in labels}
    return len(reasons) == 1, len(grades) == 1


def build_final(labels: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """2인이 이유·근거 등급 모두 일치할 때만 자동 확정 (`AGREED`).

    하나라도 다르면 None 을 돌려 사람의 토론(§8.3)에 넘긴다. `evidence_text` 는 같은 라벨
    이어도 사람마다 인용 범위가 다르므로 비교하지 않고, 첫 라벨러 것을 옮기며 그 사실을
    `note` 에 남긴다.
    """
    same_reason, same_grade = agreement_of(labels)
    if not (same_reason and same_grade):
        return None

    source = labels[0]
    others = ", ".join(row.get("labeler", "") for row in labels[1:])
    return {
        "reason_label": source.get("reason_label"),
        "evidence_grade": source.get("evidence_grade"),
        "evidence_text": source.get("evidence_text"),
        "evidence_source": source.get("evidence_source"),
        "evidence_locator": source.get("evidence_locator"),
        "confidence": source.get("confidence"),
        "method": "AGREED",
        "adjudicated_by": [row.get("labeler", "") for row in labels],
        "adjudicated_at": None,
        "note": f"2인 일치로 자동 확정. evidence_* 는 {source.get('labeler')} 기록 (vs {others})",
    }


def merge_labels(
    personal: dict[str, list[dict[str, Any]]],
    *,
    batch: str = BATCH,
    existing: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """개인 라벨 → 레코드 1건 = 1줄 (가이드 §7.3).

    개별 라벨은 `labels[]` 에 그대로 보존한다. 고치면 kappa 를 다시 계산할 수 없다 (§8.3 5번).
    `existing` 에 이전 병합 결과를 주면 사람이 채운 `final` 을 이어받는다.
    """
    kept_finals = {
        row.get("record_id"): row.get("final")
        for row in existing
        if row.get("final") and (row["final"].get("method") or "") != "AGREED"
    }

    order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for labeler in LABELERS:
        for row in personal.get(labeler, []):
            if not is_filled(row):
                continue
            record_id = row.get("record_id")
            if record_id not in grouped:
                grouped[record_id] = []
                order.append(record_id)
            grouped[record_id].append(row)

    merged: list[dict[str, Any]] = []
    for record_id in order:
        labels = grouped[record_id]
        merged.append(
            {
                "record_id": record_id,
                "batch": batch,
                # 예비 200건은 학습/검증/테스트 분할이 없다 (가이드 §7.3)
                "split": None,
                "labels": labels,
                "final": kept_finals.get(record_id) or build_final(labels),
                "guide_version": GUIDE_VERSION,
            }
        )
    return merged


def find_disagreements(merged: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    """이유 불일치와 근거 등급 불일치를 **따로** 뽑는다 (가이드 §8.3 1번).

    등급만 갈리는 건은 §6 의 문제이고, 이유가 갈리는 건은 §5 의 문제라 대응이 다르다.
    """
    reason_gap: list[str] = []
    grade_gap: list[str] = []
    for row in merged:
        same_reason, same_grade = agreement_of(row["labels"])
        if len(row["labels"]) < 2:
            continue
        if not same_reason:
            reason_gap.append(row["record_id"])
        if not same_grade:
            grade_gap.append(row["record_id"])
    return {"reason_label": reason_gap, "evidence_grade": grade_gap}


# --------------------------------------------------------------------------------------
# 일치도 (가이드 §8.4)
# --------------------------------------------------------------------------------------


@dataclass
class Agreement:
    """라벨러 쌍 하나의 일치도. kappa 만으로 읽지 않는다 (§8.4)."""

    pair: tuple[str, str]
    field_name: str
    total: int = 0
    agreed: int = 0
    kappa: float | None = None
    observed: float = 0.0
    expected: float = 0.0
    per_class: dict[str, tuple[int, int]] = field(default_factory=dict)
    """클래스 → (일치, 전체). 한 클래스가 과반이면 kappa 가 낮게 나오므로 같이 본다."""
    confusions: list[tuple[str, str, int]] = field(default_factory=list)
    """혼동 쌍 상위. 가이드 개정의 재료는 kappa 숫자가 아니라 이 목록이다."""

    @property
    def verdict(self) -> str:
        if self.kappa is None:
            return "정의 불가"
        if self.kappa >= KAPPA_TARGET:
            return "목표 충족"
        if self.kappa >= KAPPA_WARN:
            return "경고"
        return "라벨 품질 리스크"


def cohens_kappa(
    pairs: Sequence[tuple[str, str]], classes: Sequence[str]
) -> tuple[float | None, float, float]:
    """(kappa, p_o, p_e). 가이드 §8.4 공식 그대로.

        κ = (p_o − p_e) / (1 − p_e),  p_e = Σ_i P_A(i) × P_B(i)

    p_e 가 1이면 나눗셈이 정의되지 않는다 — 두 사람이 한 클래스만 썼다는 뜻이다. 그때는
    kappa 를 None 으로 두고 p_o 를 읽게 한다. 0이나 1로 채우면 없는 정보를 지어내는 것이다.
    """
    total = len(pairs)
    if total == 0:
        return None, 0.0, 0.0

    observed = sum(1 for left, right in pairs if left == right) / total
    expected = 0.0
    for cls in classes:
        share_a = sum(1 for left, _ in pairs if left == cls) / total
        share_b = sum(1 for _, right in pairs if right == cls) / total
        expected += share_a * share_b

    if expected >= 1.0:
        return None, observed, expected
    return (observed - expected) / (1 - expected), observed, expected


def pair_labels(
    merged: Sequence[dict[str, Any]], field_name: str
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """라벨러 쌍별로 (A의 값, B의 값) 목록을 모은다.

    `anchored` 가 붙은 건은 뺀다. 라벨러가 스스로 "끌렸다"고 표시한 건이라 독립 라벨이
    아니고, 일치해도 일치도의 근거가 되지 못한다 (#7 코멘트 5번).
    """
    buckets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in merged:
        labels = [label for label in row["labels"] if not has_tag(label, ANCHORED_TAG)]
        if len(labels) != 2:
            continue
        first, second = sorted(labels, key=lambda label: label.get("labeler", ""))
        pair = (first.get("labeler", ""), second.get("labeler", ""))
        buckets.setdefault(pair, []).append(
            (str(first.get(field_name)), str(second.get(field_name)))
        )
    return buckets


def agreement_for(
    merged: Sequence[dict[str, Any]], field_name: str, classes: Sequence[str]
) -> list[Agreement]:
    """쌍별 일치도. 보고는 쌍별 + 단순 평균 (가이드 §8.4)."""
    results: list[Agreement] = []
    for pair, values in sorted(pair_labels(merged, field_name).items()):
        kappa, observed, expected = cohens_kappa(values, classes)
        result = Agreement(
            pair=pair,
            field_name=field_name,
            total=len(values),
            agreed=sum(1 for left, right in values if left == right),
            kappa=kappa,
            observed=observed,
            expected=expected,
        )

        for cls in classes:
            appeared = [(left, right) for left, right in values if cls in (left, right)]
            if appeared:
                agreed = sum(1 for left, right in appeared if left == right)
                result.per_class[cls] = (agreed, len(appeared))

        confusion: dict[tuple[str, str], int] = {}
        for left, right in values:
            if left != right:
                key = (left, right) if left < right else (right, left)
                confusion[key] = confusion.get(key, 0) + 1
        result.confusions = [
            (left, right, count)
            for (left, right), count in sorted(confusion.items(), key=lambda x: -x[1])[:3]
        ]
        results.append(result)
    return results


def mean_kappa(results: Sequence[Agreement]) -> float | None:
    values = [result.kappa for result in results if result.kappa is not None]
    return sum(values) / len(values) if values else None


# --------------------------------------------------------------------------------------
# 이유 회수율 (§15) + 게이트 1
# --------------------------------------------------------------------------------------


def resolved_grade(row: dict[str, Any]) -> str | None:
    """이 레코드의 근거 등급. 확정본이 있으면 그것, 없으면 2인이 일치할 때만.

    불일치 건은 아직 등급이 정해지지 않은 것이라 None 이다. 한쪽을 골라 세면 회수율이
    토론 결과보다 먼저 정해져 버린다.
    """
    final = row.get("final")
    if final and final.get("evidence_grade"):
        return final["evidence_grade"]
    _, same_grade = agreement_of(row["labels"])
    return row["labels"][0].get("evidence_grade") if same_grade else None


@dataclass
class Recovery:
    """이유 회수율 한 덩어리 (전체 또는 저장소 하나)."""

    name: str
    resolved: int = 0
    recovered: int = 0
    unresolved: int = 0
    by_grade: dict[str, int] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return self.recovered / self.resolved if self.resolved else 0.0

    @property
    def gate1(self) -> str:
        if not self.resolved:
            return "판정 불가 (확정된 건 없음)"
        if self.rate >= GATE1_PASS:
            return "통과 — 진행"
        if self.rate >= GATE1_TIGHTEN:
            return "경계 — 저장소 기준 강화"
        return "미달 — 대체 코드 중심으로 축 이동"


def recovery_rate(
    merged: Sequence[dict[str, Any]], repo_of: dict[str, str] | None = None
) -> tuple[Recovery, list[Recovery]]:
    """(전체, 저장소별). `repo_of` 가 없으면 저장소별은 빈 목록."""
    overall = Recovery("전체")
    per_repo: dict[str, Recovery] = {}

    for row in merged:
        grade = resolved_grade(row)
        buckets = [overall]
        if repo_of:
            repo = repo_of.get(row["record_id"])
            if repo:
                buckets.append(per_repo.setdefault(repo, Recovery(repo)))

        for bucket in buckets:
            if grade is None:
                bucket.unresolved += 1
                continue
            bucket.resolved += 1
            bucket.by_grade[grade] = bucket.by_grade.get(grade, 0) + 1
            if grade in RECOVERED_GRADES:
                bucket.recovered += 1

    ordered = sorted(per_repo.values(), key=lambda item: (-item.rate, item.name))
    return overall, ordered


def unknown_causes(merged: Sequence[dict[str, Any]]) -> dict[str, int]:
    """UNKNOWN 건의 원인 태그 분포 (가이드 §6.3).

    "회수율이 낮다"와 "왜 낮다"는 대응이 다르다. 맥락이 안 붙어서면 저장소 재선정,
    맥락은 붙는데 이유가 안 적혀 있어서면 대체 코드 중심으로 축 이동이다 (§11).
    """
    counts = {tag: 0 for tag in UNKNOWN_CAUSE_TAGS}
    counts["(태그 없음)"] = 0
    for row in merged:
        for label in row["labels"]:
            if label.get("evidence_grade") != "UNKNOWN":
                continue
            tags = [tag for tag in UNKNOWN_CAUSE_TAGS if has_tag(label, tag)]
            if tags:
                for tag in tags:
                    counts[tag] += 1
            else:
                counts["(태그 없음)"] += 1
    return counts


# --------------------------------------------------------------------------------------
# 리포트 (docs/evaluation.md 에 옮겨 붙일 형태, §8.7)
# --------------------------------------------------------------------------------------


def format_report(
    merged: Sequence[dict[str, Any]],
    repo_of: dict[str, str] | None = None,
) -> list[str]:
    lines: list[str] = []
    overall, per_repo = recovery_rate(merged, repo_of)
    disagreements = find_disagreements(merged)

    lines.append(f"## 라벨 {len(merged)}건 (batch={BATCH}, guide={GUIDE_VERSION})")
    confirmed = sum(1 for row in merged if row.get("final"))
    lines.append(f"- 라벨 확정 {confirmed}건 / 토론 대상 {len(merged) - confirmed}건 (§8.3)")
    lines.append(
        f"- 이유 불일치 {len(disagreements['reason_label'])}건 / "
        f"근거 등급 불일치 {len(disagreements['evidence_grade'])}건"
    )

    lines.append("")
    lines.append("### 이유 회수율 (§15) — 게이트 1")
    lines.append(
        f"- **{overall.rate:.1%}** "
        f"(EXPLICIT+INFERRED {overall.recovered} / 등급이 정해진 {overall.resolved}건)"
    )
    if overall.resolved > confirmed:
        # 위의 "라벨 확정"보다 분모가 큰 건 착오가 아니다. 이유는 갈렸어도 "명시냐 추론이냐"가
        # 같으면 회수 여부는 이미 정해진다 — 회수율이 보는 건 이유 종류가 아니라 등급이다.
        lines.append(
            f"- 분모가 라벨 확정({confirmed}건)보다 큰 이유: "
            f"이유는 갈렸지만 등급은 같은 {overall.resolved - confirmed}건이 들어간다"
        )
    if overall.unresolved:
        lines.append(f"- 등급까지 갈린 {overall.unresolved}건은 분모에서 뺐다 (토론 후 재계산)")
    grades = ", ".join(f"{grade} {overall.by_grade.get(grade, 0)}" for grade in EVIDENCE_GRADES)
    lines.append(f"- 등급 분포: {grades}")
    lines.append(f"- **판정: {overall.gate1}** (기준 ≥{GATE1_PASS:.0%} / {GATE1_TIGHTEN:.0%}~)")

    if per_repo:
        lines.append("")
        lines.append("| 저장소 | 회수율 | 확정 | 미확정 |")
        lines.append("|---|---|---|---|")
        for item in per_repo:
            lines.append(f"| {item.name} | {item.rate:.1%} | {item.resolved} | {item.unresolved} |")
        lines.append("")
        lines.append("건수가 적은 저장소의 비율은 신뢰구간이 넓다. 비율만 보지 말 것.")

    causes = unknown_causes(merged)
    if sum(causes.values()):
        lines.append("")
        lines.append("### UNKNOWN 원인 (가이드 §6.3)")
        for tag, count in sorted(causes.items(), key=lambda item: -item[1]):
            if count:
                lines.append(f"- {tag}: {count}건")
        lines.append(
            "맥락이 안 붙어서면 저장소 재선정, 맥락은 붙는데 이유가 없어서면 축 이동 (§11)."
        )

    for field_name, classes in (
        ("reason_label", REASON_LABELS),
        ("evidence_grade", EVIDENCE_GRADES),
    ):
        results = agreement_for(merged, field_name, classes)
        if not results:
            continue
        lines.append("")
        lines.append(f"### 일치도 — {field_name} ({len(classes)}클래스)")
        lines.append("| 쌍 | 건수 | kappa | 단순 일치율 p_o | 판정 |")
        lines.append("|---|---|---|---|---|")
        for result in results:
            kappa = "정의 불가" if result.kappa is None else f"{result.kappa:.3f}"
            lines.append(
                f"| {' + '.join(result.pair)} | {result.total} | {kappa} | "
                f"{result.observed:.1%} | {result.verdict} |"
            )
        average = mean_kappa(results)
        lines.append(f"- 단순 평균 kappa: {'—' if average is None else f'{average:.3f}'}")

        confusions = [
            (f"{left} vs {right}", count)
            for result in results
            for left, right, count in result.confusions
        ]
        if confusions:
            top = sorted(confusions, key=lambda item: -item[1])[:3]
            lines.append("- 혼동 쌍 상위: " + ", ".join(f"{name} {count}건" for name, count in top))
            lines.append("  가이드 개정의 재료는 kappa 숫자가 아니라 이 목록이다 (§8.4).")
    return lines


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m classify.labels",
        description="라벨 병합 + kappa + 이유 회수율 → 게이트 1 판정 (#34).",
    )
    parser.add_argument("--labels-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument("--records", type=Path, default=None, help="저장소별 회수율용 레코드 파일")
    parser.add_argument("--out", type=Path, default=None, help=f"기본: datasets/{MERGED_FILENAME}")
    parser.add_argument("--batch", default=BATCH)
    parser.add_argument("--no-write", action="store_true", help="병합 파일을 쓰지 않고 리포트만")
    return parser


def repo_index(records_path: Path | None, labels_dir: Path) -> dict[str, str]:
    """record_id → repo. 저장소별 회수율에 쓴다. 파일이 없으면 빈 사전."""
    path = records_path or (labels_dir / RECORDS_FILENAME)
    return {
        row["record_id"]: row.get("repo", "") for row in read_jsonl(path) if row.get("record_id")
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    personal = load_personal_labels(args.labels_dir, args.batch)
    filled = {
        labeler: [row for row in rows if is_filled(row)] for labeler, rows in personal.items()
    }
    for labeler, rows in personal.items():
        print(f"{labeler}: {len(filled[labeler])}/{len(rows)}건 라벨됨", file=sys.stderr)

    if not any(filled.values()):
        print("라벨된 줄이 없다. 사람이 채운 뒤 다시 돌려라.", file=sys.stderr)
        return 1

    problems = find_label_problems(personal)
    if problems:
        print("정의되지 않은 라벨 값이 있다 (§4.2 ③ 이유 8종 / 근거 3등급):", file=sys.stderr)
        for problem in problems[:10]:
            print(f"  - {problem}", file=sys.stderr)
        if len(problems) > 10:
            print(f"  ... 외 {len(problems) - 10}건", file=sys.stderr)
        print(
            "그대로 세면 p_e 가 낮게 잡혀 kappa 가 실제보다 높게 나온다. 값을 고치고 다시 돌려라.",
            file=sys.stderr,
        )
        return 2

    out_path = args.out or Path("datasets") / MERGED_FILENAME
    existing = read_jsonl(out_path)
    merged = merge_labels(personal, batch=args.batch, existing=existing)
    if existing:
        carried = sum(
            1
            for row in merged
            if row.get("final") and (row["final"].get("method") or "") != "AGREED"
        )
        print(f"기존 병합 파일에서 토론 확정 {carried}건을 이어받았다.", file=sys.stderr)

    if not args.no_write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            for row in merged:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"병합: {out_path} ({len(merged)}건)", file=sys.stderr)

    for line in format_report(merged, repo_index(args.records, args.labels_dir)):
        print(line)

    disagreements = find_disagreements(merged)
    if disagreements["reason_label"] or disagreements["evidence_grade"]:
        print("", file=sys.stderr)
        print("토론이 필요한 건 (§8.3):", file=sys.stderr)
        for field_name, ids in disagreements.items():
            if ids:
                shown = ", ".join(ids[:5])
                more = f" 외 {len(ids) - 5}건" if len(ids) > 5 else ""
                print(f"  {field_name}: {shown}{more}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
