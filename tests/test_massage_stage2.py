import ast
import io
import json
from pathlib import Path

import pytest

import eval.massage_stage2 as stage2
from classify.baseline_llm import NVIDIA_API_URL, LlmBaseline
from eval.massage import UNK, Case
from eval.massage_stage2 import (
    FLASH,
    BlendInput,
    CallStats,
    MassageCandidate,
    StageConfig,
    StallError,
    blend_rules,
    blend_table,
    build_c4_prompt,
    build_c5_prompt,
    commit_of,
    config_table,
    cost_of,
    derived_labels,
    disagree_to_unk,
    fill_cache,
    format_report,
    free_nvidia_caller,
    isolate_free_key,
    low_score_disagree_to_unk,
    low_score_to_unk,
    majority,
    normalize_answer,
    paired_with_ours,
    pick_best,
    sample_version,
    score_labels,
    select_few_shots,
    unk_follow_ab_agree,
    unk_follow_b,
    vote,
    weak_unk_follow_b,
    wilson,
)


def case(record_id, final, accepted=None, ours=UNK, a=UNK, b=UNK, score=1.0):
    return Case(
        record_id=record_id,
        final=final,
        accepted=frozenset(accepted or {final}),
        grade="UNKNOWN" if final == UNK else "INFERRED",
        filter_miss=False,
        confidence=0.0,
        score=score,
        predictions={"ours": ours, "a": a, "b": b},
    )


# --- 0단계: 섞기 규칙 -------------------------------------------------------------------


def test_unk_follow_b_only_changes_unk():
    assert unk_follow_b(BlendInput(UNK, "BUG", "DEAD", 1.0)) == "DEAD"
    assert unk_follow_b(BlendInput("FEAT", "BUG", "DEAD", 1.0)) == "FEAT"


def test_disagree_to_unk_keeps_agreement():
    assert disagree_to_unk(BlendInput("DEAD", UNK, "DEAD", 0.1)) == "DEAD"
    assert disagree_to_unk(BlendInput("DEAD", UNK, "FEAT", 9.0)) == UNK


def test_unk_follow_ab_agree_needs_both():
    assert unk_follow_ab_agree(BlendInput(UNK, "DEAD", "DEAD", 1.0)) == "DEAD"
    assert unk_follow_ab_agree(BlendInput(UNK, "DEAD", "FEAT", 1.0)) == UNK


def test_majority_falls_back_to_ours_when_split():
    assert majority(BlendInput("DEAD", "FEAT", "LIB", 1.0)) == "DEAD"
    assert majority(BlendInput("DEAD", "FEAT", "FEAT", 1.0)) == "FEAT"


def test_score_thresholds_are_strictly_below():
    rule = low_score_to_unk(0.7)
    assert rule(BlendInput("DEAD", UNK, UNK, 0.69)) == UNK
    assert rule(BlendInput("DEAD", UNK, UNK, 0.7)) == "DEAD"
    assert rule(BlendInput(UNK, UNK, "DEAD", 0.1)) == UNK


def test_low_score_disagree_keeps_b_agreement():
    rule = low_score_disagree_to_unk(0.7)
    assert rule(BlendInput("DEAD", UNK, "DEAD", 0.1)) == "DEAD"
    assert rule(BlendInput("DEAD", UNK, "FEAT", 0.1)) == UNK
    assert rule(BlendInput("DEAD", UNK, "FEAT", 0.8)) == "DEAD"


def test_weak_unk_follow_b_only_when_score_low():
    rule = weak_unk_follow_b(0.7)
    assert rule(BlendInput(UNK, UNK, "DEAD", 0.5)) == "DEAD"
    assert rule(BlendInput(UNK, UNK, "DEAD", 0.9)) == UNK


def test_score_labels_counts_both_criteria():
    # DEAD 예측이 정답 FEAT 와 같은 묶음(정리)이라 lenient 만 맞다.
    cases = [case("1", "FEAT"), case("2", UNK, accepted={UNK, "DEAD"}), case("3", "BUG")]
    labels = {"1": "DEAD", "2": "DEAD", "3": "BUG"}
    assert score_labels(cases, labels) == {"strict": 1, "lenient": 3}


