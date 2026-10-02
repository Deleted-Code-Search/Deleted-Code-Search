# 필터 정밀도 미달 원인 분석 (#89)

담당: 성제. 입력은 `docs/reports/filter_precision.{json,md}`(결과)와 `data/filter_precision/`의 표본·키·판정 3개다.
**결과 숫자는 바꾸지 않는다** — 정밀도 57.0%, Fleiss kappa 0.462, 판정: 미달. 이 문서는 그 결과를 나눠 보고 원인을 정리한다.
판정을 다시 하지 않았다. 분류는 삭제 본문·커밋 메시지·판정자 `note`에 적힌 것을 옮긴 것이고, 추가로 쓴 신호는
조립 결과(`data/assembled/filtered.jsonl`)의 `is_test_code`·`added_hunks_same_file`과 같은 커밋의 레코드 수뿐이다.

기준 문서: CHARTER §4.2 ②(노이즈 정의)·§15, `docs/labeling_guide.md` §6.3.3, `docs/filter_rules.md` v0.7, `docs/evaluation.md` "필터 정밀도 사전 등록".

## 요약

1. **정밀도 미달의 가장 큰 단일 원인은 포맷 변경이다.** KEPT 중 다수결 "아니오" 43건 가운데 17건이 포맷·주석·독스트링만 바뀐 것이다(black/ruff 재포맷 11, 타입 표기 2, 재들여쓰기 2, 독스트링 2). CHARTER §4.2 ②에 있으나 미구현인 `NOISE_FORMAT`이다.
2. **#80 보류 항목(이동+리네임 0.9 미만, 커밋 간 이동)으로 고쳐지는 건은 43건 중 0건이다.** 사전 등록한 미달 시 절차만으로는 정밀도가 오르지 않는다.
3. **사후 적용 가능한 규칙만으로는 90%에 못 간다.** 표본 안에서 "예"를 하나도 잃지 않는 규칙 셋(포맷 11 + 저장소 분리 5 + 더미 생성 파일 1 = 17건)을 다 적용해도 57/83 = **68.7%**다. 90%가 되려면 남은 "아니오"가 6건 이하여야 하므로 43건 중 37건 이상을 "예" 손실 없이 걸러야 한다.
4. **kappa가 낮은 것은 필터가 아니라 판정 기준이 갈렸기 때문이다.** 불일치 70건의 대부분이 두 패턴이다. ① 같은 로직이 다른 모양으로 다시 나타나는 제자리 재작성을 희수는 "아니오", 재헌은 "예"(49건이 희수 아니오·재헌 예). ② revert·폴더 통째 삭제·벤더 코드 제거에 딸려 지워진 함수를 재헌만 "아니오"(10건). 판정 화면에 추가된 코드가 없어 판정자 대부분이 diff를 보지 못하고 커밋 메시지로 추정했다(note에 "not verified in diff"가 sj 139건, hs 65건, 재헌 note 99건은 "~로 보임"으로 끝남).
5. 표본은 레코드 단위로 뽑으므로 큰 커밋이 몰린다. 43건 중 15건이 커밋 5개에서 나왔다(keras `84afc519` black 4건, langchain `9ff5b5d2` 저장소 분리 4건, home-assistant `4de97abc` black 3건, pandas `bb613588` black 2건, transformers `3a275d35` ruff 2건).

아래 추정은 **같은 표본 100건으로 규칙을 고르고 같은 100건으로 센 것**이라 낙관적이다. 규칙을 바꾸면 §8.4대로 새 표본으로 다시 재야 한다.

---

## 분석 1: KEPT 100건 정밀도 나눠 보기

### deletion_kind별

| deletion_kind | 다수결 예 / 건수 | 정밀도 |
|---|---:|---:|
| FULL_FUNCTION | 33 / 45 | 73.3% |
| PARTIAL | 24 / 55 | 43.6% |
| 전체 | 57 / 100 | 57.0% |

### is_test_code별

| is_test_code | 다수결 예 / 건수 | 정밀도 |
|---|---:|---:|
| True | 22 / 34 | 64.7% |
| False | 35 / 66 | 53.0% |

