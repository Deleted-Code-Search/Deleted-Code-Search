"""필터 정밀도 표본 추출 테스트 (이슈 #89)."""

import dataclasses
import json
from collections import Counter

import pytest

from eval import filter_precision_sample as fps

CSV = (
    "repo,default_branch,recent_only,mining_since_year\n"
    "old/repo,main,true,2015\n"
    "new/repo,main,false,\n"
)
ONE_EACH = {"KEPT": 1, "NOISE_MOVE": 1, "NOISE_TRIVIAL": 1}
# 1차 층 (100/50/50). 아래 1차 테스트는 이 층으로 뽑는다
V1 = fps.ROUNDS[1].sizes


# id 에 상태 이름을 넣지 않는다 — 판정용 파일에 상태가 새는지 문자열로 검사한다
STATUS_CODE = {"KEPT": "k", "NOISE_MOVE": "m", "NOISE_TRIVIAL": "t", "NOISE_FORMAT": "f"}


def row_id(repo, status, index):
    return f"{repo}-{STATUS_CODE[status]}{index}"


def make_row(index, repo, status, author_date="2020-01-01T00:00:00Z"):
    row = {
        "id": row_id(repo, status, index),
        "repo": repo,
        "commit_sha": f"sha{index}",
        "file_path": f"pkg/mod{index}.py",
        "function_name": f"fn_{index}",
        "function_signature": f"def fn_{index}():",
        "deletion_kind": "FULL_FUNCTION" if status in ("KEPT", "NOISE_MOVE") else "PARTIAL",
        "deleted_body": "line1\nline2\nline3",
        "added_hunks_same_file": [
            {
                "old_start": 1,
                "old_count": 3,
                "new_start": 1,
                "new_count": 1,
                "added_body": f"added {index}",
            }
        ],
        "author_date": author_date,
        "commit_message": f"msg {index}",
        "source_url": f"https://github.com/{repo}/commit/sha{index}",
        "filter_status": status,
        "filter_rule_version": "v0.7",
    }
    if status == "NOISE_MOVE":
        row["filter_evidence"] = {
            "file_path": "pkg/other.py",
            "function_name": f"fn_{index}",
            "start_line": 1,
            "end_line": 3,
            "similarity": 0.95,
        }
    elif status == "NOISE_TRIVIAL":
        row["filter_evidence"] = {"line_count": 3}
    elif status == "NOISE_FORMAT":
        row["filter_evidence"] = {"keywords": ["black"], "reappear_ratio": 1.0}
    return row


def write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


@pytest.fixture
def inputs(tmp_path):
    rows = []
    for i in range(80):
        rows.append(make_row(i, "new/repo", "KEPT"))
        rows.append(make_row(i, "old/repo", "KEPT"))
        rows.append(make_row(i, "new/repo", "NOISE_MOVE"))
        rows.append(make_row(i, "old/repo", "NOISE_TRIVIAL"))
    # recent_only 저장소의 2015 이전 author_date — 모집단 밖
    for i in range(80, 130):
        rows.append(make_row(i, "old/repo", "KEPT", "2014-06-01T00:00:00Z"))
        rows.append(make_row(i, "old/repo", "NOISE_MOVE", "2014-12-31T23:59:59Z"))
    # 같은 순간의 다른 시간대 표기: 2015-01-01T00:30Z → 모집단 안
    rows.append(make_row(999, "old/repo", "KEPT", "2014-12-31T23:30:00-01:00"))
    assembled = write_rows(tmp_path / "filtered.jsonl", rows)
    csv_path = tmp_path / "sel.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    return assembled, fps.load_recent_only(csv_path)


def test_same_seed_same_sample(inputs):
    assembled, recent = inputs
    first = fps.draw_sample(assembled, recent, seed=7, sizes=V1)
    second = fps.draw_sample(assembled, recent, seed=7, sizes=V1)
    assert first.records == second.records
    assert first.key == second.key
    other = fps.draw_sample(assembled, recent, seed=8, sizes=V1)
    assert [r["record_id"] for r in other.records] != [r["record_id"] for r in first.records]


