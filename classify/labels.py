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
    python -m classify.labels                          # 본 라벨링 500건 병합 + 리포트
    python -m classify.labels --interim                # 블록당 처음 50건 중간 점검 (§8.4.1)
    python -m classify.labels --interim v3             # v3 점검 - 블록별 51~100번 (§8.4.1.1)
    python -m classify.labels --batch pre200 --no-write   # 예비 200건 리포트만

    파일은 묶음(`--batch`)에서 정해진다 (`BATCH_FILES`). 본 라벨링은 배분 파일
    (`main500_assignment.jsonl`, #85)에서 `split` 을 읽어 병합 행에 넣는다 (#158).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classify import sampling_main500
from classify.sampling import (
    BATCH,
    GUIDE_VERSION,
    LABEL_FILENAME_TEMPLATE,
    LABELERS,
    RECORDS_FILENAME,
)

# 병합·확정 파일 (가이드 §7.1). 경로·파일명은 팀 확정 전이라 sampling 과 같이 상수로 둔다 (§11-5).
MERGED_FILENAME = "labeled_500.jsonl"

# 묶음 → (레코드 파일, 개인 라벨 파일 틀, 배분 파일). `tools/label_cli.py` 의 `BATCH_FILES` 와 같은
# 묶음이고 여기에 배분 파일이 더 있다. 예비 200건은 분할이 없어 배분 파일도 없다 (#158).
BATCH_FILES: dict[str, tuple[str, str, str | None]] = {
    sampling_main500.BATCH: (
        sampling_main500.RECORDS_OUT,
        sampling_main500.LABEL_OUT_TEMPLATE,
        sampling_main500.ASSIGNMENT_OUT,
    ),
    BATCH: (RECORDS_FILENAME, LABEL_FILENAME_TEMPLATE, None),
}
# CLI 기본값. `label_cli` 와 같이 본 라벨링이다. 함수 기본값은 예비 200건 그대로 둔다 -
# `eval/gate1.py` 가 그 기본값으로 게이트 1 을 재현한다.
DEFAULT_BATCH = sampling_main500.BATCH

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
    """JSONL 을 읽는다. 파일이 없으면 빈 목록, 빈 줄은 건너뛴다.

    **`str.splitlines()` 로 나누지 않는다.** `json.dumps(ensure_ascii=False)` 는 U+2028 같은
    문자를 이스케이프하지 않고 쓰는데, `splitlines()` 는 그것을 줄바꿈으로 봐서 레코드 하나를
    둘로 자른다. langchain 커밋 메시지에 실제로 있어 맥락 결합이 멈췄다 (#146). 파일을 줄 단위로
    순회하면 `\\n`(과 JSON 이 이스케이프하는 `\\r`)에서만 나뉜다.
    """
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_personal_labels(labels_dir: Path, batch: str = BATCH) -> dict[str, list[dict[str, Any]]]:
    """`{labeler}_{batch}.jsonl` 을 사람별로 읽는다. 없는 파일은 빈 목록.

    파일 이름 틀은 `batch` 에서 정한다. 전에는 `batch` 를 받고도 예비 200건 틀을 썼다 -
    `--batch main500` 으로 돌려도 `*_pre200.jsonl` 을 읽었다 (#158).
    """
    template = BATCH_FILES[batch][1]
    return {
        labeler: read_jsonl(labels_dir / template.format(labeler=labeler)) for labeler in LABELERS
    }


def load_assignment(labels_dir: Path, batch: str) -> dict[str, dict[str, Any]]:
    """record_id → 배분 행 (블록·split·`interim`, #85). 배분 파일이 없는 묶음이면 빈 사전."""
    name = BATCH_FILES[batch][2]
    if name is None:
        return {}
    return {row["record_id"]: row for row in read_jsonl(labels_dir / name)}


def is_filled(row: dict[str, Any]) -> bool:
    """사람이 실제로 라벨한 줄인가. 빈 틀(#33이 만든 상태)은 제외한다."""
    return bool(row.get("reason_label")) and bool(row.get("evidence_grade"))


def has_tag(row: dict[str, Any], tag: str) -> bool:
    """`note` 는 "태그 + 자유 서술" 형식이라 부분 문자열로 본다 (가이드 §7.2)."""
    return tag in (row.get("note") or "")


def has_note_tag(note: str | None, tag: str) -> bool:
    """`note` 에 `tag` 가 **낱말로** 들어 있나.

    `note` 는 "태그 + 자유 서술"(가이드 §7.2)이라 태그를 따로 떼어 낼 구분자가 없다. 그렇다고
    부분 문자열로 보면(위 `has_tag`) `no-contexts` 같은 오타도 태그로 친다. 그래서
    앞뒤가 영문·숫자·`-`·`_` 가 아닐 때만 태그로 본다. 한국어 조사가 붙은 `no-context로` 는
    태그로 친다 — 영문 경계만 보기 때문이다.
    """
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(tag)}(?![A-Za-z0-9_-])"
    return re.search(pattern, note or "") is not None


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


