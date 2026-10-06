# 필터 정밀도 (#89, CHARTER §10.1) — 2차 재측정

- 시드 20261004 · filter_rule_version v0.8 · 조립 결과 sha256 `28100c446a5eae133f5c63737f339bb71e16a72550ac695bd70ab094ac4977e1`
- 판정자: hs, jh, sj · 표본 {'KEPT': 100, 'NOISE_MOVE': 34, 'NOISE_TRIVIAL': 33, 'NOISE_FORMAT': 33} · 모집단 {'KEPT': 465621, 'NOISE_MOVE': 203610, 'NOISE_TRIVIAL': 1014860, 'NOISE_FORMAT': 36982}

## 판정: 미달
- 정밀도 76.0% (목표 ≥ 90%) — 미달
- Fleiss kappa 0.763 (목표 ≥ 0.7) — 충족

## 지표
| 지표 | 값 |
|---|---|
| 정밀도 (통과 중 예) | 76.0% (Wilson 95% 66.8%–83.3%) |
| NOISE_MOVE 오판율 (이동으로 제외됐는데 예) | 2.9% (1/34, Wilson 95% 0.5%–14.9%) |
| NOISE_FORMAT 오판율 (포맷으로 제외됐는데 예) | 0.0% (0/33, Wilson 95% 0.0%–10.4%) |
| NOISE_TRIVIAL 중 예 비율 | 39.4% |
| 재현율 (층별 모집단 가중) | 46.6% |
| Fleiss kappa (3인) | 0.763 |
| 만장일치 | 165/200 |

## 쌍별 일치도 (보고용)
| 쌍 | Cohen kappa | 단순 일치율 |
|---|---|---|
| hs+jh | 0.830 | 91.5% |
| hs+sj | 0.741 | 87.5% |
| jh+sj | 0.720 | 86.0% |

## 혼동표 (다수결)
| 필터 \ 사람 | 예 | 아니오 |
|---|---:|---:|
| KEPT | 76 | 24 |
| NOISE_MOVE | 1 | 33 |
| NOISE_TRIVIAL | 13 | 20 |
| NOISE_FORMAT | 0 | 33 |

판정자별 예: hs 85, jh 100, sj 76

미달 시 절차: docs/evaluation.md "2차 재측정 사전 등록"의 "미달 시".

---

> 아래 절들은 `eval/filter_precision.py` 출력이 아니라 손으로 쓴 분석이다. 집계를 `--md-out` 으로 다시 돌리면 지워진다.
> 위 숫자는 세 판정 파일과 `key.json` 으로 다시 집계해 이 파일·JSON 과 바이트 단위로 같음을 확인했다 (2026-10-06).

## 1차 표본과 겹친 건수

0건 (1차 200건 · 2차 200건, `record_id` 기준). 사전 등록이 결과와 함께 적으라고 한 값이다.

## KEPT 아니오 24건 분석

사전 등록(`docs/evaluation.md` "필터 정밀도 2차 재측정 사전 등록" → "미달 시")대로 KEPT 중 다수결 "아니오" 24건을
라벨 가이드 §6.3.3 1~5번으로 나눴다.

**방법.** 근거는 세 판정자의 `note` 와 판정용 파일의 diff(`deleted_body`·`added_hunks_same_file`)만 썼다. 분석자가
새로 판정하지 않았다 — "아니오" 쪽 판정자들의 `note` 가 가리키는 번호를 따랐고, `note` 가 번호를 말하지 않으면
`note` 의 서술(무엇이 어디로 어떻게 바뀌었나)을 §6.3.3 표에 대어 맞췄다. 어느 번호에도 맞지 않으면 "미분류"로 두었다.
**확인 필요**는 ① 번호의 근거를 diff 로 확인하지 못했다고 판정자가 적었거나 ② 2:1 로 갈렸고 소수 의견이 번호의 근거
자체를 반박하는 건이다. 번호 경계(가이드 표)에 걸치기만 하는 건은 "비고"에 경계만 적었다. `record_id` 는 앞 8자리다
(가이드 §6.3.3 예시와 같은 표기). "판정"은 sj/jh/hs 순서의 예(Y)·아니오(N)다.

