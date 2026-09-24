"""예비 200건 샘플 추출·배분 테스트 (이슈 #33).

네트워크·DB 없이 픽스처로만 본다. #5 출력이 아직 없어 §4.4 스키마 형식의 가짜 레코드를 쓴다.
"""

import json
import pathlib
import re

import pytest

from classify import sampling


def make_record(index, repo="a/b", **overrides):
    """§4.4 DeletionRecord 모양의 최소 레코드."""
    record = {
        "id": f"{repo}-{index:04d}",
        "repo": repo,
        "repo_license": "MIT",
        "commit_sha": f"sha{index}",
        "file_path": f"src/mod{index}.py",
        "function_name": f"fn_{index}",
        "function_signature": f"def fn_{index}():",
        "deleted_body": f"def fn_{index}():\n    return {index}\n",
        "deleted_body_normalized": "def f():\n    return N\n",
        "deletion_kind": "FULL_FUNCTION",
        "is_test_code": False,
        "filter_status": "KEPT",
        "filter_rule_version": "v0",
        "context": {
            "commit_message": f"remove fn_{index}",
            "pr_number": 10 + index,
            "pr_title": "cleanup",
            "pr_body": "body",
            "issue_numbers": [1],
            "issue_titles": ["bug"],
            "review_comments": ["why?"],
        },
        "replacement": {"code": "new()", "match_method": "SAME_LOCATION", "confidence": 0.8},
        "reason": {
            "label": "BUG",
            "evidence_grade": "EXPLICIT",
            "evidence_text": "분류기가 쓴 값",
            "confidence": 0.9,
            "classifier_version": "v0",
        },
        "embedding": [0.1, 0.2],
        "source_url": f"https://github.com/{repo}/commit/sha{index}",
    }
    record.update(overrides)
    return record


def make_population(spec):
    """{repo: 건수} → 레코드 목록."""
    records = []
    for repo, count in spec.items():
        records.extend(make_record(i, repo=repo) for i in range(count))
    return records


# --------------------------------------------------------------------------------------
# 대상 선별 (ADR-003)
# --------------------------------------------------------------------------------------


def test_only_full_function_and_kept_records_are_eligible():
    assert sampling.is_eligible(make_record(1)) is True
    assert sampling.is_eligible(make_record(1, deletion_kind="PARTIAL")) is False
    assert sampling.is_eligible(make_record(1, filter_status="NOISE_MOVE")) is False


def test_load_records_skips_blank_lines():
    lines = [json.dumps({"id": "x"}), "", "  ", json.dumps({"id": "y"})]
    assert [r["id"] for r in sampling.load_records(lines)] == ["x", "y"]


# --------------------------------------------------------------------------------------
# 배분 (비례 + 최소 1건)
# --------------------------------------------------------------------------------------


def test_allocation_totals_match_the_requested_size():
    quota = sampling.allocate({"a": 500, "b": 300, "c": 200}, 200)
    assert sum(quota.values()) == 200


def test_allocation_is_proportional_to_population():
    quota = sampling.allocate({"big": 800, "small": 200}, 100)
    assert quota["big"] > quota["small"]
    assert abs(quota["big"] - 80) <= 2


def test_every_repo_with_records_gets_at_least_one():
    quota = sampling.allocate({"huge": 10_000, "tiny": 1}, 200)
    assert quota["tiny"] == 1
    assert sum(quota.values()) == 200


def test_allocation_never_exceeds_available_records():
    quota = sampling.allocate({"a": 3, "b": 5}, 200)
    assert quota == {"a": 3, "b": 5}


def test_allocation_when_repos_outnumber_the_sample_size():
    quota = sampling.allocate({f"r{i}": 10 for i in range(10)}, 4)
    assert sum(quota.values()) == 4
    assert all(count == 1 for count in quota.values())


def test_allocation_ignores_empty_repos():
    assert "empty" not in sampling.allocate({"a": 10, "empty": 0}, 5)


# --------------------------------------------------------------------------------------
# 층화 샘플링 (시드 고정 재현 — #33 완료 조건)
# --------------------------------------------------------------------------------------


def test_same_seed_gives_the_same_sample():
    population = make_population({"a/b": 300, "c/d": 200})
    first = sampling.stratified_sample(population, 100, seed=7)
    second = sampling.stratified_sample(population, 100, seed=7)

    assert [r["id"] for r in first] == [r["id"] for r in second]


