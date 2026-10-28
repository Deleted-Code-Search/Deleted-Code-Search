"""pipeline/postfilter.py 테스트 (Issue #152, 사후 필터 NOISE_FORMAT)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import postfilter
from pipeline.filter import FILTER_RULE_VERSION
from pipeline.postfilter import (
    NOISE_FORMAT,
    NOISE_GENERATED,
    PostFilterError,
    apply_file,
    apply_row,
    format_evidence,
    format_keywords,
    generated_evidence,
    reappear_counts,
    report_path_for,
)

# black이 인자를 한 줄로 접은 재포맷. 삭제 줄 4개가 모두 추가 쪽에 다시 나타난다.
_DELETED = "    x = foo(\n        'a',\n        b,\n    )\n"
_ADDED = '    x = foo("a", b)\n'


def _row(
    *,
    message: str = "Reformat with black",
    deleted: str = _DELETED,
    added: str = _ADDED,
    status: str = "KEPT",
    **extra,
) -> dict:
    return {
        "id": "r1",
        "repo": "acme/widgets",
        "deletion_kind": "PARTIAL",
        "commit_message": message,
        "deleted_body": deleted,
        "added_hunks_same_file": [{"new_start": 1, "new_count": 1, "added_body": added}],
        "filter_status": status,
        "filter_rule_version": "v0.7",
        **extra,
    }


def test_version_bumped_to_v09():
    assert FILTER_RULE_VERSION == "v0.9"
    assert apply_row(_row())["filter_rule_version"] == "v0.9"
    assert apply_row(_row(message="Add feature"))["filter_rule_version"] == "v0.9"


def test_keyword_and_reappearance_becomes_noise_format():
    out = apply_row(_row())
    assert out["filter_status"] == NOISE_FORMAT
    assert out["filter_evidence"] == {
        "keywords": ["reformat", "black"],
        "reappear_ratio": 1.0,
        "reappeared_lines": 4,
        "deleted_lines": 4,
    }


def test_keyword_without_reappearance_stays_kept():
    row = _row(added="    x = bar()\n")
    out = apply_row(row)
    assert out["filter_status"] == "KEPT"
    assert "filter_evidence" not in out


def test_reappearance_without_keyword_stays_kept():
    """재들여쓰기 같은 재등장 100%라도 메시지가 포맷 커밋이 아니면 둔다 (분석 문서 A3 손실)."""
    out = apply_row(_row(message="Add support for US in the integration"))
    assert reappear_counts(_DELETED, _ADDED) == (4, 4)
    assert out["filter_status"] == "KEPT"
    assert "filter_evidence" not in out


@pytest.mark.parametrize("status", ["NOISE_MOVE", "NOISE_TRIVIAL"])
def test_existing_noise_row_unchanged_except_version(status):
    row = _row(status=status, filter_evidence={"line_count": 4})
    out = apply_row(row)
    assert out == {**row, "filter_rule_version": FILTER_RULE_VERSION}


def test_threshold_is_ninety_percent():
    deleted = "".join(f"    line{i}()\n" for i in range(10))
    nine = "".join(f"line{i}()\n" for i in range(9))
    assert apply_row(_row(deleted=deleted, added=nine))["filter_status"] == NOISE_FORMAT
    eight = "".join(f"line{i}()\n" for i in range(8))
    assert apply_row(_row(deleted=deleted, added=eight))["filter_status"] == "KEPT"


def test_keywords_only_from_subject_line_and_whole_words():
    assert format_keywords("Fix bug\n\nran ruff and black") == []
    assert format_keywords("Remove blacklist and stylesheet") == []
    assert format_keywords("STYLE: Apply black formatting") == ["style", "black", "formatting"]
    assert format_keywords("CLN: re-wrap docstrings") == ["re-wrap"]
    assert format_keywords("fix linting, PEP 8 and flake8") == ["linting", "pep 8", "flake8"]


def test_normalization_matches_analysis_definition():
    # 따옴표 통일, 닫는 괄호 앞 쉼표, 줄 끝 쉼표, 공백뿐인 줄은 세지 않음
    assert reappear_counts("  a = ['x', ]\n\n   \n  y,\n", 'a=["x"]\nf(y)\n') == (2, 2)
    assert reappear_counts("   \n", "anything") == (0, 0)


def test_missing_added_field_stays_kept():
    row = _row()
    del row["added_hunks_same_file"]
    assert apply_row(row)["filter_status"] == "KEPT"


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_apply_file_writes_new_file_and_report(tmp_path: Path):
    src = tmp_path / "records.jsonl"
    rows = [
        _row(),
        _row(message="Add feature"),
        _row(status="NOISE_MOVE"),
        {**_row(), "repo": "acme/other", "deletion_kind": "FULL_FUNCTION"},
    ]
    _write(src, rows)
    before = src.read_bytes()
    out = tmp_path / "out" / "records_v0.9.jsonl"

    report = apply_file(src, out)

    assert src.read_bytes() == before  # 원본은 그대로
    result = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["filter_status"] for r in result] == [
        NOISE_FORMAT,
        "KEPT",
        "NOISE_MOVE",
        NOISE_FORMAT,
    ]
    assert {r["filter_rule_version"] for r in result} == {FILTER_RULE_VERSION}
    assert report["noise_format_count"] == 2
    assert report["noise_format_by_kind"] == {"FULL_FUNCTION": 1, "PARTIAL": 1}
    assert report["noise_format_by_repo"] == {
        "acme/other": {"FULL_FUNCTION": 1},
        "acme/widgets": {"PARTIAL": 1},
    }
    assert report["filter_status_before"] == {"KEPT": 3, "NOISE_MOVE": 1}
    assert report["filter_status_after"] == {"KEPT": 1, "NOISE_FORMAT": 2, "NOISE_MOVE": 1}
    assert json.loads(report_path_for(out).read_text(encoding="utf-8")) == report
    assert not list(out.parent.glob("*.tmp"))


def test_apply_file_is_idempotent_on_its_output(tmp_path: Path):
    src, first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "c.jsonl"
    _write(src, [_row(), _row(message="Add feature")])
    apply_file(src, first)
    report = apply_file(first, second)
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert report["noise_format_count"] == 0  # 이미 NOISE_FORMAT이라 바뀐 것이 없다


def test_apply_file_rejects_other_version_and_keeps_old_output(tmp_path: Path):
    src, out = tmp_path / "records.jsonl", tmp_path / "out.jsonl"
    out.write_text("previous\n", encoding="utf-8")
    _write(src, [_row(), {**_row(), "filter_rule_version": "v0.6"}])
    with pytest.raises(PostFilterError, match="v0.6"):
        apply_file(src, out)
    assert out.read_text(encoding="utf-8") == "previous\n"
    assert not (tmp_path / "out.jsonl.tmp").exists()


def test_apply_file_refuses_to_overwrite_input(tmp_path: Path):
    src = tmp_path / "records.jsonl"
    _write(src, [_row()])
    with pytest.raises(PostFilterError, match="입력과 같은 경로"):
        apply_file(src, src)


def test_cli(tmp_path: Path):
    src, out = tmp_path / "records.jsonl", tmp_path / "out.jsonl"
    _write(src, [_row()])
    assert postfilter.main(["--input", str(src), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["filter_status"] == NOISE_FORMAT
    assert postfilter.main(["--input", str(src), "--out", str(src)]) == 1


# --- NOISE_GENERATED (Issue #184) ---

# 포맷 키워드가 없는 기능 커밋 — NOISE_FORMAT 조건에 걸리지 않는다.
_FEATURE = "Remove test_before_pypi (#7937)"


@pytest.mark.parametrize(
    ("path", "category", "segment", "prefix"),
    [
        # #184 대표 경로 (dspy·rasa·dash)
        (
            "test_before_pypi/lib/python3.9/site-packages/pandas/core/frame.py",
            "site-packages",
            "site-packages",
            "test_before_pypi/lib/python3.9/site-packages",
        ),
        (
            "deactivate/lib/python3.6/site-packages/numpy/core/numeric.py",
            "site-packages",
            "site-packages",
            "deactivate/lib/python3.6/site-packages",
        ),
        (
            "usr/lib/python3/dist-packages/apt/cache.py",
            "site-packages",
            "dist-packages",
            "usr/lib/python3/dist-packages",
        ),
        (
            "env/Lib/site-packages/pip/__init__.py",
            "site-packages",
            "site-packages",
            "env/Lib/site-packages",
        ),
        # dash의 site-packages 밖 venv 표준 라이브러리 사본
        ("venv/lib/python2.7/distutils/__init__.py", "venv", "venv", "venv"),
        (
            "test_before_pypi/lib/python3.9/os.py",
            "venv",
            "lib/python3.9",
            "test_before_pypi/lib/python3.9",
        ),
        (".venv/bin/activate_this.py", "venv", ".venv", ".venv"),
        ("tools/.venv/foo/bar.py", "venv", ".venv", "tools/.venv"),
        ("venv/Scripts/activate_this.py", "venv", "venv", "venv"),
        ("pipenv/vendor/pipdeptree/__init__.py", "vendor", "vendor", "pipenv/vendor"),
        (
            "airflow/providers/google/ads/_vendor/googleads/common.py",
            "vendor",
            "_vendor",
            "airflow/providers/google/ads/_vendor",
        ),
        ("pkg/Vendored/six.py", "vendor", "Vendored", "pkg/Vendored"),
        ("proto/service_pb2.py", "protobuf", "service_pb2.py", "proto/service_pb2.py"),
        ("service_pb2_grpc.py", "protobuf", "service_pb2_grpc.py", "service_pb2_grpc.py"),
    ],
)
def test_generated_path_becomes_noise_generated(path, category, segment, prefix):
    out = apply_row(_row(message=_FEATURE, file_path=path, deletion_kind="FULL_FUNCTION"))
    assert out["filter_status"] == NOISE_GENERATED
    assert out["filter_evidence"] == {
        "path_category": category,
        "matched_segment": segment,
        "matched_prefix": prefix,
    }


def test_site_packages_wins_over_venv_root_in_same_path():
    """범주 순서는 site-packages → venv → vendor. 대표 경로는 site-packages로 남는다."""
    assert generated_evidence({"file_path": "venv/lib/python2.7/site-packages/six.py"}) == {
        "path_category": "site-packages",
        "matched_segment": "site-packages",
        "matched_prefix": "venv/lib/python2.7/site-packages",
    }


@pytest.mark.parametrize(
    "path",
    [
        # 파일 이름·일반 문자열의 vendor·venv
        "src/vendor.py",
        "src/vendor_utils/loader.py",
        "src/vendors/api.py",
        "src/myvendor/api.py",
        "tests/test_vendor.py",
        "pkg/venv.py",
        "pkg/site_packages.py",
        "pkg/site-packages.py",
        # venv를 다루는 패키지 — venv 배치 segment가 뒤따르지 않는다
        "src/hatch/venv/core.py",
        "virtualenv/create/venv.py",
        # lib 아래지만 pythonX.Y 디렉터리가 아니다
        "lib/python/helpers.py",
        "lib/python3/helpers.py",
        "src/lib/utils.py",
        # protoc 출력이 아닌 이름
        "proto/pb2_helpers.py",
        "proto/service_pb2.pyi",
        # 마이그레이션·코드 생성기 출력 디렉터리는 넣지 않았다
        "app/migrations/0001_initial.py",
        "plotly/validators/scatter/_x.py",
        "",
    ],
)
def test_ordinary_path_is_not_generated(path):
    assert generated_evidence({"file_path": path}) is None
    out = apply_row(_row(message=_FEATURE, file_path=path))
    assert out["filter_status"] == "KEPT"
    assert "filter_evidence" not in out


def test_ordinary_path_keeps_noise_format_behavior():
    out = apply_row(_row(file_path="src/widgets/core.py"))
    assert out["filter_status"] == NOISE_FORMAT
    assert out["filter_evidence"]["keywords"] == ["reformat", "black"]


def test_generated_takes_priority_over_format():
    """포맷 커밋이 벤더 디렉터리를 재포맷해도 NOISE_GENERATED다 — 경로가 먼저다."""
    row = _row(file_path="pipenv/vendor/six.py")
    assert format_evidence(row) is not None  # NOISE_FORMAT 조건도 맞는다
    out = apply_row(row)
    assert out["filter_status"] == NOISE_GENERATED
    assert out["filter_evidence"]["path_category"] == "vendor"


@pytest.mark.parametrize("status", ["NOISE_MOVE", "NOISE_TRIVIAL", "NOISE_FORMAT"])
def test_existing_noise_row_with_generated_path_unchanged(status):
    row = _row(
        status=status,
        file_path="venv/lib/python3.9/site-packages/six.py",
        filter_evidence={"line_count": 4},
    )
    out = apply_row(row)
    assert out == {**row, "filter_rule_version": FILTER_RULE_VERSION}


def test_apply_file_reports_generated_separately_from_format(tmp_path: Path):
    src = tmp_path / "records.jsonl"
    site = "test_before_pypi/lib/python3.9/site-packages/pandas/core/frame.py"
    dspy = {
        **_row(message=_FEATURE, file_path=site, deletion_kind="FULL_FUNCTION"),
        "repo": "stanfordnlp/dspy",
    }
    dash = {
        **_row(message="rm venv", file_path="venv/lib/python2.7/distutils/core.py"),
        "repo": "plotly/dash",
    }
    rows = [
        _row(),  # NOISE_FORMAT
        _row(message=_FEATURE, file_path=site, deletion_kind="FULL_FUNCTION"),
        dspy,
        dash,
        _row(file_path="pipenv/vendor/six.py"),  # 포맷 조건도 맞지만 NOISE_GENERATED
        _row(message=_FEATURE, file_path="src/vendor.py"),  # KEPT
        _row(status="NOISE_MOVE", file_path="pipenv/vendor/six.py"),  # 그대로
    ]
    _write(src, rows)
    out = tmp_path / "records_v0.9.jsonl"

    report = apply_file(src, out)

    result = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["filter_status"] for r in result] == [
        NOISE_FORMAT,
        NOISE_GENERATED,
        NOISE_GENERATED,
        NOISE_GENERATED,
        NOISE_GENERATED,
        "KEPT",
        "NOISE_MOVE",
    ]
    assert report["noise_format_count"] == 1
    assert report["noise_format_by_kind"] == {"PARTIAL": 1}
    assert report["noise_format_by_repo"] == {"acme/widgets": {"PARTIAL": 1}}
    assert report["noise_generated_count"] == 4
    assert report["noise_generated_by_kind"] == {"FULL_FUNCTION": 2, "PARTIAL": 2}
    assert report["noise_generated_by_category"] == {
        "site-packages": 2,
        "vendor": 1,
        "venv": 1,
    }
    assert report["noise_generated_by_repo"] == {
        "acme/widgets": {"FULL_FUNCTION": 1, "PARTIAL": 1},
        "plotly/dash": {"PARTIAL": 1},
        "stanfordnlp/dspy": {"FULL_FUNCTION": 1},
    }
    assert report["filter_status_before"] == {"KEPT": 6, "NOISE_MOVE": 1}
    assert report["filter_status_after"] == {
        "KEPT": 1,
        "NOISE_FORMAT": 1,
        "NOISE_GENERATED": 4,
        "NOISE_MOVE": 1,
    }
    assert json.loads(report_path_for(out).read_text(encoding="utf-8")) == report


def test_apply_file_with_generated_is_idempotent(tmp_path: Path):
    src, first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "c.jsonl"
    _write(
        src,
        [
            _row(),
            _row(message=_FEATURE, file_path="venv/lib/python2.7/site-packages/six.py"),
            _row(file_path="pkg/_vendor/six.py"),
            _row(message=_FEATURE, file_path="src/vendor.py"),
        ],
    )
    apply_file(src, first)
    report = apply_file(first, second)
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert report["noise_format_count"] == 0
    assert report["noise_generated_count"] == 0
    assert report["noise_generated_by_category"] == {}
    assert report["filter_status_after"] == {"KEPT": 1, "NOISE_FORMAT": 1, "NOISE_GENERATED": 2}


# --- 규칙 버전 v0.9 (Issue #184): v0.7 → v0.8(NOISE_FORMAT) → v0.9(NOISE_GENERATED) ---


def _version_chain_rows() -> list[dict]:
    """v0.7 조립 결과 표본: 포맷만·경로만·둘 다·둘 다 아님·이미 NOISE_MOVE."""
    return [
        {**_row(), "id": "format"},
        {**_row(message=_FEATURE, file_path="venv/lib/python3.9/site-packages/a.py"), "id": "gen"},
        {**_row(file_path="pkg/_vendor/six.py"), "id": "both"},
        {**_row(message=_FEATURE, file_path="src/vendor.py"), "id": "kept"},
        {**_row(status="NOISE_MOVE", file_path="pkg/_vendor/x.py"), "id": "move"},
    ]


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("version", ["v0.7", "v0.8", "v0.9"])
def test_apply_file_accepts_v07_v08_v09_and_writes_v09(tmp_path: Path, version):
    src, out = tmp_path / "records.jsonl", tmp_path / "out.jsonl"
    _write(src, [{**row, "filter_rule_version": version} for row in _version_chain_rows()])
    report = apply_file(src, out)
    assert report["input_versions"] == {version: 5}
    assert report["filter_rule_version"] == "v0.9"
    assert {row["filter_rule_version"] for row in _read(out)} == {"v0.9"}


def test_v08_postfilter_output_flows_into_v09(tmp_path: Path, monkeypatch):
    """v0.7 → (v0.8 사후 필터) → v0.9 와 v0.7 → v0.9 는 KEPT 집합이 같다.

    v0.8 사후 필터는 NOISE_FORMAT만 보던 이 모듈이다 — 판정 목록과 버전만 그때로 돌려 재현한다.
    """
    src = tmp_path / "records.jsonl"
    _write(src, _version_chain_rows())

    v08, via_v08, direct = tmp_path / "v08.jsonl", tmp_path / "via.jsonl", tmp_path / "direct.jsonl"
    with monkeypatch.context() as patch:
        patch.setattr(postfilter, "_JUDGES", ((NOISE_FORMAT, format_evidence),))
        patch.setattr(postfilter, "FILTER_RULE_VERSION", "v0.8")
        apply_file(src, v08)
    assert {row["filter_rule_version"] for row in _read(v08)} == {"v0.8"}
    assert [row["filter_status"] for row in _read(v08)] == [
        NOISE_FORMAT,
        "KEPT",
        NOISE_FORMAT,
        "KEPT",
        "NOISE_MOVE",
    ]

    report = apply_file(v08, via_v08)
    apply_file(src, direct)

    assert report["input_versions"] == {"v0.8": 5}
    assert report["noise_format_count"] == 0  # v0.8에서 KEPT로 남은 행은 포맷이 아니다
    assert report["noise_generated_count"] == 1
    assert report["noise_generated_by_category"] == {"site-packages": 1}

    via = {row["id"]: row["filter_status"] for row in _read(via_v08)}
    straight = {row["id"]: row["filter_status"] for row in _read(direct)}
    assert {k for k, v in via.items() if v == "KEPT"} == {"kept"}
    assert {k for k, v in straight.items() if v == "KEPT"} == {"kept"}
    # 포맷·경로 조건을 둘 다 만족하는 행만 사유가 다르다 — 어느 쪽이든 제외다
    assert via == {**straight, "both": NOISE_FORMAT}
    assert straight["both"] == NOISE_GENERATED
    assert {row["filter_rule_version"] for row in _read(via_v08)} == {"v0.9"}
