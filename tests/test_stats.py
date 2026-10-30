"""search.stats 집계와 search.api /api/stats (#93)."""

import csv
import gzip
import json

import pytest

from search import stats

REPO_A = "o/a"
REPO_B = "o/b"


def _record(rid, repo, kind, source, *, year="2021", label=None, grade=None, training=False):
    context = {"commit_message": "m", "pr_labels": None}
    if kind == "FULL_FUNCTION":
        context["pr_labels"] = []
    reason = None if label is None else {"label": label, "evidence_grade": grade}
    return {
        "id": rid,
        "repo": repo,
        "author_date": f"{year}-03-01T00:00:00+00:00",
        "deletion_kind": kind,
        "filter_status": "KEPT",
        "context": context,
        "reason": reason,
        "classification": {"source": source, "training_repo": training},
    }


RECORDS_A = [
    _record("a1", REPO_A, "FULL_FUNCTION", "HUMAN", label="DEAD", grade="EXPLICIT", training=True),
    _record("a2", REPO_A, "FULL_FUNCTION", "HUMAN", label="UNK", grade="UNKNOWN", training=True),
    _record(
        "a3", REPO_A, "FULL_FUNCTION", "MODEL_LLM", label="FEAT", grade="INFERRED", training=True
    ),
    _record(
        "a4", REPO_A, "FULL_FUNCTION", "MODEL_LLM", label="UNK", grade="UNKNOWN", training=True
    ),
    # 모델만 라벨은 이유 분포에 들어가면 안 된다 - 일부러 다른 데 없는 SEC 로 둔다
    _record(
        "a5", REPO_A, "FULL_FUNCTION", "MODEL_ONLY", label="SEC", grade="EXPLICIT", training=True
    ),
    _record("a6", REPO_A, "PARTIAL", "NOT_CLASSIFIED", year="2014", training=True),
]
RECORDS_B = [
    _record(
        "b1", REPO_B, "FULL_FUNCTION", "MODEL_LLM", label="DESIGN", grade="EXPLICIT", year="2023"
    ),
    _record("b2", REPO_B, "FULL_FUNCTION", "UNDISTINGUISHED", year="2023"),
    _record("b3", REPO_B, "PARTIAL", "NOT_CLASSIFIED", year="2024"),
]