def check_assignment(
    personal: Mapping[str, Sequence[dict[str, Any]]],
    batch: str,
    assignment: Mapping[str, Mapping[str, Any]] | None,
) -> None:
    """배분과 맞지 않는 라벨이면 `ValueError` (#158). 확인을 병합 함수 안에 두는 이유는 CLI 를
    거치지 않고 `merge_labels` 를 바로 부르는 쪽(#86)도 같은 보호를 받게 하려는 것이다.

    - 배분 파일이 있는 묶음(main500)인데 배분 정보가 없다 - `split` 을 `null` 로 지어내게 된다
    - 배분에 없는 레코드에 라벨이 있다 - train/val/test 어디에도 속하지 않아 #86 이 조용히
      빼거나 잘못 넣는다
    - 그 블록을 맡지 않은 사람의 라벨이다 - 엉뚱한 쌍으로 자동 확정되고 블록 kappa 가 다른
      쌍으로 계산된다. 파일 주인과 줄의 `labeler` 를 둘 다 본다
    """
    if assignment is None:
        if BATCH_FILES.get(batch, (None, None, None))[2] is not None:
            raise ValueError(f"{batch} 병합에는 배분 정보가 필요하다 - split 을 지어내지 않는다")
        return
    stray: set[str] = set()
    unassigned: set[tuple[str, str]] = set()
    for owner, rows in personal.items():
        for row in rows:
            if not is_filled(row):
                continue
            record_id = str(row.get("record_id"))
            assigned = assignment.get(record_id)
            if assigned is None:
                stray.add(record_id)
                continue
            labelers = assigned.get("labelers")
            for who in {owner, str(row.get("labeler"))}:
                if labelers is not None and who not in labelers:
                    unassigned.add((record_id, who))
    problems = []
    if stray:
        problems.append(
            f"배분 파일에 없는 레코드에 라벨이 있다: {len(stray)}건 {sorted(stray)[:3]}"
        )
    if unassigned:
        shown = sorted(unassigned)[:3]
        problems.append(f"그 블록을 맡지 않은 사람의 라벨이 있다: {len(unassigned)}건 {shown}")
    if problems:
        raise ValueError(" / ".join(problems))