def test_stratified_counts_and_shuffled(inputs):
    """1차: 통과 100 + NOISE_MOVE 50 + NOISE_TRIVIAL 50. 층 이름은 filter_status 그대로."""
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=1, sizes=V1, round_number=1)
    strata = Counter(item["stratum"] for item in result.key["items"])
    assert strata == {"KEPT": 100, "NOISE_MOVE": 50, "NOISE_TRIVIAL": 50}
    assert all(item["stratum"] == item["filter_status"] for item in result.key["items"])
    assert result.key["sample_sizes"] == {"KEPT": 100, "NOISE_MOVE": 50, "NOISE_TRIVIAL": 50}
    assert fps.ROUNDS[1].seed == 20261001
    assert result.records[0]["sample_id"].startswith("fp-")
    assert len(result.records) == 200
    assert len({r["record_id"] for r in result.records}) == 200
    # 섞였다: 앞 100건에 세 층이 다 있다
    assert len({item["stratum"] for item in result.key["items"][:100]}) == 3
    assert [r["sample_id"] for r in result.records] == [i["sample_id"] for i in result.key["items"]]


def test_recent_only_window_applied(inputs):
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=3, sizes=V1)
    assert result.key["population"] == {"KEPT": 161, "NOISE_MOVE": 80, "NOISE_TRIVIAL": 80}
    assert result.key["out_of_window_by_repo"] == {"old/repo": 100}
    picked = {r["record_id"] for r in result.records}
    for i in range(80, 130):
        assert row_id("old/repo", "KEPT", i) not in picked
        assert row_id("old/repo", "NOISE_MOVE", i) not in picked


def test_timezone_compared_in_utc():
    recent = {"old/repo": True}
    assert fps.in_population(
        {"repo": "old/repo", "author_date": "2014-12-31T23:30:00-01:00"}, recent
    )
    assert not fps.in_population(
        {"repo": "old/repo", "author_date": "2015-01-01T00:30:00+01:00"}, recent
    )
    with pytest.raises(fps.SampleError):
        fps.in_population({"repo": "old/repo", "author_date": "2015-01-01T00:00:00"}, recent)
    with pytest.raises(fps.SampleError):
        fps.in_population({"repo": "unknown/repo"}, recent)


def test_judge_file_hides_filter_status(inputs, tmp_path):
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=5, sizes=V1)
    out = tmp_path / "out"
    records_path, key_path = fps.write_outputs(result, out)
    text = records_path.read_text(encoding="utf-8")
    for hidden in ("filter_status", "filter_rule_version", "filter_evidence", "NOISE_", "KEPT"):
        assert hidden not in text
    for line in text.splitlines():
        row = json.loads(line)
        assert tuple(row) == fps.JUDGE_FIELDS
        assert row["deleted_line_count"] == 3  # 모든 건에 같은 식으로
    moves = [r for r in result.records if r["similar_function"] is not None]
    key_moves = {i["sample_id"] for i in result.key["items"] if i["filter_status"] == "NOISE_MOVE"}
    assert {r["sample_id"] for r in moves} == key_moves
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert key["seed"] == 5 and len(key["assembled_sha256"]) == 64


def test_judge_file_has_added_hunks_for_every_stratum(inputs, tmp_path):
    """판정을 diff 로 하게 한다 (#155). 층과 무관하게 모든 건에 조립 결과 값 그대로."""
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=5, sizes=V1)
    records_path, _ = fps.write_outputs(result, tmp_path / "out")

    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    assert "added_hunks_same_file" in fps.JUDGE_FIELDS
    assert fps.HIDDEN_KEYS.isdisjoint(fps.JUDGE_FIELDS)
    for row in rows:
        index = int(row["commit_sha"].removeprefix("sha"))
        assert row["added_hunks_same_file"] == [
            {
                "old_start": 1,
                "old_count": 3,
                "new_start": 1,
                "new_count": 1,
                "added_body": f"added {index}",
            }
        ]
        assert fps.HIDDEN_KEYS.isdisjoint(row)


def test_missing_added_hunks_stays_missing():
    """조립 결과에 키가 없으면 None — 빈 목록("추가한 줄 없음")으로 바꿔 말하지 않는다."""
    row = make_row(1, "new/repo", "KEPT")
    del row["added_hunks_same_file"]

    assert fps.judge_record(row, "fp-1")["added_hunks_same_file"] is None
    assert (
        fps.judge_record(make_row(2, "new/repo", "KEPT") | {"added_hunks_same_file": []}, "fp-2")[
            "added_hunks_same_file"
        ]
        == []
    )


def test_refuses_to_overwrite(inputs, tmp_path):
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=5, sizes=V1)
    fps.write_outputs(result, tmp_path)
    with pytest.raises(fps.SampleError, match="--overwrite"):
        fps.write_outputs(result, tmp_path)
    fps.write_outputs(result, tmp_path, overwrite=True)


