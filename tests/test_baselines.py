"""기준선 A/B 테스트 (이슈 #44).

네트워크·API 키 없이 돈다. 기준선 B 의 LLM 호출은 주입한 가짜 호출기로 대체한다.
"""

import json

import pytest

from classify import baseline_keyword as bk
from classify import baseline_llm as bl
from classify import baselines
from classify.labels import REASON_LABELS


def record(message="", body="def f():\n    pass\n", record_id="r1", **extra):
    """§4.4 레코드 모양의 최소 픽스처. 정답 라벨은 일부러 넣지 않는다."""
    row = {
        "id": record_id,
        "repo": "a/b",
        "deleted_body": body,
        "context": {"commit_message": message},
    }
    row.update(extra)
    return row


# --------------------------------------------------------------------------------------
# 공통 출력 형식
# --------------------------------------------------------------------------------------


def test_prediction_rejects_labels_outside_the_eight():
    with pytest.raises(ValueError):
        baselines.Prediction("r1", "NOT_A_LABEL", "keyword", "a1")


def test_prediction_dict_has_the_agreed_fields():
    data = baselines.Prediction("r1", "BUG", "keyword", "a1", evidence=("fix",)).to_dict()

    assert set(data) == {"record_id", "predicted_label", "method", "version", "evidence", "note"}
    # 사람 라벨 필드와 이름이 겹치면 안 된다 (ADR-005, 가이드 §7.2)
    assert "reason_label" not in data


def test_record_id_accepts_both_schema_and_label_naming():
    assert baselines.record_id_of({"id": "x"}) == "x"
    assert baselines.record_id_of({"record_id": "y"}) == "y"
    assert baselines.record_id_of({}) == ""


def test_commit_message_read_from_context_or_top_level():
    assert baselines.commit_message_of({"context": {"commit_message": "a"}}) == "a"
    assert baselines.commit_message_of({"commit_message": "b"}) == "b"
    assert baselines.commit_message_of({}) == ""


def test_summary_counts_predictions():
    predictions = [
        baselines.Prediction("1", "BUG", "keyword", "a1"),
        baselines.Prediction("2", "BUG", "keyword", "a1"),
        baselines.Prediction("3", "UNK", "keyword", "a1"),
    ]
    counts = baselines.summarize(predictions)

    assert counts.counts == {"BUG": 2, "UNK": 1}
    assert counts.total == 3
    assert "예측 3건" in counts.format_lines()[0]


# --------------------------------------------------------------------------------------
# 기준선 A - 8종 각각 (완료 조건)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("fix crash on empty header list", "BUG"),
        ("perf: avoid quadratic scan in parser", "PERF"),
        ("security: patch CVE-2024-1234 in url parsing", "SEC"),
        ("use urllib3 Retry instead of hand-rolled backoff", "LIB"),
        ("remove unused helper", "DEAD"),
        ("refactor: extract transport into its own module", "DESIGN"),
        ("deprecate python 3.7 support", "FEAT"),
        ("update docs", "UNK"),
    ],
)
def test_keyword_baseline_covers_all_eight_labels(message, expected):
    label, _ = bk.classify_message(message)
    assert label == expected


@pytest.mark.parametrize("message", ["", "   ", "\n\n"])
def test_empty_message_is_unknown(message):
    label, matched = bk.classify_message(message)

    assert label == "UNK"
    assert matched == ()


def test_unknown_is_not_forced_into_a_label():
    """억지로 고르면 §4.2 ③ "불명"의 뜻이 사라진다."""
    label, _ = bk.classify_message("wip")
    assert label == "UNK"


def test_priority_follows_the_guide_order_when_several_match():
    """여러 라벨이 매칭되면 가이드 §11-1 우선순위(SEC > LIB > DEAD > FEAT > BUG > PERF > DESIGN)."""
    # "fix"(BUG) 와 "security"(SEC) 가 함께 있으면 SEC
    assert bk.classify_message("fix security issue in parser")[0] == "SEC"
    # "refactor"(DESIGN) 와 "unused"(DEAD) 가 함께 있으면 DEAD
    assert bk.classify_message("refactor: drop unused branch")[0] == "DEAD"
    # "fix"(BUG) 와 "slow"(PERF) 가 함께 있으면 BUG
    assert bk.classify_message("fix slow lookup")[0] == "BUG"


