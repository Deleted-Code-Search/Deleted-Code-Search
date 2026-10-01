"""필터 정밀도 집계 — 판정 3개 + 키 → 정밀도·가중 재현율·kappa·판정 (이슈 #89, CHARTER §10.1).
담당: 성제 (sj)

사전 등록: `docs/evaluation.md` "필터 정밀도 사전 등록".
판정에 쓰는 숫자는 아래 상수 한 곳에만 있다.

계산:
    최종 판정    3인 다수결 (예/아니오 2값이라 동률이 없다)
    정밀도       통과 표본 중 최종 "예" 비율
    재현율       표본 비율이 아니라 **층별 모집단 크기로 가중**한다. 표본은 100/50/50 이지만
                 모집단은 층마다 크기가 전혀 다르다 (NOISE_TRIVIAL 이 KEPT 의 2배 넘는다).
                     재현율 = N_k·p_k / (N_k·p_k + N_move·p_move + N_trivial·p_trivial)
                 N = 층별 모집단 크기(key.json), p = 층별 표본의 최종 "예" 비율
    이동 오판율  NOISE_MOVE 층의 최종 "예" 비율 — 이동으로 제외됐는데 사람은 배울 게 있다고 본 것.
                 #80 이 미룬 이동 판단의 근거로 따로 보고한다 (판정 기준은 아니다)
    일치도       3인 Fleiss kappa(판정 기준) + 쌍별 Cohen kappa(보고). Cohen 은
                 `classify.labels.cohens_kappa` 를 그대로 쓴다 — 같은 계산을 두 곳에 두지 않는다

판정 파일이 표본 200건을 모두 덮지 않거나, 판정자가 3명이 아니거나, record_id 가 키와 다르면
숫자를 내지 않고 멈춘다. 일부만 판정한 채 숫자를 내면 그 숫자로 판정하게 된다.

사용:
    python -m eval.filter_precision data/filter_precision/{sj,jh,hs}_judgments.jsonl \\
        --key data/filter_precision/key.json --json-out fp.json --md-out fp.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from classify.labels import cohens_kappa
from eval.filter_precision_sample import EXCLUDED_STRATA, KEPT, NOISE_MOVE, STRATA

# 사전 등록 값 (CHARTER §10.1, docs/evaluation.md). 바꾸려면 그 절을 먼저 고친다.
PRECISION_TARGET = 0.90  # 정밀도 ≥ 90% (경계 포함)
KAPPA_TARGET = 0.70  # Fleiss kappa ≥ 0.7 (경계 포함)
JUDGE_COUNT = 3
YES, NO = "yes", "no"
CLASSES = (YES, NO)
# 보고용 신뢰구간 (판정에 쓰지 않는다)
WILSON_Z = 1.959964


class AggregateError(ValueError):
    """숫자를 낼 수 없다. 메시지에 무엇을 고칠지 있다."""


@dataclass
class FilterPrecisionReport:
    """집계 결과. `passed` 가 사전 등록 기준 판정이다."""

    judges: list[str]
    sample_counts: dict[str, int]
    population: dict[str, int]
    precision: float
    precision_ci95: tuple[float, float]
    stratum_yes_rate: dict[str, float]
    noise_move_error_rate: float
    noise_move_error_ci95: tuple[float, float]
    weighted_recall: float | None
    fleiss_kappa: float | None
    pairwise_kappa: dict[str, float | None]
    pairwise_agreement: dict[str, float]
    unanimous: int
    confusion: dict[str, dict[str, int]]
    yes_by_judge: dict[str, int]
    precision_target: float
    kappa_target: float
    passed: bool
    verdict_lines: list[str]

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """`"\\n"` 으로만 나눈다 (#146)."""
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise AggregateError(f"{path}:{line_no}: JSON 이 아니다 ({error.msg})") from None
        if not isinstance(row, dict):
            raise AggregateError(f"{path}:{line_no}: JSON 객체가 아니다")
        rows.append(row)
    return rows


def collect_votes(
    judgment_files: Mapping[Path, list[dict[str, Any]]], items: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, bool]]:
    """judge → sample_id → 예(True)/아니오(False). 계약 위반이면 `AggregateError`."""
    expected = {item["sample_id"]: item["record_id"] for item in items}
    votes: dict[str, dict[str, bool]] = {}
    for path, rows in judgment_files.items():
        judges = {row.get("judge") for row in rows}
        if len(judges) != 1:
            raise AggregateError(f"{path}: 판정자가 한 명이어야 한다 ({sorted(map(str, judges))})")
        judge = str(judges.pop())
        if judge in votes:
            raise AggregateError(f"{path}: 판정자 {judge} 의 파일이 두 개다")
        mine: dict[str, bool] = {}
        for row in rows:
            sample_id = row.get("sample_id")
            if sample_id not in expected:
                raise AggregateError(f"{path}: sample_id {sample_id!r} 가 키에 없다")
            if row.get("record_id") != expected[sample_id]:
                raise AggregateError(f"{path}: {sample_id} 의 record_id 가 키와 다르다")
            if sample_id in mine:
                raise AggregateError(f"{path}: {sample_id} 가 중복된다")
            if not isinstance(row.get("meaningful"), bool):
                raise AggregateError(f"{path}: {sample_id} 의 meaningful 이 true/false 가 아니다")
            mine[sample_id] = row["meaningful"]
        missing = sorted(set(expected) - set(mine))
        if missing:
            raise AggregateError(
                f"{path}: {judge} 가 {len(missing)}건을 판정하지 않았다 (예: {missing[:3]})"
            )
        votes[judge] = mine
    if len(votes) != JUDGE_COUNT:
        raise AggregateError(f"판정자가 {len(votes)}명이다 — {JUDGE_COUNT}명이어야 한다")
    return votes


