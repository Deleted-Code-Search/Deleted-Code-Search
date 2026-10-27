"""100개 저장소 적용 테스트 (#180) - 대상 거르기, 표본, 나눠 돌리기, 버전, CLI."""

import json

import pytest

from classify import apply


def record(rid, repo="a/b", kind="FULL_FUNCTION", status="KEPT", message="remove unused helper"):
    """조립·맥락 결합 뒤 레코드의 최소 모양."""
    return {
        "id": rid,
        "repo": repo,
        "file_path": "pkg/mod.py",
        "function_name": "helper_fn",
        "deleted_body": "def helper_fn():\n    pass\n",
        "deletion_kind": kind,
        "filter_status": status,
        "context": {"commit_message": message},
        "replacement": {"code": None, "match_method": None, "confidence": 0.0},
    }


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def training_files(tmp_path):
    """학습용 레코드 6건(train 4 · val 2)과 확정 라벨. 모델이 DEAD·DESIGN 을 가르게 한다."""
    rows, labels = [], []
    for i, (label, message, split) in enumerate(
        [
            ("DEAD", "unused unused helper", "train"),
            ("DEAD", "unused dead helper", "train"),
            ("DESIGN", "layering layering module", "train"),
            ("DESIGN", "layering split module", "train"),
            ("DEAD", "unused helper gone", "val"),
            ("DESIGN", "layering module moved", "val"),
        ]
    ):
        rows.append(record(f"L{i}", message=message))
        labels.append({"record_id": f"L{i}", "split": split, "final": {"reason_label": label}})
    records = write_jsonl(tmp_path / "train_records.jsonl", rows)
    label_path = write_jsonl(tmp_path / "labels.jsonl", labels)
    return ["--training-records", str(records), "--labels", str(label_path)]


def test_sample_is_the_same_whatever_the_file_order():
    """표본은 sha256(시드:record_id) 로 정한다 - 읽는 순서가 달라도 같다."""
    rows = [record(f"r{i}", repo="a/b" if i % 2 else "c/d") for i in range(40)]

    forward = apply.sample_per_repo(iter(rows), 5, 7, set())
    backward = apply.sample_per_repo(iter(reversed(rows)), 5, 7, set())

    assert [r["id"] for r in forward] == [r["id"] for r in backward]
    assert len(forward) == 10  # 저장소 2개 x 5건
    other_seed = apply.sample_per_repo(iter(rows), 5, 8, set())
    assert {r["id"] for r in other_seed} != {r["id"] for r in forward}


def test_sample_takes_the_smallest_keys_only_from_eligible_and_unlabeled():
    """대상(FULL_FUNCTION·KEPT)만, 확정 500건은 빼고, 키가 작은 순서로."""
    rows = [record(f"r{i}") for i in range(20)]
    rows += [record("partial", kind="PARTIAL"), record("noise", status="NOISE_MOVE")]
    labeled = {"r0", "r1"}

    picked = apply.sample_per_repo(iter(rows), 3, 1, labeled)

    candidates = sorted(
        (r["id"] for r in rows[:20] if r["id"] not in labeled),
        key=lambda rid: apply.stable_key(1, rid),
    )
    assert [r["id"] for r in picked] == candidates[:3]


def test_shards_split_the_records_without_overlap():
    """`--shard K/N` 들의 합은 전체이고 서로 겹치지 않는다."""
    ids = [f"r{i}" for i in range(200)]
    shards = [{rid for rid in ids if apply.in_shard(rid, (k, 4))} for k in range(4)]

    assert set().union(*shards) == set(ids)
    assert sum(len(s) for s in shards) == len(ids)
    assert all(shards)


@pytest.mark.parametrize("text", ["4/4", "-1/3", "x/2"])
def test_bad_shard_is_refused(text):
    with pytest.raises(ValueError):
        apply.parse_shard(text)