def test_rule_order_matches_declared_priority():
    """규칙 순서가 곧 우선순위다. 순서가 흐트러지면 결과가 조용히 바뀐다."""
    assert [label for label, _ in bk.KEYWORD_RULES] == [
        "SEC",
        "LIB",
        "DEAD",
        "FEAT",
        "BUG",
        "PERF",
        "DESIGN",
    ]


def test_matched_keywords_are_reported_as_evidence():
    label, matched = bk.classify_message("fix: crash when header is empty")

    assert label == "BUG"
    assert any("fix" in item.lower() for item in matched)


def test_word_boundaries_avoid_false_matches():
    """`prefix` 안의 fix, `classes` 안의 다른 단어에 걸리면 안 된다."""
    assert bk.classify_message("add prefix to cache keys")[0] == "UNK"
    assert bk.classify_message("rename affix helper")[0] == "UNK"


def test_predict_produces_the_common_format():
    prediction = bk.predict(record("fix crash", record_id="abc"))

    assert prediction.record_id == "abc"
    assert prediction.predicted_label == "BUG"
    assert prediction.method == baselines.METHOD_KEYWORD
    assert prediction.version == bk.KEYWORD_RULES_VERSION


def test_predict_works_without_any_label_field():
    """정답 없이 동작해야 한다 (완료 조건)."""
    plain = {"id": "r1", "context": {"commit_message": "fix bug"}}
    assert bk.predict(plain).predicted_label == "BUG"


def test_note_says_why_when_nothing_matched():
    prediction = bk.predict(record("update docs"))

    assert prediction.predicted_label == "UNK"
    assert prediction.note


def test_keyword_cli_writes_predictions(tmp_path, capsys):
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(record(message, record_id=f"r{i}"), ensure_ascii=False)
            for i, message in enumerate(["fix crash", "refactor module", "wip"])
        ),
        encoding="utf-8",
    )
    out = tmp_path / "pred.jsonl"

    assert bk.main(["--input", str(source), "--out", str(out)]) == 0

    rows = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    assert [row["predicted_label"] for row in rows] == ["BUG", "DESIGN", "UNK"]
    assert all(row["method"] == "keyword" for row in rows)
    assert "기준선 A" in capsys.readouterr().out


# --------------------------------------------------------------------------------------
# 기준선 B - LLM 호출을 목으로
# --------------------------------------------------------------------------------------


def fake_caller(answer, *, record_calls=None):
    def call(system, prompt, model):
        if record_calls is not None:
            record_calls.append((system, prompt, model))
        return answer(prompt) if callable(answer) else answer

    return call


def test_prompt_contains_all_eight_definitions():
    prompt = bl.build_prompt("def f(): pass", "remove helper")

    for code, _ in bl.LABEL_DEFINITIONS:
        assert code in prompt
    assert len(bl.LABEL_DEFINITIONS) == len(REASON_LABELS)


def test_prompt_includes_message_and_code():
    prompt = bl.build_prompt("def target(): ...", "why it went away")

    assert "why it went away" in prompt
    assert "def target" in prompt


def test_prompt_truncates_long_input():
    prompt = bl.build_prompt("x" * 10_000, "y" * 10_000)

    assert len(prompt) < 10_000


@pytest.mark.parametrize(
    ("answer", "label"),
    [
        ("BUG|커밋 메시지에 IndexError 가 적혀 있다", "BUG"),
        ("  SEC | CVE 가 언급된다  ", "SEC"),
        ("DEAD|호출자가 없다", "DEAD"),
    ],
)
def test_llm_baseline_parses_well_formed_answers(answer, label):
    baseline = bl.LlmBaseline(fake_caller(answer))
    prediction = baseline.predict(record("msg"))

    assert prediction.predicted_label == label
    assert prediction.note == ""
    assert prediction.evidence


def test_answer_outside_the_eight_becomes_unknown_with_reason():
    """조용히 버리면 기준선이 실제보다 약해 보이고 비교가 왜곡된다."""
    baseline = bl.LlmBaseline(fake_caller("REFACTOR|구조를 바꿨다"))
    prediction = baseline.predict(record("msg"))

    assert prediction.predicted_label == "UNK"
    assert "8종 밖" in prediction.note
    assert "REFACTOR" in prediction.note


