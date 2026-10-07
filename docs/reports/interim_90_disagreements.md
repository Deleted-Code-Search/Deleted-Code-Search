# #90 중간 점검 불일치 분석 — 블록당 처음 50건

- 작성: 2026-10-08, 성제(sj). 집계·초안은 Claude Code가 돕고 사람이 검토한다
- 대상: 500건 본 라벨링 중간 점검(가이드 §8.4.1) 세 블록 × 50건 = 150건 가운데 **이유 또는 등급이 갈린 69건**
- 입력 (라벨 파일은 아직 커밋 전이다. 다시 낼 때 비교하도록 sha256 앞 16자리를 적는다)

| 파일 | sha256 앞 16자리 |
|---|---|
| `datasets/labels/sj_main500.jsonl` | `5f71a37677f807d6` |
| `datasets/labels/jh_main500.jsonl` | `0a13d52843679bc1` |
| `datasets/labels/hs_main500.jsonl` | `20aeae9e70c677ee` |
| `datasets/labels/main500_records.jsonl` | `db6bc77f93b8df4d` |
| `datasets/labels/main500_assignment.jsonl` | `b7ed920a06588216` |

> **이 문서는 라벨을 확정하지 않는다.** 누가 맞는지는 §8.3 확정 토론과 회의가 정한다. §9 체크리스트를 이번 건에 적용한 결과도 **"규칙이 이렇게 답한다"**는 뜻이지 정답 라벨이 아니다. 체크리스트 질문의 예/아니오는 Claude가 레코드를 읽고 단 답이라 사람이 다시 확인해야 한다 (CHARTER §16-4, ADR-005).

---

## 0. 요약

| 항목 | 값 |
|---|---|
| 불일치 레코드 | **69 / 150건** (A 21 · B 21 · C 27). 이유만 갈림 15 · 등급만 갈림 17 · 둘 다 갈림 37 |
| 등급 혼동 (54건) | **I↔U 25** · **E↔I 21** · E↔U 8. UNKNOWN이 낀 쪽이 33건(61%) |
| 이유 혼동 (52건) | DESIGN↔UNK 16 · DEAD↔UNK 12 · DEAD↔DESIGN 7 · DEAD↔FEAT 4 · LIB↔UNK 4 · BUG↔DESIGN 3 · 그 밖 6 |
| `filter-miss`가 한쪽에만 달린 건 | **18건.** 이유 혼동 중 UNK가 낀 33건 가운데 18건이 여기서 나온다 (DESIGN↔UNK 7, DEAD↔UNK 7, LIB↔UNK 4) |
| I↔U 25건의 INFERRED 쪽 근거 | **③ 14** · ⑤ 5 · **⑥ 4** · ④ 1 · ② 1. ③ 14건은 모두 **테스트 레코드가 자기 자신이 지워진 것을 ③으로 든 것**이다 |
| ⑥ 집중 가설 | **지지되지 않는다.** I↔U에서 ⑥은 4/25건이다. 몰린 것은 ③이다 |
| `anchored` 3건 | **모두 jh, 블록 A** (A39·A42·A43). 세 건 모두 sj와 이유·등급이 같았고 kappa 분모에서 빠졌다 (A 분모 47) |
| 화면 정보 | `replacement.code`가 있던 불일치 건은 **3/69건** (A05·A18·C11). 호출자·테스트 변화는 **레코드에 필드가 없다** |
| 체크리스트 적용 (등급 불일치 54건) | 규칙이 한쪽과 일치 **42건** (sj 19 · jh 13 · hs 10) · 둘 다와 다름 **3건** · **규칙으로도 안 갈림 9건 (17%)** |
| 이진 kappa (참고) | A **0.644** · B **0.464** · C **0.122.** B·C는 3등급 kappa(0.582 · 0.220)보다 **오히려 낮다** |

배경 설명("경계 건에서 사람 감으로 갈린다")은 절반만 맞다. 숫자가 말하는 원인은 세 가지다.

1. **`filter-miss` 판정이 사람마다 다르다 (18건).** 다른 저장소로 옮긴 코드(§6.3.3 4번)와 벤더링 코드(3번)를 한 사람은 `filter-miss`로, 다른 사람은 DESIGN·DEAD·LIB 라벨로 처리했다. 같은 사람도 블록마다 다르게 했다. I↔U 경계 문제가 아니라 §6.3.3 적용 문제다.
2. **③(테스트 변화)이 "이 테스트가 같이 지워졌다"로 쓰인다.** INFERRED 근거로 ③이 150건에서 73번 쓰였고 그중 71번이 테스트 레코드다. 화면에는 테스트 변화 필드가 없다. 결국 커밋 제목을 보고 "같이 지워졌으니 무관해졌다"고 잇는 것이고, 이것은 §6.2.1이 근거로 인정하지 않는 주제적 근접성과 구별이 안 된다.
3. **E1(이유 서술)을 읽는 폭이 다르다 (E↔I 21건).** jh(블록 A)와 hs(블록 C)는 PR 본문에서 이 변경을 설명하는 문장이면 EXPLICIT으로 봤다. sj는 같은 문장을 E2·E3에서 떨어뜨려 INFERRED로 내렸다. 문장에 "왜"가 있는지부터 판단이 다르다.

---

## 1. 방법

**짝 맞추기.** `classify/labels.py`의 함수를 그대로 썼다. 새 로직은 없다.

```python
personal = load_personal_labels(Path("datasets/labels"), "main500")
assignment = load_assignment(Path("datasets/labels"), "main500")
merged = merge_labels(personal, batch="main500", assignment=assignment)
# 배분 파일의 interim=True 레코드만 (format_interim_report 와 같은 기준)
# agreement_of(row["labels"]) 가 (False, *) 또는 (*, False) 인 건 = 불일치
```

- `python -m classify.labels --interim`의 출력(블록별 kappa·혼동 쌍)과 이 문서의 건수를 맞춰 봤다. 블록 A 등급 혼동 E↔I 11 · I↔U 6, 블록 B I↔U 8 · E↔U 4 · E↔I 2, 블록 C I↔U 11 · E↔I 8 · E↔U 4로 같다.
- `anchored` 3건은 kappa 분모에서 빠지지만(`pair_labels`), 세 건 모두 일치 건이라 불일치 69건에는 영향이 없다.
- 집계 스크립트는 커밋하지 않았다(작업 디렉터리 밖 임시 파일). 위 네 줄과 아래 규칙으로 같은 숫자가 나온다.

**INFERRED 근거 번호.** INFERRED 라벨 162개(150건 × 2인 중) 전부가 `evidence_text` 맨 앞에 ①~⑥ 중 하나를 적었다. 그 **첫 번째 원문자**로 셌다. 라벨러가 적은 번호를 그대로 센 것이고, 그 번호가 가이드 정의에 맞는지는 §4에서 따로 본다.

**화면 정보.** "있었나"는 `main500_records.jsonl`의 필드로만 확인했다 — `replacement.*`, `added_hunks_same_file`, `is_test_code`, `context.*`. GitHub은 열지 않았다.

---

## 2. 혼동 유형별 건수

### 2.1 등급 (54건)

| 유형 | A (sj+jh) | B (jh+hs) | C (hs+sj) | 계 | 이 중 `filter-miss` 관련 |
|---|---:|---:|---:|---:|---:|
| I↔U | 6 | 8 | 11 | **25** | 11 |
| E↔I | 11 | 2 | 8 | **21** | 0 |
| E↔U | 0 | 4 | 4 | **8** | 7 |
| 계 | 17 | 14 | 23 | 54 | 18 |

**E↔U 8건 중 7건이 `filter-miss` 건이다.** 한쪽이 `UNK`+`filter-miss`, 다른 쪽이 EXPLICIT이다. 나머지 1건은 B37(§3.4)이다.

**E↔I에서 EXPLICIT을 단 사람**: 블록 A 11건 중 jh 10 · sj 1(A07), 블록 C 8건 중 hs 8, 블록 B 2건 중 jh 2. hs는 블록 B(jh 상대)에서는 INFERRED 쪽이었고 블록 C(sj 상대)에서는 EXPLICIT 쪽이었다 — 상대에 따라 위치가 바뀐다. 즉 "EXPLICIT 기준이 넓은 사람"이 있다기보다 **sj가 가장 좁고, jh가 가장 넓고, hs가 가운데**다. 150건 전체의 등급 분포도 같다.

| 라벨러 | EXPLICIT | INFERRED | UNKNOWN |
|---|---:|---:|---:|
| sj (100건) | 8 | 67 | 25 |
| jh (100건) | 27 | 42 | 31 |
| hs (100건) | 28 | 53 | 19 |

**I↔U에서 INFERRED를 단 사람**: hs 15 · sj 7 · jh 3.

### 2.2 이유 (52건)

| 유형 | 건수 | `filter-miss` 관련 | 주된 형태 |
|---|---:|---:|---|
| DESIGN↔UNK | 16 | 7 | 리팩터·이전 커밋에서 지워진 헬퍼를 DESIGN(I 0.5~0.6) 대 UNK. 나머지 7건은 "다른 저장소로 이동"을 DESIGN/E 대 `filter-miss 4` |
| DEAD↔UNK | 12 | 7 | 패키지·서브트리 통째 삭제에 딸린 테스트를 DEAD/I(③) 대 UNK |
| DEAD↔DESIGN | 7 | 0 | 검증 대상이 바뀌거나 사라진 테스트. 한쪽은 "무관해졌다"(DEAD, 규칙 ②), 다른 쪽은 "구조가 바뀌어 테스트가 재편됐다"(DESIGN) |
| DEAD↔FEAT | 4 | 0 | deprecated 옵션·호환 API를 검증하던 테스트·헬퍼. 지원 종료(FEAT)냐 쓸모없어짐(DEAD)이냐 |
| LIB↔UNK | 4 | 4 | `requests/packages/` 벤더링 코드 삭제. LIB 대 `filter-miss 3` |
| BUG↔DESIGN | 3 | 0 | 한 문장에 구조 이유와 버그 이유가 같이 있다 (`multi-reason`) |
| BUG↔DEAD 2 · DEAD↔SEC 1 · DESIGN↔FEAT 1 · BUG↔UNK 1 · DEAD↔LIB 1 | 6 | 0 | |

이유 쪽은 UNK가 낀 33건이 대부분 **등급 문제가 이유 칸으로 번진 것**이다(UNKNOWN이면 UNK이므로). UNK가 끼지 않은 이유 혼동은 19건이고, 그중 DEAD↔DESIGN 7 · DEAD↔FEAT 4가 테스트·deprecated 경계다.

### 2.3 같은 커밋인데 같은 사람이 다르게 단 건

블록이 달라도 같은 커밋(또는 같은 메시지의 커밋)이 여러 번 나왔다. 독립 라벨이라 사람 사이 차이는 당연하지만, **같은 사람이 같은 커밋에서 다르게 단 것**은 규칙이 모호하다는 직접 신호다.

| 커밋 | 레코드 | sj | jh | hs |
|---|---|---|---|---|
| langchain `community: move to separate repo` | A14 · C19 · C48 · B40 | U + `filter-miss 4` | U + `filter-miss 4` | **C19 DESIGN/E · C48·B40 DEAD/I** |
| crewAI `Build FlowDefinition…` | C43 · B07 | DESIGN/E | DEAD/I | **C43 DESIGN/E · B07 DESIGN/I** |
| openpilot `Remove old panda subtree` (두 커밋) | A33 · B36 / C29 · B39 | UNK/U | **A33·B36 DEAD/I · B39 UNK/U** | DEAD/I |
| requests `delete packages` | C09 · C24 · B27 · B32 | LIB/I | LIB/E | U + `filter-miss 3` |
| mem0 `extract embedchain to …-archive` | A44 · B18 · B43 | U + `filter-miss` | U + `filter-miss` | DEAD/I |

requests는 세 사람이 각자 일관되지만 **세 사람이 세 등급**을 냈다.

---

## 3. 혼동 유형별 레코드 묶음

전체 69건의 두 라벨 원문(`evidence_text`·`note` 포함)은 **부록 A**에 있다. 여기서는 유형별로 묶고 화면 정보만 붙인다. "코드"는 `replacement.code` 유무, "헝크"는 같은 파일 추가 헝크 수, "T"는 `is_test_code`.

### 3.1 `filter-miss` 경계 — 18건

한쪽이 `UNK`+`filter-miss N`, 다른 쪽이 이유 라벨이다.

| 건 | 저장소 · 함수 | `filter-miss` 쪽 | 다른 쪽 | 코드 | 헝크 | T |
|---|---|---|---|---|---:|---|
| C01 | transformers · `_get_ground_truth` | sj `4` (research_projects → 별도 저장소) | hs DESIGN/E 1.0 | 없음 | 0 | |
| C08 | langchain · `test_anonymize_allow_list` | sj `4` (experimental → 외부 저장소) | hs DEAD/I 0.7 ③ | 없음 | 0 | T |
| C16 | mem0 · `__init__` | sj `4` (evaluation/ → memory-benchmarks) | hs DESIGN/E 1.0 | 없음 | 0 | |
| C19 | langchain · `embeddings` | sj `4` (community → 별도 저장소) | hs DESIGN/E 1.0 | 없음 | 0 | |
| C28 | langchain · `_merge_splits_no_seperator` | sj `4` (ai21 → 외부 저장소) | hs DESIGN/E 1.0 | 없음 | 0 | |
| C48 | langchain · `test__convert_dict_to_message_human` | sj `4` | hs DEAD/I 0.5 ③ | 없음 | 0 | T |
| B11 | langchain · `test_default_params` | jh `4` (cohere → 외부 저장소) | hs DEAD/I 0.5 ③ | 없음 | 0 | T |
| B18 | mem0 · `google_drive_folder_loader` | jh `4` (embedchain → archive 저장소) | hs DEAD/I 0.7 ③ | 없음 | 0 | T |
| B40 | langchain · `test_faiss_with_metadatas_and_filter` | jh `4` | hs DEAD/I 0.5 ③ | 없음 | 0 | T |
| B43 | mem0 · `test_ec_base_message` | jh `4` | hs DEAD/I 0.7 ③ | 없음 | 0 | T |
| C09 | requests · `hebrewprober.__init__` | hs `3` (벤더링 chardet) | sj LIB/I 0.55 ⑤ | 없음 | 0 | |
| C24 | requests · `retry.__repr__` | hs `3` (벤더링 urllib3) | sj LIB/I 0.55 ⑤ | 없음 | 0 | |
| B27 | requests · `selectors.__init__` | hs `3` | jh LIB/E 1.0 | 없음 | 0 | |
| B32 | requests · `ordered_dict.__delitem__` | hs `3` | jh LIB/E 1.0 | 없음 | 0 | |
| B29 | docling · `group_cell_elements` | jh `1` (같은 파일 `_group_cell_elements`로 이동) | hs DESIGN/E 1.0 | 없음 | 40 | |
| A50 | docling · `_ensure_parsed` | jh `1` (v4 백엔드 → 새 파일로 이름 변경) | sj DESIGN/I 0.65 ④ | 없음 | 6 | |
| C14 | pydantic · `test_sub_model_merge` | sj `1` (`off-record-evidence`: 새 위치에 같은 테스트 확인) | hs DEAD/I 0.6 ③ | 없음 | 0 | T |
| C32 | superset · `test_extract_table_references` | hs `1` (리뷰: 헬퍼가 `sql_parse`로 이동) | sj DESIGN/I 0.75 ③ | 없음 | 0 | T |

- **4번(다른 저장소로 분리) 10건에서 hs는 한 번도 `filter-miss`를 달지 않았다.** sj·jh는 모두 달았다. hs는 문장이 옮긴 곳을 말하면 "구조 이동"(DESIGN/E)으로, 테스트면 "패키지와 같이 지워졌다"(DEAD/I ③)로 봤다.
- **3번(벤더링) 4건은 hs만 `filter-miss`를 달았다.** sj·jh는 "벤더링 의존성을 외부 패키지로 바꿨다"를 LIB로 봤다(sj I, jh E).
- **1번(이동)은 화면으로 확인되는 것이 B29 하나다.** B29는 추가 헝크에 `_group_cell_elements` staticmethod가 거의 같은 본문으로 있다. A50·C14·C32는 옮긴 곳이 다른 파일이라 화면에 없다 — C14는 sj가 GitHub을 열어 확인했고(`off-record-evidence`), 나머지는 확인하지 않았다.
- sj·jh의 4번 `note`는 대부분 "새 저장소에 이 파일이 있는지는 확인하지 않았다"로 끝난다. 가이드 §6.3.3은 "판정은 반드시 diff를 보고 한다, 메시지만으로 판정하지 않는다"인데, 4번은 diff에 삭제만 보이므로 **문장 말고 확인할 곳이 없다.** 4번을 문장으로 판정해도 되는지 가이드가 정하지 않았다 → §9.4 안건 1.

