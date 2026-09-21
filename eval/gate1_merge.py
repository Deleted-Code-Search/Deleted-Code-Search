"""게이트 1 예비 200건 사전 확정 규칙 병합 CLI (이슈 #76). 담당: 재헌 (jh)

무엇을:
    개인 라벨 3개(`datasets/labels/{sj,jh,hs}_pre200.jsonl`)를 `record_id` 기준으로 짝지어
    `evidence_grade`를 팀이 라벨링 전에 확정한 규칙으로 자동 병합하고, `eval/gate1.py`가 그대로
    읽는 병합 파일(가이드 §7.3 스키마)을 만든다.

이 파일과 #34(`classify/labels.py`)의 관계:
    grouping과 개인 라벨 검증은 #34·#37의 함수를 그대로 쓴다(`classify.labels.merge_labels`가
    record_id로 묶는 부분, `eval.gate1.check_label`이 라벨 1줄의 값을 검증하는 부분). #34의
    `build_final`은 §8.3 토론 확정 절차(이유·등급이 **모두** 일치할 때만 `AGREED`, 그 외엔 사람이
    토론)를 구현한다 — 이건 500건 본 라벨링용이다.

    예비 200건 게이트 1은 §8.3 전에 팀이 아래 규칙을 실제 데이터를 보기 전에 확정했다(이슈 #76
    본문). 그래서 이 파일은 grouping은 그대로 재사용하고 `final` 계산만 새 규칙으로 대체한다.
    §8.3 토론 확정은 이번에 적용하지 않는다 — 500건 본 라벨링에서 쓴다.

병합 규칙 (팀 확정, 데이터 확인 전 — 실제 불일치를 보고 바꾸지 않는다):
    evidence_grade — 두 라벨 중 더 약한 등급을 취한다 (EXPLICIT > INFERRED > UNKNOWN 순으로
        강하다고 보고, 더 약한 쪽을 최종으로 삼는다).
            EXPLICIT + EXPLICIT → EXPLICIT
            INFERRED + INFERRED → INFERRED
            EXPLICIT + INFERRED → INFERRED
            어느 한쪽이라도 UNKNOWN → UNKNOWN
    confidence — 두 값 중 작은 값. INFERRED+INFERRED 는 PR #38 이 정한 기존 규칙 그대로다.
        EXPLICIT은 가이드 §6.1이 1.0으로 고정하고 UNKNOWN은 §6.3이 0.0으로 고정하므로, 같은
        min 규칙을 등급 조합 전체에 그대로 적용해도 각 등급의 고정값과 어긋나지 않는다.
    reason_label — 두 라벨러가 같을 때만 `final.reason_label`에 기록하고, 다르면 null 이다
        (팀장 확정). 예비 200건은 500건 본 라벨링에 다시 포함되므로, 갈린 값 하나를 final 에
        넣으면 확정 라벨로 오해될 수 있다. 어느 쪽 등급이 더 약한지는 reason_label 에 영향을 주지
        않는다. reason_label 이 같고 등급만 다르면 reason_label 은 합의된 것이라 기록하고, 등급을
        정한(더 약한) 쪽의 evidence_text/source/locator 를 남긴다. reason_label 이 다르면
        evidence_text/source/locator 도 null 이다(근거와 reason 의 연결이 끊기므로). 원본 양쪽 값은
        `labels[]`에 그대로 있다.
        일치도는 원본 `labels` 쌍으로 kappa 를 따로 잰다(가이드 §8.4, `eval/gate1.py`의
        `agreement_for`). §8.3 토론을 통한 불일치 확정은 이번 게이트 1에는 적용하지 않는다.

산출물:
    병합 파일 — 가이드 §7.3 스키마 그대로 (`record_id`/`batch`/`split`/`labels`/`final`/
    `guide_version`). `eval/gate1.py`가 개인 라벨과 똑같이 다룬다 — `labels` 키가 있는 줄은
    병합 파일로 읽는다.

실행:
    python -m eval.gate1_merge --labels-dir datasets/labels
    python -m eval.gate1 datasets/labels/gate1_pre200_merged.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from classify.labels import LABELERS, REASON_LABELS, is_filled, merge_labels
from classify.sampling import BATCH, LABEL_FILENAME_TEMPLATE
from eval.gate1 import check_label

# 병합 1건에 실제로 들어와야 하는 라벨러 수 (가이드 §8.1 "전건 2인 독립"). 그 외는 누락·중복이다.
EXPECTED_LABELERS_PER_RECORD = 2

# 등급 서열 — 더 약한(낮은) 쪽이 병합 등급이 된다 (이슈 #76 팀 확정 규칙). 숫자가 클수록 강하다.
_GRADE_RANK: dict[str, int] = {"UNKNOWN": 0, "INFERRED": 1, "EXPLICIT": 2}

# final.method 에 남기는 값. "AGREED"·"DISCUSSED"·"THIRD_PARTY"(가이드 §7.3)와 구분되는 4번째
# 값이다 — 사람의 토론이 아니라 사전 확정된 규칙으로 자동 병합했다는 뜻.
GATE1_MERGE_METHOD = "GATE1_RULE"

MERGED_FILENAME = "gate1_pre200_merged.jsonl"


# --------------------------------------------------------------------------------------
# 입력 — 개인 라벨 3개를 읽고 검증한다
# --------------------------------------------------------------------------------------


def _check_confidence_is_a_number(row: dict[str, Any], where: str) -> list[str]:
    """등급과 무관하게 confidence 가 0~1 숫자인지 본다.

    `eval.gate1.check_label`은 INFERRED 일 때만 confidence 를 본다(가이드 §6.2가 INFERRED 에만
    구간을 두기 때문). 이 병합 규칙은 등급이 무엇이든 두 값의 min 을 confidence 로 쓰므로, 여기서는
    EXPLICIT·UNKNOWN 을 포함해 전부 숫자인지 확인한다(가이드 §7.2 "confidence: float 0~1").
    """
    value = row.get("confidence")
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
        return [
            f"{where}: confidence={value!r} — 0~1 숫자가 필수다 "
            "(병합 규칙이 두 라벨러 값의 min 을 쓴다)"
        ]
    return []


def find_pairing_problems(personal: dict[str, list[dict[str, Any]]]) -> list[str]:
    """`record_id` 마다 정확히 2인이 라벨했는지 본다.

    #33 배분(가이드 §8.1)은 전건 2인 독립이다. 1인만 있으면 그 건은 병합할 수 없고(누락),
    같은 사람이 같은 `record_id`를 두 번 라벨했으면(중복) grouping 이 "2인 쌍"을 엉뚱한
    것(같은 사람의 라벨 2개)으로 본다 — 조용히 넘기면 게이트 1 숫자가 틀린 짝으로 계산된다.
    """
    problems: list[str] = []
    labelers_by_record: dict[str, list[str]] = {}
    for labeler in LABELERS:
        for row in personal.get(labeler, []):
            if not is_filled(row):
                continue
            record_id = str(row.get("record_id") or "")
            labelers_by_record.setdefault(record_id, []).append(labeler)

    for record_id, labelers in sorted(labelers_by_record.items()):
        if len(set(labelers)) != len(labelers):
            duplicated = sorted({labeler for labeler in labelers if labelers.count(labeler) > 1})
            problems.append(
                f"record_id {record_id}: {'/'.join(duplicated)} 가 같은 레코드를 "
                "자기 파일 안에서 두 번 라벨했다 (중복)"
            )
        elif len(labelers) != EXPECTED_LABELERS_PER_RECORD:
            problems.append(
                f"record_id {record_id}: 라벨러 {len(labelers)}명({'/'.join(sorted(labelers))}) "
                f"— 가이드 §8.1 은 전건 {EXPECTED_LABELERS_PER_RECORD}인이다 (누락 또는 배분 오류)"
            )
    return problems


def load_personal(
    labels_dir: Path, batch: str = BATCH
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """개인 라벨 3개(`{labeler}_{batch}.jsonl`)를 줄 단위로 읽고 검증한다.

    JSON 이 아니거나 객체가 아닌 줄은 문제로 남기고 건너뛴다(`eval.gate1.load_inputs`와 같은
    스타일). 값 검증(라벨러·`record_id`·이유 8종·근거 3등급·UNK/UNKNOWN 짝·INFERRED 신뢰도)은
    `eval.gate1.check_label` 그대로 쓴다 — 검증 로직을 두 곳에 두면 갈릴 수 있다.
    """
    personal: dict[str, list[dict[str, Any]]] = {labeler: [] for labeler in LABELERS}
    problems: list[str] = []

    for labeler in LABELERS:
        path = labels_dir / LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        if not path.is_file():
            problems.append(f"{path}: 파일이 없다")
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path}:{line_number}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                problems.append(f"{where}: JSON 이 아니다 ({error.msg})")
                continue
            if not isinstance(row, dict):
                problems.append(f"{where}: 한 줄은 JSON 객체여야 한다 ({type(row).__name__})")
                continue
            if not is_filled(row):
                continue  # #33 빈 틀. 아직 사람이 안 채웠다
            problems.extend(check_label(row, where))
            problems.extend(_check_confidence_is_a_number(row, where))
            personal[labeler].append(row)

    problems.extend(find_pairing_problems(personal))
    return personal, problems


# --------------------------------------------------------------------------------------
# 병합 — 이슈 #76 사전 확정 규칙
# --------------------------------------------------------------------------------------


def agreed_reason_label(pair: Sequence[dict[str, Any]]) -> str | None:
    """두 라벨러의 `reason_label`이 같으면 그 값, 다르면 None.

    `evidence_grade`가 어느 쪽이 더 약한지는 여기에 영향을 주지 않는다.
    """
    first, second = pair[0]["reason_label"], pair[1]["reason_label"]
    return first if first == second else None


def merge_evidence_grade(pair: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """2인 라벨 → `final` 1건. 등급은 더 약한 쪽, confidence 는 두 값의 min.

    `final.reason_label`은 두 라벨러가 일치할 때만 채우고, 다르면 null 이다(등급과 무관).
    reason 이 일치하면 evidence_text/source/locator 는 등급을 정한(더 약한) 쪽 것을 남긴다 —
    합의된 reason 의 근거이기 때문이다. 등급이 같아 동률이면 라벨러 이름 알파벳순으로 가른다
    (매 실행마다 같은 결과). reason 이 다르면 한쪽 근거를 고르지 않고 evidence 필드를 모두 null 로
    둔다 — final.reason_label 이 null 인데 근거만 남으면 무엇의 근거인지 끊기기 때문이다. 원본
    양쪽 reason·evidence 는 `labels[]`에 그대로 있다.
    """
    weaker, _stronger = sorted(
        pair, key=lambda row: (_GRADE_RANK[row["evidence_grade"]], row["labeler"])
    )
    grade = weaker["evidence_grade"]
    confidence = min(row["confidence"] for row in pair)
    agreed_reason = agreed_reason_label(pair)

    if agreed_reason is not None:
        reason_note = "reason_label 은 두 라벨러가 일치해 그대로 기록했다."
        evidence = {
            "evidence_text": weaker["evidence_text"],
            "evidence_source": weaker["evidence_source"],
            "evidence_locator": weaker["evidence_locator"],
        }
    else:
        reason_note = (
            "reason_label 은 두 라벨러가 달라 reason_label 과 evidence_text/source/locator 를 "
            "null 로 남겼다 — 확정하지 않는다 (원본 근거는 labels[] 참고, 500건 본 라벨링에서 "
            "사람이 정한다). 일치도는 kappa 로 따로 측정한다 (가이드 §8.4)."
        )
        evidence = {"evidence_text": None, "evidence_source": None, "evidence_locator": None}

    return {
        "reason_label": agreed_reason,
        "evidence_grade": grade,
        **evidence,
        "confidence": confidence,
        "method": GATE1_MERGE_METHOD,
        "adjudicated_by": [row["labeler"] for row in pair],
        "adjudicated_at": None,
        "note": (
            "게이트 1 예비 200건 — 사전 확정 규칙으로 자동 병합 (이슈 #76, §8.3 토론 미적용). "
            f"evidence_grade = 두 라벨 중 더 약한 등급({grade}), confidence = 두 값의 min. "
            + reason_note
        ),
    }


def merge_pre200(
    personal: dict[str, list[dict[str, Any]]], *, batch: str = BATCH
) -> list[dict[str, Any]]:
    """개인 라벨 3개 → 레코드 1건 = 1줄 (가이드 §7.3 스키마).

    `record_id`로 묶는 부분은 #34 `classify.labels.merge_labels`를 그대로 쓴다 — 3개 파일의
    같은 줄 번호가 아니라 `record_id`로 짝짓는다. `merge_labels`가 계산하는 `final`(§8.3 토론
    확정, 이유·등급 모두 일치할 때만 `AGREED`)은 이 게이트 1 규칙과 다르므로 버리고
    `merge_evidence_grade`로 다시 계산한다. 2인이 아닌 건(배분 오류)은 `find_pairing_problems`
    가 이미 막았으므로 여기 도달하지 않는다.
    """
    merged = merge_labels(personal, batch=batch)
    for row in merged:
        pair = row["labels"]
        row["final"] = (
            merge_evidence_grade(pair) if len(pair) == EXPECTED_LABELERS_PER_RECORD else None
        )
    return merged


def count_grade_mismatches(merged: Sequence[dict[str, Any]]) -> int:
    """두 라벨러의 `evidence_grade`가 서로 달랐던 레코드 수.

    가이드 §6 근거 등급 정의(EXPLICIT/INFERRED/UNKNOWN 경계)가 애매한지 보는 게이트 1 결과의
    일부다(이슈 #76). 회수율·판정에는 쓰지 않는다 — 참고 지표다.
    """
    return sum(
        1
        for row in merged
        if len(row["labels"]) == EXPECTED_LABELERS_PER_RECORD
        and row["labels"][0]["evidence_grade"] != row["labels"][1]["evidence_grade"]
    )


def count_reason_mismatches(merged: Sequence[dict[str, Any]]) -> int:
    """두 라벨러의 `reason_label`이 서로 달랐던 레코드 수 (= `final.reason_label`이 null인 건)."""
    return sum(
        1
        for row in merged
        if len(row["labels"]) == EXPECTED_LABELERS_PER_RECORD
        and agreed_reason_label(row["labels"]) is None
    )


def reason_distribution(merged: Sequence[dict[str, Any]]) -> dict[str, int]:
    """`reason_label`이 합의된 레코드만 센 분포. 불일치 레코드는 어느 이유에도 넣지 않는다."""
    counts: dict[str, int] = {}
    for row in merged:
        reason = (row.get("final") or {}).get("reason_label")
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.gate1_merge",
        description=(
            "게이트 1 예비 200건 개인 라벨 3개를 사전 확정 규칙으로 병합한다 (#76). "
            "출력은 eval/gate1.py 가 그대로 읽는다."
        ),
    )
    parser.add_argument("--labels-dir", type=Path, default=Path("datasets/labels"))
    parser.add_argument("--batch", default=BATCH)
    parser.add_argument(
        "--out", type=Path, default=None, help=f"기본: <labels-dir>/{MERGED_FILENAME}"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")

    personal, problems = load_personal(args.labels_dir, args.batch)
    if problems:
        print("병합하기 전에 고칠 것:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        if len(problems) > 20:
            print(f"  ... 외 {len(problems) - 20}건", file=sys.stderr)
        return 2

    if not any(personal.values()):
        print("라벨된 줄이 없다. 사람이 채운 뒤 다시 돌려라.", file=sys.stderr)
        return 1

    merged = merge_pre200(personal, batch=args.batch)
    mismatches = count_grade_mismatches(merged)

    out_path = args.out or args.labels_dir / MERGED_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in merged:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"병합: {out_path} ({len(merged)}건)")
    print(
        f"두 라벨러의 evidence_grade 가 달랐던 레코드: {mismatches}건 / {len(merged)}건 "
        f"({mismatches / len(merged):.1%})"
    )
    print("가이드 §6 근거 등급 정의가 애매한지 보는 지표다.")

    reason_mismatches = count_reason_mismatches(merged)
    distribution = reason_distribution(merged)
    agreed = sum(distribution.values())
    print(
        f"두 라벨러의 reason_label 이 달랐던 레코드: {reason_mismatches}건 / {len(merged)}건 "
        f"({reason_mismatches / len(merged):.1%}) — final.reason_label 은 null"
    )
    print(f"reason_label 합의 레코드 {agreed}건의 분포 (불일치 건 제외):")
    for reason in REASON_LABELS:
        if reason in distribution:
            print(f"  {reason}: {distribution[reason]}건")
    print("회수율·kappa 는 eval/gate1.py 로 측정한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