def test_different_seed_gives_a_different_sample():
    population = make_population({"a/b": 300, "c/d": 200})
    first = sampling.stratified_sample(population, 100, seed=7)
    second = sampling.stratified_sample(population, 100, seed=8)

    assert [r["id"] for r in first] != [r["id"] for r in second]


def test_sample_size_and_stratification():
    population = make_population({"a/b": 400, "c/d": 100})
    sample = sampling.stratified_sample(population, 200, seed=1)

    assert len(sample) == 200
    by_repo = {}
    for record in sample:
        by_repo[record["repo"]] = by_repo.get(record["repo"], 0) + 1
    assert set(by_repo) == {"a/b", "c/d"}
    assert by_repo["a/b"] > by_repo["c/d"]  # 모집단 비율을 따른다


def test_sample_has_no_duplicates():
    population = make_population({"a/b": 300})
    sample = sampling.stratified_sample(population, 150, seed=3)
    ids = [record["id"] for record in sample]

    assert len(set(ids)) == len(ids)


def test_sample_is_shuffled_so_blocks_do_not_split_by_repo():
    """저장소별로 묶인 채 3등분하면 블록마다 난이도가 갈려 쌍별 kappa 를 비교할 수 없다."""
    population = make_population({"a/b": 300, "c/d": 300, "e/f": 300})
    sample = sampling.stratified_sample(population, 210, seed=5)
    blocks = sampling.assign_blocks([r["id"] for r in sample])

    by_id = {r["id"]: r["repo"] for r in sample}
    for block in blocks:
        repos = {by_id[record_id] for record_id in block.record_ids}
        assert len(repos) == 3, f"블록 {block.block} 에 저장소가 {len(repos)}개뿐이다"


# --------------------------------------------------------------------------------------
# 라벨링 파일 — reason.* 유출 금지 (라벨 가이드 §2.2, 완료 조건)
# --------------------------------------------------------------------------------------


def test_labeling_record_never_contains_reason_or_embedding():
    built = sampling.build_labeling_record(make_record(1))

    assert "reason" not in built
    assert "embedding" not in built
    assert "deleted_body_normalized" not in built
    assert "분류기가 쓴 값" not in json.dumps(built, ensure_ascii=False)


def test_labeling_record_has_exactly_the_guide_fields():
    """담는 키의 *구조*만 본다 - 화이트리스트를 되읽으므로 어떤 필드가 있어야 하는지는
    보지 못한다. 그건 아래 테스트가 이름을 박아서 따로 본다 (#99)."""
    built = sampling.build_labeling_record(make_record(1))

    assert set(built) == {"record_id", *sampling.LABELER_FIELDS, "replacement", "context"}
    assert set(built["replacement"]) == set(sampling.LABELER_REPLACEMENT_FIELDS)
    assert set(built["context"]) == set(sampling.LABELER_CONTEXT_FIELDS)


def test_labelers_can_see_the_numbers_that_evidence_locator_needs():
    """가이드 §7.2 로케이터가 `pr:#10623#body` 형식이라 번호 없이는 채울 수 없다 (#99).

    예비 200건에서 이 둘이 화이트리스트에서 빠져 있어, 라벨러가 커밋 메시지 끝의 `(#10623)`
    에서 번호를 주워 썼고 출처와 로케이터가 어긋난 건이 나왔다 (PR #73). 상수를 비교하는
    위 테스트는 화이트리스트를 그대로 되읽어서 이걸 못 잡는다 - 이름을 직접 박아 둔다.
    """
    context = sampling.build_labeling_record(make_record(1))["context"]

    assert context["pr_number"] == 11
    assert context["issue_numbers"] == [1]


def test_labelers_can_see_the_replacement_confidence():
    """가이드 §6.2.2 근거 ① 구간 상한이 이 값에 기댄다 (#117).

    안 보이면 모든 ① 이 0.8 미만으로 묶인다. 위 구조 테스트는 화이트리스트를 되읽어서
    빠져도 못 잡는다. 이름을 박아 둔다.
    """
    replacement = sampling.build_labeling_record(make_record(1))["replacement"]

    assert replacement["confidence"] == 0.8


