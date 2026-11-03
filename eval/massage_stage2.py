"""test 100건 massage 2단계 - 섞기 규칙과 LLM·프롬프트 조정. 담당: 성제 (sj)

게이트 2 판정이 아니다. 판정은 사전 등록 기준의 60.0% 미달 그대로다 (ADR-016).
이 모듈의 숫자는 전부 test 를 보면서 고른 설정의 값이다.
정리는 `docs/reports/classifier_improvement.md`.
`classify/` 는 고치지 않는다. 프롬프트 c4 이후는 여기에만 있다.

0단계 (API 없음):
    커밋된 test 예측(우리 방식, 기준선 A, B)과 점수만으로 섞는 규칙을 시험한다.

1단계 (API):
    우리 방식의 나머지(규칙 r1 + 모델 m2, 학습+검증 400건 학습, `WEIGHT_LLM`)는 그대로 두고
    LLM 프롬프트·모델·호출 횟수만 바꾼다. 설정은 `CONFIGS`. 답은 `CACHE_DIR` 에 남아 이어서 돈다.

유료 API 금지:
    `classify.baseline_llm.PROVIDERS`·`caller_from_env` 를 쓰지 않는다. `.env` 에서 NVIDIA 키만
    읽고, 다른 공급자 키는 환경에서 지운다. 호출기는 `NVIDIA_API_URL` 만 부르고 `FREE_MODELS`
    밖의 모델은 거부한다. 무료 표시는 2026-11-03 에 build.nvidia.com 모델 쪽에서 확인했다.

few-shot:
    train split 에서만 고르고, val·test 레코드와 같은 커밋은 뺀다 (`select_few_shots`).
    예시의 라벨은 사람이 확정한 것이다. LLM 답은 예측 파일에만 쓴다 (ADR-005).

실행:
    python -m eval.massage_stage2 run --config s1
    python -m eval.massage_stage2 check-c3    # API 없이 c3 캐시로 게이트 2 예측 재현
    python -m eval.massage_stage2 report      # 저장된 예측으로 stage2.md·json 재생성
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path
from typing import Any

from classify.baseline_llm import (
    CALL_ERRORS,
    NVIDIA_API_URL,
    NVIDIA_MIN_INTERVAL_SECONDS,
    NVIDIA_TIMEOUT_SECONDS,
    SHAPE_ERRORS,
    LlmBaseline,
    parse_answer,
)
from classify.baselines import record_id_of
from classify.classifier import (
    CANDIDATE_GUIDE,
    Classifier,
    LlmCandidate,
    build_candidate_prompt,
    load_final_labels,
    load_jsonl,
    train_model,
)
from classify.rules import passages
from eval.massage import (
    DEFAULT_LABELS,
    DEFAULT_PREDICTIONS,
    G_FINAL,
    PREFIX,
    UNK,
    Case,
    accuracy_counts,
    group_map,
    is_correct,
    load_cases,
)
from pipeline.select_repos import parse_dotenv

DEFAULT_RECORDS = Path("datasets/labels/main500_records.jsonl")
CACHE_DIR = Path("datasets/llm_cache/massage")
C3_CACHE_DIR = Path("datasets/llm_cache/classifier")
OUT_DIR = Path("docs/reports/massage")
PREDICTIONS_DIR = OUT_DIR / "stage2"
RUNS_PATH = OUT_DIR / "stage2_runs.json"
STAGE1_STRICT = 60
STAGE1_LENIENT = 78
STALL_LIMIT_SECONDS = 30 * 60
ROUNDS = 3
RATE_LIMIT_WAIT_SECONDS = 30
WORKERS = 4

FLASH = "deepseek-ai/deepseek-v4.1-flash"
NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
MISTRAL = "mistralai/mistral-large-4-1t-a52b"
FREE_MODELS = frozenset({FLASH, NEMOTRON, MISTRAL})
# 생각 과정을 끌 수 없는 모델은 생각 토큰이 상한을 다 써 답이 빈 채로 온다 (`baseline_llm` 주석).
THINKING_MAX_TOKENS = 4096
FLASH_MAX_TOKENS = 256
FLASH_OPTIONS: Mapping[str, Any] = {"chat_template_kwargs": {"thinking": False}}

# 라벨링 가이드 §5 "무엇이든 vs UNK", §6.2.1, §6.3.1 을 프롬프트 길이에 맞게 줄였다.
UNK_CRITERIA = """UNK 판단 기준 (이유 라벨을 고르기 전에 먼저 본다):
UNK 는 실패가 아니라 "이 레코드에는 판단할 재료가 없다"는 판정이다. 이유 라벨은 아래 (가)나 (나)를 \
화면에서 짚을 수 있을 때만 고른다.
(가) 삭제의 "왜"를 말하는 문장이 이 함수·파일·기능을 이름으로 가리킨다
(나) 이 레코드의 근거 6종 중 하나가 화면에 보인다
  ① 대체 코드: 삭제된 일을 무엇이 이어받았는지 특정된다
  ② 호출자 변화: 추가된 코드에 이 함수를 부르던 자리의 변화가 보인다
  ③ 테스트 변화: 비테스트 함수를 검증하던 테스트의 변경이 보인다 (테스트 자신이 지워진 것은 아니다)
  ④ 공개 표면 변화: CLI 플래그·공개 API·설정·문서·CHANGELOG 가 함께 바뀐 것이 보인다
  ⑤ 동형 삭제: 같은 패턴의 다른 삭제와 그 이유가 보이고 이 레코드가 그 패턴에 속한다
  ⑥ 삭제된 본문: 본문 내용이 이유를 드러낸다 (파일명·함수명 낱말 backup·old·legacy 만으로는 아니다)
