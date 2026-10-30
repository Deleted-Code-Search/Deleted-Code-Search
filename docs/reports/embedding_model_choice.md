# 임베딩 모델 선택 (#8)

검색 2단계(의미 검색, #193)에 쓸 임베딩 모델을 고른다. CHARTER §17 "임베딩 모델 선택 (CPU 실행 가능한 소형 모델 우선)". 결정은 [ADR-023](../adr/023-semantic-search-numpy-bge-small.md).

- 실행: `python -m eval.embedding_bench --max-tokens 128 256` → `docs/reports/embedding_model_choice.json`
- 환경: 이 노트북(Windows 11, CPU 16 스레드, 메모리 23 GB, GPU 없음), fastembed 0.9.1 + ONNX Runtime. torch·pyarrow 는 쓰지 않는다 (스마트 앱 컨트롤)
- 입력 텍스트는 실제 생성과 같다 (`search.embed.doc_text`): `function_name` + `file_path` + `deleted_body`, 모델 토큰 상한에서 자른다

## 결론

**`BAAI/bge-small-en-v1.5`, 토큰 상한 256.** 384차원, 67 MB, MIT. 72만 건 예상 3~5.5시간 (8시간 이내).

## 표본

- **질의 표본 50건**: Release `dataset-v0.9` 에서 저장소 25개(시드 20261030)를 고르고, 저장소마다 본문 있는 FULL_FUNCTION 2건(저수지 표집).
- **질의 10개**: 50건을 읽은 뒤, 그중 한 건씩을 떠올리며 이름·경로 낱말을 되도록 피해 자연어로 썼다 (`eval/embedding_bench.py` `QUERIES`). "의도한 함수의 순위"(MRR·1위 수)는 **참고 지표**다 — 질의를 쓴 사람이 정답을 알고 쓴 known-item 과제라 쉽고, 50건 중에서 고르는 것이라 변별력이 낮다. 정답 라벨로 쓰지 않는다.
- **처리량 표본 1,000건**: 같은 저장소 25개에서 40건씩(LINK_ONLY 포함). 실제 생성과 같은 길이순 묶음으로 한 번 임베딩한 시간 ÷ 1,000.

## 비교표

| 모델 | 토큰 상한 | 차원 | 크기 | 라이선스 (재배포) | CPU 시간/함수 | 72만 건 예상 | 의도한 함수 1위 / 5위 안 (10개 중) | MRR (참고) | 모델 로드 |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| jinaai/jina-embeddings-v2-base-code | 128 | 768 | 644 MB | Apache-2.0 (가능) | 49.1 ms | **9.9 h** | 9 / 10 | 0.95 | 1.1 s |
| jinaai/jina-embeddings-v2-base-code | 256 | 768 | 644 MB | Apache-2.0 (가능) | 79.2 ms | **15.9 h** | 9 / 10 | 0.95 | 0.7 s |
| ibm-granite/granite-embedding-small-english-r2 | 128 | 384 | 197 MB | Apache-2.0 (가능) | 15.2 ms | 3.0 h | 8 / 8 | 0.82 | 0.3 s |
| ibm-granite/granite-embedding-small-english-r2 | 256 | 384 | 197 MB | Apache-2.0 (가능) | 26.1 ms | 5.2 h | 8 / 8 | 0.82 | 0.3 s |
| BAAI/bge-small-en-v1.5 | 128 | 384 | 67 MB | MIT (가능) | 15.4 ms | 3.1 h | 9 / 10 | 0.93 | 0.2 s |
| **BAAI/bge-small-en-v1.5** | **256** | 384 | 67 MB | MIT (가능) | 26.5 ms | **5.3 h** | **10 / 10** | **1.00** | 0.2 s |

- 크기는 fastembed 가 받은 ONNX 파일 합계다. bge-small 은 fastembed 가 양자화본(`Qdrant/bge-small-en-v1.5-onnx-Q`)을 받는다.
- 라이선스는 fastembed 목록(`TextEmbedding.list_supported_models()`)의 값이다. 우리는 모델 가중치를 재배포하지 않고(각자 받는다) 벡터만 만든다. 벡터 파일도 Release 에 올리지 않는다 (아래 "영향").
- **실측 처리량은 벤치보다 빠르다**: 표본 생성(저장소 3개 6,719건)이 1.8분, 45~77건/초 → 72만 건 약 2.6~4.5시간. 벤치 1,000건은 묶음이 작아 느리게 잡혔다.
- 처음에는 50건 × 3회를 길이순 정렬 없이 쟀다 → jina 256 이 29시간, bge-small 256 이 9시간으로 나왔다. 묶음 안 패딩 때문이었고, 실제 생성과 같은 길이순 묶음으로 다시 쟀다 (위 표). 이 첫 측정은 버렸다.

## 질의 10개 상위 5개 (정성 비교)

각 칸은 상위 5개 함수 이름(유사도). **굵게** = 질의를 쓸 때 떠올린 함수. jina 는 시간 상한 안에 드는 쪽에 가까운 128, 나머지는 256.

| # | 질의 | jina-code (128) | granite-small-r2 (256) | bge-small (256) |
|---:|---|---|---|---|
| 1 | send a signal to stop a background daemon using the pid stored in a file | **kill_defaultd** (.54), tearDownClass (.25), close (.18), execute_request_handler, solution_handler | **kill_defaultd** (.86), execute_request_handler (.80), close (.78), \_\_init\_\_, test_get_resource | **kill_defaultd** (.70), test_trailing_comma_…_pass2 (.65), prompt_api_key (.63), tearDownClass, \_\_init\_\_ |
| 2 | reject a proxy host that points to a private or internal network address | **validate_proxy_destination** (.63), test_unauthorized_source (.35), test_callback_success_…, kill_defaultd, expected_missing_file_errors | **validate_proxy_destination** (.87), test_callback_success_… (.78), refresh, options, callback_handler | **validate_proxy_destination** (.75), test_unauthorized_source (.63), test_url_param_query, options, test_callback_success_… |
| 3 | linear blend between two points by a fraction | **interpolate** (.60), rect (.37), \_forward_silu_gating, \_\_getitem\_\_, \_set_gradient_checkpointing | **interpolate** (.83), rect (.80), \_set_gradient_checkpointing, callback_inputs_change_tensor, \_forward_silu_gating | **interpolate** (.69), \_forward_silu_gating (.62), rect (.59), callback_inputs_change_tensor, test_equal_class_weight |
| 4 | read the request history csv file and return its text | **load_requests_csv** (.61), formatting_func (.28), test_only_line_separator_is_stripped, test_get_last_traded_prices, collect_testing_data | **load_requests_csv** (.91), test_get_last_traded_prices (.83), execute_request_handler, test_get_resource, get_json_data | **load_requests_csv** (.82), test_trailing_comma_…_pass2 (.65), test_get_resource, get_json_data, prompt_api_key |
| 5 | keep only the tasks that match the include labels and drop excluded ones | **filter_tasks_by_tags** (.58), \_script_multiturn_…, test_equal_class_weight, test_only_line_separator_is_stripped, \_mbpp_eval | **filter_tasks_by_tags** (.86), test_only_line_separator_is_stripped (.80), \_set_gradient_checkpointing, \_script_multiturn_…, \_\_query | **filter_tasks_by_tags** (.70), prompt_api_key (.62), solution_handler, \_set_gradient_checkpointing, \_\_init\_\_ |
| 6 | rename pretrained checkpoint weight keys to our model's parameter names | **mapping_openclip_state_dict** (.49), train (.38), \_set_gradient_checkpointing, \_\_init\_\_, dict_learning | \_set_gradient_checkpointing (.82), formatting_func, callback_inputs_change_tensor, test_equal_class_weight, test_step_end — **10위** | **mapping_openclip_state_dict** (.70), \_set_gradient_checkpointing (.69), prompt_api_key, get_initial_state, callback_inputs_change_tensor |
| 7 | build SQL that pulls the year or month out of a date column | **get_date_extract_sql** (.51), \_\_query (.17), load_requests_csv, formatting_func, \_style | **get_date_extract_sql** (.86), \_\_query (.81), formatting_func, test_postgresql_without_env_var, load_requests_csv | **get_date_extract_sql** (.74), \_\_query (.61), load_requests_csv, test_only_line_separator_is_stripped, formatting_func |
| 8 | turn on activation recomputation for the encoder to save memory | train (.49), **\_set_gradient_checkpointing** (.45), dict_learning, \_forward_silu_gating, test_equal_class_weight | train (.82), mapping_openclip_state_dict, \_forward_silu_gating, \_script_multiturn_…, \_client — **10위** | **\_set_gradient_checkpointing** (.66), get_initial_state (.66), \_\_init\_\_, \_script_multiturn_…, callback_inputs_change_tensor |
| 9 | learn a dictionary of atoms for sparse coding from data in mini batches | **dict_learning** (.72), mapping_openclip_state_dict (.43), test_equal_class_weight, \_\_init\_\_, \_\_init\_\_ | **dict_learning** (.89), mapping_openclip_state_dict (.80), \_\_init\_\_, test_equal_class_weight, train | **dict_learning** (.82), get_initial_state (.61), mapping_openclip_state_dict, \_mbpp_eval, callback_inputs_change_tensor |
| 10 | download a package archive once and reuse a locally cached copy during install | **cache_package** (.69), test_get_resource (.26), load_requests_csv, \_script_multiturn_…, prompt_api_key | **cache_package** (.90), \_client (.79), \_script_multiturn_…, test_url_param_query, validate_proxy_destination | **cache_package** (.76), \_client (.63), test_trailing_comma_…_pass2, test_get_resource, \_\_init\_\_ |

관찰:

- **jina-code 는 1위와 2위의 간격이 가장 크다** (예: 1번 .54 vs .25). 질이 가장 좋아 보이지만, 처리 시간이 128 토큰에서도 9.9시간으로 8시간 상한을 넘는다.
- **granite-small-r2 는 유사도가 0.75~0.9 에 몰려 있다** (간격 작음). 6·8번(ML 용어를 바꿔 말한 질의)에서 의도한 함수를 10위로 놓쳤다.
- **bge-small 은 10개 모두 의도한 함수가 1위**지만 2위와의 간격이 jina 보다 작다 (8번 .66 vs .66). 2~5위는 대체로 무관한 함수다 — 50건 중에 관련 함수가 하나뿐이라 당연하다.
- 표본 50건·질의 10개(모두 자연어)는 작다. 코드 조각 질의는 이 표에 없다 — 표본 생성(저장소 3개) 뒤 화면에서 `def load_config(path): … yaml.safe_load(f)` 를 넣어 같은 이름의 지워진 함수 둘이 1·2위(.90, .89)로 나오는 것을 확인했다. 관련성 정량 평가는 Precision@10 블라인드 판정(`docs/reports/search_relevance.md`, 전체 임베딩 후)으로 한다 — 이 질의는 결과를 보고 고른 시연 예시라 그 평가 질의에서는 뺐다.

## 버린 후보와 이유

- **jinaai/jina-embeddings-v2-base-code** — 코드 전용이고 정성 질도 가장 좋아 보이지만 72만 건이 128 토큰에서도 9.9시간(256 이면 15.9시간)으로 8시간을 넘는다. 768차원이라 벡터 파일도 두 배다. 노트북 한 대로 밤새 한 번에 끝나지 않으면 이어 하기로 이틀을 써야 한다. 시간이 허락하면 다음 버전 후보로 남긴다.
- **ibm-granite/granite-embedding-small-english-r2** — 속도는 bge-small 과 같은데 의도한 함수 10개 중 2개를 10위로 놓쳤고 유사도가 몰려 있어 순위가 불안정하다.
- 목록에 있지만 비교하지 않은 것: Qwen3-Embedding-0.6B(1.1~2.4 GB, CPU 에 너무 큼), jina-embeddings-v3(CC BY-NC — 상업 재배포 불가), nomic-embed-text(768차원, jina-code 와 비슷한 크기라 시간 상한을 넘을 것으로 보았다), potion(정적 임베딩, 문맥을 보지 않음).

## pgvector 영향 (ADR-009)

#8 완료 조건의 "임베딩 차원이 pgvector 인덱스에 미치는 영향" — 이번에는 pgvector 를 쓰지 않는다 (ADR-023). 참고로 384차원 72만 건은 float32 1.1 GB, float16 0.56 GB 다. 이 크기는 전부 메모리에 올려 한 번에 내적해도 0.035초(합성 벡터 실측)라 근사 인덱스(HNSW·IVF)가 필요 없다.