def test_guide_version_constant_matches_the_guide_document():
    """문서 버전과 상수가 갈리면 라벨이 틀린 `guide_version` 을 달고 저장된다 (#99).

    가이드 §10.3(재검토 범위)과 §8.4.2.1(버전이 같은 쌍만 그 버전 kappa 에)이 이 값에
    기대므로, 갈려도 라벨링은 멀쩡히 돌아가고 집계만 조용히 틀린다. v1 -> v2 때 실제로
    갈렸다. 사람이 기억하는 대신 여기서 깨지게 한다.
    """
    guide = pathlib.Path(__file__).resolve().parents[1] / "docs" / "labeling_guide.md"
    documented = re.search(r"`guide_version:\s*(v\d+)`", guide.read_text(encoding="utf-8"))

    assert documented is not None, "가이드 상단에서 `guide_version: vN` 을 찾지 못했다"
    assert sampling.GUIDE_VERSION == documented.group(1)


def test_unknown_pipeline_fields_do_not_leak_through():
    """화이트리스트라서 #5 가 필드를 추가해도 라벨러에게 새지 않는다."""
    built = sampling.build_labeling_record(make_record(1, secret_score=0.99))

    assert "secret_score" not in built


def test_issue_bodies_and_pr_labels_are_shown_by_default():
    """ADR-018 로 §4.4 `context` 에 들어와 옵션 없이 보인다 (#112).

    이슈는 예비 200건에서 3건 중 1건만 붙었는데 그중 제목만 보이고 있었다. 이름을 직접
    박는 이유는 화이트리스트를 되읽는 구조 테스트가 필드 누락을 못 잡기 때문이다 (#99).
    """
    record = make_record(1)
    record["context"]["issue_bodies"] = ["본문"]
    record["context"]["pr_labels"] = ["bug"]

    context = sampling.build_labeling_record(record)["context"]
    assert context["issue_bodies"] == ["본문"]
    assert context["pr_labels"] == ["bug"]


def test_old_extra_context_flag_changes_nothing():
    """옛 옵션은 `tools/label_cli.py` 호환으로만 남았다 - 켜도 결과가 같다."""
    record = make_record(1)
    record["context"]["issue_bodies"] = ["본문"]

    assert sampling.build_labeling_record(record, with_extra_context=True) == (
        sampling.build_labeling_record(record)
    )


def test_record_id_comes_from_the_schema_id_field():
    """§4.4 는 `id`, 라벨 파일은 `record_id`. 이름이 달라 그냥 복사하면 None 이 된다."""
    built = sampling.build_labeling_record(make_record(1, id="uuid-1234"))

    assert built["record_id"] == "uuid-1234"
    assert "id" not in built


def test_labeling_record_keeps_the_fields_labelers_need():
    built = sampling.build_labeling_record(make_record(7, repo="x/y"))

    assert built["record_id"] == "x/y-0007"
    assert built["deleted_body"].startswith("def fn_7")
    assert built["replacement"]["match_method"] == "SAME_LOCATION"
    assert built["context"]["commit_message"] == "remove fn_7"
    assert built["source_url"].endswith("/commit/sha7")


# --------------------------------------------------------------------------------------
# 개인 라벨 빈 틀 (라벨 가이드 §7.2)
# --------------------------------------------------------------------------------------


def test_empty_label_row_matches_guide_fields():
    row = sampling.empty_label_row("rec-1", "hs")

    assert list(row) == [
        "record_id",
        "labeler",
        "reason_label",
        "evidence_grade",
        "evidence_text",
        "evidence_source",
        "evidence_locator",
        "confidence",
        "note",
        "labeled_at",
        "guide_version",
    ]
    assert row["labeler"] == "hs"
    assert row["guide_version"] == sampling.GUIDE_VERSION


def test_empty_label_row_leaves_label_values_blank():
    row = sampling.empty_label_row("rec-1", "sj")

    assert row["reason_label"] is None
    assert row["evidence_grade"] is None
    assert row["confidence"] is None
    assert row["labeled_at"] is None  # 사람이 라벨할 때 채운다


# --------------------------------------------------------------------------------------
# 블록 배분 (라벨 가이드 §8.1)
# --------------------------------------------------------------------------------------


def test_two_hundred_records_split_into_67_67_66():
    blocks = sampling.assign_blocks([f"r{i}" for i in range(200)])

    assert [len(block.record_ids) for block in blocks] == [67, 67, 66]
    assert [block.block for block in blocks] == ["A", "B", "C"]


def test_block_pairs_rotate_so_every_pair_appears_once():
    blocks = sampling.assign_blocks([f"r{i}" for i in range(200)])
    pairs = [block.labelers for block in blocks]

    assert pairs == [("sj", "jh"), ("jh", "hs"), ("hs", "sj")]
    assert len({frozenset(pair) for pair in pairs}) == 3