### 3.2 I↔U (`filter-miss` 제외) — 15건

| 건 | 저장소 · 함수 | INFERRED 쪽 | UNKNOWN 쪽 | 코드 | 헝크 | T |
|---|---|---|---|---|---:|---|
| A08 | docling · `contains` | jh DESIGN 0.6 ⑤ | sj | 없음 | 0 | |
| A29 | crawl4ai · `get_delayed_content` (`*.current.py`) | jh DESIGN 0.6 ⑤ | sj | 없음 | 0 | |
| A33 | openpilot · `test_disable_control_allowed_from_cruise` | jh DEAD 0.7 ③ | sj | 없음 | 0 | T |
| A45 | superset · `pre_load` | sj DESIGN 0.5 ⑥ | jh | 없음 | 0 | |
| A46 | browser-use · `_switch_agent_focus_to_tab` | sj DESIGN 0.5 ⑥ | jh | 없음 | 0 | |
| C25 | browser-use · `fake_load_events` | hs DESIGN 0.5 ③ | sj | 없음 | 57 | T |
| C29 | openpilot · `test_elm_protocol_autodetect_…` | hs DEAD 0.5 ③ | sj | 없음 | 0 | T |
| C33 | crawl4ai · `set_custom_headers` (`*.back.py`) | hs DEAD 0.5 ⑥ | sj | 없음 | 0 | |
| C36 | browser-use · `on_step_start` | hs DESIGN 0.5 ③ | sj | 없음 | 57 | T |
| C45 | docling · `_client` | sj DESIGN 0.5 ⑥ | hs | 없음 | 0 | |
| B12 | browser-use · `test_save_conversation_deep_directory` | hs DESIGN 0.5 ③ | jh | 없음 | 0 | T |
| B16 | vllm · `test_http_chat_no_model_name_with_curl` | hs BUG 0.6 ③ | jh | 없음 | 1 | T |
| B20 | django · `_get_trigger_name` | hs DESIGN 0.5 ② | jh | 없음 | 14 | |
| B39 | openpilot · `set_ir_power` | hs DEAD 0.5 ⑤ | jh | 없음 | 0 | |

I↔U 25건 = 이 14건 + §3.1의 `filter-miss` 쪽 I↔U 11건(A50·C08·C09·C14·C24·C32·C48·B11·B18·B40·B43).

- **INFERRED 쪽 신뢰도가 0.5~0.6에 몰려 있다** (14건 중 13건). 가이드 §6.2.2가 "0.5는 하한이지 기본값이 아니다"라고 경고한 모양 그대로다.
- **근거 문장이 구별 검사(§6.2.1)를 통과하지 못하는 건이 대부분이다.** 예: A08 jh "커밋이 레이아웃 후처리를 크게 재구성했고 이 헬퍼는 그 과정에서 제거됐다", C25 hs "같은 파일에 테스트가 대량(57개 헝크) 추가되는 과정에서 이 스텁이 삭제됐다". 함수 이름만 바꾸면 같은 커밋의 다른 삭제에도 그대로 들어맞는다.
- 반대로 UNKNOWN 쪽 `note`는 "직접 대체 코드나 호출자 변화가 화면에서 확인되지 않음"(jh)이 반복된다. 화면에 없는 것을 이유로 UNKNOWN을 단 것은 가이드 U4 그대로다.

### 3.3 E↔I — 21건

| 건 | 저장소 · 함수 | EXPLICIT 쪽이 든 문장 (요약 없이 앞부분) | INFERRED 쪽 | 코드 | 헝크 |
|---|---|---|---|---|---:|
| A03 | transformers · `custom_forward` | jh "remove all remaining `create_custom_forward` methods" | sj 0.6 ⑤ | 없음 | 4 |
| A05 | docling · `get_segmented_page` | jh "All backends now implement get_segmented_page() and create SegmentedPdfPage objects" | sj 0.7 ① | **있음 (0.7)** | 8 |
| A07 | docling · `test_rapidocr_default_models_…` | **sj** "Previously the model shipped hand-maintained per-language, per-backend model-path tables that were hard to extend…" | jh 0.75 ③ | 없음 | 15 |
| A13 | httpx · `test_urlparse_invalid_ipv6` | jh "Move test cases from `tests/test_urlparse.py` into `tests/models/test_url.py`." | sj 0.6 ③ | 없음 | 0 |
| A18 | openpilot · `get` | jh "fix mockparams arguments" | sj 0.8 ① | **있음 (0.9)** | 1 |
| A24 | django · `__init__` | jh "Rewrote form widget tests as proper unittests." | sj 0.6 ⑥ | 없음 | 3 |
| A28 | superset · `parse_filters` | jh "Every `list_*` tool's request/response schema pair re-declared the same 7 filter/pagination fields, the same 2 field validators…" | sj 0.75 ⑤ | 없음 | 4 |
| A34 | home-assistant · `__init__` | jh "Aladdin Connect has been removed. Please remove the leftover entries from your system." | sj 0.7 ④ | 없음 | 0 |
| A36 | vllm · `get_num_mm_connector_tokens` | jh "This PR migrate built-in model token-count implementations to `get_mm_lora_token_counts()`." | sj 0.75 ⑤ | 없음 | 2 |
| A37 | crewAI · `__add__` | jh "This commit decided to remove it completely for simplicity and performance:" | sj 0.75 ⑤ | 없음 | 2 |
| A40 | crewAI · `_set_embedder_config` | jh "refactors rag storage to support instance-specific configurations, allowing multiple … without global state conflicts." | sj 0.75 ⑥ | 없음 | 15 |
| C02 | django · `get_available_name` | hs "Refs #9893 -- Removed shims for lack of max_length support in file storage per deprecation timeline." | sj 0.7 ⑥ | 없음 | 0 |
| C04 | vllm · `_configure_bitblas_matmul` | hs "now that 0.14 is out, deprecate bitblas for 0.15" | sj 0.75 ④ | 없음 | 0 |
| C06 | docling · `count_right` | hs "Updated VLM pipeline code to reuse `DocTagsDocument.from_doctags_and_image_pairs` from `docling-core`, instead of…" | sj 0.7 ⑥ | 없음 | 5 |
| C11 | crewAI · `_add_code_execution_tools` | hs "Fix type errors in crew.py by updating tool-related methods to return List[BaseTool]" | sj 0.8 ① | **있음 (0.9)** | 28 |
| C13 | crewAI · `_format_union_type` | hs "Remove deprecated PydanticSchemaParser in favor of direct schema generation" | sj 0.75 ⑥ | 없음 | 0 |
| C15 | pydantic · `replace_users` | hs (리뷰) "This stops adding the changelog to the readme on release, but I think that's fine since…" | sj 0.8 ④ | 없음 | 2 |
| C17 | crewAI · `_summarize_messages` | hs "Remove dead code, unused imports, and obsolete methods" | sj 0.6 ⑥ | 없음 | 27 |
| C30 | superset · `get_raw_results` | hs "This branch removes the legacy viz pipeline — the deprecated `/superset/explore_json/` endpoints and `superset/viz.py`…" | sj 0.8 ④ | 없음 | 1 |
| B08 | home-assistant · `reset` | jh "It removes a few methods from EntityComponent that are not in use and should not be used." | hs 0.6 ⑤ | 없음 | 9 |
| B50 | transformers · `custom_forward` | jh "replace with `__call__`" | hs 0.7 ② | 없음 | 6 |

- EXPLICIT 쪽 문장을 둘로 나눌 수 있다. **(가) "무엇을 했다"만 있고 "왜"가 없는 문장** — A03·A05·A13·A18·A24·A34·A36·B50 ("remove…", "migrate…", "Move…", "Rewrote…", "has been removed", 맨 "fix …"). 가이드 §6.1.1 E1("삭제 사실이 아니라 왜") 기준으로는 EXPLICIT 자격이 없다. **(나) 이유는 있는데 이 함수에 닿는지가 문제인 문장** — A28·A37·A40·C02·C06·C17·B08 등. 여기가 E2 판정이다.
- (가)가 8건이다. E1을 "왜가 있나"로 읽느냐, "이 변경을 설명하는 문장이 있나"로 읽느냐가 갈렸다. §9 체크리스트는 E1을 **이유어가 있는지**로 기계적으로 바꾸자고 제안한다.
- C17은 가이드 §6.1.2 5번(`make_v1_generic_root_validator`, "remove unused function" → E2 실패)과 같은 형태인데 hs는 EXPLICIT을 달았다. 가이드 예시가 실제 라벨에 닿지 않은 사례다.

### 3.4 E↔U (`filter-miss` 제외) — 1건

| 건 | 저장소 · 함수 | EXPLICIT 쪽 | UNKNOWN 쪽 | 코드 | 헝크 |
|---|---|---|---|---|---:|
| B37 | keras · `_unique_padded` | hs DEAD "The original revert in c559fa8 did not revert newly created files." | jh (`no-replacement no-caller-info`) | 없음 | 0 |

hs 스스로 `note`에 "이 함수가 새로 만들어진 파일에 있는지는 확인하지 않았다"고 적었다. 문장의 집합("revert가 빠뜨린 새 파일")에 이 레코드가 속하는지 레코드만으로 확인되지 않으니 E2 실패이고, 구조적 근거도 없다.

> §2.1의 E↔U 8건은 이 1건이 아니라 `filter-miss` 쪽 7건(C01·C16·C19·C28·B27·B29·B32)에 이것을 더한 것이다.

### 3.5 이유만 갈린 건 — 15건 (등급은 일치)

| 건 | 저장소 · 함수 | 등급 | 갈린 이유 | 형태 |
|---|---|---|---|---|
| A01 | superset · `test_adds_limit_to_select` | I/I | sj DEAD · jh DESIGN | 검증 대상 모듈이 지워진 테스트 |
| A16 | scikit-learn · `test_invalid_classification_loss` | I/I | sj DEAD · jh FEAT | deprecated loss 옵션을 검증하던 테스트 (jh `needs-discussion`) |
| A19 | transformers · `test_end_to_end_thresh_16M` | I/I | sj DEAD · jh FEAT | 제거된 CLI·모듈을 검증하던 테스트 |
| A26 | django · `six.itervalues` | I/I | sj DEAD · jh FEAT | Python 2 호환 API 제거 |
| C18 | django · `fast_cache_clearing` | E/E | hs SEC · sj DEAD | 같은 문장. CVE 언급이 있으나 삭제 이유는 "upstream 수정으로 우회가 불필요해짐" (sj `needs-discussion`, hs `priority-rule`) |
| C20 | httpx · `set_minimum_tls_version_1_2` | I/I | hs DEAD · sj DESIGN | 옛 환경용 호환 헬퍼 |
| C35 | home-assistant · `test_skip_non_existing_update` | I/I | hs DEAD · sj FEAT | revert로 통합 전체가 빠짐 |
| C44 | scikit-learn · `add_example` | E/E | hs BUG · sj DEAD | "This example is broken" — 고치지 않고 지움 (sj `needs-discussion`) |
| B05 | vllm · `test_reserve_mm_ipc_gpu_memory_…` | I/I | jh DEAD · hs DESIGN | 대상 코드가 다른 곳으로 옮겨진 테스트 |
| B07 | crewAI · `handle_cancelled` | I/I | jh DEAD · hs DESIGN | 검증 대상(serializer)이 지워진 테스트 |
| B17 | scikit-learn · `test_get_params_deprecated` | I/I | jh BUG · hs DEAD | 잘못된 동작을 고정하던 테스트 |
| B24 | crawl4ai · `crawl_chanel` | I/I | jh DESIGN · hs DEAD | 릴리스 커밋의 테스트 스크립트 정리 |
| B26 | pandas · `test_constructor_32bit` | I/I | jh DEAD · hs DESIGN | 폐기 클래스 테스트를 `Index`로 다시 씀 |
| B45 | home-assistant · `async_update` | E/E | jh DESIGN · hs BUG | 같은 문장에 (a) 중복 (b) 갱신 안 되는 버그 (둘 다 `multi-reason`) |
| B47 | celery · `__ne__` | E/E | jh DEAD · hs LIB | 같은 문장. 언어 기본 동작으로 대체 — 라이브러리 호출 추가 없음 |

- **테스트 레코드 9건이 여기 있다.** 검증 대상이 사라지거나 옮겨졌을 때 테스트 삭제를 DEAD(§5.1 규칙 ② "무관해져서 정리")로 볼지, 대상의 이유(DESIGN·FEAT)를 따라갈지가 가이드에 없다. 규칙 ②는 "무관해진" 경우만 말하고, 대상이 리팩터·폐기된 경우를 말하지 않는다.
- C18·C44·B45·B47 4건은 **같은 문장을 인용하고 이유만 다르다.** §3.2 "더 구체적인 쪽" 규칙이 이 쌍들(SEC↔DEAD, BUG↔DEAD, DESIGN↔BUG, DEAD↔LIB)을 다루지 않는다. 체크리스트(§9)는 등급만 다루므로 이 15건은 해결하지 못한다.

---

## 4. INFERRED 근거 ①~⑥ 집계

### 4.1 전체와 불일치 건

| 근거 | 150건 전체 (INFERRED 162개) | 불일치 69건 안 | 그중 I↔U 25건의 INFERRED 쪽 |
|---|---:|---:|---:|
| ① 대체 코드 | 10 | 3 | 0 |
| ② 호출자 변화 | 3 | 2 | 1 |
| ③ 테스트 변화 | **73** | 31 | **14** |
| ④ 공개 표면 변화 | 19 | 8 | 1 |
| ⑤ 같은 커밋 동형 삭제 | 27 | 11 | 5 |
| ⑥ 삭제된 코드 자체 | 30 | 13 | **4** |

### 4.2 라벨러별

| 라벨러 | ① | ② | ③ | ④ | ⑤ | ⑥ | 계 |
|---|---:|---:|---:|---:|---:|---:|---:|
| sj | 5 | 0 | 17 | 8 | 13 | **24** | 67 |
| jh | 2 | 0 | 22 | 7 | 9 | 2 | 42 |
| hs | 3 | 3 | **34** | 4 | 5 | 4 | 53 |

### 4.3 ⑥이 I↔U에 몰리나 — 아니다. 몰린 것은 ③이다

- I↔U 25건 중 ⑥은 **4건**(A45·A46·C45 sj, C33 hs)이다. ⑥을 가장 많이 쓴 sj(24회)도 I↔U에서는 3회였다. 가설은 지지되지 않는다.
- ③은 I↔U에서 **14건**이고, **14건 모두 테스트 레코드**다. 150건 전체에서도 ③ 73회 중 **71회가 테스트 레코드**다(`is_test_code = true`).
- 가이드 §6.2.1의 ③은 "같은 커밋에서 **이 함수를 검증하던 테스트가** 추가·수정·삭제됐나"다 — 비테스트 함수의 근거다. 테스트 레코드에서 ③을 쓰면 "이 테스트 자신이 지워졌다"가 근거가 되어 **순환한다.** 그리고 CHARTER §4.2 ③ 표에 적힌 대로 ③은 "현재 라벨링 레코드에는 필드 없음"이다. 실제 `evidence_text`도 "커밋이 X 패키지를 옮기며(커밋 제목) 그 패키지의 테스트인 이 함수가 함께 삭제됐다" 꼴이다. 이것은 §6.2.1 구별 검사를 통과하지 못하는 주제적 근접성과 같다.
- 다만 **테스트 레코드에서 "검증 대상이 같은 커밋에서 사라졌다"는 쓸모 있는 정보다** — 규칙 ②(무관해진 테스트 → DEAD)의 판정 재료가 바로 이것이다. 문제는 근거 목록에 그 칸이 없어서 ③을 빌려 쓰는 데 있다. → §9.4 안건 2.
- ⑤도 같은 문제를 갖는다. ⑤는 "같은 커밋의 다른 레코드가 명시된 이유를 갖고"인데, **label_cli는 레코드를 한 건씩만 보여 준다**(같은 커밋의 다른 레코드가 화면에 없다). I↔U의 ⑤ 5건(A08·A29 jh, C09·C24 sj, B39 hs)은 모두 커밋 전체 작업(재구성·일괄 삭제)을 들었고, 다른 레코드의 명시 이유를 인용한 것은 없다.

