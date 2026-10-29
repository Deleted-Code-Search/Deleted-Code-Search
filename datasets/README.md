# datasets

- `labeled_500.jsonl` — 수동 라벨 500건 (검증용). 사람이 라벨링한다. LLM 출력은 정답으로 넣지 않는다 (ADR-005).
- `dataset-v0.9/` — 공개 데이터셋 dataset-v0.9 (#83). 문서는 [`dataset-v0.9/README.md`](dataset-v0.9/README.md). Git 에는 확정 500건(`main500.jsonl`)·저장소 목록·학습 저장소 20개·요약만 넣고, 저장소 100개 전체(Parquet + jsonl.gz)는 GitHub Release `dataset-v0.9` 로 낸다. 빌드는 `python -m pipeline.export`.
- `export/` — 내보내기 결과를 여기 두지 않는다. 대용량 출력은 gitignore 된 `data/export/` 에 만든다.

라이선스: 데이터셋 CC BY 4.0, 포함 스니펫은 원 저장소 라이선스·출처 링크 유지 (ADR-011). 코드 본문은 permissive 라이선스(MIT/Apache/BSD) 저장소만 포함하고, 그 밖은 링크만 (ADR-007).

대용량 산출물(추출 결과, 클론된 저장소)은 커밋하지 않는다. `.gitignore` 참조.