def merge_labels(
    personal: dict[str, list[dict[str, Any]]],
    *,
    batch: str = BATCH,
    existing: Sequence[dict[str, Any]] = (),
    assignment: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """개인 라벨 → 레코드 1건 = 1줄 (가이드 §7.3).

    개별 라벨은 `labels[]` 에 그대로 보존한다. 고치면 kappa 를 다시 계산할 수 없다 (§8.3 5번).
    `existing` 에 이전 병합 결과를 주면 사람이 채운 `final` 을 이어받는다.

    `assignment`(배분 파일, #85)를 주면 `split` 을 거기서 넣는다. 배분과 맞지 않는 라벨은
    `check_assignment` 가 거부한다.
    """
    check_assignment(personal, batch, assignment)
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
                "split": assignment[record_id]["split"] if assignment is not None else None,
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

    UNKNOWN 라벨 1건은 셋 중 한 곳에만 들어간다:
        - 원인 태그 4종 중 하나라도 있으면 → 그 태그들 (복수면 각각)
        - 원인 태그 없이 `filter-miss` 만 있으면 → `filter-miss` 칸. §6.3.2 예외 조항으로
          유효한 라벨이지만 "무엇이 없어서 못 했나"를 물을 대상이 아니므로 원인 분포에 넣지
          않는다 (#134). 원인 태그와 함께 달린 `filter-miss` 는 원인 태그로만 센다 — §6.3.2가
          `filter-miss` 는 "예외 조항에서만 원인 태그의 자리를 대신한다"고 했기 때문이다.
        - 둘 다 없으면 → `(태그 없음)`. 진짜 태그 누락만 남는다.
    """
    # label_cli 저장 검증(#88)과 같은 낱말 매칭(`has_note_tag`)을 쓴다 — `filter-miss 의심` 이
    # 저장을 통과했다면 여기서도 filter-miss 로 세져야 한다 (가이드 §6.3.3).
    counts = {tag: 0 for tag in UNKNOWN_CAUSE_TAGS}
    counts["(태그 없음)"] = 0
    counts[FILTER_MISS_TAG] = 0
    for row in merged:
        for label in row["labels"]:
            if label.get("evidence_grade") != "UNKNOWN":
                continue
            tags = [tag for tag in UNKNOWN_CAUSE_TAGS if has_tag(label, tag)]
            if tags:
                for tag in tags:
                    counts[tag] += 1
            elif has_note_tag(label.get("note"), FILTER_MISS_TAG):
                counts[FILTER_MISS_TAG] += 1
            else:
                counts["(태그 없음)"] += 1
    return counts


# --------------------------------------------------------------------------------------
# 리포트 (docs/evaluation.md 에 옮겨 붙일 형태, §8.7)
# --------------------------------------------------------------------------------------


def format_report(
    merged: Sequence[dict[str, Any]],
    repo_of: dict[str, str] | None = None,
    batch: str = BATCH,
) -> list[str]:
    """병합 결과 리포트 - 확정·불일치 건수, 이유 회수율, UNKNOWN 원인, 쌍별 일치도."""
    lines: list[str] = []
    overall, per_repo = recovery_rate(merged, repo_of)
    disagreements = find_disagreements(merged)

    lines.append(f"## 라벨 {len(merged)}건 (batch={batch}, guide={GUIDE_VERSION})")
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
        filter_miss = causes.pop(FILTER_MISS_TAG)
        for tag, count in sorted(causes.items(), key=lambda item: -item[1]):
            if count:
                lines.append(f"- {tag}: {count}건")
        lines.append(
            "맥락이 안 붙어서면 저장소 재선정, 맥락은 붙는데 이유가 없어서면 축 이동 (§11)."
        )
        if filter_miss:
            lines.append("")
            lines.append(
                f"- {FILTER_MISS_TAG} (원인 태그 없이): {filter_miss}건 — "
                "원인 분포에서 제외(§6.3.2). 태그 누락이 아니다"
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


# 가이드 §8.4.1 중간 점검 기준 - reason·grade kappa 둘 다 이 값 이상이면 통과.
INTERIM_KAPPA = KAPPA_WARN


def _kappa_text(result: Agreement | None) -> str:
    """kappa 와 p_o 를 한 칸에. p_o 를 같이 보지 않으면 한 클래스 과반일 때 잘못 읽는다 (§8.4)."""
    if result is None:
        return "-"
    kappa = "정의 불가" if result.kappa is None else f"{result.kappa:.3f}"
    return f"{kappa} (p_o {result.observed:.0%})"


def format_interim_report(
    merged: Sequence[dict[str, Any]], assignment: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """블록당 처음 50건 중간 점검 (가이드 §8.4.1). 블록(= 라벨러 쌍)마다 따로 판정한다.

    배분 파일의 `interim` 레코드만 센다. 두 사람이 51번째 건부터 더 라벨했어도 여기 들어오지
    않는다 - 그냥 "둘 다 라벨한 건" 을 세면 점검 표본이 사람마다 진행 속도에 따라 달라진다.
    `anchored` 라벨은 `pair_labels` 처럼 빼고, 그래서 kappa 분모가 50보다 작을 수 있어 함께 적는다.
    50건을 두 사람이 다 끝내기 전에는 판정하지 않는다.
    """
    lines = [f"## 중간 점검 - 블록당 처음 50건 (가이드 §8.4.1, 기준 kappa ≥ {INTERIM_KAPPA})"]
    by_id = {row["record_id"]: row for row in merged}
    for block in sorted({row["block"] for row in assignment.values()}):
        ids = [rid for rid, row in assignment.items() if row["block"] == block and row["interim"]]
        labelers = next(row["labelers"] for row in assignment.values() if row["block"] == block)
        rows = [by_id[rid] for rid in ids if rid in by_id]
        done = sum(1 for row in rows if len(row["labels"]) >= 2)
        reason = next(iter(agreement_for(rows, "reason_label", REASON_LABELS)), None)
        grade = next(iter(agreement_for(rows, "evidence_grade", EVIDENCE_GRADES)), None)
        counted = reason.total if reason else 0

        if done < len(ids):
            verdict = "진행 중 - 아직 판정하지 않는다"
        elif reason is None or grade is None or reason.kappa is None or grade.kappa is None:
            verdict = "판단 보류 - kappa 정의 불가, p_o 와 혼동 쌍으로 본다"
        elif reason.kappa >= INTERIM_KAPPA and grade.kappa >= INTERIM_KAPPA:
            verdict = "통과"
        else:
            # v3 는 이 50건을 재라벨하지 않는다 - 갈린 건만 재판정한다 (가이드 §8.4.1 v3 머리말)
            verdict = "미달 - 재라벨하지 않는다. 갈린 건만 v3 재판정 (§8.4.1.1)"

        lines.append("")
        lines.append(f"### 블록 {block} ({' + '.join(labelers)}) - {verdict}")
        lines.append(
            f"- 2인 완료 {done}/{len(ids)}건, kappa 분모 {counted}건"
            + (f" (anchored 제외 {done - counted}건)" if done > counted else "")
        )
        # 재판정 라벨(v3)이 들어오면 이 수치는 2026-10-08 v2 점검 기록(가이드 §6.4.1 표)이 아니다.
        rejudged = sum(
            1
            for row in rows
            if any(label.get("guide_version") == INTERIM_V3_VERSION for label in row["labels"])
        )
        if rejudged:
            lines.append(
                f"- {INTERIM_V3_VERSION} 재판정 라벨이 든 레코드 {rejudged}건 - v2 점검 기록과 "
                "다른 수치다 (v2 원본: datasets/labels/snapshots/v2_interim/)"
            )
        lines.append(f"- reason_label kappa {_kappa_text(reason)}")
        lines.append(f"- evidence_grade kappa {_kappa_text(grade)}")
        lines.extend(_confusion_lines(reason, grade))
    return lines


def _confusion_lines(reason: Agreement | None, grade: Agreement | None) -> list[str]:
    lines = []
    for name, result in (("reason_label", reason), ("evidence_grade", grade)):
        if result and result.confusions:
            pairs = ", ".join(f"{a} vs {b} {n}건" for a, b, n in result.confusions)
            lines.append(f"- {name} 혼동 쌍 상위: {pairs}")
    return lines


# 가이드 v3 §8.4.1.1 4번 / `docs/evaluation.md` "500건 라벨링 v3 사전 등록" 2번 - v3 점검은
# 블록별 51~100번(`block_position`), 두 라벨이 모두 이 버전인 쌍만.
INTERIM_V3_POSITIONS = range(51, 101)
INTERIM_V3_VERSION = "v3"


def format_interim_v3_report(
    merged: Sequence[dict[str, Any]], assignment: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """v3 점검 - 블록별 51~100번, 두 라벨이 모두 v3 인 쌍만 (가이드 §8.4.1.1 4번).

    **결과는 보고만 한다.** 0.6 미만이어도 가이드를 고치지 않고 블록 정지·재라벨도 없다
    (v3 사전 등록 3번). 계산은 `format_interim_report` 와 같다(§8.4).

    둘 다 라벨했지만 kappa 쌍에 넣지 않는 레코드는 까닭별로 센다 - 분모와 함께 적지 않으면
    블록끼리 수치를 비교할 수 없다 (§8.4.1):
        혼재      두 라벨의 `guide_version` 이 다르다 (§8.4.2.1). jh 블록 A 51번(v2)이 그렇다
        v3 아님   두 라벨이 같은 버전이지만 v3 가 아니다
        anchored  `anchored` 를 빼면 독립 라벨이 2개가 안 된다 (§8.4)
    """
    first, last = INTERIM_V3_POSITIONS[0], INTERIM_V3_POSITIONS[-1]
    lines = [
        f"## v3 점검 - 블록별 {first}~{last}번, 두 라벨이 모두 {INTERIM_V3_VERSION} 인 쌍만 "
        f"(가이드 §8.4.1.1, 기준 kappa ≥ {INTERIM_KAPPA}, 보고만 한다)"
    ]
    by_id = {row["record_id"]: row for row in merged}
    for block in sorted({row["block"] for row in assignment.values()}):
        ids = [
            rid
            for rid, row in assignment.items()
            if row["block"] == block and row.get("block_position") in INTERIM_V3_POSITIONS
        ]
        labelers = next(row["labelers"] for row in assignment.values() if row["block"] == block)
        rows = [by_id[rid] for rid in ids if rid in by_id]
        done = [row for row in rows if len(row["labels"]) >= 2]
        paired: list[dict[str, Any]] = []
        skipped = {"혼재": 0, f"{INTERIM_V3_VERSION} 아님": 0, "anchored": 0}
        for row in done:
            independent = [label for label in row["labels"] if not has_tag(label, ANCHORED_TAG)]
            versions = {label.get("guide_version") for label in independent}
            if len(independent) != 2:
                skipped["anchored"] += 1
            elif len(versions) > 1:
                skipped["혼재"] += 1
            elif versions != {INTERIM_V3_VERSION}:
                skipped[f"{INTERIM_V3_VERSION} 아님"] += 1
            else:
                paired.append(row)
        reason = next(iter(agreement_for(paired, "reason_label", REASON_LABELS)), None)
        grade = next(iter(agreement_for(paired, "evidence_grade", EVIDENCE_GRADES)), None)

        if len(done) < len(ids):
            verdict = "진행 중 - 아직 판정하지 않는다"
        elif reason is None or grade is None or reason.kappa is None or grade.kappa is None:
            verdict = "판단 보류 - kappa 정의 불가, p_o 와 혼동 쌍으로 본다"
        elif reason.kappa >= INTERIM_KAPPA and grade.kappa >= INTERIM_KAPPA:
            verdict = "통과 - 보고만 한다"
        else:
            verdict = "미달 - 보고만 한다. 가이드·라벨을 고치지 않고 끝까지 진행 (v3 사전 등록 3번)"

        lines.append("")
        lines.append(f"### 블록 {block} ({' + '.join(labelers)}) - {verdict}")
        excluded = ", ".join(f"{name} {count}건" for name, count in skipped.items() if count)
        lines.append(
            f"- 2인 완료 {len(done)}/{len(ids)}건, kappa 분모 {len(paired)}쌍"
            + (f" (제외: {excluded})" if excluded else "")
        )
        lines.append(f"- reason_label kappa {_kappa_text(reason)}")
        lines.append(f"- evidence_grade kappa {_kappa_text(grade)}")
        lines.extend(_confusion_lines(reason, grade))
    return lines


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자. 실행 방법은 모듈 독스트링 "실행" 절."""
    parser = argparse.ArgumentParser(
        prog="python -m classify.labels",
        description="라벨 병합 + kappa + 이유 회수율 (#34) / 500건 중간 점검 (#158).",
    )
    parser.add_argument("--labels-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument(
        "--records", type=Path, default=None, help="저장소별 회수율용 레코드 파일 (기본: 묶음의 것)"
    )
    parser.add_argument("--out", type=Path, default=None, help=f"기본: datasets/{MERGED_FILENAME}")
    parser.add_argument(
        "--batch",
        choices=tuple(BATCH_FILES),
        default=DEFAULT_BATCH,
        help=f"기본 {DEFAULT_BATCH} (본 라벨링). {BATCH} 는 예비 200건",
    )
    parser.add_argument("--no-write", action="store_true", help="병합 파일을 쓰지 않고 리포트만")
    parser.add_argument(
        "--interim",
        nargs="?",
        const="v2",
        choices=("v2", "v3"),
        default=None,
        help="중간 점검만. 값 없이 = v2, 블록당 처음 50건 (§8.4.1) / v3 = 블록별 51~100번, "
        "v3 쌍만 (§8.4.1.1). 병합 파일은 쓰지 않는다",
    )
    return parser


def repo_index(records_path: Path | None, labels_dir: Path, batch: str = BATCH) -> dict[str, str]:
    """record_id → repo. 저장소별 회수율에 쓴다. 파일이 없으면 빈 사전."""
    path = records_path or (labels_dir / BATCH_FILES[batch][0])
    return {
        row["record_id"]: row.get("repo", "") for row in read_jsonl(path) if row.get("record_id")
    }


def main(argv: Sequence[str] | None = None) -> int:
    """병합하고 리포트를 낸다. 라벨이 없으면 1, 거부하면 2, 정상이면 0."""
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

    assignment: dict[str, dict[str, Any]] | None = None
    if BATCH_FILES[args.batch][2] is not None:
        assignment = load_assignment(args.labels_dir, args.batch)
        if not assignment:
            print(
                f"배분 파일이 없다: {args.labels_dir / BATCH_FILES[args.batch][2]} "
                "(#85 가 만든다). split·중간 점검에 필요하다.",
                file=sys.stderr,
            )
            return 2
    elif args.interim:
        print(f"{args.batch} 에는 배분 파일이 없어 중간 점검을 할 수 없다.", file=sys.stderr)
        return 2

    out_path = args.out or Path("datasets") / MERGED_FILENAME
    # 중간 점검은 토론 확정(`final`)을 쓰지 않아 기존 병합 파일을 읽지 않는다.
    existing = [] if args.interim else read_jsonl(out_path)
    # 기본 출력 경로는 묶음과 상관없이 같다. 다른 묶음의 병합 파일이면 거기 쌓인 토론 확정을
    # 덮어써 잃는다 - 쓰기 전에 멈춘다 (#158).
    other_batches = sorted({str(row.get("batch")) for row in existing} - {args.batch})
    if other_batches:
        print(
            f"{out_path} 는 다른 묶음({', '.join(other_batches)})의 병합 파일이다. 덮어쓰면 그 "
            "토론 확정 결과가 사라진다. --out 으로 다른 경로를 줘라.",
            file=sys.stderr,
        )
        return 2
    try:
        merged = merge_labels(personal, batch=args.batch, existing=existing, assignment=assignment)
    except ValueError as error:
        print(f"{error} - 다른 묶음의 라벨이 섞였나 확인해라.", file=sys.stderr)
        return 2

    if args.interim:
        assert assignment is not None  # 위에서 배분 파일 없는 묶음은 걸렀다
        report = format_interim_v3_report if args.interim == "v3" else format_interim_report
        for line in report(merged, assignment):
            print(line)
        return 0
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

    repo_of = repo_index(args.records, args.labels_dir, args.batch)
    for line in format_report(merged, repo_of, args.batch):
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
