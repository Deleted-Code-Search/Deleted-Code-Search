"""데이터셋 내보내기 — dataset-v0.9 (Issue #83). 담당: 재헌

무엇을:
    필터 `v0.9` 조립 결과(`data/assembled_v0.9/*.jsonl`, 저장소 100개, 파일 4개)에서 KEPT 행만
    골라 분류 결과(`data/classified/*.jsonl`)를 `record_id` 로 붙이고, 저장소마다 Parquet 과
    jsonl.gz 로 쓴다. 추출·맥락 결합·사후 필터·분류는 다시 돌리지 않는다 — 이미 있는 파일을
    읽기만 한다.

내보내는 행 (export 계약):
    `filter_status == "KEPT"` 인 행만. NOISE_*(MOVE·RENAME·FORMAT·BULK·GENERATED·TRIVIAL) 는
    내보내지 않고 건수만 매니페스트에 남긴다. PARTIAL 은 KEPT 면 들어간다 (CHARTER §4.2 ②,
    ADR-015 "데이터셋 추출에는 PARTIAL 5줄 이상만 포함").

한 행의 모양:
    §4.4 `DeletionRecord` 필드 + 내보내기 쪽 속성 두 개(`code_policy`, `classification`).
    §4.4 스키마에는 필드를 더하지 않는다 — `training_repo` 같은 분류 출력 쪽 속성은
    `classification` 묶음에만 둔다 (#180 판정, `docs/reports/apply100.md` §3).

    - §4.4 에 있지만 이 파이프라인이 만들지 않는 `deleted_body_normalized`·`embedding` 은 넣지
      않는다. §4.4 밖의 추출 필드 중 `start_line`·`end_line` 은 넣고, 라벨링 화면용
      `added_hunks_same_file`·필터 판정용 `filter_evidence` 는 넣지 않는다.
    - 맥락 결합은 FULL_FUNCTION 만 했다(#142). 맥락이 없는 행은 `context` 의 목록 칸과 PR 칸이
      null 이고(빈 목록이 아니다 — "PR 이 없다" 와 "보지 않았다" 를 가른다) `replacement` 가
      null 이다. `context.commit_message` 는 추출 단계 값으로 늘 채운다.

이유(`reason`)와 `classification.source`:
    HUMAN            확정 500건 (`datasets/labeled_500.jsonl` 의 `final`). split 그대로.
                     `reason.classifier_version` = "human"
    MODEL_LLM        LLM 포함 표본(`llm_sample.jsonl`, r1+m2+c3+llm). 확정 500건과 겹치지 않는다
    MODEL_ONLY       학습 저장소 20개의 모델만 라벨(`r1+m2`). 현재 결정대로 그대로 둔다
    UNDISTINGUISHED  신규 80개의 모델만 라벨 — "이유 미구분". `reason` 은 null 이고 분류기
                     버전만 `classification.classifier_version` 에 남긴다 (#180 조장 판정)
    NOT_CLASSIFIED   분류 대상(FULL_FUNCTION·KEPT)이 아닌 PARTIAL, 또는 분류 결과에 없는 행

    우선순위는 HUMAN > MODEL_LLM > 모델만이다. 사람 라벨이 있으면 분류기 라벨을 쓰지 않는다.

라이선스 (CHARTER §4.2 ④, ADR-007·011):
    저장소 라이선스는 선정 CSV(`docs/repo_final100.csv`)의 `license` 열이다. permissive
    (`select_repos.is_permissive_license`, MIT/Apache/BSD) 이면 `CODE_INCLUDED`, 아니면
    `LINK_ONLY` — 코드 본문(`deleted_body`·`function_signature`·`replacement.code`)과 코드를
    인용할 수 있는 `reason.evidence_text` 를 null 로 두고 `source_url`·메타데이터만 남긴다.
    모든 행에 `repo_license`·`source_url` 을 채운다.

    저장소는 permissive 여도 행 일부가 다른 라이선스 아래인 경우(하위 경로, 이력상 다른 라이선스
    시기)는 `docs/license_overrides.csv` 의 규칙에 맞는 행만 `LINK_ONLY` 로 낸다. 규칙은
    경로 접두사(`path_prefix`)와 `author_date` 날짜 상한(`before_date`, 그날 전) 중 하나 이상이고,
    근거는 `docs/repo_final100.md` §3.1 이다. 이유 라벨·split·분류 속성은 그대로 둔다 — 행을
    빼지 않고 코드 칸만 비운다. `repo_license` 는 저장소 루트 라이선스 그대로다.

파일 나눔 (shard):
    저장소마다 따로 쓴다. 한 저장소의 행을 입력 순서대로 이어 붙이다가, JSON 한 줄(UTF-8,
    줄바꿈 포함)의 누적 바이트가 `--max-shard-bytes`(기본 1.5e9)를 넘게 되면 새 shard 를 연다.
    압축 전 크기로 나누므로 같은 입력이면 같은 경계가 나오고(결정적), 두 형식의 shard 가 같은
    행을 담는다. 압축 파일은 압축 전보다 거의 커지지 않으므로 GitHub Release 파일 한도
    (2 GiB) 아래에 여유가 있다. 쓰고 난 뒤 파일마다 `MAX_FILE_BYTES`(2e9) 이하인지 확인한다.

    파일 이름: `<owner>__<name>-<k:05d>-of-<n:05d>.{parquet,jsonl.gz}` (k 는 0부터). Release
    자산은 디렉터리가 없어서 한 디렉터리에 평평하게 둔다. gzip 헤더의 파일 이름·시각은 비워
    같은 내용이면 같은 바이트가 나온다.

나눠 돌리기:
    입력 파일 4개를 프로세스 하나씩(`--workers`)으로 돌린다. 한 저장소는 한 입력 파일에만
    있어야 하고, 파일 안에서 이어져 있어야 한다 (`assemble.py` 는 저장소 이름순으로 쓴다).
    아니면 거부한다.

출력 (`--out`, gitignore 된 `data/` 아래):
    <저장소>-*.parquet, <저장소>-*.jsonl.gz   저장소 100개
    main500-v0.9.parquet, main500-v0.9.jsonl.gz  확정 500건만
    manifest.json   파일별 건수·바이트·sha256, 저장소별 건수, 검증 결과
    repos.csv       저장소별 라이선스·code_policy·training_repo·건수
    training_repos.txt
    SHA256SUMS

    `--commit-dir`(기본 `datasets/dataset-v0.9`) 에는 Git 에 넣을 작은 파일만 쓴다:
    main500.jsonl(확정 500건, 이 형식 그대로), repos.csv, training_repos.txt, summary.json.

사용:
    python -m pipeline.export \\
        --input data/assembled_v0.9/records_v0.9.jsonl \\
        --input data/assembled_v0.9/records100_hs_v0.9.jsonl \\
        --input data/assembled_v0.9/records100_jh_v0.9.jsonl \\
        --input data/assembled_v0.9/records100_sj_v0.9.jsonl \\
        --classified data/classified/model_only_old20.jsonl \\
        --classified data/classified/model_only_hs.jsonl \\
        --classified data/classified/model_only_jh.jsonl \\
        --classified data/classified/model_only_sj.jsonl \\
        --llm-sample data/classified/llm_sample.jsonl \\
        --out data/export/dataset-v0.9
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.select_repos import is_permissive_license

DATASET_VERSION = "dataset-v0.9"
FILTER_RULE_VERSION = "v0.9"
LANGUAGE = "python"
HUMAN_CLASSIFIER_VERSION = "human"

# 압축 전 JSON 줄 누적 바이트가 이 값을 넘으면 새 shard. 결정적이다 (입력만으로 정해진다).
DEFAULT_MAX_SHARD_BYTES = 1_500_000_000
# 쓰고 난 파일의 상한. GitHub Release 자산 한도 2 GiB(2,147,483,648) 보다 작게 잡았다.
MAX_FILE_BYTES = 2_000_000_000
# Parquet row group 하나에 모으는 행 수. 본문이 큰 행이 있어 작게 둔다.
ROW_GROUP_ROWS = 2_000

CODE_INCLUDED = "CODE_INCLUDED"
LINK_ONLY = "LINK_ONLY"

SOURCE_HUMAN = "HUMAN"
SOURCE_MODEL_LLM = "MODEL_LLM"
SOURCE_MODEL_ONLY = "MODEL_ONLY"
SOURCE_UNDISTINGUISHED = "UNDISTINGUISHED"
SOURCE_NOT_CLASSIFIED = "NOT_CLASSIFIED"

# 분류 대상 (`classify.sampling.is_eligible` 과 같은 범위).
CLASSIFIED_KIND = "FULL_FUNCTION"
KEPT = "KEPT"

DEFAULT_LICENSES = Path("docs") / "repo_final100.csv"
DEFAULT_LICENSE_OVERRIDES = Path("docs") / "license_overrides.csv"
DEFAULT_LABELS = Path("datasets") / "labeled_500.jsonl"
DEFAULT_TRAINING_RECORDS = Path("datasets") / "labels" / "main500_records.jsonl"
DEFAULT_COMMIT_DIR = Path("datasets") / DATASET_VERSION

# 조립 결과는 `json.dumps` 출력이라 문자열 안의 `"` 는 늘 `\"` 다. 그래서 이스케이프되지 않은
# `"filter_status": "X"` 는 최상위 키에서만 나온다. NOISE 행은 이 정규식으로 건수만 세고
# JSON 을 풀지 않는다 (KEPT 가 아닌 행이 전체의 3/4 이다).
_STATUS_RE = re.compile(rb'"filter_status": "([A-Z_]+)"')
_REPO_RE = re.compile(rb'^\{"repo": "([^"\\]+)"')


class ExportError(ValueError):
    """내보내기를 거부한다. 출력은 만들지 않는다 (또는 남은 것을 믿지 않는다)."""


# --------------------------------------------------------------------------------------
# 스키마
# --------------------------------------------------------------------------------------

REVIEW_COMMENT_TYPE = pa.struct(
    [
        ("comment_id", pa.int64()),
        ("body", pa.string()),
        ("path", pa.string()),
        ("line", pa.int64()),
        ("side", pa.string()),
        ("outdated", pa.bool_()),
        ("author", pa.string()),
    ]
)

SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("repo", pa.string()),
        ("repo_license", pa.string()),
        ("commit_sha", pa.string()),
        ("parent_sha", pa.string()),
        ("author_date", pa.string()),
        ("file_path", pa.string()),
        ("start_line", pa.int64()),
        ("end_line", pa.int64()),
        ("language", pa.string()),
        ("function_name", pa.string()),
        ("function_signature", pa.string()),
        ("deleted_body", pa.string()),
        ("deletion_kind", pa.string()),
        ("is_test_code", pa.bool_()),
        ("filter_status", pa.string()),
        ("filter_rule_version", pa.string()),
        (
            "context",
            pa.struct(
                [
                    ("commit_message", pa.string()),
                    ("pr_number", pa.int64()),
                    ("pr_title", pa.string()),
                    ("pr_body", pa.string()),
                    ("pr_labels", pa.list_(pa.string())),
                    ("issue_numbers", pa.list_(pa.int64())),
                    ("issue_titles", pa.list_(pa.string())),
                    ("issue_bodies", pa.list_(pa.string())),
                    ("review_comments", pa.list_(REVIEW_COMMENT_TYPE)),
                ]
            ),
        ),
        (
            "replacement",
            pa.struct(
                [
                    ("code", pa.string()),
                    ("match_method", pa.string()),
                    ("confidence", pa.float64()),
                ]
            ),
        ),
        (
            "reason",
            pa.struct(
                [
                    ("label", pa.string()),
                    ("evidence_grade", pa.string()),
                    ("evidence_text", pa.string()),
                    ("confidence", pa.float64()),
                    ("classifier_version", pa.string()),
                ]
            ),
        ),
        ("source_url", pa.string()),
        ("code_policy", pa.string()),
        (
            "classification",
            pa.struct(
                [
                    ("source", pa.string()),
                    ("training_repo", pa.bool_()),
                    ("classifier_version", pa.string()),
                    ("split", pa.string()),
                    ("human_method", pa.string()),
                ]
            ),
        ),
    ]
)

CONTEXT_LIST_FIELDS = ("pr_labels", "issue_numbers", "issue_titles", "issue_bodies")


# --------------------------------------------------------------------------------------
# 입력 읽기 (분류 결과·라벨·라이선스)
# --------------------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """줄 단위로 읽는다. `splitlines()` 를 쓰지 않는다 (U+2028, #146)."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


@dataclass(frozen=True)
class Reason:
    """분류기 출력 한 줄의 §4.4 `reason` 부분."""

    label: str
    evidence_grade: str
    evidence_text: str | None
    confidence: float
    classifier_version: str
    repo: str
    training_repo: bool

    def as_reason(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "evidence_grade": self.evidence_grade,
            "evidence_text": self.evidence_text,
            "confidence": self.confidence,
            "classifier_version": self.classifier_version,
        }


def load_classified(paths: Sequence[Path]) -> tuple[dict[str, Reason], Counter[str]]:
    """분류기 출력 파일들 -> `{record_id: Reason}`. 같은 record_id 가 두 번이면 거부한다.

    두 번째 값은 파일별 행 수다.
    """
    result: dict[str, Reason] = {}
    per_file: Counter[str] = Counter()
    for path in paths:
        for row in _iter_jsonl(path):
            record_id = str(row.get("record_id") or "").strip()
            if not record_id:
                raise ExportError(f"{path}: record_id 가 없는 행")
            if record_id in result:
                raise ExportError(f"분류 결과에 같은 record_id 가 두 번: {record_id} ({path})")
            result[record_id] = Reason(
                label=row["label"],
                evidence_grade=row["evidence_grade"],
                evidence_text=row.get("evidence_text"),
                confidence=float(row.get("confidence") or 0.0),
                classifier_version=row["classifier_version"],
                repo=str(row.get("repo")),
                training_repo=bool(row.get("training_repo")),
            )
            per_file[str(path)] += 1
    return result, per_file


@dataclass(frozen=True)
class HumanLabel:
    """확정 500건 한 줄 (`labeled_500.jsonl` 의 `final` + `split`)."""

    record_id: str
    split: str
    method: str
    reason: dict[str, Any]


def load_human_labels(path: Path) -> dict[str, HumanLabel]:
    """`labeled_500.jsonl` -> `{record_id: HumanLabel}`. 확정되지 않은 행이 있으면 거부한다."""
    labels: dict[str, HumanLabel] = {}
    for row in _iter_jsonl(path):
        record_id = str(row.get("record_id") or "").strip()
        final = row.get("final") or {}
        if not record_id or not final.get("reason_label") or not final.get("evidence_grade"):
            raise ExportError(f"{path}: 확정되지 않은 라벨 행 {record_id!r}")
        if record_id in labels:
            raise ExportError(f"{path}: 같은 record_id 가 두 번: {record_id}")
        labels[record_id] = HumanLabel(
            record_id=record_id,
            split=str(row.get("split")),
            method=str(final.get("method")),
            reason={
                "label": final["reason_label"],
                "evidence_grade": final["evidence_grade"],
                "evidence_text": final.get("evidence_text"),
                "confidence": float(final.get("confidence") or 0.0),
                "classifier_version": HUMAN_CLASSIFIER_VERSION,
            },
        )
    return labels


def load_training_repos(path: Path) -> list[str]:
    """확정 500건을 뽑은 저장소 = 분류기 학습 저장소 (`classify.apply` 와 같은 정의)."""
    return sorted({str(row["repo"]) for row in _iter_jsonl(path)})


def load_licenses(path: Path) -> dict[str, str]:
    """선정 CSV -> `{repo: license}`. `license` 열이 비면 거부한다."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    licenses: dict[str, str] = {}
    for row in rows:
        repo = (row.get("repo") or "").strip()
        license_id = (row.get("license") or "").strip()
        if not repo or not license_id:
            raise ExportError(f"{path}: repo·license 가 빈 행 {row}")
        if repo in licenses:
            raise ExportError(f"{path}: 같은 저장소가 두 번: {repo}")
        licenses[repo] = license_id
    return licenses


@dataclass(frozen=True)
class LicenseOverride:
    """저장소 안 일부 행을 `LINK_ONLY` 로 돌리는 규칙 한 줄 (`docs/license_overrides.csv`)."""

    repo: str
    path_prefix: str
    before_date: str
    license: str

    @property
    def key(self) -> str:
        return f"{self.repo}|{self.path_prefix or '*'}|<{self.before_date or '*'}"

    def matches(self, file_path: str, author_date: str) -> bool:
        if self.path_prefix and not file_path.startswith(self.path_prefix):
            return False
        # 날짜는 `author_date` 의 앞 10자(작성자 시간대의 날짜)로 비교한다.
        return not self.before_date or author_date[:10] < self.before_date


def load_license_overrides(path: Path) -> dict[str, list[LicenseOverride]]:
    """규칙 CSV -> `{repo: [LicenseOverride]}`. 경로·날짜가 둘 다 빈 규칙은 거부한다."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    overrides: dict[str, list[LicenseOverride]] = {}
    for row in rows:
        rule = LicenseOverride(
            repo=(row.get("repo") or "").strip(),
            path_prefix=(row.get("path_prefix") or "").strip(),
            before_date=(row.get("before_date") or "").strip(),
            license=(row.get("license") or "").strip(),
        )
        if not rule.repo or not rule.license or not (rule.path_prefix or rule.before_date):
            raise ExportError(f"{path}: repo·license, path_prefix·before_date 중 하나가 필요 {row}")
        if rule.before_date and len(rule.before_date) != 10:
            raise ExportError(f"{path}: before_date 는 YYYY-MM-DD: {rule.before_date!r}")
        overrides.setdefault(rule.repo, []).append(rule)
    return overrides


def code_policy_of(license_id: str) -> str:
    """permissive(MIT/Apache/BSD) 면 코드 포함, 아니면 링크만 (ADR-007·011)."""
    return CODE_INCLUDED if is_permissive_license(license_id) else LINK_ONLY


def repo_slug(repo: str) -> str:
    """`owner/name` -> `owner__name` (`data/context` 등과 같은 규칙)."""
    if repo.count("/") != 1:
        raise ExportError(f"저장소 이름이 owner/name 이 아니다: {repo!r}")
    return repo.replace("/", "__")


def shard_name(repo: str, index: int, count: int, suffix: str) -> str:
    """`owner__name-00000-of-00001.parquet`."""
    return f"{repo_slug(repo)}-{index:05d}-of-{count:05d}{suffix}"


# --------------------------------------------------------------------------------------
# 한 행 만들기
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class JoinTables:
    """행을 만들 때 쓰는 표. 워커마다 하나씩 들고 있는다."""

    model_only: dict[str, Reason]
    llm: dict[str, Reason]
    human: dict[str, HumanLabel]
    licenses: dict[str, str]
    training_repos: frozenset[str]
    overrides: dict[str, list[LicenseOverride]] = field(default_factory=dict)


def _context_of(record: dict[str, Any]) -> dict[str, Any]:
    context = record.get("context")
    commit_message = record.get("commit_message")
    if not isinstance(context, dict):
        # 맥락 결합을 하지 않은 행 (PARTIAL). 목록 칸은 빈 목록이 아니라 null 이다.
        return {
            "commit_message": commit_message,
            "pr_number": None,
            "pr_title": None,
            "pr_body": None,
            **{name: None for name in CONTEXT_LIST_FIELDS},
            "review_comments": None,
        }
    return {
        "commit_message": context.get("commit_message", commit_message),
        "pr_number": context.get("pr_number"),
        "pr_title": context.get("pr_title"),
        "pr_body": context.get("pr_body"),
        **{name: list(context.get(name) or []) for name in CONTEXT_LIST_FIELDS},
        "review_comments": [
            {key: comment.get(key) for key in REVIEW_COMMENT_TYPE.names}
            for comment in context.get("review_comments") or []
        ],
    }


def classify_row(
    record_id: str, training_repo: bool, tables: JoinTables
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """`(reason, classification)`. 우선순위는 사람 > LLM 포함 > 모델만 (모듈 독스트링)."""
    human = tables.human.get(record_id)
    if human is not None:
        return dict(human.reason), {
            "source": SOURCE_HUMAN,
            "training_repo": training_repo,
            "classifier_version": HUMAN_CLASSIFIER_VERSION,
            "split": human.split,
            "human_method": human.method,
        }
    base = {"training_repo": training_repo, "split": None, "human_method": None}
    llm = tables.llm.get(record_id)
    if llm is not None:
        return llm.as_reason(), {
            "source": SOURCE_MODEL_LLM,
            "classifier_version": llm.classifier_version,
            **base,
        }
    model = tables.model_only.get(record_id)
    if model is not None:
        if training_repo:
            return model.as_reason(), {
                "source": SOURCE_MODEL_ONLY,
                "classifier_version": model.classifier_version,
                **base,
            }
        # 신규 80개 모델만 라벨 - 구체적인 이유를 믿지 않는다 (#180 판정).
        return None, {
            "source": SOURCE_UNDISTINGUISHED,
            "classifier_version": model.classifier_version,
            **base,
        }
    # 분류 대상이 아니거나(PARTIAL) 분류 결과에 없는 행.
    return None, {"source": SOURCE_NOT_CLASSIFIED, "classifier_version": None, **base}


def build_row(
    record: dict[str, Any], tables: JoinTables
) -> tuple[dict[str, Any], LicenseOverride | None]:
    """조립 결과 한 줄(KEPT) -> `(내보내기 한 줄, 맞은 라이선스 규칙 또는 None)`."""
    repo = str(record["repo"])
    record_id = str(record.get("id") or "").strip()
    if not record_id:
        raise ExportError(f"{repo}: id 가 없는 KEPT 행")
    license_id = tables.licenses.get(repo)
    if license_id is None:
        raise ExportError(f"라이선스 목록에 없는 저장소: {repo}")
    policy = code_policy_of(license_id)
    override = None
    if policy == CODE_INCLUDED:
        file_path = str(record.get("file_path") or "")
        author_date = str(record.get("author_date") or "")
        override = next(
            (r for r in tables.overrides.get(repo, ()) if r.matches(file_path, author_date)), None
        )
        if override is not None:
            policy = LINK_ONLY
    training_repo = repo in tables.training_repos
    kind = str(record.get("deletion_kind"))
    reason, classification = classify_row(record_id, training_repo, tables)

    replacement = record.get("replacement")
    if isinstance(replacement, dict):
        replacement = {
            "code": replacement.get("code"),
            "match_method": replacement.get("match_method"),
            "confidence": replacement.get("confidence"),
        }
    else:
        replacement = None

    row: dict[str, Any] = {
        "id": record_id,
        "repo": repo,
        "repo_license": license_id,
        "commit_sha": record.get("commit_sha"),
        "parent_sha": record.get("parent_sha"),
        "author_date": record.get("author_date"),
        "file_path": record.get("file_path"),
        "start_line": record.get("start_line"),
        "end_line": record.get("end_line"),
        "language": LANGUAGE,
        "function_name": record.get("function_name"),
        "function_signature": record.get("function_signature"),
        "deleted_body": record.get("deleted_body"),
        "deletion_kind": kind,
        "is_test_code": record.get("is_test_code"),
        "filter_status": record.get("filter_status"),
        "filter_rule_version": record.get("filter_rule_version"),
        "context": _context_of(record),
        "replacement": replacement,
        "reason": reason,
        "source_url": record.get("source_url"),
        "code_policy": policy,
        "classification": classification,
    }
    if policy == LINK_ONLY:
        row["deleted_body"] = None
        row["function_signature"] = None
        if row["replacement"] is not None:
            row["replacement"]["code"] = None
        if row["reason"] is not None:
            row["reason"]["evidence_text"] = None
    if not row["source_url"]:
        raise ExportError(f"{record_id}: source_url 이 비었다")
    return row, override


def row_line(row: dict[str, Any]) -> bytes:
    """jsonl 한 줄 (UTF-8, 끝에 `\\n`). 키 순서는 `SCHEMA` 순서다."""
    return (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------------------
# 쓰기
# --------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class ShardWriter:
    """한 shard 의 Parquet·jsonl.gz 쌍. 같은 행을 같은 순서로 쓴다."""

    def __init__(self, parquet_path: Path, jsonl_path: Path) -> None:
        self.parquet_path = parquet_path
        self.jsonl_path = jsonl_path
        self.records = 0
        self.uncompressed_bytes = 0
        self._buffer: list[dict[str, Any]] = []
        self._parquet = pq.ParquetWriter(parquet_path, SCHEMA, compression="zstd")
        self._raw = jsonl_path.open("wb")
        # 파일 이름·시각을 헤더에 넣지 않는다 - 같은 내용이면 같은 바이트.
        self._gzip = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=0)

    def write(self, row: dict[str, Any], line: bytes) -> None:
        self._gzip.write(line)
        self._buffer.append(row)
        self.records += 1
        self.uncompressed_bytes += len(line)
        if len(self._buffer) >= ROW_GROUP_ROWS:
            self._flush()

    def _flush(self) -> None:
        if self._buffer:
            self._parquet.write_table(pa.Table.from_pylist(self._buffer, schema=SCHEMA))
            self._buffer = []

    def close(self) -> None:
        self._flush()
        self._parquet.close()
        self._gzip.close()
        self._raw.close()


@dataclass
class FileEntry:
    """매니페스트의 파일 한 줄."""

    name: str
    repo: str
    format: str
    shard: int
    shards: int
    records: int
    bytes: int
    sha256: str = ""


@dataclass
class RepoResult:
    """저장소 하나를 쓴 결과."""

    repo: str
    status_counts: Counter[str] = field(default_factory=Counter)
    exported: int = 0
    by_kind: Counter[str] = field(default_factory=Counter)
    by_source: Counter[str] = field(default_factory=Counter)
    by_label: Counter[str] = field(default_factory=Counter)
    by_policy: Counter[str] = field(default_factory=Counter)
    by_override: Counter[str] = field(default_factory=Counter)
    with_context: int = 0
    shard_records: list[int] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)


class RepoWriter:
    """저장소 하나의 shard 들. 압축 전 누적 바이트로 나눈다 (모듈 독스트링 "파일 나눔")."""

    def __init__(self, repo: str, out_dir: Path, max_shard_bytes: int) -> None:
        self.repo = repo
        self.out_dir = out_dir
        self.max_shard_bytes = max_shard_bytes
        self.result = RepoResult(repo)
        self._closed: list[ShardWriter] = []
        self._current: ShardWriter | None = None

    def _open(self) -> ShardWriter:
        index = len(self._closed)
        slug = repo_slug(self.repo)
        return ShardWriter(
            # 프로세스마다 다른 임시 이름. 같은 저장소가 입력 두 곳이면 close 가 거부한다.
            self.out_dir / f"{slug}-{index:05d}.parquet.{os.getpid()}.part",
            self.out_dir / f"{slug}-{index:05d}.jsonl.gz.{os.getpid()}.part",
        )

    def write(self, row: dict[str, Any]) -> None:
        line = row_line(row)
        if len(line) > self.max_shard_bytes:
            raise ExportError(f"{row['id']}: 한 행이 shard 상한보다 크다 ({len(line):,}B)")
        current = self._current
        if current is not None and current.uncompressed_bytes + len(line) > self.max_shard_bytes:
            current.close()
            self._closed.append(current)
            current = None
        if current is None:
            current = self._current = self._open()
        current.write(row, line)

    def close(self) -> RepoResult:
        """남은 shard 를 닫고 `-of-N` 이름으로 바꾼다. 행이 0건이어도 빈 shard 1개를 쓴다."""
        if self._current is None:
            self._current = self._open()
        self._current.close()
        self._closed.append(self._current)
        count = len(self._closed)
        for index, shard in enumerate(self._closed):
            self.result.shard_records.append(shard.records)
            for part, suffix, fmt in (
                (shard.parquet_path, ".parquet", "parquet"),
                (shard.jsonl_path, ".jsonl.gz", "jsonl.gz"),
            ):
                final = self.out_dir / shard_name(self.repo, index, count, suffix)
                # 링크는 대상이 있으면 실패한다 - 확인과 이름 바꾸기 사이에 다른 프로세스가
                # 끼어들 틈이 없다 (exists() 후 replace 는 Windows 에서 PermissionError 로 샜다).
                try:
                    os.link(part, final)
                except FileExistsError:
                    raise ExportError(
                        f"저장소 {self.repo} 가 입력 파일 두 곳에 있다 ({final.name})"
                    ) from None
                part.unlink()
                self.result.files.append(
                    FileEntry(
                        name=final.name,
                        repo=self.repo,
                        format=fmt,
                        shard=index,
                        shards=count,
                        records=shard.records,
                        bytes=final.stat().st_size,
                    )
                )
        return self.result


@dataclass
class InputResult:
    """입력 파일 하나를 처리한 결과 (워커 -> 메인)."""

    path: str
    lines: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    repos: list[RepoResult] = field(default_factory=list)
    human_rows: list[dict[str, Any]] = field(default_factory=list)
    matched_model_only: set[str] = field(default_factory=set)
    matched_llm: set[str] = field(default_factory=set)
    # 분류 결과에는 있는데 v0.9 에서 KEPT 가 아닌 행: {record_id: filter_status}
    classified_not_kept: dict[str, str] = field(default_factory=dict)
    exported_ids: int = 0
    seconds: float = 0.0


def _scan_status(line: bytes) -> tuple[str, str] | None:
    """NOISE 행은 JSON 을 풀지 않고 `(repo, filter_status)` 만 읽는다. 못 읽으면 None."""
    repo = _REPO_RE.match(line)
    statuses = _STATUS_RE.findall(line)
    if repo is None or len(statuses) != 1:
        return None
    return repo.group(1).decode("utf-8"), statuses[0].decode("ascii")


def export_input(
    path: Path, out_dir: Path, tables: JoinTables, max_shard_bytes: int
) -> InputResult:
    """입력 파일 하나를 읽어 저장소별로 쓴다. 저장소는 파일 안에서 이어져 있어야 한다."""
    started = time.monotonic()
    result = InputResult(str(path))
    seen: set[str] = set()
    writer: RepoWriter | None = None
    ids: set[str] = set()
    classified_ids = tables.model_only.keys() | tables.llm.keys()

    def switch(repo: str) -> RepoWriter:
        nonlocal writer
        if writer is not None:
            result.repos.append(writer.close())
        if repo in seen:
            raise ExportError(f"{path}: 저장소 {repo} 의 행이 이어져 있지 않다")
        seen.add(repo)
        writer = RepoWriter(repo, out_dir, max_shard_bytes)
        return writer

    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            result.lines += 1
            scanned = _scan_status(raw)
            if scanned is not None and scanned[1] not in (KEPT, "NOISE_GENERATED"):
                repo, status = scanned
                record = None
            else:
                record = json.loads(raw)
                repo, status = str(record["repo"]), str(record["filter_status"])
            current = writer if writer is not None and writer.repo == repo else switch(repo)
            current.result.status_counts[status] += 1
            result.status_counts[status] += 1
            if status != KEPT:
                if record is not None and str(record.get("id")) in classified_ids:
                    result.classified_not_kept[str(record["id"])] = status
                continue
            assert record is not None
            row, override = build_row(record, tables)
            record_id = row["id"]
            if record_id in ids:
                raise ExportError(f"{path}: 같은 record_id 가 두 번: {record_id}")
            ids.add(record_id)
            current.write(row)
            stats = current.result
            stats.exported += 1
            stats.by_kind[row["deletion_kind"]] += 1
            stats.by_policy[row["code_policy"]] += 1
            if override is not None:
                stats.by_override[override.key] += 1
            source = row["classification"]["source"]
            stats.by_source[source] += 1
            if row["reason"] is not None:
                stats.by_label[f"{source}:{row['reason']['label']}"] += 1
            if row["context"]["pr_labels"] is not None:
                stats.with_context += 1
            if source == SOURCE_HUMAN:
                result.human_rows.append(row)
            if record_id in tables.model_only:
                result.matched_model_only.add(record_id)
            if record_id in tables.llm:
                result.matched_llm.add(record_id)
    if writer is not None:
        result.repos.append(writer.close())
    result.exported_ids = len(ids)
    for repo_result in result.repos:
        for entry in repo_result.files:
            entry.sha256 = _sha256(out_dir / entry.name)
    result.seconds = time.monotonic() - started
    return result


def _worker(path: str, out_dir: str, args: dict[str, Any], max_shard_bytes: int) -> InputResult:
    """프로세스 하나 - 표를 직접 읽는다 (87만 건 dict 를 피클로 넘기지 않는다)."""
    tables = load_tables(**args)
    return export_input(Path(path), Path(out_dir), tables, max_shard_bytes)


def load_tables(
    classified: Sequence[str],
    llm_sample: Sequence[str],
    labels: str,
    licenses: str,
    training_records: str,
    license_overrides: str,
) -> JoinTables:
    model_only, _ = load_classified([Path(p) for p in classified])
    llm, _ = load_classified([Path(p) for p in llm_sample])
    return JoinTables(
        model_only=model_only,
        llm=llm,
        human=load_human_labels(Path(labels)),
        licenses=load_licenses(Path(licenses)),
        training_repos=frozenset(load_training_repos(Path(training_records))),
        overrides=load_license_overrides(Path(license_overrides)),
    )


def write_small_file(path: Path, rows: Iterable[dict[str, Any]], fmt: str) -> int:
    """확정 500건 같은 작은 파일. 행 수를 돌려준다."""
    rows = list(rows)
    if fmt == "parquet":
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path, compression="zstd")
    elif fmt == "jsonl.gz":
        with (
            path.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz,
        ):
            for row in rows:
                gz.write(row_line(row))
    elif fmt == "jsonl":
        with path.open("wb") as handle:
            for row in rows:
                handle.write(row_line(row))
    else:
        raise ValueError(fmt)
    return len(rows)


