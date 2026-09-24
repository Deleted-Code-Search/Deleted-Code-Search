# ADR-015: PARTIAL 삭제의 최소 줄 수 기준 — NOISE_TRIVIAL

- 날짜: **2026-09-17**
- 상태: 채택
- 관련 이슈: #62 (이 ADR), #63 (구현, 재헌). #33 (예비 200건 샘플 추출, 희수)은 이 ADR로 바뀌지 않는다
- 결정 경로: 스키마 변경(`filter_status`에 `NOISE_TRIVIAL` 추가)이므로 §13 대상이다. 현재 팀은 정기 회의를 운영하지 않고 비동기로 결정한다. 이 ADR은 조장(성제)이 기안하고 PR 리뷰(1명 필수, §8.2)로 팀 확인을 받는다. 반대 의견은 PR 코멘트로 받는다

## 맥락
- CHARTER.md §4.2 ②와 ADR-003은 **함수 전체 삭제(FULL_FUNCTION)를 1차 대상**으로 정했다. §4.2 파서 계층은 "부분 삭제는 확장"이라고만 적었다.
  **PARTIAL을 어디까지 포함할지는 정해져 있지 않다.**
- 2026-09-17 `pydantic/pydantic` 전체 히스토리 실측(`docs/evaluation.md` 2026-09-17 절). 필터(NOISE_MOVE) 적용 후 22,691건 중 PARTIAL이 19,690건(86.8%)이다.
- PARTIAL의 삭제 줄 수(`deleted_body` 줄 수, 빈 줄 포함) 분포는 두 저장소가 같은 모양이다.

| 삭제 줄 수 | pydantic (필터 적용 후, 19,690건) | requests (필터 연결 전, 4,755건) |
|---|---:|---:|
| 1줄 | 10,326 (52.4%) | 2,337 (49.1%) |
| 2-4줄 | 6,423 (32.6%) | 1,553 (32.7%) |
| 5-9줄 | 1,834 (9.3%) | 555 (11.7%) |
| 10줄 이상 | 1,107 (5.6%) | 310 (6.5%) |

- **표본에서 본 것 (출처를 구분한다).**
  - 1~4줄 PARTIAL의 실제 사례는 `psf/requests` 최근 300커밋 재표본(2026-09-14, 커밋 d63e94f 제외, 20건 중 PARTIAL 19건)에서 나왔다.
    타입 힌트 한 줄(`headers: Mapping[str, str | bytes] | None = None,`), 파라미터 한 줄(`strict=True,`), 빈 줄만 삭제된 레코드 2건 등이다.
  - 2026-09-17 pydantic 표본 20건은 PARTIAL을 5줄 이상에서만 뽑았다. 그래서 1~4줄 사례는 들어 있지 않다.
  - 조장의 판단: 1~4줄 PARTIAL은 타입 힌트 수정, 파라미터 제거, 딕셔너리 조각 삭제 같은 **함수 수정**이다.
    CHARTER §0이 말한 "이 방식은 해봤는데 이래서 버렸다"는 실패 지식이 아니다.

