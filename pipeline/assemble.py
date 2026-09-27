"""추출 결과 조립 — KEPT·제외 JSONL 병합, `filter_status` 부여 (Issue #101). 담당: 재헌

`pipeline/run.py`(Issue #81)가 저장소마다 남긴 세 파일을 입력으로 받는다:

    <stem>.jsonl            필터 통과 레코드 (`extract.write_jsonl`, `filter_*` 키 없음)
    <stem>_excluded.jsonl   필터 제외 레코드 (`extract.write_excluded_jsonl`, #97)
    <stem>_run.json         저장소별 실행 기록 (`status`·`filter_rule_version`·건수 등)

실행 디렉터리(`run.py`의 `out_dir`) 여러 개 — 노트북·배치마다 하나 — 를 받아 하나의 JSONL로
합친다. 한 줄은 추출 JSONL의 키 전부에 `filter_status`·`filter_rule_version`을 더한 것이다:

    kept 행      `filter_status = "KEPT"`, `filter_rule_version` = 실행 기록의 값. 다른 키는
                 더하지 않는다 (`filter_evidence`도 넣지 않는다).
    excluded 행  파일에 있는 그대로 (`filter_status`(NOISE_*)·`filter_rule_version`·
                 `filter_evidence` 보존). 행의 `filter_rule_version`이 실행 기록과 같은지만
                 확인한다.

`docs/filter_rules.md` "제외 레코드 보존" 절의 "최종 조립 단계"가 이것이다. 필터 판정은
하지 않는다 — 규칙 버전(`FILTER_RULE_VERSION`)과 무관하고, 지금 코드의 상수와 비교하지도
않는다. 버전은 실행 기록에서 읽는다.

실행 기록이 신뢰의 기준이다. `batch_summary.json`은 보지 않는다 — 같은 `out_dir`에서 배치를
다시 돌리면 덮어써지는 배치 단위 요약이라 저장소별 최종 상태를 대신할 수 없다.

상태별 처리:
    SUCCESS  kept/excluded 파일을 읽어 조립한다.
    FAILED   재시도 뒤에도 실패한 저장소다. 레코드를 넣지 않고 보고서 `failed_repos`에 남긴다.
             옆에 kept/excluded 파일이 있어도 **읽지 않는다** — `run.py`는 재실행이 실패해도
             이전 실행의 파일을 그대로 두므로(버전이 달라도), 그 파일은 이 기록의 결과가 아니다.
    그 밖    RUNNING(끊긴 실행)·정의되지 않은 값·`status` 없음은 조립을 거부한다.

거부(`AssemblyError`, 최종 출력을 만들지 않는다):
    - 실행 디렉터리가 없거나 실행 기록이 하나도 없음
    - 실행 기록을 읽을 수 없음(JSON 아님, 객체 아님, `repo` 없음)
    - 실행 기록 없는 JSONL — 어느 실행 기록의 kept/excluded 경로에도 해당하지 않는 `*.jsonl`
    - 같은 `repo`의 실행 기록이 두 번 — 상태와 무관하게. 어느 쪽도 고르지 않는다
    - SUCCESS 기록의 `filter_rule_version`이 없음·null·빈 문자열·공백
    - SUCCESS 기록끼리 `filter_rule_version`이 다름 (FAILED 기록의 버전은 보지 않는다)
    - SUCCESS가 하나도 없음 — 조립 결과의 `filter_rule_version`을 정할 수 없다
    - SUCCESS 저장소의 kept/excluded 파일이 없음, JSON 객체가 아닌 줄, 행 수가 기록의
      `kept_count`·`excluded_count`와 다름(`run.is_completed`와 같은 기준)
    - excluded 행의 `filter_status`가 CHARTER §4.4의 NOISE_* 값이 아님, 또는
      `filter_rule_version`이 실행 기록과 다름
    - 출력 경로가 입력 실행 디렉터리 안 — 다음 조립 때 실행 기록 없는 JSONL로 잡히고,
      입력 파일을 덮어쓸 수도 있다

출력: `--out` 경로의 JSONL과, 그 옆 `<out stem>_assembly.json` 보고서(`AssemblyReport`).
행 순서는 저장소 이름 오름차순, 저장소 안에서는 kept 파일 순서 다음 excluded 파일 순서다.
둘 다 임시 파일에 다 쓴 뒤 `os.replace`로 확정한다 — 거부되면 최종 경로의 이전 파일은 그대로다.

범위 밖: 맥락 결합·대체 코드(`context.py`), 이유 분류, 선정 CSV 대비 누락 저장소 확인.

사용:
    python -m pipeline.assemble --run-dir data/run_a --run-dir data/run_b \\
        --out data/assembled/filtered.jsonl
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.filter import NOISE_MOVE, NOISE_TRIVIAL
from pipeline.run import RUN_SUFFIX, STATUS_FAILED, STATUS_SUCCESS, output_paths

KEPT = "KEPT"
# CHARTER §4.4 `filter_status` enum 중 제외 사유. excluded JSONL에는 이 값만 온다 (#97).
NOISE_STATUSES = frozenset(
    {
        NOISE_MOVE,
        "NOISE_RENAME",
        "NOISE_FORMAT",
        "NOISE_BULK",
        "NOISE_GENERATED",
        NOISE_TRIVIAL,
    }
)
REPORT_SUFFIX = "_assembly.json"


class AssemblyError(ValueError):
    """조립 거부. 이 예외가 나면 최종 출력은 만들어지지 않는다."""


@dataclass(frozen=True)
class RunRecord:
    """실행 디렉터리에서 찾은 저장소 실행 기록 하나 (`<stem>_run.json`)."""

    run_dir: Path
    path: Path
    repo: str
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AssemblyReport:
    """조립 결과 요약. `failed_repos`는 실패로 제외한 저장소다."""

    out_path: str
    run_dirs: list[str]
    filter_rule_version: str
    record_count: int
    filter_status_counts: dict[str, int]
    repos: list[dict[str, Any]]
    failed_repos: list[dict[str, Any]]

    def to_json_dict(self) -> dict[str, Any]:
        """보고서 파일에 쓸 dict."""
        return dataclasses.asdict(self)


def report_path_for(out_path: str | Path) -> Path:
    """보고서 경로. `filtered.jsonl` → `filtered_assembly.json`."""
    path = Path(out_path)
    return path.with_name(path.stem + REPORT_SUFFIX)


def _load_run_record(run_dir: Path, path: Path) -> RunRecord:
    """실행 기록 하나를 읽는다. 조립할 수 없는 기록이면 `AssemblyError`."""
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssemblyError(f"{path}: 실행 기록을 읽을 수 없다: {error}") from error
    if not isinstance(metadata, dict):
        raise AssemblyError(f"{path}: 실행 기록이 JSON 객체가 아니다")
    repo = metadata.get("repo")
    if not isinstance(repo, str) or not repo:
        raise AssemblyError(f"{path}: 실행 기록에 repo가 없다")
    status = metadata.get("status")
    if status not in (STATUS_SUCCESS, STATUS_FAILED):
        raise AssemblyError(
            f"{path}: {repo}의 status가 {status!r}다 — SUCCESS·FAILED만 조립한다"
            " (RUNNING은 끊긴 실행이다. 다시 돌려라)"
        )
    return RunRecord(run_dir, path, repo, status, metadata)


def discover_runs(run_dirs: Sequence[str | Path]) -> list[RunRecord]:
    """실행 디렉터리들의 실행 기록을 모두 읽는다. 하위 디렉터리는 보지 않는다.

    같은 repo의 기록이 두 번 나오거나(상태 무관), 어느 기록에도 속하지 않는 `*.jsonl`이
    있으면 거부한다. FAILED 기록의 kept/excluded 파일은 기록에 속한 것으로 치지만 읽지 않는다.
    """
    runs: list[RunRecord] = []
    seen: dict[str, Path] = {}
    for raw_dir in run_dirs:
        run_dir = Path(raw_dir)
        if not run_dir.is_dir():
            raise AssemblyError(f"{run_dir}: 실행 디렉터리가 없다")
        dir_runs = [
            _load_run_record(run_dir, path) for path in sorted(run_dir.glob(f"*{RUN_SUFFIX}"))
        ]
        if not dir_runs:
            raise AssemblyError(f"{run_dir}: 실행 기록(*{RUN_SUFFIX})이 없다")
        claimed: set[str] = set()
        for run in dir_runs:
            # 같은 디렉터리를 두 번 줘서 같은 파일을 두 번 만난 경우도 중복이다
            if run.repo in seen:
                raise AssemblyError(
                    f"같은 repo의 실행 기록이 두 번 있다: {run.repo} ({seen[run.repo]}, {run.path})"
                )
            seen[run.repo] = run.path
            kept_path, excluded_path, _run_path = output_paths(run_dir, run.repo)
            claimed.update((kept_path.name, excluded_path.name))
        orphans = [path for path in sorted(run_dir.glob("*.jsonl")) if path.name not in claimed]
        if orphans:
            names = ", ".join(path.name for path in orphans)
            raise AssemblyError(f"{run_dir}: 실행 기록이 없는 JSONL이 있다: {names}")
        runs.extend(dir_runs)
    return runs


def _success_version(runs: Sequence[RunRecord]) -> str:
    """SUCCESS 기록들의 공통 `filter_rule_version`. 없거나 비었거나 섞이면 거부한다."""
    if not runs:
        raise AssemblyError("SUCCESS 실행 기록이 없다 — 조립할 레코드가 없다")
    versions: dict[str, list[str]] = {}
    for run in runs:
        version = run.metadata.get("filter_rule_version")
        if not isinstance(version, str) or not version.strip():
            raise AssemblyError(f"{run.path}: {run.repo}의 filter_rule_version이 비어 있다")
        versions.setdefault(version, []).append(run.repo)
    if len(versions) > 1:
        detail = "; ".join(f"{version}: {', '.join(repos)}" for version, repos in versions.items())
        raise AssemblyError(f"filter_rule_version이 섞였다 — {detail}")
    return next(iter(versions))


def _expected_count(run: RunRecord, key: str) -> int:
    """실행 기록의 건수 필드. 0 이상의 int(`bool` 제외)가 아니면 거부한다."""
    value = run.metadata.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AssemblyError(f"{run.path}: {run.repo}의 {key}가 올바르지 않다: {value!r}")
    return value


def _iter_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """JSONL의 (줄 번호, 객체). 파일이 없거나 객체가 아닌 줄이 있으면 거부한다."""
    if not path.is_file():
        raise AssemblyError(f"{path}: 출력 파일이 없다")
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except ValueError as error:
                raise AssemblyError(f"{path}:{line_no}: JSON이 아니다: {error}") from error
            if not isinstance(row, dict):
                raise AssemblyError(f"{path}:{line_no}: JSON 객체가 아니다")
            yield line_no, row


def _check_count(run: RunRecord, path: Path, key: str, actual: int) -> None:
    """파일 행 수가 실행 기록의 건수와 같은지."""
    expected = _expected_count(run, key)
    if actual != expected:
        raise AssemblyError(f"{path}: 행 수 {actual}가 실행 기록의 {key} {expected}와 다르다")


def _write_run_rows(handle: Any, run: RunRecord, version: str, counts: Counter[str]) -> None:
    """SUCCESS 저장소 하나의 kept·excluded 행을 `handle`에 쓴다."""
    kept_path, excluded_path, _run_path = output_paths(run.run_dir, run.repo)
    kept_rows = 0
    for _line_no, row in _iter_rows(kept_path):
        kept_row = {**row, "filter_status": KEPT, "filter_rule_version": version}
        handle.write(json.dumps(kept_row, ensure_ascii=False) + "\n")
        kept_rows += 1
    _check_count(run, kept_path, "kept_count", kept_rows)
    counts[KEPT] += kept_rows

    excluded_rows = 0
    for line_no, row in _iter_rows(excluded_path):
        status = row.get("filter_status")
        if not isinstance(status, str) or status not in NOISE_STATUSES:
            raise AssemblyError(
                f"{excluded_path}:{line_no}: filter_status {status!r}는 NOISE_* 값이 아니다"
            )
        row_version = row.get("filter_rule_version")
        if row_version != version:
            raise AssemblyError(
                f"{excluded_path}:{line_no}: filter_rule_version {row_version!r}가 실행 기록의 "
                f"{version!r}와 다르다"
            )
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[status] += 1
        excluded_rows += 1
    _check_count(run, excluded_path, "excluded_count", excluded_rows)


def _repo_entry(run: RunRecord) -> dict[str, Any]:
    """보고서의 조립된 저장소 한 줄 — 건수와 출처."""
    return {
        "repo": run.repo,
        "run_dir": str(run.run_dir),
        "kept_count": run.metadata.get("kept_count"),
        "excluded_count": run.metadata.get("excluded_count"),
        "pipeline_code_sha": run.metadata.get("pipeline_code_sha"),
        "pipeline_dirty": run.metadata.get("pipeline_dirty"),
    }


def _failed_entry(run: RunRecord) -> dict[str, Any]:
    """보고서의 실패로 제외한 저장소 한 줄."""
    return {
        "repo": run.repo,
        "run_dir": str(run.run_dir),
        **{
            key: run.metadata.get(key)
            for key in (
                "failure_stage",
                "failure_type",
                "failure_reason",
                "attempt_count",
                "filter_rule_version",
            )
        },
    }


def assemble(run_dirs: Sequence[str | Path], out_path: str | Path) -> AssemblyReport:
    """실행 디렉터리들을 조립해 `out_path`(JSONL)와 보고서(`report_path_for`)를 쓴다.

    거부 조건은 모듈 독스트링 그대로다. 행을 읽기 전에 실행 기록 전체(중복·상태·버전)를 먼저
    검사하고, 행은 임시 파일로 흘려 쓰며 검사한다. 어디서든 거부되면 임시 파일만 지우고 최종
    경로는 건드리지 않는다.
    """
    dirs = [Path(run_dir) for run_dir in run_dirs]
    out = Path(out_path)
    report_path = report_path_for(out)
    out_parent = out.parent.resolve()
    if any(run_dir.resolve() == out_parent for run_dir in dirs):
        raise AssemblyError(f"{out}: 출력 경로가 입력 실행 디렉터리 안에 있다")

    runs = discover_runs(dirs)
    succeeded = sorted((run for run in runs if run.status == STATUS_SUCCESS), key=lambda r: r.repo)
    failed = sorted((run for run in runs if run.status == STATUS_FAILED), key=lambda r: r.repo)
    version = _success_version(succeeded)
    for run in succeeded:
        _expected_count(run, "kept_count")
        _expected_count(run, "excluded_count")

    out.parent.mkdir(parents=True, exist_ok=True)
    out_tmp = out.with_name(out.name + ".tmp")
    report_tmp = report_path.with_name(report_path.name + ".tmp")
    counts: Counter[str] = Counter()
    try:
        with out_tmp.open("w", encoding="utf-8") as handle:
            for run in succeeded:
                _write_run_rows(handle, run, version, counts)
        report = AssemblyReport(
            out_path=str(out),
            run_dirs=[str(run_dir) for run_dir in dirs],
            filter_rule_version=version,
            record_count=sum(counts.values()),
            filter_status_counts=dict(sorted(counts.items())),
            repos=[_repo_entry(run) for run in succeeded],
            failed_repos=[_failed_entry(run) for run in failed],
        )
        report_tmp.write_text(
            json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # 결과를 먼저 바꾸므로, 보고서 교체가 실패할 자리면 결과도 바꾸기 전에 거부한다
        if report_path.is_dir():
            raise AssemblyError(f"{report_path}: 보고서 경로가 디렉터리다")
        os.replace(out_tmp, out)
        os.replace(report_tmp, report_path)
    finally:
        out_tmp.unlink(missing_ok=True)
        report_tmp.unlink(missing_ok=True)
    return report


def _log_stderr(message: str) -> None:
    """진행 로그. 결과 파일과 섞이지 않게 stderr로 보낸다."""
    print(message, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    """조립 CLI 인자."""
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.assemble",
        description=(
            "pipeline.run 실행 디렉터리들의 KEPT·제외 JSONL을 합쳐 filter_status를 붙인다 "
            "(Issue #101)."
        ),
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        action="append",
        required=True,
        dest="run_dirs",
        help="pipeline.run의 --out-dir. 여러 번 줄 수 있다",
    )
    parser.add_argument("--out", type=Path, required=True, help="조립 결과 JSONL 경로")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """조립했으면 0(실패로 제외한 저장소가 있어도), 거부했으면 1을 돌려준다."""
    args = build_parser().parse_args(argv)
    try:
        report = assemble(args.run_dirs, args.out)
    except AssemblyError as error:
        _log_stderr(f"조립 거부: {error}")
        return 1
    _log_stderr(
        f"조립 완료: {args.out} — 저장소 {len(report.repos)}개, 레코드 {report.record_count}건, "
        f"filter_rule_version {report.filter_rule_version}, {report.filter_status_counts}"
    )
    for item in report.failed_repos:
        _log_stderr(
            f"실패로 제외: {item['repo']} ({item['failure_stage']}: {item['failure_type']}) "
            f"— {item['run_dir']}"
        )
    _log_stderr(f"보고서: {report_path_for(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
