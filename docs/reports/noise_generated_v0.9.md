# NOISE_GENERATED (v0.9) 사후 확인 — #89 2차 표본 재채점 · 경로 표본 50건 (#184)

담당: 재헌 (jh). 2026-10-29. #184 PR 리뷰(조장) 후속.

**이 문서는 §8.4 정밀도 재측정이 아니다.** 새 수동 200건(통과 100 + 제외 100) 표본은 뽑지 않았고 판정도 하지 않았다
— **정식 재측정 미실시**. 아래 두 확인은 v0.9 규칙이 기존 판정 결과와 실제 걸린 행에서 어떻게 보이는지 보는 것이다.

1. 기존 #89 2차 표본 200건(`docs/reports/filter_precision_v2.md`)에 v0.9 를 적용해 같은 지표로 다시 센다
2. v0.9 적용 결과의 `NOISE_GENERATED` 150,861건에서 고정 시드로 50건을 뽑아 경로를 사람이 검토할 수 있게 표로 낸다

## 1. 2차 표본 200건 재채점

### 방법

- 표본·판정은 2차 그대로다. 새로 뽑거나 다시 판정하지 않았다
- 2차 표본의 입력 `data/assembled/records_v0.8.jsonl` 의 sha256 이 2차 `key.json` 의 `assembled_sha256`
  (`28100c446a5eae133f5c63737f339bb71e16a72550ac695bd70ab094ac4977e1`)과 같음을 확인했다. 이 파일에 사후 필터를
  적용한 `data/assembled_v0.9/records_v0.9.jsonl` 에서 2차 표본 200건(`data/filter_precision_v2/records.jsonl` 의
  `record_id` = 조립 결과의 `id`)을 찾아 v0.9 `filter_status` 를 읽었다
- 사후 필터는 KEPT 행만 바꾼다. 그래서 재채점에서 달라질 수 있는 것은 **KEPT 층 표본이 `NOISE_GENERATED` 로 빠지는 것**
  뿐이고, 정밀도는 2차와 같은 정의(통과 표본 중 3인 다수결 "예" 비율)로 v0.9 에서도 KEPT 인 표본만으로 센다

### 결과

200건 모두 찾았고 `filter_rule_version` 은 모두 v0.9 다. **v0.9 에서 `NOISE_GENERATED` 가 된 표본은 0건이다.**
층별 건수가 2차 키와 같다 (KEPT 100 · NOISE_MOVE 34 · NOISE_TRIVIAL 33 · NOISE_FORMAT 33).

| 지표 | 2차 (v0.8) | v0.9 적용 재채점 |
|---|---|---|
| 정밀도 (통과 중 예) | 76.0% (76/100, Wilson 95% 66.8%–83.3%) | **76.0%** (76/100, 같음) |
| NOISE_MOVE 오판율 | 2.9% (1/34) | 2.9% (1/34) |
| NOISE_FORMAT 오판율 | 0.0% (0/33) | 0.0% (0/33) |
| NOISE_TRIVIAL 중 예 비율 | 39.4% | 39.4% |
| 혼동표 | KEPT 76/24 · MOVE 1/33 · TRIVIAL 13/20 · FORMAT 0/33 | 같음 |
| Fleiss kappa · 만장일치 | 0.763 · 165/200 | 같음 (표본·판정이 같다) |
| 재현율 (층별 모집단 가중) | 46.6% | 아래 참고 |

- 판정(다수결)은 다시 집계하지 않았다. 바뀐 표본이 없어 혼동표는 2차 보고서 값 그대로다. sj·hs 판정 파일은 이 작업
  사본에 없어 `eval.filter_precision` 을 다시 돌리지는 않았다
- 재현율: 2차와 같은 식(네 층 가중)에서 v0.9 로 바뀌는 것은 KEPT 모집단 N 뿐이다. 20개 저장소 조립 결과에서
  `NOISE_GENERATED` 는 139건(pandas `_vendored`)이라 N_KEPT 는 최대 139 줄고, 식에 넣으면 46.58% → 46.58%(46.6%)로
  바뀌지 않는다. `NOISE_GENERATED` 층의 "예" 비율은 표본이 없어 식에 넣지 못한다 — 그 층을 넣는 것은 새 표본의 일이다