## 결정
- **적용 범위는 데이터셋 추출이다.** PARTIAL 레코드는 **`deleted_body`가 5줄 이상일 때만** 데이터셋에 포함한다.
- **라벨링 대상 범위는 이 ADR에서 변경하지 않는다.** 예비 200건과 본 500건은 `docs/labeling_guide.md` v1대로 FULL_FUNCTION만 대상으로 한다. 라벨링에 PARTIAL을 포함할지는 게이트 1 이후 별도 결정으로 남긴다.
- **4줄 이하 PARTIAL은 `filter_status`를 `NOISE_TRIVIAL`로 표시해 제외한다.**
- 줄 수는 `deleted_body`의 줄 수이고 **빈 줄을 포함해 센다** (위 실측과 같은 기준). 구현(#63)은 정확히 `len(deleted_body.splitlines())`이며 마지막 빈 줄은 세지 않는다 (아래 "한계").
- FULL_FUNCTION은 줄 수와 무관하게 이 규칙의 대상이 아니다.
- §4.4 `DeletionRecord.filter_status` enum에 `NOISE_TRIVIAL`을 추가한다 (스키마 변경, §13).

## 이유
- **1~4줄 삭제는 함수를 버린 것이 아니라 다듬은 것이다.** 삭제 이유를 물을 대상이 아니다.
- **데이터가 모자라지 않는다.** pydantic 기준 5줄 이상 PARTIAL은 2,941건(PARTIAL의 14.9%)으로, FULL_FUNCTION 3,001건과 비슷한 규모다.
- **경계값 5.** 실측 분포에서 1-4줄과 5줄 이상 사이가 자연스러운 구분점이라 골랐다. 근거는 표본 읽기이며, 정밀한 최적화는 아니다.
- **라벨링 범위를 바꾸지 않는 이유.** 라벨 가이드 v1이 FULL_FUNCTION을 전제로 작성됐다 (가이드 §2, PARTIAL은 v1 범위 밖).
  또 게이트 1은 이유가 기록돼 있는지를 재는 것이라 FULL_FUNCTION만으로 답이 나온다.

## 대안과 버린 이유
- **PARTIAL 전부 포함** — pydantic에서 4줄 이하가 PARTIAL의 85.1%(16,749건)다. 데이터셋의 PARTIAL 대부분이 함수 수정으로 채워진다.
- **PARTIAL 전부 제외 (FULL_FUNCTION만)** — 5줄 이상 PARTIAL 2,941건을 버린다. FULL_FUNCTION 3,001건과 비슷한 규모다.

## 한계
- **5줄이라는 값은 다시 볼 수 있다.** 게이트 1 라벨링은 FULL_FUNCTION만 다루므로 이 값을 직접 검증하지 않는다.
  라벨링에 PARTIAL을 포함할지 정할 때 함께 재검토하고, 포함한다면 5-9줄 구간에서 UNK 비율이 높게 나올 때 기준을 올린다.
- **줄 수는 빈 줄을 포함해 센다.** 주석만 5줄 이상 삭제된 PARTIAL도 유지된다. 다만 줄 수는 `len(deleted_body.splitlines())`이고
  `deleted_body`는 삭제 줄을 `"\n"`으로 이어 붙인 값(끝 개행 없음)이라, **마지막 삭제 줄이 빈 줄이면 그 줄은 세지 않는다** (보정하지 않는다, #63).
  빈 줄만 5줄 삭제된 PARTIAL은 4로 세어 `NOISE_TRIVIAL`이 되고, 6줄이어야 유지된다. 코드 4줄 뒤에 빈 줄 1줄이 함께 삭제된 경우도 4로 세어 제외된다.
- 삭제 줄만 센다. 대체(추가) 코드의 줄 수는 보지 않는다.
- 근거 표본이 얇다. 1~4줄 사례는 requests 재표본 PARTIAL 19건에서 나왔고 판정자는 1인이다.

## 영향
- `pipeline/filter.py` 구현: **#63 (재헌)**. 추출 JSONL에는 `filter_status` 필드가 없고, NOISE_TRIVIAL 레코드는 NOISE_MOVE와 같은 방식으로 추출 JSONL에서 빠져 **excluded JSONL에 보존된다** (#97 구조, `filter_evidence = {"line_count": 줄 수}`, `docs/filter_rules.md` "제외 레코드 보존" 절).
- CHARTER.md: §4.2 ②에 규칙 추가, §4.4 `filter_status`에 `NOISE_TRIVIAL` 추가, §5 표에 행 추가, §19 기록 (v1.13).
- `docs/filter_rules.md`: NOISE_TRIVIAL 행과 세부 규칙 추가, 규칙 버전 v0.4 → v0.5.
- **라벨링 입력은 바뀌지 않는다.** 예비 200건과 본 500건은 `docs/labeling_guide.md` v1대로 FULL_FUNCTION만 대상이다.
  `classify/sampling.py`(희수 영역, `TARGET_DELETION_KIND = "FULL_FUNCTION"`)는 그대로 두며 변경이 필요 없다.
  라벨링에 PARTIAL을 포함할지는 게이트 1 이후 별도 결정이다.
- 필터 규칙 변경이므로 §10.1 정밀도 재측정 대상에 포함한다 (수동 200건: 통과 100 + 제외 100).
- 라벨 데이터가 아직 없어 재라벨 비용은 없다.
