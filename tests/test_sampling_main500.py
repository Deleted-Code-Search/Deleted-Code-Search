"""본 라벨링 500건 추출 테스트 (#85)."""

import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from classify import sampling
from classify import sampling_main500 as m5

SINCE = datetime(2015, 1, 1, tzinfo=UTC)


def record(index, repo="a/b", **overrides):
    """맥락까지 붙은 조립 결과 한 줄 (KEPT·FULL_FUNCTION)."""
    row = {
        "id": f"{repo}-{index:04d}",
        "repo": repo,
        "commit_sha": f"sha{index}",
        "file_path": f"src/m{index}.py",
        "function_name": f"fn_{index}",
        "function_signature": f"def fn_{index}():",
        "deleted_body": f"def fn_{index}():\n    pass\n",
        "added_hunks_same_file": [{"old_start": 1, "added_body": "def g(): ..."}],
        "deletion_kind": "FULL_FUNCTION",
        "filter_status": "KEPT",
        "author_date": "2020-01-01T00:00:00Z",
        "is_test_code": False,
        "source_url": f"https://github.com/{repo}/commit/sha{index}",
        "context": {"commit_message": "remove fn"},
        "replacement": {"code": None, "match_method": "NONE", "confidence": 0.0},
    }
    row.update(overrides)
    return row