다음이면 UNK:
- U1 맥락이 없고 커밋 메시지가 한 줄 사실 서술뿐이다
- U2 cleanup·refactor·uprev·bump·Update X 류이거나 삭제 사실만 말한다
- U3 이유 문장이 커밋 전체만 말하고 위 6종이 하나도 없다 ("큰 작업 X 를 하는데 이 함수가 그 영역에 \
있다"는 근거가 아니다)
- U4 대체 코드가 (없음)이고 호출자 변화도 화면으로 알 수 없다. "(없음)"은 대체 코드가 \
없다는 증거가 아니다
구별 검사: 네 근거 문장에서 함수 이름만 바꿔도 같은 커밋의 다른 삭제에 들어맞으면 근거가 \
아니다 → UNK.
"아마", "~일 것 같다"로만 쓸 수 있으면 UNK.
반대로 다음은 UNK 가 아니다: 근거를 짚은 한 문장을 쓸 수 있다 / 후보가 둘 이상인데 각각 근거가 \
있다 / 8종에 딱 맞는 것이 없다고 느낀다 (DESIGN 이 넓은 범주다)."""

MAX_SHOT_CONTEXT_CHARS = 500
MAX_SHOT_CODE_CHARS = 300
# (라벨, 테스트 코드 여부) 마다 정한 수. test 분포를 보고 맞추지 않았다. 테스트 함수 칸은 가이드
# 규칙 2 (근거 없으면 UNK, 대상 삭제가 보이면 DEAD) 를 보여 주려고 둔다.
FEW_SHOT_QUOTA: Mapping[tuple[str, bool], int] = {
    ("UNK", False): 3,
    ("UNK", True): 1,
    ("DESIGN", False): 2,
    ("DEAD", False): 1,
    ("DEAD", True): 1,
    ("FEAT", False): 1,
    ("LIB", False): 1,
}
EMPTY_TEXT = re.compile(r"\s+")


# --------------------------------------------------------------------------------------
# 0단계: 섞기 규칙
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BlendInput:
    ours: str
    a: str
    b: str
    score: float


BlendRule = Callable[[BlendInput], str]
THRESHOLDS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.2)


def unk_follow_b(row: BlendInput) -> str:
    return row.b if row.ours == UNK else row.ours


def unk_follow_a(row: BlendInput) -> str:
    return row.a if row.ours == UNK else row.ours


def unk_follow_ab_agree(row: BlendInput) -> str:
    return row.b if row.ours == UNK and row.a == row.b else row.ours


def b_unk_to_unk(row: BlendInput) -> str:
    return UNK if row.b == UNK else row.ours


def disagree_to_unk(row: BlendInput) -> str:
    return row.ours if row.ours == row.b else UNK


def design_needs_b(row: BlendInput) -> str:
    return UNK if row.ours == "DESIGN" and row.b != "DESIGN" else row.ours


def majority(row: BlendInput) -> str:
    votes = Counter((row.ours, row.a, row.b))
    label, count = votes.most_common(1)[0]
    return label if count >= 2 else row.ours


def low_score_to_unk(threshold: float) -> BlendRule:
    def rule(row: BlendInput) -> str:
        return UNK if row.ours != UNK and row.score < threshold else row.ours

    return rule


def low_score_disagree_to_unk(threshold: float) -> BlendRule:
    def rule(row: BlendInput) -> str:
        return UNK if row.ours not in (UNK, row.b) and row.score < threshold else row.ours

    return rule


def weak_unk_follow_b(threshold: float) -> BlendRule:
    def rule(row: BlendInput) -> str:
        return row.b if row.ours == UNK and row.score < threshold else row.ours

    return rule


def blend_rules() -> dict[str, BlendRule]:
    rules: dict[str, BlendRule] = {
        "ours": lambda row: row.ours,
        "unk→b": unk_follow_b,
        "unk→a": unk_follow_a,
        "unk→a=b": unk_follow_ab_agree,
        "b-unk→unk": b_unk_to_unk,
        "불일치→unk": disagree_to_unk,
        "design-b확인": design_needs_b,
        "다수결(ours,a,b)": majority,
    }
    for threshold in THRESHOLDS:
        rules[f"점수<{threshold}→unk"] = low_score_to_unk(threshold)
        rules[f"점수<{threshold}+불일치→unk"] = low_score_disagree_to_unk(threshold)
        rules[f"unk점수<{threshold}→b"] = weak_unk_follow_b(threshold)
    return rules


def apply_labels(cases: Sequence[Case], method: str, labels: Mapping[str, str]) -> list[Case]:
    """각 레코드 예측에 `method` 이름으로 라벨을 더한다. 빠진 레코드가 있으면 오류."""
    return [
        replace(case, predictions={**case.predictions, method: labels[case.record_id]})
        for case in cases
    ]


def score_labels(cases: Sequence[Case], labels: Mapping[str, str]) -> dict[str, int]:
    """두 기준으로 센다. strict = 8종 + 확정 라벨, lenient = G-final + 라벨러 누구든."""
    scored = apply_labels(cases, "x", labels)
    return {
        "strict": accuracy_counts(scored, "x", group_map(None), lenient=False),
        "lenient": accuracy_counts(scored, "x", group_map(G_FINAL), lenient=True),
    }


def blend_table(
    cases: Sequence[Case], rows: Mapping[str, BlendInput], rules: Mapping[str, BlendRule]
) -> list[dict[str, Any]]:
    table = []
    for name, rule in rules.items():
        labels = {record_id: rule(row) for record_id, row in rows.items()}
        table.append(
            {
                "name": f"{PREFIX}/blend:{name}",
                "unk_predicted": sum(label == UNK for label in labels.values()),
                **score_labels(cases, labels),
            }
        )
    return sorted(table, key=lambda row: (-row["lenient"], -row["strict"], row["name"]))


def blend_inputs(cases: Sequence[Case]) -> dict[str, BlendInput]:
    return {
        case.record_id: BlendInput(
            case.predictions["ours"], case.predictions["a"], case.predictions["b"], case.score
        )
        for case in cases
    }


# --------------------------------------------------------------------------------------
# 1단계: 프롬프트·설정
# --------------------------------------------------------------------------------------


def commit_of(record: Mapping[str, Any]) -> str:
    return str(record.get("source_url") or "").rsplit("/", 1)[-1]


def _agreed(row: Mapping[str, Any]) -> bool:
    final = row["final"]["reason_label"]
    return all(label["reason_label"] == final for label in row["labels"])


def _shot_answer(row: Mapping[str, Any]) -> str:
    """예시의 답 줄. 사람이 쓴 근거를 쓰고, UNK 는 무엇이 없었는지 태그로 말한다."""
    final = row["final"]
    if final["reason_label"] != UNK:
        text = EMPTY_TEXT.sub(" ", final.get("evidence_text") or "").strip()
        return f"{final['reason_label']}|{text[:200]}"
    notes = " ".join(label.get("note") or "" for label in row["labels"])
    tags = [
        tag
        for tag in (
            "filter-miss",
            "no-context",
            "vague-message",
            "no-replacement",
            "no-caller-info",
        )
        if tag in notes
    ]
    return f"UNK|이 레코드까지 잇는 이유 문장도 근거 6종도 화면에 없다 ({', '.join(tags)})"


def render_shot(record: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    context = " / ".join(EMPTY_TEXT.sub(" ", passage.text) for passage in passages(dict(record)))[
        :MAX_SHOT_CONTEXT_CHARS
    ]
    code = (record.get("deleted_body") or "")[:MAX_SHOT_CODE_CHARS]
    return (
        f"파일: {record.get('file_path') or ''}\n"
        f"함수: {record.get('function_name') or ''}\n"
        f"테스트 코드: {'예' if record.get('is_test_code') else '아니오'}\n"
        f"맥락: {context or '(맥락 없음)'}\n"
        f"삭제된 코드:\n```\n{code}\n```\n"
        f"답: {_shot_answer(row)}"
    )


def select_few_shots(
    label_rows: Sequence[Mapping[str, Any]],
    records_by_id: Mapping[str, Mapping[str, Any]],
    quota: Mapping[tuple[str, bool], int] = FEW_SHOT_QUOTA,
) -> list[Mapping[str, Any]]:
    """예시 행을 정해진 규칙으로 고른다. test 정답을 보지 않는다.

    train split 에서 라벨러 둘이 일치한 건만, val·test 와 같은 커밋은 빼고, 칸마다 맥락이 있고
    짧은 순으로 고른다. 커밋은 겹치지 않게 한다 - 같은 커밋의 예시가 여럿이면 프롬프트가
    "이 커밋이면 이 라벨" 로 읽힌다. 근거 없는 답을 보여 주지 않게 이유 라벨은 근거 문장이
    있어야 한다.
    """
    test_commits = {
        commit_of(records_by_id[row["record_id"]])
        for row in label_rows
        if row["split"] != "train" and row["record_id"] in records_by_id
    }
    candidates = []
    for row in label_rows:
        record = records_by_id.get(row["record_id"])
        if row["split"] != "train" or record is None or not _agreed(row):
            continue
        if commit_of(record) in test_commits or not passages(dict(record)):
            continue
        if row["final"]["reason_label"] != UNK and not row["final"].get("evidence_text"):
            continue
        candidates.append((len(render_shot(record, row)), row["record_id"], row))
    chosen: list[Mapping[str, Any]] = []
    used_commits: set[str] = set()
    counts: Counter[tuple[str, bool]] = Counter()
    for _length, _record_id, row in sorted(candidates, key=lambda item: item[:2]):
        record = records_by_id[row["record_id"]]
        slot = (row["final"]["reason_label"], bool(record.get("is_test_code")))
        commit = commit_of(record)
        if counts[slot] < quota.get(slot, 0) and commit not in used_commits:
            chosen.append(row)
            counts[slot] += 1
            used_commits.add(commit)
    return chosen


def build_c4_prompt(record: dict[str, Any]) -> str:
    """c3 + UNK 판단 기준."""
    return build_candidate_prompt(record).replace(
        CANDIDATE_GUIDE, f"{CANDIDATE_GUIDE}\n\n{UNK_CRITERIA}", 1
    )


def few_shot_block(shots: Sequence[str]) -> str:
    joined = "\n\n".join(f"[예시 {index}]\n{shot}" for index, shot in enumerate(shots, start=1))
    return f"사람이 확정한 예시 (형식과 UNK 기준을 보라, 내용을 끌어다 쓰지 않는다):\n\n{joined}"


def build_c5_prompt(record: dict[str, Any], shots: Sequence[str]) -> str:
    """c4 + train 예시."""
    guide = f"{CANDIDATE_GUIDE}\n\n{UNK_CRITERIA}"
    return build_c4_prompt(record).replace(guide, f"{guide}\n\n{few_shot_block(shots)}", 1)


@dataclass(frozen=True)
class StageConfig:
    name: str
    prompt: str  # c3, c4, c5
    model: str
    samples: int = 1
    temperature: float = 0.0

    @property
    def max_tokens(self) -> int:
        return FLASH_MAX_TOKENS if self.model == FLASH else THINKING_MAX_TOKENS

    @property
    def options(self) -> Mapping[str, Any]:
        return FLASH_OPTIONS if self.model == FLASH else {}


# s3 이후의 프롬프트는 s1·s2 를 test 에서 본 뒤에 c4 로 정했다 (c5 가 lenient 75 로 더 낮았다).
CONFIGS: dict[str, StageConfig] = {
    "s1": StageConfig("s1", "c4", FLASH),
    "s2": StageConfig("s2", "c5", FLASH),
    "s3": StageConfig("s3", "c4", NEMOTRON),
    "s4": StageConfig("s4", "c4", MISTRAL),
    # s3 의 LLM 단독이 가장 높아 s5·s6 은 nemotron 으로 간다. s4 는 응답이 없었다 (보고서 §2).
    "s5": StageConfig("s5", "c5", NEMOTRON),
    "s6": StageConfig("s6", "c4", NEMOTRON, samples=3, temperature=0.7),
}


# --------------------------------------------------------------------------------------
# 1단계: 호출
# --------------------------------------------------------------------------------------


@dataclass
class CallStats:
    calls: int = 0
    failed_calls: int = 0
    last_success: float = field(default_factory=time.monotonic)
    stalled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class StallError(Exception):
    """`STALL_LIMIT_SECONDS` 넘게 성공한 응답이 없다. 그때까지의 답으로 정리한다."""


def free_nvidia_caller(
    api_key: str,
    config: StageConfig,
    stats: CallStats,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    stall_limit: float = STALL_LIMIT_SECONDS,
) -> Callable[[str, str, str], str]:
    """NVIDIA 무료 엔드포인트 전용 호출기. 다른 URL·허용 밖 모델은 부르지 않는다.

    `classify.baseline_llm.nvidia_caller` 와 달리 온도·토큰 상한이 설정을 따르고, 스레드 사이에
    호출 간격(무료 한도 분당 40회)을 나눠 쓴다. 시간 초과는 같은 300초다.
    """
    if config.model not in FREE_MODELS:
        raise ValueError(f"무료로 확인하지 않은 모델: {config.model}")
    next_slot = [0.0]
    slot_lock = threading.Lock()

    def call(system: str, prompt: str, model: str) -> str:
        if model not in FREE_MODELS:
            raise ValueError(f"무료로 확인하지 않은 모델: {model}")
        if time.monotonic() - stats.last_success > stall_limit:
            stats.stalled = True
            raise StallError(f"{stall_limit:.0f}초 넘게 응답 없음")
        with slot_lock:
            now = time.monotonic()
            wait = next_slot[0] - now
            next_slot[0] = max(now, next_slot[0]) + NVIDIA_MIN_INTERVAL_SECONDS
        if wait > 0:
            time.sleep(wait)
        payload = json.dumps(
            {
                "model": model,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                **config.options,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            NVIDIA_API_URL,
            data=payload,
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        )
        with stats.lock:
            stats.calls += 1
        try:
            with urlopen(request, timeout=NVIDIA_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
            choices = body.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content") or ""
        except SHAPE_ERRORS as error:
            raise ValueError(f"응답 모양이 다르다: {error!r}") from error
        if text.strip():
            with stats.lock:
                stats.last_success = time.monotonic()
        return text

    return call


def normalize_answer(text: str) -> str:
    """`**UNK**|...` 처럼 꾸민 답을 읽게 한다. 이 모듈의 큰 모델들이 가끔 그렇게 쓴다."""
    return re.sub(r"[*`]", "", text or "")


def vote(labels: Sequence[str]) -> str:
    """다수결. 셋 다 갈리면 근거가 흔들린다는 뜻이라 UNK 로 본다."""
    label, count = Counter(labels).most_common(1)[0]
    return label if count * 2 > len(labels) else UNK


@dataclass
class MassageCandidate(LlmCandidate):
    """c4·c5 프롬프트와 다수결을 쓰는 후보. 실패 시 표를 주지 않는 것은 `LlmCandidate` 와 같다."""

    prompt_builder: Callable[[dict[str, Any]], str] = build_c4_prompt
    samplers: Sequence[LlmBaseline] = ()
    votes: dict[str, list[str]] = field(default_factory=dict)

    def propose(self, record: dict[str, Any]) -> tuple[str, str] | None:
        prompt = self.prompt_builder(record)
        answers: list[tuple[str, str]] = []
        for runner in self.samplers:
            try:
                text = runner.ask(prompt)
            except CALL_ERRORS as error:
                self._fail(f"호출 실패: {type(error).__name__}")
                continue
            label, reason, problem = parse_answer(normalize_answer(text))
            if problem:
                self._fail(problem.split(":")[0])
                continue
            answers.append((label, reason))
        self.votes[record_id_of(record)] = [label for label, _reason in answers]
        if not answers:
            return None
        winner = vote([label for label, _reason in answers])
        reason = next((text for label, text in answers if label == winner), "")
        return winner, reason


@dataclass
class ConfigClassifier(Classifier):
    config_name: str = ""

    @property
    def version(self) -> str:
        return f"{PREFIX}/{self.config_name}"


def sample_version(config: StageConfig, index: int) -> str:
    """캐시 키에 들어가는 프롬프트 버전. 답을 여러 번 받을 때만 온도와 회차를 붙인다."""
    if config.samples == 1:
        return config.prompt
    return f"{config.prompt}@t{config.temperature}#{index}"


def fill_cache(
    runners: Sequence[LlmBaseline],
    prompts: Sequence[str],
    stats: CallStats,
    *,
    workers: int = WORKERS,
    rounds: int = ROUNDS,
) -> dict[str, int]:
    """답을 병렬로 받아 캐시에 넣는다. 실패는 다음 차례에 다시 묻는다.

    라벨은 뒤에서 캐시만 읽어 순서대로 낸다. 응답이 온 순서가 결과에 들어가지 않는다.
    """
    jobs = [(runner, prompt) for runner in runners for prompt in prompts]
    failures: Counter[str] = Counter()

    def ask(job: tuple[LlmBaseline, str]) -> tuple[LlmBaseline, str] | None:
        runner, prompt = job
        try:
            if stats.stalled:
                return job
            answer = runner.ask(prompt)
            if not answer.strip():
                failures["빈 응답"] += 1
                return job
            return None
        except StallError:
            return job
        except urllib.error.HTTPError as error:
            failures[f"HTTP {error.code}"] += 1
            with stats.lock:
                stats.failed_calls += 1
            if error.code == 429:
                time.sleep(RATE_LIMIT_WAIT_SECONDS)
            return job
        except CALL_ERRORS as error:
            failures[type(error).__name__] += 1
            with stats.lock:
                stats.failed_calls += 1
            return job

    for round_index in range(rounds):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            jobs = [job for job in pool.map(ask, jobs) if job is not None]
        print(f"  {round_index + 1}차: 남은 {len(jobs)}건, 호출 {stats.calls}", file=sys.stderr)
        if not jobs or stats.stalled:
            break
    return dict(failures) | {"미응답": len(jobs)}


def isolate_free_key(env_path: Path) -> str:
    """NVIDIA 키만 돌려주고 유료 공급자 키를 이 프로세스에서 지운다."""
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(name, None)
    values = parse_dotenv(env_path.read_text(encoding="utf-8"))
    key = (os.environ.get("NVIDIA_API_KEY") or values.get("NVIDIA_API_KEY") or "").strip()
    if not key:
        raise SystemExit("NVIDIA_API_KEY 가 없다. .env 에 채워라 (§8.4).")
    return key


@dataclass(frozen=True)
class Inputs:
    label_rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    test_records: list[dict[str, Any]]
    shots: list[str]


def load_inputs(labels_path: Path = DEFAULT_LABELS, records_path: Path = DEFAULT_RECORDS) -> Inputs:
    label_rows = load_jsonl(labels_path)
    records = load_jsonl(records_path)
    by_id = {record_id_of(record): record for record in records}
    split = {row["record_id"]: row["split"] for row in label_rows}
    test_records = [record for record in records if split.get(record_id_of(record)) == "test"]
    shots = [
        render_shot(by_id[row["record_id"]], row) for row in select_few_shots(label_rows, by_id)
    ]
    return Inputs(label_rows, records, test_records, shots)


def fit_test_model(inputs: Inputs) -> Any:
    """게이트 2 와 같은 모델 - 학습+검증 400건."""
    split = {row["record_id"]: row["split"] for row in inputs.label_rows}
    training = [r for r in inputs.records if split.get(record_id_of(r)) in {"train", "val"}]
    return train_model(training, load_final_labels(inputs.label_rows))


def prompt_builder_for(config: StageConfig, shots: Sequence[str]) -> Callable[[dict], str]:
    if config.prompt == "c3":
        return build_candidate_prompt
    if config.prompt == "c4":
        return build_c4_prompt
    if config.prompt == "c5":
        return lambda record: build_c5_prompt(record, shots)
    raise ValueError(f"알 수 없는 프롬프트: {config.prompt}")


def classify_all(
    config: StageConfig, inputs: Inputs, samplers: Sequence[LlmBaseline], model: Any
) -> list[dict[str, Any]]:
    candidate = MassageCandidate(
        samplers[0], prompt_builder=prompt_builder_for(config, inputs.shots), samplers=samplers
    )
    classifier = ConfigClassifier(model=model, llm=candidate, config_name=config.name)
    rows = []
    for record in inputs.test_records:
        result = classifier.classify(record).to_dict()
        votes = candidate.votes[record_id_of(record)]
        rows.append({**result, "llm_votes": votes, "llm_failed": not votes})
    return rows


def run_config(config: StageConfig, env_path: Path, out_dir: Path = PREDICTIONS_DIR) -> None:
    """한 설정을 돌려 예측과 실행 기록을 남긴다. 다시 돌리면 캐시에 없는 건만 묻는다."""
    api_key = isolate_free_key(env_path)
    inputs = load_inputs()
    stats = CallStats()
    caller = free_nvidia_caller(api_key, config, stats)
    samplers = [
        LlmBaseline(
            caller,
            model=config.model,
            cache_dir=CACHE_DIR,
            prompt_version=sample_version(config, index),
        )
        for index in range(1, config.samples + 1)
    ]
    builder = prompt_builder_for(config, inputs.shots)
    prompts = [builder(record) for record in inputs.test_records]
    started = time.monotonic()
    failures = fill_cache(samplers, prompts, stats)
    elapsed = time.monotonic() - started
    # 라벨을 낼 때는 네트워크를 쓰지 않는다. 캐시에 없는 건은 표 없이 간다.
    offline = [replace_caller(runner) for runner in samplers]
    rows = classify_all(config, inputs, offline, fit_test_model(inputs))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{config.name}.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    record_run(config, elapsed, stats, failures)
    print(
        f"{config.name}: {elapsed:.0f}초, 호출 {stats.calls}회, 실패 {failures}, "
        f"표 없는 레코드 {sum(row['llm_failed'] for row in rows)}건, 멈춤 {stats.stalled}"
    )


def _no_network(_system: str, _prompt: str, _model: str) -> str:
    raise ValueError("캐시에 없다 (이 단계는 API 를 부르지 않는다)")


def replace_caller(runner: LlmBaseline) -> LlmBaseline:
    return LlmBaseline(
        _no_network,
        model=runner.model,
        cache_dir=runner.cache_dir,
        prompt_version=runner.prompt_version,
    )


def record_run(
    config: StageConfig, elapsed: float, stats: CallStats, failures: Mapping[str, int]
) -> None:
    """실행 기록을 더해 쌓는다. 캐시로 이어 돌린 뒤에도 실제 걸린 시간이 남아야 한다."""
    runs = json.loads(RUNS_PATH.read_text(encoding="utf-8")) if RUNS_PATH.is_file() else {}
    entry = runs.setdefault(
        config.name,
        {
            "prompt": config.prompt,
            "model": config.model,
            "samples": config.samples,
            "temperature": config.temperature,
            "elapsed_seconds": 0.0,
            "calls": 0,
            "failed_calls": 0,
            "attempts": [],
        },
    )
    entry["elapsed_seconds"] = round(entry["elapsed_seconds"] + elapsed, 1)
    entry["calls"] += stats.calls
    entry["failed_calls"] += stats.failed_calls
    entry["attempts"].append(
        {
            "elapsed_seconds": round(elapsed, 1),
            "calls": stats.calls,
            "failures": dict(failures),
            "stalled": stats.stalled,
        }
    )
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNS_PATH.write_text(
        json.dumps(runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def check_c3(inputs: Inputs | None = None) -> int:
    """c3 캐시로 게이트 2 예측이 재현되는지 본다. 이 모듈의 틀이 맞는지 확인하는 용도다."""
    inputs = inputs or load_inputs()
    config = StageConfig("c3-check", "c3", FLASH)
    runner = LlmBaseline(_no_network, model=FLASH, cache_dir=C3_CACHE_DIR, prompt_version="c3")
    rows = classify_all(config, inputs, [runner], fit_test_model(inputs))
    committed = {
        row["record_id"]: row["predicted_label"]
        for row in load_jsonl(DEFAULT_PREDICTIONS / "ours.jsonl")
    }
    differing = [row for row in rows if committed[row["record_id"]] != row["predicted_label"]]
    print(f"c3 재현: 100건 중 다른 예측 {len(differing)}건")
    return len(differing)


# --------------------------------------------------------------------------------------
# 보고서
# --------------------------------------------------------------------------------------


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    return f"{seconds / 60:.1f}분" if seconds >= 60 else f"{seconds:.0f}초"


def load_config_rows(out_dir: Path = PREDICTIONS_DIR) -> dict[str, list[dict[str, Any]]]:
    return {path.stem: load_jsonl(path) for path in sorted(out_dir.glob("*.jsonl"))}


def llm_labels(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """모델 점수를 더하지 않은 LLM 답. 답이 없는 건은 UNK."""
    return {row["record_id"]: vote(row["llm_votes"]) if row["llm_votes"] else UNK for row in rows}


def answered(config_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    """LLM 이 답한 설정. 전부 실패한 설정은 LLM 을 잰 것이 아니라 순위에 넣지 않는다."""
    return [name for name, rows in config_rows.items() if any(row["llm_votes"] for row in rows)]


def config_table(
    cases: Sequence[Case],
    config_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    runs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    table = []
    for name, rows in config_rows.items():
        run = runs.get(name, {})
        labels = {row["record_id"]: row["predicted_label"] for row in rows}
        table.append(
            {
                "name": f"{PREFIX}/{name}",
                "config": name,
                "prompt": run.get("prompt"),
                "model": run.get("model"),
                "samples": run.get("samples"),
                "unk_predicted": sum(label == UNK for label in labels.values()),
                "no_llm_vote": sum(bool(row["llm_failed"]) for row in rows),
                "elapsed_seconds": run.get("elapsed_seconds"),
                "calls": run.get("calls"),
                "failed_calls": run.get("failed_calls"),
                **score_labels(cases, labels),
                "llm_only": score_labels(cases, llm_labels(rows)),
            }
        )
    return table


def derived_labels(
    config_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, str]]:
    """API 를 더 부르지 않고 만드는 예측. LLM 단독과 세 설정의 LLM 답 다수결."""
    names = answered(config_rows)
    by_config = {name: llm_labels(config_rows[name]) for name in names}
    derived = {f"{PREFIX}/{name}:llm-only": labels for name, labels in by_config.items()}
    for trio in combinations(names, 3):
        derived[f"{PREFIX}/vote:{'+'.join(trio)}"] = {
            record_id: vote([by_config[name][record_id] for name in trio])
            for record_id in by_config[trio[0]]
        }
    return derived


def wilson(count: int, total: int = 100, z: float = 1.96) -> tuple[float, float]:
    rate = count / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    return centre - margin, centre + margin


def paired_with_ours(cases: Sequence[Case], labels: Mapping[str, str]) -> dict[str, Any]:
    """게이트 2 예측과 짝 비교 (정확 이항 검정, 양측). 어느 기준이든 한쪽만 맞힌 건만 센다."""
    result: dict[str, Any] = {}
    criteria = {"strict": (group_map(None), False), "lenient": (group_map(G_FINAL), True)}
    scored = apply_labels(cases, "x", labels)
    for name, (mapping, lenient) in criteria.items():
        new_only = ours_only = 0
        for case in scored:
            new = is_correct(case.predictions["x"], case, mapping, lenient=lenient)
            ours = is_correct(case.predictions["ours"], case, mapping, lenient=lenient)
            new_only += new and not ours
            ours_only += ours and not new
        total = new_only + ours_only
        tail = sum(math.comb(total, k) for k in range(min(new_only, ours_only) + 1))
        result[name] = {
            "new_only": new_only,
            "ours_only": ours_only,
            "p": min(1.0, 2 * tail / 2**total) if total else 1.0,
        }
    return result


def unk_confusion(cases: Sequence[Case], labels: Mapping[str, str]) -> dict[str, int]:
    counts = Counter(
        ("정답 UNK" if case.final == UNK else "정답 이유")
        + (" / 예측 UNK" if labels[case.record_id] == UNK else " / 예측 이유")
        for case in cases
    )
    return dict(sorted(counts.items()))


def _pct(count: int, total: int = 100) -> str:
    return f"{100 * count / total:.1f}%"


def _gain(count: int, base: int) -> str:
    return f"{100 * (count - base) / 100:+.1f}"


def pick_best(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """G-final+lenient 먼저, 같으면 8종 strict. 1단계 헤드라인과 같은 기준이 앞이다."""
    return max(rows, key=lambda row: (row["lenient"], row["strict"], str(row["name"])))


def _ci(count: int) -> str:
    low, high = wilson(count)
    return f"{100 * low:.1f}~{100 * high:.1f}%"


def _rank_rows(lines: list[str], rows: Sequence[Mapping[str, Any]]) -> None:
    lines += [
        "| 이름 | G-final + lenient | 1단계 대비 %p | 8종 strict | 60% 대비 %p | UNK 예측 수 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['name']}` | {_pct(row['lenient'])} | "
            f"{_gain(row['lenient'], STAGE1_LENIENT)} | {_pct(row['strict'])} | "
            f"{_gain(row['strict'], STAGE1_STRICT)} | {row['unk_predicted']} |"
        )


def format_report(result: Mapping[str, Any]) -> str:
    best = result["best"]
    floor = result["floor"]
    paired = result["best_paired"]
    configs = {row["config"]: row for row in result["configs"]}
    lines = [
        "# massage 2단계 — 섞기 규칙과 LLM·프롬프트 조정 (test 100건)",
        "",
        f"## 최고 설정: `{best['name']}`",
        "",
        "| 기준 | 최고 설정 | 95% 구간 | 1단계 | 대비 | 기준선 A | 기준선 B | 전부 UNK |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
        f"| G-final + lenient | **{_pct(best['lenient'])}** | {_ci(best['lenient'])} | "
        f"{_pct(STAGE1_LENIENT)} | {_gain(best['lenient'], STAGE1_LENIENT)}%p | "
        f"{_pct(floor['a']['lenient'])} | {_pct(floor['b']['lenient'])} | "
        f"{_pct(floor['unk']['lenient'])} |",
        f"| 8종 strict | **{_pct(best['strict'])}** | {_ci(best['strict'])} | "
        f"{_pct(STAGE1_STRICT)} | {_gain(best['strict'], STAGE1_STRICT)}%p | "
        f"{_pct(floor['a']['strict'])} | {_pct(floor['b']['strict'])} | "
        f"{_pct(floor['unk']['strict'])} |",
        "",
        "> **게이트 2 결과가 아니다.** 사전 등록 기준의 test 정확도는 **60.0%, 게이트 2 미달** "
        "그대로다. 아래는 같은 test 100건을 보면서 규칙·프롬프트·모델을 고른 값이고, "
        f"이름 {result['tried']}개 중 가장 높은 하나다. 새 데이터에서는 더 낮을 것으로 봐야 한다.",
        "",
        "게이트 2 예측과 짝 비교 (한쪽만 맞힌 건, 정확 이항 검정): "
        f"strict 최고 설정만 {paired['strict']['new_only']}건 : 게이트 2 만 "
        f"{paired['strict']['ours_only']}건 (p = {paired['strict']['p']:.3f}) / "
        f"lenient {paired['lenient']['new_only']}건 : {paired['lenient']['ours_only']}건 "
        f"(p = {paired['lenient']['p']:.3f}). 이 p 값은 여러 설정 중 고른 것을 보정하지 않았다.",
        "",
        f"이 설정을 내는 데 든 것: {result['best_cost']}.",
        "",
        "`python -m eval.massage_stage2 report` 로 재생성. 같은 숫자: `stage2.json`. "
        "예측: `stage2/`. 실행 기록: `stage2_runs.json`. LLM 응답: `datasets/llm_cache/massage/`.",
        "",
        "## 1. 읽는 법",
        "",
    ]
    lines += reading_notes(result, configs)
    lines += [
        "",
        "## 2. 0단계: API 없이 섞기",
        "",
        f"커밋된 예측만 쓴다. 규칙 {len(result['blends'])}개를 test 에서 모두 재 본 값이다.",
        "",
    ]
    _rank_rows(lines, result["blends"])
    lines += [
        "",
        "## 3. 1단계: API 설정별",
        "",
        "규칙 r1 + 모델 m2 + `WEIGHT_LLM` 은 그대로이고 LLM 부분만 바꿨다. "
        "`모델+LLM` 은 게이트 2 와 같은 합산이고, `LLM 단독` 은 모델 점수를 더하지 않은 답이다. "
        "걸린 시간·호출에는 이어 돌린 것과 실패한 호출이 들어 있다.",
        "",
        "| 설정 | 프롬프트 | 모델 | 질의 | 모델+LLM lenient | 대비 %p | 모델+LLM strict | "
        "대비 %p | LLM 단독 lenient | 대비 %p | LLM 단독 strict | 대비 %p | 표 없음 | "
        "걸린 시간 | 호출 (실패) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["configs"]:
        only = row["llm_only"]
        failed = not row["no_llm_vote"] < 100
        lines.append(
            f"| `{row['name']}` | {row['prompt']} | {row['model']} | {row['samples']} | "
            f"{_pct(row['lenient'])} | {_gain(row['lenient'], STAGE1_LENIENT)} | "
            f"{_pct(row['strict'])} | {_gain(row['strict'], STAGE1_STRICT)} | "
            + (
                "- | - | - | - | "
                if failed
                else f"{_pct(only['lenient'])} | {_gain(only['lenient'], STAGE1_LENIENT)} | "
                f"{_pct(only['strict'])} | {_gain(only['strict'], STAGE1_STRICT)} | "
            )
            + f"{row['no_llm_vote']} | {format_seconds(row['elapsed_seconds'])} | "
            f"{row['calls']} ({row['failed_calls']}) |"
        )
    lines += [
        "",
        "기준선과 바닥 (게이트 2 예측 그대로):",
        "",
        "| 방식 | G-final + lenient | 8종 strict |",
        "|---|---:|---:|",
        f"| 기준선 A | {_pct(floor['a']['lenient'])} | {_pct(floor['a']['strict'])} |",
        f"| 기준선 B | {_pct(floor['b']['lenient'])} | {_pct(floor['b']['strict'])} |",
        f"| 전부 UNK | {_pct(floor['unk']['lenient'])} | {_pct(floor['unk']['strict'])} |",
        f"| 게이트 2 예측 (1단계) | {_pct(STAGE1_LENIENT)} | {_pct(STAGE1_STRICT)} |",
        "",
        "## 4. API 추가 호출 없이 설정 섞기",
        "",
        "답이 있는 설정의 LLM 단독과, 세 설정 LLM 답 다수결(셋 다 갈리면 UNK) 전부다.",
        "",
    ]
    _rank_rows(lines, result["derived"])
    lines += [
        "",
        "## 5. 프롬프트·모델 고른 기록",
        "",
    ]
    lines += SETUP_NOTES
    return "\n".join(lines) + "\n"


SETUP_NOTES = [
    "- **유료 API 는 부르지 않았다.** 호출기는 NVIDIA URL 과 허용 목록의 세 모델만 부르고, "
    "다른 공급자 키는 프로세스에서 지운다 (`eval/massage_stage2.py`, 테스트 있음)",
    "- **c4** = c3 + 라벨링 가이드 §5·§6.2.1·§6.3.1 의 UNK 판단 기준. "
    "**c5** = c4 + train 예시 10건",
    "- few-shot 은 train 에서 라벨러 둘이 일치한 건만, val·test 와 같은 커밋을 빼고, "
    "칸(라벨, 테스트 여부)마다 짧은 순으로 기계적으로 골랐다. "
    "UNK 4 · DESIGN 2 · DEAD 2 · FEAT 1 · LIB 1",
    f"- 모델: `{NEMOTRON}`(550B) 와 `{MISTRAL}`(1T). 2026-11-03 목록(85개)과 모델 쪽 "
    '"Free Endpoint" 표시, 짧은 호출로 확인했다. '
    "답이 오지 않는 후보 `moonshotai/kimi-k2.6`(404) 은 뺐다",
    "- 설정 순서는 s1·s2 를 본 뒤 c4 를 골랐고, s3 을 본 뒤 s5·s6 을 nemotron 으로 정했다. "
    "즉 test 결과가 다음 설정을 정했다",
    "- 큰 모델은 생각 과정을 끄지 못해 토큰 상한을 4096 으로 올렸다 (flash 는 256 그대로)",
    "- s6 은 온도 0.7 로 3번 물었다. 온도 0 이면 같은 답이 나와 다수결이 뜻이 없다. "
    "셋 다 갈리면 UNK 로 본다. 무료 한도(429)로 세 번 이어 돌려 100건 모두 3표를 채웠다",
    "- 호출 실패는 다음 차례에 다시 묻고, 끝내 답이 없으면 게이트 2 와 같이 LLM 표 없이 낸다. "
    "시간 초과는 300초 그대로다",
    "- 프롬프트·설정은 이 모듈에만 있다. `classify/` 와 기존 보고서·dataset-v0.9 는 바꾸지 않았다",
]


def reading_notes(result: Mapping[str, Any], configs: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """숫자가 말하는 것. 값은 결과에서 읽어 재생성해도 어긋나지 않게 한다."""

    def both(row: Mapping[str, Any]) -> str:
        return f"lenient {_pct(row['lenient'])} · strict {_pct(row['strict'])}"

    notes = [
        f"- **가장 큰 변화는 모델 교체다.** 같은 c4 에서 flash {both(configs['s1']['llm_only'])} → "
        f"nemotron {both(configs['s3']['llm_only'])} (둘 다 LLM 단독)",
        f"- **큰 모델에서는 scikit-learn 모델을 더하면 오히려 내려간다.** s3 모델+LLM "
        f"{both(configs['s3'])}. `WEIGHT_LLM` 0.5 는 flash 답에 맞춰 고른 값이다. "
        "LLM 단독 설정은 `r1+m2+c3` 와 다른 구성이라는 뜻이기도 하다",
        f"- **few-shot 은 올리지 못했다.** flash s2 {both(configs['s2'])}, nemotron s5 "
        f"{both(configs['s5'])}. 둘 다 같은 모델의 c4 보다 낮다",
        f"- **UNK 판단 기준(c4)만으로는 거의 안 움직였다.** s1 {both(configs['s1'])} "
        f"(게이트 2 lenient {_pct(STAGE1_LENIENT)} · strict {_pct(STAGE1_STRICT)})",
        f"- **API 없는 섞기는 lenient 를 못 올렸다.** 최고 `{result['blends'][0]['name']}` "
        f"{both(result['blends'][0])}. 기준선 B 를 믿고 UNK 를 뒤집으면 크게 떨어진다",
        f"- **`{configs['s4']['name']}` 은 결과가 아니다.** {MISTRAL} 호출 "
        f"{configs['s4']['calls']}건이 모두 300초 시간 초과라 30분 규칙으로 멈췄다. 표의 "
        f"{both(configs['s4'])} 는 LLM 없이 모델만 낸 값이고 순위에 넣지 않았다",
        "- **차이가 작다.** 100건이라 구간이 ±8%p 안팎이고 lenient 바닥(전부 UNK)도 "
        f"{_pct(result['floor']['unk']['lenient'])} 다. 온도 0.7 답이 섞인 s6·다수결의 "
        "1~3%p 차이는 우연과 구별되지 않는다",
        "- 최고 설정의 UNK 혼동: "
        + ", ".join(f"{key} {count}건" for key, count in result["best_confusion"].items()),
    ]
    return notes


def cost_of(name: str, configs: Sequence[Mapping[str, Any]]) -> str:
    """이름에 든 설정의 실행 합계. 섞기 규칙은 API 를 부르지 않는다."""
    used = [
        row
        for row in configs
        if row["config"] in re.split(r"[/+:]", name.removeprefix(f"{PREFIX}/"))
    ]
    if not used:
        return "API 호출 없음"
    seconds = sum(row["elapsed_seconds"] or 0 for row in used)
    calls = sum(row["calls"] or 0 for row in used)
    names = ", ".join(row["config"] for row in used)
    return f"{names} 실행 합계 {format_seconds(seconds)}, 호출 {calls}회 (NVIDIA 무료 API)"


def build_result() -> dict[str, Any]:
    cases = load_cases(DEFAULT_LABELS, DEFAULT_PREDICTIONS)
    floor = {
        "a": score_labels(cases, {case.record_id: case.predictions["a"] for case in cases}),
        "b": score_labels(cases, {case.record_id: case.predictions["b"] for case in cases}),
        "unk": score_labels(cases, {case.record_id: UNK for case in cases}),
    }
    rules = blend_rules()
    inputs = blend_inputs(cases)
    blends = blend_table(cases, inputs, rules)
    runs = json.loads(RUNS_PATH.read_text(encoding="utf-8")) if RUNS_PATH.is_file() else {}
    config_rows = load_config_rows()
    configs = config_table(cases, config_rows, runs)
    labels_by_name = {
        f"{PREFIX}/blend:{name}": {key: rule(row) for key, row in inputs.items()}
        for name, rule in rules.items()
    }
    labels_by_name |= {
        f"{PREFIX}/{name}": {row["record_id"]: row["predicted_label"] for row in config_rows[name]}
        for name in answered(config_rows)
    }
    derived_by_name = derived_labels(config_rows)
    labels_by_name |= derived_by_name
    derived = sorted(
        (
            {
                "name": name,
                "unk_predicted": sum(label == UNK for label in labels.values()),
                **score_labels(cases, labels),
            }
            for name, labels in derived_by_name.items()
        ),
        key=lambda row: (-row["lenient"], -row["strict"], row["name"]),
    )
    ranked = [*blends, *(row for row in configs if row["no_llm_vote"] < 100), *derived]
    best = pick_best(ranked)
    best_labels = labels_by_name[best["name"]]
    return {
        "prefix": PREFIX,
        "not_gate2": "게이트 2 판정은 60.0% 미달 그대로다",
        "stage1": {"strict": STAGE1_STRICT, "lenient": STAGE1_LENIENT},
        "floor": floor,
        "blends": blends,
        "configs": configs,
        "derived": derived,
        "tried": len(ranked),
        "best": best,
        "best_cost": cost_of(best["name"], configs),
        "best_paired": paired_with_ours(cases, best_labels),
        "best_confusion": unk_confusion(cases, best_labels),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.massage_stage2")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", choices=sorted(CONFIGS), required=True)
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    commands.add_parser("check-c3")
    commands.add_parser("shots")
    commands.add_parser("report")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    if args.command == "run":
        run_config(CONFIGS[args.config], args.env_file)
    elif args.command == "check-c3":
        return 1 if check_c3() else 0
    elif args.command == "shots":
        print(few_shot_block(load_inputs().shots))
    else:
        result = build_result()
        (OUT_DIR / "stage2.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        (OUT_DIR / "stage2.md").write_text(format_report(result), encoding="utf-8", newline="\n")
        print(f"최고: {result['best']['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