| deletion_kind × is_test_code | 다수결 예 / 건수 | 정밀도 |
|---|---:|---:|
| FULL_FUNCTION · 테스트 | 12 / 13 | 92.3% |
| FULL_FUNCTION · 비테스트 | 21 / 32 | 65.6% |
| PARTIAL · 테스트 | 10 / 21 | 47.6% |
| PARTIAL · 비테스트 | 14 / 34 | 41.2% |

`is_test_code`는 경로에 `tests` 디렉터리가 있을 때만 True다(`pipeline/extract.py:_is_test_code`, #75). 그래서 keras의 `*_test.py` 5건(fp-086, fp-087, fp-108, fp-110, fp-129)은 False로 잡혀 있다. 이 표는 필드 값을 그대로 썼다.

### 저장소별

| 저장소 | 다수결 예 / 건수 | 정밀도 |
|---|---:|---:|
| home-assistant/core | 16 / 30 | 53.3% |
| huggingface/transformers | 8 / 14 | 57.1% |
| pandas-dev/pandas | 6 / 11 | 54.5% |
| keras-team/keras | 4 / 9 | 44.4% |
| langchain-ai/langchain | 2 / 8 | 25.0% |
| vllm-project/vllm | 6 / 7 | 85.7% |
| apache/superset | 3 / 4 | 75.0% |
| django/django | 2 / 3 | 66.7% |
| commaai/openpilot | 2 / 3 | 66.7% |
| unslothai/unsloth | 1 / 2 | 50.0% |
| pydantic/pydantic | 2 / 2 | 100% |
| browser-use/browser-use | 2 / 2 | 100% |
| celery/celery | 2 / 2 | 100% |
| crewAIInc/crewAI | 0 / 1 | 0% |
| unclecode/crawl4ai | 0 / 1 | 0% |
| scikit-learn/scikit-learn | 1 / 1 | 100% |

저장소당 건수가 작아(30건 이상은 home-assistant 하나) 저장소 간 차이는 해석하지 않는다. langchain 8건 중 4건은 한 커밋(저장소 분리)이다.

### 판정자별 KEPT "예" 비율

| 판정자 | KEPT 예 | FULL_FUNCTION 예 (45) | PARTIAL 예 (55) | 테스트 예 (34) | 비테스트 예 (66) | 참고: 200건 전체 예 |
|---|---:|---:|---:|---:|---:|---:|
| hs | 38 / 100 (38%) | 26 | 12 | 11 | 27 | 41 |
| jh | 57 / 100 (57%) | 23 | 34 | 25 | 32 | 79 |
| sj | 65 / 100 (65%) | 40 | 25 | 22 | 43 | 71 |

KEPT 표결 분포: 3예 27건, 2예 30건, 1예 19건, 0예 24건. 만장일치가 51건뿐이다.
판정자 경향: 희수는 PARTIAL에 엄격하다(12/55). 재헌은 FULL_FUNCTION에 가장 엄격하고(23/45) PARTIAL에 가장 너그럽다(34/55). 성제는 FULL_FUNCTION을 거의 다 "예"로 봤다(40/45).

---

## 분석 2: KEPT 중 다수결 "아니오" 43건의 원인

분류 순서는 위에서부터 먼저 맞는 것 하나다(이동 → 포맷 → 대량 → 생성 → 테스트 → 짧은 PARTIAL → 불명). 예컨대 테스트 파일을 black으로 재포맷한 건은 포맷에 둔다.
"재등장 %"는 삭제 줄을 공백 제거·따옴표 통일·닫는 괄호 앞 쉼표 제거로 정규화한 뒤, 같은 파일 추가 헝크(`added_hunks_same_file`) 전체를 같은 방식으로 정규화한 문자열에 부분 문자열로 있는 줄의 비율이다. 판정 근거가 아니라 필터 후보 추정용 신호다.

### 분류 집계

| 원인 | 건수 | 세부 |
|---|---:|---|
| 놓친 이동·리네임 | 3 | 이동+리네임 0.9 미만 **0** / 커밋 간 이동 **0** / 그 밖 3 |
| 포맷·주석·독스트링만 변경 | 17 | black·ruff·yapf 재포맷 11, 타입 표기 현대화 2, 새 블록으로 재들여쓰기 2, 독스트링 2 |
| 대량 구조 변경 | 5 | 모두 다른 저장소로 분리하는 커밋 |
| 생성·벤더링 코드 | 1 | |
| 테스트 코드 정리 | 9 | |
| 짧은 PARTIAL (5줄 이상이지만 사소한 수정) | 7 | |
| 판단 근거 불명 | 1 | note는 있으나 목록 유형 밖 |
| 합계 | 43 | |

43건 모두 note가 있다. 그래서 "note 없음"으로 불명이 된 건은 없다.

### 건별 근거

표결은 "예" 표 수다(0~1).

**놓친 이동·리네임 — 그 밖 (3)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-058 | unsloth | FULL 21 | 0 | 메시지 "remove redundant backend.backend folder". 같은 커밋의 6개 파일·FULL 91건 모두 같은 파일 추가 헝크가 없다. 세 note 모두 "다른 경로로 이동" 추정이고 목적지는 확인하지 않았다(이미 있던 사본을 지운 것이면 가이드 §6.3.3상 "예"다) |
| fp-071 | home-assistant | FULL 8 | 0 | 메시지 "Make Tuya find_dpcode a class method". 지워진 것은 본문 없는 `@overload` 스텁이다. 같은 파일 추가 헝크에 `@classmethod def find_dpcode(cls, ...)`가 있으나 시그니처가 다르다(`dptype` 인자가 없다). 같은 파일 안에서 모듈 함수가 클래스 메서드가 됐다 |
| fp-187 | openpilot | FULL 5 | 0 | 메시지 "move common.window to xx". 커밋 전체가 이 파일의 함수 4개를 지운 것뿐이고 추가 헝크가 없다. hs note는 목적지가 다른 저장소일 것으로 봤다 |

**포맷·주석·독스트링만 변경 (17)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-004 | pandas | PARTIAL 5 | 0 | 메시지 "re-wrap docstrings". 삭제 줄이 전부 독스트링이고 재등장 100% |
| fp-017 | transformers | PARTIAL 6 | 0 | 메시지 "Reformat source code with black", 재등장 100% |
| fp-027 | transformers | PARTIAL 11 | 0 | 메시지 "[style] Rework ruff rules … all typing". `Optional[...]` 시그니처 줄만 지워졌다(note: 타입 표기 현대화). 재등장 0%라 공백 정규화로는 잡히지 않는다 |
| fp-028 | keras | PARTIAL 35 | 0 | 메시지 "Reformatting the codebase with black"(2칸 → 4칸 들여쓰기), 재등장 100% |
| fp-037 | keras | PARTIAL 31 | 0 | fp-028과 같은 커밋, 재등장 100% |
| fp-052 | vllm | PARTIAL 8 | 0 | 메시지 "Convert formatting to use ruff instead of yapf + isort", 재등장 100% |
| fp-057 | home-assistant | PARTIAL 6 | 0 | 메시지 "Black", 테스트 파일, 재등장 100% |
| fp-087 | keras | FULL 3 | 0 | black 커밋(fp-028과 같은 커밋). 제자리 재포맷이 FULL_FUNCTION 삭제로 잡혔다. ADR-014 결정 3에 따라 같은 위치는 이동이 아니다. 재등장 100% |
| fp-089 | pandas | PARTIAL 8 | 0 | 메시지 "STYLE: Apply black formatting", 재등장 100% |
| fp-102 | pandas | PARTIAL 16 | 0 | fp-089와 같은 커밋, 재등장 100% |
| fp-108 | keras | FULL 107 | 0 | black 커밋. 제자리 재포맷이고 재등장 100% |
| fp-115 | langchain | PARTIAL 16 | 1 | 기능 커밋(runnable map streaming)이다. 지워진 줄이 새 `async with` 블록 안에서 들여쓰기만 바뀌어 재등장 100%(hs note "indent only"). jh는 "크게 재작성"으로 보고 "예"를 줬다 |
| fp-141 | home-assistant | PARTIAL 6 | 1 | 새 조건문 아래로 재들여쓰기됐고 재등장 100%(hs note). jh는 "예" |
| fp-143 | transformers | PARTIAL 5 | 0 | fp-027과 같은 커밋, 타입 표기만 바뀜. 재등장 0% |
| fp-160 | home-assistant | PARTIAL 13 | 0 | 메시지 "Black". 재등장 85%이고 나머지는 줄 분할 차이다 |
| fp-167 | home-assistant | PARTIAL 7 | 0 | 메시지 "Black", 재등장 100% |
| fp-174 | transformers | PARTIAL 13 | 1 | 독스트링의 `Examples::` 블록만 지워졌다(코드 샘플 리팩터 #5036). 재등장 38%. sj는 "예" |

**대량 구조 변경 (5)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-045 | langchain | FULL 3 | 1 | 메시지 "community: move to separate repo". 이 커밋에서 FULL_FUNCTION 13,208건이 나왔고 1,870개 파일 모두 추가 헝크가 없다. 저장소 안에는 대응 코드가 없다. sj만 "예" |
| fp-091 | langchain | FULL 7 | 1 | fp-045와 같은 커밋(테스트 파일) |
| fp-145 | langchain | FULL 10 | 1 | fp-045와 같은 커밋 |
| fp-198 | langchain | FULL 31 | 1 | fp-045와 같은 커밋 |
| fp-047 | langchain | FULL 12 | 1 | 메시지 "databricks: mv to partner repo". 이 커밋에서 FULL 106건, 9개 파일 모두 추가 헝크가 없다. sj만 "예" |

**생성·벤더링 코드 (1)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-178 | transformers | FULL 2 | 1 | `src/transformers/utils/dummy_tf_objects.py`. 메시지 "update the dummy-creation process", hs note "auto-generated dummy file". sj만 "예" |

**테스트 코드 정리 (9)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-008 | home-assistant | PARTIAL 5 | 1 | config flow 테스트의 입력 dict를 region이 들어간 공용 입력으로 바꿨다(메시지 "Missing region key in config flow test") |
| fp-018 | home-assistant | PARTIAL 5 | 1 | 기대 trace dict의 `"variables"` 위치가 바뀌었다(hs note). 재등장 100% |
| fp-030 | pandas | PARTIAL 5 | 1 | `to_datetime` cache 인자 추가에 맞춰 assert를 고쳤다(메시지·hs note) |
| fp-038 | home-assistant | PARTIAL 13 | 0 | `hass.helpers.entity_registry` 접근을 직접 호출로 치환했다(메시지 #72032) |
| fp-050 | home-assistant | PARTIAL 15 | 0 | `patch(utcnow)` 블록을 freezegun으로 바꿨다(메시지) |
| fp-098 | django | PARTIAL 41 | 1 | `Sum`을 try/finally로 바꿔 끼우던 테스트를 서브클래스 방식으로 재구성했다(hs note). 재등장 72% |
| fp-129 | keras | PARTIAL 15 | 1 | 분할 결과 assert 블록이고 변수 이름만 바뀌었다(hs note). `_test.py`인데 `is_test_code`=False |
| fp-132 | home-assistant | PARTIAL 12 | 1 | unittest를 pytest 함수로 재작성했다(메시지 #41223) |
| fp-183 | pandas | PARTIAL 6 | 0 | `Series`를 `pd.Series`로 이름공간만 치환했다(메시지 #67276) |

**짧은 PARTIAL — 5줄 이상이지만 사소한 수정 (7)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-007 | crewAI | PARTIAL 6 | 0 | 메시지 "fix ruff linting and mypy issues". 흩어진 6줄이고 재등장 50%라 순수 포맷은 아니다 |
| fp-009 | home-assistant | PARTIAL 5 | 0 | 메시지 "Small cleanups". assert와 convert 호출을 타입 검사 블록 안으로 옮겼다(hs note). 재등장 80% |
| fp-010 | crawl4ai | PARTIAL 7 | 0 | `LlmConfig`를 `LLMConfig`로 일괄 리네임했다(메시지) |
| fp-081 | superset | PARTIAL 5 | 1 | revert로 세션 처리 줄이 바뀌었고 함수는 남았다(hs note). sj만 "예" |
| fp-096 | home-assistant | PARTIAL 6 | 1 | `hass.data`를 `runtime_data`로 치환했다(메시지 #144950) |
| fp-126 | transformers | PARTIAL 5 | 1 | assert를 예외로 치환했다(메시지 #13909) |
| fp-172 | home-assistant | PARTIAL 10 | 0 | 도메인 문자열 리터럴을 `DOMAIN` 상수로 치환했다(메시지 #180122) |

**판단 근거 불명 (1)**

| sample_id | 저장소 | 종류·줄 | 표결 | 근거 |
|---|---|---|---:|---|
| fp-180 | home-assistant | FULL 4 | 1 | `state` 프로퍼티를 `_attr_state` 클래스 속성으로 바꿨다(메시지·note). note에 이유는 분명하지만 FULL_FUNCTION의 기계적 리팩터라 목록의 어느 유형에도 맞지 않는다. 그래서 지시대로 불명에 뒀다 |

---

## 분석 3: 판정자 불일치 70건

불일치는 KEPT 49건, NOISE_TRIVIAL 21건이다. NOISE_MOVE 50건은 모두 만장일치 "아니오"다.

### 소수 의견을 낸 판정자

| 층 | 소수 판정자·방향 | 건수 |
|---|---|---:|
| KEPT | hs만 아니오 | 19 |
| KEPT | jh만 아니오 | 10 |
| KEPT | jh만 예 | 10 |
| KEPT | sj만 예 | 9 |
| KEPT | sj만 아니오 | 1 |
| NOISE_TRIVIAL | jh만 예 | 16 |
| NOISE_TRIVIAL | hs만 아니오 | 4 |
| NOISE_TRIVIAL | hs만 예 | 1 |

### 유형별 패턴 (KEPT 49건)

| 유형 | 건수 | 갈린 방향 | 건 |
|---|---:|---|---|
| ① 같은 자리 재작성·기계적 치환(로직이 다른 모양으로 다시 나타남) | 20 | hs 아니오, jh 예. sj는 반반 | fp-008, 018, 021, 030, 032, 042, 065, 066, 079, 096, 098, 107, 115, 126, 129, 132, 141, 150, 153, 185 |
| ② 다른 곳으로 옮겨 흡수됨(hs가 목적지를 note에 적음) | 9 | hs만 아니오 | fp-034, 062, 070, 092, 121, 127, 152, 157, 188 |
| ③ revert·폴더 통째 삭제·저장소 비우기·벤더 코드 제거에 딸려 지워짐 | 10 | jh만 아니오 | fp-016, 022, 031, 036, 059, 078, 086, 110, 137, 173 |
| ④ 다른 저장소로 분리 | 5 | sj만 예 | fp-045, 047, 091, 145, 198 |
| ⑤ 독스트링·생성 파일·얇은 getter·revert 부분 수정 | 4 | sj만 예 | fp-081, 174, 178, 180 |
| ⑥ xfail 테스트 삭제 | 1 | sj만 아니오 | fp-139 |

테스트 코드에서 한 사람만 갈리는 패턴은 따로 보이지 않는다. 테스트 건의 불일치는 유형 ①·②에 섞여 있다. 판정자별로 보면 hs 11/34, jh 25/34, sj 22/34다.

note로 본 각 패턴의 근거:

- **①** 희수는 같은 로직이 다시 나타나면 "아니오"로 봤다. 재헌은 바뀐 이유가 있으면 "예"로 봤다. 같은 건을 두 사람이 다른 질문으로 판정한 셈이다.
  - fp-066 hs: "oot_validator -> model_validator(mode="after"), values[...] -> self.… ; logic identical" (원문 그대로, 앞 글자 누락)
  - fp-066 jh: "Pydantic 2 호환성 적용 과정에서 기존 root_validator 기반 환경 검증 로직이 크게 재작성된 것으로 보임."
- **②** 희수는 diff에서 목적지를 찾아 이동으로 봤다. 나머지 둘은 도구가 비슷한 함수를 못 찾았다는 것만 보고 "예"를 줬다. 가이드 §6.3.3("본문이 거의 그대로 다른 위치 → 아니오")에 따르면 희수 쪽이 맞을 수 있는 건이 있다.
  - fp-062 hs: "module-level function moved into GenerateSchema as method _get_wrapped_inner_schema; body near-identical". 같은 파일 재등장 87%
  - fp-070 hs: "body identical in shared AttentionMaskConverter._expand_mask"
  - fp-062 sj: "not verified in diff whether moved into a method/other file"
- **③** 재헌은 revert나 대량 정리에 딸려 지워진 함수를 "아니오"로 봤다.
  - fp-059 jh: "CI 실패로 되돌린 revert 커밋에서 삭제됨"
  - fp-036 jh: "오래된 중복 가이드 폴더를 통째로 제거하면서 … 함께 삭제됨"
  - 희수와 성제는 "no counterpart"를 근거로 "예"를 줬다(fp-022 hs: "revert boundary").
  - CHARTER §4.2 ②의 `NOISE_BULK`(파일 전체 삭제 + 메시지 + 함수 100개 이상)와 가이드 §6.3.3("실제 삭제 → 예") 중 어느 쪽을 적용하느냐가 갈렸다.
- **④** 세 사람 모두 다른 저장소로의 이동이라고 적었다. 성제만 "저장소 안에 대응 코드가 없다"를 근거로 "예"를 줬다(fp-045 sj: "no counterpart in this repo"). §6.3.3은 저장소 밖 이동을 다루지 않는다.

### 재헌·희수가 갈린 60건 (쌍별 kappa 0.315)

| 방향 | KEPT | NOISE_TRIVIAL | 합 |
|---|---:|---:|---:|
| hs 아니오 · jh 예 | 29 | 20 | 49 |
| hs 예 · jh 아니오 | 10 | 1 | 11 |

- **hs 아니오·jh 예 49건:** KEPT 29건은 유형 ①(20)과 ②(9)다. NOISE_TRIVIAL 20건도 같은 패턴이다. 한두 줄 제자리 수정을 희수는 "같은 줄이 조금 바뀜", 재헌은 "의도가 있는 변경"으로 봤다.
  - fp-190 hs: "gelu_fast coefficient literal fixed 7978845608 -> 0.7978845608; one-token fix on same line"
  - fp-190 jh: "mixed precision 환경에서 GELU 정밀도 문제를 수정하기 위해 … 변경됨"
  - 이 20건 중 16건은 재헌 혼자 "예"다(성제도 아니오). 그래서 다수결 NOISE_TRIVIAL "예" 비율(12%)에는 거의 반영되지 않았다.
- **hs 예·jh 아니오 11건:** 유형 ③ 10건(revert 3, 폴더·저장소 비우기 3, 벤더 subtree 1, 리팩터에 딸린 helper 3)과 TRIVIAL fp-049 1건이다. fp-049는 hs가 diff를 확인해 제자리 재작성으로 봤다.

### 판정 근거의 한계

판정 화면에는 추가된 코드가 없었다(`docs/evaluation.md` "판정자가 보는 것"). 그래서 대부분 커밋 메시지로 추정했다.

| 판정자 | note에 "not verified/not checked/확인 필요" | note에 "commit msg" | note가 "~로 보임"으로 끝남 | note에 diff를 확인했다고 적음 |
|---|---:|---:|---:|---:|
| hs | 65 | 77 | 0 | 14 |
| jh | 8 | 0 | 99 | 0 |
| sj | 139 | 109 | 0 | 0 |

유형 ①·②는 대체 코드를 보면 대부분 결론이 날 건이다. kappa 0.462의 상당 부분은 필터가 아니라 판정 절차에서 나왔다고 본다.

---

## 필터 보강 후보

"걸러짐"은 표본 KEPT 100건에 규칙을 적용했을 때 다수결 "아니오" 43건 중 몇 건이 빠지는지다. "예 손실"은 다수결 "예" 57건 중 몇 건이 함께 빠지는지다. 둘 다 같은 표본으로 규칙을 고른 추정치다.

| 후보 | CHARTER §4.2 ② | 43건 중 걸러짐 | 예 손실 (57건 중) | 사후 적용 | 쓰는 필드 / 다시 돌려야 하는 이유 |
|---|---|---:|---:|---|---|
| **A1. NOISE_FORMAT — 메시지 포맷 키워드 + 삭제 줄 재등장 ≥ 90%** | 포맷·주석·독스트링 | **11** (fp-004, 017, 028, 037, 052, 057, 087, 089, 102, 108, 167) | **0** | **가능** | `deleted_body`, `added_hunks_same_file[].added_body`, `commit_message`. 키워드: black, ruff, yapf, isort, reformat, formatting, style, lint, re-wrap 등 |
| A2. A1에서 재등장 기준을 80%로 | 〃 | 12 (+fp-160) | 0 | 가능 | 〃 |
| A3. 삭제 줄 재등장 ≥ 90%만(메시지 무관) | 〃 | 14 (+fp-018, 115, 141) | 2 (fp-042, fp-188) | 가능 | 〃. 재들여쓰기까지 잡지만 "예"를 잃는다 |
| A4. 메시지 포맷 키워드만 | 〃 | 16 | 0 | 가능 | `commit_message`. 표본에서는 손실이 없지만 "style"·"lint" 같은 낱말이 기능 변경 커밋에도 붙는다(fp-132 "pytest style"). 표본 밖 오탐 위험이 가장 크다 |
| B. 타입 표기 현대화(`Optional[X]` → `X \| None` 등) | 포맷 | 2 (fp-027, 143) | 표본에서는 0 | 가능하지만 비권장 | 같은 필드를 쓰지만 타입 표기 정규화 규칙이 따로 필요하다. 2건에 비해 규칙이 크다 |
| C. 독스트링만 삭제 | 독스트링 | 1 (fp-174). fp-004는 A1이 잡는다 | 표본에서는 0 | **재추출 필요** | `deleted_body`는 지워진 줄만 담는다. 그 줄이 독스트링 안인지는 부모 파일 원문을 파싱해야 안다. NOISE_MOVE가 PARTIAL을 다루지 못하는 것과 같은 이유다 |
| D1. NOISE_BULK — CHARTER 문구 그대로(파일 삭제 + "remove/delete directory/module" + 함수 ≥ 100) | 대량 구조 변경 | **0** | **1** (fp-036 "Remove the guides folder") | 근사로 가능 | `commit_message`, `commit_sha`별 FULL_FUNCTION 집계, `added_hunks_same_file`이 비었는지(파일 삭제의 근사). 파일이 실제로 지워졌는지(diff 상태 D)는 레코드에 없다. 정확히 하려면 추출을 다시 돌려야 한다 |
| D2. 다른 저장소로 분리(메시지 "move/mv … repo" + 그 파일의 추가 헝크 없음) | 대량 구조 변경(문구 확장) | **5** (fp-045, 047, 091, 145, 198) | **0** | 근사로 가능 | D1과 같다. CHARTER 문구 밖이라 §13 변경 절차가 필요하다 |
| D3. 함수 ≥ 100 + 파일 추가 없음(메시지 무관) | 대량 구조 변경(문구 축소) | 6 (D2 + fp-178) | **9** (fp-034, 036, 064, 078, 086, 105, 110, 123, 137) | 근사로 가능 | 권하지 않는다. 판정자들은 deprecated 모델 삭제, 벤더 코드 제거, revert를 대부분 "예"로 봤다 |
| E. NOISE_GENERATED — 경로 `dummy_*_objects.py` | 생성 코드 | 1 (fp-178) | 0 | **가능** | `file_path`. 일반 패턴(`migrations/`, `vendor/`, `_pb2.py`)은 표본에서 0건이다. fp-078(벤더 gunicorn, 다수결 예)은 경로가 `gunicorn/sock.py`라 경로로 가릴 수 없다 |
| F. NOISE_RENAME — 본문 동일, 이름만 변경 | 리네임 | 0 | — | 재추출 필요 | 같은 커밋에서 추가된 함수 목록과 본문이 레코드에 저장되지 않는다. 다른 파일 쪽은 `added_hunks_same_file`에도 없다 |
| **G. #80 보류 ① 이동+리네임 유사도 0.9 미만** | 이동 | **0** | 위험 있음. 다수결 예 중 hs가 "거의 그대로 옮김"이라 적은 fp-062(재등장 87%), fp-070, fp-152가 빠질 수 있다. 이 셋은 §6.3.3상 노이즈일 수 있어 판정 쪽 문제이기도 하다 | 재추출 필요 | NOISE_MOVE 후보(같은 커밋의 추가 함수 본문)는 추출 단계에서만 만든다. 조립 결과에는 KEPT 건의 후보가 남지 않는다 |
| **H. #80 보류 ② 커밋 간 이동** | 이동(문구 확장) | **0** | — | 재추출 필요 | 이웃 커밋의 추가 함수가 필요하다. 레코드는 삭제 쪽만 담는다 |
| (참고) 테스트 코드 제외 | 해당 없음(§4.2 ②는 플래그로 보존) | 12 | 22 | 가능 | 권하지 않는다. CHARTER 위반이고 손실이 더 크다 |
| (참고) PARTIAL 최소 줄 수 5 → 7 | ADR-015 변경 | 14 | 8 | 가능 | 권하지 않는다. 짧은 PARTIAL에도 "예"가 많다 |

### 사전 등록 대응(#80 보류 항목)만 했을 때

43건 중 **0건**이 고쳐진다(G·H 모두 0). 사전 등록한 미달 시 절차("#80에서 보류한 항목을 재검토하고 필터를 고친 뒤 다시 잰다")만으로는 정밀도가 그대로다. 오히려 G는 다수결 "예" 일부를 뺄 위험이 있다. "그 밖" 이동 3건(fp-058, 071, 187)도 #80 항목이 아니다. 저장소 밖 이동이거나, 이미 있던 사본을 지운 것이거나, 시그니처가 바뀐 클래스 메서드 통합이다.

### 조합했을 때 (표본 안 추정)

| 조합 | 걸러짐 | 예 손실 | 남은 KEPT | 정밀도 |
|---|---:|---:|---:|---:|
| 현재 | — | — | 100 | 57.0% |
| A1 + D2 + E (사후 가능, 손실 0) | 17 | 0 | 83 | 68.7% |
| A2 + B + D2 + E (사후 가능, 손실 0) | 20 | 0 | 80 | 71.3% |
| A3 + B + C + D2 + E | 23 | 2 | 75 | 73.3% |
| 90%에 필요한 것 | ≥ 37 | 0 | ≤ 63 | 90% |

남는 "아니오"는 주로 테스트 코드 정리 9건, 짧은 PARTIAL 7건, 재들여쓰기, 기계적 치환이다. 판정자 사이에서도 갈리는 유형이라(분석 3의 ①) 필터 규칙으로 나누기 어렵다.

## 다음에 정할 것 (결정은 팀 몫, 이 PR은 분석만)

1. **사후 적용 가능한 A1·D2·E부터 넣을지.** 재헌 영역이다. D2는 CHARTER §4.2 ② 문구 밖이라 §13 절차가 필요하다. 넣으면 §8.4대로 새 표본으로 다시 잰다.
2. **판정 기준을 먼저 맞출지(변경 제안).** 유형 ①(같은 자리 재작성·기계적 치환)과 ③(revert·폴더 삭제에 딸린 삭제)을 "예"로 볼지 "아니오"로 볼지가 정해지지 않으면, 필터를 고쳐도 다시 잴 때 kappa가 오르지 않는다. 경계는 `docs/labeling_guide.md` §6.3.3이 정하므로 거기를 고치는 일이다.
3. **판정 화면에 같은 파일 추가 헝크를 보여 줄지(변경 제안).** 지금은 판정자가 대체 코드를 못 봐서 커밋 메시지로 추정한다. 바꾸면 다음 측정의 사전 등록(`docs/evaluation.md`)이 달라진다.
4. **90% 목표를 이 정의로 유지할지.** 위 추정으로는 사후 규칙만으로 70% 안팎이다. CHARTER §10.1 목표를 바꾸는 것은 §13 절차 대상이다.
