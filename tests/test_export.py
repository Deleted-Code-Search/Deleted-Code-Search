"""pipeline.export — dataset-v0.9 내보내기 (#83)."""

import gzip
import hashlib
import json

import pyarrow.parquet as pq
import pytest

from pipeline import export
from pipeline.export import (
    SCHEMA,
    ExportError,
    HumanLabel,
    JoinTables,
    Reason,
    build_parser,
    classify_row,
    run,
    shard_name,
)

OLD = "acme/old"  # 학습 저장소 (MIT)
NEW = "acme/new"  # 신규 저장소 (Apache-2.0)
GPL = "acme/gpl"  # 비-permissive (GPL-3.0)


def record(
    repo, rid, status="KEPT", kind="FULL_FUNCTION", context=True, body=None, date="2024-01-01"
):
    row = {
        "repo": repo,
        "commit_sha": "c" * 40,
        "parent_sha": "p" * 40,
        "file_path": "pkg/mod.py",
        "function_name": "f",
        "start_line": 1,
        "end_line": 9,
        "deletion_kind": kind,
        "deleted_body": body or f"def f():\n    return {rid!r}\n",
        "added_hunks_same_file": ["x = 1"],
        "author_date": f"{date}T00:00:00+09:00",
        "commit_message": "remove f",
        "id": rid,
        "function_signature": "def f():",
        "is_test_code": False,
        "source_url": f"https://github.com/{repo}/commit/{'c' * 40}",
        "filter_status": status,
        "filter_rule_version": "v0.9",
    }
    if status != "KEPT":
        row["filter_evidence"] = {"rule": "x"}
    if context:
        row["context"] = {
            "commit_message": "remove f",
            "pr_number": 7,
            "pr_title": "t",
            "pr_body": None,
            "pr_labels": [],
            "issue_numbers": [3],
            "issue_titles": ["i"],
            "issue_bodies": ["b"],
            "review_comments": [
                {
                    "comment_id": 1,
                    "body": "why",
                    "path": "pkg/mod.py",
                    "line": None,
                    "side": "LEFT",
                    "outdated": True,
                    "author": "u",
                }
            ],
        }
        row["replacement"] = {
            "code": "def g(): ...",
            "match_method": "SAME_LOCATION",
            "confidence": 0.9,
        }
    return row


