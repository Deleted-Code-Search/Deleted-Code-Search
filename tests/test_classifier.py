"""분류기 골격 테스트 (이슈 #84) - 규칙·모델·LLM 후보·앙상블·근거 등급.

네트워크를 타지 않는다. LLM 은 가짜 호출기로 본다. 정확도는 보지 않는다 - 끝까지 연결되는지와
근거 규칙(가이드 §6)을 지키는지만 본다.
"""

import json

import pytest

from classify import classifier as clf
from classify import rules
from classify.baseline_llm import LlmBaseline
from classify.model import ReasonModel


def make_record(record_id="r1", **overrides):
    """§4.4 레코드 모양의 최소 레코드. 맥락은 비어 있다 - 테스트마다 채운다."""
    record = {
        "id": record_id,
        "repo": "a/b",
        "file_path": "src/net/retry_helper.py",
        "function_name": "legacy_backoff",
        "deleted_body": "def legacy_backoff():\n    return 1\n",
        "context": {
            "commit_message": "",
            "pr_number": None,
            "pr_title": None,
            "pr_body": None,
            "pr_labels": [],
            "issue_numbers": [],
            "issue_titles": [],
            "issue_bodies": [],
            "review_comments": [],
        },
        "replacement": {"code": None, "match_method": None, "confidence": 0.0},
    }
    for key, value in overrides.items():
        if key in record["context"]:
            record["context"][key] = value
        else:
            record[key] = value
    return record


def fake_llm(answer):
    """늘 같은 답을 주는 LLM 후보."""
    return clf.LlmCandidate(LlmBaseline(caller=lambda system, prompt, model: answer))


def trained_model(label="DESIGN"):
    """`layering` 이 나오면 `label`, `segfault` 가 나오면 BUG 로 기우는 모델.

    두 단어는 기준선 A 키워드가 **아니다** - 규칙은 아무것도 못 찾고 모델만 답하는 상황을
    만들려고 골랐다.
    """
    records, labels = [], []
    for index in range(3):
        for reason, word in ((label, "layering"), ("BUG", "segfault")):
            records.append(make_record(f"{reason}{index}", commit_message=f"{word} {word}"))
            labels.append(reason)
    return ReasonModel().fit(records, labels)


# --------------------------------------------------------------------------------------
# 규칙 - 문장·위치 (가이드 §7.2)
# --------------------------------------------------------------------------------------


def test_every_context_source_gets_its_locator():
    record = make_record(
        commit_message="Remove the retry helper.",
        pr_number=12,
        pr_title="Drop legacy retry",
        pr_body="It raced on reconnect.",
        issue_numbers=[7],
        issue_titles=["Backoff races"],
        issue_bodies=["Two threads double the delay."],
        review_comments=[
            {"comment_id": 99, "body": "Use urllib3 Retry instead."},
            {"comment_id": None, "body": "Why was this still here?"},
        ],
    )
    found = {(p.source, p.locator) for p in rules.passages(record)}

    assert found == {
        ("commit", "commit:message"),
        ("pr", "pr:#12#title"),
        ("pr", "pr:#12#body"),
        ("issue", "issue:#7#title"),
        ("issue", "issue:#7#body"),
        ("review", "review:comment_99"),
        ("review", "review:unknown"),
    }


def test_old_string_review_comments_still_read_as_unknown_locator():
    """ADR-018 전 레코드는 리뷰 코멘트가 문자열이다. 터지지 않고 `review:unknown` 으로 읽는다."""
    record = make_record(review_comments=["this was never called anywhere"])

    assert [p.locator for p in rules.passages(record)] == ["review:unknown"]


def test_code_blocks_are_not_read_as_reasons():
    """코드 예시 안의 단어가 이유 키워드로 잡히면 안 된다 (`pipeline.context.strip_code`)."""
    record = make_record(commit_message="Cleanup\n\n```\n# fix the crash later\n```")

    assert all("crash" not in p.text for p in rules.passages(record))


def test_pr_template_comments_are_not_read():
    """PR 템플릿 안내문은 작성자 글이 아니다. pydantic 템플릿의 `"fix #123"` 이 BUG 로 잡혔다."""
    record = make_record(
        pr_number=12,
        pr_body='<!-- WARNING: please use "fix #123" style references -->\nRemove unused helper.',
    )

    assert [p.text for p in rules.passages(record)] == ["Remove unused helper."]