@pytest.mark.parametrize("answer", ["설명만 길게 적은 답", "", "   "])
def test_unparseable_answer_becomes_unknown_with_reason(answer):
    baseline = bl.LlmBaseline(fake_caller(answer))
    prediction = baseline.predict(record("msg"))

    assert prediction.predicted_label == "UNK"
    assert prediction.note


def test_call_failure_is_recorded_not_raised():
    def boom(system, prompt, model):
        raise OSError("network down")

    baseline = bl.LlmBaseline(boom)
    prediction = baseline.predict(record("msg"))

    assert prediction.predicted_label == "UNK"
    assert "호출 실패" in prediction.note
    assert baseline.failures["호출 실패"] == 1


def test_version_records_prompt_and_model():
    baseline = bl.LlmBaseline(fake_caller("BUG|x"), model="test-model")
    prediction = baseline.predict(record("msg"))

    assert prediction.version == f"{bl.PROMPT_VERSION}/test-model"


def test_cache_avoids_a_second_call(tmp_path):
    calls = []
    baseline = bl.LlmBaseline(
        fake_caller("BUG|근거", record_calls=calls), cache_dir=tmp_path / "llm"
    )
    first = baseline.predict(record("same message"))
    second = baseline.predict(record("same message"))

    assert first.predicted_label == second.predicted_label
    assert len(calls) == 1
    assert baseline.calls == 1
    assert baseline.cache_hits == 1


def test_cache_key_changes_with_prompt_version(tmp_path):
    """프롬프트를 고치면 옛 답을 쓰면 안 된다."""
    calls = []
    cache = tmp_path / "llm"
    bl.LlmBaseline(fake_caller("BUG|x", record_calls=calls), cache_dir=cache).predict(record("m"))
    later = bl.LlmBaseline(
        fake_caller("BUG|x", record_calls=calls), cache_dir=cache, prompt_version="b2"
    )
    later.predict(record("m"))

    assert len(calls) == 2


def test_cache_key_changes_with_model(tmp_path):
    calls = []
    cache = tmp_path / "llm"
    bl.LlmBaseline(fake_caller("BUG|x", record_calls=calls), model="m1", cache_dir=cache).predict(
        record("m")
    )
    bl.LlmBaseline(fake_caller("BUG|x", record_calls=calls), model="m2", cache_dir=cache).predict(
        record("m")
    )

    assert len(calls) == 2


def test_corrupt_cache_falls_back_to_asking_again(tmp_path):
    calls = []
    cache = tmp_path / "llm"
    baseline = bl.LlmBaseline(fake_caller("BUG|x", record_calls=calls), cache_dir=cache)
    baseline.predict(record("m"))
    for path in cache.glob("*.json"):
        path.write_text("깨진 내용", encoding="utf-8")

    baseline.predict(record("m"))

    assert len(calls) == 2


def test_predict_all_keeps_record_order():
    baseline = bl.LlmBaseline(fake_caller("BUG|x"))
    records = [record("m", record_id=f"r{i}") for i in range(3)]

    assert [p.record_id for p in baseline.predict_all(records)] == ["r0", "r1", "r2"]


def test_llm_output_is_never_written_as_a_human_label():
    """ADR-005 - 출력 필드가 사람 라벨 이름과 겹치지 않는다."""
    baseline = bl.LlmBaseline(fake_caller("BUG|근거"))
    data = baseline.predict(record("msg")).to_dict()

    assert "predicted_label" in data
    for human_field in ("reason_label", "evidence_grade", "labeler", "final"):
        assert human_field not in data


def test_llm_cli_dry_run_needs_no_api_key(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record("remove helper")), encoding="utf-8")

    assert bl.main(["--input", str(source), "--env-file", str(tmp_path / "none"), "--dry-run"]) == 0
    assert "분류 체계" in capsys.readouterr().out


def test_llm_cli_stops_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record("remove helper")), encoding="utf-8")

    assert bl.main(["--input", str(source), "--env-file", str(tmp_path / "none")]) == 2
