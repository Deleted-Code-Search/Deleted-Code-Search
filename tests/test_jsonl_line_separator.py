"""JSONL 읽기가 U+2028 에서 레코드를 자르지 않는다 (#146).

`json.dumps(ensure_ascii=False)` 는 U+2028(LINE SEPARATOR)을 이스케이프하지 않고 쓰는데,
`str.splitlines()` 는 그것을 줄바꿈으로 본다. #82 에서 langchain 커밋 메시지 4건 때문에 맥락
결합이 30번 연속 멈췄다. 읽는 곳마다 같은 레코드 하나가 한 건으로 읽히는지 본다.
"""

import json

import pytest

from classify import baseline_keyword, baseline_llm, classifier, labels, sampling
from pipeline import context

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