def test_an_issue_closing_reference_alone_is_not_a_reason():
    """`Fixes #12` 는 "이 이슈를 닫는다" 는 연결이다. 이슈가 기능 요청이어도 BUG 가 되면 안 된다."""
    for message in ("Fixes #3409.", "Fix GH-12", "closes https://github.com/a/b/issues/7"):
        assert rules.find_reason_sentences(make_record(commit_message=message)) == []


def test_closing_reference_is_ignored_for_the_label_but_kept_in_the_quote():
    """라벨은 참조를 빼고 고르되, 인용문은 원문 그대로다 (가이드 §6.1)."""
    record = make_record(commit_message="Remove unused legacy_backoff, fixes #12")
    [sentence] = rules.find_reason_sentences(record)

    assert sentence.label == "DEAD"
    assert sentence.passage.text == "Remove unused legacy_backoff, fixes #12"


def test_markdown_bullets_are_stripped_and_fragments_dropped():
    sentences = rules.split_sentences("- [x]\n* Removed unused helper\n## Why")

    assert sentences == ["Removed unused helper"]


def test_target_name_must_be_a_whole_word():
    """`parse` 가 `parser` 에 걸리면 "이 함수를 가리킨다" 가 거짓이 된다."""
    assert rules.mentions("drop parse_all", ("parse_all",))
    assert not rules.mentions("drop parse_all_items", ("parse_all",))


def test_short_function_names_are_not_used_as_targets():
    """`get`·`run` 같은 이름은 아무 문장에나 걸린다."""
    assert rules.target_names(make_record(function_name="get", file_path="x/io.py")) == ()


def test_reason_sentences_carry_label_and_whether_they_name_the_target():
    record = make_record(
        commit_message="legacy_backoff is unused now. Also fix typo in docs.",
    )
    found = {(s.label, s.names_target) for s in rules.find_reason_sentences(record)}

    assert found == {("DEAD", True), ("BUG", False)}


# --------------------------------------------------------------------------------------
# 분류 - 근거가 먼저다
# --------------------------------------------------------------------------------------


def test_no_evidence_is_unk_even_if_the_model_and_llm_disagree():
    """근거가 없으면 모델·LLM 이 무엇을 고르든 UNK 다 (가이드 §6.2.1)."""
    classifier = clf.Classifier(model=trained_model("DESIGN"), llm=fake_llm("DESIGN|구조 변경"))
    result = classifier.classify(make_record(commit_message="Update things"))

    assert (result.label, result.evidence_grade, result.confidence) == ("UNK", "UNKNOWN", 0.0)


def test_sentence_naming_the_function_is_explicit_with_its_locator():
    record = make_record(pr_number=12, pr_body="legacy_backoff is no longer used by the client.")
    result = clf.Classifier().classify(record)

    assert (result.label, result.evidence_grade) == ("DEAD", "EXPLICIT")
    assert result.confidence == clf.EXPLICIT_CONFIDENCE
    assert result.evidence_locator == "pr:#12#body"
    assert result.evidence_text == "legacy_backoff is no longer used by the client."


def test_sentence_that_does_not_reach_this_function_is_inferred():
    """이유는 말하지만 이 함수를 가리키지 않는다 - 가이드 §6.1.1 E2 를 못 넘는다."""
    record = make_record(commit_message="Remove unused helpers across the package.")
    result = clf.Classifier().classify(record)

    assert (result.label, result.evidence_grade) == ("DEAD", "INFERRED")
    assert result.confidence == clf.UNREACHED_SENTENCE_CONFIDENCE
    assert result.evidence_locator == "commit:message"


def test_replacement_alone_is_inferred_evidence_one():
    """대체 코드만 있으면 근거 ① - `diff:replacement`, 이유는 모델이 고른다."""
    record = make_record(
        commit_message="Adjust layering",
        replacement={
            "code": "def backoff():\n    ...",
            "match_method": "SAME_LOCATION",
            "confidence": 0.9,
        },
    )
    result = clf.Classifier(model=trained_model("DESIGN")).classify(record)

    assert (result.label, result.evidence_grade) == ("DESIGN", "INFERRED")
    assert (result.evidence_source, result.evidence_locator) == ("diff", "diff:replacement")
    assert result.confidence == 0.9


