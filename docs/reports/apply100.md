# 분류기 100개 저장소 적용 - 모델만 전체, LLM 포함 표본 (#180)

- 작성: 2026-10-28, 희수(hs). 숫자 원본: `docs/reports/apply100.json` (`classify.apply_report`)
- 코드: `classify/apply.py`, `classify/apply_report.py`
- **성능은 버전마다 그 숫자만 말한다.** 모델만 `r1+m2` 는 val **52.0%**, LLM 포함 `r1+m2+c3+llm:deepseek-ai/deepseek-v4.1-flash` 는 test **60.0%** 다. 두 숫자 모두 학습과 **같은 20개 저장소** 안에서 쟀다. 모델만 라벨에 60.0% 를 붙이지 않는다
- 이 보고서의 일치율은 **정확도가 아니다.** 정답이 없는 레코드에서 두 버전이 같은 라벨을 냈는지만 센다

## 1. 무엇을 돌렸나

| | 모델만 | LLM 포함 |
|---|---|---|
| 버전 | `r1+m2` | `r1+m2+c3+llm:deepseek-ai/deepseek-v4.1-flash` |
| 학습 | 확정 500건 중 train 300 | train+val 400 |
| 대상 | 분류 대상 전체 (FULL_FUNCTION·KEPT) **870,115건** | 저장소마다 30건 표본 **3,000건** |
| 표본 | - | 저장소마다 `sha256(20261027:record_id)` 가 가장 작은 30건. 확정 500건은 뺐다 |

- 입력은 v0.8 파일 4개다: 기존 20개(`records_v0.8.jsonl`), 희수 21개·성제 38개·재헌 21개(`records100_{hs,sj,jh}_v0.8.jsonl`, 사후 필터 `NOISE_FORMAT` 까지). 디스크 때문에 한 파일로 합치지 않았다. `classify.apply` 가 여러 입력을 이어서 읽는다
- 출력 행마다 **`training_repo`** 를 붙였다: 확정 500건을 뽑은 20개 저장소면 `true` (조장 조건 ①). 학습·검증·평가 split 모두 같은 20개 저장소에서 나왔다
- 사람 확정 500건은 모델만 출력에도 들어 있지만(500건 겹침) 데이터셋에서는 사람 라벨을 쓴다
- 처리 시간: 모델만은 파일 4개를 동시에 돌려 기존 20개 1,681초 · 희수 2,023초 · 성제 900초 · 재헌 1,194초(가장 긴 것 약 34분)이고 실패는 없다. LLM 포함은 §4 "LLM 호출"

## 2. 라벨 분포

| | 건수 | 라벨 |
|---|---:|---|
| 모델만, 기존 20개 | 250,486 | DESIGN 127,174 · UNK 100,165 · FEAT 13,744 · DEAD 7,922 · LIB 1,449 · BUG 31 · SEC 1 |
| 모델만, 신규 80개 | 619,629 | UNK 323,114 · DESIGN 296,514 · LIB 1 |
| LLM 포함 표본, 기존 20개 | 600 | UNK 276 · DESIGN 200 · FEAT 65 · DEAD 40 · LIB 16 · BUG 2 · SEC 1 |
| LLM 포함 표본, 신규 80개 | 2,400 | UNK 974 · DESIGN 820 · FEAT 352 · DEAD 190 · LIB 43 · BUG 11 · SEC 7 · PERF 3 |

- **모델만 버전은 신규 80개에서 UNK·DESIGN 만 낸다** (61만여 건 중 다른 라벨 1건)
- 원인: m2 는 학습 300건(기존 20개)의 낱말로 판단한다. 표본에서 모델이 아는 낱말 비율(중앙값)이 기존 0.69 → 신규 0.60~0.61 로 떨어지고, 가장 높은 확률(중앙값)도 0.65 → 0.55~0.59 다. 기존 20개에서 DEAD·FEAT 가 나오던 것도 그 저장소에만 있는 낱말 덕분이다
- 등급: 모델만 EXPLICIT 1,810 (0.2%), LLM 포함 표본 EXPLICIT 15 (0.5%). **분류기 등급은 보수적이다** - EXPLICIT 비율은 사람 확정 500건 기준(110/500)으로 보고한다 (`classifier_grade_v3.md`)

## 3. 표본에서 두 버전 일치 (조장 조건 ②)

| | 일치 | 95% 구간 | kappa | LLM 이 UNK·DESIGN 이 아닌 이유를 낸 건 중 일치 |
|---|---:|---|---:|---:|
| 전체 3,000 | 1,876 (62.5%) | 60.8~64.3% | 0.405 | 21 / 730 |
| 기존 20개 600 | 436 (72.7%) | 69.0~76.1% | 0.559 | **21 / 124 (16.9%)** |
| 신규 80개 2,400 | 1,440 (60.0%) | 58.0~61.9% | 0.367 | **0 / 606 (0%)** |

LLM 포함 라벨별 일치 (모델만이 같은 라벨을 낸 건 / LLM 포함이 그 라벨을 낸 건):