# --------------------------------------------------------------------------------------
# 검증
# --------------------------------------------------------------------------------------


def verify_files(out_dir: Path, entries: Sequence[FileEntry]) -> dict[str, Any]:
    """다시 읽어 확인한다: 크기 상한, gzip 무결성, Parquet 스키마·행 수, 두 형식 행 수·id 일치."""
    problems: list[str] = []
    sources: Counter[str] = Counter()
    policies: Counter[str] = Counter()
    link_only_with_code = 0
    no_reason_with_reason = 0
    new_repo_model_only = 0
    for entry in entries:
        path = out_dir / entry.name
        if path.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"{entry.name}: {path.stat().st_size:,}B > {MAX_FILE_BYTES:,}B")
    pairs: dict[tuple[str, int], dict[str, FileEntry]] = {}
    for entry in entries:
        pairs.setdefault((entry.repo, entry.shard), {})[entry.format] = entry
    for (repo, shard), pair in sorted(pairs.items()):
        if set(pair) != {"parquet", "jsonl.gz"}:
            problems.append(f"{repo} shard {shard}: 형식 쌍이 아니다 {sorted(pair)}")
            continue
        parquet_file = pq.ParquetFile(out_dir / pair["parquet"].name)
        if not parquet_file.schema_arrow.equals(SCHEMA):
            problems.append(f"{pair['parquet'].name}: 스키마가 다르다")
        table = parquet_file.read(
            columns=[
                "id",
                "repo",
                "code_policy",
                "deleted_body",
                "function_signature",
                "replacement",
                "reason",
                "classification",
            ]
        )
        parquet_ids = table.column("id").to_pylist()
        repos = set(table.column("repo").to_pylist())
        if repos - {repo} and repo != "main500":
            problems.append(f"{pair['parquet'].name}: 다른 저장소 행 {sorted(repos - {repo})}")
        for policy, body, signature, replacement, reason, meta in zip(
            table.column("code_policy").to_pylist(),
            table.column("deleted_body").to_pylist(),
            table.column("function_signature").to_pylist(),
            table.column("replacement").to_pylist(),
            table.column("reason").to_pylist(),
            table.column("classification").to_pylist(),
            strict=True,
        ):
            sources[meta["source"]] += 1
            policies[policy] += 1
            if policy == LINK_ONLY and (
                body is not None
                or signature is not None
                or (replacement or {}).get("code") is not None
                or (reason or {}).get("evidence_text") is not None
            ):
                link_only_with_code += 1
            if meta["source"] in (SOURCE_UNDISTINGUISHED, SOURCE_NOT_CLASSIFIED) and reason:
                no_reason_with_reason += 1
            if meta["source"] == SOURCE_MODEL_ONLY and not meta["training_repo"]:
                new_repo_model_only += 1
        jsonl_ids: list[str] = []
        try:
            with gzip.open(out_dir / pair["jsonl.gz"].name, "rb") as handle:
                for line in io.BufferedReader(handle):
                    jsonl_ids.append(json.loads(line)["id"])
        except (OSError, EOFError, json.JSONDecodeError) as error:
            problems.append(f"{pair['jsonl.gz'].name}: gzip/JSON 을 읽을 수 없다 ({error})")
            continue
        if not (len(parquet_ids) == len(jsonl_ids) == pair["parquet"].records):
            problems.append(
                f"{repo} shard {shard}: 행 수 parquet {len(parquet_ids)} / "
                f"jsonl {len(jsonl_ids)} / 기록 {pair['parquet'].records}"
            )
        elif parquet_ids != jsonl_ids:
            problems.append(f"{repo} shard {shard}: 두 형식의 id 순서가 다르다")
    return {
        "files_checked": len(entries),
        "problems": problems,
        # main500 파일은 저장소 파일과 같은 행이라 빼고 센다 (아래 호출부).
        "readback_by_source": dict(sources),
        "readback_by_code_policy": dict(policies),
        "link_only_with_code": link_only_with_code,
        "undistinguished_or_unclassified_with_reason": no_reason_with_reason,
        "new_repo_model_only": new_repo_model_only,
    }