---

## 5. `anchored` 3건

| 건 | `record_id` | 저장소 · 함수 | `anchored` | 두 라벨 |
|---|---|---|---|---|
| A39 | `405e6cf7-9f36-54fd-b5fc-f093047d2140` | unslothai/unsloth · `_on_progress_update` | **jh** | 둘 다 DESIGN/INFERRED |
| A42 | `e925d450-7361-5d70-ba65-aae0d40001f1` | browser-use/browser-use · `close` | **jh** | 둘 다 DESIGN/INFERRED |
| A43 | `d7494074-0c98-5fc0-a099-dfa3a068aa3f` | pandas-dev/pandas · `test_dti_cmp_null_scalar_inequality` | **jh** | 둘 다 DESIGN/INFERRED |

- 셋 다 jh, 블록 A, 연속 구간(39~43)이다. 그래서 블록 A kappa 분모가 50이 아니라 47이다.
- 세 건이 일치 건이라 빼면 블록 A의 일치율이 내려간다(뺀 쪽이 보수적). 불일치 분석에는 영향이 없다.
- 세 `note` 모두 `anchored` 뒤에 판단 내용만 있고, 무엇에 끌렸는지(`reason.*` 노출인지, AI 보조 응답인지)는 적혀 있지 않다. 부록 A에는 없다(일치 건). AI 보조 의견을 보고 고른 것이 `anchored` 사유인지 팀이 정해야 한다 — 셋 다 같은 AI 보조 프롬프트를 썼다면 **AI 의견을 본 것 자체가 독립성을 깎는 정도는 세 사람이 같다.** `anchored`는 스스로 표시한 사람만 손해를 본다.

---

## 6. 화면에 있던 정보 (레코드 데이터로 확인한 사실)

| 항목 | 불일치 69건 | 비고 |
|---|---:|---|
| `replacement.code` 있음 | **3** (A05 0.7 · A18 0.9 · C11 0.9) | 셋 다 `SAME_LOCATION`. 나머지는 `NONE` 44 · `null` 22 |
| 같은 파일 추가 헝크 있음 | 25 | 0개인 건 44 |
| 추가 헝크에 삭제된 함수 이름이 다시 나옴 | 3 | A05·A18·C11 (= 대체 코드 3건) |
| 테스트 코드 (`is_test_code`) | 28 | |
| PR 연결 | 64 | PR 없는 5건: A29·A33·C29·C33·B39 |
| 호출자 변화 | **필드 없음** | `CALLER_CHANGE`는 미구현. 다른 파일의 호출부는 화면에 없다 |
| 테스트 변화 | **필드 없음** | 같은 파일 추가 헝크에 테스트 코드가 보일 때만 (A07·C25·C36·B26 등) |
| 같은 커밋의 다른 레코드 | **화면에 없음** | label_cli는 한 건씩 보여 준다. ⑤는 라벨러의 기억에 기댄다 |
| 감싸는 클래스 이름 | **필드 없음** | `function_signature`는 `def …` 한 줄이다. A37(`__add__`)·B45(`async_update`)는 어느 클래스의 메서드인지 화면만으로 모른다 |

**정리하면, I↔U 경계에 선 건들에서 ①·②·③·⑤를 화면으로 확인할 방법이 사실상 없었다.** 화면에서 확인 가능한 것은 ④(문장·추가 헝크가 공개 표면을 이름으로 말할 때)와 ⑥(본문)뿐이다. INFERRED를 단 쪽이 근거 번호를 적긴 했지만, 그 번호가 가리키는 정보가 화면에 없었다는 뜻이다.

---

## 7. 회의용 대표 사례 15건

유형마다 고르게, 같은 패턴이 여러 번 나온 것을 먼저 골랐다. 각 건의 두 라벨 원문은 부록 A.

| # | 건 | 유형 | 왜 이 건인가 | 같은 패턴 |
|---|---|---|---|---|
| 1 | **C19** langchain `embeddings` | `filter-miss 4` ↔ DESIGN/E | "다른 저장소로 이동"을 filter-miss로 볼지. hs는 같은 커밋 C48·B40에서 DEAD/I를 달아 **같은 사람도 갈렸다** | C01·C08·C16·C28·C48·B11·B18·B40·B43 (10건) |
| 2 | **B27** requests `selectors.__init__` | `filter-miss 3` ↔ LIB/E | 벤더링 제거. 같은 커밋 4건에서 **세 사람이 세 등급**(hs U · sj I · jh E) | C09·C24·B32 |
| 3 | **B29** docling `group_cell_elements` | `filter-miss 1` ↔ DESIGN/E | 같은 파일 추가 헝크에 거의 같은 본문이 있다. 화면으로 확인되는 유일한 이동 건. 커밋은 "slow table parsing"을 고치는데 이동 이유는 "for readability" | — |
| 4 | **A13** httpx `test_urlparse_invalid_ipv6` | E↔I (+이동 의심) | 문장이 "다른 파일로 옮긴다"고 말하지만 화면으로 확인 불가. 아무도 이동 여부를 판정하지 않았다 | A50·C14·C32 |
| 5 | **A36** vllm `get_num_mm_connector_tokens` | E↔I | "migrate X to Y" — 왜가 없는 문장을 EXPLICIT으로 볼지 (E1) | A03·A05·A24·A34·B50 |
| 6 | **A28** superset `parse_filters` | E↔I | 이유("같은 검증기를 15번 다시 선언") + 집합 경계가 문장에 있다. E2-(나)가 성립하는 쪽의 예 | — |
| 7 | **C17** crewAI `_summarize_messages` | E↔I | "Remove dead code … obsolete methods" — 가이드 §6.1.2 5번과 같은 형태인데 EXPLICIT이 달렸다 | B08 |
| 8 | **A37** crewAI `__add__` | E↔I | 이유 문장은 분명한데 이 메서드가 그 클래스 소속인지 **레코드에 클래스 이름이 없어** 확인 불가 | B45 |
| 9 | **A45** superset `pre_load` | I↔U | 대형 리팩터 커밋의 헬퍼, 화면에 아무것도 없음. ⑥ 0.5 대 UNKNOWN | A08·A46·C45 |
| 10 | **C29 / B39 / A33** openpilot `Remove old panda subtree` | I↔U | 한 줄 메시지, PR 없음. **jh가 A33은 I, B39는 U** — 같은 사람이 같은 메시지에서 갈림 | B36 |
| 11 | **C25** browser-use `fake_load_events` | I↔U | 추가 헝크 57개가 보이지만 무엇이 이어받았는지 특정되지 않음. "대량 추가 중 삭제" ③ 0.5 | C36 |
| 12 | **B16** vllm `test_http_chat_no_model_name_with_curl` | I↔U | 버그 수정 PR에서 지워진 테스트. 추가 헝크는 `import BadRequestError` 한 줄 — 이것이 근거인가 | B12 |
| 13 | **C33** crawl4ai `set_custom_headers` | I↔U | 파일명 `*.back.py`(백업 사본)가 ⑥ 근거인가. ⑥은 본문·함수명·시그니처만 말한다 | A29 (`*.current.py`) |
| 14 | **B07 / A01** 검증 대상이 사라진 테스트 | DEAD↔DESIGN | 대상이 리팩터로 사라졌을 때 테스트 삭제 이유를 DEAD로 볼지 대상 이유를 따를지 | A16·A19·B05·B26 (DEAD↔FEAT 포함) |
| 15 | **C18** django `fast_cache_clearing` | SEC↔DEAD (E/E) | 같은 문장, 다른 이유. CVE가 언급되지만 삭제 이유는 "우회가 불필요해짐" | C44·B45·B47 |

---

## 8. 참고: 등급을 "회수(E+I) vs UNKNOWN" 이진으로 본 kappa

> **판정 기준을 바꾸자는 것이 아니다.** §8.4.1 중간 점검 기준은 3등급 kappa 그대로다. 이 표는 "갈린 것이 E↔I(회수 여부와 무관)인지, 회수 경계인지"를 가르는 진단용이다.

| 블록 | 분모 | 3등급 kappa (`p_o`) | 이진 kappa (`p_o`, `p_e`) | 이진 교차표 (앞 사람 기준 R·U) |
|---|---:|---|---|---|
| A (sj+jh) | 47 | 0.385 (64%) | **0.644** (87%, 0.64) | RR 33 · UU 8 · RU 3 · UR 3 |
| B (jh+hs) | 50 | 0.582 (72%) | **0.464** (76%, 0.55) | RR 28 · UU 10 · RU 2 · UR 10 |
| C (hs+sj) | 50 | 0.220 (54%) | **0.122** (70%, 0.66) | RR 32 · UU 3 · RU 11 · UR 4 |

- `cohens_kappa()`를 그대로 썼다. 클래스 2개(R = EXPLICIT·INFERRED, U = UNKNOWN), `anchored` 제외는 3등급과 같다.
- **블록 A만 이진으로 올라간다.** A의 불일치는 E↔I가 주였다(11/17). B·C는 이진에서 **더 내려간다** — 불일치가 회수 경계(I↔U·E↔U)에 있고, R이 과반이라 `p_e`가 커지기 때문이다.
- 따라서 "E와 I를 합쳐 보면 괜찮다"는 B·C에 맞지 않는다. §15 이유 회수율 자체가 라벨러에 따라 흔들린다. 블록 C에서 hs는 회수(R) 43건, sj는 36건이다.

---

## 9. [변경 제안] 판정 체크리스트 초안

> **[변경 제안]** 이 절은 가이드를 바꾸지 않는다. 회의에서 채택하면 가이드 §6 보강 PR(v2.1)로 따로 올린다 (§8.4.1 2~3번). 질문 중 일부(9.1 F4, M3)는 가이드 해석을 하나로 고르는 것이라 회의 결정이 필요하다 — §9.4.

**목적.** 라벨러(또는 AI 보조)가 **예/아니오로만 답하고**, 등급과 INFERRED 신뢰도는 답의 조합에서 기계적으로 나오게 한다. 이유 라벨(8종)은 정하지 않는다 — 등급만 정한다.

**원칙.** 모든 질문은 **화면에 있는 필드**로 답한다. 화면에 없으면 "아니오"다(GitHub을 열어 답했으면 `off-record-evidence`).

### 9.1 질문

**F단계 — 배울 게 없는 삭제인가 (§6.3.3 1~4번).** 하나라도 "예"면 여기서 끝난다.

| 코드 | 질문 | 예이면 |
|---|---|---|
| F1 | 같은 파일 추가 헝크(또는 `replacement.code`)에 이 함수 본문이 **거의 그대로** 다른 이름·위치로 있나? | `filter-miss 1` |
| F3 | 경로가 벤더링·생성 위치(`packages/`·`vendor/`·`_vendor/`·`third_party/`·생성 파일 표시)이고, 문장이 벤더링·생성 코드를 지운다고 말하나? | `filter-miss 3` |
| F4 | 문장이 **다른 저장소로** 옮긴다(move/migrate/extract to + 저장소 이름)고 말하고, 이 파일 경로가 그 문장이 가리키는 디렉터리·패키지 안에 있나? | `filter-miss 4` [§9.4 안건 1] |
| Fx | 문장이 이 파일·함수를 **다른 파일로** 옮긴다고 말하는데, 같은 파일 추가 헝크로는 확인되지 않나? | **판정 보류** — `source_url` 확인 필요 (`off-record-evidence`) |

**E단계 — EXPLICIT인가 (§6.1.1을 질문으로 쪼갬).**

| 코드 | 질문 |
|---|---|
| E1 | 문장에 **이유어**가 있나? 원인·목적 연결(because · since · so that · to + 동사 · in favor of · now that · 때문에 · 위해) 또는 문제 서술(broken · bug · error · slow · unused · dead · obsolete · duplicate/re-declared · insecure · deprecated + 시점). "X를 제거/이동/교체/재작성/마이그레이션했다"만 있으면 **아니오**. 맨 `fix`·`cleanup`·`refactor`·`DEPR:`도 **아니오** |
| E2 | 그 문장이 이 함수·클래스·파일을 **이름으로** 가리키나? 아니면 문장에 경계가 있는 집합을 정의하고, 이 레코드가 **경로·함수명·시그니처·본문으로** 그 집합에 속함이 확인되나? (클래스 소속처럼 레코드에 없는 정보가 필요하면 **아니오**) |
| E3 | E2를 이름으로 통과했으면 자동 예. 집합으로 통과했으면, 그 집합이 "이 커밋이 바꾼 것 전부"보다 좁은가? |

**I단계 — 구조적 근거 (§6.2.1을 화면 기준으로 쪼갬).** 모든 신호는 **구별 검사**(근거 문장에서 함수 이름만 바꾸면 같은 커밋의 다른 삭제에도 맞나 → 맞으면 아니오)를 통과해야 "예"다.

| 코드 | 세기 | 질문 | 가이드 근거 |
|---|---|---|---|
| S1 | 강 | `replacement.code`가 있고, `replacement.confidence` ≥ 0.8이고, 그 코드가 삭제된 일을 이어받나? | ① (§6.2.2 상한 규칙) |
| S2 | 강 | (비테스트) 삭제된 본문·시그니처가 **문장이 제거·폐기 대상으로 이름을 댄 것**(모듈·클래스·함수)을 직접 쓰나? 또는 위험 패턴·`@skip`/`@xfail`·단정문 없음처럼 이유를 직접 드러내나? | ⑥ 직접 |
| M1 | 중 | `replacement.code`가 있지만 confidence < 0.8이거나, 같은 파일 추가 헝크에 **삭제된 일을 이어받는 코드**가 이름이 달라도 보이나? | ① (§2.1 추가 헝크) |
| M2 | 중 | 추가 헝크에 이 함수를 **부르던 자리**가 다른 호출로 바뀐 줄이 보이나? | ② |
| M3 | 중 | (테스트) 테스트 본문이 부르는 대상(함수·클래스·모듈)이나 테스트 파일명의 대상 이름을, 문장이 제거·폐기·교체 대상으로 **이름으로** 말하나? | ③ 역방향 [§9.4 안건 2] |
| M4 | 중 | 문장 또는 추가 헝크가 이 함수가 속한 공개 기능(통합·CLI·공개 API·설정·문서)의 제거·폐기를 **경로·모듈·클래스 이름과 일치하는 이름으로** 말하나? | ④ |
| M5 | 중 | 같은 커밋의 다른 레코드가 명시된 이유를 갖고, 이 레코드가 같은 패턴인가? **현재 화면에서는 항상 아니오** (§9.4 안건 4) | ⑤ |
| M6 | 중 | 삭제된 본문·함수명·시그니처가 이유의 **정황**을 보이나? (예: 호환 플래그 폴백, deprecated 경고 무시 데코레이터) | ⑥ 정황 |

### 9.2 규칙

```
F1·F3·F4 중 예 ≥ 1           → UNK + UNKNOWN + filter-miss N                (끝)
Fx = 예                        → 판정 보류, source_url 확인 후 F1부터 다시      (끝)
E1 ∧ E2 ∧ E3                  → EXPLICIT, confidence 1.0                     (끝)
S1 ∨ S2                        → INFERRED, confidence 0.85
M1~M6 중 예 ≥ 2                → INFERRED, confidence 0.7
M1~M6 중 예 = 1                → INFERRED, confidence 0.6
그 밖                          → UNK + UNKNOWN (원인 태그: E1 아니오 → vague-message,
                                  맥락 없음 → no-context, 대체 없음 → no-replacement,
                                  호출자 모름 → no-caller-info)
```