def test_blend_table_includes_identity_rule_with_original_counts():
    cases = [case("1", "DEAD", ours="DEAD", score=0.5), case("2", UNK, ours=UNK, b="DEAD")]
    inputs = {
        c.record_id: BlendInput(c.predictions["ours"], UNK, c.predictions["b"], c.score)
        for c in cases
    }
    table = {row["name"]: row for row in blend_table(cases, inputs, blend_rules())}
    assert table["massage/blend:ours"]["strict"] == 2
    assert table["massage/blend:unk→b"]["strict"] == 1
    assert table["massage/blend:ours"]["name"].startswith("massage/")


# --- few-shot --------------------------------------------------------------------------


def _label_row(record_id, split, label, *, agreed=True, evidence="근거 문장", note=""):
    other = label if agreed else "DEAD" if label != "DEAD" else "FEAT"
    return {
        "record_id": record_id,
        "split": split,
        "final": {"reason_label": label, "evidence_text": evidence if label != UNK else None},
        "labels": [
            {"reason_label": label, "note": note},
            {"reason_label": other, "note": note},
        ],
    }


def _record(record_id, commit, *, test=False, body="def f():\n    pass\n"):
    return {
        "record_id": record_id,
        "source_url": f"https://github.com/o/r/commit/{commit}",
        "file_path": "a.py",
        "function_name": "f",
        "is_test_code": test,
        "deleted_body": body,
        "context": {"commit_message": f"remove f in {commit}"},
    }


QUOTA = {("UNK", False): 2, ("DEAD", False): 1, ("DEAD", True): 1}


def test_few_shots_never_use_other_splits_or_their_commits():
    rows = [
        _label_row("t", "train", UNK),
        _label_row("leak", "train", UNK),  # test 와 같은 커밋
        _label_row("x", "test", UNK),
        _label_row("v", "val", UNK),
    ]
    records = {
        "t": _record("t", "c1"),
        "leak": _record("leak", "c2"),
        "x": _record("x", "c2"),
        "v": _record("v", "c3"),
    }
    chosen = [row["record_id"] for row in select_few_shots(rows, records, QUOTA)]
    assert chosen == ["t"]


def test_few_shots_skip_disagreements_and_reasons_without_evidence():
    rows = [
        _label_row("split", "train", "DEAD", agreed=False),
        _label_row("empty", "train", "DEAD", evidence=None),
        _label_row("good", "train", "DEAD"),
    ]
    records = {key: _record(key, f"c-{key}") for key in ("split", "empty", "good")}
    chosen = [row["record_id"] for row in select_few_shots(rows, records, QUOTA)]
    assert chosen == ["good"]


def test_few_shots_fill_quota_by_test_flag_with_distinct_commits():
    rows = [
        _label_row("a", "train", "DEAD"),
        _label_row("b", "train", "DEAD"),
        _label_row("c", "train", "DEAD"),
        _label_row("d", "train", "DEAD"),
    ]
    records = {
        "a": _record("a", "c1"),
        "b": _record("b", "c2", body="y" * 100),
        "c": _record("c", "c1", test=True, body="x" * 200),  # 커밋이 a 와 겹쳐 빠진다
        "d": _record("d", "c3", test=True),
    }
    chosen = {row["record_id"] for row in select_few_shots(rows, records, QUOTA)}
    assert chosen == {"a", "d"}


def test_commit_of_reads_url_tail():
    assert commit_of({"source_url": "https://github.com/o/r/commit/abc"}) == "abc"


def test_prompts_add_to_c3_in_order():
    record = _record("r", "c1")
    c4 = build_c4_prompt(record)
    c5 = build_c5_prompt(record, ["예시 본문"])
    assert "UNK 판단 기준" in c4 and "구별 검사" in c4
    assert c4.index("UNK 판단 기준") < c4.index("파일: a.py")
    assert c5.index("UNK 판단 기준") < c5.index("예시 본문") < c5.index("파일: a.py")


# --- 유료 경로 차단 -----------------------------------------------------------------------


def test_module_does_not_reference_paid_paths():
    tree = ast.parse(Path(stage2.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
    }
    assert not imported & {"PROVIDERS", "caller_from_env", "anthropic_caller", "anthropic"}


def test_isolate_free_key_removes_paid_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "paid")
    monkeypatch.setenv("OPENAI_API_KEY", "paid")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("NVIDIA_API_KEY=free\nANTHROPIC_API_KEY=paid2\n", encoding="utf-8")
    assert isolate_free_key(env) == "free"
    assert "ANTHROPIC_API_KEY" not in stage2.os.environ
    assert "OPENAI_API_KEY" not in stage2.os.environ