- 2차 KEPT "아니오" 24건 중 §6.3.3 3번(생성·벤더링)으로 나눈 4건과 3/4번 1건은 **v0.9 에서도 KEPT 다.**
  - fp2-009·fp2-194 `src/transformers/utils/dummy_tf_objects.py` — 코드 생성기 출력이지만 경로 segment 가 규칙 밖
  - fp2-070 `sklearn/externals/joblib/externals/loky/...` — `externals/` 는 일부러 넣지 않았다 (`docs/filter_rules.md`)
  - fp2-143 openpilot `pyextra/`, fp2-078 openpilot `panda/` — subtree 라 경로에 벤더 표시가 없다

  2차 분석의 후보 1(NOISE_GENERATED, "최대 4~5건")은 v0.9 에서 경로로 명백한 범위만 넣었기 때문에 이 표본에서는
  0건이다. v0.9 가 겨냥한 것은 표본에 거의 나오지 않는 저장소 100개의 venv·설치본·벤더 통삭제다(20개 조립 결과에서는
  139건, 100개에서는 150,861건).

### 한계

- **이 표본은 규칙을 고를 때 사용한 표본이므로 이 결과는 낙관적일 수 있다.** 2차 KEPT "아니오" 분석이 #184 규칙 후보
  1의 근거였다. 이번에는 바뀐 건이 0건이라 숫자가 그대로지만, 같은 표본으로 규칙을 고르고 효과를 센 숫자라는 점은 같다
- 20개 저장소 표본이다. v0.9 가 실제로 많이 거르는 dspy·pipenv·rasa·dash 등은 2차 모집단에 없다. 100개 기준 정밀도는
  이 재채점으로 알 수 없다
- **정식 재측정 미실시.** §8.4·§10.1 의 새 표본 200건 재측정은 하지 않았다

## 2. NOISE_GENERATED 무작위 50건 경로 확인

### 방법

- 모집단: `data/assembled_v0.9/` 의 v0.9 파일 4개에서 `filter_status == "NOISE_GENERATED"` 인 행 **150,861건**
  (site-packages 103,713 · vendor 46,580 · protobuf 528 · venv 40). 사후 필터 보고서(`*_postfilter.json`)의 합과 같다
- 추출: 파일 이름 순서(`records100_hs_v0.9.jsonl`, `records100_jh_v0.9.jsonl`, `records100_sj_v0.9.jsonl`,
  `records_v0.9.jsonl`)로, 파일 안은 줄 순서로 행을 이어 붙인 목록에 `random.Random(20261029).sample(목록, 50)`
  (Python 3.12). **시드 20261029**
- 각 건의 `repo`·`file_path`·`filter_evidence.path_category`·`matched_prefix` 와 커밋 메시지 첫 줄을 적었다
- "경로 확인"은 분석자(jh)가 경로와 커밋 첫 줄만 보고 적은 메모다. **diff·파일 내용을 열어 보지 않았고 정답 판정이 아니다.**
  표의 "검토"가 빈 칸이 아닌 건은 사람이 직접 볼 것을 권한다

### 표 (50건)

`#` 는 추출 순서, `종류` F = FULL_FUNCTION. 커밋은 앞 9자리.