def test_model_only_run_labels_only_eligible_records_with_the_model_only_version(tmp_path, capsys):
    """모델만 버전 - 대상만 내고, 버전에 c3 가 없고, 학습은 train split 만 쓴다.

    `training_repo` 는 학습 레코드 파일(확정 500건)에 나오는 저장소인가다.
    """
    first = write_jsonl(tmp_path / "a.jsonl", [record("x1"), record("x2", kind="PARTIAL")])
    second = write_jsonl(
        tmp_path / "b.jsonl",
        [record("x3", status="NOISE_FORMAT"), record("x4"), record("x5", repo="z/new")],
    )
    out = tmp_path / "out" / "model_only.jsonl"

    code = apply.main(
        ["--input", str(first), "--input", str(second), "--out", str(out)]
        + training_files(tmp_path)
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert code == 0
    assert [row["record_id"] for row in rows] == ["x1", "x4", "x5"]
    assert [row["training_repo"] for row in rows] == [True, True, False]
    assert {row["classifier_version"] for row in rows} == {"r1+m2"}
    assert set(rows[0]) == {
        "record_id",
        "repo",
        "training_repo",
        "label",
        "evidence_grade",
        "evidence_text",
        "confidence",
        "classifier_version",
    }
    printed = capsys.readouterr().out
    assert "학습 split: train /" in printed
    assert "학습 저장소 2건 / 신규 저장소 1건" in printed
    assert not (tmp_path / "out" / "model_only.jsonl.tmp").exists()


def test_llm_needs_a_sample(tmp_path, capsys):
    """LLM 포함 버전은 표본에만 쓴다 (게이트 2 "바뀐 계획") - 전체에 돌리려 하면 거부."""
    source = write_jsonl(tmp_path / "a.jsonl", [record("x1")])

    code = apply.main(["--input", str(source), "--out", str(tmp_path / "o.jsonl"), "--llm"])

    assert code == 2
    assert "--sample-per-repo" in capsys.readouterr().err


def test_llm_sample_uses_train_and_val_and_the_gate2_version(tmp_path, monkeypatch, capsys):
    """LLM 포함 버전은 학습+검증으로 학습하고 버전이 게이트 2 때와 같은 꼴이다. 네트워크 없이."""
    monkeypatch.setattr(apply, "caller_from_env", lambda provider: lambda s, p, m: "DEAD|unused")
    source = write_jsonl(tmp_path / "a.jsonl", [record(f"x{i}") for i in range(5)])
    out = tmp_path / "llm.jsonl"
    args = ["--input", str(source), "--out", str(out), "--llm", "--sample-per-repo", "2"]

    code = apply.main(args + ["--cache-dir", str(tmp_path / "cache")] + training_files(tmp_path))

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert code == 0
    assert len(rows) == 2
    assert {row["classifier_version"] for row in rows} == {
        "r1+m2+c3+llm:deepseek-ai/deepseek-v4.1-flash"
    }
    assert "학습 split: train+val /" in capsys.readouterr().out


def test_a_failed_llm_call_leaves_no_output(tmp_path, monkeypatch, capsys):
    """호출이 실패한 건은 LLM 없이 분류되는데 버전은 LLM 포함이다 - 그런 출력은 남기지 않는다."""

    def timing_out(system, prompt, model):
        raise TimeoutError("timed out")

    monkeypatch.setattr(apply, "caller_from_env", lambda provider: timing_out)
    source = write_jsonl(tmp_path / "a.jsonl", [record(f"x{i}") for i in range(3)])
    out = tmp_path / "llm.jsonl"
    args = ["--input", str(source), "--out", str(out), "--llm", "--sample-per-repo", "2"]

    code = apply.main(args + ["--cache-dir", str(tmp_path / "cache")] + training_files(tmp_path))

    assert code == 1
    assert not out.exists() and not (tmp_path / "llm.jsonl.tmp").exists()
    assert "LLM 호출 실패 2건" in capsys.readouterr().err


def test_an_unreadable_llm_answer_still_gives_output(tmp_path, monkeypatch):
    """답을 못 읽은 건은 분류기 설계대로 LLM 표 없이 간다 - 같은 답이면 늘 같으므로 막지 않는다."""
    monkeypatch.setattr(apply, "caller_from_env", lambda provider: lambda s, p, m: "no label here")
    source = write_jsonl(tmp_path / "a.jsonl", [record("x1")])
    out = tmp_path / "llm.jsonl"
    args = ["--input", str(source), "--out", str(out), "--llm", "--sample-per-repo", "1"]

    code = apply.main(args + ["--cache-dir", str(tmp_path / "cache")] + training_files(tmp_path))

    assert code == 0 and out.exists()


def test_missing_input_is_refused(tmp_path, capsys):
    code = apply.main(["--input", str(tmp_path / "none.jsonl"), "--out", str(tmp_path / "o.jsonl")])

    assert code == 2
    assert "입력 파일이 없다" in capsys.readouterr().err


def test_sample_only_writes_the_sample_and_resampling_it_gives_the_same(tmp_path, capsys):
    """큰 입력을 한 번만 읽고 표본만 작은 파일로 - 그 파일에서 다시 뽑아도 같은 표본이다."""
    rows = [record(f"r{i}", repo="a/b" if i % 3 else "c/d") for i in range(30)]
    source = write_jsonl(tmp_path / "big.jsonl", rows)
    labels = write_jsonl(tmp_path / "labels.jsonl", [{"record_id": "r1", "final": {}}])
    sample = tmp_path / "sample.jsonl"
    args = ["--sample-per-repo", "4", "--seed", "5", "--labels", str(labels)]

    code = apply.main(["--input", str(source), "--out", str(sample), "--sample-only"] + args)

    written = [json.loads(line)["id"] for line in sample.read_text(encoding="utf-8").splitlines()]
    expected = [r["id"] for r in apply.sample_per_repo(iter(rows), 4, "5", set())]
    assert code == 0
    assert written == expected and len(written) == 8
    again = [r["id"] for r in apply.sample_per_repo(apply.iter_records([sample]), 4, "5", set())]
    assert again == written
    assert "표본 8건, 저장소 2개" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [["--llm", "--sample-per-repo", "3"], []])
def test_sample_only_needs_a_size_and_no_llm(tmp_path, capsys, extra):
    source = write_jsonl(tmp_path / "a.jsonl", [record("x1")])

    code = apply.main(
        ["--input", str(source), "--out", str(tmp_path / "o.jsonl"), "--sample-only"] + extra
    )

    assert code == 2
    assert "--sample-only" in capsys.readouterr().err
