"""사후 필터 NOISE_FORMAT — 포맷·스타일 변환 제거 (Issue #152). 담당: 성제

#89 필터 정밀도 미달(57%) 원인 분석(`docs/reports/filter_precision_analysis.md`, PR #151)의
후보 A1을 그대로 구현한다. 새로 고안하지 않는다:

    커밋 메시지에 포맷 키워드가 있고, 삭제 줄이 같은 파일 추가 헝크에 90% 이상 다시
    나타나면 NOISE_FORMAT.

    재등장 비율 — 같은 파일 추가 헝크(`added_hunks_same_file`)를 이어 붙여 정규화한 문자열에,
    삭제 줄 하나하나를 정규화해 부분 문자열로 있는 줄의 비율. 정규화는 공백 전부 제거 →
    따옴표 통일(' → ") → 닫는 괄호 앞 쉼표 제거다. 삭제 줄은 여기에 줄 끝 쉼표도 지운다
    (black이 매직 쉼표로 펼친 인자를 한 줄로 접는 경우). 공백뿐인 삭제 줄은 세지 않는다.

    키워드 — 커밋 메시지 **첫 줄**에서 대소문자 무시, 낱말 단위("blacklist"는 아니다):
    black, ruff, yapf, isort, reformat…, formatting, style, lint…, re-wrap/rewrap, pep8,
    flake8.

    분석 문서는 키워드를 "black, ruff, yapf, isort, reformat, formatting, style, lint, re-wrap
    등"으로만 적었다. 정규화·키워드의 세부(첫 줄만, `lint…`·`pep ?8`·`flake8`, 줄 끝 쉼표)는
    분석 때 쓴 스크립트에서 그대로 옮겼다 — 문서 숫자(A1 11건·A4 16건, fp-009 80%·fp-160
    85%·fp-188 92%)가 이 정의로 재현된다. 첫 줄만 보는 이유: 본문까지 보면 기능 커밋 본문의
    "lint"·"style"이 걸린다. #89 표본 KEPT 100건에서 첫 줄은 다수결 "아니오" 16·"예" 0건,
    본문 포함은 "아니오" 18·"예" 4건이다.

왜 사후 단계인가: 규칙이 쓰는 필드(`commit_message`, `deleted_body`,
`added_hunks_same_file`)가 조립 결과에 이미 있다. 추출·맥락 결합을 다시 돌리지 않는다.

대상은 `filter_status == "KEPT"` 행뿐이다. 이미 NOISE_*인 행은 판정·근거를 건드리지 않는다.
모든 행의 `filter_rule_version`을 `FILTER_RULE_VERSION`(v0.8)으로 올린다 — v0.8은 "v0.7
추출 + 이 사후 필터"다. 입력은 v0.7(직전 버전) 또는 v0.8(다시 돌리기) 행만 받는다.

순서: 맥락 결합(`pipeline.context --attach`) **뒤**의 `records.jsonl`에 적용한다.
`filtered.jsonl`에 먼저 적용하면 NOISE_FORMAT이 된 FULL_FUNCTION 행을 가리키는 맥락을
붙이기 단계가 거부한다(KEPT·FULL_FUNCTION만 대상).

입력은 10GB가 넘으므로 줄 단위로 읽고 쓴다. 임시 파일에 다 쓴 뒤 `os.replace`로 확정한다 —
거부되면 최종 경로의 이전 파일은 그대로다. 입력 파일은 덮어쓰지 않는다.

사용:
    python -m pipeline.postfilter --input data/assembled/records.jsonl \\
        --out data/assembled/records_v0.8.jsonl

출력: `--out` JSONL과 그 옆 `<out stem>_postfilter.json` 보고서(저장소별·deletion_kind별
NOISE_FORMAT 건수).

기준: CHARTER.md §4.2 ②·§4.4, docs/filter_rules.md "NOISE_FORMAT"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pipeline.context import added_hunk_text
from pipeline.filter import FILTER_RULE_VERSION

KEPT = "KEPT"
NOISE_FORMAT = "NOISE_FORMAT"  # CHARTER §4.4 enum 값. 새 이름을 만들지 않는다.

# v0.8에서 바뀐 것은 이 사후 필터뿐이다. 그 앞 버전의 조립 결과만 받는다.
PREVIOUS_RULE_VERSION = "v0.7"
ACCEPTED_INPUT_VERSIONS = frozenset({PREVIOUS_RULE_VERSION, FILTER_RULE_VERSION})

# 분석 문서 후보 A1: 재등장 ≥ 90%.
REAPPEAR_THRESHOLD = 0.9

FORMAT_KEYWORDS = re.compile(
    r"\b(black|ruff|yapf|isort|reformat\w*|formatting|style|lint\w*|re-?wrap|pep ?8|flake8)\b",
    re.IGNORECASE,
)

_WHITESPACE = re.compile(r"\s+")
_COMMA_BEFORE_CLOSE = re.compile(r",([)\]}])")
_TRAILING_COMMA = re.compile(r",$")

REPORT_SUFFIX = "_postfilter.json"


class PostFilterError(ValueError):
    """사후 필터 거부. 이 예외가 나면 최종 출력은 만들어지지 않는다."""


def normalize(text: str) -> str:
    """공백 제거 → 따옴표 통일(' → ") → 닫는 괄호 앞 쉼표 제거."""
    text = _WHITESPACE.sub("", text).replace("'", '"')
    return _COMMA_BEFORE_CLOSE.sub(r"\1", text)


def format_keywords(message: str) -> list[str]:
    """커밋 메시지 첫 줄의 포맷 키워드(소문자, 처음 나온 순서, 중복 없음)."""
    subject = message.split("\n")[0]
    found = (match.group(1).lower() for match in FORMAT_KEYWORDS.finditer(subject))
    return list(dict.fromkeys(found))


def reappear_counts(deleted_body: str, added_text: str) -> tuple[int, int]:
    """(재등장한 삭제 줄 수, 공백뿐이 아닌 삭제 줄 수)."""
    haystack = normalize(added_text)
    lines = [
        _TRAILING_COMMA.sub("", normalize(line))
        for line in deleted_body.splitlines()
        if line.strip()
    ]
    return sum(1 for line in lines if line and line in haystack), len(lines)


def format_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    """NOISE_FORMAT이면 `filter_evidence`, 아니면 `None`. 행의 상태는 보지 않는다."""
    keywords = format_keywords(str(row.get("commit_message") or ""))
    if not keywords:
        return None
    added_text = added_hunk_text(row)
    if added_text is None:  # 추가 줄 필드가 없으면 판정할 수 없다 — 그대로 둔다
        return None
    matched, total = reappear_counts(str(row.get("deleted_body") or ""), added_text)
    if total == 0:
        return None
    ratio = matched / total
    if ratio < REAPPEAR_THRESHOLD:
        return None
    return {
        "keywords": keywords,
        "reappear_ratio": round(ratio, 4),
        "reappeared_lines": matched,
        "deleted_lines": total,
    }


def apply_row(row: dict[str, Any]) -> dict[str, Any]:
    """행 하나에 규칙을 적용한 새 행. KEPT만 판정하고, 버전은 모든 행에서 올린다."""
    out = {**row, "filter_rule_version": FILTER_RULE_VERSION}
    if row.get("filter_status") == KEPT:
        evidence = format_evidence(row)
        if evidence is not None:
            out["filter_status"] = NOISE_FORMAT
            out["filter_evidence"] = evidence
    return out


def report_path_for(out_path: str | Path) -> Path:
    """`records_v0.8.jsonl` → `records_v0.8_postfilter.json`."""
    path = Path(out_path)
    return path.with_name(path.stem + REPORT_SUFFIX)


def apply_file(in_path: str | Path, out_path: str | Path) -> dict[str, Any]:
    """`in_path`를 줄 단위로 읽어 규칙을 적용하고 `out_path`에 쓴다. 보고서를 돌려준다."""
    src, out = Path(in_path), Path(out_path)
    if out.resolve() == src.resolve():
        raise PostFilterError(f"{out}: 입력과 같은 경로다 — 원본은 덮어쓰지 않는다")
    status_before: Counter[str] = Counter()
    status_after: Counter[str] = Counter()
    input_versions: Counter[str] = Counter()
    changed: dict[str, Counter[str]] = {}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        with src.open(encoding="utf-8") as reader, tmp.open("w", encoding="utf-8") as writer:
            for line_no, line in enumerate(reader, start=1):
                try:
                    row = json.loads(line)
                except ValueError as error:
                    raise PostFilterError(f"{src}:{line_no}: JSON이 아니다: {error}") from error
                if not isinstance(row, dict):
                    raise PostFilterError(f"{src}:{line_no}: JSON 객체가 아니다")
                version = row.get("filter_rule_version")
                if version not in ACCEPTED_INPUT_VERSIONS:
                    raise PostFilterError(
                        f"{src}:{line_no}: filter_rule_version {version!r} — "
                        f"{sorted(ACCEPTED_INPUT_VERSIONS)}만 받는다"
                    )
                result = apply_row(row)
                status_before[str(row.get("filter_status"))] += 1
                status_after[result["filter_status"]] += 1
                input_versions[version] += 1
                if result["filter_status"] != row.get("filter_status"):
                    repo = str(row.get("repo"))
                    changed.setdefault(repo, Counter())[str(row.get("deletion_kind"))] += 1
                writer.write(json.dumps(result, ensure_ascii=False) + "\n")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)

    by_kind: Counter[str] = Counter()
    for counts in changed.values():
        by_kind.update(counts)
    report = {
        "input_path": str(src),
        "out_path": str(out),
        "filter_rule_version": FILTER_RULE_VERSION,
        "input_versions": dict(sorted(input_versions.items())),
        "record_count": status_before.total(),
        "filter_status_before": dict(sorted(status_before.items())),
        "filter_status_after": dict(sorted(status_after.items())),
        "noise_format_count": by_kind.total(),
        "noise_format_by_kind": dict(sorted(by_kind.items())),
        "noise_format_by_repo": {
            repo: dict(sorted(counts.items())) for repo, counts in sorted(changed.items())
        },
    }
    report_path = report_path_for(out)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자."""
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.postfilter",
        description="조립 결과에 사후 필터 NOISE_FORMAT을 적용한다 (Issue #152).",
    )
    parser.add_argument(
        "--input", type=Path, required=True, help="조립·맥락 결합 결과 JSONL (records.jsonl)"
    )
    parser.add_argument("--out", type=Path, required=True, help="결과 JSONL. 입력과 달라야 한다")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """적용했으면 0, 거부했으면 1."""
    args = build_parser().parse_args(argv)
    try:
        report = apply_file(args.input, args.out)
    except PostFilterError as error:
        print(f"사후 필터 거부: {error}", file=sys.stderr)
        return 1
    print(
        f"사후 필터 완료: {args.out} — 레코드 {report['record_count']}건, "
        f"NOISE_FORMAT {report['noise_format_count']}건 {report['noise_format_by_kind']}, "
        f"filter_rule_version {FILTER_RULE_VERSION}",
        file=sys.stderr,
    )
    print(f"보고서: {report_path_for(args.out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