def test_replacement_confidence_caps_the_inferred_confidence():
    """파이프라인이 0.7 만 확신하는 대체 코드로 0.8+ 를 줄 수 없다 (#103 0.7 논의)."""
    record = make_record(
        replacement={
            "code": "def backoff(): ...",
            "match_method": "SAME_LOCATION",
            "confidence": 0.7,
        },
    )

    assert clf.Classifier(model=trained_model()).classify(record).confidence == 0.7


def test_replacement_below_the_inferred_floor_is_not_evidence():
    """0.5 아래는 INFERRED 가 아니라 UNKNOWN 이다 (가이드 §6.2.2)."""
    record = make_record(
        replacement={
            "code": "def backoff(): ...",
            "match_method": "SAME_LOCATION",
            "confidence": 0.3,
        },
    )

    assert clf.Classifier(model=trained_model()).classify(record).label == "UNK"


def test_null_replacement_code_is_not_evidence():
    """`code` 가 `null` 이면 근거 ① 을 쓸 수 없다 (가이드 §6.2.3)."""
    record = make_record(
        replacement={"code": None, "match_method": "SAME_LOCATION", "confidence": 0.9}
    )

    assert clf.Classifier(model=trained_model()).classify(record).label == "UNK"


# --------------------------------------------------------------------------------------
# LLM - 후보와 근거 문장만 (ADR-005)
# --------------------------------------------------------------------------------------


def test_on_a_tie_the_label_backed_by_evidence_beats_the_llm_vote():
    """이유 문장은 DEAD, LLM 은 SEC - 한 표씩 동점이다. 근거가 받치는 쪽이 이긴다.

    가이드 §11-1 우선순위로만 가르면 SEC 가 이긴다. LLM 한 표가 근거를 이기면 안 된다 (ADR-005).
    """
    record = make_record(commit_message="Remove unused helpers.")
    result = clf.Classifier(llm=fake_llm("SEC|위험한 패턴")).classify(record)

    assert result.label == "DEAD"


def test_model_can_pick_a_reason_the_sentence_keyword_missed():
    """문장 키워드가 다른 이유를 가리켜도 모델이 강하면 그 이유를 고를 수 있다.

    예비 200건에서 사람 DEAD 23건의 문장 키워드는 대부분 DESIGN 이었다. 키워드 라벨로 후보를
    자르면 DEAD 가 아예 나올 수 없었다. 이때 등급은 가장 약한 INFERRED(하한) 다.

    문장 키워드가 두 라벨로 갈리게 만든 이유: 한 라벨로 모이면 규칙 점수가 1.0 이 되어 모델
    확률(1 미만)이 가중치 1:1:1 에서는 못 이긴다. 가중치는 #86 에서 val 로 정한다.
    """
    # 문장 키워드는 DESIGN(tidy)·PERF(faster) 로 갈리고, 둘 다 모델이 배우지 않은 이유다.
    record = make_record(commit_message="Tidy things. Make it faster. more layering layering")
    result = clf.Classifier(model=trained_model("DEAD")).classify(record)

    assert result.label == "DEAD"
    assert (result.evidence_grade, result.confidence) == ("INFERRED", clf.INFERRED_FLOOR)
    assert "DESIGN" in result.note


def test_llm_breaks_a_tie_among_labels_the_evidence_allows():
    record = make_record(commit_message="Remove unused code. Refactor the module.")
    without = clf.Classifier().classify(record)
    with_llm = clf.Classifier(llm=fake_llm("DESIGN|구조를 바꿨다")).classify(record)

    assert without.label == "DEAD"  # 동점이면 가이드 §11-1 우선순위 (DEAD > DESIGN)
    assert with_llm.label == "DESIGN"


def test_llm_writes_the_evidence_sentence_only_for_inferred_from_replacement():
    """EXPLICIT 은 원문 인용이어야 해서 LLM 이 쓰지 않는다. 대체 코드 근거는 써 줄 수 있다."""
    record = make_record(
        replacement={
            "code": "def backoff(): ...",
            "match_method": "SAME_LOCATION",
            "confidence": 0.9,
        },
    )
    result = clf.Classifier(llm=fake_llm("LIB|urllib3 Retry 로 바꿨다")).classify(record)

    assert (result.label, result.evidence_text) == ("LIB", "urllib3 Retry 로 바꿨다")
    assert "LLM" in result.note


def test_llm_failure_does_not_stop_classification():
    def broken(system, prompt, model):
        raise OSError("network down")

    candidate = clf.LlmCandidate(LlmBaseline(caller=broken))
    result = clf.Classifier(llm=candidate).classify(make_record(commit_message="Remove unused."))

    assert result.label == "DEAD"
    assert candidate.failures == {"호출 실패: OSError": 1}


