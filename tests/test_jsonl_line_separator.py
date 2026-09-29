"""JSONL 읽기가 U+2028 에서 레코드를 자르지 않는다 (#146).

`json.dumps(ensure_ascii=False)` 는 U+2028(LINE SEPARATOR)을 이스케이프하지 않고 쓰는데,
`str.splitlines()` 는 그것을 줄바꿈으로 본다. #82 에서 langchain 커밋 메시지 4건 때문에 맥락
결합이 30번 연속 멈췄다. 읽는 곳마다 같은 레코드 하나가 한 건으로 읽히는지 본다.
"""

import json

import pytest

from classify import baseline_keyword, baseline_llm, classifier, labels, sampling
from eval import gate1, gate1_bounds, gate1_merge
from pipeline import context
from tools import label_cli

MESSAGE = "Deprecate beta decorator  See the migration guide."


@pytest.fixture
def records_file(tmp_path):
    """U+2028 이 든 레코드 하나. PARTIAL 이라 맥락 결합 대상은 아니다 - 읽다 멈추면 안 된다."""
    record = {
        "id": "r1",
        "repo": "a/b",
        "commit_sha": "sha1",
        "file_path": "src/a.py",
        "function_name": "beta",
        "commit_message": MESSAGE,
        "deleted_body": "def beta():\n    pass\n",
        "deletion_kind": "PARTIAL",
    }
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3  # 옛 방식이면 세 줄로 잘린다
    return path


@pytest.mark.parametrize("read", [labels.read_jsonl, classifier.load_jsonl])
def test_readers_keep_the_record_whole(records_file, read):
    """두 읽기 함수가 레코드 하나를 한 건으로, 메시지를 그대로 읽는다."""
    rows = read(records_file)

    assert len(rows) == 1
    assert rows[0]["commit_message"] == MESSAGE


def test_context_input_is_read_before_filtering(records_file, tmp_path, capsys):
    """langchain 에서 멈춘 곳. 걸러질 PARTIAL 행이라도 읽다가 멈추면 안 된다."""
    argv = ["--input", str(records_file), "--full-function-only"]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--env-file", str(tmp_path / "none")]

    assert context.main(argv) == 1  # 대상 0건 - 네트워크까지 가지 않는다
    assert "FULL_FUNCTION 만: 1건 중 0건" in capsys.readouterr().err


def test_sampling_reads_one_record(records_file, tmp_path, capsys):
    """#85 가 20개 저장소 조립 결과를 여기로 읽는다."""
    sampling.main(["--input", str(records_file), "--out-dir", str(tmp_path / "out"), "--dry-run"])

    assert "입력 1건" in capsys.readouterr().out


def test_baseline_clis_read_one_record(records_file, tmp_path, capsys):
    """기준선 A·B 도 같은 레코드 파일을 읽는다."""
    out = tmp_path / "pred.jsonl"
    baseline_keyword.main(["--input", str(records_file), "--out", str(out)])
    baseline_llm.main(
        ["--input", str(records_file), "--env-file", str(tmp_path / "none"), "--dry-run"]
    )

    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    assert "1건 대상" in capsys.readouterr().err


def _write_rows(path, rows):
    """행들을 `ensure_ascii=False` JSONL 로 쓴다 - 파이프라인·라벨 도구가 쓰는 방식 그대로."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return path


@pytest.fixture
def label_file(tmp_path):
    """note 에 U+2028 이 든 개인 라벨 한 줄 (예비 200건 파일 이름 규칙)."""
    row = {**sampling.empty_label_row("r1", "sj"), "note": MESSAGE}
    name = sampling.LABEL_FILENAME_TEMPLATE.format(labeler="sj")
    return _write_rows(tmp_path / "labels" / name, [row])


def test_label_cli_reads_the_records_file_whole(tmp_path):
    """#90 에서 라벨러가 여는 레코드 파일. 커밋 메시지가 맥락에 그대로 실린다."""
    record = {"id": "r1", "repo": "a/b", "context": {"commit_message": MESSAGE}}
    path = _write_rows(tmp_path / "records.jsonl", [sampling.build_labeling_record(record)])

    views, problems = label_cli.load_records(path)

    assert problems == []
    assert views["r1"]["context"]["commit_message"] == MESSAGE


def test_label_cli_reads_the_label_file_whole(label_file):
    """라벨러가 note 에 붙여 넣은 문장에 U+2028 이 섞여도 파일을 연다."""
    assert [row["note"] for row in label_cli.LabelFile.load(label_file).rows] == [MESSAGE]


def test_gate1_readers_see_one_row(label_file, tmp_path):
    """게이트 판정 스크립트 셋 - JSON 이 아니라는 문제로 줄을 버리지 않는다."""
    inputs = gate1.load_inputs([label_file])
    _personal, problems = gate1_merge.load_personal(label_file.parent)
    merged = _write_rows(
        tmp_path / "merged.jsonl", [{"record_id": "r1", "labels": [], "n": MESSAGE}]
    )

    assert not [p for p in inputs.problems if "JSON" in p]
    assert not [p for p in problems if "JSON" in p]
    assert len(gate1_bounds.read_merged(merged)) == 1