def test_blocks_cover_every_record_exactly_once():
    ids = [f"r{i}" for i in range(200)]
    blocks = sampling.assign_blocks(ids)
    covered = [record_id for block in blocks for record_id in block.record_ids]

    assert sorted(covered) == sorted(ids)


def test_every_record_is_labelled_by_exactly_two_people():
    """§10.2 가 "각 건 2인 이상"을 요구한다."""
    ids = [f"r{i}" for i in range(200)]
    rows = sampling.label_rows_by_labeler(sampling.assign_blocks(ids))

    seen: dict[str, set[str]] = {}
    for labeler, labeler_rows in rows.items():
        for row in labeler_rows:
            seen.setdefault(row["record_id"], set()).add(labeler)

    assert len(seen) == 200
    assert all(len(labelers) == 2 for labelers in seen.values())


def test_each_labeler_takes_two_blocks():
    ids = [f"r{i}" for i in range(200)]
    rows = sampling.label_rows_by_labeler(sampling.assign_blocks(ids))

    assert set(rows) == set(sampling.LABELERS)
    # 200건 × 2인 = 400건을 셋이 나눠 가진다
    assert sum(len(v) for v in rows.values()) == 400
    for labeler, labeler_rows in rows.items():
        assert 130 <= len(labeler_rows) <= 136, f"{labeler}: {len(labeler_rows)}건"


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


@pytest.fixture
def input_file(tmp_path):
    population = make_population({"a/b": 200, "c/d": 120, "e/f": 60})
    population.append(make_record(999, repo="a/b", deletion_kind="PARTIAL"))
    population.append(make_record(998, repo="a/b", filter_status="NOISE_MOVE"))
    path = tmp_path / "deletions.jsonl"
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in population),
        encoding="utf-8",
    )
    return path


def test_cli_writes_records_and_three_label_files(tmp_path, input_file, capsys):
    out_dir = tmp_path / "labels"
    exit_code = sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)])
    assert exit_code == 0

    records_path = out_dir / sampling.RECORDS_FILENAME
    assert records_path.exists()
    records = [json.loads(line) for line in records_path.read_text("utf-8").splitlines()]
    assert len(records) == sampling.SAMPLE_SIZE
    assert all("reason" not in record for record in records)

    for labeler in sampling.LABELERS:
        path = out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        assert path.exists(), path
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        assert all(row["labeler"] == labeler for row in rows)
        assert all(row["reason_label"] is None for row in rows)


def test_cli_label_files_reference_only_sampled_records(tmp_path, input_file):
    out_dir = tmp_path / "labels"
    sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)])

    records_path = out_dir / sampling.RECORDS_FILENAME
    sampled_ids = {
        json.loads(line)["record_id"] for line in records_path.read_text("utf-8").splitlines()
    }
    for labeler in sampling.LABELERS:
        path = out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)
        for line in path.read_text("utf-8").splitlines():
            assert json.loads(line)["record_id"] in sampled_ids


def test_cli_dry_run_writes_nothing(tmp_path, input_file):
    out_dir = tmp_path / "labels"
    exit_code = sampling.main(["--input", str(input_file), "--out-dir", str(out_dir), "--dry-run"])

    assert exit_code == 0
    assert not out_dir.exists()


def test_cli_is_reproducible_across_runs(tmp_path, input_file):
    first, second = tmp_path / "a", tmp_path / "b"
    sampling.main(["--input", str(input_file), "--out-dir", str(first)])
    sampling.main(["--input", str(input_file), "--out-dir", str(second)])

    assert (first / sampling.RECORDS_FILENAME).read_text("utf-8") == (
        second / sampling.RECORDS_FILENAME
    ).read_text("utf-8")


# --------------------------------------------------------------------------------------
# 기존 라벨 보호 (사람이 채운 값을 덮어쓰지 않는다)
# --------------------------------------------------------------------------------------


def fill_one_label(path):
    """사람이 라벨 한 건을 채운 상태를 만든다."""
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    rows[0]["reason_label"] = "BUG"
    rows[0]["evidence_text"] = "사람이 3분 들여 쓴 근거"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )


def test_rerun_refuses_to_overwrite_existing_label_files(tmp_path, input_file):
    """재실행 한 번으로 3인 20시간이 날아가면 안 된다 (가이드 §8.1)."""
    out_dir = tmp_path / "labels"
    assert sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)]) == 0

    label_path = out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler="hs")
    fill_one_label(label_path)

    exit_code = sampling.main(
        ["--input", str(input_file), "--out-dir", str(out_dir), "--seed", "999"]
    )

    assert exit_code == 2
    kept = json.loads(label_path.read_text("utf-8").splitlines()[0])
    assert kept["reason_label"] == "BUG"
    assert kept["evidence_text"] == "사람이 3분 들여 쓴 근거"