def verify_human(
    human_rows: Sequence[dict[str, Any]], labels: dict[str, HumanLabel]
) -> dict[str, Any]:
    """확정 500건이 모두 있고, 라벨·split 이 `labeled_500.jsonl` 과 같은가."""
    by_id = {row["id"]: row for row in human_rows}
    missing = sorted(set(labels) - set(by_id))
    changed: list[str] = []
    for record_id, label in labels.items():
        row = by_id.get(record_id)
        if row is None:
            continue
        reason = row["reason"]
        expected = label.reason
        # LINK_ONLY 행은 근거 문장(코드를 인용할 수 있다)만 비운다. 라벨·등급·split 은 같아야 한다.
        evidence_expected = (
            expected["evidence_text"] if row["code_policy"] == CODE_INCLUDED else None
        )
        if (
            row["classification"]["split"] != label.split
            or reason["label"] != expected["label"]
            or reason["evidence_grade"] != expected["evidence_grade"]
            or reason["evidence_text"] != evidence_expected
            or reason["confidence"] != expected["confidence"]
        ):
            changed.append(record_id)
    return {
        "expected": len(labels),
        "found": len(by_id),
        "missing": missing,
        "changed": changed,
        "split": dict(Counter(row["classification"]["split"] for row in human_rows)),
        "label": dict(Counter(row["reason"]["label"] for row in human_rows)),
        "grade": dict(Counter(row["reason"]["evidence_grade"] for row in human_rows)),
        "link_only": sorted(row["id"] for row in human_rows if row["code_policy"] == LINK_ONLY),
    }


