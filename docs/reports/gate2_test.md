# 게이트 2 입력 - test 100 결과 (#86, #87)

- 작성: 2026-10-14, 희수(hs). 숫자 원본: `docs/reports/gate2_test.json`, 예측: `docs/reports/gate2_test/{ours,keyword_a,llm_b}.jsonl`
- **판정은 이 문서가 하지 않는다.** #92 에서 `docs/meetings/gate-2.md` 로 한다. 여기는 사전 등록 기준에 숫자를 넣어 본 것까지다
- 실행: 2026-10-14 20:31 ~ 22:00, 커밋 `616fe2d`. **test 는 이 한 번만 돌렸다.** H 기록(PR #175, `docs/evaluation.md` "H 기록")이 main 에 머지된 뒤다

## 1. 결과

| 방식 | 버전 | test 정확도 | 95% 구간 (Wilson) |
|---|---|---:|---|
| 모두 UNK 라고 답하기 | - | 50.0% | - |
| 기준선 A (커밋 메시지 키워드) | `a1` | 33.0% | 24.6~42.7% |
| 기준선 B (LLM 직접 질의) | `b1` / deepseek-v4.1-flash | 38.0% | 29.1~47.8% |
| **우리 방식** | `r1+m2+c3` + deepseek-v4.1-flash | **60.0%** | 50.2~69.1% |

**사전 등록 기준에 넣어 보면** (`docs/evaluation.md` "게이트 2 사전 등록", "H 기록"):

| # | 기준 | 값 | 우리 방식 | 충족 |
|---|---|---:|---:|---|
| ① | 정확도 ≥ min(80%, H = 80.8%) | 80.0% | 60.0% | **아니오** (−20.0%p) |
| ② | 정확도 ≥ max(A, B) + 10%p | 48.0% | 60.0% | 예 (+12.0%p) |

둘 다 충족해야 통과다 (ADR-016 결정 3).

**짝 비교** (같은 100건에서 한쪽만 맞힌 건, 정확 이항 검정):
- 우리 vs B: 우리만 31건 : B 만 9건 (p = 0.0007)
- 우리 vs A: 우리만 36건 : A 만 9건 (p = 0.0001)

## 2. 실행 중 생긴 예외 두 건 (결과를 보기 전에 다시 부르지 않기로 정했다)

| 방식 | 레코드 | 무엇이 | 처리 | 정확도에 줄 수 있는 영향 |
|---|---|---|---|---|
| 우리 방식 | `18e7be08…` (home-assistant/core `name`) | LLM 호출 시간 초과 | 정의된 동작대로 LLM 표 없이 모델만으로 분류 → DESIGN (정답 DESIGN) | LLM 이 다른 라벨을 냈다면 틀릴 수 있었다 → **59~60%** |
| 기준선 B | `aa251005…` | 8종 밖 라벨 `TST` | 사전 정의대로 UNK (정답 DEAD) | 제대로 읽혔다면 맞았을 수 있다 → **38~39%** |

- 두 건 모두 ①·② 충족 여부를 바꾸지 않는다
- 시간 초과 건의 c3 답은 캐시에 없다. 이 코드로 다시 돌리면 그 1건은 API 를 다시 부르고 답이 다를 수 있다. **판정에 쓰는 것은 커밋한 예측 파일이다**

## 3. 클래스별 (F1)

| 이유 | 정답 수 | 우리 방식 | 기준선 B | 기준선 A |
|---|---:|---:|---:|---:|
| UNK | 50 | **0.729** | 0.423 | 0.542 |
| DESIGN | 21 | **0.549** | 0.407 | 0.000 |
| DEAD | 18 | **0.500** | 0.417 | 0.261 |
| FEAT | 6 | **0.444** | 0.278 | 0.000 |
| LIB | 4 | 0.000 | **0.400** | 0.182 |
| BUG | 1 | 0.000 | 0.000 | 0.000 |

정답 등급별 정확도 (EXPLICIT 24 / INFERRED 26 / UNKNOWN 50):

| | EXPLICIT | INFERRED | UNKNOWN |
|---|---:|---:|---:|
| 우리 방식 | 11 | 14 | **35** |
| 기준선 B | 11 | 12 | 15 |
| 기준선 A | 4 | 0 | 29 |

우리 방식 혼동 행렬 (행 = 정답, 열 = 예측):

| 정답 \ 예측 | BUG | LIB | DEAD | DESIGN | FEAT | UNK |
|---|---:|---:|---:|---:|---:|---:|
| BUG | | | | 1 | | |
| LIB | | | 1 | 1 | | 2 |
| DEAD | | | 7 | 3 | 4 | 4 |
| DESIGN | | 2 | | 14 | 1 | 4 |
| FEAT | | | 1 | | 4 | 1 |
| UNK | | | 1 | 11 | 3 | 35 |

기준선 A·B 의 혼동 행렬과 precision·recall 은 JSON 에 있다.

## 4. 읽는 법

- **test 는 절반이 UNK 다.** 모두 UNK 라고 답해도 50.0% 이고, 기준선 A·B 는 그보다 낮다. 우리 방식은 그 바닥보다 10%p 높다
- **기준선 B 는 검증 53.0% → test 38.0% 로 떨어졌다.** UNK 를 21건만 낸다(정답 50). test 의 UNK 비율이 검증(39%)보다 높아 B 의 약점이 크게 드러났다. 우리 방식은 검증 59.0% → test 60.0% 다
- 우리 방식이 B 를 앞선 것은 대부분 **UNK** 에서다 (UNK 정답 50건 중 우리 35 · B 15). 사람이 "근거 없음" 으로 둔 건을 B 는 DESIGN(21)·FEAT(11) 로 채운다
- 약한 곳은 검증과 같다. **DEAD** 18건 중 7건(FEAT 4, UNK 4, DESIGN 3 으로 감), **UNK 를 DESIGN 으로** 11건
- 우리 방식은 test 에서 EXPLICIT 등급을 한 건도 내지 않았다 (정답 EXPLICIT 24). 등급은 판정에 쓰지 않지만, 데이터셋에 붙일 때 분류기 등급이 EXPLICIT 을 거의 못 낸다는 뜻이다 (`docs/reports/classifier_val.md` §4)

## 5. 한계 (판정 기록에 함께 적을 것)

- `docs/reports/classifier_val.md` §5 그대로다 - 우리 LLM 은 가이드 요약도 받는다(B 는 `b1` 그대로), 라벨러가 AI 보조 프롬프트를 썼을 수 있다, test 의 이유 분포(집계)는 실행 전에 봤다
- H 의 95% 구간이 72.0~87.4% 라 ① 기준 80% 는 점추정이다 (`docs/evaluation.md` "H 기록")
- 100건이라 구간이 넓다. 그래도 우리 방식의 95% 구간(50.2~69.1%)은 B(29.1~47.8%)·A(24.6~42.7%)와 겹치지 않고, 짝 비교도 우연과 구별된다(p = 0.0007). ① 의 80% 는 우리 구간 밖이다

## 6. 재현

```bash
python -m classify.classifier --records datasets/labels/main500_records.jsonl --labels datasets/labeled_500.jsonl --llm --split test --final-test --out docs/reports/gate2_test/ours.jsonl
python -m classify.baseline_llm --input datasets/labels/main500_records.jsonl --split test --final-test --out docs/reports/gate2_test/llm_b.jsonl
python -m classify.baseline_keyword --input datasets/labels/main500_records.jsonl --split test --final-test --out docs/reports/gate2_test/keyword_a.jsonl
python -m classify.evaluate docs/reports/gate2_test/ours.jsonl --split test
```

LLM 응답 캐시(test 199건)는 파일 수 때문에 따로 올린다. 캐시가 있으면 위 첫 두 줄은 시간 초과 1건만 다시 묻는다.
