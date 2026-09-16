"""기준선 B: LLM 직접 질의 (§10.2). 담당: 희수 (hs)

무엇을:
    diff 와 커밋 메시지를 LLM 에 주고 §4.2 ③ 8종 중 하나를 고르게 한다. 기준선 A 보다 강한
    상대이고, 게이트 2(§9 6주차)에서 우리 방식이 이겨야 하는 쪽이다.

이 출력은 정답이 아니다 (ADR-005):
    LLM 출력을 라벨 자리에 쓰면 §10.2 평가가 자기 참조로 무너진다. 우리 방식과 기준선 B 를
    같은 LLM 답으로 채점하는 꼴이 되기 때문이다. 그래서 출력 필드는 `predicted_label` 이고
    (사람 라벨은 `reason_label`), 이 파일은 `datasets/labels/` 아래에 아무것도 쓰지 않는다.

캐시하는 이유:
    같은 레코드를 다시 질의하면 돈이 들고, 모델이 매번 다른 답을 줄 수 있어 재현이 안 된다.
    프롬프트·모델·입력이 같으면 캐시를 쓴다. 캐시 키에 프롬프트 버전이 들어가므로 프롬프트를
    고치면 자동으로 다시 묻는다.

실행:
    python -m classify.baseline_llm --input records.jsonl --out predictions.jsonl
    python -m classify.baseline_llm --input records.jsonl --dry-run   # 프롬프트만 확인

    ANTHROPIC_API_KEY 는 .env 에서만 읽는다 (§8.4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classify.baselines import (
    METHOD_LLM,
    UNKNOWN_LABEL,
    Prediction,
    commit_message_of,
    record_id_of,
    summarize,
    write_predictions,
)
from classify.labels import REASON_LABELS
from pipeline.select_repos import load_env_file, resolve_cache_dir

# 프롬프트를 고치면 올린다. 캐시 키와 예측 파일에 함께 들어가므로 어느 프롬프트로 낸 답인지 남는다.
PROMPT_VERSION = "b1"
DEFAULT_MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_DIFF_CHARS = 4000
MAX_MESSAGE_CHARS = 2000

# §4.2 ③ 표를 그대로 옮긴다. 기준선이 우리와 **같은 분류 체계**로 답해야 비교가 성립한다.
LABEL_DEFINITIONS = (
    ("BUG", "잘못된 동작을 고치기 위해 제거"),
    ("PERF", "느리거나 자원을 많이 써서 교체"),
    ("SEC", "취약점·위험한 패턴 제거"),
    ("LIB", "직접 구현을 외부/표준 라이브러리로 대체"),
    ("DEAD", "호출되지 않아 제거"),
    ("DESIGN", "구조·추상화 변경으로 제거"),
    ("FEAT", "기능 자체를 없앰 (폐기, 지원 종료)"),
    ("UNK", "위 어느 것으로도 판단 불가"),
)

SYSTEM_PROMPT = (
    "당신은 오픈소스 커밋에서 함수가 삭제된 이유를 분류한다. "
    "주어진 정보만으로 판단하고, 없는 맥락을 지어내지 않는다. "
    "판단할 근거가 부족하면 UNK 를 고른다 - 억지로 고르는 것보다 낫다."
)

# 답을 한 줄로 받는다. 자유 서술을 파싱하면 실패가 늘고, 그 실패가 UNK 로 섞여 기준선을
# 실제보다 약하게 만든다.
ANSWER_RE = re.compile(r"^\s*([A-Z]+)\s*\|\s*(.*)$", re.MULTILINE)


def build_prompt(deleted_body: str, commit_message: str) -> str:
    """LLM 에 줄 본문. diff 와 커밋 메시지만 넣는다 - 기준선 B 의 정의가 "직접 질의" 다."""
    definitions = "\n".join(f"- {code}: {meaning}" for code, meaning in LABEL_DEFINITIONS)
    message = (commit_message or "(커밋 메시지 없음)")[:MAX_MESSAGE_CHARS]
    body = (deleted_body or "(삭제 코드 없음)")[:MAX_DIFF_CHARS]
    return (
        "아래 함수가 왜 삭제됐는지 한 가지로 분류하라.\n\n"
        f"분류 체계:\n{definitions}\n\n"
        f"커밋 메시지:\n{message}\n\n"
        f"삭제된 코드:\n```\n{body}\n```\n\n"
        "답은 정확히 한 줄로, `라벨|근거` 형식으로만 쓴다. "
        "라벨은 위 8개 중 하나이고, 근거는 한 문장이다.\n"
        "예: BUG|커밋 메시지에 빈 헤더에서 IndexError 가 났다고 적혀 있다"
    )


def parse_answer(text: str) -> tuple[str, str, str]:
    """LLM 응답 → (라벨, 근거, 실패 사유).

    8종 밖이거나 형식이 깨지면 UNK 로 두되 **사유를 남긴다.** 조용히 UNK 로 만들면 기준선이
    실제보다 약해 보이고, 우리 방식과의 차이가 실력이 아니라 파싱 실패 때문이 된다.
    """
    if not text or not text.strip():
        return UNKNOWN_LABEL, "", "빈 응답"

    match = ANSWER_RE.search(text)
    if match is None:
        snippet = text.strip().replace("\n", " ")[:80]
        return UNKNOWN_LABEL, "", f"형식 불일치: {snippet!r}"

    label, reason = match.group(1).strip().upper(), match.group(2).strip()
    if label not in REASON_LABELS:
        return UNKNOWN_LABEL, reason, f"8종 밖 라벨: {label!r}"
    return label, reason, ""


Caller = Callable[[str, str, str], str]
"""(system, prompt, model) -> 응답 텍스트. 테스트·다른 공급자를 위해 주입 가능하게 둔다."""


def anthropic_caller(api_key: str, *, timeout: int = 60) -> Caller:
    """Anthropic Messages API 호출기.

    SDK 를 의존성에 넣지 않고 urllib 으로 직접 부른다. 팀 전원이 패키지를 하나 더 설치해야
    하는 비용보다 POST 한 번을 직접 쓰는 편이 싸다 (select_repos 가 GitHub API 를 다루는
    방식과 같다).
    """

    def call(system: str, prompt: str, model: str) -> str:
        payload = json.dumps(
            {
                "model": model,
                "max_tokens": 256,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        blocks = body.get("content") or []
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")

    return call


@dataclass
class LlmBaseline:
    """기준선 B 실행기. 호출기를 주입받아 네트워크 없이도 테스트할 수 있다."""

    caller: Caller
    model: str = DEFAULT_MODEL
    cache_dir: Path | None = None
    prompt_version: str = PROMPT_VERSION
    calls: int = 0
    cache_hits: int = 0
    failures: dict[str, int] = field(default_factory=dict)

    def _cache_path(self, prompt: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = f"{self.prompt_version}|{self.model}|{prompt}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> str | None:
        """캐시된 응답 텍스트. 없거나 쓸 수 없는 형태면 None (다시 묻는다).

        유효한 JSON 이어도 `text` 가 문자열이 아닐 수 있다 (손상·형식 변경). 그대로 넘기면
        `parse_answer` 의 문자열 연산에서 AttributeError 가 나고, 그건 `predict` 의 except 에
        걸리지 않아 배치 전체가 죽는다. 형태까지 확인하고서야 캐시로 인정한다.
        """
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        text = payload.get("text") if isinstance(payload, dict) else None
        return text if isinstance(text, str) else None

    def _write_cache(self, path: Path, text: str) -> None:
        """응답을 캐시에 남긴다. 실패해도 응답 자체는 버리지 않는다.

        이미 API 를 불러 받은 답이다. 디스크 문제로 그것을 UNK 로 만들면 돈을 쓰고도 결과를
        잃고, 실패 통계에도 "호출 실패" 로 잘못 기록돼 모델이 불안정한 것처럼 보인다.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"model": self.model, "text": text}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as error:
            self.failures["캐시 쓰기"] = self.failures.get("캐시 쓰기", 0) + 1
            print(f"캐시를 남기지 못했다 (응답은 그대로 쓴다): {error}", file=sys.stderr)

    def _ask(self, prompt: str) -> str:
        path = self._cache_path(prompt)
        if path is not None:
            cached = self._read_cache(path)
            if cached is not None:
                self.cache_hits += 1
                return cached

        self.calls += 1
        text = self.caller(SYSTEM_PROMPT, prompt, self.model)
        if path is not None:
            self._write_cache(path, text)
        return text

    def predict(self, record: dict[str, Any]) -> Prediction:
        """레코드 하나를 분류한다. 호출이 실패해도 예측을 돌려준다 (사유를 남기고 UNK)."""
        prompt = build_prompt(record.get("deleted_body", ""), commit_message_of(record))
        version = f"{self.prompt_version}/{self.model}"

        try:
            text = self._ask(prompt)
        except (urllib.error.URLError, OSError, ValueError) as error:
            self.failures["호출 실패"] = self.failures.get("호출 실패", 0) + 1
            return Prediction(
                record_id=record_id_of(record),
                predicted_label=UNKNOWN_LABEL,
                method=METHOD_LLM,
                version=version,
                note=f"호출 실패: {type(error).__name__}: {error}",
            )

        label, reason, problem = parse_answer(text)
        if problem:
            self.failures[problem.split(":")[0]] = self.failures.get(problem.split(":")[0], 0) + 1
        return Prediction(
            record_id=record_id_of(record),
            predicted_label=label,
            method=METHOD_LLM,
            version=version,
            evidence=(reason,) if reason else (),
            note=problem,
        )

    def predict_all(self, records: Sequence[dict[str, Any]]) -> list[Prediction]:
        return [self.predict(record) for record in records]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m classify.baseline_llm",
        description="기준선 B - LLM 에 diff + 커밋 메시지를 주고 이유 8종 예측 (#44).",
    )
    parser.add_argument("--input", type=Path, required=True, help="레코드 JSONL")
    parser.add_argument("--out", type=Path, default=None, help="예측 JSONL 저장 경로")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N건만 (비용 확인용)")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--dry-run", action="store_true", help="API 를 부르지 않고 첫 프롬프트만 출력"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    load_env_file(args.env_file)

    records = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # 음수를 그대로 슬라이스하면 records[:-1] 이 되어 "앞 N건만" 의 정반대가 된다.
    # 마지막 한 건만 빼고 전부 유료 호출하는 셈이라, 비용을 아끼려는 옵션이 비용을 쓴다.
    if args.limit < 0:
        print("--limit 는 0 이상이어야 한다 (0 이면 전체).", file=sys.stderr)
        return 2
    if args.limit:
        records = records[: args.limit]
    if not records:
        print("레코드가 없다.", file=sys.stderr)
        return 1

    if args.dry_run:
        first = records[0]
        print(build_prompt(first.get("deleted_body", ""), commit_message_of(first)))
        print(f"\n(dry-run: {len(records)}건 대상, API 를 부르지 않았다)", file=sys.stderr)
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY 가 없다. .env 에 채워라 (§8.4).", file=sys.stderr)
        return 2

    cache_dir = args.cache_dir or resolve_cache_dir(None) / "llm_baseline"
    baseline = LlmBaseline(anthropic_caller(api_key), model=args.model, cache_dir=cache_dir)
    predictions = baseline.predict_all(records)

    if args.out:
        written = write_predictions(args.out, predictions)
        print(f"예측: {args.out} ({written}건)", file=sys.stderr)

    print(f"기준선 B (프롬프트 {baseline.prompt_version} / 모델 {baseline.model})")
    for line in summarize(predictions).format_lines():
        print(line)
    print(f"  API {baseline.calls}회 / 캐시 {baseline.cache_hits}회")
    if baseline.failures:
        detail = ", ".join(f"{reason} {count}건" for reason, count in baseline.failures.items())
        print(f"  실패: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