| | UNK | DESIGN | FEAT | DEAD | LIB | BUG | SEC | PERF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 기존 20개 | 233/276 | 182/200 | 8/65 | 6/40 | 7/16 | 0/2 | 0/1 | - |
| 신규 80개 | 653/974 | 787/820 | 0/352 | 0/190 | 0/43 | 0/11 | 0/7 | 0/3 |

- **신규 80개의 일치 1,440건은 전부 UNK(653)·DESIGN(787)이다.** LLM 포함이 다른 이유를 낸 606건 중 모델만이 맞힌 것은 0건이다. 조장이 정한 기준의 "UNK·DESIGN 에서만 맞는 식" 에 그대로 해당한다
- kappa 0.367 은 이유를 가려서가 아니라 UNK 와 DESIGN 을 가르는 데서 나온 값이다
- **기존 20개도 구체적인 이유는 잘 맞히지 못한다** (21/124). 신규 80개만 내리면 기존 20개가 더 믿을 만해 보이는 착시가 생긴다는 조장의 우려와 맞는 숫자다
- 갈린 쌍 상위: (모델만 DESIGN, LLM 포함 UNK) 364, (UNK, FEAT) 239, (DESIGN, FEAT) 165, (DESIGN, DEAD) 130, (UNK, DEAD) 91
- 저장소별(30건씩) 일치율은 JSON `sample_agreement.per_repo` 에 있다. 30건이라 ±18%p 정도로 거칠어 참고로만 본다. 가장 낮은 곳은 `plotly/plotly.py`·`python-poetry/poetry`·`run-llama/llama_index` 20%, 가장 높은 곳은 `stanfordnlp/dspy`·`Huanshere/VideoLingo` 100%(LLM 포함도 거의 모두 UNK)다
- **판정은 조장이 한다** (신규 80개 모델만 라벨을 "이유 미구분" 으로 내릴지). 이 보고서는 숫자만 낸다

## 4. 한계

- **일치율은 정확도가 아니다.** LLM 포함 버전도 test 60.0% 다. "모델만이 LLM 포함과 다르다" 는 "모델만이 틀렸다" 가 아니다
- **두 버전은 독립이 아니다.** LLM 포함 버전의 점수에는 모델 확률(학습+검증 400건)이 들어 있다. 그래서 일치율은 독립된 두 판단보다 높게 나온다. 신규 0/606 은 그런데도 나온 숫자다
- **val 52.0% 는 신규 저장소에 옮겨 쓸 수 없다.** 같은 20개 저장소 안에서 잰 숫자이고, val 정답의 72% 가 UNK·DESIGN 이었다
- **`stanfordnlp/dspy` 표본 30건 중 29건이 한 커밋(`Remove test_before_pypi (#7937)`) 이다.** 패키지 사본 디렉터리 하나를 지운 커밋이 필터를 통과해 dspy KEPT 가 97,044건(제외 6,804건)으로 튀었다. 이 저장소의 표본 일치 30/30 도 그 때문이다 (모두 UNK). 필터(`pipeline/`) 쪽 확인이 필요하다
- **`training_repo` 는 분류기 출력에만 있다.** §4.4 `DeletionRecord` 에는 이 칸이 없다. 데이터셋(#83) 레코드에 넣으려면 §13 변경 절차가 필요하다
- **100개가 파일 4개다** (합계 49.6GB). 노트북 디스크 때문에 합친 파일은 만들지 않았다
- **LLM 호출:** NVIDIA 무료 API 가 오류 코드 없이 줄을 세웠다 (2026-10-27 저녁, 짧은 요청 하나에 96초 → HTTP 200). 시간 초과를 120초에서 300초로 늘리고 10개 프로세스로 나눠 6시간 38분 걸렸다. 1회차 호출 실패 8건(시간 초과 4, HTTP 오류 4)은 2회차에 다시 받았다. 마지막 합치기는 API 0회 / 캐시 3,000회다. 호출이 실패한 건은 LLM 없이 분류되는데 버전은 LLM 포함으로 적히므로, 그런 건이 하나라도 있으면 `classify.apply` 가 출력을 남기지 않는다

## 5. 재현

```bash
# 모델만 - 파일마다 (기존 20개 예)
python -m classify.apply --input data/assembled/records_v0.8.jsonl \
    --out data/classified/model_only_old20.jsonl
# 표본만 뽑기 - 파일마다 (큰 입력을 한 번만 읽는다)
python -m classify.apply --input data/assembled/records_v0.8.jsonl --sample-only \
    --sample-per-repo 30 --out data/classified/sample_old20.jsonl
# LLM 포함 - 캐시가 있으면 API 를 부르지 않는다 (datasets/llm_cache/classifier)
python -m classify.apply --input data/classified/sample_old20.jsonl \
    --input data/classified/sample_hs.jsonl --input data/classified/sample_sj.jsonl \
    --input data/classified/sample_jh.jsonl --llm --sample-per-repo 30 \
    --out data/classified/llm_sample.jsonl
# 요약
python -m classify.apply_report --model-only data/classified/model_only_old20.jsonl \
    --model-only data/classified/model_only_hs.jsonl --model-only data/classified/model_only_sj.jsonl \
    --model-only data/classified/model_only_jh.jsonl --with-llm data/classified/llm_sample.jsonl \
    --json docs/reports/apply100.json
```