- **ADR-012 구간(0.5 / 0.8 경계)은 그대로다.** 구간 안의 값을 0.6 · 0.7 · 0.85 세 점으로 고정하자는 것이다. 지금 INFERRED가 일치한 58쌍의 신뢰도 차이는 평균 0.143이고, 27쌍이 0.15 이상 벌어진다 (sj 0.5~0.85, jh 0.5~0.95, hs 0.5~0.8 분포). kappa에는 안 잡히지만 §15 회수율 구간(≥0.5 / ≥0.8)을 가른다.
- **E2·E3 실패를 INFERRED로 보내지 않는다.** 가이드 §6.1.1 표는 "E2·E3 실패 → INFERRED"라고 쓰지만 §6.2.1은 "6종 중 하나를 못 대면 UNKNOWN"이다. 두 문장이 다르게 읽혀 I↔U가 생긴다 — sj의 ⑤·⑥ 0.5 라벨 일부가 이 경로다. 규칙은 §6.2.1을 따른다: E가 실패하면 I단계 신호가 있을 때만 INFERRED다 [§9.4 안건 3].

### 9.3 이번 등급 불일치 54건에 적용한 결과

> 질문의 답은 Claude가 레코드를 읽고 단 것이다. **정답이 아니라 "규칙을 이렇게 읽으면 이렇게 나온다"**이고, 회의 전 jh·hs가 자기 쪽 건을 다시 확인해야 한다. 이 문서는 sj 세션에서 만들었고 규칙은 가이드 v2의 좁은 해석을 옮긴 것이라, **sj 라벨과 많이 맞는 것은 규칙이 sj가 쓴 해석을 담았기 때문**이지 sj가 맞다는 근거가 아니다.

| 결과 | 건수 | 비율 |
|---|---:|---:|
| 규칙이 한쪽과 일치 | **42** | 78% |
| — sj와 일치 | 19 | |
| — jh와 일치 | 13 | |
| — hs와 일치 | 10 | |
| 규칙이 둘 다와 다름 | **3** | 6% |
| **규칙으로도 안 갈림** (질문 답이 정해지지 않음) | **9** | **17%** |

블록별:

| 블록 | 등급 불일치 | 앞 사람과 일치 | 뒤 사람과 일치 | 둘 다 다름 | 안 갈림 |
|---|---:|---:|---:|---:|---:|
| A (sj+jh) | 17 | sj 8 | jh 4 | 2 | 3 |
| B (jh+hs) | 14 | jh 9 | hs 4 | 0 | 1 |
| C (hs+sj) | 23 | hs 6 | sj 11 | 1 | 5 |

**건별 답** (예 = ○, 아니오 = ✕, 안 정해짐 = ?. I단계는 "예"인 신호만 적는다)

| 건 | F | E1 E2 E3 | I단계 "예" | 규칙 등급 | 일치 |
|---|---|---|---|---|---|
| A03 | ✕ | ✕ | M2 (추가 헝크 `gradient_checkpointing_func(layer_module.__call__`) | I 0.6 | sj |
| A05 | ✕ | ✕ | M1 (`replacement.code`, 0.7) | I 0.6 | sj |
| A07 | ✕ | ○ ✕ — | M1 (같은 파일에 새 테스트 `_capture_params` 등) | I 0.6 | jh |
| A08 | ✕ | ✕ | — | U | sj |
| A13 | **Fx** | | | **?** — 다른 파일로 이동이라고 말함 | — |
| A18 | ✕ | ✕ (맨 fix) | S1 (`code`, 0.9) | I 0.85 | sj |
| A24 | ✕ | ✕ | — (중첩 헬퍼, 구별 검사 실패) | U | **둘 다 다름** (sj I · jh E) |
| A28 | ✕ | ○ ○ ○ (집합: list 스키마의 중복 검증기) | | E | jh |
| A29 | ✕ | ✕ | M6? (파일명 `*.current.py`) | **?** — 안건 5 | — |
| A33 | ✕ | ✕ | — (서브트리 전체, 구별 검사 실패) | U | sj |
| A34 | ✕ | ✕ ("has been removed") | M4 (`aladdin_connect` 통합 제거) | I 0.6 | sj |
| A36 | ✕ | ✕ ("migrate … to") | M1 (추가 헝크 `get_mm_lora_token_counts`가 같은 계산) | I 0.6 | sj |
| A37 | ✕ | ○ ✕ (클래스 소속 확인 불가) — | — | U | **둘 다 다름** (sj I · jh E) |
| A40 | ✕ | ○ ✕ — | M1 (추가 `__init__`이 `get_embedding_function`으로 embedder 처리) | I 0.6 | sj |
| A45 | ✕ | ✕ | — | U | jh |
| A46 | ✕ | ✕ | — | U | jh |
| A50 | **Fx** (v4 → 새 파일 이름 변경) | | (아니면 M4: 추가 헝크 FutureWarning) | **?** | — |
| C01 | F4 | | | U + fm4 | sj |
| C02 | ✕ | ○ ✕ (shim 집합 ≠ 테스트 스텁) — | M6 (시그니처에 `max_length` 없음) | I 0.6 | sj |
| C04 | ✕ | ○ ○ ○ ("bitblas" 이름) | | E | hs |
| C06 | ✕ | ○ ? — (`count_right`가 DocTags 역직렬화 소속인지 본문으로 확정 못 함) | M1 | **?** (E 또는 I 0.6) | — |
| C08 | F4 | | | U + fm4 | sj |
| C09 | F3 | | | U + fm3 | hs |
| C11 | ✕ | ○ ○ ○ ("tool-related methods" in crew.py, 대체 코드 반환형으로 소속 확인) | | E | hs |
| C13 | ✕ | ○ ○ ○ (`PydanticSchemaParser` = 파일 이름) | | E | hs |
| C14 | ✕ (화면 기준) | ✕ | — | U | sj (sj는 fm1, 등급은 같음) |
| C15 | ✕ | ○ ? — (리뷰 코멘트는 `pyproject.toml`에 달림) | M4 (추가 헝크: `setup.py install` 지원 중단) | **?** (E 또는 I 0.6) | — |
| C16 | **?** ("in favor of"·"superseded"와 "this moved"가 함께 있음) | | | **?** (fm4 또는 E) | — |
| C17 | ✕ | ○ ✕ (§6.1.2 5번과 같음) — | — | U | **둘 다 다름** (hs E · sj I) |
| C19 | F4 | | | U + fm4 | sj |
| C24 | F3 | | | U + fm3 | hs |
| C25 | ✕ | ✕ | — (57헝크 중 이어받는 코드 특정 안 됨) | U | sj |
| C28 | F4 | | | U + fm4 | sj |
| C29 | ✕ | ✕ | — | U | sj |
| C30 | ✕ | ○ ✕ — | S2 (시그니처 `viz_obj: BaseViz` — 문장이 제거한다는 `superset/viz.py`) | I 0.85 | sj |
| C32 | **Fx** (리뷰: 대상이 `sql_parse`로 이동) | | (아니면 M3) | **?** | — |
| C33 | ✕ | ✕ | M6? (파일명 `*.back.py`) | **?** — 안건 5 | — |
| C36 | ✕ | ✕ | — | U | sj |
| C45 | ✕ | ✕ | — | U | hs |
| C48 | F4 | | | U + fm4 | sj |
| B08 | ✕ | ○ ✕ (목록에 `reset` 없음) — | S2 (본문이 부르는 `async_reset`을 문장이 제거 목록에 이름으로 넣음) | I 0.85 | hs |
| B11 | F4 | | | U + fm4 | jh |
| B12 | ✕ | ✕ | — | U | jh |
| B16 | ✕ | ○ ✕ — | — (`import BadRequestError` 한 줄, 구별 검사 실패) | U | jh |
| B18 | F4 | | | U + fm4 | jh |
| B20 | ✕ | ✕ | M1? (트리거 대신 identity 시퀀스 조회 — 이 함수를 이어받는지 특정 못 함) | **?** (I 0.6 또는 U) | — |
| B27 | F3 | | | U + fm3 | hs |
| B29 | F1 (`_group_cell_elements`) | | | U + fm1 | jh |
| B32 | F3 | | | U + fm3 | hs |
| B37 | ✕ | ○ ✕ (새 파일 소속 확인 불가) — | — | U | jh |
| B39 | ✕ | ✕ | — | U | jh |
| B40 | F4 | | | U + fm4 | jh |
| B43 | F4 | | | U + fm4 | jh |
| B50 | ✕ | ✕ ("replace with `__call__`") | M2 (호출부 `gradient_checkpointing_func(encoder_layer.__call__`) | I 0.6 | hs |

**안 갈린 9건의 원인**

| 원인 | 건 | 무엇이 필요한가 |
|---|---|---|
| 다른 파일로 옮겼다는데 화면에서 확인 불가 (Fx) | A13 · A50 · C32 | `source_url` 확인. 또는 다른 파일 추가 헝크를 화면에 넣기 |
| E2 집합 소속을 본문으로 확정 못 함 | C06 · C15 | 소속 판정을 "본문에 집합 이름이 나오나"로 더 좁힐지 |
| 파일명(백업·스냅숏 사본)이 ⑥인가 | A29 · C33 | 안건 5 |
| "다른 저장소로 대체"가 4번 분리인가 | C16 | 안건 1 |
| 추가 헝크가 이어받는 코드인지 특정 못 함 | B20 | M1 판정에 "삭제 함수의 이름·인자·반환값 중 하나가 추가 헝크에 이어져야 한다" 같은 기준 |

**둘 다와 다른 3건**(A24·A37·C17)은 규칙이 UNKNOWN을 내고 두 사람은 모두 회수(E 또는 I)를 낸 경우다. 두 사람이 이유에는 동의한다(A24·A37 DESIGN, C17 DEAD). **규칙이 사람보다 엄격한 지점**이라 회의에서 따로 본다 — 특히 A37은 레코드에 클래스 이름이 없어서 생긴 것이라 데이터 쪽 문제다(§6 마지막 행).

**정리.** 규칙을 쓰면 54건 중 45건(83%)은 질문 답만 맞추면 등급이 하나로 정해진다. 9건(17%)은 질문의 답 자체가 정해지지 않는다. 다만 이 83%는 **질문 답을 한 사람(Claude)이 단 것**이라, 두 사람이 따로 답하면 질문 수준에서 다시 갈릴 수 있다 — 체크리스트가 kappa를 올리는지는 재라벨로만 안다.

### 9.4 회의에서 정할 것

| # | 안건 | 걸린 건 | 선택지 |
|---|---|---|---|
| 1 | **§6.3.3 4번(다른 저장소로 분리)을 문장만으로 판정해도 되나** | 10건 (+C16) | (가) 문장이 옮길 저장소를 말하고 경로가 그 안에 있으면 `filter-miss 4` (F4안) / (나) 새 저장소에 파일이 있는지 `source_url`·대상 저장소로 확인해야 함 / (다) 4번은 이유 라벨(DESIGN)로 둔다 |
| 2 | **테스트 레코드에서 "검증 대상이 같은 커밋에서 사라졌다"를 근거로 인정하나** | ③ 테스트 71회, I↔U 14건 | (가) M3처럼 대상 **이름**이 문장에 있을 때만 인정 (③의 역방향으로 명시) / (나) 인정하지 않음 — ③은 비테스트 함수 전용 / 어느 쪽이든 **ADR-017 6종 목록의 해석**이라 §13 절차인지 먼저 정한다 |
| 3 | **E2·E3 실패 시 기본값** | sj의 ⑤·⑥ 0.5 라벨 등 | §6.1.1 표("INFERRED")와 §6.2.1("6종 없으면 UNKNOWN")을 하나로. 제안: §6.2.1을 따르고 §6.1.1 표에 "단, §6.2.1 근거가 있을 때"를 붙인다 |
| 4 | **⑤를 화면에서 쓸 수 있게 할지** | ⑤ 27회 | label_cli에 같은 커밋의 다른 레코드 목록을 보여 주거나, 지금처럼 화면 밖이면 ⑤를 쓰지 않는다 |
| 5 | **경로(파일명) 표시가 ⑥인가** — `*.back.py`·`*.current.py` | A29 · C33 | ⑥에 "파일 경로"를 넣을지 (ADR-017 해석) |
| 6 | **E1을 이유어 목록으로 기계화할지** | E↔I (가)형 8건 | 목록을 가이드에 둘지, 예시만 둘지 |
| 7 | **INFERRED 신뢰도를 0.6 · 0.7 · 0.85 세 점으로 고정할지** | I-I 58쌍 중 27쌍이 0.15 이상 차이 | ADR-012 구간 안의 운용 규칙이라 ADR 변경은 아니다 |
| 8 | **테스트 삭제의 이유 라벨** — 대상이 리팩터·폐기됐을 때 DEAD(규칙 ②)인가, 대상의 이유를 따르나 | 이유만 갈린 테스트 9건 | §5.1 규칙 ② 확장 |
| 9 | **AI 보조 의견을 본 것이 `anchored` 사유인가** | A39·A42·A43 (jh) | 셋 다 같은 프롬프트를 썼으면 표시 여부가 사람마다 달라지지 않게 정한다 |

이 표의 1·2·3·5는 체크리스트가 기계적이 되기 전에 정해야 하는 것이다. 나머지는 체크리스트 없이도 가이드 보강 재료다.

---

## 부록 A. 불일치 69건 — 두 라벨 원문 나란히

`evidence_text`·`note`는 라벨 파일 원문 그대로다(줄바꿈만 공백으로, `|`는 이스케이프). 블록 안은 `block_position` 순이다. 라벨러 순서는 배분 파일의 `labelers` 순(A: sj·jh, B: jh·hs, C: hs·sj).

### 블록 A

#### A01 · `92620e7f-e463-555a-8a31-d9ed0311d2c5` · apache/superset · `test_adds_limit_to_select`

