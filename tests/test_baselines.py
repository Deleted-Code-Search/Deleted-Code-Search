"""기준선 A/B 테스트 (이슈 #44).

네트워크·API 키 없이 돈다. 기준선 B 의 LLM 호출은 주입한 가짜 호출기로 대체한다.
"""

import http.client
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


def test_connection_dropped_mid_response_is_a_call_failure_not_a_crash():
    """응답을 읽다 끊기면 `IncompleteRead` 가 난다 - `OSError` 가 아니라 빠져 있었다 (#84).

    잡지 않으면 한 건의 끊김으로 배치 전체가 멈추고 예측 파일이 남지 않는다.
    """

    def dropped(system, prompt, model):
        raise http.client.IncompleteRead(b"partial")

    baseline = bl.LlmBaseline(dropped)
    prediction = baseline.predict(record("msg"))

    assert prediction.predicted_label == "UNK"
    assert "IncompleteRead" in prediction.note
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


def test_empty_answer_is_not_cached(tmp_path):
    """빈 답은 실패다. 남기면 원인을 고친 뒤에도 그 건은 다시 묻지 않는다 (#59)."""
    answers = iter(["", "BUG|근거"])
    baseline = bl.LlmBaseline(lambda *_: next(answers), cache_dir=tmp_path / "llm")

    assert baseline.predict(record("same message")).predicted_label == "UNK"
    assert baseline.predict(record("same message")).predicted_label == "BUG"
    assert baseline.calls == 2


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


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "m", "text": {"oops": 1}},  # 유효 JSON, text 가 dict
        {"model": "m", "text": 42},
        {"model": "m", "text": None},
        {"model": "m"},  # text 자체가 없음
        ["not", "a", "dict"],
    ],
)
def test_cache_with_non_string_text_is_treated_as_a_miss(tmp_path, payload):
    """유효 JSON 이어도 text 가 문자열이 아니면 parse_answer 가 터져 배치 전체가 죽는다."""
    calls = []
    cache = tmp_path / "llm"
    baseline = bl.LlmBaseline(fake_caller("BUG|근거", record_calls=calls), cache_dir=cache)
    baseline.predict(record("m"))
    for path in cache.glob("*.json"):
        path.write_text(json.dumps(payload), encoding="utf-8")

    again = bl.LlmBaseline(fake_caller("BUG|근거", record_calls=calls), cache_dir=cache)
    prediction = again.predict(record("m"))  # 예외 없이 다시 물어야 한다

    assert prediction.predicted_label == "BUG"
    assert len(calls) == 2


def test_cache_write_failure_keeps_the_api_answer(tmp_path, monkeypatch, capsys):
    """이미 돈을 주고 받은 답이다. 디스크 문제로 UNK 로 만들면 결과와 비용을 함께 잃는다."""
    from pathlib import Path

    baseline = bl.LlmBaseline(fake_caller("BUG|정상 응답"), cache_dir=tmp_path / "llm")
    monkeypatch.setattr(Path, "write_text", _raise_disk_full)

    prediction = baseline.predict(record("m"))

    assert prediction.predicted_label == "BUG"
    assert prediction.evidence == ("정상 응답",)
    assert baseline.failures.get("캐시 쓰기") == 1
    assert "호출 실패" not in baseline.failures  # 원인을 잘못 기록하지 않는다


def _raise_disk_full(*args, **kwargs):
    raise OSError("disk full")


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


@pytest.mark.parametrize("limit", ["-1", "-100"])
def test_negative_limit_is_rejected(tmp_path, limit, capsys):
    """`records[:-1]` 이 되어 마지막 하나만 빼고 전부 유료 호출한다. 정반대 동작이다."""
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(json.dumps(record("m", record_id=f"r{i}")) for i in range(10)), encoding="utf-8"
    )

    exit_code = bl.main(
        ["--input", str(source), "--env-file", str(tmp_path / "none"), "--limit", limit]
    )

    assert exit_code == 2
    assert "0 이상" in capsys.readouterr().err


def test_zero_limit_means_everything(tmp_path, capsys):
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(json.dumps(record("m", record_id=f"r{i}")) for i in range(3)), encoding="utf-8"
    )

    bl.main(
        ["--input", str(source), "--env-file", str(tmp_path / "none"), "--limit", "0", "--dry-run"]
    )

    assert "3건 대상" in capsys.readouterr().err


