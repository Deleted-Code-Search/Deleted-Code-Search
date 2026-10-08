# #90 중간 점검 재판정 대상 — 라벨러별 `record_id`

- 작성: 2026-10-08, 성제(sj). 목록은 `classify/labels.py`의 `merge_labels()`·`agreement_of()`로 뽑았고 Claude Code가 돕고 사람이 검토한다 (#162)
- 근거: 라벨 가이드 v3 §8.4.1.1, `docs/evaluation.md` "500건 라벨링 v3 사전 등록", 불일치 분석 `docs/reports/interim_90_disagreements.md`(PR #161)
- 입력 — #161과 같은 파일이다 (sha256 앞 16자리)

| 파일 | sha256 앞 16자리 |
|---|---|
| `datasets/labels/sj_main500.jsonl` | `5f71a37677f807d6` |
| `datasets/labels/jh_main500.jsonl` | `0a13d52843679bc1` |
| `datasets/labels/hs_main500.jsonl` | `20aeae9e70c677ee` |
| `datasets/labels/main500_records.jsonl` | `db6bc77f93b8df4d` |
| `datasets/labels/main500_assignment.jsonl` | `b7ed920a06588216` |

## 무엇을 하나

블록별 처음 50건(배분 파일 `interim = true`) 150건 중 **두 라벨의 이유 또는 등급이 갈린 69건**(A 21 · B 21 · C 27)을, 그 블록의 두 라벨러가 **v3 기준으로 각자 다시 판정한다.** 두 라벨이 일치한 81건은 그대로 둔다.

| 라벨러 | 재판정 건수 | 블록 |
|---|---:|---|
| sj | **48** | A 21 · C 27 |
| jh | **42** | A 21 · B 21 |
| hs | **48** | B 21 · C 27 |
| 계 | 138 라벨 (69건 × 2인) | |

재판정 뒤에도 갈린 건은 제3자가 두 라벨 중 하나를 고른다 — A → hs, B → sj, C → jh (가이드 §8.3 v3).

## 지킬 것

1. **서로의 이전 라벨을 보지 않는다.** 상대 라벨 파일을 열지 않는다 (가이드 §2.2).
2. **#161 문서의 부록 A, §3 표, §7 대표 사례, §9.3 건별 답을 재판정이 끝날 때까지 열지 않는다.** 두 사람의 이전 라벨과 규칙을 적용한 답이 나란히 있다. 이미 읽었으면 그 사실은 되돌릴 수 없다 — 그래서 이 69건으로 kappa를 재지 않는다 (가이드 §8.4.1.1 4번).
3. **화면만 보고 판정한다** (v3 규칙 5). AI 보조를 쓰면 `docs/labeling_ai_prompt.md`에 label_cli 화면만 넣는다 — 이전 라벨을 넣지 않는다.
4. 이 목록에는 **어느 쪽이 갈렸는지(이유인지 등급인지)와 이전 라벨을 일부러 적지 않았다.** 그것을 알면 짝의 라벨을 짐작할 수 있다.
5. 재판정 라벨은 `guide_version` `v3`로 저장된다 (`classify/sampling.py` `GUIDE_VERSION`).

## 시작 전에 정해져야 하는 것

재판정은 아래가 정해지기 전에는 시작하지 않는다 (가이드 "v3 적용 전 선행 조건").

| 선행 조건 | 지금 상태 |
|---|---|
| 6 — 재판정 행을 어떻게 보존·식별하나 | **미정.** 같은 `record_id`·`labeler`에 두 번째 라벨이 생긴다. 제자리 덮어쓰기면 v2 라벨이 사라지고, 덧붙이면 `pair_labels()`가 그 레코드를 kappa에서 조용히 뺀다 |
| 9 — label_cli로 특정 `record_id`를 다시 연다 | **지금은 안 된다.** 아래 "label_cli 확인 결과" |
| 12 — v2 라벨 원본 보존 | **라벨 파일이 아직 커밋 전이다.** 재판정 전에 지금 파일(위 sha256)을 보존해야 v2 점검 수치와 H(ADR-016)의 입력을 다시 낼 수 있다 |

### label_cli 확인 결과 (2026-10-08, `tools/label_cli.py` 읽고 확인, 구현하지 않음)

| 질문 | 답 | 근거 |
|---|---|---|
| 특정 `record_id`를 다시 열어 고칠 수 있나 | **아니오.** `record_id`를 받는 인자가 없다. 다시 여는 길은 `:u`(직전 건 수정)뿐이고, 이번 실행에서 저장한 순서를 거슬러 가다가 그것이 바닥나면 파일에서 `labeled_at`이 가장 늦은 줄을 연다. 과거의 임의 건으로 갈 수 없다 | `LabelSession.previous_index()`, `build_parser()` |
| 이어하기가 "파일에서 첫 빈 라벨부터"인가 | **예.** 파일 줄 순서로 `reason_label`·`evidence_grade` 중 하나라도 비어 있는 첫 줄부터 연다. 블록 순서나 `block_position`이 아니라 **파일 순서**다 | `LabelFile.next_unlabeled()`, `classify.labels.is_filled()` |
| 라벨 칸을 비운 레코드를 다시 띄우나 | **예.** 두 칸 중 하나를 비우면 `is_filled()`가 거짓이라 빈 줄로 보고 다시 띄운다. 파일 앞쪽(1~50번 구간)에 있으므로 51번보다 먼저 뜬다. 다만 손으로 비우면 그 줄의 v2 라벨이 지워지고(선행 조건 12), 남은 칸(`evidence_text`·`note` 등)은 다시 저장할 때 덮어써진다 | `is_filled()`, `LabelSession.save()` |

라벨 파일 줄 순서는 sj `A1~50 · C1~50 · A51~167 · C51~166`, jh `A1~50 · B1~50 · A51~167 · B51~167`, hs `B1~50 · C1~50 · B51~167 · C51~166`이다. 그래서 각자 **앞 블록의 51~167번을 다 끝내야 뒤 블록의 51번이 열린다** (가이드 §8.4.1.1 "순서 주의").

### 이미 51번을 찍은 건 1건

jh가 블록 A 51번(`102a3083-262e-523f-9edd-2a317dc42666`)을 v3 전에 v2로 저장했다. 재판정 대상이 아니고 고치지 않는다 (가이드 §8.4.2). 그 쌍은 §8.4.2.1의 `혼재`라 v3 점검에서 빠진다 — 블록 A v3 점검 분모는 최대 49쌍이다.

## 참고 — 집계용 숫자 (라벨러는 읽지 않아도 된다)

- 69건의 split: train 43 · val 9 · test 17. 테스트 100건 중 1~50번이 34건이고 그중 17건이 여기 있다 — H(ADR-016)에서 이 17건의 독립 라벨은 v3 재판정 라벨이다 (`docs/evaluation.md` "H와의 관계")
- 일치해서 유지하는 81건: A 29 · B 29 · C 23

---

## sj — 48건 (A 21 · C 27)

### 블록 A (짝: jh) — 21건

| 건 | `record_id` | 저장소 | 파일 | 함수 |
|---|---|---|---|---|
| A01 | `92620e7f-e463-555a-8a31-d9ed0311d2c5` | apache/superset | `tests/unit_tests/mcp_service/sql_lab/test_sql_lab_utils.py` | `test_adds_limit_to_select` |
| A03 | `46aa2a72-b241-5189-9d6f-c6573329657c` | huggingface/transformers | `src/transformers/models/vilt/modeling_vilt.py` | `custom_forward` |
| A05 | `e01eaafa-d05a-51d3-9e59-898dc88baf9c` | docling-project/docling | `docling/backend/pypdfium2_backend.py` | `get_segmented_page` |
| A07 | `02aa646a-bea8-56b0-8955-f66c49c256f6` | docling-project/docling | `tests/test_rapid_ocr_model.py` | `test_rapidocr_default_models_use_current_default_assets` |
| A08 | `511c845c-43b8-5bf8-9045-9214ed25d5f8` | docling-project/docling | `docling/utils/layout_utils.py` | `contains` |
| A13 | `1edfec7b-c069-579a-ac36-0ff746fc540c` | encode/httpx | `tests/test_urlparse.py` | `test_urlparse_invalid_ipv6` |
| A16 | `6b8771dd-8114-57cb-acee-0ab85c58a59a` | scikit-learn/scikit-learn | `sklearn/ensemble/_hist_gradient_boosting/tests/test_gradient_boosting.py` | `test_invalid_classification_loss` |
| A18 | `d276677d-cea7-50ff-93ad-4cd4990d678d` | commaai/openpilot | `selfdrive/loggerd/tests/loggerd_tests_common.py` | `get` |
| A19 | `69cc961f-88ac-5bd4-8320-a1b29e95a244` | huggingface/transformers | `tests/test_hf_api.py` | `test_end_to_end_thresh_16M` |
| A24 | `03049cc5-6b9c-5366-9f3b-92d32a127c30` | django/django | `tests/forms_tests/tests/test_widgets.py` | `__init__` |
| A26 | `b9993312-7a83-5c18-80b5-0b597be8a623` | django/django | `django/utils/six.py` | `itervalues` |
| A28 | `cd719847-bd4c-5949-9955-ec596b1a2b9f` | apache/superset | `superset/mcp_service/task/schemas.py` | `parse_filters` |
| A29 | `53c0d77f-5e10-5056-8912-feceaea49f0f` | unclecode/crawl4ai | `crawl4ai/async_crawler_strategy.current.py` | `get_delayed_content` |
| A33 | `e94df97b-880d-50a9-8c38-c21703b7dc2b` | commaai/openpilot | `panda/tests/safety/test_toyota.py` | `test_disable_control_allowed_from_cruise` |
| A34 | `dbee26cc-3540-5c5f-838e-73c0d5a5c7b0` | home-assistant/core | `homeassistant/components/aladdin_connect/api.py` | `__init__` |
| A36 | `d3fcea70-6a07-59db-b4c1-a911036acdfd` | vllm-project/vllm | `vllm/model_executor/models/internvl.py` | `get_num_mm_connector_tokens` |
| A37 | `ccbdb405-7ee7-50a5-b053-b7d725a9c3cf` | crewAIInc/crewAI | `lib/crewai/src/crewai/flow/runtime/__init__.py` | `__add__` |
| A40 | `362bda69-4859-5670-9da9-02d4ffbde30a` | crewAIInc/crewAI | `src/crewai/knowledge/storage/knowledge_storage.py` | `_set_embedder_config` |
| A45 | `63f216a4-1245-5cbf-bc78-6c86eb887602` | apache/superset | `superset/views/dashboard/api.py` | `pre_load` |
| A46 | `891efad6-aea8-5d41-ac02-f00f82f12616` | browser-use/browser-use | `browser_use/browser/navigation_watchdog.py` | `_switch_agent_focus_to_tab` |
| A50 | `c3012ee0-ad49-5449-9f9c-b16bd8917142` | docling-project/docling | `docling/backend/docling_parse_v4_backend.py` | `_ensure_parsed` |

### 블록 C (짝: hs) — 27건

| 건 | `record_id` | 저장소 | 파일 | 함수 |
|---|---|---|---|---|
| C01 | `88487d19-ed93-56f7-b244-2e7db017d551` | huggingface/transformers | `examples/research_projects/lxmert/modeling_frcnn.py` | `_get_ground_truth` |
| C02 | `37b96414-6822-53c7-9457-dbc97e4a2136` | django/django | `tests/file_storage/models.py` | `get_available_name` |
| C04 | `ef5c8a6d-811d-5582-a8d3-fa33f54eaef6` | vllm-project/vllm | `vllm/model_executor/layers/quantization/kernels/mixed_precision/bitblas.py` | `_configure_bitblas_matmul` |
| C06 | `bf5e99fd-db33-5454-9059-47c807af5bc2` | docling-project/docling | `docling/pipeline/vlm_pipeline.py` | `count_right` |
| C08 | `3f525d74-a5d7-58f7-943f-53f6b3ddb737` | langchain-ai/langchain | `libs/experimental/tests/unit_tests/test_data_anonymizer.py` | `test_anonymize_allow_list` |
| C09 | `49a82b90-64e8-56b7-959a-51716b4d4488` | psf/requests | `requests/packages/chardet/hebrewprober.py` | `__init__` |
| C11 | `f7fffa0c-761f-5866-a07c-f937fedad4a8` | crewAIInc/crewAI | `src/crewai/crew.py` | `_add_code_execution_tools` |
| C13 | `7ff061a5-7b2a-5e67-9d85-7d1fb3ddb737` | crewAIInc/crewAI | `lib/crewai/src/crewai/utilities/pydantic_schema_parser.py` | `_format_union_type` |
| C14 | `22e7efcb-5b23-5ced-9b6a-fe9ec792623a` | pydantic/pydantic | `tests/test_config.py` | `test_sub_model_merge` |
| C15 | `be37a8aa-a254-58ff-9fbe-3b2877f78bd2` | pydantic/pydantic | `setup.py` | `replace_users` |
| C16 | `628884b6-d4ab-5872-9e5c-6a5f21af81c5` | mem0ai/mem0 | `evaluation/src/langmem.py` | `__init__` |
| C17 | `f41659a5-816f-556d-bc4f-6ac3d29a042b` | crewAIInc/crewAI | `src/crewai/agents/crew_agent_executor.py` | `_summarize_messages` |
| C18 | `0ce8735f-68bf-59d0-9d9c-cf01ed612299` | django/django | `django/core/serializers/xml_serializer.py` | `fast_cache_clearing` |
| C19 | `4689a9e6-047b-5c0e-b98f-3df61ab4dde7` | langchain-ai/langchain | `libs/community/langchain_community/vectorstores/epsilla.py` | `embeddings` |
| C20 | `8fdf755e-9206-5072-9c01-bd2ea6992234` | encode/httpx | `httpx/_compat.py` | `set_minimum_tls_version_1_2` |
| C24 | `b7d85e43-0fc2-5cf9-8d2d-ff424b1c47e6` | psf/requests | `requests/packages/urllib3/util/retry.py` | `__repr__` |
| C25 | `63e29cbe-cc02-584d-a2bc-f287bf374ecd` | browser-use/browser-use | `tests/ci/test_rust_agent.py` | `fake_load_events` |
| C28 | `4a96d1f7-b9cc-5540-ab07-bd6495721e1d` | langchain-ai/langchain | `libs/partners/ai21/langchain_ai21/semantic_text_splitter.py` | `_merge_splits_no_seperator` |
| C29 | `0d84dee2-581e-549f-9789-3c0b1d9c20a1` | commaai/openpilot | `panda/tests/elm_wifi.py` | `test_elm_protocol_autodetect_ISO14230_KWP_FAST` |
| C30 | `d4707de0-23a6-5680-be18-06631886e791` | apache/superset | `superset/views/core.py` | `get_raw_results` |
| C32 | `7cebafb4-c9b6-540d-b94f-b592f89a0e4b` | apache/superset | `tests/unit_tests/migrations/shared/utils_test.py` | `test_extract_table_references` |
| C33 | `7fe496f2-4f01-50eb-8f0e-4de0f3ad2dd4` | unclecode/crawl4ai | `crawl4ai/async_crawler_strategy.back.py` | `set_custom_headers` |
| C35 | `6711d4ca-1f83-5be3-8e16-6ff9fb32c4e1` | home-assistant/core | `tests/components/update/test_init.py` | `test_skip_non_existing_update` |
| C36 | `0eda74ce-6286-5bef-afb1-5348187a7d50` | browser-use/browser-use | `tests/ci/test_rust_agent.py` | `on_step_start` |
| C44 | `5239a2c7-e2eb-55e3-adbb-950467aceabf` | scikit-learn/scikit-learn | `examples/applications/svm_gui.py` | `add_example` |
| C45 | `6ae90852-f144-56d2-b858-8997bb5609f0` | docling-project/docling | `docs/examples/service_client/task_api.py` | `_client` |
| C48 | `50dbd3a2-9bb5-5906-a39f-62f9249d0159` | langchain-ai/langchain | `libs/community/tests/unit_tests/chat_models/test_hunyuan.py` | `test__convert_dict_to_message_human` |

## jh — 42건 (A 21 · B 21)

### 블록 A (짝: sj) — 21건

| 건 | `record_id` | 저장소 | 파일 | 함수 |
|---|---|---|---|---|
| A01 | `92620e7f-e463-555a-8a31-d9ed0311d2c5` | apache/superset | `tests/unit_tests/mcp_service/sql_lab/test_sql_lab_utils.py` | `test_adds_limit_to_select` |
| A03 | `46aa2a72-b241-5189-9d6f-c6573329657c` | huggingface/transformers | `src/transformers/models/vilt/modeling_vilt.py` | `custom_forward` |
| A05 | `e01eaafa-d05a-51d3-9e59-898dc88baf9c` | docling-project/docling | `docling/backend/pypdfium2_backend.py` | `get_segmented_page` |
| A07 | `02aa646a-bea8-56b0-8955-f66c49c256f6` | docling-project/docling | `tests/test_rapid_ocr_model.py` | `test_rapidocr_default_models_use_current_default_assets` |
| A08 | `511c845c-43b8-5bf8-9045-9214ed25d5f8` | docling-project/docling | `docling/utils/layout_utils.py` | `contains` |
| A13 | `1edfec7b-c069-579a-ac36-0ff746fc540c` | encode/httpx | `tests/test_urlparse.py` | `test_urlparse_invalid_ipv6` |
| A16 | `6b8771dd-8114-57cb-acee-0ab85c58a59a` | scikit-learn/scikit-learn | `sklearn/ensemble/_hist_gradient_boosting/tests/test_gradient_boosting.py` | `test_invalid_classification_loss` |
| A18 | `d276677d-cea7-50ff-93ad-4cd4990d678d` | commaai/openpilot | `selfdrive/loggerd/tests/loggerd_tests_common.py` | `get` |
| A19 | `69cc961f-88ac-5bd4-8320-a1b29e95a244` | huggingface/transformers | `tests/test_hf_api.py` | `test_end_to_end_thresh_16M` |
| A24 | `03049cc5-6b9c-5366-9f3b-92d32a127c30` | django/django | `tests/forms_tests/tests/test_widgets.py` | `__init__` |
| A26 | `b9993312-7a83-5c18-80b5-0b597be8a623` | django/django | `django/utils/six.py` | `itervalues` |
| A28 | `cd719847-bd4c-5949-9955-ec596b1a2b9f` | apache/superset | `superset/mcp_service/task/schemas.py` | `parse_filters` |
| A29 | `53c0d77f-5e10-5056-8912-feceaea49f0f` | unclecode/crawl4ai | `crawl4ai/async_crawler_strategy.current.py` | `get_delayed_content` |
| A33 | `e94df97b-880d-50a9-8c38-c21703b7dc2b` | commaai/openpilot | `panda/tests/safety/test_toyota.py` | `test_disable_control_allowed_from_cruise` |
| A34 | `dbee26cc-3540-5c5f-838e-73c0d5a5c7b0` | home-assistant/core | `homeassistant/components/aladdin_connect/api.py` | `__init__` |
| A36 | `d3fcea70-6a07-59db-b4c1-a911036acdfd` | vllm-project/vllm | `vllm/model_executor/models/internvl.py` | `get_num_mm_connector_tokens` |
| A37 | `ccbdb405-7ee7-50a5-b053-b7d725a9c3cf` | crewAIInc/crewAI | `lib/crewai/src/crewai/flow/runtime/__init__.py` | `__add__` |
| A40 | `362bda69-4859-5670-9da9-02d4ffbde30a` | crewAIInc/crewAI | `src/crewai/knowledge/storage/knowledge_storage.py` | `_set_embedder_config` |
| A45 | `63f216a4-1245-5cbf-bc78-6c86eb887602` | apache/superset | `superset/views/dashboard/api.py` | `pre_load` |
| A46 | `891efad6-aea8-5d41-ac02-f00f82f12616` | browser-use/browser-use | `browser_use/browser/navigation_watchdog.py` | `_switch_agent_focus_to_tab` |
| A50 | `c3012ee0-ad49-5449-9f9c-b16bd8917142` | docling-project/docling | `docling/backend/docling_parse_v4_backend.py` | `_ensure_parsed` |

### 블록 B (짝: hs) — 21건

| 건 | `record_id` | 저장소 | 파일 | 함수 |
|---|---|---|---|---|
| B05 | `de2ada3d-8cc1-56a9-8216-3debab9bb33d` | vllm-project/vllm | `tests/v1/worker/test_gpu_worker.py` | `test_reserve_mm_ipc_gpu_memory_scales_pynvvideocodec_budget_by_api_servers` |
| B07 | `5ddcf3b6-0033-5964-8ce9-168b8170a821` | crewAIInc/crewAI | `lib/crewai/tests/test_flow_serializer.py` | `handle_cancelled` |
| B08 | `a48582a5-1d09-50ab-afe0-1f211d3a2490` | home-assistant/core | `homeassistant/helpers/entity_component.py` | `reset` |
| B11 | `d7179e0e-b4b4-5779-ba4a-42792204c657` | langchain-ai/langchain | `libs/partners/cohere/tests/unit_tests/test_chat_models.py` | `test_default_params` |
| B12 | `8c31ff4d-395a-5609-a797-d9991f628adc` | browser-use/browser-use | `tests/test_save_conversation.py` | `test_save_conversation_deep_directory` |
| B16 | `fa436235-dc94-5f4a-8186-5adcbbee95ba` | vllm-project/vllm | `tests/entrypoints/openai/test_chat.py` | `test_http_chat_no_model_name_with_curl` |
| B17 | `29757a36-47f9-5333-b026-995ea6989085` | scikit-learn/scikit-learn | `sklearn/tests/test_base.py` | `test_get_params_deprecated` |
| B18 | `f1fa77c0-5299-5bd2-8c61-998a8a36ad4b` | mem0ai/mem0 | `embedchain/tests/loaders/test_google_drive.py` | `google_drive_folder_loader` |
| B20 | `0442196f-fcb2-5756-afa6-ecd23d2731ab` | django/django | `django/db/backends/oracle/operations.py` | `_get_trigger_name` |
| B24 | `77ad03ed-43b4-5130-98b4-84772f14e3ef` | unclecode/crawl4ai | `tests/proxy/test_chanel_basic.py` | `crawl_chanel` |
| B26 | `780ee554-a555-5eaa-9d45-1dfae6c3e901` | pandas-dev/pandas | `pandas/tests/indexes/numeric/test_numeric.py` | `test_constructor_32bit` |
| B27 | `7134a699-0999-5274-af3d-307e9864046f` | psf/requests | `requests/packages/urllib3/util/selectors.py` | `__init__` |
| B29 | `f49f9e0d-892d-5ac0-ab02-ddb3d37bffe9` | docling-project/docling | `docling/backend/msword_backend.py` | `group_cell_elements` |
| B32 | `6821b0a1-4caf-5228-bca8-eac5537834ae` | psf/requests | `requests/packages/urllib3/packages/ordered_dict.py` | `__delitem__` |
| B37 | `0381af80-36d6-541c-88e5-c6d425b45564` | keras-team/keras | `keras/src/backend/torch/ops/nn.py` | `_unique_padded` |
| B39 | `5237adf3-aad9-5424-a94b-d33548082b4e` | commaai/openpilot | `panda/python/__init__.py` | `set_ir_power` |
| B40 | `dc781737-ff36-50e2-8ac2-5f4bf12d4a0a` | langchain-ai/langchain | `libs/community/tests/unit_tests/vectorstores/test_faiss.py` | `test_faiss_with_metadatas_and_filter` |
| B43 | `d45dc8ad-b13f-599a-aeea-34cad3cd215a` | mem0ai/mem0 | `embedchain/tests/memory/test_memory_messages.py` | `test_ec_base_message` |
| B45 | `2aa0be25-4af8-55a0-9bba-2cb3051a0d88` | home-assistant/core | `homeassistant/components/openuv/__init__.py` | `async_update` |
| B47 | `dd40d5c2-517d-5750-ae19-a00f7d5aa2be` | celery/celery | `celery/schedules.py` | `__ne__` |
| B50 | `109b8b87-f103-5c0f-9179-bde4f6106c0d` | huggingface/transformers | `src/transformers/models/mvp/modeling_mvp.py` | `custom_forward` |

## hs — 48건 (B 21 · C 27)

### 블록 B (짝: jh) — 21건

| 건 | `record_id` | 저장소 | 파일 | 함수 |
|---|---|---|---|---|
| B05 | `de2ada3d-8cc1-56a9-8216-3debab9bb33d` | vllm-project/vllm | `tests/v1/worker/test_gpu_worker.py` | `test_reserve_mm_ipc_gpu_memory_scales_pynvvideocodec_budget_by_api_servers` |
| B07 | `5ddcf3b6-0033-5964-8ce9-168b8170a821` | crewAIInc/crewAI | `lib/crewai/tests/test_flow_serializer.py` | `handle_cancelled` |
| B08 | `a48582a5-1d09-50ab-afe0-1f211d3a2490` | home-assistant/core | `homeassistant/helpers/entity_component.py` | `reset` |
| B11 | `d7179e0e-b4b4-5779-ba4a-42792204c657` | langchain-ai/langchain | `libs/partners/cohere/tests/unit_tests/test_chat_models.py` | `test_default_params` |
| B12 | `8c31ff4d-395a-5609-a797-d9991f628adc` | browser-use/browser-use | `tests/test_save_conversation.py` | `test_save_conversation_deep_directory` |
| B16 | `fa436235-dc94-5f4a-8186-5adcbbee95ba` | vllm-project/vllm | `tests/entrypoints/openai/test_chat.py` | `test_http_chat_no_model_name_with_curl` |
| B17 | `29757a36-47f9-5333-b026-995ea6989085` | scikit-learn/scikit-learn | `sklearn/tests/test_base.py` | `test_get_params_deprecated` |
| B18 | `f1fa77c0-5299-5bd2-8c61-998a8a36ad4b` | mem0ai/mem0 | `embedchain/tests/loaders/test_google_drive.py` | `google_drive_folder_loader` |
| B20 | `0442196f-fcb2-5756-afa6-ecd23d2731ab` | django/django | `django/db/backends/oracle/operations.py` | `_get_trigger_name` |
| B24 | `77ad03ed-43b4-5130-98b4-84772f14e3ef` | unclecode/crawl4ai | `tests/proxy/test_chanel_basic.py` | `crawl_chanel` |
| B26 | `780ee554-a555-5eaa-9d45-1dfae6c3e901` | pandas-dev/pandas | `pandas/tests/indexes/numeric/test_numeric.py` | `test_constructor_32bit` |
| B27 | `7134a699-0999-5274-af3d-307e9864046f` | psf/requests | `requests/packages/urllib3/util/selectors.py` | `__init__` |
| B29 | `f49f9e0d-892d-5ac0-ab02-ddb3d37bffe9` | docling-project/docling | `docling/backend/msword_backend.py` | `group_cell_elements` |
| B32 | `6821b0a1-4caf-5228-bca8-eac5537834ae` | psf/requests | `requests/packages/urllib3/packages/ordered_dict.py` | `__delitem__` |
| B37 | `0381af80-36d6-541c-88e5-c6d425b45564` | keras-team/keras | `keras/src/backend/torch/ops/nn.py` | `_unique_padded` |
| B39 | `5237adf3-aad9-5424-a94b-d33548082b4e` | commaai/openpilot | `panda/python/__init__.py` | `set_ir_power` |
| B40 | `dc781737-ff36-50e2-8ac2-5f4bf12d4a0a` | langchain-ai/langchain | `libs/community/tests/unit_tests/vectorstores/test_faiss.py` | `test_faiss_with_metadatas_and_filter` |
| B43 | `d45dc8ad-b13f-599a-aeea-34cad3cd215a` | mem0ai/mem0 | `embedchain/tests/memory/test_memory_messages.py` | `test_ec_base_message` |
| B45 | `2aa0be25-4af8-55a0-9bba-2cb3051a0d88` | home-assistant/core | `homeassistant/components/openuv/__init__.py` | `async_update` |
| B47 | `dd40d5c2-517d-5750-ae19-a00f7d5aa2be` | celery/celery | `celery/schedules.py` | `__ne__` |
| B50 | `109b8b87-f103-5c0f-9179-bde4f6106c0d` | huggingface/transformers | `src/transformers/models/mvp/modeling_mvp.py` | `custom_forward` |

### 블록 C (짝: sj) — 27건

| 건 | `record_id` | 저장소 | 파일 | 함수 |
|---|---|---|---|---|
| C01 | `88487d19-ed93-56f7-b244-2e7db017d551` | huggingface/transformers | `examples/research_projects/lxmert/modeling_frcnn.py` | `_get_ground_truth` |
| C02 | `37b96414-6822-53c7-9457-dbc97e4a2136` | django/django | `tests/file_storage/models.py` | `get_available_name` |
| C04 | `ef5c8a6d-811d-5582-a8d3-fa33f54eaef6` | vllm-project/vllm | `vllm/model_executor/layers/quantization/kernels/mixed_precision/bitblas.py` | `_configure_bitblas_matmul` |
| C06 | `bf5e99fd-db33-5454-9059-47c807af5bc2` | docling-project/docling | `docling/pipeline/vlm_pipeline.py` | `count_right` |
| C08 | `3f525d74-a5d7-58f7-943f-53f6b3ddb737` | langchain-ai/langchain | `libs/experimental/tests/unit_tests/test_data_anonymizer.py` | `test_anonymize_allow_list` |
| C09 | `49a82b90-64e8-56b7-959a-51716b4d4488` | psf/requests | `requests/packages/chardet/hebrewprober.py` | `__init__` |
| C11 | `f7fffa0c-761f-5866-a07c-f937fedad4a8` | crewAIInc/crewAI | `src/crewai/crew.py` | `_add_code_execution_tools` |
| C13 | `7ff061a5-7b2a-5e67-9d85-7d1fb3ddb737` | crewAIInc/crewAI | `lib/crewai/src/crewai/utilities/pydantic_schema_parser.py` | `_format_union_type` |
| C14 | `22e7efcb-5b23-5ced-9b6a-fe9ec792623a` | pydantic/pydantic | `tests/test_config.py` | `test_sub_model_merge` |
| C15 | `be37a8aa-a254-58ff-9fbe-3b2877f78bd2` | pydantic/pydantic | `setup.py` | `replace_users` |
| C16 | `628884b6-d4ab-5872-9e5c-6a5f21af81c5` | mem0ai/mem0 | `evaluation/src/langmem.py` | `__init__` |
| C17 | `f41659a5-816f-556d-bc4f-6ac3d29a042b` | crewAIInc/crewAI | `src/crewai/agents/crew_agent_executor.py` | `_summarize_messages` |
| C18 | `0ce8735f-68bf-59d0-9d9c-cf01ed612299` | django/django | `django/core/serializers/xml_serializer.py` | `fast_cache_clearing` |
| C19 | `4689a9e6-047b-5c0e-b98f-3df61ab4dde7` | langchain-ai/langchain | `libs/community/langchain_community/vectorstores/epsilla.py` | `embeddings` |
| C20 | `8fdf755e-9206-5072-9c01-bd2ea6992234` | encode/httpx | `httpx/_compat.py` | `set_minimum_tls_version_1_2` |
| C24 | `b7d85e43-0fc2-5cf9-8d2d-ff424b1c47e6` | psf/requests | `requests/packages/urllib3/util/retry.py` | `__repr__` |
| C25 | `63e29cbe-cc02-584d-a2bc-f287bf374ecd` | browser-use/browser-use | `tests/ci/test_rust_agent.py` | `fake_load_events` |
| C28 | `4a96d1f7-b9cc-5540-ab07-bd6495721e1d` | langchain-ai/langchain | `libs/partners/ai21/langchain_ai21/semantic_text_splitter.py` | `_merge_splits_no_seperator` |
| C29 | `0d84dee2-581e-549f-9789-3c0b1d9c20a1` | commaai/openpilot | `panda/tests/elm_wifi.py` | `test_elm_protocol_autodetect_ISO14230_KWP_FAST` |
| C30 | `d4707de0-23a6-5680-be18-06631886e791` | apache/superset | `superset/views/core.py` | `get_raw_results` |
| C32 | `7cebafb4-c9b6-540d-b94f-b592f89a0e4b` | apache/superset | `tests/unit_tests/migrations/shared/utils_test.py` | `test_extract_table_references` |
| C33 | `7fe496f2-4f01-50eb-8f0e-4de0f3ad2dd4` | unclecode/crawl4ai | `crawl4ai/async_crawler_strategy.back.py` | `set_custom_headers` |
| C35 | `6711d4ca-1f83-5be3-8e16-6ff9fb32c4e1` | home-assistant/core | `tests/components/update/test_init.py` | `test_skip_non_existing_update` |
| C36 | `0eda74ce-6286-5bef-afb1-5348187a7d50` | browser-use/browser-use | `tests/ci/test_rust_agent.py` | `on_step_start` |
| C44 | `5239a2c7-e2eb-55e3-adbb-950467aceabf` | scikit-learn/scikit-learn | `examples/applications/svm_gui.py` | `add_example` |
| C45 | `6ae90852-f144-56d2-b858-8997bb5609f0` | docling-project/docling | `docs/examples/service_client/task_api.py` | `_client` |
| C48 | `50dbd3a2-9bb5-5906-a39f-62f9249d0159` | langchain-ai/langchain | `libs/community/tests/unit_tests/chat_models/test_hunyuan.py` | `test__convert_dict_to_message_human` |