def test_llm_prompt_carries_context_locators_and_replacement():
    """기준선 B 와 달리 맥락 전체와 대체 코드를 준다 - 그 차이가 우리 방식이다."""
    record = make_record(
        pr_number=12,
        pr_body="Switched to urllib3 Retry.",
        replacement={
            "code": "def backoff(): ...",
            "match_method": "SAME_LOCATION",
            "confidence": 0.9,
        },
    )
    prompt = clf.build_candidate_prompt(record)

    assert "[pr:#12#body] Switched to urllib3 Retry." in prompt
    assert "def backoff(): ..." in prompt


# --------------------------------------------------------------------------------------
# 출력 형식
# --------------------------------------------------------------------------------------


def test_schema_reason_has_exactly_the_charter_fields():
    """§4.4 `reason` 과 이름·개수가 같아야 한다 (§13). 로케이터는 칸이 없어 넣지 않는다."""
    result = clf.Classifier().classify(make_record(commit_message="Remove unused."))

    assert set(result.to_schema_reason()) == {
        "label",
        "evidence_grade",
        "evidence_text",
        "confidence",
        "classifier_version",
    }


def test_prediction_file_uses_predicted_label_not_reason_label():
    """사람 라벨(`reason_label`)과 이름을 갈라 섞이면 바로 보이게 한다 (ADR-005)."""
    row = clf.Classifier().classify(make_record(commit_message="Remove unused.")).to_dict()

    assert row["predicted_label"] == "DEAD"
    assert "reason_label" not in row
    assert row["method"] == clf.METHOD_OURS


def test_classification_rejects_labels_outside_the_eight():
    with pytest.raises(ValueError):
        clf.Classification("r", "MAYBE", "EXPLICIT", "", "", "", 1.0)


# --------------------------------------------------------------------------------------
# 모델·입력
# --------------------------------------------------------------------------------------


def test_model_gives_probabilities_over_the_reasons_it_saw():
    model = trained_model("DESIGN")
    probabilities = model.predict_proba(make_record(commit_message="more layering"))

    assert set(probabilities) == {"BUG", "DESIGN"}
    assert probabilities["DESIGN"] > probabilities["BUG"]


def test_model_does_not_learn_unk():
    """UNK 는 근거 유무로 정한다. 모델이 텍스트 모양으로 UNK 를 고르면 안 된다."""
    records = [make_record(f"t{i}") for i in range(3)]
    model = ReasonModel().fit(records, ["UNK", "DEAD", "BUG"])

    assert "UNK" not in model.classes


def test_model_with_a_single_reason_stays_untrained_instead_of_crashing():
    records = [make_record(f"t{i}") for i in range(3)]
    model = ReasonModel().fit(records, ["DEAD", "DEAD", "UNK"])

    assert not model.trained
    assert model.predict_proba(records[0]) == {}


def test_only_settled_labels_are_used_for_training():
    """두 사람이 갈려 확정되지 않은 건을 한쪽 라벨로 채우면 그 사람 판단을 정답으로 배운다."""
    rows = [
        {"record_id": "a", "final": {"reason_label": "DEAD"}},
        {"record_id": "b", "final": {"reason_label": None}},
        {"record_id": "c", "final": {}},
    ]

    assert clf.load_final_labels(rows) == {"a": "DEAD"}


def test_cli_runs_end_to_end_without_an_api_key(tmp_path, capsys):
    records = [
        make_record("a", commit_message="Remove unused helpers."),
        make_record("b", commit_message="Refactor retry module."),
        make_record("c", commit_message="Update things"),
    ]
    labels = [
        {"record_id": "a", "final": {"reason_label": "DEAD"}},
        {"record_id": "b", "final": {"reason_label": "DESIGN"}},
    ]
    records_path, labels_path = tmp_path / "records.jsonl", tmp_path / "labels.jsonl"
    out_path = tmp_path / "out.jsonl"
    records_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    labels_path.write_text("\n".join(json.dumps(r) for r in labels), encoding="utf-8")

    code = clf.main(
        ["--records", str(records_path), "--labels", str(labels_path), "--out", str(out_path)]
    )

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    assert code == 0
    assert [row["predicted_label"] for row in rows] == ["DEAD", "DESIGN", "UNK"]
    assert "정확도를 말하지 않는다" in capsys.readouterr().out