def classified(rid, repo, label="DEAD", version="r1+m2"):
    return {
        "record_id": rid,
        "repo": repo,
        "training_repo": repo == OLD,
        "label": label,
        "evidence_grade": "INFERRED",
        "evidence_text": "같은 자리에 대체 코드가 들어왔다: def g()",
        "confidence": 0.65,
        "classifier_version": version,
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    # 입력 파일 2개 - 저장소는 파일마다 이어져 있다.
    records_a = [
        record(OLD, "h1", date="2023-01-01"),  # 확정 라벨, 라이선스 규칙 날짜 전 -> 링크만
        record(OLD, "o1"),  # 학습 저장소 모델만
        record(OLD, "p1", kind="PARTIAL", context=False),  # 분류 대상 아님
        record(OLD, "n1", status="NOISE_TRIVIAL"),
    ]
    # 본문에 이스케이프된 "filter_status": "KEPT" 가 있는 NOISE 행 - KEPT 로 오인하면 안 된다.
    tricky = record(NEW, "n2", status="NOISE_MOVE", body='x = \'"filter_status": "KEPT"\'\n')
    records_b = [
        record(NEW, "u1"),  # 신규 모델만 -> 이유 미구분
        record(NEW, "l1"),  # 신규 LLM 표본
        record(NEW, "g1", status="NOISE_GENERATED"),  # 분류 결과에는 있다 (v0.8 KEPT 였다)
        tricky,
        record(GPL, "x1"),  # 링크만
    ]
    inputs = [
        write_jsonl(tmp_path / "a.jsonl", records_a),
        write_jsonl(tmp_path / "b.jsonl", records_b),
    ]
    model = write_jsonl(
        tmp_path / "model.jsonl",
        [
            classified("h1", OLD, "UNK"),
            classified("o1", OLD, "DEAD"),
            classified("u1", NEW, "DESIGN"),
            classified("l1", NEW, "UNK"),
            classified("g1", NEW, "UNK"),
            classified("x1", GPL, "FEAT"),
        ],
    )
    llm = write_jsonl(
        tmp_path / "llm.jsonl", [classified("l1", NEW, "BUG", version="r1+m2+c3+llm:x")]
    )
    labels = write_jsonl(
        tmp_path / "labels.jsonl",
        [
            {
                "record_id": "h1",
                "batch": "main500",
                "split": "val",
                "final": {
                    "reason_label": "LIB",
                    "evidence_grade": "EXPLICIT",
                    "evidence_text": "bump lib",
                    "confidence": 1.0,
                    "method": "AGREED",
                },
            }
        ],
    )
    training = write_jsonl(tmp_path / "training.jsonl", [{"record_id": "h1", "repo": OLD}])
    overrides = tmp_path / "overrides.csv"
    overrides.write_text(
        "repo,path_prefix,before_date,license,note\n"
        f"{OLD},,2023-06-01,AGPL-3.0,AGPL until 2023-06-01\n"
        f"{NEW},vendored/,,GPL-3.0,no rows match\n",
        encoding="utf-8",
    )
    licenses = tmp_path / "licenses.csv"
    licenses.write_text(
        f"repo,license\n{OLD},MIT\n{NEW},Apache-2.0\n{GPL},GPL-3.0\n", encoding="utf-8"
    )
    argv = [
        *[x for p in inputs for x in ("--input", str(p))],
        "--classified",
        str(model),
        "--llm-sample",
        str(llm),
        "--labels",
        str(labels),
        "--licenses",
        str(licenses),
        "--training-records",
        str(training),
        "--license-overrides",
        str(overrides),
        "--out",
        str(tmp_path / "out"),
        "--commit-dir",
        str(tmp_path / "commit"),
        "--workers",
        "2",
    ]
    return tmp_path, argv


def read_rows(out, repo):
    rows = []
    for path in sorted(out.glob(f"{repo.replace('/', '__')}-*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            rows += [json.loads(line) for line in handle]
    return {row["id"]: row for row in rows}


def test_end_to_end(env):
    tmp_path, argv = env
    manifest = run(build_parser().parse_args(argv))
    out = tmp_path / "out"
    assert manifest["ok"], manifest["checks"]

    # NOISE_* 는 내보내지 않는다. KEPT 만.
    rows = {**read_rows(out, OLD), **read_rows(out, NEW), **read_rows(out, GPL)}
    assert set(rows) == {"h1", "o1", "p1", "u1", "l1", "x1"}
    assert manifest["checks"]["filter_status"]["all_rows"]["NOISE_MOVE"] == 1

    # 이유 출처
    assert rows["h1"]["reason"]["label"] == "LIB"
    assert rows["h1"]["reason"]["classifier_version"] == "human"
    assert rows["h1"]["classification"] == {
        "source": "HUMAN",
        "training_repo": True,
        "classifier_version": "human",
        "split": "val",
        "human_method": "AGREED",
    }
    assert rows["o1"]["classification"]["source"] == "MODEL_ONLY"
    assert rows["o1"]["reason"]["label"] == "DEAD"
    assert rows["u1"]["classification"]["source"] == "UNDISTINGUISHED"
    assert rows["u1"]["reason"] is None
    assert rows["u1"]["classification"]["classifier_version"] == "r1+m2"
    assert rows["l1"]["classification"]["source"] == "MODEL_LLM"
    assert rows["l1"]["reason"]["label"] == "BUG"
    assert rows["p1"]["classification"]["source"] == "NOT_CLASSIFIED"

    # 맥락이 없는 행(PARTIAL)은 목록 칸이 null, commit_message 는 채운다
    assert rows["p1"]["context"]["pr_labels"] is None
    assert rows["p1"]["context"]["commit_message"] == "remove f"
    assert rows["p1"]["replacement"] is None
    assert rows["h1"]["context"]["review_comments"][0]["side"] == "LEFT"

    # 라이선스 규칙 (저장소는 permissive, 날짜 전 행만 링크만): 라벨·split 은 그대로, 코드만 비운다
    assert rows["h1"]["code_policy"] == "LINK_ONLY"
    assert rows["h1"]["repo_license"] == "MIT"
    assert rows["h1"]["deleted_body"] is None
    assert rows["h1"]["function_signature"] is None
    assert rows["h1"]["replacement"]["code"] is None
    assert rows["h1"]["replacement"]["match_method"] == "SAME_LOCATION"
    assert rows["h1"]["reason"]["evidence_text"] is None
    assert rows["h1"]["function_name"] == "f"
    assert rows["h1"]["source_url"].startswith("https://github.com/acme/old/commit/")
    policy = manifest["checks"]["code_policy"]
    assert policy["exported"] == {"CODE_INCLUDED": 4, "LINK_ONLY": 2}
    assert policy["link_only_by_override"] == 1
    assert policy["link_only_by_repo_license"] == 1
    assert manifest["totals"]["link_only_by_rule"] == {
        "acme/old|*|<2023-06-01": 1,
        "acme/new|vendored/|<*": 0,
    }
    assert "added_hunks_same_file" not in rows["h1"]
    assert "filter_evidence" not in rows["h1"]

    # 라이선스
    assert rows["x1"]["code_policy"] == "LINK_ONLY"
    assert rows["x1"]["deleted_body"] is None
    assert rows["x1"]["function_signature"] is None
    assert rows["x1"]["replacement"]["code"] is None
    assert rows["x1"]["repo_license"] == "GPL-3.0"
    assert rows["x1"]["source_url"].startswith("https://github.com/acme/gpl/commit/")
    assert rows["o1"]["code_policy"] == "CODE_INCLUDED"
    assert rows["o1"]["deleted_body"].startswith("def f")

    # 조인 보고: g1 은 v0.9 에서 NOISE_GENERATED 라 빠졌다
    join = manifest["checks"]["join"]
    assert join["classifier_target_missing"] == 0
    assert join["model_only_unmatched_by_v09_status"] == {"NOISE_GENERATED": 1}
    assert manifest["checks"]["human"]["missing"] == []
    assert manifest["checks"]["human"]["changed"] == []
    assert manifest["checks"]["human"]["link_only"] == ["h1"]

    # Parquet 은 jsonl.gz 와 같은 행, 같은 스키마
    for repo in (OLD, NEW, GPL):
        parquet = pq.read_table(out / shard_name(repo, 0, 1, ".parquet"))
        assert parquet.schema.equals(SCHEMA)
        assert set(parquet.column("id").to_pylist()) == set(read_rows(out, repo))

    # 메타데이터와 Git 용 파일
    assert (out / "training_repos.txt").read_text(encoding="utf-8") == f"{OLD}\n"
    commit = tmp_path / "commit"
    main500 = [json.loads(line) for line in (commit / "main500.jsonl").open(encoding="utf-8")]
    assert [row["id"] for row in main500] == ["h1"]
    assert main500[0]["deleted_body"] is None
    assert main500[0]["reason"]["label"] == "LIB"
    assert main500[0]["classification"]["split"] == "val"
    assert "acme/gpl,GPL-3.0,LINK_ONLY,False" in (commit / "repos.csv").read_text("utf-8")
    sums = (out / "SHA256SUMS").read_text("utf-8").splitlines()
    assert len(sums) == len(manifest["files"])


def test_shards_are_deterministic_and_small(env):
    tmp_path, argv = env
    # 행 하나가 약 1KB - 상한 1.5KB 면 행마다 shard 하나가 된다.
    argv = [*argv, "--max-shard-bytes", "1500"]
    first = run(build_parser().parse_args(argv))
    assert first["ok"], first["checks"]
    names = sorted(entry["name"] for entry in first["files"] if entry["repo"] == OLD)
    assert names == [
        "acme__old-00000-of-00003.jsonl.gz",
        "acme__old-00000-of-00003.parquet",
        "acme__old-00001-of-00003.jsonl.gz",
        "acme__old-00001-of-00003.parquet",
        "acme__old-00002-of-00003.jsonl.gz",
        "acme__old-00002-of-00003.parquet",
    ]
    assert all(entry["records"] == 1 for entry in first["files"] if entry["repo"] == OLD)
    hashes = {e["name"]: e["sha256"] for e in first["files"]}
    second = run(build_parser().parse_args([*argv, "--force"]))
    assert {e["name"]: e["sha256"] for e in second["files"]} == hashes
    with gzip.open(tmp_path / "out" / names[0], "rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest()  # 실제 gzip 이다


def test_empty_repo_still_gets_files(env, tmp_path):
    _, argv = env
    only_noise = write_jsonl(
        tmp_path / "c.jsonl", [record("acme/empty", "e1", status="NOISE_MOVE")]
    )
    (tmp_path / "licenses.csv").write_text(
        f"repo,license\n{OLD},MIT\n{NEW},Apache-2.0\n{GPL},GPL-3.0\nacme/empty,MIT\n", "utf-8"
    )
    manifest = run(build_parser().parse_args([*argv, "--input", str(only_noise)]))
    assert manifest["ok"], manifest["checks"]
    empty = [e for e in manifest["files"] if e["repo"] == "acme/empty"]
    assert len(empty) == 2 and all(e["records"] == 0 for e in empty)


def test_missing_repo_fails_checks(env, tmp_path):
    _, argv = env
    (tmp_path / "licenses.csv").write_text(
        f"repo,license\n{OLD},MIT\n{NEW},Apache-2.0\n{GPL},GPL-3.0\nacme/never,MIT\n", "utf-8"
    )
    manifest = run(build_parser().parse_args(argv))
    assert not manifest["ok"]
    assert manifest["checks"]["repos_missing"] == ["acme/never"]


def test_missing_human_record_fails_checks(env, tmp_path):
    _, argv = env
    with (tmp_path / "labels.jsonl").open("a", encoding="utf-8") as handle:
        final = {"reason_label": "UNK", "evidence_grade": "UNKNOWN", "method": "AGREED"}
        handle.write(json.dumps({"record_id": "gone", "split": "test", "final": final}) + "\n")
    manifest = run(build_parser().parse_args(argv))
    assert not manifest["ok"]
    assert manifest["checks"]["human"]["missing"] == ["gone"]


def test_rejects_non_contiguous_repo(env, tmp_path):
    _, argv = env
    write_jsonl(tmp_path / "a.jsonl", [record(OLD, "h1"), record(GPL, "x2"), record(OLD, "o1")])
    write_jsonl(tmp_path / "b.jsonl", [record(NEW, "u1")])
    with pytest.raises(ExportError, match="이어져 있지 않다"):
        run(build_parser().parse_args(argv))


def test_rejects_repo_in_two_inputs(env, tmp_path):
    _, argv = env
    write_jsonl(tmp_path / "a.jsonl", [record(OLD, "h1"), record(NEW, "u9")])
    with pytest.raises(ExportError, match="두 곳"):
        run(build_parser().parse_args(argv))


def test_rejects_duplicate_classified_id(env, tmp_path):
    _, argv = env
    with (tmp_path / "model.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(classified("o1", OLD)) + "\n")
    with pytest.raises(ExportError, match="같은 record_id"):
        run(build_parser().parse_args(argv))


def test_main_rejects_missing_training_records(env, tmp_path, capsys):
    _, argv = env
    (tmp_path / "training.jsonl").unlink()
    assert export.main(argv) == 2
    assert "training.jsonl" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_refuses_non_empty_output_without_force(env, tmp_path):
    _, argv = env
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep.txt").write_text("x", "utf-8")
    with pytest.raises(ExportError, match="비어 있지 않다"):
        run(build_parser().parse_args(argv))
    assert (tmp_path / "out" / "keep.txt").exists()


def test_classify_row_priority():
    reason = Reason("DEAD", "INFERRED", None, 0.5, "r1+m2", OLD, True)
    human = HumanLabel("a", "test", "THIRD_PARTY", {"label": "BUG"})
    tables = JoinTables(
        model_only={"a": reason, "b": reason, "c": reason},
        llm={"b": reason},
        human={"a": human},
        licenses={},
        training_repos=frozenset({OLD}),
    )
    assert classify_row("a", True, tables)[1]["source"] == "HUMAN"
    assert classify_row("b", True, tables)[1]["source"] == "MODEL_LLM"
    assert classify_row("c", True, tables)[1]["source"] == "MODEL_ONLY"
    assert classify_row("c", False, tables) == (
        None,
        {
            "source": "UNDISTINGUISHED",
            "classifier_version": "r1+m2",
            "training_repo": False,
            "split": None,
            "human_method": None,
        },
    )
    assert classify_row("z", False, tables)[1]["source"] == "NOT_CLASSIFIED"


def test_code_policy_follows_permissive_list():
    assert export.code_policy_of("MIT") == "CODE_INCLUDED"
    assert export.code_policy_of("BSD-2-Clause") == "CODE_INCLUDED"
    assert export.code_policy_of("Apache-2.0") == "CODE_INCLUDED"
    assert export.code_policy_of("GPL-3.0") == "LINK_ONLY"
    assert export.code_policy_of("AGPL-3.0") == "LINK_ONLY"
    assert export.code_policy_of("NOASSERTION") == "LINK_ONLY"


def test_rejects_override_without_path_or_date(env, tmp_path):
    _, argv = env
    (tmp_path / "overrides.csv").write_text(
        f"repo,path_prefix,before_date,license,note\n{OLD},,,GPL-3.0,x\n", encoding="utf-8"
    )
    with pytest.raises(ExportError, match="path_prefix"):
        run(build_parser().parse_args(argv))


def test_rejects_override_for_unknown_repo(env, tmp_path):
    _, argv = env
    (tmp_path / "overrides.csv").write_text(
        "repo,path_prefix,before_date,license,note\nacme/nope,x/,,GPL-3.0,x\n", encoding="utf-8"
    )
    with pytest.raises(ExportError, match="목록에 없다"):
        run(build_parser().parse_args(argv))