def test_force_allows_deliberate_overwrite(tmp_path, input_file):
    out_dir = tmp_path / "labels"
    sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)])
    fill_one_label(out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler="hs"))

    exit_code = sampling.main(["--input", str(input_file), "--out-dir", str(out_dir), "--force"])

    assert exit_code == 0
    first = json.loads(
        (out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler="hs"))
        .read_text("utf-8")
        .splitlines()[0]
    )
    assert first["reason_label"] is None


def test_refusal_happens_before_any_file_is_touched(tmp_path, input_file):
    """레코드 파일만 있어도 멈춘다. 반쯤 쓰고 멈추면 더 헷갈린다."""
    out_dir = tmp_path / "labels"
    sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)])
    for labeler in sampling.LABELERS:
        (out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler=labeler)).unlink()
    records_path = out_dir / sampling.RECORDS_FILENAME
    before = records_path.read_text("utf-8")

    assert sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)]) == 2
    assert records_path.read_text("utf-8") == before
    assert not (out_dir / sampling.LABEL_FILENAME_TEMPLATE.format(labeler="hs")).exists()


def test_dry_run_is_allowed_even_when_outputs_exist(tmp_path, input_file):
    out_dir = tmp_path / "labels"
    sampling.main(["--input", str(input_file), "--out-dir", str(out_dir)])

    assert sampling.main(["--input", str(input_file), "--out-dir", str(out_dir), "--dry-run"]) == 0


def test_write_jsonl_refuses_existing_file_by_default(tmp_path):
    path = tmp_path / "x.jsonl"
    sampling.write_jsonl(path, [{"a": 1}])

    with pytest.raises(FileExistsError):
        sampling.write_jsonl(path, [{"a": 2}])

    assert sampling.write_jsonl(path, [{"a": 2}], overwrite=True) == 1


# --------------------------------------------------------------------------------------
# id 계약 (라벨 가이드 §7.2 — 라벨과 레코드를 잇는 유일 키)
# --------------------------------------------------------------------------------------


def test_record_id_is_read_through_one_path():
    """공용 레코드 파일과 개인 빈 틀이 같은 값을 써야 한다."""
    record = make_record(1, id="uuid-1")

    assert sampling.build_labeling_record(record)["record_id"] == sampling.record_id_of(record)


@pytest.mark.parametrize("bad", [{"id": None}, {"id": ""}, {"id": "   "}])
def test_blank_ids_are_reported(bad):
    problems = sampling.find_id_problems([make_record(1) | bad])

    assert problems
    assert "비어 있다" in problems[0]


def test_missing_id_key_is_reported():
    record = make_record(1)
    del record["id"]

    assert sampling.find_id_problems([record])


def test_duplicate_ids_are_reported():
    records = [make_record(1, id="same"), make_record(2, id="same")]
    problems = sampling.find_id_problems(records)

    assert any("중복" in problem for problem in problems)


def test_valid_ids_have_no_problems():
    assert sampling.find_id_problems([make_record(i) for i in range(5)]) == []


def test_cli_stops_when_ids_are_missing(tmp_path):
    """라벨링을 시작한 뒤에는 되돌릴 수 없으므로 추출 전에 멈춘다."""
    records = [make_record(i) for i in range(5)]
    for record in records:
        del record["id"]
    path = tmp_path / "bad.jsonl"
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8"
    )
    out_dir = tmp_path / "labels"

    assert sampling.main(["--input", str(path), "--out-dir", str(out_dir)]) == 2
    assert not out_dir.exists()


def test_cli_stops_when_ids_collide(tmp_path):
    records = [make_record(i, id="dup") for i in range(5)]
    path = tmp_path / "dup.jsonl"
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8"
    )
    out_dir = tmp_path / "labels"

    assert sampling.main(["--input", str(path), "--out-dir", str(out_dir)]) == 2
    assert not out_dir.exists()


def test_cli_reports_when_nothing_is_eligible(tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps(make_record(1, deletion_kind="PARTIAL")), encoding="utf-8")

    assert sampling.main(["--input", str(path), "--out-dir", str(tmp_path / "out")]) == 1
