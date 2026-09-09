# datasets

- `labeled_500.jsonl` — 수동 라벨 500건 (검증용). 사람이 라벨링한다. LLM 출력은 정답으로 넣지 않는다 (ADR-005).
- `export/` — 공개 데이터셋(JSONL + Parquet) 빌드 스크립트. 스키마는 CHARTER.md §4.4.

라이선스: 데이터셋 CC BY 4.0, 포함 스니펫은 원 저장소 라이선스·출처 링크 유지 (ADR-011). permissive 라이선스(MIT/Apache/BSD) 저장소만 포함 (ADR-007).

대용량 산출물(추출 결과, 클론된 저장소)은 커밋하지 않는다. `.gitignore` 참조.