@pytest.mark.parametrize("provider", ["nvidia", "anthropic"])
def test_llm_cli_stops_without_api_key(tmp_path, monkeypatch, capsys, provider):
    """키가 없으면 어느 키가 필요한지 말하고 멈춘다 (§8.4 - 키는 .env 에만)."""
    for provider_config in bl.PROVIDERS.values():
        monkeypatch.delenv(provider_config.env_key, raising=False)
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record("remove helper")), encoding="utf-8")
    argv = ["--input", str(source), "--env-file", str(tmp_path / "none"), "--provider", provider]

    assert bl.main(argv) == 2
    assert bl.PROVIDERS[provider].env_key in capsys.readouterr().err


class FakeResponse:
    """`urlopen` 이 돌려주는 응답 흉내."""

    def __init__(self, body):
        """`body` 를 JSON 으로 담아 둔다."""
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        """`with urlopen(...) as response` 용."""
        return self

    def __exit__(self, *exc):
        """예외를 삼키지 않는다."""
        return False

    def read(self):
        """응답 본문 바이트."""
        return self.body


def test_nvidia_caller_sends_openai_style_request_and_reads_the_answer(monkeypatch):
    """OpenAI 호환 형식. 생각 과정을 끄고 temperature 0 으로 보낸다 (#59)."""
    sent = []

    def fake_urlopen(request, timeout):
        """보낸 요청을 남기고 정상 답을 준다."""
        sent.append(request)
        return FakeResponse({"choices": [{"message": {"content": "BUG|근거"}}]})

    monkeypatch.setattr(bl.urllib.request, "urlopen", fake_urlopen)
    call = bl.nvidia_caller("nvapi-test", min_interval=0)

    assert call("시스템", "프롬프트", "some/model") == "BUG|근거"
    request = sent[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == bl.NVIDIA_API_URL
    assert request.get_header("Authorization") == "Bearer nvapi-test"
    assert payload["model"] == "some/model"
    assert payload["temperature"] == 0
    assert payload["chat_template_kwargs"] == {"thinking": False}
    assert payload["messages"] == [
        {"role": "system", "content": "시스템"},
        {"role": "user", "content": "프롬프트"},
    ]


def test_nvidia_caller_empty_choices_is_an_empty_answer(monkeypatch):
    """답이 없으면 빈 문자열 - `parse_answer` 가 "빈 응답" 으로 사유를 남긴다."""
    monkeypatch.setattr(bl.urllib.request, "urlopen", lambda *a, **k: FakeResponse({}))

    assert bl.nvidia_caller("k", min_interval=0)("s", "p", "m") == ""


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("nvidia", []),
        ("nvidia", {"choices": {"0": {}}}),
        ("nvidia", {"choices": ["BUG|x"]}),
        ("nvidia", {"choices": [{"message": "BUG|x"}]}),
        ("anthropic", []),
        ("anthropic", {"content": "BUG|x"}),
        ("anthropic", {"content": [{"type": "text", "text": 7}]}),
    ],
)
def test_malformed_response_is_a_value_error(monkeypatch, provider, body):
    """모양이 다른 응답은 `ValueError` - `CALL_ERRORS` 라 그 건만 실패로 남는다 (#59 코드래빗)."""
    monkeypatch.setattr(bl.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    kwargs = {"min_interval": 0} if provider == "nvidia" else {}
    call = bl.PROVIDERS[provider].make_caller("k", **kwargs)

    with pytest.raises(ValueError, match="응답 모양"):
        call("s", "p", "m")


def test_one_odd_answer_does_not_stop_the_batch():
    """OpenAI 호환 서버는 `content` 를 조각 리스트로 주기도 한다. 그 건만 UNK, 다음 건은 계속."""
    answers = iter([[{"type": "text", "text": "BUG|x"}], "DEAD|y"])
    baseline = bl.LlmBaseline(lambda *_: next(answers))

    first, second = baseline.predict_all([record("a", record_id="r1"), record("b", record_id="r2")])

    assert (first.predicted_label, second.predicted_label) == ("UNK", "DEAD")
    assert "문자열이 아니다" in first.note


def test_nvidia_caller_spaces_calls_under_the_free_rate_limit(monkeypatch):
    """분당 40회를 넘으면 429 가 한 건의 실패로 남는다. 두 번째 호출은 간격만큼 기다린다."""
    clock = iter([100.0, 100.0, 100.5, 101.6])
    waits = []
    monkeypatch.setattr(bl.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(bl.time, "sleep", waits.append)
    monkeypatch.setattr(
        bl.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse({"choices": [{"message": {"content": "BUG|x"}}]}),
    )
    call = bl.nvidia_caller("k", min_interval=1.6)

    call("s", "p", "m")
    call("s", "p", "m")

    assert waits == [pytest.approx(1.1)]