def test_caller_refuses_models_outside_allowlist():
    with pytest.raises(ValueError):
        free_nvidia_caller("k", StageConfig("x", "c4", "anthropic/claude"), CallStats())
    caller = free_nvidia_caller("k", StageConfig("x", "c4", FLASH), CallStats())
    with pytest.raises(ValueError):
        caller("s", "p", "openai/gpt")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_caller_only_calls_nvidia_with_config_settings():
    seen = []

    def urlopen(request, timeout):
        seen.append((request.full_url, json.loads(request.data), timeout))
        body = {"choices": [{"message": {"content": "UNK|근거"}}]}
        return _Response(json.dumps(body).encode("utf-8"))

    stats = CallStats()
    config = StageConfig("x", "c4", stage2.NEMOTRON, temperature=0.7)
    caller = free_nvidia_caller("k", config, stats, urlopen=urlopen)
    assert caller("s", "p", config.model) == "UNK|근거"
    url, payload, timeout = seen[0]
    assert url == NVIDIA_API_URL and timeout == 300
    assert payload["temperature"] == 0.7
    assert payload["max_tokens"] == stage2.THINKING_MAX_TOKENS
    assert stats.calls == 1


def test_caller_stops_after_stall_limit():
    stats = CallStats()
    stats.last_success -= 100
    caller = free_nvidia_caller("k", StageConfig("x", "c4", FLASH), stats, stall_limit=10)
    with pytest.raises(StallError):
        caller("s", "p", FLASH)
    assert stats.stalled


# --- 가짜 LLM ---------------------------------------------------------------------------


def test_vote_needs_majority():
    assert vote(["DEAD", "DEAD", "UNK"]) == "DEAD"
    assert vote(["DEAD", "FEAT", "UNK"]) == UNK
    assert vote(["DESIGN"]) == "DESIGN"


def test_normalize_answer_strips_markup():
    assert normalize_answer("**UNK**|`근거`") == "UNK|근거"


def test_sample_version_differs_per_sample_only_when_sampling():
    assert sample_version(StageConfig("x", "c5", FLASH), 1) == "c5"
    many = StageConfig("x", "c5", FLASH, samples=3, temperature=0.7)
    assert sample_version(many, 1) != sample_version(many, 2)


def _runner(answers):
    return LlmBaseline(lambda _s, _p, _m: answers.pop(0), model=FLASH)


def test_candidate_votes_across_samples():
    samplers = [_runner(["DEAD|a"]), _runner(["UNK|b"]), _runner(["DEAD|c"])]
    candidate = MassageCandidate(samplers[0], prompt_builder=lambda _r: "p", samplers=samplers)
    assert candidate.propose({"record_id": "r"}) == ("DEAD", "a")
    assert candidate.votes["r"] == ["DEAD", "UNK", "DEAD"]


def test_candidate_gives_no_vote_when_every_sample_fails():
    def boom(_s, _p, _m):
        raise OSError("down")

    runner = LlmBaseline(boom, model=FLASH)
    candidate = MassageCandidate(runner, prompt_builder=lambda _r: "p", samplers=[runner])
    assert candidate.propose({"record_id": "r"}) is None
    assert candidate.votes["r"] == []
    assert candidate.failures == {"호출 실패: OSError": 1}


def test_fill_cache_retries_and_resumes_from_cache(tmp_path):
    attempts = []

    def flaky(_s, prompt, _m):
        attempts.append(prompt)
        if attempts.count(prompt) == 1 and prompt == "p2":
            raise OSError("끊김")
        return "UNK|근거"

    runner = LlmBaseline(flaky, model=FLASH, cache_dir=tmp_path, prompt_version="c4")
    failures = fill_cache([runner], ["p1", "p2"], CallStats(), workers=1)
    assert failures["미응답"] == 0 and failures["OSError"] == 1
    assert sorted(attempts) == ["p1", "p2", "p2"]

    # 이어서 돌리면 캐시만 읽는다.
    attempts.clear()
    fill_cache([runner], ["p1", "p2"], CallStats(), workers=1)
    assert attempts == []