def fleiss_kappa(counts: Sequence[Sequence[int]]) -> float | None:
    """Fleiss kappa. `counts[i][j]` = i번째 건에서 j번째 범주를 고른 판정자 수 (건마다 합이 같다).

        P_i = (Σ_j n_ij² − n) / (n(n − 1)),  P̄ = mean(P_i)
        p_j = Σ_i n_ij / (N·n),              P_e = Σ_j p_j²
        κ = (P̄ − P_e) / (1 − P_e)

    P_e 가 1(모두가 한 범주만 씀)이면 정의되지 않아 None 이다.
    """
    if not counts:
        return None
    raters = sum(counts[0])
    if raters < 2 or any(sum(row) != raters for row in counts):
        raise AggregateError("Fleiss kappa: 건마다 판정자 수가 같고 2명 이상이어야 한다")
    total = len(counts)
    p_bar = sum((sum(n * n for n in row) - raters) / (raters * (raters - 1)) for row in counts)
    p_bar /= total
    shares = [sum(row[j] for row in counts) / (total * raters) for j in range(len(counts[0]))]
    p_e = sum(share * share for share in shares)
    if p_e >= 1.0:
        return None
    return (p_bar - p_e) / (1 - p_e)


def weighted_recall(population: Mapping[str, int], yes_rate: Mapping[str, float]) -> float | None:
    """N_k·p_k / Σ_층 N_s·p_s. 분모가 0(어느 층에도 "예"가 없음)이면 None."""
    kept_part = population[KEPT] * yes_rate[KEPT]
    denominator = sum(population[stratum] * yes_rate[stratum] for stratum in STRATA)
    return kept_part / denominator if denominator else None