# --------------------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------------------


def _postfilter_counts(inputs: Sequence[Path]) -> dict[str, dict[str, int]] | None:
    """입력 옆 `<stem>_postfilter.json` 의 `filter_status_after`. 하나라도 없으면 None."""
    counts: dict[str, dict[str, int]] = {}
    for path in inputs:
        report = path.with_name(path.stem + "_postfilter.json")
        if not report.is_file():
            return None
        counts[str(path)] = json.loads(report.read_text(encoding="utf-8"))["filter_status_after"]
    return counts


def run(args: argparse.Namespace) -> dict[str, Any]:
    """내보내고 검증한다. 매니페스트(dict)를 돌려준다. 문제가 있으면 ExportError."""
    out_dir: Path = args.out
    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            raise ExportError(f"출력 디렉터리가 비어 있지 않다: {out_dir} (--force 로 지운다)")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    table_args = {
        "classified": [str(p) for p in args.classified],
        "llm_sample": [str(p) for p in args.llm_sample],
        "labels": str(args.labels),
        "licenses": str(args.licenses),
        "training_records": str(args.training_records),
        "license_overrides": str(args.license_overrides),
    }
    # 메인에서 한 번 읽어 중복·겹침을 먼저 확인한다 (워커가 시작하기 전에 거부).
    model_only, model_files = load_classified(args.classified)
    llm, llm_files = load_classified(args.llm_sample)
    human = load_human_labels(args.labels)
    licenses = load_licenses(args.licenses)
    training_repos = load_training_repos(args.training_records)
    overrides = load_license_overrides(args.license_overrides)
    unknown_override_repos = sorted(set(overrides) - set(licenses))
    if unknown_override_repos:
        raise ExportError(f"라이선스 규칙의 저장소가 목록에 없다: {unknown_override_repos}")
    overlap_llm_human = sorted(set(llm) & set(human))
    if overlap_llm_human:
        raise ExportError(f"LLM 표본에 확정 500건이 섞였다: {overlap_llm_human[:5]}")
    llm_not_in_model = sorted(set(llm) - set(model_only))
    flag_mismatch = sorted(
        rid
        for table in (model_only, llm)
        for rid, reason in table.items()
        if reason.training_repo != (reason.repo in training_repos)
    )
    if flag_mismatch:
        raise ExportError(f"training_repo 플래그가 학습 저장소 목록과 다르다: {flag_mismatch[:5]}")

    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [
            pool.submit(_worker, str(path), str(out_dir), table_args, args.max_shard_bytes)
            for path in args.input
        ]
        results = [future.result() for future in futures]
    elapsed = time.monotonic() - started

    repo_results: dict[str, RepoResult] = {}
    for result in results:
        for repo_result in result.repos:
            if repo_result.repo in repo_results:
                raise ExportError(f"저장소 {repo_result.repo} 가 입력 파일 두 곳에 있다")
            repo_results[repo_result.repo] = repo_result
    entries = [entry for repo in sorted(repo_results) for entry in repo_results[repo].files]

    # 확정 500건 - Release 용 두 형식 + Git 용 jsonl. labeled_500 순서대로.
    order = {record_id: index for index, record_id in enumerate(human)}
    human_rows = sorted(
        (row for result in results for row in result.human_rows), key=lambda r: order[r["id"]]
    )
    for fmt, suffix in (("parquet", ".parquet"), ("jsonl.gz", ".jsonl.gz")):
        name = f"main500-{FILTER_RULE_VERSION}{suffix}"
        count = write_small_file(out_dir / name, human_rows, fmt)
        path = out_dir / name
        entries.append(
            FileEntry(name, "main500", fmt, 0, 1, count, path.stat().st_size, _sha256(path))
        )

    matched_model = set().union(*(r.matched_model_only for r in results))
    matched_llm = set().union(*(r.matched_llm for r in results))
    not_kept: dict[str, str] = {}
    for result in results:
        not_kept.update(result.classified_not_kept)
    unmatched_model = set(model_only) - matched_model
    unmatched_llm = set(llm) - matched_llm
    eligible = sum(r.by_kind[CLASSIFIED_KIND] for r in repo_results.values())
    eligible_joined = sum(
        r.by_source[s]
        for r in repo_results.values()
        for s in (SOURCE_HUMAN, SOURCE_MODEL_LLM, SOURCE_MODEL_ONLY, SOURCE_UNDISTINGUISHED)
    )
    not_classified_full = eligible - eligible_joined

    status_total: Counter[str] = Counter()
    for result in results:
        status_total.update(result.status_counts)
    postfilter = _postfilter_counts(args.input)
    status_matches_postfilter = None
    if postfilter is not None:
        status_matches_postfilter = all(
            dict(result.status_counts) == postfilter[result.path] for result in results
        )

    expected_repos = sorted(licenses)
    # 저장소 파일만 다시 읽어 센다 - main500 은 같은 행의 사본이라 따로 본다.
    file_check = verify_files(out_dir, [e for e in entries if e.repo != "main500"])
    main500_check = verify_files(out_dir, [e for e in entries if e.repo == "main500"])
    file_check["problems"] += main500_check["problems"]
    human_check = verify_human(human_rows, human)
    exported_total = sum(r.exported for r in repo_results.values())
    checks = {
        "repos_expected": len(expected_repos),
        "repos_exported": len(repo_results),
        "repos_missing": sorted(set(expected_repos) - set(repo_results)),
        "repos_unexpected": sorted(set(repo_results) - set(expected_repos)),
        "training_repos_count": len(training_repos),
        "training_repos_missing_from_export": sorted(set(training_repos) - set(repo_results)),
        "duplicate_record_ids": exported_total - sum(r.exported_ids for r in results),
        "join": {
            "classifier_target_rows": eligible,
            "classifier_target_joined": eligible_joined,
            "classifier_target_missing": not_classified_full,
            "model_only_rows": len(model_only),
            "model_only_matched": len(matched_model),
            "model_only_unmatched": len(unmatched_model),
            "model_only_unmatched_by_v09_status": dict(
                Counter(not_kept.get(rid, "ABSENT_OR_OTHER") for rid in unmatched_model)
            ),
            "llm_rows": len(llm),
            "llm_matched": len(matched_llm),
            "llm_unmatched": len(unmatched_llm),
            "llm_unmatched_by_v09_status": dict(
                Counter(not_kept.get(rid, "ABSENT_OR_OTHER") for rid in unmatched_llm)
            ),
            "llm_not_in_model_only": len(llm_not_in_model),
            "duplicate_record_ids_in_classified": 0,  # load_classified 가 거부한다
        },
        "human": human_check,
        "filter_status": {
            "all_rows": dict(status_total),
            "exported_only": KEPT,
            "exported_rows": exported_total,
            "kept_rows": status_total[KEPT],
            "matches_postfilter_report": status_matches_postfilter,
        },
        "files": file_check,
        "max_file_bytes": max((e.bytes for e in entries), default=0),
        "max_file_limit": MAX_FILE_BYTES,
    }
    by_policy: Counter[str] = sum((r.by_policy for r in repo_results.values()), Counter())
    by_override: Counter[str] = sum((r.by_override for r in repo_results.values()), Counter())
    checks["code_policy"] = {
        "exported": dict(by_policy),
        "readback": file_check["readback_by_code_policy"],
        "link_only_by_override": sum(by_override.values()),
        "link_only_by_repo_license": sum(
            r.by_policy[LINK_ONLY]
            for repo, r in repo_results.items()
            if code_policy_of(licenses[repo]) == LINK_ONLY
        ),
    }
    ok = (
        not checks["repos_missing"]
        and not checks["repos_unexpected"]
        and not checks["training_repos_missing_from_export"]
        and file_check["link_only_with_code"] == 0
        and file_check["undistinguished_or_unclassified_with_reason"] == 0
        and file_check["new_repo_model_only"] == 0
        and checks["duplicate_record_ids"] == 0
        and not_classified_full == 0
        and not human_check["missing"]
        and not human_check["changed"]
        and not file_check["problems"]
        and exported_total == status_total[KEPT]
        and dict(by_policy) == file_check["readback_by_code_policy"]
        and checks["code_policy"]["link_only_by_override"]
        + checks["code_policy"]["link_only_by_repo_license"]
        == by_policy[LINK_ONLY]
        and status_matches_postfilter is not False
    )

    repos_table = []
    for repo in expected_repos:
        repo_result = repo_results.get(repo, RepoResult(repo))
        repos_table.append(
            {
                "repo": repo,
                "license": licenses[repo],
                "code_policy": code_policy_of(licenses[repo]),
                "training_repo": repo in training_repos,
                "records": repo_result.exported,
                "full_function": repo_result.by_kind[CLASSIFIED_KIND],
                "partial": repo_result.by_kind["PARTIAL"],
                "human": repo_result.by_source[SOURCE_HUMAN],
                "model_llm": repo_result.by_source[SOURCE_MODEL_LLM],
                "model_only": repo_result.by_source[SOURCE_MODEL_ONLY],
                "undistinguished": repo_result.by_source[SOURCE_UNDISTINGUISHED],
                "not_classified": repo_result.by_source[SOURCE_NOT_CLASSIFIED],
                "noise_rows": sum(c for s, c in repo_result.status_counts.items() if s != KEPT),
                "shards": len(repo_result.shard_records),
            }
        )

    manifest = {
        "dataset_version": DATASET_VERSION,
        "filter_rule_version": FILTER_RULE_VERSION,
        "ok": ok,
        "inputs": {
            "records": [str(p) for p in args.input],
            "classified": dict(model_files),
            "llm_sample": dict(llm_files),
            "labels": str(args.labels),
            "licenses": str(args.licenses),
            "training_records": str(args.training_records),
            "license_overrides": str(args.license_overrides),
        },
        "max_shard_bytes": args.max_shard_bytes,
        "seconds": round(elapsed, 1),
        "totals": {
            "records": exported_total,
            "by_kind": dict(sum((r.by_kind for r in repo_results.values()), Counter())),
            "by_source": dict(sum((r.by_source for r in repo_results.values()), Counter())),
            "with_context": sum(r.with_context for r in repo_results.values()),
            "by_code_policy": dict(by_policy),
            "link_only_by_rule": {
                rule.key: by_override[rule.key] for rules in overrides.values() for rule in rules
            },
        },
        "training_repos": training_repos,
        "checks": checks,
        "repos": repos_table,
        "files": [entry.__dict__ for entry in entries],
    }
    _write_outputs(out_dir, args.commit_dir, manifest, repos_table, training_repos, human_rows)
    return manifest


