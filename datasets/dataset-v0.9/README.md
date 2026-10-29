# Deleted Code Search dataset-v0.9

오픈소스 Python 저장소 100개에서 **지워진 함수**를 모으고, 왜 지워졌는지(이유 8종 + 근거 등급)와 대신 무엇이 들어왔는지를 함께 붙인 데이터셋이다. 만든 코드는 `pipeline/export.py` (#83), 스키마는 CHARTER §4.4 `DeletionRecord` 다.

- 데이터셋 라이선스 **CC BY 4.0**. 행에 들어 있는 코드 스니펫은 **원 저장소 라이선스**(`repo_license`)를 따르고, 행마다 출처 커밋 링크(`source_url`)가 있다 (ADR-011)
- 필터 규칙 **`v0.9`** (= v0.7 추출 + `NOISE_GENERATED`·`NOISE_FORMAT` 사후 필터, `docs/filter_rules.md`)
- 내보낸 행은 `filter_status == KEPT` 뿐이다. NOISE_* 는 건수만 `manifest.json` 에 있다
- 배포는 GitHub Release `dataset-v0.9` 다 (§7). PostgreSQL 적재는 이번 범위가 아니다 (§8)

## 1. 무엇이 들어 있나

| | 값 |
|---|---|
| 저장소 | **100개** (`repos.csv`) — 학습 저장소 20개 + 신규 80개 (ADR-020, `docs/repo_final100.md`) |
| 행 | **1,354,052** (FULL_FUNCTION 723,087 · PARTIAL 5줄 이상 630,965) |
| 사람 확정 라벨 | **500건** (`main500.jsonl`, split train 300 / val 100 / test 100) |
| 라이선스 | Apache-2.0 51 · MIT 39 · BSD-3-Clause 9 · BSD-2-Clause 1 — 루트 라이선스는 100개 모두 permissive. 코드 포함 **1,319,661행** (`CODE_INCLUDED`), 라이선스 때문에 링크만 **34,391행** (`LINK_ONLY`, 확정 500건 중 19건 포함, §4) |

이유 출처별 행 수 (`classification.source`):

| source | 행 | `reason` | 뜻 |
|---|---:|---|---|
| `HUMAN` | 500 | 있음 (`classifier_version = "human"`) | 확정 500건. 2인 라벨 + 불일치는 제3자 판정 (ADR-019, 가이드 v3) |
| `MODEL_LLM` | 2,891 | 있음 (`r1+m2+c3+llm:deepseek-ai/deepseek-v4.1-flash`) | 저장소마다 30건 표본에만 돌린 LLM 포함 분류기 (test 60.0%) |
| `MODEL_ONLY` | 249,247 | 있음 (`r1+m2`) | 학습 저장소 20개의 모델만 분류기 (val 52.0%) |
| `UNDISTINGUISHED` | 470,449 | **null — 이유 미구분** | 신규 80개의 모델만 분류 결과. 구체적인 이유를 믿지 않는다 (아래 §3) |
| `NOT_CLASSIFIED` | 630,965 | null | 분류 대상(FULL_FUNCTION)이 아닌 PARTIAL |

### reason 이 null 인 행 — 라벨 누락이 아니다

| | 행 | 삭제 이유(`reason`) |
|---|---:|---|
| 전체 KEPT | **1,354,052** | |
| `FULL_FUNCTION` (함수 전체 삭제) | **723,087** | **분류 대상.** 사람 라벨·분류기 출력이 붙는다 (신규 80개 모델만 결과는 이유 미구분이라 null) |
| `PARTIAL` (함수 일부 5줄 이상 삭제) | **630,965** | **분류 대상이 아니다.** 늘 null |

- 삭제 이유 분류 대상은 **FULL_FUNCTION 뿐이다** (ADR-003, 라벨링 대상과 같은 범위). PARTIAL 은 데이터셋에는 넣지만(ADR-015) 분류하지 않으므로 `reason = null` 이 **정상**이다
- 그래서 전체 행 중 reason 이 없는 PARTIAL 을 **"라벨 누락" 으로 해석하면 안 된다.** `classification.source = NOT_CLASSIFIED` 630,965건이 정확히 이 PARTIAL 이다 — 분류 대상인 FULL_FUNCTION 723,087건은 모두 분류 결과가 붙었다 (누락 0)
- 이유 분포·회수율을 셀 때 분모는 PARTIAL 을 뺀 FULL_FUNCTION 으로 잡는다

## 2. 사람 라벨과 모델 결과를 가르는 법

- **사람이 확정한 값은 `classification.source == "HUMAN"` 인 500건뿐이다.** 그 행의 `reason` 은 `datasets/labeled_500.jsonl` 의 `final` 그대로이고 `classification.split`·`classification.human_method`(`AGREED` 350 / `THIRD_PARTY` 150)가 붙는다. 필터 `v0.9` 때문에 라벨·split 을 바꾸지 않았다 — 500건 모두 v0.9 에서도 KEPT 다
- 나머지 `reason` 은 전부 분류기 출력이다. 버전은 `reason.classifier_version` 에 있다. **분류기 라벨을 정답으로 쓰지 않는다** (ADR-005)
- 정확도는 버전마다 그 숫자만 말한다. 모델만 `r1+m2` val **52.0%**, LLM 포함 test **60.0%** — 둘 다 학습과 같은 20개 저장소 안에서 잰 값이다 (`docs/reports/apply100.md`, `docs/meetings/gate-2.md`)
- 이유 분포를 보고할 때는 확정 500건 + LLM 표본만 쓴다. 모델만 라벨 분포는 이유 분포로 쓰지 않는다 (#180 판정)

## 3. 학습 저장소 20개와 "이유 미구분"

`classification.training_repo` 는 그 행의 저장소가 확정 500건을 뽑은 20개(= 분류기 학습·검증·평가 저장소)인지다. §4.4 `DeletionRecord` 에는 넣지 않고 분류 출력 쪽 속성으로만 둔다. 목록은 `training_repos.txt` 에도 있다.

```
apache/superset
browser-use/browser-use
celery/celery
commaai/openpilot
crewAIInc/crewAI
django/django
docling-project/docling
encode/httpx
home-assistant/core
huggingface/transformers
keras-team/keras
langchain-ai/langchain
mem0ai/mem0
pandas-dev/pandas
psf/requests
pydantic/pydantic
scikit-learn/scikit-learn
unclecode/crawl4ai
unslothai/unsloth
vllm-project/vllm
```

- 신규 80개에서 모델만 분류기는 61만여 건 중 UNK·DESIGN 이 아닌 라벨을 1건만 냈고, LLM 포함 표본이 다른 이유를 낸 606건 중 모델만이 같은 라벨을 낸 것은 0건이다. 숫자를 보기 전에 정한 기준대로 **신규 80개 모델만 라벨은 `reason = null`, `source = UNDISTINGUISHED`** 로 낸다. 어떤 분류기를 돌렸는지는 `classification.classifier_version` 에 남긴다
- 학습 저장소 20개의 모델만 라벨은 그대로 둔다. 다만 그 저장소에서도 LLM 포함이 UNK·DESIGN 이 아닌 이유를 낸 124건 중 모델만이 같은 라벨을 낸 것은 21건(16.9%)이다 — 구체적인 이유는 믿을 만하지 않다
- LLM 표본(저장소마다 30건, 100개 3,000건)은 모델만 라벨이 아니라 `MODEL_LLM` 으로 이유를 낸다. 3,000건 중 109건은 v0.9 에서 `NOISE_GENERATED` 가 되어 빠졌고 2,891건이 남았다 (dspy 등 가상환경 삭제 커밋)

## 4. 라이선스와 코드 포함 (ADR-007·011, CHARTER §4.2 ④)

`code_policy` 는 두 곳에 있고 기준이 다르다. 섞어 읽지 않는다.

- **행 단위 — 레코드의 `code_policy`** (데이터셋 본체). 행마다 정한다
  - `CODE_INCLUDED` — 저장소 루트 라이선스가 permissive(MIT·Apache-2.0·BSD 계열, `pipeline.select_repos.PERMISSIVE_SPDX`)이고, 그 행이 `docs/license_overrides.csv` 의 어떤 규칙에도 걸리지 않는다. 코드 본문을 넣는다
  - `LINK_ONLY` — 저장소 루트 라이선스가 permissive 가 아니거나, 그 행이 `docs/license_overrides.csv` 규칙(하위 경로 또는 `author_date` 기준)에 걸린다. `deleted_body`·`function_signature`·`replacement.code`·`reason.evidence_text` 를 null 로 두고 `source_url`·경로·함수 이름·맥락 같은 메타데이터만 남긴다
  - **dataset-v0.9 의 행 단위 값: `CODE_INCLUDED` 1,319,661행 / `LINK_ONLY` 34,391행** (합계 1,354,052행). `LINK_ONLY` 는 전부 `docs/license_overrides.csv` 규칙으로 적용된 행이다 (아래)
- **저장소 단위 — `repos.csv` 의 `code_policy` 열.** 저장소 루트 라이선스만 보고 정한 값이다. 루트 라이선스는 100개 모두 permissive 라 이 열은 100개 모두 `CODE_INCLUDED` 다. **이 값은 그 저장소의 모든 행에 코드가 들어 있다는 뜻이 아니다** — 행마다의 값은 레코드의 `code_policy` 를 본다
- 라이선스 값은 `docs/repo_final100.csv` 의 `license` 열이다. 2026-10-29 에 100개 전부의 현재 루트 라이선스 원문, 현재 기본 브랜치 트리에 있는 하위 라이선스 파일, 현재 루트 라이선스 파일 경로의 변경 이력(과 그 경로의 가장 오래된 판)을 다시 봤다 (`docs/repo_final100.md` §3.1). 과거에 다른 이름·경로였다가 바뀌었거나 지워진 라이선스 파일의 이력, 지금은 지워진 하위 디렉터리의 라이선스 파일, 소스 파일마다의 라이선스 헤더는 보지 않았다. 현재 루트 라이선스 원문은 100개 모두 CSV 값과 같고 permissive 이며, 루트 이중 라이선스는 없다. 다만 행 단위로는 아래 34,391행이 `LINK_ONLY` 다

### 라이선스 때문에 링크만 내는 행 — 34,391행

- **총 34,391행은 `LINK_ONLY` 로 낸다.** 저장소는 permissive 지만 그 행의 코드가 다른 라이선스 아래였거나 그 시기 라이선스를 정할 수 없는 경우다 — 근거는 `docs/repo_final100.md` §3.1, 규칙은 `docs/license_overrides.csv`

  | 저장소 | 범위 | 그때 라이선스 | 행 |
  |---|---|---|---:|
  | `unslothai/unsloth` | `studio/`, `unsloth/kernels/moe/` | AGPL-3.0 | 12,895 |
  | `exo-explore/exo` | `author_date` 2025-12-18 전 | GPL-3.0 (2024-07-16 전은 없음) | 9,759 |
  | `NVIDIA-NeMo/Speech` | 2020-09-25 전 | 불확실 ("per-package licenses") | 5,561 |
  | `agno-agi/agno` | 2025-02-10 전 | MPL-2.0 | 2,830 |
  | `PrefectHQ/prefect` | 2022-03-08 전 | Prefect Community License 1.0 / 없음 | 2,496 |
  | `hpcaitech/ColossalAI` | `examples/images/diffusion/` | CreativeML Open RAIL-M | 353 |
  | `blakeblackshear/frigate` | 2020-07-26 전 | AGPL-3.0 | 256 |
  | `zulip/zulip` | 2015-09-25 전 | Zulip Enterprise License (독점) | 217 |
  | `run-llama/llama_index` | 일부 통합 패키지 | GPL·AGPL·빈 LICENSE | 24 |

- **그 안에 확정 500건 중 19건이 있다** (모두 unsloth `studio/`). **이 19건도 라벨·split 은 그대로이고 코드 본문만 지웠다.** `main500.jsonl`·`main500-v0.9.*` 에도 같은 정책이 적용돼 있다
- **이 행들은 통계와 평가 집합에서 빠지지 않는다.** 행 수(1,354,052)·이유 출처별 건수·확정 500건 집합과 그 평가 숫자는 그대로다
- 비우는 칸: `deleted_body`, `function_signature`, `replacement.code`, `reason.evidence_text` (근거 문장은 코드를 인용할 수 있어서). 남기는 칸: `id`, `repo`, `repo_license`(저장소 루트 라이선스), `commit_sha`, `file_path`, `start_line`·`end_line`, `function_name`, `source_url`, `context`, `replacement.match_method`·`confidence`, `reason.label`·`evidence_grade`·`confidence`·`classifier_version`, `classification` 전체
- `repos.csv` 의 `code_policy` 열(저장소 루트 기준, 100개 모두 `CODE_INCLUDED`)과 레코드의 `code_policy`(행 기준) 차이는 위 §4 첫머리를 본다
- PSF(`psf/black` `src/blib2to3/`, celery `celery/utils/dispatch/`, home-assistant `homeassistant/backports/`)·ISC(requests 2012 이전) 행은 permissive 라 코드를 넣었다. 허용 목록(MIT/Apache/BSD) 밖이라는 점은 #26 에서 다룬다. celery `docs/`(CC BY-SA 4.0, 32행)는 #28 결정대로 포함했다

## 5. 파일

GitHub Release `dataset-v0.9` 자산 (디렉터리 없이 평평하다):

| 파일 | 내용 |
|---|---|
| `<owner>__<name>-<k>-of-<n>.parquet` | 저장소 하나의 k번째 shard (zstd) |
| `<owner>__<name>-<k>-of-<n>.jsonl.gz` | 같은 행, 같은 순서의 gzip JSONL |
| `main500-v0.9.parquet`, `main500-v0.9.jsonl.gz` | 확정 500건만 (저장소 파일에도 같은 행이 있다) |
| `repos.csv` | 저장소별 라이선스·`code_policy`(저장소 루트 기준)·`training_repo`·출처별 건수·shard 수 |
| `training_repos.txt` | 학습 저장소 20개 |
| `manifest.json` | 파일별 행 수·바이트·sha256, 입력, 검증 결과 |
| `SHA256SUMS` | `sha256sum -c SHA256SUMS` 로 확인 |
| `README.md` | 이 문서 |

Git 저장소 안(`datasets/dataset-v0.9/`)에는 작은 파일만 있다: `main500.jsonl`(확정 500건, 같은 형식), `repos.csv`, `training_repos.txt`, `summary.json`(manifest 에서 실행마다 바뀌는 값을 뺀 것), 이 README.

**shard 규칙.** 저장소마다 행을 입력 순서대로 이어 붙이다가 JSON 한 줄(UTF-8, 줄바꿈 포함)의 누적 크기가 1.5×10⁹ 바이트를 넘게 되면 다음 shard 로 넘어간다. 압축 전 크기로 나누므로 같은 입력이면 같은 경계·같은 파일 이름이 나오고, Parquet 과 jsonl.gz 의 k번째 shard 는 같은 행을 담는다. 쓰고 난 파일은 모두 2×10⁹ 바이트(GitHub Release 한도 2 GiB 아래) 이하인지 확인했다. 행이 0건인 저장소도 빈 shard 1개를 쓴다. gzip 헤더에 파일 이름·시각을 넣지 않아 같은 내용이면 같은 바이트다.

## 6. 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | string | 레코드 id (uuid5). 라벨·분류 결과의 `record_id` |
| `repo` | string | `owner/name` |
| `repo_license` | string | 원 저장소 라이선스 (SPDX) |
| `commit_sha`, `parent_sha` | string | 삭제 커밋과 부모 |
| `author_date` | string | 커밋 작성 시각, ISO 8601 (시간대 포함, 원문 그대로) |
| `file_path` | string | 저장소 루트 기준 경로 (부모 커밋 쪽) |
| `start_line`, `end_line` | int64 | 부모 커밋 파일에서 지워진 범위 (§4.4 밖, 추출 필드) |
| `language` | string | 늘 `python` |
| `function_name`, `function_signature` | string | 지워진 함수. `LINK_ONLY` 면 시그니처 null |
| `deleted_body` | string | 지워진 코드 원문. `LINK_ONLY` 면 null |
| `deletion_kind` | string | `FULL_FUNCTION` · `PARTIAL`(5줄 이상) |
| `is_test_code` | bool | 테스트 코드 삭제인가 (제외하지 않고 표시만) |
| `filter_status`, `filter_rule_version` | string | 늘 `KEPT`, `v0.9` |
| `context.commit_message` | string | 커밋 메시지 (늘 있다) |
| `context.pr_number`·`pr_title`·`pr_body` | int64/string | 연결된 PR. 없으면 null |
| `context.pr_labels`·`issue_numbers`·`issue_titles`·`issue_bodies` | list | 맥락 결합 결과. **맥락 결합을 하지 않은 행(PARTIAL)은 null**, 했는데 없으면 빈 목록 |
| `context.review_comments[]` | list<struct> | `comment_id, body, path, line, side(LEFT/RIGHT), outdated, author` (ADR-018) |
| `replacement` | struct | `code, match_method(SAME_LOCATION/CALLER_CHANGE/NONE), confidence`. 맥락 결합을 하지 않은 행은 null |
| `reason` | struct | `label(8종), evidence_grade(EXPLICIT/INFERRED/UNKNOWN), evidence_text, confidence, classifier_version`. `UNDISTINGUISHED`·`NOT_CLASSIFIED` 는 null |
| `source_url` | string | GitHub 커밋 링크 |
| `code_policy` | string | `CODE_INCLUDED` · `LINK_ONLY` (내보내기 속성, 행마다. §4) |
| `classification.source` | string | §1 표 (내보내기 속성) |
| `classification.training_repo` | bool | 학습 저장소 20개인가 |
| `classification.classifier_version` | string | 이 행에 돌린 분류기 (사람이면 `human`). `UNDISTINGUISHED` 도 채운다 |
| `classification.split`, `classification.human_method` | string | `HUMAN` 행만: `train/val/test`, `AGREED/THIRD_PARTY` |

§4.4 에 있는 `deleted_body_normalized`·`embedding` 은 이 버전에서 만들지 않아 넣지 않았다. 추출 중간 필드(`added_hunks_same_file`, `filter_evidence`)도 넣지 않았다. Parquet 스키마는 모든 파일이 같다 (`pipeline.export.SCHEMA`, 검증에서 파일마다 대조).

## 7. 받기와 사용

### 7.1 GitHub Release 에서 받기

- 저장소 100개 전체는 이 저장소(`Deleted-Code-Search/Deleted-Code-Search`)의 GitHub **Releases** 에 tag **`dataset-v0.9`** 로 올린다. Git 에는 확정 500건과 목록만 있다 (§5)
- **현재 상태: GitHub Release `dataset-v0.9`로 배포했다.** Release: https://github.com/Deleted-Code-Search/Deleted-Code-Search/releases/tag/dataset-v0.9
- Release 페이지에서 tag `dataset-v0.9` 의 Assets 를 연다. 파일 이름이 `<owner>__<name>-<k>-of-<n>.<형식>` 이라 저장소 이름으로 골라 받을 수 있다 (예: `psf/requests` → `psf__requests-00000-of-00001.parquet`). 어느 저장소가 몇 shard 인지는 `repos.csv` 의 `shards` 열, 파일별 행 수·sha256 은 `manifest.json` 에 있다
- 형식은 하나만 받으면 된다 — Parquet(열 단위, 분석용)과 jsonl.gz(한 줄 한 레코드)는 같은 행이다

Release 가 올라간 뒤 gh CLI 로:

```bash
# 전체 (약 1.1GB)
gh release download dataset-v0.9 -R Deleted-Code-Search/Deleted-Code-Search -D dataset-v0.9
# 한 형식만, 또는 저장소 하나만
gh release download dataset-v0.9 -R Deleted-Code-Search/Deleted-Code-Search -D dataset-v0.9 --pattern '*-of-*.parquet' --pattern 'repos.csv' --pattern 'manifest.json' --pattern 'SHA256SUMS'
gh release download dataset-v0.9 -R Deleted-Code-Search/Deleted-Code-Search -D dataset-v0.9 --pattern 'psf__requests-*.jsonl.gz'
# 무결성 (전체를 받았을 때. 일부만 받았으면 --ignore-missing)
cd dataset-v0.9 && sha256sum -c SHA256SUMS
```

### 7.2 읽기

```python
import glob

import pyarrow.dataset as ds

files = sorted(glob.glob("dataset-v0.9/*-of-*.parquet"))  # 저장소 파일만 (main500-* 제외)
table = ds.dataset(files, format="parquet").to_table()
human = table.filter(ds.field("classification", "source") == "HUMAN")
```

```python
import gzip
import json

path = "dataset-v0.9/psf__requests-00000-of-00001.jsonl.gz"
with gzip.open(path, "rt", encoding="utf-8") as f:
    rows = [json.loads(line) for line in f]
```

두 형식을 같이 받으면 같은 행이 두 번이다. 저장소 파일과 `main500-*` 도 겹친다 — 전체를 읽을 때는 저장소 파일 한 형식만 쓴다.

### 7.3 Release 만들기 (팀)

아래로 다시 만들고 검증이 통과(`manifest.json` `"ok": true`)한 뒤 올린다. 추출·분류를 다시 돌리지 않는다. 메모리가 빠듯하면 `--workers 2` 로 돌린다.

```bash
python -m pipeline.export \
  --input data/assembled_v0.9/records_v0.9.jsonl --input data/assembled_v0.9/records100_hs_v0.9.jsonl \
  --input data/assembled_v0.9/records100_jh_v0.9.jsonl --input data/assembled_v0.9/records100_sj_v0.9.jsonl \
  --classified data/classified/model_only_old20.jsonl --classified data/classified/model_only_hs.jsonl \
  --classified data/classified/model_only_jh.jsonl --classified data/classified/model_only_sj.jsonl \
  --llm-sample data/classified/llm_sample.jsonl --out data/export/dataset-v0.9
gh release create dataset-v0.9 data/export/dataset-v0.9/* --title "dataset-v0.9" --notes-file datasets/dataset-v0.9/README.md
```

## 8. 알려진 한계

- **필터 정밀도 목표(90%) 미달.** v0.8 에서 76.0% (#89 2차). v0.9 는 새 표본으로 다시 재지 않았다. KEPT 행에도 이동·포맷·생성 코드 삭제가 섞여 있다
- **분류 정확도 게이트 2 미달.** LLM 포함 test 60.0%, 모델만 val 52.0% (같은 20개 저장소 안). 학습 저장소 밖으로 일반화되지 않는다 — 그래서 신규 80개 모델만 라벨은 이유 미구분이다
- **사람 라벨 kappa 가 낮다.** 사전 등록 최종 평균 `reason` 0.499 / `grade` 0.404. 제3자 판정 150건은 판정 이유가 기록되지 않았다 (`docs/reports/labels_main500.md`)
- **분류 결과는 v0.8 입력으로 냈다.** v0.9 에서 `NOISE_GENERATED` 가 된 행의 분류 결과는 쓰지 않았다 (`manifest.json` `checks.join`). v0.9 KEPT 의 분류 대상은 모두 분류 결과가 있다
- **PARTIAL 은 맥락 결합·분류를 하지 않았다** (#142). `context` 목록 칸·`replacement`·`reason` 이 null 이다
- **라이선스 때문에 34,391행은 코드 없이 링크만 있다** (확정 500건 중 19건 포함, 라벨·split 은 그대로, §4). 판정 근거는 `docs/repo_final100.md` §3.1 — 자동 수집한 원문으로 1차 판정했고 "확인 필요"·"불확실" 항목은 원문을 수동 검토했다. 현재 루트 라이선스 파일 경로의 이력과 현재 트리의 하위 라이선스 파일만 봤다 — 파일 머리 라이선스 주석·지워진 디렉터리의 라이선스는 보지 않았다. 이력 경계는 `author_date` 날짜로 갈라 몇 건은 어긋날 수 있다. 벤더링 경로는 v0.9 `NOISE_GENERATED` 가 경로로 명백한 것만 뺐다
- **PostgreSQL 적재는 이번 범위가 아니다.** #83 본문에는 DB 적재가 있지만 이번 PR 은 파일 내보내기까지만 한다. DB 적재는 검색 UI(#8) 때 별도 이슈로 한다
- 리뷰 코멘트·PR·이슈 본문은 GitHub 사용자가 쓴 글이다. 코드 라이선스와 별개다
- `recent_only` 저장소 5개(django·scikit-learn·pandas·celery·requests)는 2015년 이후만 채굴했다 (`docs/repo_final100.md`)