def test_too_small_stratum(tmp_path):
    rows = [make_row(1, "new/repo", "KEPT"), make_row(2, "new/repo", "NOISE_MOVE")]
    path = write_rows(tmp_path / "a.jsonl", rows)
    with pytest.raises(fps.SampleError, match="NOISE_TRIVIAL 모집단이 0건"):
        fps.draw_sample(path, {"new/repo": False}, sizes=ONE_EACH)


def test_unregistered_status_stops(tmp_path):
    """사전 등록 층 밖의 사유(아직 미구현인 NOISE_RENAME 등)는 조용히 빼지 않고 멈춘다."""
    rows = [make_row(i, "new/repo", status) for i, status in enumerate(ONE_EACH)]
    rows.append({**make_row(9, "new/repo", "KEPT"), "filter_status": "NOISE_RENAME"})
    path = write_rows(tmp_path / "a.jsonl", rows)
    with pytest.raises(fps.SampleError, match="NOISE_RENAME"):
        fps.draw_sample(path, {"new/repo": False}, sizes=ONE_EACH)


def test_line_separator_inside_string_is_not_a_line_break(tmp_path):
    rows = [make_row(i, "new/repo", status) for i, status in enumerate(ONE_EACH)]
    rows[0]["commit_message"] = "a b"
    path = write_rows(tmp_path / "a.jsonl", rows)
    result = fps.draw_sample(path, {"new/repo": False}, sizes=ONE_EACH)
    assert result.key["population"] == ONE_EACH


# ── 2차 재측정 (2026-10-03 사전 등록): 4층, NOISE_FORMAT, #90 500건 제외 ──


def test_round_two_registered_values():
    """2차 사전 등록 값. 1차 값은 재현용으로 그대로 남는다."""
    second = fps.ROUNDS[2]
    assert fps.CURRENT_ROUND == 2
    assert second.seed == fps.DEFAULT_SEED == 20261004
    assert dict(second.sizes) == {
        "KEPT": 100,
        "NOISE_MOVE": 34,
        "NOISE_TRIVIAL": 33,
        "NOISE_FORMAT": 33,
    }
    assert sum(second.sizes.values()) == 200
    assert fps.STRATA == ("KEPT", "NOISE_MOVE", "NOISE_TRIVIAL", "NOISE_FORMAT")
    assert second.assembled.as_posix() == "data/assembled/records_v0.8.jsonl"
    assert second.out_dir.as_posix() == "data/filter_precision_v2"
    assert second.exclude.as_posix() == "datasets/labels/main500_assignment.jsonl"
    first = fps.ROUNDS[1]
    assert first.seed == 20261001 and first.exclude is None
    assert dict(first.sizes) == {"KEPT": 100, "NOISE_MOVE": 50, "NOISE_TRIVIAL": 50}
    assert first.out_dir.as_posix() == "data/filter_precision"


def v2_rows():
    """층마다 모집단 60건(new/repo) — 4층 모두 표본보다 많다. 전부 v0.8."""
    rows = [
        make_row(i, "new/repo", status)
        for i in range(60)
        for status in ("KEPT", "NOISE_MOVE", "NOISE_TRIVIAL", "NOISE_FORMAT")
    ]
    rows += [make_row(i, "new/repo", "KEPT") for i in range(60, 120)]
    # recent_only 구간 밖 — 제외 목록에 있어도 구간 밖으로 센다
    rows.append(make_row(500, "old/repo", "NOISE_FORMAT", "2014-06-01T00:00:00Z"))
    for row in rows:
        row["filter_rule_version"] = "v0.8"
    return rows


@pytest.fixture
def v2_inputs(tmp_path):
    assembled = write_rows(tmp_path / "records_v0.8.jsonl", v2_rows())
    csv_path = tmp_path / "sel.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    return assembled, fps.load_recent_only(csv_path)


def test_four_strata_counts_with_noise_format(v2_inputs):
    """2차 기본값: KEPT 100 + NOISE_MOVE 34 + NOISE_TRIVIAL 33 + NOISE_FORMAT 33 = 200."""
    assembled, recent = v2_inputs
    result = fps.draw_sample(assembled, recent)
    strata = Counter(item["stratum"] for item in result.key["items"])
    assert strata == {"KEPT": 100, "NOISE_MOVE": 34, "NOISE_TRIVIAL": 33, "NOISE_FORMAT": 33}
    assert result.key["round"] == 2 and result.key["seed"] == 20261004
    assert result.key["population"] == {
        "KEPT": 120,
        "NOISE_MOVE": 60,
        "NOISE_TRIVIAL": 60,
        "NOISE_FORMAT": 60,
    }
    assert result.key["filter_rule_version"] == "v0.8"
    assert len({r["record_id"] for r in result.records}) == 200
    assert all(r["sample_id"].startswith("fp2-") for r in result.records)
    assert len({item["stratum"] for item in result.key["items"][:100]}) == 4