def lines(rows):
    """`scan_candidates`·`fetch_records` 가 받는 줄들 - 파이프라인이 쓰는 모양 그대로."""
    return iter(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def test_equal_allocation_gives_25_each_and_spreads_what_short_repos_cannot_take():
    """저장소마다 25건, 모자라면 남는 몫을 나머지에 고르게 (조장 결정)."""
    counts = {f"r{i:02d}": 1000 for i in range(20)}
    assert set(m5.allocate_equal(counts, 500).values()) == {25}

    counts["r00"], counts["r01"] = 10, 0
    quota = m5.allocate_equal(counts, 500)

    assert quota["r00"] == 10 and "r01" not in quota
    assert sum(quota.values()) == 500
    assert max(quota.values()) - min(v for repo, v in quota.items() if repo != "r00") <= 1


def test_scan_counts_why_each_target_was_left_out():
    """recent_only 구간 밖·예비 200건 겹침(그중 합의)·맥락 없음을 따로 센다."""
    keep, old = record(1, repo="dj/dj"), record(2, repo="dj/dj", author_date="2014-12-31T23:00:00Z")
    overlap, consensus = record(3), record(4)
    no_context = record(5, context=None)
    partial = record(6, deletion_kind="PARTIAL")
    excluded = {
        m5.overlap_key(r["commit_sha"], r["file_path"], r["function_name"])
        for r in (overlap, consensus)
    }
    agreed = {
        m5.overlap_key(consensus["commit_sha"], consensus["file_path"], consensus["function_name"])
    }

    found, stats = m5.scan_candidates(
        lines([keep, old, overlap, consensus, no_context, partial]),
        {"dj/dj": SINCE},
        excluded,
        agreed,
    )

    assert found == [m5.Candidate("dj/dj-0001", "dj/dj")]
    assert (stats["eligible"], stats["before_window"], stats["missing_context"]) == (5, 1, 1)
    assert (stats["pre200_overlap"], stats["pre200_keys"], stats["consensus_keys"]) == (2, 2, 1)


def test_one_pre200_key_can_exclude_several_same_name_functions():
    """같은 파일에서 `__init__` 이 둘 지워지면 키 하나에 행 둘 - 둘 다 빼고, 키는 하나로 센다."""
    first = record(1, function_name="__init__")
    second = record(2, function_name="__init__", commit_sha="sha1", file_path="src/m1.py")
    key = m5.overlap_key("sha1", "src/m1.py", "__init__")

    found, stats = m5.scan_candidates(lines([first, second]), {}, {key}, {key})

    assert found == []
    assert (stats["pre200_overlap"], stats["pre200_keys"], stats["consensus_keys"]) == (2, 1, 1)


@pytest.mark.parametrize(
    ("value", "inside"),
    [
        ("2015-01-01T00:00:00Z", True),
        ("2014-12-31T23:59:59Z", False),
        ("2014-12-31T20:00:00-05:00", True),  # UTC 로는 2015-01-01 01:00
        ("not a date", False),
    ],
)
def test_window_is_compared_in_utc(value, inside):
    """구간은 UTC 기준. 날짜를 읽을 수 없으면 구간 밖으로 본다."""
    assert m5.in_window(value, SINCE) is inside


def _main_sample(seed=m5.DEFAULT_SEED):
    """20개 저장소 × 30건에서 500건을 뽑아 블록·분할까지."""
    candidates = [
        m5.Candidate(f"r{repo:02d}-{i:02d}", f"r{repo:02d}")
        for repo in range(20)
        for i in range(30)
    ]
    sample = m5.sample_equal(candidates, m5.SAMPLE_SIZE, seed)
    repo_of = {c.record_id: c.repo for c in sample}
    assignments = sampling.assign_blocks([c.record_id for c in sample])
    return sample, repo_of, assignments, m5.assign_splits(assignments, repo_of, m5.SPLITS, seed)


def test_sample_is_reproducible_and_equal_per_repo():
    """같은 시드면 같은 500건, 저장소마다 25건."""
    first, *_ = _main_sample()
    second, *_ = _main_sample()

    assert first == second
    assert set(Counter(c.repo for c in first).values()) == {25}


def test_test_and_val_are_spread_evenly_over_repos_and_blocks():
    """조장 결정 - test 100 은 블록·저장소에 고르게. 25건씩이면 저장소마다 정확히 5건."""
    _sample, repo_of, assignments, split_of = _main_sample()

    assert Counter(split_of.values()) == {"train": 300, "val": 100, "test": 100}
    for split in ("test", "val"):
        per_repo = Counter(repo_of[rid] for rid, s in split_of.items() if s == split)
        assert set(per_repo.values()) == {5}
        per_block = Counter(
            a.block for a in assignments for rid in a.record_ids if split_of[rid] == split
        )
        assert max(per_block.values()) - min(per_block.values()) <= 1


def test_each_labeler_file_starts_with_the_first_50_of_both_blocks():
    """가이드 §8.4.1 - 블록마다 처음 50건을 두 사람이 끝내야 중간 점검을 한다."""
    _sample, _repo_of, assignments, _split_of = _main_sample()
    by_block = {a.block: a.record_ids for a in assignments}

    rows = sampling.label_rows_by_labeler(assignments, m5.INTERIM_UNIT)
    hs = [row["record_id"] for row in rows["hs"]]  # 블록 B·C

    assert hs[:100] == by_block["B"][:50] + by_block["C"][:50]
    assert sorted(hs) == sorted(by_block["B"] + by_block["C"])


def test_pre200_keys_read_the_commit_from_the_source_url(tmp_path):
    """예비 200건 레코드 파일에는 `commit_sha` 가 없다 - `source_url` 끝에서 읽는다."""
    agreed, split = (
        {"labels": [{"reason_label": "BUG"}, {"reason_label": "BUG"}]},
        {"labels": [{"reason_label": "BUG"}, {"reason_label": "DEAD"}]},
    )
    (tmp_path / sampling.RECORDS_FILENAME).write_text(
        "".join(
            json.dumps({**sampling.build_labeling_record(record(i)), "record_id": f"p{i}"}) + "\n"
            for i in (1, 2)
        ),
        encoding="utf-8",
    )
    (tmp_path / m5.PRE200_MERGED).write_text(
        json.dumps({"record_id": "p1", **agreed})
        + "\n"
        + json.dumps({"record_id": "p2", **split})
        + "\n",
        encoding="utf-8",
    )

    keys, consensus = m5.pre200_keys(tmp_path)

    assert keys == {("sha1", "src/m1.py", "fn_1"), ("sha2", "src/m2.py", "fn_2")}
    assert consensus == {("sha1", "src/m1.py", "fn_1")}


def test_labeler_records_carry_the_added_hunks():
    """#89 1차 판정 때 "대신 들어간 코드" 가 안 보였다 - 라벨러 레코드에 담는다 (성제 요청)."""
    built = sampling.build_labeling_record(record(1))

    assert built["added_hunks_same_file"] == [{"old_start": 1, "added_body": "def g(): ..."}]


def test_main_writes_records_labels_and_assignment(tmp_path, capsys):
    """끝까지: 600건 입력 → 500건 레코드·빈 틀 3개·배분 파일. 이미 있으면 덮어쓰지 않는다."""
    rows = [record(i, repo=f"o/r{repo:02d}") for repo in range(20) for i in range(30)]
    source = tmp_path / "records.jsonl"
    source.write_text("".join(lines(rows)), encoding="utf-8")
    pre200 = tmp_path / "pre200"
    pre200.mkdir()
    (pre200 / sampling.RECORDS_FILENAME).write_text("", encoding="utf-8")
    (pre200 / m5.PRE200_MERGED).write_text("", encoding="utf-8")
    csv_path = tmp_path / "repos.csv"
    csv_path.write_text("repo,recent_only,mining_since_year\no/r00,true,2015\n", encoding="utf-8")
    out = tmp_path / "out"
    argv = ["--input", str(source), "--out-dir", str(out), "--pre200-dir", str(pre200)]
    argv += ["--repos-csv", str(csv_path)]

    assert m5.main(argv) == 0

    def read(name):
        return [json.loads(line) for line in (out / name).read_text("utf-8").splitlines()]

    assignment = read(m5.ASSIGNMENT_OUT)
    assert len(read(m5.RECORDS_OUT)) == len(assignment) == 500
    assert {len(read(m5.LABEL_OUT_TEMPLATE.format(labeler=x))) for x in "sj jh hs".split()} == {
        333,
        334,
    }
    assert sum(row["interim"] for row in assignment) == 150
    assert "reason" not in read(m5.RECORDS_OUT)[0]
    assert m5.main(argv) == 2  # 라벨이 채워졌을 수 있는 파일은 --force 로만
    assert "이미 있는" in capsys.readouterr().err