def test_fill_cache_reports_unanswered_after_rounds():
    def down(_s, _p, _m):
        raise OSError("down")

    runner = LlmBaseline(down, model=FLASH)
    failures = fill_cache([runner], ["p"], CallStats(), workers=1, rounds=2)
    assert failures == {"OSError": 2, "미응답": 1}


# --- 집계·보고서 ---------------------------------------------------------------------------


def test_config_table_and_report_use_massage_names(monkeypatch):
    cases = [case("1", "DEAD"), case("2", UNK)]
    rows = [
        {"record_id": "1", "predicted_label": "DEAD", "llm_votes": ["UNK"], "llm_failed": False},
        {"record_id": "2", "predicted_label": UNK, "llm_votes": [], "llm_failed": True},
    ]
    runs = {
        "s1": {"prompt": "c4", "model": FLASH, "samples": 1, "elapsed_seconds": 90.0, "calls": 2}
    }
    table = config_table(cases, {"s1": rows}, runs)
    row = table[0]
    assert row["name"] == "massage/s1"
    assert row["strict"] == 2 and row["no_llm_vote"] == 1
    assert row["llm_only"]["strict"] == 1

    zero = {"strict": 0, "lenient": 0}
    result = {
        "best": row,
        "floor": {"a": zero, "b": zero, "unk": zero},
        "blends": [],
        "configs": table,
        "derived": [],
        "tried": 1,
        "best_cost": "s1",
        "best_paired": {
            "strict": {"new_only": 1, "ours_only": 0, "p": 1.0},
            "lenient": {"new_only": 1, "ours_only": 0, "p": 1.0},
        },
        "best_confusion": {"정답 UNK / 예측 UNK": 1},
    }
    notes = ["읽는 법 자리"]
    monkeypatch.setattr(stage2, "reading_notes", lambda _r, _c: notes)
    text = format_report(result)
    assert "최고 설정: `massage/s1`" in text
    assert "게이트 2 결과가 아니다" in text
    assert "1.5분" in text


def test_pick_best_prefers_lenient_then_strict():
    rows = [
        {"name": "a", "lenient": 80, "strict": 50},
        {"name": "b", "lenient": 80, "strict": 60},
        {"name": "c", "lenient": 79, "strict": 90},
    ]
    assert pick_best(rows)["name"] == "b"


def test_failed_config_is_left_out_of_derived_rows():
    def rows(votes):
        return [
            {"record_id": str(i), "llm_votes": v, "llm_failed": not v} for i, v in enumerate(votes)
        ]

    config_rows = {
        "s1": rows([["DEAD"], ["UNK"]]),
        "s2": rows([["DEAD"], ["FEAT"]]),
        "s3": rows([["FEAT"], ["LIB"]]),
        "s4": rows([[], []]),
    }
    derived = derived_labels(config_rows)
    assert "massage/s4:llm-only" not in derived
    assert all("s4" not in name for name in derived)
    # 둘째 레코드는 세 답이 갈려 UNK 다.
    assert derived["massage/vote:s1+s2+s3"] == {"0": "DEAD", "1": UNK}


def test_wilson_matches_gate2_report_interval():
    low, high = wilson(60)
    assert round(100 * low, 1) == 50.2 and round(100 * high, 1) == 69.1


def test_paired_counts_one_sided_wins_with_exact_p():
    cases = [case(str(i), "DEAD", ours="DEAD" if i == 0 else UNK) for i in range(6)]
    labels = {str(i): "DEAD" if i else UNK for i in range(6)}
    strict = paired_with_ours(cases, labels)["strict"]
    assert strict["new_only"] == 5 and strict["ours_only"] == 1
    assert strict["p"] == pytest.approx(14 / 64)


def test_cost_sums_only_named_configs():
    configs = [
        {"config": "s1", "elapsed_seconds": 60.0, "calls": 10},
        {"config": "s3", "elapsed_seconds": 120.0, "calls": 20},
        {"config": "s6", "elapsed_seconds": 60.0, "calls": 30},
    ]
    assert "호출 40회" in cost_of("massage/vote:s1+s6", configs)
    assert "호출 20회" in cost_of("massage/s3:llm-only", configs)
    assert cost_of("massage/blend:ours", configs) == "API 호출 없음"