def test_noise_format_stops_round_one_sizes(v2_inputs):
    """1차 층으로 v0.8 을 읽으면 사전 등록 밖 사유(NOISE_FORMAT)에서 멈춘다."""
    assembled, recent = v2_inputs
    with pytest.raises(fps.SampleError, match="NOISE_FORMAT"):
        fps.draw_sample(assembled, recent, sizes=V1)


def test_unregistered_status_still_stops_in_round_two(tmp_path):
    rows = [make_row(i, "new/repo", s) for i, s in enumerate(fps.STRATA)]
    rows.append({**make_row(9, "new/repo", "KEPT"), "filter_status": "NOISE_RENAME"})
    path = write_rows(tmp_path / "a.jsonl", rows)
    with pytest.raises(fps.SampleError, match="NOISE_RENAME"):
        fps.draw_sample(path, {"new/repo": False}, sizes=dict.fromkeys(fps.STRATA, 1))


def test_main500_ids_are_excluded_from_population(v2_inputs):
    """제외 목록의 레코드는 뽑히지 않고 층별 모집단에서도 빠진다. 건수는 key 에 남는다."""
    assembled, recent = v2_inputs
    excluded = {row_id("new/repo", "KEPT", i) for i in range(10)}
    excluded |= {row_id("new/repo", "NOISE_FORMAT", i) for i in range(3)}
    excluded.add(row_id("old/repo", "NOISE_FORMAT", 500))  # 구간 밖
    excluded.add("not-in-assembled")
    result = fps.draw_sample(assembled, recent, exclude_ids=excluded)

    assert excluded.isdisjoint(r["record_id"] for r in result.records)
    assert result.key["population"] == {
        "KEPT": 110,
        "NOISE_MOVE": 60,
        "NOISE_TRIVIAL": 60,
        "NOISE_FORMAT": 57,
    }
    assert result.key["excluded_ids"] == {
        "count": 15,
        "in_population_by_stratum": {
            "KEPT": 10,
            "NOISE_MOVE": 0,
            "NOISE_TRIVIAL": 0,
            "NOISE_FORMAT": 3,
        },
        "out_of_window": 1,
        "not_in_assembled": 1,
    }
    assert result.key["out_of_window_by_repo"] == {"old/repo": 1}


def test_exclusion_outside_population_does_not_change_sample(v2_inputs):
    """제외 확인은 난수를 쓰지 않는다 — 모집단 밖 id 만 빼면 표본이 그대로다."""
    assembled, recent = v2_inputs
    plain = fps.draw_sample(assembled, recent)
    outside = fps.draw_sample(
        assembled, recent, exclude_ids={row_id("old/repo", "NOISE_FORMAT", 500), "nope"}
    )
    assert plain.records == outside.records


def test_load_exclude_ids_reads_assignment_records(tmp_path):
    path = tmp_path / "main500_assignment.jsonl"
    path.write_text(
        '{"record_id": "a", "batch": "main500", "labelers": ["sj", "jh"]}\n'
        '{"record_id": "b", "batch": "main500", "labelers": ["jh", "hs"]}\n',
        encoding="utf-8",
    )
    assert fps.load_exclude_ids(path) == {"a", "b"}
    path.write_text('{"batch": "main500"}\n', encoding="utf-8")
    with pytest.raises(fps.SampleError, match="record_id"):
        fps.load_exclude_ids(path)


def test_round_two_judge_file_hides_filter_status(v2_inputs, tmp_path):
    """NOISE_FORMAT 층이 있어도 판정용 파일에 필터 판정·근거가 없다. 추가 헝크는 모든 건에."""
    assembled, recent = v2_inputs
    result = fps.draw_sample(assembled, recent)
    records_path, _ = fps.write_outputs(result, tmp_path / "v2")
    text = records_path.read_text(encoding="utf-8")
    for hidden in ("filter_status", "filter_rule_version", "filter_evidence", "NOISE_", "KEPT"):
        assert hidden not in text
    for hidden in ("reappear_ratio", "keywords"):  # NOISE_FORMAT 근거
        assert hidden not in text
    for line in text.splitlines():
        row = json.loads(line)
        assert tuple(row) == fps.JUDGE_FIELDS
        assert row["added_hunks_same_file"]
    format_ids = {i["sample_id"] for i in result.key["items"] if i["stratum"] == "NOISE_FORMAT"}
    assert all(
        r["similar_function"] is None for r in result.records if r["sample_id"] in format_ids
    )