def wilson_interval(successes: int, total: int, z: float = WILSON_Z) -> tuple[float, float]:
    """비율의 Wilson 95% 구간. 보고용이다."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def aggregate(
    judgment_files: Mapping[Path, list[dict[str, Any]]], key: Mapping[str, Any]
) -> FilterPrecisionReport:
    """판정과 키로 사전 등록 지표와 판정을 낸다."""
    items = key.get("items") or []
    population = key.get("population") or {}
    for stratum in STRATA:
        if not isinstance(population.get(stratum), int):
            raise AggregateError(f"key.json 의 population.{stratum} 가 없다")
    votes = collect_votes(judgment_files, items)
    judges = sorted(votes)

    majority: dict[str, bool] = {}
    counts: list[list[int]] = []
    unanimous = 0
    for item in items:
        yes = sum(votes[judge][item["sample_id"]] for judge in judges)
        majority[item["sample_id"]] = yes * 2 > len(judges)
        counts.append([yes, len(judges) - yes])
        unanimous += yes in (0, len(judges))

    confusion = {stratum: {YES: 0, NO: 0} for stratum in STRATA}
    for item in items:
        if item.get("stratum") not in confusion:
            raise AggregateError(f"key.json 의 {item['sample_id']} 층 {item.get('stratum')!r}")
        answer = YES if majority[item["sample_id"]] else NO
        confusion[item["stratum"]][answer] += 1
    sample_counts = {stratum: sum(cell.values()) for stratum, cell in confusion.items()}
    if not all(sample_counts.values()):
        raise AggregateError(f"표본에 빈 층이 있다: {sample_counts}")

    yes_rate = {stratum: confusion[stratum][YES] / sample_counts[stratum] for stratum in STRATA}
    precision = yes_rate[KEPT]
    recall = weighted_recall(population, yes_rate)
    fleiss = fleiss_kappa(counts)

    pairwise_kappa: dict[str, float | None] = {}
    pairwise_agreement: dict[str, float] = {}
    for left, right in combinations(judges, 2):
        pairs = [
            (YES if votes[left][sid] else NO, YES if votes[right][sid] else NO)
            for sid in (item["sample_id"] for item in items)
        ]
        kappa, observed, _expected = cohens_kappa(pairs, CLASSES)
        pairwise_kappa[f"{left}+{right}"] = kappa
        pairwise_agreement[f"{left}+{right}"] = observed

    precision_ok = precision >= PRECISION_TARGET
    kappa_ok = fleiss is not None and fleiss >= KAPPA_TARGET
    verdict = [
        f"정밀도 {precision:.1%} (목표 ≥ {PRECISION_TARGET:.0%}) — "
        + ("충족" if precision_ok else "미달"),
        "Fleiss kappa "
        + ("정의 불가" if fleiss is None else f"{fleiss:.3f}")
        + f" (목표 ≥ {KAPPA_TARGET}) — {'충족' if kappa_ok else '미달'}",
    ]
    return FilterPrecisionReport(
        judges=judges,
        sample_counts=sample_counts,
        population={stratum: population[stratum] for stratum in STRATA},
        precision=precision,
        precision_ci95=wilson_interval(confusion[KEPT][YES], sample_counts[KEPT]),
        stratum_yes_rate=yes_rate,
        noise_move_error_rate=yes_rate[NOISE_MOVE],
        noise_move_error_ci95=wilson_interval(
            confusion[NOISE_MOVE][YES], sample_counts[NOISE_MOVE]
        ),
        weighted_recall=recall,
        fleiss_kappa=fleiss,
        pairwise_kappa=pairwise_kappa,
        pairwise_agreement=pairwise_agreement,
        unanimous=unanimous,
        confusion=confusion,
        yes_by_judge={judge: sum(votes[judge].values()) for judge in judges},
        precision_target=PRECISION_TARGET,
        kappa_target=KAPPA_TARGET,
        passed=precision_ok and kappa_ok,
        verdict_lines=verdict,
    )


def _kappa_text(value: float | None) -> str:
    return "정의 불가" if value is None else f"{value:.3f}"


def format_report(report: FilterPrecisionReport, key: Mapping[str, Any]) -> str:
    """사람이 읽을 Markdown."""
    low, high = report.precision_ci95
    move_low, move_high = report.noise_move_error_ci95
    recall = "정의 불가" if report.weighted_recall is None else f"{report.weighted_recall:.1%}"
    lines = [
        "# 필터 정밀도 (#89, CHARTER §10.1)",
        "",
        f"- 시드 {key.get('seed')} · filter_rule_version {key.get('filter_rule_version')} · "
        f"조립 결과 sha256 `{key.get('assembled_sha256')}`",
        f"- 판정자: {', '.join(report.judges)} · 표본 {report.sample_counts} · "
        f"모집단 {report.population}",
        "",
        f"## 판정: {'통과' if report.passed else '미달'}",
        *(f"- {line}" for line in report.verdict_lines),
        "",
        "## 지표",
        "| 지표 | 값 |",
        "|---|---|",
        f"| 정밀도 (통과 중 예) | {report.precision:.1%} (Wilson 95% {low:.1%}–{high:.1%}) |",
        f"| NOISE_MOVE 오판율 (이동으로 제외됐는데 예) | {report.noise_move_error_rate:.1%} "
        f"({report.confusion[NOISE_MOVE][YES]}/{report.sample_counts[NOISE_MOVE]}, "
        f"Wilson 95% {move_low:.1%}–{move_high:.1%}) |",
        *(
            f"| {stratum} 중 예 비율 | {report.stratum_yes_rate[stratum]:.1%} |"
            for stratum in EXCLUDED_STRATA
            if stratum != NOISE_MOVE
        ),
        f"| 재현율 (층별 모집단 가중) | {recall} |",
        f"| Fleiss kappa (3인) | {_kappa_text(report.fleiss_kappa)} |",
        f"| 만장일치 | {report.unanimous}/{sum(report.sample_counts.values())} |",
        "",
        "## 쌍별 일치도 (보고용)",
        "| 쌍 | Cohen kappa | 단순 일치율 |",
        "|---|---|---|",
        *(
            f"| {pair} | {_kappa_text(kappa)} | {report.pairwise_agreement[pair]:.1%} |"
            for pair, kappa in report.pairwise_kappa.items()
        ),
        "",
        "## 혼동표 (다수결)",
        "| 필터 \\ 사람 | 예 | 아니오 |",
        "|---|---:|---:|",
        *(
            f"| {stratum} | {cell[YES]} | {cell[NO]} |"
            for stratum, cell in report.confusion.items()
        ),
        "",
        "판정자별 예: " + ", ".join(f"{j} {n}" for j, n in report.yes_by_judge.items()),
    ]
    if not report.passed:
        lines += [
            "",
            "미달 시 절차(사전 등록): #80 에서 보류한 항목"
            "(이동+리네임 유사도 0.9 미만, 커밋 간 이동)을 20개 저장소 데이터로 재검토하고"
            " 필터를 고친 뒤 다시 잰다.",
        ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.filter_precision",
        description="필터 정밀도 집계 — 정밀도·가중 재현율·kappa·판정 (#89, §10.1).",
    )
    parser.add_argument("judgments", type=Path, nargs="+", help="판정자별 판정 파일 (3개)")
    parser.add_argument(
        "--key", type=Path, required=True, help="eval.filter_precision_sample 의 key.json"
    )
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--md-out", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """통과 0, 미달 3, 숫자를 못 냄 1."""
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    try:
        key = json.loads(args.key.read_text(encoding="utf-8"))
        files = {path: read_jsonl(path) for path in args.judgments}
        report = aggregate(files, key)
    except (AggregateError, OSError, ValueError) as error:
        print(f"집계 중단: {error}", file=sys.stderr)
        return 1
    text = format_report(report, key)
    print(text)
    if args.md_out:
        args.md_out.write_text(text, encoding="utf-8")
    if args.json_out:
        payload = {"key": {k: v for k, v in key.items() if k != "items"}, **report.to_json_dict()}
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if report.passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