def _write_gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "export"
    data.mkdir()
    _write_gz(data / "o__a-00000-of-00001.jsonl.gz", RECORDS_A)
    _write_gz(data / "o__b-00000-of-00001.jsonl.gz", RECORDS_B)
    # 확정 500건 사본 - shard 가 아니라 읽지 않는다
    _write_gz(data / "main500-v0.9.jsonl.gz", RECORDS_A[:2])

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with (dataset / "repos.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["repo", "training_repo", "records", "noise_rows"])
        writer.writerow([REPO_A, "True", 6, 10])
        writer.writerow([REPO_B, "False", 3, 5])
    (dataset / "training_repos.txt").write_text(REPO_A + "\n", encoding="utf-8")
    summary = {
        "dataset_version": "dataset-v0.9",
        "filter_rule_version": "v0.9",
        "totals": {
            "records": 9,
            "by_kind": {"FULL_FUNCTION": 7, "PARTIAL": 2},
            "by_source": {
                "HUMAN": 2,
                "MODEL_LLM": 3,
                "MODEL_ONLY": 1,
                "UNDISTINGUISHED": 1,
                "NOT_CLASSIFIED": 2,
            },
            "with_context": 7,
        },
    }
    (dataset / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    repo_list = tmp_path / "repo_final100.csv"
    repo_list.write_text(
        "repo,recent_only,mining_since_year\no/a,true,2015\no/b,false,2020\n", encoding="utf-8"
    )

    reports = tmp_path / "reports"
    reports.mkdir()
    method = {"n": 100, "correct": 60, "accuracy": 0.6}
    (reports / "gate2_test.json").write_text(
        json.dumps(
            {
                "methods": {
                    "ours": method,
                    "keyword_a": {"n": 100, "correct": 33, "accuracy": 0.33},
                    "llm_b": {"n": 100, "correct": 38, "accuracy": 0.38},
                }
            }
        ),
        encoding="utf-8",
    )
    (reports / "classifier_val.json").write_text(
        json.dumps(
            {
                "val": {"ours": {"n": 100}},
                "experiments": [{"name": stats.MODEL_ONLY_EXPERIMENT, "val_accuracy": 0.52}],
            }
        ),
        encoding="utf-8",
    )
    (reports / "filter_precision_v2.json").write_text(
        json.dumps(
            {
                "precision": 0.76,
                "precision_ci95": [0.667, 0.833],
                "sample_counts": {"KEPT": 100},
                "key": {"filter_rule_version": "v0.8"},
            }
        ),
        encoding="utf-8",
    )
    (reports / "gate2_h.json").write_text(
        json.dumps({"H": 80 / 99, "wilson95": [0.72, 0.87], "agreement": {"denominator": 99}}),
        encoding="utf-8",
    )
    return {"data": data, "dataset": dataset, "repo_list": repo_list, "reports": reports}


def _build(env, runs=()):
    totals, files = stats.scan_dir(env["data"])
    return stats.build_stats(
        totals, files, env["dataset"], env["repo_list"], env["reports"], list(runs)
    )


def test_headline_and_checks(env):
    result = _build(env)
    head = result["headline"]
    assert head["repos"] == 2
    assert head["kept"] == 9  # main500 사본은 세지 않는다
    assert head["noise_rows"] == 15
    assert head["deleted_before_filter"] == 24
    assert (head["full_function"], head["partial"], head["with_context"]) == (7, 2, 7)
    assert result["checks"] == {"ok": True, "against": "summary.json, repos.csv", "mismatches": []}


def test_sources_kept_apart_and_model_only_not_in_reason_distribution(env):
    result = _build(env)
    assert result["label_sources"]["counts"] == {
        "HUMAN": 2,
        "MODEL_LLM": 3,
        "MODEL_ONLY": 1,
        "UNDISTINGUISHED": 1,
        "NOT_CLASSIFIED": 2,
    }
    reason = result["reason_distribution"]
    assert set(stats.DISTRIBUTION_SOURCES) == {"HUMAN", "MODEL_LLM"}
    assert "MODEL_ONLY" not in reason
    assert "MODEL_ONLY" in reason["excluded_sources"]
    assert reason["HUMAN"]["n"] == 2
    assert reason["HUMAN"]["counts"]["DEAD"] == 1
    assert reason["MODEL_LLM"]["n"] == 3
    assert reason["MODEL_LLM"]["counts"]["FEAT"] == 1
    # MODEL_ONLY 의 SEC 가 어디에도 섞이지 않았다
    assert reason["HUMAN"]["counts"]["SEC"] == 0
    assert reason["MODEL_LLM"]["counts"]["SEC"] == 0

    grade = result["grade_distribution"]
    assert "MODEL_ONLY" not in grade
    assert grade["HUMAN"]["counts"] == {"EXPLICIT": 1, "INFERRED": 0, "UNKNOWN": 1}
    assert grade["MODEL_LLM"]["counts"] == {"EXPLICIT": 1, "INFERRED": 1, "UNKNOWN": 1}
    assert grade["explicit_rate_human"] == 0.5


def test_repo_rows(env):
    rows = {row["repo"]: row for row in _build(env)["repos"]["rows"]}
    a, b = rows[REPO_A], rows[REPO_B]
    assert (a["kept"], a["noise_rows"], a["training_repo"], a["recent_only"]) == (6, 10, True, True)
    # LLM 표본 2건 중 INFERRED 1건 - MODEL_ONLY 의 EXPLICIT 은 회수율에 안 들어간다
    assert (a["llm_n"], a["llm_recovered"], a["llm_recovery_rate"]) == (2, 1, 0.5)
    assert a["human_n"] == 2
    assert (b["training_repo"], b["llm_n"], b["llm_recovery_rate"]) == (False, 1, 1.0)


def test_yearly(env):
    result = _build(env)["yearly"]
    assert result["rows"] == [
        {"year": "2014", "FULL_FUNCTION": 0, "PARTIAL": 1},
        {"year": "2021", "FULL_FUNCTION": 5, "PARTIAL": 0},
        {"year": "2023", "FULL_FUNCTION": 2, "PARTIAL": 0},
        {"year": "2024", "FULL_FUNCTION": 0, "PARTIAL": 1},
    ]
    assert result["recent_only_repos"] == [REPO_A]


def test_performance_cards_from_reports(env):
    cards = {card["key"]: card for card in _build(env)["performance"]["cards"]}
    assert cards["classifier_test"]["value"] == 0.6
    assert cards["model_only_val"]["value"] == 0.52
    assert (cards["baseline_a"]["value"], cards["baseline_b"]["value"]) == (0.33, 0.38)
    assert cards["human_agreement"]["value"] == 0.8081
    assert cards["filter_precision"]["value"] == 0.76
    assert all(card["source"] for card in cards.values())


def test_missing_report_stops(env):
    (env["reports"] / "gate2_h.json").unlink()
    with pytest.raises(stats.StatsError, match="gate2_h.json"):
        _build(env)


def test_no_runs_leaves_system_empty(env):
    system = _build(env)["system"]
    assert system["available"] is False
    assert system["runs"] == []
    assert system["note"] == "실행 기록 없음"
    assert system["expected_repos"] == 2


def test_runs_latest_per_repo(env, tmp_path):
    old, new = tmp_path / "runs_old", tmp_path / "runs_new"
    old.mkdir()
    new.mkdir()

    def run(folder, repo, finished, status="SUCCESS", attempts=1, elapsed=10.0):
        meta = {
            "repo": repo,
            "status": status,
            "finished_at": finished,
            "elapsed_seconds": elapsed,
            "attempt_count": attempts,
        }
        (folder / f"{repo.replace('/', '__')}_run.json").write_text(json.dumps(meta), "utf-8")

    run(old, REPO_A, "2026-10-01T00:00:00+00:00", status="FAILED")
    run(new, REPO_A, "2026-10-02T00:00:00+00:00", attempts=2, elapsed=30.0)
    run(new, REPO_B, "2026-10-02T00:00:00+00:00", status="FAILED")
    system = _build(env, [old, new])["system"]
    assert system["available"] is True
    assert [r["repo"] for r in system["runs"]] == [REPO_A, REPO_B]
    assert system["runs"][0]["status"] == "SUCCESS"
    assert system["summary"]["failed"] == 1
    assert system["summary"]["failure_rate"] == 0.5
    assert system["summary"]["retried"] == 1


def test_mismatch_with_summary_is_reported(env):
    path = env["dataset"] / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["totals"]["records"] = 10
    path.write_text(json.dumps(summary), encoding="utf-8")
    checks = _build(env)["checks"]
    assert checks["ok"] is False
    assert checks["mismatches"][0]["field"] == "records"


def test_missing_data_dir_explains_download(tmp_path):
    with pytest.raises(stats.StatsError, match="gh release download"):
        stats.scan_dir(tmp_path / "nope")


def test_main_writes_output(env, tmp_path):
    out = tmp_path / "out" / "dashboard_stats.json"
    code = stats.main(
        [
            "--data", str(env["data"]),
            "--dataset", str(env["dataset"]),
            "--repo-list", str(env["repo_list"]),
            "--reports", str(env["reports"]),
            "--out", str(out),
            "--jobs", "1",
        ]
    )  # fmt: skip
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["headline"]["kept"] == 9
    # 레코드 원문(id 등)은 출력에 없다
    assert "a1" not in out.read_text(encoding="utf-8")


def test_api_stats(env, tmp_path, monkeypatch):
    # 스마트 앱 컨트롤이 pydantic_core DLL 을 막는 노트북에서는 건너뛴다 (컨테이너·CI 에서 돈다)
    pytest.importorskip("fastapi", exc_type=ImportError)
    from fastapi.testclient import TestClient

    from search import api

    out = tmp_path / "dashboard_stats.json"
    out.write_text(json.dumps(_build(env), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(api.STATS_ENV, str(out))
    client = TestClient(api.app)
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["stats_file"] is True
    response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json()["headline"]["kept"] == 9

    monkeypatch.setenv(api.STATS_ENV, str(tmp_path / "missing.json"))
    assert client.get("/api/stats").status_code == 503


def test_runs_median_even_count_and_null_elapsed(tmp_path):
    folder = tmp_path / "runs"
    folder.mkdir()
    for i, elapsed in enumerate([10.0, 40.0, 20.0, 30.0, None]):
        meta = {"repo": f"o/r{i}", "status": "SUCCESS", "elapsed_seconds": elapsed}
        (folder / f"o__r{i}_run.json").write_text(json.dumps(meta), "utf-8")
    system = stats.read_runs([folder])
    # 짝수 개(4)는 가운데 두 값의 평균 - 처리 시간이 없는 실행은 중앙값에서 뺀다
    assert system["summary"]["median_elapsed_seconds"] == 25.0
    assert system["summary"]["max_elapsed_seconds"] == 40.0
    # 표에는 남는다
    assert [r["elapsed_seconds"] for r in system["runs"]][-1] is None
    assert system["n"] == 5


def test_runs_median_odd_and_no_elapsed(tmp_path):
    folder = tmp_path / "runs"
    folder.mkdir()
    for i, elapsed in enumerate([5.0, 1.0, 3.0]):
        meta = {"repo": f"o/r{i}", "status": "SUCCESS", "elapsed_seconds": elapsed}
        (folder / f"o__r{i}_run.json").write_text(json.dumps(meta), "utf-8")
    assert stats.read_runs([folder])["summary"]["median_elapsed_seconds"] == 3.0

    empty = tmp_path / "no_elapsed"
    empty.mkdir()
    (empty / "o__x_run.json").write_text(
        json.dumps({"repo": "o/x", "status": "FAILED", "elapsed_seconds": None}), "utf-8"
    )
    summary = stats.read_runs([empty])["summary"]
    assert summary["median_elapsed_seconds"] is None
    assert summary["max_elapsed_seconds"] is None


def test_write_json_replaces_without_leftover(tmp_path):
    out = tmp_path / "reports" / "dashboard_stats.json"
    stats.write_json(out, {"a": 1})
    stats.write_json(out, {"a": 2})
    assert json.loads(out.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in out.parent.iterdir()] == ["dashboard_stats.json"]
