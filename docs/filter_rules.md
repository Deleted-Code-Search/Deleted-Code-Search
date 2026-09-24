# 필터 규칙

버전: v0.7. 담당: 재헌. 규칙 변경 시 이 문서의 버전을 올리고, `DeletionRecord.filter_rule_version`에 반영하며(코드 상수 `pipeline/filter.py:FILTER_RULE_VERSION`을 같은 PR에서 같은 값으로 올린다 — 테스트가 둘이 같은지 확인한다), PR에 테스트와 정밀도 재측정 결과를 첨부한다. 재측정은 수동 200건 (통과 100 + 제외 100)이다 (CHARTER.md §8.4, §10.1).

NOISE_MOVE의 정규화·유사도·후보 범위는 ADR-014(`docs/adr/014-move-detection-rules.md`)가 최종 기준이다. 이 문서는 ADR-014의 결정을 요약해 필터 규칙 전체(다른 노이즈 유형 포함)와 나란히 두고, ADR-014가 정하지 않고 #52 구현에서 결정한 세부사항(같은 위치 판정 방법, 1:1 매칭 알고리즘, 실제 added-line 겹침 조건)을 함께 기록한다. ADR-014와 이 문서가 어긋나면 ADR-014가 옳다 — 이 문서를 고친다.

## 노이즈 제외 대상 (CHARTER.md §4.2 ②)
| filter_status | 규칙 | 상태 |
|---|---|---|
| NOISE_MOVE | 같은 커밋에서, 실제 added line과 겹치는 함수 중 정규화 본문(ADR-014 결정 1)이 완전히 같거나 `SequenceMatcher.ratio() >= 0.9`(ADR-014 결정 2)인 함수가 **다른 위치**(다른 파일, 또는 같은 파일의 다른 자리)에 있으면 이동. 같은 파일 안 같은 위치는 제자리 수정이므로 제외 대상이 아니다(ADR-014 결정 3). 이동은 삭제 함수 1개 : 추가 함수 1개(1:1)로만 성립한다 — 아래 절 참고 | 구현 (`pipeline/filter.py` + `pipeline/extract.py`, Issue #52, ADR-014 정합 완료). FULL_FUNCTION 삭제만 대상(PARTIAL은 함수 전체 원문이 아니라 비교 불가). 회귀 테스트는 `tests/test_filter.py`, `tests/test_extract.py`. **실제 저장소 표본 정밀도 재측정은 아직 안 함**(§8.4) — #5 채굴 실행 후 별도 측정 필요 |
| NOISE_RENAME | 본문 동일, 이름만 변경 | 미구현 |
| NOISE_FORMAT | 포맷·주석·독스트링만 변경 | 미구현 |
| NOISE_BULK | 파일 전체 삭제 + "remove/delete directory/module" 계열 메시지 + 함수 100개 이상 | 미구현 |
| NOISE_GENERATED | 마이그레이션·자동 생성·vendored 경로 패턴 | 미구현 |
| NOISE_TRIVIAL | PARTIAL 레코드 중 `deleted_body`가 **4줄 이하**인 것. 줄 수는 `len(deleted_body.splitlines())`(빈 줄 포함, 마지막 빈 줄은 세지 않음 — 아래 절). 5줄 이상 PARTIAL만 데이터셋에 유지한다. FULL_FUNCTION은 대상이 아니다. 라벨링 대상 범위는 바꾸지 않는다 (ADR-015) | 구현 (`pipeline/filter.py:partition_trivial` + `pipeline/extract.py`, #63). 회귀 테스트는 `tests/test_filter.py`, `tests/test_extract.py`. 정밀도 재측정은 §10.1 필터 평가(#89)에서 한다 |

테스트 코드 삭제는 제외하지 않고 `is_test_code` 플래그로 구분한다.

### NOISE_MOVE — 최우선 원칙: 오탐보다 미탐 (코드 리뷰 BLOCKER/HIGH 수정, ADR-014 예정)

CHARTER §10.1의 필터 정밀도 목표(≥90%)에서, "이동 아닌데 이동으로 잘못 제외"(오탐)는 그 삭제
레코드를 데이터셋에서 영구히 지운다. 반대로 "이동인데 못 잡음"(미탐)은 노이즈가 남을 뿐 사람 라벨링
단계에서 걸러낼 수 있다. 그래서 애매한 경우 삭제 레코드를 **유지**하는 쪽으로 규칙을 만든다 — 아래
"실제 added function" 판정과 "1:1 매칭"이 둘 다 이 원칙을 구현한 것이다. ADR-014는 팀장이 별도로
기록한다.

### NOISE_MOVE — 이동 후보(added-side) 판정: 실제 added line과 겹치는 함수만

`pipeline/extract.py:collect_added_functions`. 이번 diff에서 자식 파일의 실제 added-line 범위
(`_parse_added_line_ranges`, 헝크의 `new_start`~`new_start+new_count-1`)와 함수의
`[start_line, end_line]`이 **하나라도** 겹치면 후보다 — 함수 전체가 added line일 필요는 없다.
부모 커밋부터 있었고 이번 diff와 전혀 안 겹치는 함수(이번 커밋에서 손 안 댐)는 후보에 넣지 않는다.
ADR-014는 이 세부 규칙을 정하지 않았고(#52 구현에 위임), 결정 3의 "같은 커밋에서 추가된 함수"라는
후보 풀 자체와 모순되지 않는다.

**수정 전 버그(코드 리뷰 BLOCKER, 수정됨):** "그 파일에 추가 줄이 하나라도 있으면 파일 전체 함수를
후보로" 삼는 구현이었다. 그러면 이번 커밋에서 전혀 손 안 댄, 그 파일에 원래 있던 함수까지 후보가
돼, 다른 파일의 무관한 진짜 삭제가 그 함수와 우연히 비슷하다는 이유로 이동 오판될 수 있었다(실제
git 저장소로 재현: 흔한 이름의 짧은 함수 2개가 서로 다른 파일에 우연히 같은 경우). 회귀 테스트는
`tests/test_extract.py::TestCollectAddedFunctions::test_b1_untouched_function_in_partially_edited_file_is_not_a_candidate`,
`tests/test_filter.py::test_a_untouched_function_does_not_cause_unrelated_deletion_to_be_excluded`.

### NOISE_MOVE — 정규화 (ADR-014 결정 1)

AST(tree-sitter) 기반. 치환 규칙은 ADR-014가 정한 그대로다:
- 일반 식별자(변수명·파라미터명) → `VAR`
- 문자열 리터럴 → `STR`, 숫자 리터럴 → `NUM` (정수·부동소수점·복소수 모두 NUM — 하나의 LIT로
  합치지 않는다)
- 주석·독스트링 제거, 공백·들여쓰기 정규화
- 치환하지 않는다: **현재 비교 대상 함수 자신의 이름**, attribute 이름(`self.headers`의
  `headers`), 호출 대상 이름(`json.loads`의 `loads`)

**중첩 함수 이름의 범위 (ADR-014).** `PythonAdapter`는 중첩 함수를 바깥 함수와 별도의 `Function`
으로도 추출한다. 같은 이름을 자리에 따라 다르게 다룬다:
- 바깥 함수를 정규화할 때: 그 본문 안에 있는 중첩 함수 **선언의 이름**은 일반 식별자와 똑같이
  `VAR`로 치환한다 — 중첩 함수의 나머지(매개변수·본문)는 건너뛰지 않고 보통 규칙대로 재귀
  정규화한다. "선언 이름만" 못 쓰게 막는 것이지 중첩 함수 서브트리를 통째로 들어내는 게 아니다.
- 그 중첩 함수 자신을 별도로(자기 `body`로) 정규화할 때: 이번엔 그 함수가 "현재 비교 대상"이라
  자신의 이름이 보존된다.
- 구현: `pipeline/filter.py:_find_root_function`(정규화 대상 텍스트 최상위의
  `function_definition`을 찾음) + `_normalize_node`의 `is_root` 분기.

**정규화 결과의 표현.** ADR-014 결정 2가 요구하는 "줄 목록" 비교를 위해, `normalize_function_body`는
문자열 하나가 아니라 **정규화된 줄의 리스트**(`list[str]`)를 돌려준다 — 원본 줄 경계는 유지하고
그 줄 안의 공백만 정규화한다(토큰을 그 줄 안에서 공백 하나로 이어 붙임). 토큰을 하나도 만들지
않은 줄(빈 줄, 주석·독스트링만 있던 줄)은 목록에서 아예 빠진다.

**bool/None (팀장 최종 확인, ADR-014 반영 예정).** `True`/`False`/`None`은 STR·NUM 어느 쪽으로도
치환하지 않고 원문 그대로 남긴다 — 원래 의미를 유지하는 쪽이 과도한 치환보다 안전하다는 정밀도
우선 원칙에 따른 결정이다. 복소수는 결정 1의 "숫자 리터럴" 범주에 속하므로(위 "정규화" 절) NUM —
문자열·숫자 두 범주를 벗어나는 새 분류가 필요했던 적은 없다. 이 두 결정은 ADR-014 원문에는 아직
없지만 팀이 확정했고, ADR-014 본문 반영은 팀장이 별도로 한다(이 PR에서는 ADR-014 파일을 건드리지
않는다).

### NOISE_MOVE — 유사도 (ADR-014 결정 2)

1. 정규화 본문이 **완전히 같으면**(줄 목록이 완전히 같으면, 해시로 먼저 비교) 이동으로 확정한다
   (similarity 1.0).
2. 아니면 `difflib.SequenceMatcher(None, 삭제_줄목록, 추가_줄목록, autojunk=False).ratio()`가
   **0.9 이상**(0.9 포함)이면 이동으로 판정한다.
   - 비교 단위는 정규화 본문의 **줄 목록**이다 — 문자열 전체를 이어 붙여 문자 단위로 비교하지
     않는다. 인자 순서는 (삭제 함수, 추가 함수)로 고정한다.
   - `autojunk=False`: 기본값(`True`)은 200개 이상인 시퀀스에서 자주 나오는 줄을 무시해, 긴
     함수의 비율을 실제보다 낮게 낸다.
3. ratio < 0.9면 짝 후보가 아니다. threshold 0.9는 CHARTER §4.2②에 이미 있으므로 바꾸지 않는다.

해시는 exact-match를 빠르게 확인하려는 내부 최적화일 뿐 외부에 노출하지 않는다. 구현:
`pipeline/filter.py:_move_similarity`.

### NOISE_MOVE — 후보 범위 (ADR-014 결정 3)

- 같은 커밋에서 추가된 함수 가운데 **파일 경로 또는 위치가 삭제 함수와 다른 것만** 후보다(다른
  파일로의 이동, 같은 파일 안의 위치 이동).
- **같은 경로·같은 위치**의 추가 함수는 유사도 계산 전에 후보에서 제외하고 수정으로 처리한다
  (NOISE_MOVE로 판정하지 않는다). "같은 위치" 판정 기준은 ADR-014가 정하지 않고 #52 구현에
  위임했다 — 아래 "same-position 판정" 절.
- **정규화 본문이 0줄인 함수는 이동 후보에서 제외한다** — 어느 한쪽이라도 0줄이면 짝이 아니다
  (`_move_similarity`의 첫 단계). 0줄끼리는 우연히 해시가 같아 완전 일치로 오판될 수 있고, 1차
  거르기의 분모(긴 쪽 줄 수)가 0이 되는 것도 막는다. `Function.body`가 시그니처를 포함하고
  결정 1이 대상 함수 이름을 보존하므로, 이 구현에서는 실제로 0줄이 생기지 않는다 — 이 규칙은
  정규화가 시그니처를 떼는 구현까지 막는 방어 규칙이다(ADR-014).
- **1차 거르기:** 정규화 본문의 **줄 수** 차이가 **긴 쪽 기준으로 20%를 넘으면**
  (`(긴 쪽 − 짧은 쪽) / 긴 쪽 > 0.2`) 비교하지 않는다. `ratio() = 2M/(a+b)`이고 일치 원소 수
  `M ≤` 짧은 쪽이므로, 차이가 긴 쪽의 20%를 넘으면 ratio 상한이 `2×0.8/1.8 ≈ 0.889 < 0.9`다.
  **정확히 20%는 "넘는" 게 아니므로 스킵하지 않는다** — 다만 20% 경계에서도 최댓값이 `8/9 ≈ 0.889`
  라 실제로 그 경계에서 0.9를 넘는 일은 (내용이 진짜 다르면) 드물다.

## NOISE_MOVE — 1:1 greedy 매칭 (코드 리뷰 HIGH 수정, #52 구현 결정 — ADR-014 범위 밖)

이동은 정의상 삭제 함수 1개 : 추가 함수 1개 관계다. 하나의 added function이 여러 deleted function을
동시에 "이동"으로 설명하면 안 된다(수정 전에는 소비 추적이 없어, 같은/유사한 함수가 같은 커밋에
여러 번 삭제되고 진짜 이동 후보가 하나뿐이어도 전부 이동으로 잘못 판정될 수 있었다). 최적 bipartite
matching은 과하다고 보고, 유사도 내림차순 **greedy**로 확정했다(`pipeline/filter.py:find_moved`, #97부터 본체는 `_match_moved`):

1. 유효한 (삭제, 추가) pair를 전부 만든다 — same-position pair는 여기서 제외(아래 절 참고).
2. 각 pair의 유사도를 계산한다(위 "유사도" 절 그대로): 0줄 제외 → 20% 줄 수 프리필터 → 정규화
   완전 동일(해시로 먼저 비교, 1.0) → `SequenceMatcher(..., autojunk=False)`. `< 0.9`거나
   앞 단계에서 탈락하면 그 pair는 버린다.
3. 남은 pair를 유사도(정규화 유사도) 내림차순으로 정렬한다. **동점 tie-break** (v0.7부터):
   1. **원문 유사도 내림차순** (#80). 삭제 쪽 `deleted_hunk`와 추가 쪽 `Function.body`
      원문을 줄마다 `strip()`하고 빈 줄을 뺀 줄 목록으로
      `SequenceMatcher(None, 삭제_원문줄, 추가_원문줄, autojunk=False).ratio()` — 인자 순서는
      ADR-014 결정 2와 같은 (삭제, 추가)다. 구현: `pipeline/filter.py:_raw_similarity`.
   2. 원문 유사도까지 같으면 삭제 쪽 key (`commit_sha, file_path, start_line, end_line`)
      오름차순, 그다음 추가 쪽 key (`file_path, start_line, end_line`) 오름차순 — 이 값들은
      그 커밋 안에서 안정적으로 식별 가능한 값이라, dict 순회 순서 등 입력 순서에 결과가
      흔들리지 않는다 (v0.6까지의 tie-break 그대로).

   **원문 유사도는 이동 판정 기준이 아니다.** 이동 여부는 여전히 정규화 유사도와
   `SIMILARITY_THRESHOLD = 0.9`(ADR-014 결정 2)로만 정한다. 원문 유사도는 threshold를 이미
   통과한 pair 사이에서, 정규화 유사도가 **같을 때만** 정렬 순서를 정한다 — 정규화 유사도가
   더 높은 pair를 원문 유사도로 뒤집지 않고, threshold 미만 pair를 원문 유사도로 살리지
   않는다. excluded JSONL의 `filter_evidence.similarity`도 정규화 유사도 그대로다.
4. 정렬된 순서대로, 삭제·추가 양쪽 다 아직 안 쓰였으면 pair를 확정하고 둘 다 소비 처리한다.
5. 짝을 찾은 삭제만 NOISE_MOVE로 제외한다. 못 찾은 삭제는 KEPT.

**원문 유사도 tie-break를 넣은 이유 (#80).** 게이트 1 filter-miss 18건 중 283e72d9
(`pydantic` 커밋 8e0455c9c6, `test_forward_ref_sub_types`): `tests/test_py36.py`와
`tests/test_py37.py`의 두 삭제가 `tests/test_forward_ref.py`의 같은 목적지와 정규화 유사도
0.9524로 동점이었다. v0.6까지는 동점을 경로 사전순으로 깨서 `test_py36.py`가 목적지를
먼저 차지했고, 실제 git rename 원본인 `test_py37.py`는 KEPT로 남았다. 이 커밋의 동점 10쌍에서
같은 현상이 있었다. 정규화는 리터럴을 `STR`/`NUM`으로 지워 두 원본을 구분하지 못하지만, 원문은
구분한다. ADR-014의 범위(정규화·threshold·후보 범위·1:1)를 넓히지 않고 현행 1:1 매칭 안의
tie-break만 바꾼 것이다.

**사전 분석 (#80, `pydantic/pydantic` 전체 히스토리 5,723커밋 시뮬레이션).** v0.6 재현은
FULL_FUNCTION 8,978건 중 NOISE_MOVE 5,977건, 남은 3,001건으로 pre200 모집단과 일치했다.
v0.7을 적용하면 판정이 바뀌는 커밋은 3개, 레코드는 24건(12쌍이 서로 자리를 바꿈)이다.
커밋별 NOISE_MOVE 총 개수는 그대로이고 1:1도 유지된다. 조사한 12쌍 모두 원문이 더 같은 원본을
고르는 방향이었다. 이 값은 정밀도가 아니다 — **정밀도 재측정은 #89에서 한다.**

**범위 밖 (#80에서 다루지 않음, #89까지 보류).** 이동+리네임으로 정규화 유사도가 0.8889인
사례(4aafd867)는 threshold·정규화를 바꾸지 않았으므로 여전히 KEPT다. 두 커밋 뒤에 다시 나타나는
사례(dd5e735a)는 커밋을 넘는 이동 탐지를 하지 않으므로 여전히 KEPT다.

회귀 테스트: `tests/test_filter.py`의 `test_b_one_to_one_exact_match_consumes_only_one_deleted`,
`test_c_higher_similarity_wins_the_shared_candidate`,
`test_d_greedy_matching_does_not_reuse_candidates_across_independent_groups`,
`test_e_greedy_matching_is_deterministic_regardless_of_input_order`. 원문 유사도 tie-break(#80):
`test_regression_283e72d9_rename_source_wins_normalized_tie`,
`test_raw_tie_break_does_not_depend_on_source_path_order`,
`test_raw_tie_break_keeps_one_to_one_and_is_order_independent`,
`test_raw_similarity_never_overrides_higher_normalized_similarity`,
`test_raw_similarity_tie_falls_back_to_record_key_then_candidate_key`,
`test_high_raw_similarity_does_not_rescue_below_threshold_pair`.

## NOISE_MOVE — same-position 판정 (Issue #52 구현 결정 — ADR-014가 #52에 위임)

같은 `file_path`에 유사한 함수가 추가돼도 그게 "제자리 수정"이면 이동이 아니다(ADR-014 결정 3).
"같은 위치"를 언제, 어떻게 판정하는지는 ADR-014가 정하지 않고 #52 구현에서 다음과 같이 정했다.

**선택한 방법 — diff 헝크 매핑.** 삭제된 함수의 부모 쪽 줄 범위 `[start_line, end_line]`과 겹치는
diff 헝크(`git diff --unified=0`의 `@@ -old_start,old_count +new_start,new_count @@`)를 찾고, 그
헝크의 `new_start`~`new_start+new_count-1`을 "제자리 수정이라면 자식 파일에서 있어야 할 범위"로
삼는다. 후보 함수의 위치가 이 범위와 겹치면 같은 위치(제자리 수정)로 보고 이동 후보에서 뺀다. 겹치는
헝크가 없거나 전부 `new_count == 0`(그 자리에 아무것도 안 남음)이면 같은 위치가 아니라고 본다 —
그러면 파일 안 다른 곳에 있는 유사 함수는 정당한 이동 후보로 남는다. 구현은
`pipeline/extract.py:collect_same_file_hunks`(헝크 수집) + `pipeline/filter.py:_is_same_position`
(판정).

**왜 단순 `start_line == start_line` 비교보다 안전한가.** 함수보다 위쪽에 무관한 줄(다른 함수 추가,
삽입 등)이 끼어들면 자식 파일의 줄 번호가 그만큼 밀린다 — 단순 시작 줄 비교는 이런 경우 "달라졌다"고
오판해 제자리 수정을 이동으로 잘못 판정한다. 헝크의 `new_start`는 git이 그 앞의 모든 헝크의 순증감을
이미 반영해 계산해 준 값이라, 앞쪽 삽입·삭제로 인한 밀림을 따로 계산하지 않아도 된다 — 정확히 그
밀림 문제를 피하려고 이 방법을 골랐다. 회귀 테스트(`tests/test_filter.py`의
`test_d_unrelated_insertion_above_shifts_line_numbers_but_stays_same_position`)로 실제 git
저장소에서 확인했다: 함수 위에 무관한 함수가 추가돼 자식 쪽 시작 줄이 5 → 9로 밀려도, 헝크
매핑으로는 여전히 같은 위치로 판정된다.

**added-line 겹침 판정과의 관계(중복·충돌 없음).** added 후보가 "실제 added line과 겹치는 함수"로
제한되지만, same-position 판정과 겹치는 질문을 던지지 않는다 — 하나는 "이게 진짜 추가된 함수인가"
(candidacy), 다른 하나는 "그 추가가 제자리인가, 다른 자리인가"(position)다. 실제로 이동한 함수는
정의상 그 새 위치의 줄이 added line이므로 두 조건 모두에서 자연히 candidate로 남는다.

**알려진 한계.**
- **헝크 병합.** `--unified=0`은 컨텍스트 줄이 없어서, 삭제된 함수와 그 바로 근처의 무관한 삽입
  사이에 "겹치는 줄"이 하나도 없으면 git이 둘을 하나의 헝크로 합쳐 버리는 경우가 있다(실제 재현:
  함수 바로 위에 새 함수가 끼어드는 커밋). 이러면 same-position 범위가 실제보다 넓어져서, 그 넓어진
  범위 안에 우연히 들어오는 무관한 추가 콘텐츠까지 "같은 위치"로 판정될 수 있다. 다만 그 무관한
  콘텐츠는 어차피 정규화 유사도가 낮아 애초에 이동 후보가 아니었을 가능성이 높아, 실제 오탐으로
  이어지는 경우는 드물 것으로 본다 — 표본 정밀도 측정(§10.1) 때 이 케이스를 별도로 확인해야 한다.
- **함수 경계와 헝크 경계 불일치.** 겹침(overlap) 판정이라 후보 함수의 시작 줄이 헝크 범위와 정확히
  일치하지 않아도(예: 헝크가 함수 앞뒤로 한두 줄 더 포함) 같은 위치로 잡힌다 — 대부분 의도한 동작이지만,
  아주 긴 헝크가 여러 함수를 아우르면 그만큼 range가 넓어진다.
- **PARTIAL 미대상.** same-position 판정 자체는 FULL_FUNCTION에만 적용된다(PARTIAL은 애초에 이동
  후보가 아니므로, 모듈 독스트링 참고).
- 이 판정은 Issue #52(NOISE_MOVE)에 한정된 것이고, 범용 parent→child 줄 번호 매핑 프레임워크가
  아니다 — 다른 필터·단계가 줄 번호 매핑이 필요하면 별도로 설계해야 한다.

## NOISE_TRIVIAL — PARTIAL 최소 줄 수 (ADR-015)

`docs/adr/015-partial-min-lines.md`가 최종 기준이다. 이 절은 요약이다.

**규칙.**
- 대상: `deletion_kind == PARTIAL` 레코드만. FULL_FUNCTION은 줄 수와 무관하게 이 규칙에서 제외되지 않는다.
- 줄 수: 정확히 `len(deleted_body.splitlines())` (내부 필드로는 `len(deleted_hunk.splitlines())`). 보정하지 않는다. **빈 줄도 센다** — 2026-09-17 실측도 빈 줄을 포함해 셌다. 단, 마지막 빈 줄은 세지 않을 수 있다 (아래 "한계").
- **5줄 이상 → 유지, 4줄 이하 → `NOISE_TRIVIAL`.**

**근거 (2026-09-17 `pydantic/pydantic` 실측, 필터 적용 후 PARTIAL 19,690건).**

| 삭제 줄 수 | 건수 | 비율 |
|---|---:|---:|
| 1줄 | 10,326 | 52.4% |
| 2-4줄 | 6,423 | 32.6% |
| 5-9줄 | 1,834 | 9.3% |
| 10줄 이상 | 1,107 | 5.6% |

5줄 이상 PARTIAL 2,941건은 FULL_FUNCTION 3,001건과 비슷한 규모다. `psf/requests`(PARTIAL 4,755건)도 1줄 49.1%, 2-4줄 32.7%로 같은 분포였다.

**적용 범위.** 데이터셋 추출에만 적용한다. 라벨링 입력(`classify/sampling.py`)은 ADR-015와 무관하게 `docs/labeling_guide.md` v1대로 FULL_FUNCTION만 대상이다.

**계약 분리.**
- `pipeline/extract.py`의 중간 추출 JSONL(`to_json_dict`·`write_jsonl`)은 **최종 데이터셋 계약이 아니다.** 이 JSONL에는 `filter_status`·`filter_rule_version`·`filter_evidence` 필드가 없다.
- `NOISE_TRIVIAL` 레코드는 NOISE_MOVE와 같은 구조로 **별도 excluded JSONL에 보존한다** (#97에서 확정, 아래 "제외 레코드 보존" 절). 추출 JSONL에서는 빠진다.

**구현 (#63).**
- `pipeline/filter.py:partition_trivial`이 (유지, 제외)로 나눈다. 이 구현부터 규칙 버전은 **v0.6**이다 (v0.5는 ADR-015/#62의 명세 버전, 구현 전 상태). #63 전후 결과는 버전으로 구분된다.
- **정밀도 재측정은 #89에서 진행한다.** #63(PR #125)에서는 수행하지 않는다.
- `filter_evidence`는 `{"line_count": 줄 수}` — 위 줄 수(`len(deleted_body.splitlines())`, int) 그대로다. 아래 "제외 레코드 보존" 절의 표.
- NOISE_MOVE와 함께 적용할 때: NOISE_MOVE는 FULL_FUNCTION만, NOISE_TRIVIAL은 PARTIAL만 대상이라 제외 대상이 겹치지 않고 적용 순서가 판정에 영향을 주지 않는다. PARTIAL은 이동 후보를 소비하지도 않는다. `pipeline/extract.py:extract_repo_with_excluded`가 커밋마다 `partition_moved` → `partition_trivial` 순으로 적용하고, 두 사유의 제외 레코드를 그 커밋의 추출 순서대로 합친다.
- 경계 테스트(`tests/test_filter.py`): 4줄 → NOISE_TRIVIAL, 5줄 → 유지, FULL_FUNCTION 4줄 이하 → 유지, 빈 줄만 5줄 → 4로 세어 NOISE_TRIVIAL, 빈 줄만 6줄 → 5로 세어 유지.

**한계.**
- 5줄이라는 값은 다시 볼 수 있다. 게이트 1 라벨링은 FULL_FUNCTION만 다루므로 이 값을 직접 검증하지 않는다. 라벨링에 PARTIAL을 포함할지 정할 때 함께 재검토한다.
- **마지막 빈 줄은 세지 않는다.** `deleted_body`는 삭제 줄을 `"\n"`으로 이어 붙인 값(끝 개행 없음)이라, 마지막 삭제 줄이 빈 줄이면 `splitlines()`가 그 줄을 세지 않아 실제 삭제 줄 수보다 1 적게 나온다. 보정하지 않는다(팀 결정). 그래서:
  - 코드 4줄 + 끝 빈 줄 1줄(실제 5줄 삭제) → 4 → `NOISE_TRIVIAL`.
  - 빈 줄만 N줄 삭제 → N−1. 빈 줄만 5줄이면 4 → `NOISE_TRIVIAL`, 6줄이어야 유지된다. 빈 줄 1줄만 삭제되면 0이다.
  - 중간·앞쪽 빈 줄은 센다. 빈 줄이 아닌 줄로 끝나면 실제 삭제 줄 수와 같다.

## 제외 레코드 보존 (#97)

필터가 제외한 레코드는 버리지 않고 별도 JSONL에 사유와 함께 남긴다. §10.1 필터 평가의 "제외 100건" 표본(#89)과 최종 조립 단계의 재료다. 이 절은 판정 규칙이 아니라 출력 구조를 정한다 — 규칙 버전은 올리지 않는다.

**두 파일.**
- **추출 JSONL** (`write_jsonl`): 필터를 통과한 레코드만. 키는 `to_json_dict`의 16개 그대로이고 `filter_status`·`filter_rule_version`·`filter_evidence`를 **넣지 않는다**.
- **excluded JSONL** (`write_excluded_jsonl`): 필터가 제외한 레코드만. 한 줄 = 추출 JSONL과 같은 16개 키 + `filter_status` + `filter_rule_version` + `filter_evidence`. **flat 구조**다 (원본을 `{"record": ...}`로 감싸지 않는다). §4.4의 `reason`(이유 분류)과 헷갈리지 않게 `reason`이라는 이름은 쓰지 않는다.
- 한 레코드는 두 파일 중 정확히 한쪽에만 있다. 두 파일을 합치면 필터 전 전체 추출과 같다. 같은 레코드는 `id`로 식별한다.

**필드.**
- `filter_status`: §4.4 enum 값 그대로. excluded JSONL에는 `NOISE_*`만 온다 (`KEPT`는 오지 않는다).
- `filter_rule_version`: 이 문서의 버전 (`pipeline/filter.py:FILTER_RULE_VERSION`). 노트북 여러 대의 결과를 합치거나 규칙 수정 뒤 재실행할 때 서로 다른 버전이 섞였는지 가려낸다.
- `filter_evidence`: 사유별 판정 근거 객체. 키는 사유마다 다르다.

| filter_status | `filter_evidence` | 상태 |
|---|---|---|
| NOISE_MOVE | `{"file_path", "function_name", "start_line", "end_line", "similarity"}` — **이동 목적지**(자식 커밋) 함수의 경로·이름·줄 범위와 greedy 매칭이 확정한 유사도(`_move_similarity` 값 그대로, 완전 일치면 1.0). 삭제 쪽 값은 행의 최상위 필드에 있다. 함수 이름에 클래스 한정자가 없어 줄 범위를 함께 남긴다 | 구현 (#97) |
| NOISE_TRIVIAL | `{"line_count"}` — 판정에 쓴 줄 수 `len(deleted_body.splitlines())` (int, 0~4). 마지막 빈 줄은 세지 않은 값이다(NOISE_TRIVIAL 절 "한계") | 구현 (#63) |

**출력 경로.** writer는 호출자가 준 경로에 쓰기만 한다 (경로 정책을 코드가 정하지 않는다). 운영 시 추출 JSONL이 `<stem>.jsonl`이면 excluded JSONL은 `<stem>_excluded.jsonl`로 둔다. 제외가 0건이어도 **빈 파일을 만든다** — "제외 없음"과 "excluded 출력을 안 돌림"을 구분하기 위해서다. 둘 다 생성 데이터라 `data/` 아래에 두고 커밋하지 않는다 (`.gitignore`).

**코드.** `pipeline/filter.py:partition_moved`(NOISE_MOVE)와 `partition_trivial`(NOISE_TRIVIAL, #63)이 각각 (남은 레코드, 제외 레코드)를 돌려주고, `pipeline/extract.py:extract_repo_with_excluded`가 커밋별로 둘을 적용해 누적한다. 한 커밋의 제외 레코드는 사유와 무관하게 추출 순서대로 놓인다. 기존 `find_moved`·`exclude_moved`는 반환 계약을 그대로 유지하는 래퍼다 — NOISE_MOVE만 다루고, 판정 결과는 #97 이전과 같다. `extract_repo`는 시그니처와 반환 타입이 그대로이고, #63부터 NOISE_TRIVIAL도 빠진 결과를 돌려준다.

**최종 조립 단계 (미구현).** 추출 JSONL의 행에는 `filter_status = KEPT`를, excluded JSONL의 행에는 그 행의 `filter_status`를 사용해 최종 `DeletionRecord.filter_status`를 구성한다. KEPT 레코드의 `filter_rule_version`은 실행 단위 메타데이터로 추출 시점에 기록한다. 구현은 재헌 5번(병렬화·실패 복구)에서 저장소별 처리 시간·실패율 기록과 함께 한다. 조립 단계 자체는 #97 범위 밖이다.

## 변경 이력
| 버전 | 날짜 | 변경 | 정밀도 |
|---|---|---|---|
| v0 | 2026-09-09 | 골격 | — |
| v0.1 | 2026-09-15 | NOISE_MOVE 세부 규칙 명세 (ADR-014, PR #54, #53). 명세와 버전 표기만 — 테스트·`filter_rule_version` 반영·정밀도 재측정은 #52 구현 PR에서 수행 | — (#52에서 재측정) |
| v0.2 | 2026-09-15 | NOISE_MOVE 최초 구현(Issue #52): 정규화·유사도·20% 줄 수 프리필터(원본 줄 수 기준, 이후 v0.4에서 정규화 줄 수 기준으로 수정) + same-position(헝크 매핑) 판정 | 실측 전 |
| v0.3 | 2026-09-15 | 코드 리뷰 BLOCKER/HIGH 수정(Issue #52): added 후보를 "실제 added line과 겹치는 함수"로 제한(B-1), 1:1 greedy 매칭 도입(H-1), "오탐보다 미탐" 최우선 원칙 명시(ADR-014 예정) | 실측 전 |
| v0.4 | 2026-09-15 | ADR-014 정합(팀장 결정 — ADR-014를 최종 기준으로 코드 수정): placeholder를 VAR/STR/NUM 3종으로 분리(기존 LIT 통합 폐기), 중첩 함수 선언 이름만 VAR로 치환(본문은 재귀 정규화 유지), 정규화 결과를 줄 목록(list[str])으로 변경, 1차 거르기 기준을 원본 줄 수에서 **정규화 줄 수**로 수정, `SequenceMatcher`를 문자열 전체 대신 **줄 목록**으로 비교하고 `autojunk=False` 명시, 정규화 본문 0줄 함수 제외 가드 추가. bool/None/복소수 placeholder 분류는 팀장 최종 확인 완료(bool/None 미치환, 복소수 NUM) — ADR-014 본문 반영은 팀장이 별도 진행 | 실측 전 |
| v0.5 | 2026-09-17 | NOISE_TRIVIAL 규칙 명세 (ADR-015, #62): PARTIAL 중 `deleted_body` 4줄 이하(빈 줄 포함) 제외. 명세와 버전 표기만 — 구현·테스트·`filter_rule_version` 반영·정밀도 재측정은 #63 | — (#63에서 재측정) |
| v0.6 | 2026-09-24 | NOISE_TRIVIAL 실제 구현 (Issue #63 / PR #125, ADR-015 후속 메모): PARTIAL만 대상, `len(deleted_body.splitlines()) <= 4`이면 `NOISE_TRIVIAL`로 제외하고 5줄 이상은 유지. FULL_FUNCTION은 비적용. 마지막 빈 줄(trailing empty line)은 보정하지 않는다. 판정 줄 수를 `filter_evidence.line_count`에 기록하고, 제외 레코드는 #97 `ExcludedRecord`로 excluded JSONL에 보존. 기존 NOISE_MOVE 공개 API(`find_moved`·`partition_moved`·`exclude_moved`) 계약 유지. 정밀도 재측정은 #89에서 진행 | — (#89에서 재측정) |
| v0.7 | 2026-09-24 | NOISE_MOVE 1:1 greedy 매칭의 동점 tie-break 변경 (Issue #80, 283e72d9): 정렬 key를 `(-정규화 유사도, record_key, candidate_key)`에서 `(-정규화 유사도, -원문 유사도, record_key, candidate_key)`로. 원문 유사도는 줄마다 strip·빈 줄 제거한 원문 줄 목록의 `SequenceMatcher(삭제, 추가, autojunk=False).ratio()`이고, threshold를 통과한 pair의 정렬 순서에만 쓴다 — 판정 threshold(0.9)·정규화·후보 범위·1:1·`filter_evidence` 키와 `similarity` 값(정규화 유사도)은 그대로다. 같은 입력의 NOISE_MOVE/KEPT 판정이 바뀔 수 있어 버전을 올린다 (pydantic 사전 분석: 3커밋 24건, 커밋별 NOISE_MOVE 수 불변). ADR-014 결정 내용 변경 없음 | — (#89에서 재측정) |
