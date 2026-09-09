# 필터 규칙

버전: v0 (작성 예정). 담당: 재헌. 규칙 변경 시 이 문서의 버전을 올리고, `DeletionRecord.filter_rule_version`에 반영하며, PR에 테스트와 정밀도 재측정 결과(수동 100건)를 첨부한다 (CHARTER.md §8.4, §10.1).

## 노이즈 제외 대상 (CHARTER.md §4.2 ②)
| filter_status | 규칙 | 상태 |
|---|---|---|
| NOISE_MOVE | 같은 커밋에서 정규화 본문 유사도 ≥ 0.9인 함수가 다른 위치에 추가됨 | 미구현 |
| NOISE_RENAME | 본문 동일, 이름만 변경 | 미구현 |
| NOISE_FORMAT | 포맷·주석·독스트링만 변경 | 미구현 |
| NOISE_BULK | 파일 전체 삭제 + "remove/delete directory/module" 계열 메시지 + 함수 100개 이상 | 미구현 |
| NOISE_GENERATED | 마이그레이션·자동 생성·vendored 경로 패턴 | 미구현 |

테스트 코드 삭제는 제외하지 않고 `is_test_code` 플래그로 구분한다.

## 변경 이력
| 버전 | 날짜 | 변경 | 정밀도 |
|---|---|---|---|
| v0 | 2026-09-09 | 골격 | — |
