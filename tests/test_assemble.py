"""pipeline/assemble.py 테스트 (Issue #101).

입력 파일은 실제 writer(`extract.write_jsonl`·`write_excluded_jsonl`)로 쓰고, 실행 기록은
`run.py`의 `<stem>_run.json`과 같은 키로 직접 쓴다. 마지막 테스트는 실제 `run.run_batch`
출력을 그대로 조립한다 — 두 단계 사이의 형식이 어긋나면 거기서 잡힌다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import assemble as assemble_module
from pipeline import run as run_module
from pipeline.assemble import AssemblyError, assemble, report_path_for
from pipeline.extract import (
    DeletedFunction,
    ExcludedRecord,
    excluded_to_json_dict,
    make_record_id,
    to_json_dict,
    write_excluded_jsonl,
    write_jsonl,
)
from pipeline.filter import FILTER_RULE_VERSION, NOISE_MOVE, NOISE_TRIVIAL
from tests.test_run import _CODE_STATE, _make_source, requires_git

_VERSION = "v0.7"


def _record(repo: str, n: int, kind: str = "FULL_FUNCTION") -> DeletedFunction:
    """`repo`의 n번째 삭제 레코드. 값은 n으로만 갈린다."""
    commit_sha = f"{n:040x}"
    file_path = f"pkg/m{n}.py"
    function_name = f"f{n}"
    return DeletedFunction(
        repo=repo,
        commit_sha=commit_sha,
        parent_sha=f"{n + 1:040x}",
        file_path=file_path,
        function_name=function_name,
        start_line=1,
        end_line=2,
        deletion_kind=kind,
        deleted_hunk=f"def {function_name}():\n    return {n}",
        added_hunks_same_file=(),
        author_date="2026-09-01T00:00:00+00:00",
        commit_message=f"remove {function_name}",
        id=make_record_id(repo, commit_sha, file_path, function_name, 1),
        function_signature=f"def {function_name}()",
        is_test_code=False,
        source_url=f"https://github.com/{repo}/commit/{commit_sha}",
    )


def _move(record: DeletedFunction, version: str = _VERSION) -> ExcludedRecord:
    """`record`를 NOISE_MOVE로 제외한 레코드. evidence는 #97 NOISE_MOVE 키 5개다."""
    evidence = {
        "file_path": "new.py",
        "function_name": record.function_name,
        "start_line": 1,
        "end_line": 2,
        "similarity": 1.0,
    }
    return ExcludedRecord(record, NOISE_MOVE, version, evidence)


def _trivial(record: DeletedFunction, version: str = _VERSION) -> ExcludedRecord:
    """`record`를 NOISE_TRIVIAL로 제외한 레코드. evidence는 `line_count` 하나다."""
    return ExcludedRecord(record, NOISE_TRIVIAL, version, {"line_count": 2})


def _metadata(repo: str, status: str, version: str | None, kept: int, excluded: int) -> dict:
    """`run._metadata`와 같은 키. 조립이 보는 값만 인자로 받는다."""
    failed = status == run_module.STATUS_FAILED
    return {
        "repo": repo,
        "default_branch": "main",
        "ref": "origin/main",
        "status": status,
        "started_at": "2026-09-27T00:00:00+00:00",
        "finished_at": "2026-09-27T00:01:00+00:00",
        "elapsed_seconds": 60.0,
        "retry_wait_seconds": 70.0 if failed else 0.0,
        "attempt_count": 3 if failed else 1,
        "retry_count": 2 if failed else 0,
        "commit_count": None if failed else 10,
        "kept_count": None if failed else kept,
        "excluded_count": None if failed else excluded,
        "noise_move_count": None if failed else excluded,
        "noise_trivial_count": None if failed else 0,
        "failure_stage": "clone" if failed else None,
        "failure_type": "CalledProcessError" if failed else None,
        "failure_reason": "network" if failed else None,
        "repository_head_sha": None if failed else "a" * 40,
        "filter_rule_version": version,
        "pipeline_code_sha": "f" * 40,
        "pipeline_dirty": False,
    }