| # | repo | file_path | path_category | matched_prefix | 함수 | 종류 | 커밋 · 첫 줄 | 경로 확인 | 검토 |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | pypa/pipenv | `pipenv/vendor/ruamel/yaml/error.py` | vendor | `pipenv/vendor` | `__init__` | F | `861b3c650` vendor: remove ruamel-yaml | 벤더 사본 (ruamel.yaml) | |
| 2 | pypa/pipenv | `pipenv/patched/piptools/_vendored/pip/_vendor/webencodings/tests.py` | vendor | `pipenv/patched/piptools/_vendored` | `test_encode` | F | `14fabb272` Delete vendored pip from piptools | 벤더 사본 안의 벤더 사본 (pip → webencodings) | `patched/` |
| 3 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/indexes/test_frozen.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `test_tricky_container` | F | `3cb512641` Remove test_before_pypi (#7937) | 커밋된 venv 설치본 (pandas) | |
| 4 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/sqlalchemy/orm/session.py` | site-packages | 〃 | `_register_altered` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (sqlalchemy) | |
| 5 | pypa/pipenv | `pipenv/vendor/pip9/req/req_uninstall.py` | vendor | `pipenv/vendor` | `__init__` | F | `46a0e793b` Delete vendored pip9 | 벤더 사본 (pip 9) | |
| 6 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pip/_vendor/requests/api.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `delete` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pip 안 requests) | |
| 7 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/core/series.py` | site-packages | 〃 | `reset_index` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 8 | pypa/pipenv | `pipenv/vendor/pip9/_vendor/urllib3/util/selectors.py` | vendor | `pipenv/vendor` | `modify` | F | `46a0e793b` Delete vendored pip9 | 벤더 사본 (pip 9 안 urllib3) | |
| 9 | pypa/pipenv | `pipenv/vendor/pathlib.py` | vendor | `pipenv/vendor` | `__str__` | F | `6c94ac41c` remove unused vendored pathlib | 벤더 사본 (pathlib 백포트) | |
| 10 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/litellm/integrations/langfuse/langfuse.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `_get_chat_content_for_langfuse` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (litellm) | |
| 11 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pip/_internal/operations/install/wheel.py` | site-packages | 〃 | `is_dir_path` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pip) | |
| 12 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/tools/test_to_datetime.py` | site-packages | 〃 | `test_to_datetime_with_space_in_series` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 13 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/numpy/polynomial/tests/test_printing.py` | site-packages | 〃 | `test_hermiteE_str` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (numpy) | |
| 14 | pypa/pipenv | `pipenv/patched/pip/_vendor/pkg_resources/tests/test_pkg_resources.py` | vendor | `pipenv/patched/pip/_vendor` | `test_get_cache_path` | F | `0d3e1f92a` Revert "updated pkg_resources" | 벤더 사본 (pip 안 pkg_resources), 벤더 갱신의 되돌림 | `patched/` |
| 15 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/io/excel/test_xlsxwriter.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `test_engine_kwargs` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 16 | pypa/pipenv | `pipenv/vendor/urllib3/response.py` | vendor | `pipenv/vendor` | `_init_length` | F | `13b023b98` Remove urllib3 from vendor, use pip._vendor | 벤더 사본 (urllib3) | |
| 17 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/numpy/lib/_user_array_impl.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `__setattr__` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (numpy) | |
| 18 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/series/methods/test_copy.py` | site-packages | 〃 | `test_copy_tzaware` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 19 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/sqlalchemy/testing/config.py` | site-packages | 〃 | `__str__` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (sqlalchemy) | |
| 20 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/multiprocess/managers.py` | site-packages | 〃 | `__iter__` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (multiprocess) | |
| 21 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/plotting/test_boxplot_method.py` | site-packages | 〃 | `test_boxplot_legacy3` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 22 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/jsonschema/validators.py` | site-packages | 〃 | `create` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (jsonschema) | |
| 23 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pydantic/v1/color.py` | site-packages | 〃 | `__eq__` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pydantic) | |
| 24 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pip/_vendor/platformdirs/__init__.py` | site-packages | 〃 | `site_data_dir` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pip 안 platformdirs) | |
| 25 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/arrays/sparse/test_indexing.py` | site-packages | 〃 | `test_getslice` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 26 | pypa/pipenv | `pipenv/patched/piptools/_vendored/pip/_vendor/pyparsing.py` | vendor | `pipenv/patched/piptools/_vendored` | `countedArray` | F | `14fabb272` Delete vendored pip from piptools | 벤더 사본 안의 벤더 사본 (pip → pyparsing) | `patched/` |
| 27 | RasaHQ/rasa | `deactivate/lib/python3.6/site-packages/pkg_resources/__init__.py` | site-packages | `deactivate/lib/python3.6/site-packages` | `check_version_conflict` | F | `c1bc17db5` fixes new function | 커밋된 venv 설치본 (setuptools 의 pkg_resources). 커밋 첫 줄은 venv 삭제를 말하지 않는다 | 커밋 첫 줄 |
| 28 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/extension/json/test_json.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `test_value_counts_with_normalize` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 29 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/dtypes/cast/test_infer_dtype.py` | site-packages | 〃 | `test_infer_dtype_from_datetime` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 30 | pypa/pipenv | `pipenv/vendor/pydantic/typing.py` | vendor | `pipenv/vendor` | `is_new_type` | F | `95df3fd64` Convert from pydantic to vanilla dataclasses (…) | 벤더 사본 (pydantic). 의존성을 빼는 결정은 프로젝트 것이지만 지워진 코드는 업스트림 사본 | |
| 31 | pypa/pipenv | `pipenv/vendor/distlib/_backport/tarfile.py` | vendor | `pipenv/vendor` | `open` | F | `c5f601668` Vendor: drop distlib - use the one from pip._vendor | 벤더 사본 (distlib) | |
| 32 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/numpy/_core/tests/test_stringdtype.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `test_cfloat_casts` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (numpy) | |
| 33 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/litellm/utils.py` | site-packages | 〃 | `process_system_message` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (litellm) | |
| 34 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/io/parsers/base_parser.py` | site-packages | 〃 | `unpack_if_single_element` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 35 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pyarrow/vendored/version.py` | site-packages | 〃 | `__lt__` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pyarrow). `vendored` 도 있지만 범주 순서상 site-packages | |
| 36 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/mako/lexer.py` | site-packages | 〃 | `exception_kwargs` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (mako) | |
| 37 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pandas/tests/generic/test_generic.py` | site-packages | 〃 | `test_flags_identity` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (pandas) | |
| 38 | pypa/pipenv | `pipenv/vendor/more_itertools/more.py` | vendor | `pipenv/vendor` | `set_partitions` | F | `952e7fb90` Removed more-itertools vendor dep. (#5047) | 벤더 사본 (more-itertools) | |
| 39 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/pkg_resources/__init__.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `clone` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (setuptools 의 pkg_resources) | |
| 40 | pypa/pipenv | `pipenv/vendor/concurrent3/futures/_base.py` | vendor | `pipenv/vendor` | `map` | F | `16ef900b0` removed vendorized concurrent3 in favor of built in package for 3.x+ | 벤더 사본 (futures 백포트) | |
| 41 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/numpy/f2py/__init__.py` | site-packages | `test_before_pypi/lib/python3.9/site-packages` | `__getattr__` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (numpy) | |
| 42 | stanfordnlp/dspy | `test_before_pypi/lib/python3.9/site-packages/numpy/lib/tests/test_function_base.py` | site-packages | 〃 | `test_basic` | F | `3cb512641` 〃 | 커밋된 venv 설치본 (numpy) | |
| 43 | pypa/pipenv | `pipenv/vendor/pipenv.patched.pip/_internal/network/cache.py` | vendor | `pipenv/vendor` | `suppressed_cache_errors` | F | `c4e80ac13` Update vendored pipdeptree to 2.30.0 (#6458) | 벤더 디렉터리 아래. 디렉터리 이름 `pipenv.patched.pip` 가 특이하다 — 벤더링 도구 산출물로 보이나 경로만으로는 무엇인지 확실치 않다 | 디렉터리 이름 |
| 44 | pypa/pipenv | `pipenv/vendor/pathlib.py` | vendor | `pipenv/vendor` | `name` | F | `6c94ac41c` remove unused vendored pathlib | 벤더 사본 (pathlib 백포트). #9 와 같은 파일·다른 함수 | |
| 45 | pypa/pipenv | `pipenv/vendor/pip9/_vendor/distlib/util.py` | vendor | `pipenv/vendor` | `__init__` | F | `46a0e793b` Delete vendored pip9 | 벤더 사본 (pip 9 안 distlib) | |
| 46 | pypa/pipenv | `pipenv/vendor/click/_compat.py` | vendor | `pipenv/vendor` | `get_text_stdout` | F | `fac3eaeaa` Revendor packages and update patches | 벤더 사본 (click), 재벤더링 | |
| 47 | RasaHQ/rasa | `deactivate/lib/python3.6/site-packages/setuptools/package_index.py` | site-packages | `deactivate/lib/python3.6/site-packages` | `url_ok` | F | `c1bc17db5` fixes new function | 커밋된 venv 설치본 (setuptools). #27 과 같은 커밋 | 커밋 첫 줄 |
| 48 | pypa/pipenv | `pipenv/patched/pip/_vendor/pkg_resources/_vendor/packaging/version.py` | vendor | `pipenv/patched/pip/_vendor` | `public` | F | `0d3e1f92a` Revert "updated pkg_resources" | 벤더 사본 (pip → pkg_resources → packaging) | `patched/` |
| 49 | plotly/dash | `venv/lib/python2.7/site-packages/pip/utils/__init__.py` | site-packages | `venv/lib/python2.7/site-packages` | `untar_file` | F | `0911675d3` rm venv | 커밋된 venv 설치본 (pip) | |
| 50 | pypa/pipenv | `pipenv/patched/notpip/_vendor/pyparsing.py` | vendor | `pipenv/patched/notpip/_vendor` | `getName` | F | `f3166e673` check point progress on only bringing in pip==22.0.4 (#4966) | 벤더 사본 안의 벤더 사본 (pip → pyparsing) | `patched/` |

〃 = 위 행과 같다.

### 요약

- 범주: site-packages 32 · vendor 18 · venv 0 · protobuf 0. 저장소: dspy 29 · pipenv 18 · rasa 2 · dash 1. 50건 모두
  FULL_FUNCTION, `is_test_code` 참 13건. 커밋은 16개 — dspy 29건이 한 커밋(`3cb512641`)이다
- 경로로 보면 **50건 모두 커밋된 venv 의 설치 패키지(`<뿌리>/lib/pythonX.Y/site-packages/`) 또는 벤더 디렉터리 안의 서드파티
  사본**으로 보인다. 경로상 프로젝트가 직접 쓴 코드로 보이는 건은 찾지 못했다. 이것은 경로·커밋 첫 줄만 본 메모이고
  정답 판정이 아니다
- 사람 검토를 권하는 건 (규칙 오탐이라고 본 것은 아니다):
  - **`patched/` 5건 (#2, 14, 26, 48, 50).** pipenv 의 `patched/` 는 pipenv 가 손댄 벤더 사본이다. 지워진 함수가 업스트림
    그대로인지, pipenv 가 고친 부분인지는 경로로 알 수 없다. 고친 부분이라면 "배울 게 있는 삭제"일 수 있다
  - **#43 `pipenv/vendor/pipenv.patched.pip/...`.** 점이 들어간 디렉터리 이름이 특이하다. 벤더 디렉터리 아래라 규칙상
    맞지만 무엇이 만든 경로인지 확인하지 않았다
  - **rasa 2건 (#27, 47).** 경로(`deactivate/lib/python3.6/site-packages/`)는 명백한 venv 인데 커밋 첫 줄이
    "fixes new function"이다. 같은 커밋에 프로젝트 코드 변경이 섞였을 수 있다 — 이 2건 자체는 설치본이다

### 한계

- **정식 재측정 미실시.** 이 50건은 §8.4 의 새 수동 200건 정밀도 재측정이 아니고, 3인 판정도 아니다
- 표본이 모집단 비중을 따라 dspy 한 커밋과 pipenv 에 몰렸다. **protobuf(528건)·venv(40건) 범주와 saleor(`vendored/`
  3,215)·airflow·ray·prefect·pandas 등의 `_vendor(ed)`·`vendor` 는 한 건도 뽑히지 않았다** — 저장소마다 벤더 디렉터리가
  프로젝트 코드와 섞일 위험은 이 표본으로 알 수 없다
- 경로·커밋 첫 줄만 봤다. diff 와 파일 머리(생성 표시·라이선스 헤더)는 보지 않았다

## 저장소별 변화 (100개, v0.8 → v0.9)

`data/assembled_v0.9/` 파일 4개를 다시 읽어 셌다. 사후 필터는 KEPT 를 `NOISE_GENERATED` 로만 바꿨으므로(이번 실행의
`noise_format_count` 0) KEPT(v0.8) = KEPT(v0.9) + `NOISE_GENERATED` 이고, 합계 1,504,913 은 사후 필터 보고서의 적용 전
KEPT 합과 같다. `NOISE_GENERATED` 가 1건 이상인 저장소 16개 전부다.

| 저장소 | KEPT (v0.8) | NOISE_GENERATED | KEPT (v0.9) |
|---|---:|---:|---:|
| stanfordnlp/dspy | 96,935 | 93,140 | 3,795 |
| pypa/pipenv | 48,411 | 41,694 | 6,717 |
| RasaHQ/rasa | 21,425 | 6,213 | 15,212 |
| plotly/dash | 8,606 | 4,492 | 4,114 |
| saleor/saleor | 24,335 | 3,215 | 21,120 |
| PrefectHQ/prefect | 17,405 | 785 | 16,620 |
| apache/airflow | 53,673 | 640 | 53,033 |
| ray-project/ray | 67,309 | 365 | 66,944 |
| pandas-dev/pandas | 59,333 | 139 | 59,194 |
| exo-explore/exo | 10,780 | 89 | 10,691 |
| ManimCommunity/manim | 19,127 | 27 | 19,100 |
| huggingface/lerobot | 5,796 | 25 | 5,771 |
| PrefectHQ/fastmcp | 8,304 | 15 | 8,289 |
| sgl-project/sglang | 41,005 | 14 | 40,991 |
| modelscope/FunASR | 8,147 | 4 | 8,143 |
| NVIDIA-NeMo/Speech | 30,667 | 4 | 30,663 |
| **전체 (100개)** | **1,504,913** | **150,861** | **1,354,052** |