- 파일 `tests/unit_tests/mcp_service/sql_lab/test_sql_lab_utils.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #36739 · 이슈 1개 · 리뷰 코멘트 20개
- 커밋 첫 줄: refactor: Migrates the MCP `execute_sql` tool to use the SQL execution API (#36739)

| | sj | jh |
|---|---|---|
| reason_label | DEAD | DESIGN |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.75 | 0.7 |
| evidence_locator | pr:#36739#body | diff:replacement |
| evidence_text | ③ 테스트 변화 — 테스트 대상인 sql_lab_utils.py가 PR에서 삭제되어(Database.execute()로 대체) 그 모듈의 _apply_limit 테스트도 함께 삭제됨 | ③ — The test directly exercised _apply_limit, while this commit removes the old sql_lab_utils execution structure and the test disappears with it. |
| note | Test target module removal is stated in pr_body; whether the whole test file was deleted is not verified | no-replacement The deleted test has no replacement match in the provided context. |

#### A03 · `46aa2a72-b241-5189-9d6f-c6573329657c` · huggingface/transformers · `custom_forward`

- 파일 `src/transformers/models/vilt/modeling_vilt.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 4개 (6줄) · PR #27020 · 이슈 1개 · 리뷰 코멘트 7개
- 커밋 첫 줄: [`core`] Refactor of `gradient_checkpointing` (#27020)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.6 | 1.0 |
| evidence_locator | commit:message | commit:message |
| evidence_text | ⑤ 같은 커밋의 동형 삭제 — 커밋 메시지가 모든 create_custom_forward 제거를 말하고, 이 함수는 그 클로저 래퍼 중 하나 | remove all remaining `create_custom_forward` methods |
| note | multi-reason Closure removed in a repo-wide GC refactor; PR body also mentions DDP fixes, not verified whether this removal was part of that fix | 같은 gradient checkpointing 리팩터링에서 custom_forward 래퍼를 제거하고 __call__ 직접 전달 구조로 변경함. |

#### A05 · `e01eaafa-d05a-51d3-9e59-898dc88baf9c` · docling-project/docling · `get_segmented_page`

- 파일 `docling/backend/pypdfium2_backend.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 있음 · match_method `SAME_LOCATION` · confidence 0.7
- 같은 파일 추가 헝크 8개 (127줄) · PR #1745 · 이슈 1개 · 리뷰 코멘트 4개
- 커밋 첫 줄: feat: Make Page.parsed_page the only source of truth for text cells, add OCR cells to it (#1745)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.7 | 1.0 |
| evidence_locator | diff:replacement | pr:#1745#body |
| evidence_text | ① 대체 코드 — 같은 위치에서 None만 반환하던 스텁이 _compute_text_cells()와 get_pdf_page_geometry()로 SegmentedPdfPage를 만드는 구현으로 교체됨 | All backends now implement get_segmented_page() and create SegmentedPdfPage objects |
| note | needs-discussion Stub replaced by same-signature implementation, not clearly a removal; how other backends handled it is not verified | 기존 None 반환 stub을 실제 SegmentedPdfPage 생성 구현으로 바꾸며 backend 구조를 통일함. |

#### A07 · `02aa646a-bea8-56b0-8955-f66c49c256f6` · docling-project/docling · `test_rapidocr_default_models_use_current_default_assets`

- 파일 `tests/test_rapid_ocr_model.py` · 테스트 코드 예 · 불일치: 등급 E↔I, 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 15개 (37줄) · PR #3863 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: feat: Refactor RapidOcrModel to support all PP-OCR languages via version/backbone resolution (#3863)

| | sj | jh |
|---|---|---|
| reason_label | DEAD | DESIGN |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.75 |
| evidence_locator | pr:#3863#body | diff:replacement |
| evidence_text | Previously the model shipped hand-maintained per-language, per-backend model-path tables that were hard to extend and effectively capped support at a couple of languages. | ③ — The old test validated fixed _default_models asset paths, while the refactor replaces those static tables with resolver-based model selection and adds tests for the resolved RapidOCR parameters. |
| note | needs-discussion Test targeted the hand-maintained _default_models tables replaced by a resolver; actual deletion of _default_models is not verified | 기존 고정 모델 경로 테이블 검증이 resolver 기반 구조 검증으로 바뀌면서 테스트가 재구성됨. |

#### A08 · `511c845c-43b8-5bf8-9045-9214ed25d5f8` · docling-project/docling · `contains`

- 파일 `docling/utils/layout_utils.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #530 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: feat: Updated Layout processing with forms and key-value areas (#530)

| | sj | jh |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.6 |
| evidence_locator | — | commit:message |
| evidence_text | — | ⑤ — The commit broadly restructures layout postprocessing and hierarchical cluster processing, and this bbox containment helper is removed during that redesign. |
| note | no-context no-replacement no-caller-info Large layout-processing overhaul commit; whether this function belonged to the old code sent to ERZ is not verified | no-replacement no-caller-info 대체 구현과 호출자 변화가 화면에 없어 구조 개편 과정에서 제거된 것으로만 추론 가능함. |

#### A13 · `1edfec7b-c069-579a-ac36-0ff746fc540c` · encode/httpx · `test_urlparse_invalid_ipv6`

- 파일 `tests/test_urlparse.py` · 테스트 코드 예 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #2982 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Moving test cases into 'test_url.py' (#2982)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.6 | 1.0 |
| evidence_locator | pr:#2982#body | pr:#2982#body |
| evidence_text | ③ 테스트 변화 — PR 본문이 tests/test_urlparse.py의 테스트 케이스를 tests/models/test_url.py로 옮긴다고 서술하고, 삭제된 테스트가 그 대상 중 하나 | Move test cases from `tests/test_urlparse.py` into `tests/models/test_url.py`. |
| note | Test relocation PR; whether this exact test survives unchanged in test_url.py is not verified | 기존 URL parsing 테스트를 tests/models/test_url.py로 이동하고 invalid URL 케이스를 통합함. |

#### A16 · `6b8771dd-8114-57cb-acee-0ab85c58a59a` · scikit-learn/scikit-learn · `test_invalid_classification_loss`

- 파일 `sklearn/ensemble/_hist_gradient_boosting/tests/test_gradient_boosting.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔FEAT
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #25834 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: MAINT Clean deprecated losses in (hist) gradient boosting for 1.3 (#25834)

| | sj | jh |
|---|---|---|
| reason_label | DEAD | FEAT |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.75 | 0.7 |
| evidence_locator | commit:message | commit:message |
| evidence_text | ③ 테스트 변화 — 삭제된 테스트가 FutureWarning을 무시하며 deprecated 문자열 loss="binary_crossentropy"를 직접 검증하고, 커밋 제목이 deprecated losses 정리를 서술 | ④ — The deleted test exercises the deprecated "binary_crossentropy" loss option, while this commit removes deprecated losses from histogram gradient boosting. |
| note | Test targeted a deprecated loss option; whether the option itself was removed in this commit is not verified | needs-discussion deprecated 사용자 옵션 제거로 해당 테스트도 사라진 것으로 보이나, 테스트 특례의 DEAD와 FEAT 경계는 기준에 완전히 명시되지 않음. |

#### A18 · `d276677d-cea7-50ff-93ad-4cd4990d678d` · commaai/openpilot · `get`

- 파일 `selfdrive/loggerd/tests/loggerd_tests_common.py` · 테스트 코드 예 · 불일치: 등급 E↔I
- `replacement`: code 있음 · match_method `SAME_LOCATION` · confidence 0.9
- 같은 파일 추가 헝크 1개 (11줄) · PR #20611 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: add putBool/getBool wrappers to cython params class (#20611)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.8 | 1.0 |
| evidence_locator | diff:replacement | commit:message |
| evidence_text | ① 대체 코드 — 같은 위치의 get(self, k, block=False, encoding=None)이 이어받았고, 커밋 메시지가 새 API 사용과 mockparams 인자 수정을 서술 | fix mockparams arguments |
| note | Same-location replacement extends the mock signature to match the real Params API; the real Params.get signature is not verified | 기존 MockParams.get을 새 Params API 인자 형태에 맞게 확장하고 get_bool을 추가함. |

#### A19 · `69cc961f-88ac-5bd4-8320-a1b29e95a244` · huggingface/transformers · `test_end_to_end_thresh_16M`

- 파일 `tests/test_hf_api.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔FEAT
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #13100 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Rely on huggingface_hub for common tools (#13100)

| | sj | jh |
|---|---|---|
| reason_label | DEAD | FEAT |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.75 | 0.65 |
| evidence_locator | pr:#13100#body | pr:#13100#body |
| evidence_text | ③ 테스트 변화 — 삭제된 테스트가 hf_api의 create_repo와 transformers-cli LFS 명령에 직접 의존하고, PR 본문이 hf_api 모듈 제거와 해당 CLI deprecate를 서술 | ④ — The deleted test exercises a transformers-cli LFS command, while this PR deprecates CLI functionality tied to the removed hf_api module and moves common tooling to huggingface_hub. |
| note | Test target module hf_api removed per PR body; whether the whole test file was deleted is not verified | no-replacement 해당 lfs-enable-largefiles 명령 자체가 같은 커밋에서 제거됐는지는 화면에서 직접 확인되지 않음. |

#### A24 · `03049cc5-6b9c-5366-9f3b-92d32a127c30` · django/django · `__init__`

- 파일 `tests/forms_tests/tests/test_widgets.py` · 테스트 코드 예 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 3개 (3줄) · PR #5209 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: Rewrote form widget tests as proper unittests.

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.6 | 1.0 |
| evidence_locator | commit:message | commit:message |
| evidence_text | ⑥ 삭제된 코드 자체 — 테스트 안에 중첩 정의된 헬퍼 위젯이고, 커밋 메시지가 doctest 방식 위젯 테스트를 단위 테스트로 재작성한다고 서술 | Rewrote form widget tests as proper unittests. |
| note | Whether ComplexMultiWidget was redefined in the new widget_tests files is not verified | 기존 form widget 테스트 구조를 doctest 계열에서 proper unittest 구조로 재작성하면서 테스트 내부 helper 초기화 코드가 제거됨. |

#### A26 · `b9993312-7a83-5c18-80b5-0b597be8a623` · django/django · `itervalues`

- 파일 `django/utils/six.py` · 테스트 코드 아니오 · 불일치: 이유 DEAD↔FEAT
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #10935 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Refs #27753 -- Removed django.utils.six.

| | sj | jh |
|---|---|---|
| reason_label | DEAD | FEAT |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.55 | 0.75 |
| evidence_locator | pr:#10935#title | pr:#10935#title |
| evidence_text | ⑥ 삭제된 코드 자체 — d.values()를 감싼 Python 2/3 호환 래퍼이고, PR 제목이 private Python 2 compatibility APIs 제거를 서술 | ④ — This function is part of django.utils.six, and the commit removes the private Python 2 compatibility APIs provided by that module. |
| note | Whether six.py was deleted entirely and its callers were removed in this commit is not verified | no-replacement Python 2 호환 API 자체를 제거하는 변경이며, 이 helper를 대신하는 Django 내부 대체 구현은 화면에서 확인되지 않음. |

#### A28 · `cd719847-bd4c-5949-9955-ec596b1a2b9f` · apache/superset · `parse_filters`

- 파일 `superset/mcp_service/task/schemas.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 4개 (6줄) · PR #41923 · 이슈 0개 · 리뷰 코멘트 13개
- 커밋 첫 줄: refactor(mcp): dedupe list-tool schemas and delete dead middleware (#41923)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.75 | 1.0 |
| evidence_locator | pr:#41923#body | pr:#41923#body |
| evidence_text | ⑤ 같은 커밋의 동형 삭제 — PR이 15개 list 스키마의 중복 검증기를 PaginatedListRequest 기반 클래스로 올렸고, 이 파일도 ListTasksRequest가 PaginatedListRequest[TaskColumnFilter]를 상속하도록 바뀜 | Every `list_*` tool's request/response schema pair re-declared the same 7 filter/pagination fields, the same 2 field validators, and the same 13-field response envelope |
| note | Shared validators moved into a generic base class; the base-class validator code is not visible in this screen, not verified | task 스키마의 중복 filters validator를 공통 PaginatedListRequest 기반으로 올려 동일 동작을 유지하면서 구조를 통합함. |

#### A29 · `53c0d77f-5e10-5056-8912-feceaea49f0f` · unclecode/crawl4ai · `get_delayed_content`

- 파일 `crawl4ai/async_crawler_strategy.current.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR 없음 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Enhance AsyncWebCrawler and related configurations

| | sj | jh |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.6 |
| evidence_locator | — | commit:message |
| evidence_text | — | ⑤ — The crawler strategy and configuration flow are refactored to reduce legacy parameters, and this internal delayed-content helper is removed during that restructuring. |
| note | no-context vague-message Snapshot file (.current.py) deleted in a large config refactor commit that does not mention this function; whether the original file keeps it not verified | no-replacement no-caller-info 대체 구현과 호출자 변화가 화면에 없어 리팩터링 과정에서 제거된 것으로만 추론 가능함. |

#### A33 · `e94df97b-880d-50a9-8c38-c21703b7dc2b` · commaai/openpilot · `test_disable_control_allowed_from_cruise`

- 파일 `panda/tests/safety/test_toyota.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR 없음 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Remove old panda subtree

| | sj | jh |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.7 |
| evidence_locator | — | commit:message |
| evidence_text | — | ③ — This test belongs to the old panda subtree, which is removed by the commit, so the Toyota safety behavior it tested is no longer part of that subtree. |
| note | no-context vague-message Whole panda subtree removed by a one-line commit message; whether panda moved to a separate repo or submodule is not verified | 테스트 대상이던 old panda subtree 자체가 제거되면서 해당 Toyota safety 테스트도 더 이상 필요 없어져 삭제된 것으로 판단함. |

#### A34 · `dbee26cc-3540-5c5f-838e-73c0d5a5c7b0` · home-assistant/core · `__init__`

- 파일 `homeassistant/components/aladdin_connect/api.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #120980 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Remove Aladdin Connect integration (#120980)

| | sj | jh |
|---|---|---|
| reason_label | FEAT | FEAT |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.7 | 1.0 |
| evidence_locator | pr:#120980#body | pr:#120980#body |
| evidence_text | ④ 공개 표면 변화 — PR이 Aladdin Connect 통합 전체를 제거하고 Breaking change로 표시하며, 이 생성자는 그 통합의 인증 클래스 | Aladdin Connect has been removed. Please remove the leftover entries from your system. |
| note | Whole integration removed with a breaking-change notice; the reason for removal is not stated, not verified | Aladdin Connect 통합 자체가 제거되면서 해당 API 초기화 코드도 함께 삭제됨. |

#### A36 · `d3fcea70-6a07-59db-b4c1-a911036acdfd` · vllm-project/vllm · `get_num_mm_connector_tokens`

- 파일 `vllm/model_executor/models/internvl.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 2개 (15줄) · PR #55071 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: [LoRA][Refactor] Unify multimodal LoRA token count hooks (#55071)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.75 | 1.0 |
| evidence_locator | pr:#55071#body | pr:#55071#body |
| evidence_text | ⑤ 같은 커밋의 동형 삭제 — PR이 내장 모델의 레거시 분리 훅을 get_mm_lora_token_counts로 마이그레이션하고, 이 파일에 같은 계산을 이어받는 새 메서드가 추가됨 | This PR migrate built-in model token-count implementations to `get_mm_lora_token_counts()`. |
| note | Legacy hook replaced by unified hook with an embeds-based calculation; E2/E3 boundary | multi-reason 기존 connector token hook을 통합 get_mm_lora_token_counts 구조로 이관한 리팩터링이며, legacy hook 호환성 유지 문제는 별도 리뷰에서 지적됨. |

#### A37 · `ccbdb405-7ee7-50a5-b053-b7d725a9c3cf` · crewAIInc/crewAI · `__add__`

- 파일 `lib/crewai/src/crewai/flow/runtime/__init__.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 2개 (4줄) · PR #6327 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: Remove `StateProxy` from flow state access (#6327)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.75 | 1.0 |
| evidence_locator | pr:#6327#body | commit:message |
| evidence_text | ⑤ 같은 커밋의 동형 삭제 — 커밋이 StateProxy/LockedDictProxy/LockedListProxy 계층을 통째로 제거하고 flow.state가 기반 객체를 직접 반환하도록 바꿨으며, 이 메서드는 그 일부 | This commit decided to remove it completely for simplicity and performance: |
| note | multi-reason PR states both simplicity and performance as reasons; class membership of __add__ (LockedListProxy) is not verified | multi-reason StateProxy/LockedListProxy 계층 자체를 제거해 raw state를 직접 노출하는 구조로 단순화했으며, 성능 개선도 함께 명시됨. |

#### A40 · `362bda69-4859-5670-9da9-02d4ffbde30a` · crewAIInc/crewAI · `_set_embedder_config`

- 파일 `src/crewai/knowledge/storage/knowledge_storage.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 15개 (79줄) · PR #3455 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: refactor: unify rag storage with instance-specific client support (#3455)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | INFERRED | EXPLICIT |
| confidence | 0.75 | 1.0 |
| evidence_locator | pr:#3455#body | pr:#3455#body |
| evidence_text | ⑥ 삭제된 코드 자체 — embedder 설정 메서드가 같은 커밋의 __init__에서 get_embedding_function과 create_client로 인스턴스 전용 클라이언트를 만드는 방식으로 이어받아지고, PR 본문이 인스턴스별 embedder 설정 지원을 서술 | refactors rag storage to support instance-specific configurations, allowing multiple knowledge/memory instances with different embedder configs to coexist without global state conflicts. |
| note | Embedder config folded into instance-specific client creation; E2/E3 boundary | embedding 설정 책임을 기존 helper 중심 구조에서 instance-specific RAG client 생성 구조로 옮긴 리팩터링임. |

#### A45 · `63f216a4-1245-5cbf-bc78-6c86eb887602` · apache/superset · `pre_load`

- 파일 `superset/views/dashboard/api.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #9315 · 이슈 0개 · 리뷰 코멘트 6개
- 커밋 첫 줄: [dashboard] Refactor API using SIP-35 (#9315)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | pr:#9315#body | — |
| evidence_text | ⑥ 삭제된 코드 자체 — slug/owners 정규화 pre_load 훅이고, PR이 대시보드 API 전체를 SIP-35 구조로 리팩터한다고 서술 | — |
| note | Whether the normalization was moved to a new schema file is not verified | no-replacement no-caller-info SIP-35 기반 Dashboard API 리팩터링 중 삭제됐지만, 이 pre_load 로직의 직접 대체 코드나 호출자 변화가 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. |

#### A46 · `891efad6-aea8-5d41-ac02-f00f82f12616` · browser-use/browser-use · `_switch_agent_focus_to_tab`

- 파일 `browser_use/browser/navigation_watchdog.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #2573 · 이슈 0개 · 리뷰 코멘트 37개
- 커밋 첫 줄: feature flags for iframes and recent_events, split security and nav actions

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | pr:#2573#body | — |
| evidence_text | ⑥ 삭제된 코드 자체 — 탭 포커스 전환 헬퍼이고, 커밋 메시지가 navigation 액션 분리를 서술하며 PR이 CDP 기반 전면 재작성을 서술 | — |
| note | Whether tab focus switching was redefined in another watchdog is not verified | no-replacement no-caller-info navigation action 분리 과정에서 삭제된 것으로 보이지만, 이 tab focus helper의 직접 대체 코드나 호출자 변화가 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. |

#### A50 · `c3012ee0-ad49-5449-9f9c-b16bd8917142` · docling-project/docling · `_ensure_parsed`

- 파일 `docling/backend/docling_parse_v4_backend.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 6개 (10줄) · PR #2872 · 이슈 0개 · 리뷰 코멘트 8개
- 커밋 첫 줄: feat: Introduce docling-parse v5 and deprecate old docling-parse backends (#2872)

| | sj | jh |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.65 | 0.0 |
| evidence_locator | pr:#2872#body | — |
| evidence_text | ④ 공개 표면 변화 — V4 백엔드가 통합된 DoclingParseDocumentBackend를 상속하는 FutureWarning 스텁으로 바뀌고, PR 본문이 파일·클래스 이름 변경과 백엔드 통합을 서술 | — |
| note | Renamed-and-consolidated backend; whether _ensure_parsed survives unchanged in the new docling_parse_backend.py is not verified | filter-miss 1 — docling_parse_v4_backend.py와 DoclingParseV4* 구현이 새 canonical docling_parse_backend.py / DoclingParse* 이름으로 이동·리네임된 변경으로, 기능 삭제가 아님. |


### 블록 B

#### B05 · `de2ada3d-8cc1-56a9-8216-3debab9bb33d` · vllm-project/vllm · `test_reserve_mm_ipc_gpu_memory_scales_pynvvideocodec_budget_by_api_servers`

- 파일 `tests/v1/worker/test_gpu_worker.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #49322 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: [Misc] Move PyNvVideoCodec stuff out of gpu worker (#49322)

| | jh | hs |
|---|---|---|
| reason_label | DEAD | DESIGN |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.75 | 0.5 |
| evidence_locator | commit:message | diff:replacement |
| evidence_text | ③ — PyNvVideoCodec-specific GPU memory budgeting was moved out of `gpu_worker`, making this gpu-worker-specific test irrelevant in its original location. | ③ 테스트 변화 — 이 커밋이 PyNvVideoCodec 코드를 gpu worker 밖으로 옮겼고, 그 코드를 검증하던 이 테스트가 같은 파일에서 삭제됐다. |
| note | PyNvVideoCodec 관련 책임이 gpu_worker 밖으로 이동하면서 해당 worker 내부 동작을 검증하던 테스트가 무관해진 것으로 판단함. |  |

#### B07 · `5ddcf3b6-0033-5964-8ce9-168b8170a821` · crewAIInc/crewAI · `handle_cancelled`

- 파일 `lib/crewai/tests/test_flow_serializer.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #6017 · 이슈 0개 · 리뷰 코멘트 43개
- 커밋 첫 줄: Build FlowDefinition from Flow DSL metadata (#6017)

| | jh | hs |
|---|---|---|
| reason_label | DEAD | DESIGN |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.85 | 0.7 |
| evidence_locator | pr:#6017#body | diff:replacement |
| evidence_text | ③ — The obsolete flow serializer implementation and its related serializer tests were removed as FlowDefinition replaced the old introspection/serialization structure. | ③ 테스트 변화 — 이 테스트가 검증하던 flow_serializer.py가 같은 커밋에서 삭제되고 FlowDefinition 구조로 대체되면서 테스트의 Flow 정의가 함께 삭제됐다. |
| note | 기존 flow_serializer가 obsolete로 제거되면서 해당 serializer 테스트 내부의 listener helper도 더 이상 필요 없어져 삭제된 것으로 판단함. |  |

#### B08 · `a48582a5-1d09-50ab-afe0-1f211d3a2490` · home-assistant/core · `reset`

- 파일 `homeassistant/helpers/entity_component.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 9개 (30줄) · PR #12237 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: Entity layer cleanup (#12237)

| | jh | hs |
|---|---|---|
| reason_label | DEAD | DEAD |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.6 |
| evidence_locator | pr:#12237#body | diff:replacement |
| evidence_text | It removes a few methods from EntityComponent that are not in use and should not be used. | ⑤ 같은 커밋의 동형 삭제 — 같은 커밋이 EntityComponent의 안 쓰는 메서드들(extract_from_service, prepare_reload 등)을 함께 제거했고, reset()은 async_reset이 비공개(_async_reset)로 바뀌며 남은 동기 래퍼다. |
| note | priority-rule 기존 공개 reset 계층이 사용되지 않는 메서드로 명시되어 제거됐고, 내부 reset 로직은 _async_reset으로 유지됨. |  |

#### B11 · `d7179e0e-b4b4-5779-ba4a-42792204c657` · langchain-ai/langchain · `test_default_params`

- 파일 `libs/partners/cohere/tests/unit_tests/test_chat_models.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #20081 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: cohere: move package to external repo (#20081)

| | jh | hs |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.5 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ③ 테스트 변화 — 이 커밋이 cohere 패키지를 저장소에서 제거했고(커밋 제목), 그 패키지의 ChatCohere를 검증하던 이 테스트가 함께 삭제됐다. |
| note | filter-miss 4 — Cohere 패키지와 관련 테스트가 외부 저장소로 분리·이동된 변경으로, 기능 삭제가 아님. |  |

#### B12 · `8c31ff4d-395a-5609-a797-d9991f628adc` · browser-use/browser-use · `test_save_conversation_deep_directory`

- 파일 `tests/test_save_conversation.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #1973 · 이슈 0개 · 리뷰 코멘트 3개
- 커밋 첫 줄: centralize LLM mocking for tests into mocks.py

| | jh | hs |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.5 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ③ 테스트 변화 — 커밋이 테스트용 LLM mocking을 mocks.py로 중앙화하는 과정에서 이 파일의 이 테스트가 삭제됐다. |
| note | no-replacement conversation/file save와 테스트 구조가 리팩터링된 PR이지만, 이 deep-directory 저장 테스트의 직접 대체 테스트나 동일 검증의 이동이 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. | Commit and PR only say LLM mocking was centralized; the PR does not mention this test, so the link is inferred. |

#### B16 · `fa436235-dc94-5f4a-8186-5adcbbee95ba` · vllm-project/vllm · `test_http_chat_no_model_name_with_curl`

- 파일 `tests/entrypoints/openai/test_chat.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 BUG↔UNK
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 1개 (1줄) · PR #24663 · 이슈 1개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Fix model name included in responses (#24663)

| | jh | hs |
|---|---|---|
| reason_label | UNK | BUG |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.6 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ③ 테스트 변화 — 이 PR이 served-model-name 응답 동작을 고치는 버그 수정이고(이슈 #15845), model 없이 호출하던 이 테스트가 삭제되며 BadRequestError import가 추가됐다. |
| note | no-replacement PR은 served-model-name 응답 버그를 수정하는 변경이지만, 이 no-model-name 테스트가 왜 삭제됐는지 또는 어떤 테스트로 대체됐는지가 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. | The PR sentence explains the bug fix, not this test's removal, so EXPLICIT was not used; BadRequestError import suggests the expected behavior changed. |

#### B17 · `29757a36-47f9-5333-b026-995ea6989085` · scikit-learn/scikit-learn · `test_get_params_deprecated`

- 파일 `sklearn/tests/test_base.py` · 테스트 코드 예 · 불일치: 이유 BUG↔DEAD
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #9569 · 이슈 3개 · 리뷰 코멘트 3개
- 커밋 첫 줄: remove modification of warning registry for no reason (#9569)

| | jh | hs |
|---|---|---|
| reason_label | BUG | DEAD |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.9 | 0.7 |
| evidence_locator | issue:#9560#body | diff:replacement |
| evidence_text | ③ — The test encoded the old behavior that deprecated parameters were excluded from get_params(), which was removed while fixing the incorrect warning/deprecation handling. | ③ 테스트 변화 — 이 커밋이 get_params의 deprecated 속성 필터링을 제거해서(이슈 #9560) 그 동작을 검증하던 이 테스트가 삭제됐다. |
| note | deprecated parameter를 get_params 결과에서 제외하던 기존 잘못된 동작을 수정하면서 그 동작을 기대하던 테스트가 삭제된 것으로 판단함. | The commit title explains why the get_params warning handling was removed, not why this test was removed. |

#### B18 · `f1fa77c0-5299-5bd2-8c61-998a8a36ad4b` · mem0ai/mem0 · `google_drive_folder_loader`

- 파일 `embedchain/tests/loaders/test_google_drive.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #5230 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: chore: extract embedchain to mem0ai/embedchain-archive (#5230)

| | jh | hs |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.7 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ③ 테스트 변화 — 이 PR이 embedchain 패키지 전체를 archive 저장소로 옮기며 deprecation을 마무리했고, 그 패키지의 테스트 fixture인 이 함수가 함께 삭제됐다. |
| note | filter-miss 4 — Embedchain 패키지와 관련 테스트가 mem0ai/embedchain-archive 외부 저장소로 순수 분리·이동된 변경으로, 기능 삭제가 아님. | Test fixture removed with the whole embedchain package; the PR sentence explains the package extraction, not this fixture. |

#### B20 · `0442196f-fcb2-5756-afa6-ecd23d2731ab` · django/django · `_get_trigger_name`

- 파일 `django/db/backends/oracle/operations.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 14개 (66줄) · PR #8055 · 이슈 0개 · 리뷰 코멘트 16개
- 커밋 첫 줄: Fixed #26682 -- Added support for Oracle identity columns.

| | jh | hs |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.5 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ② 호출자 변화 — 같은 파일의 추가 코드가 트리거 이름 대신 identity 컬럼의 sequence_name을 조회(_get_sequence_name)하는 방식으로 바뀌었다. |
| note | no-replacement no-caller-info Oracle identity column 지원으로 sequence 처리 구조가 크게 바뀌었지만, 이 _get_trigger_name helper의 직접 대체 코드나 호출자 변화가 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. | Replacement code is empty and the old callers are not visible; the PR sentence says only that identity column support was added. |

#### B24 · `77ad03ed-43b4-5130-98b4-84772f14e3ef` · unclecode/crawl4ai · `crawl_chanel`

- 파일 `tests/proxy/test_chanel_basic.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #1836 · 이슈 3개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Sync sec-ch-ua with User-Agent and keep WebGL alive in stealth mode

| | jh | hs |
|---|---|---|
| reason_label | DESIGN | DEAD |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.75 | 0.5 |
| evidence_locator | commit:message | commit:message |
| evidence_text | ③ — Old anti-bot test scripts were cleaned up while a new clean anti-bot test was introduced, indicating this helper was removed as part of test restructuring. | ③ 테스트 변화 — 커밋 메시지가 "Clean up old test scripts, add clean anti-bot test"라고 적어, 옛 테스트 스크립트인 이 함수가 정리되고 새 anti-bot 테스트가 추가됐다. |
| note | 기존 anti-bot 테스트 스크립트를 정리하고 새 clean anti-bot test로 전환하는 과정에서 테스트 구조가 재편된 것으로 판단함. | Release commit; the message names no reason for this specific script, only a general cleanup of old test scripts. |

#### B26 · `780ee554-a555-5eaa-9d45-1dfae6c3e901` · pandas-dev/pandas · `test_constructor_32bit`

- 파일 `pandas/tests/indexes/numeric/test_numeric.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 12개 (14줄) · PR #49682 · 이슈 1개 · 리뷰 코멘트 2개
- 커밋 첫 줄: DEPR: remove Int64Index, UInt64Index, Float64Index from tests.indexes.numeric (#49682)

| | jh | hs |
|---|---|---|
| reason_label | DEAD | DESIGN |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.8 | 0.6 |
| evidence_locator | pr:#49682#body | diff:replacement |
| evidence_text | ③ — Tests tied to Int64Index, UInt64Index, and Float64Index were removed as those legacy numeric index classes were being eliminated. | ③ 테스트 변화 — 이 커밋이 Int64Index 등을 Index로 대체하며 같은 파일의 테스트를 다시 쓰는 과정에서(추가 헝크) _index_cls 기반의 이 테스트가 삭제됐다. |
| note | 구형 Int64Index/UInt64Index/Float64Index 계열 제거에 따라 해당 클래스의 32bit constructor 동작을 검증하던 테스트가 더 이상 필요하지 않게 된 것으로 판단함. | The commit and issue explain the removal of the numeric index classes, not this test; whether its assertions were rewritten elsewhere is not verified. |

#### B27 · `7134a699-0999-5274-af3d-307e9864046f` · psf/requests · `__init__`

- 파일 `requests/packages/urllib3/util/selectors.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 LIB↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #4067 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: delete packages

| | jh | hs |
|---|---|---|
| reason_label | LIB | UNK |
| evidence_grade | EXPLICIT | UNKNOWN |
| confidence | 1.0 | 0.0 |
| evidence_locator | pr:#4067#body | — |
| evidence_text | This pull request works on removing all vendored dependencies from Requests. | — |
| note | vendored urllib3 구현을 저장소에서 제거하고 외부 의존성 사용으로 전환하는 변경임. | filter-miss 3 Vendored urllib3 copy under requests/packages was deleted by the PR that removes all vendored dependencies; it is not the project's own code. |

#### B29 · `f49f9e0d-892d-5ac0-ab02-ddb3d37bffe9` · docling-project/docling · `group_cell_elements`

- 파일 `docling/backend/msword_backend.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 40개 (167줄) · PR #2553 · 이슈 1개 · 리뷰 코멘트 0개
- 커밋 첫 줄: fix(docx): slow table parsing (#2553)

| | jh | hs |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | EXPLICIT |
| confidence | 0.0 | 1.0 |
| evidence_locator | — | commit:message |
| evidence_text | — | Move nested function group_cell_elements out of _handle_tables for |
| note | filter-miss 1 — group_cell_elements가 _handle_tables 내부 nested function에서 같은 파일의 _group_cell_elements staticmethod로 이동된 변경으로, 기능 삭제가 아님. | Moved out of the nested scope into a static method with docling_table passed as a parameter instead of a closure. |

#### B32 · `6821b0a1-4caf-5228-bca8-eac5537834ae` · psf/requests · `__delitem__`

- 파일 `requests/packages/urllib3/packages/ordered_dict.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 LIB↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #4067 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: delete packages

| | jh | hs |
|---|---|---|
| reason_label | LIB | UNK |
| evidence_grade | EXPLICIT | UNKNOWN |
| confidence | 1.0 | 0.0 |
| evidence_locator | pr:#4067#body | — |
| evidence_text | This pull request works on removing all vendored dependencies from Requests. | — |
| note | vendored urllib3의 ordered_dict 구현을 저장소에서 제거하고 외부 의존성 사용으로 전환하는 변경임. | filter-miss 3 Vendored urllib3 copy under requests/packages was deleted by the PR that removes all vendored dependencies; it is not the project's own code. |

#### B37 · `0381af80-36d6-541c-88e5-c6d425b45564` · keras-team/keras · `_unique_padded`

- 파일 `keras/src/backend/torch/ops/nn.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #23511 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Fix incomplete revert of pluggable_backend (#23511)

| | jh | hs |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | EXPLICIT |
| confidence | 0.0 | 1.0 |
| evidence_locator | — | commit:message |
| evidence_text | — | The original revert in c559fa8 did not revert newly created files. |
| note | no-replacement no-caller-info pluggable_backend의 이전 revert에서 새로 생성된 파일들이 누락되어 이를 정리한 커밋이지만, 이 함수가 어떤 기존 구현으로 복귀했는지 또는 어떤 구조에 흡수됐는지 현재 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. | Leftover code from the reverted pluggable_backend; whether this function sits in a newly created file is not verified. |

#### B39 · `5237adf3-aad9-5424-a94b-d33548082b4e` · commaai/openpilot · `set_ir_power`

- 파일 `panda/python/__init__.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR 없음 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Remove old panda subtree

| | jh | hs |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.5 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ⑤ 같은 커밋의 동형 삭제 — 커밋이 옛 panda subtree 전체를 제거했고(커밋 제목) 이 함수는 그 안의 panda 파이썬 모듈 일부다. |
| note | no-replacement no-caller-info old panda subtree가 제거되면서 함께 삭제된 메서드이지만, IR power 설정 기능이 다른 구현으로 이동했는지 또는 실제로 제거됐는지 현재 화면에서 확인되지 않아 삭제 이유를 특정하기 어려움. | Whole old panda subtree removed; whether a newer copy replaced it is not visible. |

#### B40 · `dc781737-ff36-50e2-8ac2-5f4bf12d4a0a` · langchain-ai/langchain · `test_faiss_with_metadatas_and_filter`

- 파일 `libs/community/tests/unit_tests/vectorstores/test_faiss.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #31060 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: community: move to separate repo (#31060)

| | jh | hs |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.5 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ③ 테스트 변화 — 이 커밋이 langchain-community 패키지를 별도 저장소로 옮기며(커밋 제목) 그 패키지의 FAISS를 검증하던 이 테스트가 함께 삭제됐다. |
| note | filter-miss 4 — langchain-community가 별도 저장소(langchain-ai/langchain-community)로 분리·이동되면서 해당 FAISS 테스트도 함께 이동한 변경으로, 기능 삭제가 아님. | Test removed together with the community package moved to a separate repo; moved versus deleted is not verified. |

#### B43 · `d45dc8ad-b13f-599a-aeea-34cad3cd215a` · mem0ai/mem0 · `test_ec_base_message`

- 파일 `embedchain/tests/memory/test_memory_messages.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #5230 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: chore: extract embedchain to mem0ai/embedchain-archive (#5230)

| | jh | hs |
|---|---|---|
| reason_label | UNK | DEAD |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.7 |
| evidence_locator | — | diff:replacement |
| evidence_text | — | ③ 테스트 변화 — 이 PR이 embedchain 패키지 전체를 archive 저장소로 옮기며 deprecation을 마무리했고, 그 패키지의 테스트인 이 함수가 함께 삭제됐다. |
| note | filter-miss 4 — legacy Embedchain 코드 전체가 mem0ai/embedchain-archive 별도 저장소로 순수 이동되면서 해당 BaseMessage 테스트도 함께 이동한 변경으로, 기능 삭제가 아님. | Test removed with the whole embedchain package; the PR sentence explains the package extraction, not this test. |

#### B45 · `2aa0be25-4af8-55a0-9bba-2cb3051a0d88` · home-assistant/core · `async_update`

- 파일 `homeassistant/components/openuv/__init__.py` · 테스트 코드 아니오 · 불일치: 이유 BUG↔DESIGN
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 24개 (78줄) · PR #80705 · 이슈 0개 · 리뷰 코멘트 3개
- 커밋 첫 줄: Replace custom OpenUV data object with coordinators (#80705)

| | jh | hs |
|---|---|---|
| reason_label | DESIGN | BUG |
| evidence_grade | EXPLICIT | EXPLICIT |
| confidence | 1.0 | 1.0 |
| evidence_locator | pr:#80705#body | pr:#80705#body |
| evidence_text | OpenUV currently uses a custom data management object, which (a) is duplicative of what `DataUpdateCoordinator` does and (b) doesn't properly update all entities via the `homeassistant.update_entity` service (as it should). This PR replaces the custom object with coordinators. | OpenUV currently uses a custom data management object, which (a) is duplicative of what `DataUpdateCoordinator` does and (b) doesn't properly update all entities via the `homeassistant.update_entity` service (as it should). |
| note | multi-reason 기존 custom data object의 갱신 책임을 DataUpdateCoordinator 기반 구조로 교체한 DESIGN이며, 모든 entity가 제대로 갱신되지 않던 버그 수정 동기도 함께 있음. | multi-reason The sentence gives both a structural reason (duplicates DataUpdateCoordinator) and a behavior reason (entities not updated via the update_entity service). |

#### B47 · `dd40d5c2-517d-5750-ae19-a00f7d5aa2be` · celery/celery · `__ne__`

- 파일 `celery/schedules.py` · 테스트 코드 아니오 · 불일치: 이유 DEAD↔LIB
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #7257 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Remove __ne__ methods (#7257)

| | jh | hs |
|---|---|---|
| reason_label | DEAD | LIB |
| evidence_grade | EXPLICIT | EXPLICIT |
| confidence | 1.0 | 1.0 |
| evidence_locator | pr:#7257#body | commit:message |
| evidence_text | These are already defined as the opposite of __eq__ in Python 3, and when __eq__ returns NotImplemented, Python by default will return True. | These are already defined as the opposite of __eq__ in Python 3, and when __eq__ returns NotImplemented, Python by default will return True. |
| note | Python 3에서 __ne__ 동작이 __eq__의 반대로 기본 제공되어 별도 구현이 중복·불필요해진 코드임. | Hand-written __ne__ replaced by the language default behavior; no import or library call was added. |

#### B50 · `109b8b87-f103-5c0f-9179-bde4f6106c0d` · huggingface/transformers · `custom_forward`

- 파일 `src/transformers/models/mvp/modeling_mvp.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 6개 (10줄) · PR #27020 · 이슈 1개 · 리뷰 코멘트 7개
- 커밋 첫 줄: [`core`] Refactor of `gradient_checkpointing` (#27020)

| | jh | hs |
|---|---|---|
| reason_label | DESIGN | DESIGN |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.7 |
| evidence_locator | commit:message | diff:replacement |
| evidence_text | replace with `__call__` | ② 호출자 변화 — 호출부가 custom_forward 클로저 대신 self.gradient_checkpointing_func(encoder_layer.__call__, ...)를 직접 쓰도록 바뀌었다(커밋 메시지 "remove create_custom_forward", "replace with __call__"). |
| note | gradient checkpointing에서 사용하던 custom_forward wrapper를 제거하고 module의 __call__을 직접 전달하는 구조로 변경됨. | Replacement code is empty so signal 1 was not used; the PR explains the gradient checkpointing refactor as a whole, not this closure. |


### 블록 C

#### C01 · `88487d19-ed93-56f7-b244-2e7db017d551` · huggingface/transformers · `_get_ground_truth`

- 파일 `examples/research_projects/lxmert/modeling_frcnn.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #36645 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Remove research projects (#36645)

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | EXPLICIT | UNKNOWN |
| confidence | 1.0 | 0.0 |
| evidence_locator | pr:#36645#body | — |
| evidence_text | This PR removes the research projects from `transformers` and adds a link to the repo where they live now: https://github.com/huggingface/transformers-research-projects | — |
| note | The projects still exist in another repository, so this is a structural move rather than a feature removal. | filter-miss 4 PR states research_projects was moved out to the separate repo huggingface/transformers-research-projects; presence of this file in the new repo not verified |

#### C02 · `37b96414-6822-53c7-9457-dbc97e4a2136` · django/django · `get_available_name`

- 파일 `tests/file_storage/models.py` · 테스트 코드 예 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #5143 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Refs #9893 -- Removed shims for lack of max_length support in file storage per deprecation timeline.

| | hs | sj |
|---|---|---|
| reason_label | FEAT | FEAT |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.7 |
| evidence_locator | commit:message | commit:message |
| evidence_text | Refs #9893 -- Removed shims for lack of max_length support in file storage per deprecation timeline. | ⑥ 삭제된 코드 자체 — max_length 인자 없이 name만 받는 옛 스토리지 시그니처의 테스트 스텁이고, 커밋 메시지가 max_length 미지원 shim을 deprecation 종료에 따라 제거한다고 서술 |
| note | Test storage override that lacked the max_length argument went away with the deprecated compatibility shim. | FEAT vs DEAD boundary for a test-model stub; link between this stub and the max_length shim is inferred from signature, not verified |

#### C04 · `ef5c8a6d-811d-5582-a8d3-fa33f54eaef6` · vllm-project/vllm · `_configure_bitblas_matmul`

- 파일 `vllm/model_executor/layers/quantization/kernels/mixed_precision/bitblas.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #32683 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: [Quantization][Deprecation] Remove BitBlas (#32683)

| | hs | sj |
|---|---|---|
| reason_label | FEAT | FEAT |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.75 |
| evidence_locator | pr:#32683#body | pr:#32683#title |
| evidence_text | now that 0.14 is out, deprecate bitblas for 0.15 | ④ 공개 표면 변화 — PR 제목이 BitBlas 양자화 지원 제거(Deprecation)를 서술하고, 이 함수는 BitBlas 전용 matmul 구성 코드 |
| note |  | Whole BitBlas backend removed; whether the file was deleted entirely is not verified |

#### C06 · `bf5e99fd-db33-5454-9059-47c807af5bc2` · docling-project/docling · `count_right`

- 파일 `docling/pipeline/vlm_pipeline.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 5개 (54줄) · PR #1158 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: feat: updated vlm pipeline (with latest changes from docling-core) (#1158)

| | hs | sj |
|---|---|---|
| reason_label | LIB | LIB |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.7 |
| evidence_locator | pr:#1158#body | pr:#1158#body |
| evidence_text | Updated VLM pipeline code to reuse` DocTagsDocument.from_doctags_and_image_pairs` from `docling-core`, instead of having DocTags de-serialization happening inside the pipeline. | ⑥ 삭제된 코드 자체 — 토큰 파싱 헬퍼이고, 같은 커밋이 DocTags 역직렬화를 docling-core의 DocTagsDocument.from_doctags_and_image_pairs 호출로 대체하며 PR 본문이 이를 서술 |
| note | The PR sentence does not name count_right; it is a helper of the de-serialization that moved to docling-core, which is an inference. | LIB vs DESIGN boundary since docling-core is a sibling package; whether this helper moved into docling-core is not verified |

#### C08 · `3f525d74-a5d7-58f7-943f-53f6b3ddb737` · langchain-ai/langchain · `test_anonymize_allow_list`

- 파일 `libs/experimental/tests/unit_tests/test_data_anonymizer.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #26879 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: experimental: migrate to external repo (#26879)

| | hs | sj |
|---|---|---|
| reason_label | DEAD | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.7 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ③ 테스트 변화 — 이 커밋이 langchain-experimental 패키지를 외부 저장소로 옮겼고(커밋 제목), 그 패키지의 PresidioAnonymizer를 검증하던 이 테스트가 함께 삭제됐다. | — |
| note | The commit sentence explains why the package moved (security scanners cannot tell monorepo sources apart), not why this test was removed. | filter-miss 4 Commit and PR body state libs/experimental was migrated to an external repo so security scanners stop flagging it for core users; presence of this test in the new repo not verified |

#### C09 · `49a82b90-64e8-56b7-959a-51716b4d4488` · psf/requests · `__init__`

- 파일 `requests/packages/chardet/hebrewprober.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 LIB↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #4067 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: delete packages

| | hs | sj |
|---|---|---|
| reason_label | UNK | LIB |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.55 |
| evidence_locator | — | pr:#4067#body |
| evidence_text | — | ⑤ 같은 커밋의 동형 삭제 — 커밋이 requests/packages/ 아래 vendored 코드를 일괄 삭제하고, PR 본문이 모든 vendored 의존성 제거를 서술 |
| note | filter-miss 3 Vendored chardet copy under requests/packages was deleted by the PR that removes all vendored dependencies; it is not the project's own code. | Vendored chardet code removed wholesale; PR is marked work in progress, whether it was replaced by an external chardet dependency is not verified |

#### C11 · `f7fffa0c-761f-5866-a07c-f937fedad4a8` · crewAIInc/crewAI · `_add_code_execution_tools`

- 파일 `src/crewai/crew.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I, 이유 BUG↔DESIGN
- `replacement`: code 있음 · match_method `SAME_LOCATION` · confidence 0.9
- 같은 파일 추가 헝크 28개 (81줄) · PR #2277 · 이슈 1개 · 리뷰 코멘트 1개
- 커밋 첫 줄: Add support for custom LLM implementations (#2277)

| | hs | sj |
|---|---|---|
| reason_label | BUG | DESIGN |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.8 |
| evidence_locator | commit:message | diff:replacement |
| evidence_text | Fix type errors in crew.py by updating tool-related methods to return List[BaseTool] | ① 대체 코드 — 같은 위치의 _add_code_execution_tools가 Union 타입 입력과 hasattr 검사, List[BaseTool] 반환으로 이어받았고, 커밋 메시지가 tool 관련 메서드의 타입 오류 수정을 서술 |
| note | The method was rewritten in place with new type annotations and a hasattr guard; the sentence says tool-related methods, not this one by name. | Same-name method replaced with a type-safe signature; no behavior change, so BUG is not claimed |

#### C13 · `7ff061a5-7b2a-5e67-9d85-7d1fb3ddb737` · crewAIInc/crewAI · `_format_union_type`

- 파일 `lib/crewai/src/crewai/utilities/pydantic_schema_parser.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I, 이유 BUG↔DESIGN
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #4066 · 이슈 2개 · 리뷰 코멘트 0개
- 커밋 첫 줄: feat: use json schema for tool argument serialization

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | BUG |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.75 |
| evidence_locator | commit:message | issue:#4064#body |
| evidence_text | Remove deprecated PydanticSchemaParser in favor of direct schema generation | ⑥ 삭제된 코드 자체 — Union 타입을 Optional[...]/Union[...] 파이썬 표기 문자열로 만드는 코드이고, 이슈 #4064가 이 표기를 JSON이 아니라고 지적하며 커밋 메시지가 JSON 스키마로 교체한다고 서술 |
| note | multi-reason The sentence gives a structural reason (parser replaced by direct JSON schema generation); the PR is titled fix and links bug issues #4064 and #3915 about fake JSON in tool arguments. | multi-reason Bug-reported Python-style schema string removed together with the whole deprecated parser (BUG vs DESIGN); where the replacement JSON schema code lives is not verified |

#### C14 · `22e7efcb-5b23-5ced-9b6a-fe9ec792623a` · pydantic/pydantic · `test_sub_model_merge`

- 파일 `tests/test_config.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #12481 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Do not inherit config in model fields that are themselves of type model (pydantic/pydantic-core#612)

| | hs | sj |
|---|---|---|
| reason_label | DEAD | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.6 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ③ 테스트 변화 — 커밋이 중첩 model 필드에서 config를 상속하지 않도록 바꿔서(커밋 제목), 상속·병합된 config를 검증하던 이 테스트가 삭제됐다. | — |
| note | The title describes the behavior change in pydantic-core, not this test; whether the test was rewritten elsewhere is not verified. | filter-miss 1 Pure move of the sub-model merge test while importing pydantic-core, identical test confirmed in the new location (off-record-evidence) |

#### C15 · `be37a8aa-a254-58ff-9fbe-3b2877f78bd2` · pydantic/pydantic · `replace_users`

- 파일 `setup.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 2개 (15줄) · PR #4473 · 이슈 0개 · 리뷰 코멘트 18개
- 커밋 첫 줄: Remove Cython & Move to `pyproject.toml` (#4473)

| | hs | sj |
|---|---|---|
| reason_label | FEAT | FEAT |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.8 |
| evidence_locator | review:comment_961944960 | review:comment_961944960 |
| evidence_text | This stops adding the changelog to the readme on release, but I think that's fine since we're linking to the changelog with `[project.urls]` | ④ 공개 표면 변화 — setup.py의 릴리스 시 README changelog 삽입 기능이 사라지고, 리뷰 코멘트가 [project.urls] 링크로 충분하다고 서술 |
| note | The helper formatted user mentions in the changelog that was embedded into the readme; that readme embedding was dropped. | FEAT vs DESIGN boundary since the changelog is replaced by a link; E2 boundary |

#### C16 · `628884b6-d4ab-5872-9e5c-6a5f21af81c5` · mem0ai/mem0 · `__init__`

- 파일 `evaluation/src/langmem.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #5520 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: chore: retire in-repo evaluation/ in favor of mem0ai/memory-benchmarks (#5520)

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | EXPLICIT | UNKNOWN |
| confidence | 1.0 | 0.0 |
| evidence_locator | pr:#5520#body | — |
| evidence_text | Retires the in-repo `evaluation/` directory in favor of the actively maintained [`mem0ai/memory-benchmarks`](https://github.com/mem0ai/memory-benchmarks) repo, which covers **LOCOMO + LongMemEval + BEAM**. | — |
| note | The original code is preserved in the new repository and git history, so this is a structural move rather than a feature removal. | filter-miss 4 PR states evaluation/ was retired in favor of the separate repo mem0ai/memory-benchmarks, with the baselines kept in a legacy snapshot; presence of this file in the new repo not verified |

#### C17 · `f41659a5-816f-556d-bc4f-6ac3d29a042b` · crewAIInc/crewAI · `_summarize_messages`

- 파일 `src/crewai/agents/crew_agent_executor.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 27개 (147줄) · PR #3440 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: refactor: cleanup crew agent executor (#3440)

| | hs | sj |
|---|---|---|
| reason_label | DEAD | DEAD |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.6 |
| evidence_locator | commit:message | pr:#3440#body |
| evidence_text | Remove dead code, unused imports, and obsolete methods | ⑥ 삭제된 코드 자체 — 이어받은 코드가 없는 요약 메서드이고, 커밋 메시지가 dead code와 obsolete methods 제거를 서술 |
| note | The sentence does not name _summarize_messages; it describes the cleanup of this executor file. | Whether callers of _summarize_messages already had been removed before this commit is not verified |

#### C18 · `0ce8735f-68bf-59d0-9d9c-cf01ed612299` · django/django · `fast_cache_clearing`

- 파일 `django/core/serializers/xml_serializer.py` · 테스트 코드 아니오 · 불일치: 이유 DEAD↔SEC
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 2개 (2줄) · PR #21246 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Refs CVE-2025-64460 -- Removed workaround for minidom document checks.

| | hs | sj |
|---|---|---|
| reason_label | SEC | DEAD |
| evidence_grade | EXPLICIT | EXPLICIT |
| confidence | 1.0 | 1.0 |
| evidence_locator | commit:message | commit:message |
| evidence_text | CVE-2025-12084 was fixed upstream in CPython and backported to 3.14.2, 3.13.11, and 3.12.13, making this workaround unnecessary. | CVE-2025-12084 was fixed upstream in CPython and backported to 3.14.2, 3.13.11, and 3.12.13, making this workaround unnecessary. |
| note | priority-rule The sentence mentions a CVE so SEC takes priority; the workaround itself was for minidom performance and became unnecessary after the upstream fix. | needs-discussion SEC vs DEAD boundary: CVE is mentioned but removal reason is the workaround becoming unnecessary after the upstream fix; whether callers were also removed is not verified |

#### C19 · `4689a9e6-047b-5c0e-b98f-3df61ab4dde7` · langchain-ai/langchain · `embeddings`

- 파일 `libs/community/langchain_community/vectorstores/epsilla.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #31060 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: community: move to separate repo (#31060)

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | EXPLICIT | UNKNOWN |
| confidence | 1.0 | 0.0 |
| evidence_locator | pr:#31060#body | — |
| evidence_text | langchain-community is moving to https://github.com/langchain-ai/langchain-community | — |
| note | The package still exists in another repository, so this is a structural move rather than a feature removal. | filter-miss 4 Commit and PR body state langchain-community was moved to the separate repo langchain-ai/langchain-community; presence of this file in the new repo not verified |

#### C20 · `8fdf755e-9206-5072-9c01-bd2ea6992234` · encode/httpx · `set_minimum_tls_version_1_2`

- 파일 `httpx/_compat.py` · 테스트 코드 아니오 · 불일치: 이유 DEAD↔DESIGN
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #3319 · 이슈 0개 · 리뷰 코멘트 8개
- 커밋 첫 줄: Introduce new `SSLContext` API & escalate deprecations. (#3319)

| | hs | sj |
|---|---|---|
| reason_label | DEAD | DESIGN |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.5 | 0.5 |
| evidence_locator | diff:replacement | pr:#3319#body |
| evidence_text | ⑥ 삭제된 코드 자체 — 'minimum_version'을 쓸 수 없는 옛 환경용 폴백 옵션 설정이라, 새 SSLContext API로 바뀌고 deprecation이 강화된 이 커밋 이후엔 쓸 곳이 없어 보인다. | ⑥ 삭제된 코드 자체 — 최소 TLS 버전을 옛 옵션 플래그로 맞추던 호환 헬퍼이고, PR이 SSL 설정을 새 SSLContext API로 교체한다고 서술 |
| note | No sentence mentions this helper; the review comment about a dependency is unrelated. | Whether callers of this helper were removed with the new SSLContext API is not verified |

#### C24 · `b7d85e43-0fc2-5cf9-8d2d-ff424b1c47e6` · psf/requests · `__repr__`

- 파일 `requests/packages/urllib3/util/retry.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 LIB↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #4067 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: delete packages

| | hs | sj |
|---|---|---|
| reason_label | UNK | LIB |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.55 |
| evidence_locator | — | pr:#4067#body |
| evidence_text | — | ⑤ 같은 커밋의 동형 삭제 — 커밋이 requests/packages/ 아래 vendored 코드를 일괄 삭제하고, PR 본문이 모든 vendored 의존성 제거를 서술 |
| note | filter-miss 3 Vendored urllib3 copy under requests/packages was deleted by the PR that removes all vendored dependencies; it is not the project's own code. | Vendored urllib3 code removed wholesale; PR is marked work in progress, whether it was replaced by an external urllib3 dependency is not verified |

#### C25 · `63e29cbe-cc02-584d-a2bc-f287bf374ecd` · browser-use/browser-use · `fake_load_events`

- 파일 `tests/ci/test_rust_agent.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 57개 (213줄) · PR #4975 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: Fix Rust PR review feedback

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ③ 테스트 변화 — 같은 파일에 Rust agent 테스트와 헬퍼가 대량(57개 헝크) 추가되는 과정에서 이 스텁이 삭제됐다. | — |
| note | The commit message only says it fixes review feedback; whether the stub was replaced by another fake is not verified. | vague-message no-replacement Commit message only says "Fix Rust PR review feedback" and does not explain this test helper's removal; whether the tests using it were renamed or removed is not verified |

#### C28 · `4a96d1f7-b9cc-5540-ab07-bd6495721e1d` · langchain-ai/langchain · `_merge_splits_no_seperator`

- 파일 `libs/partners/ai21/langchain_ai21/semantic_text_splitter.py` · 테스트 코드 아니오 · 불일치: 등급 E↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #25827 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: ai21: migrate to external repo (#25827)

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | EXPLICIT | UNKNOWN |
| confidence | 1.0 | 0.0 |
| evidence_locator | commit:message | — |
| evidence_text | ai21: migrate to external repo | — |
| note | The package still exists in another repository, so this is a structural move rather than a feature removal; the sentence states the destination, not a deeper reason. | filter-miss 4 Commit and PR title state the ai21 partner package was migrated to an external repo; presence of this file in the new repo not verified |

#### C29 · `0d84dee2-581e-549f-9789-3c0b1d9c20a1` · commaai/openpilot · `test_elm_protocol_autodetect_ISO14230_KWP_FAST`

- 파일 `panda/tests/elm_wifi.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR 없음 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Remove old panda subtree

| | hs | sj |
|---|---|---|
| reason_label | DEAD | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ③ 테스트 변화 — 커밋이 옛 panda subtree 전체를 제거했고(커밋 제목), 그 안의 ELM 테스트인 이 함수가 함께 삭제됐다. | — |
| note | Whole old panda subtree removed; whether a newer copy replaced it is not visible. | no-context vague-message Whole panda subtree removed by a one-line commit message; whether panda moved to a separate repo or submodule is not verified |

#### C30 · `d4707de0-23a6-5680-be18-06631886e791` · apache/superset · `get_raw_results`

- 파일 `superset/views/core.py` · 테스트 코드 아니오 · 불일치: 등급 E↔I, 이유 DESIGN↔FEAT
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 1개 (1줄) · PR #41714 · 이슈 4개 · 리뷰 코멘트 23개
- 커밋 첫 줄: chore(viz): remove legacy explore_json + viz.py pipeline (#41714)

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | FEAT |
| evidence_grade | EXPLICIT | INFERRED |
| confidence | 1.0 | 0.8 |
| evidence_locator | pr:#41714#body | pr:#41714#body |
| evidence_text | This branch removes the legacy viz pipeline — the deprecated `/superset/explore_json/` endpoints and `superset/viz.py` (both `@deprecated(eol_version="5.0.0")` since 3.0) — by first migrating all 15 remaining `useLegacyApi` charts to `/api/v1/chart/data`… | ④ 공개 표면 변화 — PR 본문이 deprecated된 explore_json 라우트와 results 헬퍼, viz.py 제거를 명시하고 Removes existing feature or API로 표시하며, 이 함수는 그 results 헬퍼 |
| note | Charts keep working through the v1 chart data API, so this is a pipeline replacement; the deprecated endpoints removal could also be read as FEAT. | FEAT vs DESIGN boundary since the functionality moved to the v1 chart data API; large multi-phase PR so E3 is X |

#### C32 · `7cebafb4-c9b6-540d-b94f-b592f89a0e4b` · apache/superset · `test_extract_table_references`

- 파일 `tests/unit_tests/migrations/shared/utils_test.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #19421 · 이슈 2개 · 리뷰 코멘트 1개
- 커밋 첫 줄: perf: refactor SIP-68 db migrations with INSERT SELECT FROM (#19421)

| | hs | sj |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.75 |
| evidence_locator | — | review:comment_846573728 |
| evidence_text | — | ③ 테스트 변화 — 삭제된 테스트의 대상 헬퍼 extract_table_references가 superset.sql_parse로 이동했다고 리뷰 코멘트가 서술 |
| note | filter-miss 1 A reviewer says the helper moved to superset.sql_parse, so the test is presumed moved with it rather than discarded; not verified on GitHub. | DEAD vs DESIGN boundary for a test whose target moved to sql_parse; whether an equivalent test exists in the new location is not verified |

#### C33 · `7fe496f2-4f01-50eb-8f0e-4de0f3ad2dd4` · unclecode/crawl4ai · `set_custom_headers`

- 파일 `crawl4ai/async_crawler_strategy.back.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR 없음 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: release: merge 0.9.0 secure-by-default Docker server hardening into develop

| | hs | sj |
|---|---|---|
| reason_label | DEAD | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ⑥ 삭제된 코드 자체 — 파일명이 .back.py인 백업 복사본의 메서드이고, 이 커밋이 exec 기반 hook_manager.py 등 옛 구현을 걷어내는 릴리스 병합이다. | — |
| note | The release squash message never mentions this backup file; the file name suggests an unused copy. | no-context vague-message Backup file (.back.py) removed in a large security-hardening release commit whose message does not mention it; whether the original file keeps this function not verified |

#### C35 · `6711d4ca-1f83-5be3-8e16-6ff9fb32c4e1` · home-assistant/core · `test_skip_non_existing_update`

- 파일 `tests/components/update/test_init.py` · 테스트 코드 예 · 불일치: 이유 DEAD↔FEAT
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #67641 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: Revert "Add update integration (#66552)" (#67641)

| | hs | sj |
|---|---|---|
| reason_label | DEAD | FEAT |
| evidence_grade | INFERRED | INFERRED |
| confidence | 0.7 | 0.5 |
| evidence_locator | diff:replacement | pr:#67641#title |
| evidence_text | ③ 테스트 변화 — 이 PR이 update integration 추가 커밋을 되돌려서(PR 제목), 그 통합 구성요소의 update/skip API를 검증하던 이 테스트가 함께 삭제됐다. | ⑤ 같은 커밋의 동형 삭제 — PR 제목이 update 통합 도입 커밋 #66552 전체를 되돌려 이 테스트도 함께 삭제됨 |
| note | Revert PR; the title names the reverted integration but gives no reason. | Revert of the whole update integration; the reason for reverting is not verified |

#### C36 · `0eda74ce-6286-5bef-afb1-5348187a7d50` · browser-use/browser-use · `on_step_start`

- 파일 `tests/ci/test_rust_agent.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `None` · confidence 0.0
- 같은 파일 추가 헝크 57개 (213줄) · PR #4975 · 이슈 0개 · 리뷰 코멘트 1개
- 커밋 첫 줄: Fix Rust PR review feedback

| | hs | sj |
|---|---|---|
| reason_label | DESIGN | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ③ 테스트 변화 — 같은 파일에 Rust agent 테스트와 헬퍼가 대량(57개 헝크) 추가되는 과정에서 이 콜백 정의가 삭제됐다. | — |
| note | The commit message only says it fixes review feedback; whether the callback was replaced elsewhere is not verified. | vague-message no-replacement Commit message only says "Fix Rust PR review feedback" and does not explain this test callback's removal; whether the tests using it were renamed or removed is not verified |

#### C44 · `5239a2c7-e2eb-55e3-adbb-950467aceabf` · scikit-learn/scikit-learn · `add_example`

- 파일 `examples/applications/svm_gui.py` · 테스트 코드 아니오 · 불일치: 이유 BUG↔DEAD
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #28620 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: DOC remove broken SVM GUI example (#28620)

| | hs | sj |
|---|---|---|
| reason_label | BUG | DEAD |
| evidence_grade | EXPLICIT | EXPLICIT |
| confidence | 1.0 | 1.0 |
| evidence_locator | pr:#28620#body | pr:#28620#body |
| evidence_text | This example is broken when executing it. | This example is broken when executing it. |
| note | The broken example was removed rather than fixed; the PR plans to rewrite it later. | needs-discussion DEAD vs FEAT boundary: whole broken example removed, not a fix of this function |

#### C45 · `6ae90852-f144-56d2-b858-8997bb5609f0` · docling-project/docling · `_client`

- 파일 `docs/examples/service_client/task_api.py` · 테스트 코드 아니오 · 불일치: 등급 I↔U, 이유 DESIGN↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #3627 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: docs: Update service client examples (#3627)

| | hs | sj |
|---|---|---|
| reason_label | UNK | DESIGN |
| evidence_grade | UNKNOWN | INFERRED |
| confidence | 0.0 | 0.5 |
| evidence_locator | — | pr:#3627#title |
| evidence_text | — | ⑥ 삭제된 코드 자체 — 환경변수로 서비스 클라이언트를 만드는 예제 헬퍼이고, 커밋·PR 제목이 서비스 클라이언트 예제 갱신을 서술 |
| note | no-context vague-message no-replacement The commit only says it updates service client examples, the PR body is an unfilled template and no added code in this file is shown; why this helper was removed is unknown. | Whether the client construction survives in the updated example is not verified |

#### C48 · `50dbd3a2-9bb5-5906-a39f-62f9249d0159` · langchain-ai/langchain · `test__convert_dict_to_message_human`

- 파일 `libs/community/tests/unit_tests/chat_models/test_hunyuan.py` · 테스트 코드 예 · 불일치: 등급 I↔U, 이유 DEAD↔UNK
- `replacement`: code 없음 · match_method `NONE` · confidence 0.0
- 같은 파일 추가 헝크 0개 (0줄) · PR #31060 · 이슈 0개 · 리뷰 코멘트 0개
- 커밋 첫 줄: community: move to separate repo (#31060)

| | hs | sj |
|---|---|---|
| reason_label | DEAD | UNK |
| evidence_grade | INFERRED | UNKNOWN |
| confidence | 0.5 | 0.0 |
| evidence_locator | diff:replacement | — |
| evidence_text | ③ 테스트 변화 — 이 커밋이 langchain-community 패키지를 별도 저장소로 옮기며(커밋 제목) 그 패키지의 hunyuan 모델을 검증하던 이 테스트가 함께 삭제됐다. | — |
| note | Test removed together with the community package moved to a separate repo; moved versus deleted is not verified. | filter-miss 4 Commit and PR body state langchain-community was moved to the separate repo langchain-ai/langchain-community; presence of this test in the new repo not verified |