def _write_run(
    run_dir: Path,
    repo: str,
    kept: list[DeletedFunction],
    excluded: list[ExcludedRecord],
    *,
    status: str = run_module.STATUS_SUCCESS,
    version: str | None = _VERSION,
    **overrides,
) -> dict:
    """`run.py`와 같은 이름으로 kept/excluded/실행 기록을 쓰고 기록을 돌려준다."""
    kept_path, excluded_path, run_path = run_module.output_paths(run_dir, repo)
    write_jsonl(kept, kept_path)
    write_excluded_jsonl(excluded, excluded_path)
    metadata = {**_metadata(repo, status, version, len(kept), len(excluded)), **overrides}
    run_path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def _write_failed(run_dir: Path, repo: str, **overrides) -> dict:
    """실행 기록만 있는 FAILED 저장소."""
    run_dir.mkdir(parents=True, exist_ok=True)
    metadata = {**_metadata(repo, run_module.STATUS_FAILED, _VERSION, 0, 0), **overrides}
    run_module.output_paths(run_dir, repo)[2].write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def _read_jsonl(path: Path) -> list[dict]:
    """JSONL 파일을 행별 dict 목록으로 읽는다."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _standard_run(run_dir: Path, repo: str = "acme/widgets") -> tuple[list, list]:
    """kept 2 + NOISE_MOVE 1 + NOISE_TRIVIAL 1."""
    kept = [_record(repo, 1), _record(repo, 2, "PARTIAL")]
    excluded = [_move(_record(repo, 3)), _trivial(_record(repo, 4, "PARTIAL"))]
    _write_run(run_dir, repo, kept, excluded)
    return kept, excluded


# --- 정상 조립 -----------------------------------------------------------------------


def test_kept_gets_kept_status_and_excluded_rows_are_preserved(tmp_path: Path):
    """kept 행에는 `filter_status`·`filter_rule_version` 두 키만 붙고, excluded 행은 writer
    출력 그대로다."""
    kept, excluded = _standard_run(tmp_path / "run")
    out = tmp_path / "out" / "filtered.jsonl"

    report = assemble([tmp_path / "run"], out)

    rows = _read_jsonl(out)
    assert rows[:2] == [
        {**to_json_dict(record), "filter_status": "KEPT", "filter_rule_version": _VERSION}
        for record in kept
    ]
    assert rows[2:] == [excluded_to_json_dict(item) for item in excluded]
    assert all("filter_evidence" not in row for row in rows[:2])
    assert report.filter_rule_version == _VERSION
    assert report.record_count == 4
    assert report.filter_status_counts == {"KEPT": 2, NOISE_MOVE: 1, NOISE_TRIVIAL: 1}
    assert report.failed_repos == []


def test_merges_multiple_run_dirs_in_repo_order(tmp_path: Path):
    """실행 디렉터리 여러 개를 합치고, 입력 순서와 무관하게 저장소 이름 순으로 쓴다."""
    _standard_run(tmp_path / "b", "zeta/z")
    _standard_run(tmp_path / "a", "alpha/a")
    out = tmp_path / "out" / "filtered.jsonl"

    report = assemble([tmp_path / "b", tmp_path / "a"], out)

    rows = _read_jsonl(out)
    assert [row["repo"] for row in rows] == ["alpha/a"] * 4 + ["zeta/z"] * 4
    assert [item["repo"] for item in report.repos] == ["alpha/a", "zeta/z"]
    assert report.repos[0]["run_dir"] == str(tmp_path / "a")


def test_version_comes_from_metadata_not_code_constant(tmp_path: Path):
    """버전은 실행 기록에서 읽는다 — 지금 코드의 `FILTER_RULE_VERSION`과 달라도 거부하지 않는다."""
    old = "v0.1"
    assert old != FILTER_RULE_VERSION
    repo = "acme/widgets"
    _write_run(
        tmp_path / "run", repo, [_record(repo, 1)], [_move(_record(repo, 2), old)], version=old
    )

    out = tmp_path / "out" / "f.jsonl"

    report = assemble([tmp_path / "run"], out)

    assert report.filter_rule_version == old
    assert {row["filter_rule_version"] for row in _read_jsonl(out)} == {old}


def test_empty_excluded_file_is_valid(tmp_path: Path):
    """제외 0건의 빈 excluded 파일은 정상 입력이다 (#97 "0건이어도 빈 파일")."""
    repo = "acme/widgets"
    _write_run(tmp_path / "run", repo, [_record(repo, 1)], [])

    report = assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")

    assert report.filter_status_counts == {"KEPT": 1}


def test_writes_report_file(tmp_path: Path):
    """보고서가 `<stem>_assembly.json`에 반환값과 같은 내용으로 남고 임시 파일은 남지 않는다."""
    _standard_run(tmp_path / "run")
    out = tmp_path / "out" / "filtered.jsonl"

    report = assemble([tmp_path / "run"], out)

    assert report_path_for(out) == tmp_path / "out" / "filtered_assembly.json"
    saved = json.loads(report_path_for(out).read_text(encoding="utf-8"))
    assert saved == report.to_json_dict()
    assert saved["repos"][0]["pipeline_code_sha"] == "f" * 40
    assert not list((tmp_path / "out").glob("*.tmp"))


# --- FAILED --------------------------------------------------------------------------


def test_failed_repo_is_excluded_reported_and_its_stale_files_are_not_read(tmp_path: Path):
    """FAILED 저장소는 옆에 남은 이전 kept/excluded를 읽지 않고 보고만 하며, 그 버전은 혼합
    판정에 들어가지 않는다."""
    run_dir = tmp_path / "run"
    _standard_run(run_dir, "acme/ok")
    # 이전 성공 실행이 남긴 파일 — 버전도 다르고 내용도 깨졌지만 읽지 않으므로 상관없다
    stale = "stale/repo"
    _write_run(run_dir, stale, [_record(stale, 9)], [_move(_record(stale, 8), "v0.1")])
    kept_path, excluded_path, _ = run_module.output_paths(run_dir, stale)
    kept_path.write_text("not json\n", encoding="utf-8")
    _write_failed(run_dir, stale, filter_rule_version="v9.9")
    out = tmp_path / "out" / "f.jsonl"

    report = assemble([run_dir], out)

    assert {row["repo"] for row in _read_jsonl(out)} == {"acme/ok"}
    assert [item["repo"] for item in report.repos] == ["acme/ok"]
    assert report.failed_repos == [
        {
            "repo": stale,
            "run_dir": str(run_dir),
            "failure_stage": "clone",
            "failure_type": "CalledProcessError",
            "failure_reason": "network",
            "attempt_count": 3,
            "filter_rule_version": "v9.9",
        }
    ]
    # FAILED 기록의 버전(v9.9)은 혼합 판정에 들어가지 않는다
    assert report.filter_rule_version == _VERSION


def test_failed_repo_without_version_is_still_only_reported(tmp_path: Path):
    """FAILED 기록은 `filter_rule_version`이 없어도 거부 사유가 아니다 — 보고만 한다."""
    _standard_run(tmp_path / "run")
    _write_failed(tmp_path / "run", "broken/repo", filter_rule_version=None)

    report = assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")

    assert [item["repo"] for item in report.failed_repos] == ["broken/repo"]


def test_only_failed_runs_is_rejected(tmp_path: Path):
    """SUCCESS가 하나도 없으면 조립 결과의 버전을 정할 수 없어 거부한다."""
    _write_failed(tmp_path / "run", "acme/widgets")

    with pytest.raises(AssemblyError, match="SUCCESS 실행 기록이 없다"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


# --- 거부 ----------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["acme__widgets.jsonl", "acme__widgets_excluded.jsonl"])
def test_jsonl_without_run_metadata_is_rejected(tmp_path: Path, filename: str):
    """어느 실행 기록에도 속하지 않는 kept/excluded JSONL이 있으면 거부한다."""
    _standard_run(tmp_path / "run", "other/repo")
    (tmp_path / "run" / filename).write_text("", encoding="utf-8")

    with pytest.raises(AssemblyError, match="실행 기록이 없는 JSONL"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


def test_run_dir_without_any_metadata_is_rejected(tmp_path: Path):
    """출력 파일만 있고 실행 기록이 하나도 없는 디렉터리는 거부한다."""
    repo = "acme/widgets"
    kept_path, excluded_path, run_path = run_module.output_paths(tmp_path / "run", repo)
    write_jsonl([_record(repo, 1)], kept_path)
    write_excluded_jsonl([], excluded_path)

    with pytest.raises(AssemblyError, match="실행 기록"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


def test_missing_run_dir_is_rejected(tmp_path: Path):
    """존재하지 않는 실행 디렉터리는 거부한다."""
    with pytest.raises(AssemblyError, match="실행 디렉터리가 없다"):
        assemble([tmp_path / "nope"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("content", ["not json", "[1, 2]", "{}", '{"repo": ""}'])
def test_unreadable_run_metadata_is_rejected(tmp_path: Path, content: str):
    """JSON이 아니거나 객체가 아니거나 `repo`가 없는 실행 기록은 거부한다."""
    _standard_run(tmp_path / "run")
    run_module.output_paths(tmp_path / "run", "acme/widgets")[2].write_text(content, "utf-8")

    with pytest.raises(AssemblyError):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("version", [None, "", "   ", 7, "missing"])
def test_success_without_filter_rule_version_is_rejected(tmp_path: Path, version):
    """SUCCESS 기록의 `filter_rule_version`이 없음·null·빈 값·공백·문자열 아님이면 거부한다."""
    repo = "acme/widgets"
    metadata = _write_run(tmp_path / "run", repo, [_record(repo, 1)], [])
    if version == "missing":
        del metadata["filter_rule_version"]
    else:
        metadata["filter_rule_version"] = version
    run_module.output_paths(tmp_path / "run", repo)[2].write_text(json.dumps(metadata), "utf-8")

    with pytest.raises(AssemblyError, match="filter_rule_version이 비어 있다"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("status", ["RUNNING", "DONE", None, "missing"])
def test_running_or_unknown_status_is_rejected(tmp_path: Path, status):
    """RUNNING·정의되지 않은 값·`status` 없음은 FAILED처럼 빼지 않고 거부한다."""
    repo = "acme/widgets"
    metadata = _write_run(tmp_path / "run", repo, [_record(repo, 1)], [])
    if status == "missing":
        del metadata["status"]
    else:
        metadata["status"] = status
    run_module.output_paths(tmp_path / "run", repo)[2].write_text(json.dumps(metadata), "utf-8")

    with pytest.raises(AssemblyError, match="SUCCESS·FAILED만 조립한다"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize(
    ("first", "second"),
    [("SUCCESS", "SUCCESS"), ("SUCCESS", "FAILED"), ("FAILED", "SUCCESS"), ("FAILED", "FAILED")],
)
def test_same_repo_in_two_run_dirs_is_rejected(tmp_path: Path, first: str, second: str):
    """같은 repo의 실행 기록이 두 디렉터리에 있으면 상태 조합과 무관하게 거부한다."""
    repo = "acme/widgets"
    for name, status in (("a", first), ("b", second)):
        if status == "SUCCESS":
            _standard_run(tmp_path / name, repo)
        else:
            _write_failed(tmp_path / name, repo)
    # 두 입력 모두 SUCCESS가 되도록 다른 저장소를 하나 둔다 (SUCCESS 0건 거부와 구분)
    _standard_run(tmp_path / "c", "other/repo")

    with pytest.raises(AssemblyError, match="같은 repo의 실행 기록이 두 번"):
        assemble([tmp_path / "a", tmp_path / "b", tmp_path / "c"], tmp_path / "out" / "f.jsonl")


def test_same_run_dir_given_twice_is_rejected(tmp_path: Path):
    """같은 디렉터리를 두 번 주면 같은 기록을 두 번 만나므로 중복으로 거부한다."""
    _standard_run(tmp_path / "run")

    with pytest.raises(AssemblyError, match="두 번"):
        assemble([tmp_path / "run", tmp_path / "run"], tmp_path / "out" / "f.jsonl")


def test_mixed_filter_rule_versions_are_rejected(tmp_path: Path):
    """SUCCESS 기록끼리 `filter_rule_version`이 다르면 거부한다."""
    _write_run(tmp_path / "a", "acme/a", [_record("acme/a", 1)], [], version="v0.6")
    _write_run(tmp_path / "b", "acme/b", [_record("acme/b", 1)], [], version="v0.7")

    with pytest.raises(AssemblyError, match="filter_rule_version이 섞였다"):
        assemble([tmp_path / "a", tmp_path / "b"], tmp_path / "out" / "f.jsonl")


def test_excluded_row_version_differing_from_metadata_is_rejected(tmp_path: Path):
    """excluded 행의 `filter_rule_version`이 실행 기록과 다르면 거부한다."""
    repo = "acme/widgets"
    _write_run(tmp_path / "run", repo, [], [_move(_record(repo, 1), "v0.6")], version="v0.7")

    with pytest.raises(AssemblyError, match="실행 기록의 'v0.7'와 다르다"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("status", ["KEPT", "NOISE_UNKNOWN", None, ["NOISE_MOVE"]])
def test_excluded_row_without_noise_status_is_rejected(tmp_path: Path, status):
    """excluded 행의 `filter_status`가 CHARTER §4.4의 NOISE_* 값이 아니면 거부한다."""
    repo = "acme/widgets"
    _write_run(tmp_path / "run", repo, [], [_move(_record(repo, 1))])
    excluded_path = run_module.output_paths(tmp_path / "run", repo)[1]
    row = {**_read_jsonl(excluded_path)[0], "filter_status": status}
    excluded_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(AssemblyError, match="NOISE_\\* 값이 아니다"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("key", ["kept_count", "excluded_count"])
def test_row_count_differing_from_metadata_is_rejected(tmp_path: Path, key: str):
    """파일 행 수가 실행 기록의 `kept_count`·`excluded_count`와 다르면 거부한다."""
    repo = "acme/widgets"
    metadata = _write_run(tmp_path / "run", repo, [_record(repo, 1)], [_move(_record(repo, 2))])
    metadata[key] += 1
    run_module.output_paths(tmp_path / "run", repo)[2].write_text(json.dumps(metadata), "utf-8")

    with pytest.raises(AssemblyError, match=key):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("index", [0, 1])
def test_success_with_missing_output_file_is_rejected(tmp_path: Path, index: int):
    """SUCCESS 기록인데 kept나 excluded 파일이 없으면 거부한다."""
    _standard_run(tmp_path / "run")
    run_module.output_paths(tmp_path / "run", "acme/widgets")[index].unlink()

    with pytest.raises(AssemblyError, match="출력 파일이 없다"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


@pytest.mark.parametrize("content", ["\n", "not json\n", "[1]\n"])
def test_non_object_line_is_rejected(tmp_path: Path, content: str):
    """빈 줄·JSON이 아닌 줄·객체가 아닌 줄이 있으면 거부한다."""
    _standard_run(tmp_path / "run")
    kept_path = run_module.output_paths(tmp_path / "run", "acme/widgets")[0]
    kept_path.write_text(kept_path.read_text(encoding="utf-8") + content, encoding="utf-8")

    with pytest.raises(AssemblyError, match="JSON"):
        assemble([tmp_path / "run"], tmp_path / "out" / "f.jsonl")


def test_output_inside_run_dir_is_rejected(tmp_path: Path):
    """출력 경로가 입력 실행 디렉터리 안이면 거부한다."""
    _standard_run(tmp_path / "run")

    with pytest.raises(AssemblyError, match="입력 실행 디렉터리 안"):
        assemble([tmp_path / "run"], tmp_path / "run" / "f.jsonl")


def test_rejection_keeps_previous_output_and_leaves_no_temp_files(tmp_path: Path):
    """행을 쓰는 도중 거부돼도 이전 결과·보고서는 바이트 그대로이고 임시 파일은 남지 않는다."""
    _standard_run(tmp_path / "run")
    out = tmp_path / "out" / "f.jsonl"
    assemble([tmp_path / "run"], out)
    previous = out.read_bytes(), report_path_for(out).read_bytes()
    # 행을 쓰는 도중(두 번째 파일)에 거부되게 만든다
    excluded_path = run_module.output_paths(tmp_path / "run", "acme/widgets")[1]
    excluded_path.write_text("not json\n", encoding="utf-8")

    with pytest.raises(AssemblyError):
        assemble([tmp_path / "run"], out)

    assert (out.read_bytes(), report_path_for(out).read_bytes()) == previous
    assert not list(out.parent.glob("*.tmp"))


# --- CLI -----------------------------------------------------------------------------


def test_cli_returns_0_and_reports_failed_repos(tmp_path: Path, capsys):
    """FAILED 저장소가 있어도 조립되면 0을 돌려주고, 제외한 저장소를 stderr에 알린다."""
    _standard_run(tmp_path / "run")
    _write_failed(tmp_path / "run", "broken/repo")
    out = tmp_path / "out" / "f.jsonl"

    code = assemble_module.main(["--run-dir", str(tmp_path / "run"), "--out", str(out)])

    assert code == 0
    err = capsys.readouterr().err
    assert "실패로 제외: broken/repo" in err
    assert out.exists() and report_path_for(out).exists()


def test_cli_returns_1_on_rejection(tmp_path: Path, capsys):
    """거부되면 1을 돌려주고 출력 파일을 만들지 않는다."""
    _write_run(tmp_path / "run", "acme/widgets", [_record("acme/widgets", 1)], [], status="RUNNING")
    out = tmp_path / "out" / "f.jsonl"

    code = assemble_module.main(["--run-dir", str(tmp_path / "run"), "--out", str(out)])

    assert code == 1
    assert "조립 거부" in capsys.readouterr().err
    assert not out.exists()


def test_cli_accepts_multiple_run_dirs(tmp_path: Path):
    """`--run-dir`를 여러 번 주면 모두 합친다."""
    _standard_run(tmp_path / "a", "acme/a")
    _standard_run(tmp_path / "b", "acme/b")
    out = tmp_path / "out" / "f.jsonl"

    code = assemble_module.main(
        ["--run-dir", str(tmp_path / "a"), "--run-dir", str(tmp_path / "b"), "--out", str(out)]
    )

    assert code == 0
    assert {row["repo"] for row in _read_jsonl(out)} == {"acme/a", "acme/b"}


# --- run.py 출력 그대로 ---------------------------------------------------------------


@requires_git
def test_assembles_real_run_batch_output(tmp_path: Path):
    """`run.run_batch`가 실제로 쓴 파일을 조립한다. 성공 1 + 실패 1(없는 clone 경로)."""
    source = _make_source(tmp_path / "source")
    settings = run_module.RunSettings(
        out_dir=tmp_path / "run", repos_dir=tmp_path / "repos", max_attempts=1, retry_delays=()
    )
    jobs = [
        run_module.RepoJob("acme/widgets", "main", str(source)),
        run_module.RepoJob("acme/missing", "main", str(tmp_path / "no-such-source")),
    ]
    summary = run_module.run_batch(jobs, settings, code_state=_CODE_STATE, log=lambda _m: None)
    assert summary["failed_count"] == 1
    out = tmp_path / "out" / "f.jsonl"

    report = assemble([settings.out_dir], out)

    rows = _read_jsonl(out)
    kept_path, excluded_path, _ = run_module.output_paths(settings.out_dir, "acme/widgets")
    kept_rows = _read_jsonl(kept_path)
    excluded_rows = _read_jsonl(excluded_path)
    assert (
        rows
        == [
            {**row, "filter_status": "KEPT", "filter_rule_version": FILTER_RULE_VERSION}
            for row in kept_rows
        ]
        + excluded_rows
    )
    assert report.filter_status_counts == {"KEPT": 2, NOISE_MOVE: 1, NOISE_TRIVIAL: 1}
    assert [item["repo"] for item in report.failed_repos] == ["acme/missing"]
