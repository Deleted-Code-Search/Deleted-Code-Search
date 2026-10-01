"""필터 정밀도 표본 추출 테스트 (이슈 #89)."""

import json
from collections import Counter

import pytest

from eval import filter_precision_sample as fps

CSV = (
    "repo,default_branch,recent_only,mining_since_year\n"
    "old/repo,main,true,2015\n"
    "new/repo,main,false,\n"
)


# id 에 상태 이름을 넣지 않는다 — 판정용 파일에 상태가 새는지 문자열로 검사한다
STATUS_CODE = {"KEPT": "k", "NOISE_MOVE": "m", "NOISE_TRIVIAL": "t"}


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
        "deletion_kind": "PARTIAL" if status == "NOISE_TRIVIAL" else "FULL_FUNCTION",
        "deleted_body": "line1\nline2\nline3",
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
    return row


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
    assembled = tmp_path / "filtered.jsonl"
    assembled.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    csv_path = tmp_path / "sel.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    return assembled, fps.load_recent_only(csv_path)


def test_same_seed_same_sample(inputs):
    assembled, recent = inputs
    first = fps.draw_sample(assembled, recent, seed=7)
    second = fps.draw_sample(assembled, recent, seed=7)
    assert first.records == second.records
    assert first.key == second.key
    other = fps.draw_sample(assembled, recent, seed=8)
    assert [r["record_id"] for r in other.records] != [r["record_id"] for r in first.records]


def test_hundred_per_stratum_and_shuffled(inputs):
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=1)
    strata = Counter(item["stratum"] for item in result.key["items"])
    assert strata == {"KEPT": 100, "EXCLUDED": 100}
    assert len(result.records) == 200
    assert len({r["record_id"] for r in result.records}) == 200
    # 섞였다: 앞 100건이 한 층으로만 채워져 있지 않다
    assert len({item["stratum"] for item in result.key["items"][:100]}) == 2
    assert [r["sample_id"] for r in result.records] == [i["sample_id"] for i in result.key["items"]]


def test_recent_only_window_applied(inputs):
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=3)
    assert result.key["population"] == {"KEPT": 161, "EXCLUDED": 160}
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
    result = fps.draw_sample(assembled, recent, seed=5)
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


def test_refuses_to_overwrite(inputs, tmp_path):
    assembled, recent = inputs
    result = fps.draw_sample(assembled, recent, seed=5)
    fps.write_outputs(result, tmp_path)
    with pytest.raises(fps.SampleError, match="--overwrite"):
        fps.write_outputs(result, tmp_path)
    fps.write_outputs(result, tmp_path, overwrite=True)


def test_too_small_population(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text(json.dumps(make_row(1, "new/repo", "KEPT")) + "\n", encoding="utf-8")
    with pytest.raises(fps.SampleError, match="표본"):
        fps.draw_sample(path, {"new/repo": False}, per_stratum=1)


def test_line_separator_inside_string_is_not_a_line_break(tmp_path):
    rows = [make_row(1, "new/repo", "KEPT"), make_row(2, "new/repo", "NOISE_MOVE")]
    rows[0]["commit_message"] = "a b"
    path = tmp_path / "a.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    result = fps.draw_sample(path, {"new/repo": False}, per_stratum=1)
    assert result.key["population"] == {"KEPT": 1, "EXCLUDED": 1}