def write_exclude(path, ids):
    path.write_text("".join(json.dumps({"record_id": i}) + "\n" for i in ids), encoding="utf-8")
    return path


def run_main(tmp_path, assembled, exclude, *extra):
    out = tmp_path / "filter_precision_v2"
    argv = ["--assembled", str(assembled), "--selection-csv", str(tmp_path / "sel.csv")]
    argv += ["--out-dir", str(out), "--exclude", str(exclude), *extra]
    return fps.main(argv), out


@pytest.fixture
def expect_exclude(monkeypatch):
    """2차 사전 등록의 제외 목록 크기(500)를 테스트 크기로 바꾼다."""

    def set_count(count):
        plan = dataclasses.replace(fps.ROUNDS[2], exclude_count=count)
        monkeypatch.setitem(fps.ROUNDS, 2, plan)

    return set_count


def test_main_round_two_writes_v2_outputs(v2_inputs, tmp_path, expect_exclude):
    assembled, _ = v2_inputs
    expect_exclude(1)
    exclude = write_exclude(tmp_path / "ex.jsonl", [row_id("new/repo", "KEPT", 0)])
    code, out = run_main(tmp_path, assembled, exclude)
    assert code == 0
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    assert key["round"] == 2 and key["seed"] == 20261004
    assert key["excluded_ids"]["source"] == str(exclude)
    assert key["excluded_ids"]["in_population_by_stratum"]["KEPT"] == 1
    assert key["population"]["KEPT"] == 119
    assert sum(key["sample_sizes"].values()) == 200


def test_round_two_registers_500_exclusions():
    assert fps.ROUNDS[2].exclude_count == 500
    assert fps.ROUNDS[1].exclude_count is None


def test_main_round_two_stops_on_exclude_count_mismatch(v2_inputs, tmp_path):
    """사전 등록은 500개 — 다른 개수면 조립 결과를 훑기 전에 멈추고 아무것도 쓰지 않는다."""
    assembled, _ = v2_inputs
    exclude = write_exclude(tmp_path / "ex.jsonl", [row_id("new/repo", "KEPT", 0)])
    code, out = run_main(tmp_path, assembled, exclude)
    assert code == 1
    assert not out.exists()


def test_exclude_count_counts_unique_ids():
    fps.check_exclude_count({"a", "b"}, 2)
    with pytest.raises(fps.SampleError, match="고유 record_id 가 1개 — 2개"):
        fps.check_exclude_count(frozenset(["a", "a"]), 2)


def test_main_round_two_stops_on_id_missing_from_assembled(v2_inputs, tmp_path, expect_exclude):
    assembled, _ = v2_inputs
    expect_exclude(2)
    exclude = write_exclude(tmp_path / "ex.jsonl", [row_id("new/repo", "KEPT", 0), "missing"])
    code, out = run_main(tmp_path, assembled, exclude)
    assert code == 1
    assert not (out / "records.jsonl").exists() and not (out / "key.json").exists()


def test_main_round_two_allows_out_of_window_ids(v2_inputs, tmp_path, expect_exclude):
    """구간 밖 id 는 조립 결과에 있으므로 통과. 모집단 안 제외 건수는 목록 크기와 달라도 된다."""
    assembled, _ = v2_inputs
    expect_exclude(2)
    ids = [row_id("new/repo", "KEPT", 0), row_id("old/repo", "NOISE_FORMAT", 500)]
    code, out = run_main(tmp_path, assembled, write_exclude(tmp_path / "ex.jsonl", ids))
    assert code == 0
    excluded = json.loads((out / "key.json").read_text(encoding="utf-8"))["excluded_ids"]
    assert sum(excluded["in_population_by_stratum"].values()) == 1
    assert excluded["out_of_window"] == 1 and excluded["not_in_assembled"] == 0


def test_main_round_one_skips_exclusion_checks(inputs, tmp_path):
    """1차 실행에는 제외 목록 검증을 적용하지 않는다."""
    assembled, _ = inputs
    exclude = write_exclude(tmp_path / "ex.jsonl", ["missing"])
    code, out = run_main(tmp_path, assembled, exclude, "--round", "1")
    assert code == 0
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    assert key["round"] == 1 and key["excluded_ids"]["not_in_assembled"] == 1