def _write_repos_csv(path: Path, repos_table: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(repos_table[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(repos_table)


def _write_outputs(
    out_dir: Path,
    commit_dir: Path | None,
    manifest: dict[str, Any],
    repos_table: Sequence[dict[str, Any]],
    training_repos: Sequence[str],
    human_rows: Sequence[dict[str, Any]],
) -> None:
    training_text = "".join(f"{repo}\n" for repo in training_repos)
    (out_dir / "training_repos.txt").write_text(training_text, encoding="utf-8", newline="\n")
    _write_repos_csv(out_dir / "repos.csv", repos_table)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    sums = "".join(f"{entry['sha256']}  {entry['name']}\n" for entry in manifest["files"])
    (out_dir / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    if commit_dir is None:
        return
    commit_dir.mkdir(parents=True, exist_ok=True)
    # Release 자산 README 는 Git 에 있는 데이터셋 문서 그대로다 (SHA256SUMS 밖).
    readme = commit_dir / "README.md"
    if readme.is_file():
        shutil.copyfile(readme, out_dir / "README.md")
    write_small_file(commit_dir / "main500.jsonl", human_rows, "jsonl")
    _write_repos_csv(commit_dir / "repos.csv", repos_table)
    (commit_dir / "training_repos.txt").write_text(training_text, encoding="utf-8", newline="\n")
    # Git 에 넣는 요약 - 실행마다 달라지는 값(시간·sha256·절대 경로)은 뺀다.
    summary = {
        key: manifest[key]
        for key in ("dataset_version", "filter_rule_version", "ok", "totals", "training_repos")
    }
    summary["checks"] = {k: v for k, v in manifest["checks"].items() if k != "files"}
    summary["checks"]["file_problems"] = manifest["checks"]["files"]["problems"]
    summary["files"] = [
        {k: entry[k] for k in ("name", "repo", "format", "shard", "shards", "records")}
        for entry in manifest["files"]
    ]
    (commit_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.export",
        description="dataset-v0.9 내보내기 - 저장소별 Parquet·jsonl.gz (#83).",
    )
    parser.add_argument("--input", type=Path, action="append", required=True, help="여러 번")
    parser.add_argument("--classified", type=Path, action="append", required=True, help="모델만")
    parser.add_argument("--llm-sample", type=Path, action="append", default=[])
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--licenses", type=Path, default=DEFAULT_LICENSES)
    parser.add_argument("--training-records", type=Path, default=DEFAULT_TRAINING_RECORDS)
    parser.add_argument("--license-overrides", type=Path, default=DEFAULT_LICENSE_OVERRIDES)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--commit-dir", type=Path, default=DEFAULT_COMMIT_DIR)
    parser.add_argument("--max-shard-bytes", type=int, default=DEFAULT_MAX_SHARD_BYTES)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="비어 있지 않은 --out 을 지운다")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    missing = [
        p
        for p in [
            *args.input,
            *args.classified,
            *args.llm_sample,
            args.labels,
            args.licenses,
            args.license_overrides,
        ]
        if not p.is_file()
    ]
    if missing:
        print(f"입력 파일이 없다: {missing}", file=sys.stderr)
        return 2
    if args.max_shard_bytes <= 0 or args.max_shard_bytes > MAX_FILE_BYTES:
        print(f"--max-shard-bytes 는 1~{MAX_FILE_BYTES} 이어야 한다", file=sys.stderr)
        return 2
    try:
        manifest = run(args)
    except ExportError as error:
        print(f"거부: {error}", file=sys.stderr)
        return 1
    totals = manifest["totals"]
    checks = manifest["checks"]
    print(f"{DATASET_VERSION}: {totals['records']:,}건, 저장소 {checks['repos_exported']}개")
    print(f"  출처: {totals['by_source']}")
    print(f"  파일 {len(manifest['files'])}개, 가장 큰 파일 {checks['max_file_bytes']:,}B")
    print(f"  검증: {'통과' if manifest['ok'] else '실패'} -> {args.out / 'manifest.json'}")
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