| sample_id | record_id | 저장소 | 함수 | 종류 | 판정 | 번호 | 근거 (note·diff) | 비고 |
|---|---|---|---|---|---|---|---|---|
| fp2-001 | `85e560e4` | pandas-dev/pandas | `test_append_records` | PARTIAL | N/N/N | 2 | black 재포맷(따옴표, `2.`→`2.0`, 줄 합치기), 추가 헝크에 같은 값 — 셋 다 | |
| fp2-009 | `e55606cf` | huggingface/transformers | `__init__` | FULL | Y/N/N | 3 | `dummy_tf_objects.py` 의 2줄 dummy 생성자, 자동 생성 파일이라는 hs·sj note | 확인 필요 — 생성 표시를 diff 에서 본 판정자가 없다(셋 다 "not verified/확인되지 않음") |
| fp2-024 | `314fb9fb` | unslothai/unsloth | `_align_vae_dtype` | PARTIAL | N/N/N | 5 | docstring·주석 9줄을 같은 자리에서 같은 사실로 줄임(추가 헝크), 코드 변화 없음 — 셋 다 | |
| fp2-027 | `2770ca53` | home-assistant/core | `test_supported_features` | PARTIAL | N/N/N | 5 | `SUPPORT_*` → `ClimateEntityFeature.*`, 같은 자리·같은 6개·같은 순서(추가 헝크 [3/4]) | 2번(문법 현대화)과 경계 |
| fp2-035 | `fcd6c03d` | pandas-dev/pandas | `test_replace_empty_pattern` | PARTIAL | N/N/N | 2 | `Series` → `pd.Series` 일괄 치환, 추가 헝크와 1:1 같은 값 — 셋 다 | 5번과 경계 |
| fp2-049 | `99e5fa38` | scikit-learn/scikit-learn | `test_select_heuristics_regression` | PARTIAL | N/N/N | 2 | black 재포맷(따옴표·줄바꿈·`(5, )`→`(5,)`), 같은 인자 — 셋 다 | |
| fp2-050 | `e1786ec3` | langchain-ai/langchain | `on_tool_start` | FULL | N/N/N | 4 | `community/` 패키지째 langchain-ai/langchain-community 로 분리(#31060) — 셋 다 | |
| fp2-069 | `d94f599d` | pandas-dev/pandas | `std` | PARTIAL | Y/N/N | 미분류 | docstring 의 `.. versionadded::` 지시문만 삭제, 추가 헝크 0개 | 확인 필요 — 아래 "미분류" |
| fp2-070 | `de43d3cd` | scikit-learn/scikit-learn | `f` | FULL | N/N/N | 3 | "MAINT Unvendor joblib"(#13531), `sklearn/externals/joblib/externals/loky/` 벤더링 코드 — 셋 다 | |
| fp2-073 | `e991ece0` | langchain-ai/langchain | `url` | FULL | N/N/N | 4 | fp2-050 과 같은 커밋(#31060) — 셋 다 | |
| fp2-078 | `ec455987` | commaai/openpilot | `_button_msg` | FULL | N/N/N | 3 또는 4 | "Remove old panda subtree" — jh 3번(벤더링), hs 별도 레포 submodule 로 전환(날짜로 추정, diff 아님), sj 3/4 | 확인 필요 — 3번과 4번 사이 |
| fp2-083 | `11ae4928` | home-assistant/core | `add_card` | PARTIAL | N/N/Y | 5 | 같은 load→view 찾기→변환→저장 흐름이 같은 자리에 `yaml.` 접두사로 다시 들어감(추가 헝크 [16]-[20]) — sj·jh | 확인 필요 — sj 가 `load_yaml(fname, True)` 차이를 diff 로 확인 못 함, hs 는 예 |
| fp2-087 | `b9057460` | huggingface/transformers | `create_token_type_ids_from_sequences` | PARTIAL | N/N/N | 2 | `List[int]` → `list[int]`, 추가 헝크와 1:1 — 셋 다 | |
| fp2-090 | `ad3f87b1` | home-assistant/core | `mock_network` | PARTIAL | N/Y/N | 2 | 삭제 9줄 중 8줄이 괄호 `with (...)` 형태로 들여쓰기만 바뀌어 다시 들어감(추가 헝크 [1/1]) — sj·hs | 확인 필요 — jh 는 같은 헝크에 새로 붙은 `async_get_loaded_adapters` mock 을 근거로 예 |
| fp2-096 | `a9088051` | langchain-ai/langchain | `from_url` | FULL | N/N/N | 1 | 같은 파일에 본문 그대로, 들여쓰기만 4칸 줄어 다시 들어감(클래스가 중첩 밖으로) — 셋 다 | |
| fp2-108 | `9314c856` | home-assistant/core | `test_encoding_subscribable_topics` | PARTIAL | N/Y/N | 5 | fixture 이름 변경·`caplog` 인자 제거·타입 힌트(추가 헝크 [10/13]) — sj "borderline n5", hs | 확인 필요 — jh 는 fixture 구조 변경으로 예, hs 도 본문 변화 미확인 |
| fp2-118 | `46aa6dc8` | vllm-project/vllm | `is_deep_gemm_e8m0_used` | PARTIAL | N/N/N | 2 | yapf/isort → ruff 재포맷, 백슬래시 줄 이음 → 괄호 — 셋 다 | |
| fp2-119 | `819dd506` | home-assistant/core | `test_options_template_error` | PARTIAL | N/N/N | 2 | `data_entry_flow.FlowResultType.X` → `FlowResultType.X`, 추가 헝크와 1:1 — 셋 다 | 5번과 경계 |
| fp2-125 | `b62a8898` | home-assistant/core | `mock_backup_nvm_raw` | FULL | N/Y/N | 1 | 중첩 함수가 같은 파일 `backup_nvm` fixture 로 옮겨 감, 본문 같음 + `-> bytes`(추가 헝크 [1/10]) — sj "borderline n1", hs | 확인 필요 — jh 는 함께 추가된 progress `emit()` 줄을 근거로 예("옮기며 다시 씀") |
| fp2-143 | `c7046469` | commaai/openpilot | `test_data_id_3` | FULL | N/Y/N | 3 | "Squashed 'pyextra/' changes", `git-subtree-dir: pyextra` 서드파티 json-rpc — sj·hs | jh 는 이동·대체를 diff 에서 못 찾아 예. 벤더링 근거(subtree)는 반박하지 않음 |
| fp2-144 | `8dcbe096` | langchain-ai/langchain | `test_batch_from_str` | FULL | N/N/N | 4 | fp2-050 과 같은 커밋(#31060) — 셋 다 | |
| fp2-154 | `3f9cb74c` | vllm-project/vllm | `_get_parameter_value` | FULL | N/N/N | 1 | 중복 helper 를 `vllm/tool_parsers/utils.py::get_parameter_value` 로 통합, 같은 로직 — hs 가 PR diff 로 확인 | 확인 필요 — 새 위치가 다른 파일이라 판정 화면 밖, sj·jh 는 미확인 |
| fp2-181 | `b021bcb3` | pandas-dev/pandas | `test_loc_getitem_iterator` | FULL | N/Y/N | 미분류 | 같은 파일에 같은 이름·같은 로직으로 다시 들어가고 fixture 만 `test_data` → `string_series`(추가 헝크 [7/15]) | 확인 필요 — 아래 "미분류" |
| fp2-194 | `8ea95d50` | huggingface/transformers | `from_pretrained` | FULL | N/Y/N | 3 | `dummy_tf_objects.py` 자동 생성 dummy 의 3줄 stub, "Better dummies" 로 재생성 — sj·hs | 확인 필요 — 생성 표시 미확인(sj "judgment call"), jh 는 metaclass 로 구조를 바꾼 리팩터링으로 예 |

### 번호별 건수

| 번호 | 건수 | 그중 확인 필요 | sample_id |
|---|---:|---:|---|
| 1 순수 이동·리네임 | 3 | 2 | 096, 125, 154 |
| 2 기계적 포맷·스타일 변환 | 7 | 1 | 001, 035, 049, 087, 090, 118, 119 |
| 3 생성·벤더링 코드 | 4 | 2 | 009, 070, 143, 194 |
| 3 또는 4 | 1 | 1 | 078 |
| 4 다른 저장소로 분리 | 3 | 0 | 050, 073, 144 (모두 langchain #31060 한 커밋) |
| 5 사소한 다듬기 (PARTIAL) | 4 | 2 | 024, 027, 083, 108 |
| 미분류 | 2 | 2 | 069, 181 |
| **계** | **24** | **10** | |

- 만장일치 "아니오" 15건, 2:1 9건(009, 069, 083, 090, 108, 125, 143, 181, 194). 2:1 의 소수 "예"는 jh 6건·sj 2건·hs 1건이다.
- 종류별: PARTIAL 12건(2번 7, 5번 4, 미분류 1), FULL_FUNCTION 12건(1번 3, 3번 4, 3/4번 1, 4번 3, 미분류 1).
- 2번 7건 중 3건(001, 049, 118)은 커밋 메시지 첫 줄에 포맷 키워드가 있는데도 v0.8 NOISE_FORMAT 을 빠져나갔다.
  같은 규칙 함수(`pipeline/postfilter.py` 의 `format_keywords`·`reappear_counts`)로 재보면 재등장이 001 2/6, 049 5/6,
  118 2/5 로 0.9 미만이다 — 포매터가 줄을 합치거나 나누면 줄 단위 재등장이 깨진다. 나머지 4건(035, 087, 090, 119)은
  첫 줄에 키워드가 없다(087 타입 표기 현대화는 `docs/filter_rules.md` 에 이미 "미구현"으로 적혀 있다).

### 미분류 2건

가이드 §6.3.3 은 "아니오"를 1~5번으로 닫고 **나머지는 전부 "예"**를 기본값으로 정한다. 아래 두 건은
**가이드 기본값상 예(1~5 해당 없음), 다수결은 아니오**다.

- **fp2-069** (`std`, PARTIAL, sj 예 / jh·hs 아니오): docstring 의 `.. versionadded::` 지시문 줄만 지우고 대신 들어간 줄이
  없다. hs 는 5번, jh 는 "비기능적 문서 정리"로 아니오. 5번은 "같은 자리에서 거의 같은 뜻으로 **바뀐**" 것이라 대체 없이
  지운 경우와 맞지 않고, 포매터 변환(2번)도 아니다 — 1~5 해당 없음.
- **fp2-181** (`test_loc_getitem_iterator`, FULL_FUNCTION, jh 예 / sj·hs 아니오): 같은 이름·같은 로직이 같은 파일에 fixture
  이름만 바뀌어 다시 들어갔다. "제자리 소폭 수정"은 5번의 모양이지만 5번은 FULL_FUNCTION 에 쓰지 않고, 함수 이름·파일이
  같아 1번(이동·리네임)도 아니다 — 1~5 해당 없음.

**정밀도 76.0%, KEPT 아니오 24건, 위 번호별 표는 그대로 둔다** — 사전 등록이 3인 다수결을 최종 판정으로 정했으므로
판정 뒤에 기준을 다시 대어 바꾸지 않는다.

사후 참고치(**확정값 아님**): 두 건에 가이드 기본값("예")을 적용하면 KEPT 아니오 22건, 정밀도 78/100 = 78.0% 다.
판정이 끝난 뒤 계산한 숫자라 미달·충족 판단에 쓰지 않는다.

## 미달 시 절차에 따른 다음 조치

사전 등록 "미달 시"는 ① KEPT "아니오"를 §6.3.3 번호별로 나눠 기록하고(위 절) ② 필터를 고치면 규칙 버전을 올리고 **새
시드로 새 표본**을 뽑아 다시 잰다. #80 보류 항목 절차는 다시 등록하지 않았다. kappa 가 0.763 으로 충족이라 정밀도 76.0%
는 확정 숫자다.

**이 PR 에서는 필터 규칙을 고치지 않는다.** 아래는 규칙 수정 후보이고, 각각 별도 이슈 + 테스트 + 새 표본 재측정(CHARTER §8.4)
으로 간다. 같은 24건으로 규칙을 고르고 같은 24건으로 효과를 세지 않는다 — 그래서 "고치면 몇 %"라는 표본 안 추정은
내지 않는다.

| 순위 | 후보 | 걸리는 건 (최대) | 담당 영역 | 근거와 주의 |
|---|---|---:|---|---|
| 1 | **NOISE_GENERATED 구현** — 벤더링·자동 생성 | 4~5 (009, 070, 143, 194, +078) | 재헌 (`pipeline/`) | `docs/filter_rules.md` 에 이미 "미구현"으로 있는 유형이다. 이번 건의 단서: `*/externals/*` 경로, `git-subtree-dir:` 커밋, `utils/dummy_*_objects.py`. 4개 저장소에서 나온 단서라 과적합 위험 — 경로 목록은 이 24건이 아니라 저장소 20개의 구조에서 정해야 한다. 5건 중 3건이 확인 필요 |
| 2 | **NOISE_FORMAT 재등장 비교를 줄 단위에서 토큰 단위로** | 3 (001, 049, 118) | 성제 (`pipeline/postfilter.py`, #152) | 키워드는 걸렸는데 재등장 0.33~0.83 으로 빠졌다. 줄 합치기·나누기를 흡수하는 비교(공백·줄바꿈을 무시한 토큰열)가 후보. 임계 0.9 를 낮추는 것은 1차 분석의 "기능 커밋 첫 줄 style/lint" 오탐을 다시 연다 |
| 3 | **NOISE_MOVE 누락 원인 조사** (규칙 변경 전) | 1~3 (096, 125, 154) | 재헌 | 096 은 같은 파일 다른 자리·본문 같음인데 KEPT 다 — ADR-014 결정 3 안인지, 왜 빠졌는지 먼저 본다. 154 는 다른 파일 + 이름 변경(밑줄 제거), 125 는 줄 추가가 있어 경계. 결정 3·유사도 기준을 바꾸면 ADR-014 변경(§13) |
| 4 | 다른 저장소로 분리(4번) | 3 (+078) | — | 3건이 전부 langchain #31060 한 커밋이다. 규칙(커밋 단위 대량 삭제 + "separate repo")을 만들 근거로 약하다. sj note 로는 1차 표본에도 같은 커밋 건(fp-045/091/145/198)이 있었다 — 이 커밋 하나의 모집단 비중부터 센다 |
| — | 5번 사소한 다듬기 (PARTIAL 5줄 이상) | 4 (024, 027, 083, 108) | — | ADR-015 의 4줄 경계를 넘는 다듬기다. 경계를 바꾸면 ADR-015 변경(§13)이고, NOISE_TRIVIAL 층 "예" 39.4%(13/33)가 이미 재현율 46.6% 를 깎고 있어 경계를 올리는 쪽은 재현율과 맞바꾼다. 이번엔 후보로 올리지 않는다 |
| — | 미분류 2건 | 2 (069, 181) | 성제 (가이드) | 가이드 기본값상 예, 다수결은 아니오 2건. 판정 오류인지 기본값 예외인지 회의 안건 |

순위는 "걸리는 건 수"와 "이미 정의된 유형인가"로 매겼다. 1·2 가 모두 효과를 내도 최대 8건이라 90% 에 닿는다고 말할 수
없다(KEPT 100건 기준 76 + 8 = 84, 그것도 규칙을 고른 표본 안 숫자다).

## 알려진 한계 (사전 등록에 적은 것)

- 1차(57.0%·0.462) → 2차(76.0%·0.763) 차이를 필터(v0.8)·판정 기준(§6.3.3 1~5번)·판정 화면(추가 헝크) 중 어느 하나 덕으로
  돌릴 수 없다. 셋이 함께 바뀌었고 표본·판정자 경험도 다르다.
- NOISE_FORMAT 오판율 0/33 은 필터의 독립적 정확도라기보다 규칙과 판정 기준 2번이 얼마나 같은지를 잰다.
- 추가 헝크는 같은 파일만 담아 다른 파일로 간 이동은 화면만으로 보이지 않는다 — 위 fp2-154 가 그 예다.
- 제외 층이 34·33건이라 구간이 넓다(NOISE_MOVE 0.5–14.9%, NOISE_FORMAT 0.0–10.4%). 방향을 보는 데만 쓴다.
- #90 500건을 뺀 모집단이라 라벨 데이터와 같은 레코드로 맞대어 볼 수 없다.
- (사전 등록 밖, 관찰) 판정자별 "예"가 jh 100 · hs 85 · sj 76 으로 벌어져 있다. kappa 는 충족했지만 2:1 9건 중 6건이 jh
  혼자 "예"다.
