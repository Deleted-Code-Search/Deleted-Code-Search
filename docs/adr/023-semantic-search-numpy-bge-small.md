# ADR-023: 검색 2단계 의미 검색은 bge-small-en-v1.5 + numpy 벡터 파일

- 날짜: 2026-10-30
- 상태: 제안 (PR 리뷰 승인으로 채택)
- 관련 이슈: #8(모델 비교), #193(의미 검색), ADR-009(PostgreSQL + pgvector), ADR-022(검색 1단계 SQLite FTS5)
- 결정 경로: 기술 스택(§7)·저장 방식(§5 표)·§17 미결정 항목 결정이라 §13 절차를 따른다. **이슈 #8·#193 → 팀 확인(PR 리뷰 승인으로 갈음) → 이 ADR → CHARTER v1.22 §4.2 ⑤·§5 표·§7·§17.** 반대 의견은 PR 코멘트로 받는다

## 맥락

- CHARTER §4.2 ⑤ 2단계: 자연어 또는 코드 조각 → 임베딩 → 상위 K. 임베딩 모델은 §17 미결정이었고, 저장은 ADR-009(PostgreSQL + pgvector)였다. ADR-022 는 1단계를 SQLite FTS5 로 하며 ADR-009 를 2단계용으로 남겼다.
- 대상은 Release `dataset-v0.9` 의 FULL_FUNCTION **723,087건**이다 (PARTIAL 은 넣지 않는다 — 함수 일부라 "이 함수와 비슷한 삭제" 질의에 맞지 않는다).
- 이 노트북에는 Docker 가 없고 스마트 앱 컨트롤이 일부 네이티브 DLL(torch, pyarrow)을 막는다. GPU 도 없다. fastembed + ONNX Runtime 은 동작한다 (확인됨).
- 생성은 노트북 한 대에서 밤새 한 번(8시간 이내)에 끝나야 한다.

## 결정

1. **모델은 `BAAI/bge-small-en-v1.5`(384차원, MIT), 토큰 상한 256.** fastembed(ONNX)로 돌린다. 모델 캐시는 `data/models/`.
2. 임베딩할 텍스트: `function_name` + `file_path` + `deleted_body`(글자 4,000자, 토큰 256에서 자름). `LINK_ONLY` 는 본문이 없으므로 `function_name` + `file_path` + 커밋 메시지 첫 줄. 어느 쪽인지 메타 `text_kind` 에 남긴다.
3. **벡터는 PostgreSQL + pgvector 대신 numpy 파일로 둔다** (`python -m search.embed` → `data/search/semantic/`, gitignore): `vectors.npy`(float16, L2 정규화), `ids.npy`, `meta.npy`(필터용 int16 — 저장소·연도·이유·등급·출처·code_policy·text_kind), `manifest.json`(모델·차원·건수·생성 시각·입력 sha256).
4. 생성은 입력 jsonl.gz 파일(= 저장소) 단위 조각으로 저장하고, 다시 돌리면 같은 입력 sha·모델·상한의 조각은 건너뛴다. 끝에 조각을 합친다.
5. 검색(`search/semantic.py`)은 정확 검색(전수 내적)이다. API 서버가 시작할 때 모델과 벡터를 한 번 읽고, 벡터는 float32 로 메모리에 둔다(1.1 GB). `GET /api/search?mode=semantic` — 기존 필터 그대로, 결과에 `score`(코사인). 목록 칼럼·상세는 1단계 SQLite 색인을 id 로 재사용한다.
6. **ADR-009 는 이 ADR 로 대체된다** (벡터 저장). 관계형 메타 저장도 1단계(ADR-022)에서 SQLite 로 갔으므로 PostgreSQL 은 현재 쓰는 곳이 없다. 하이브리드(임베딩 + 키워드, §4.2 ⑤)는 이번 범위가 아니다 — 두 모드를 따로 두고 Precision@10 으로 비교한다.

## 이유

모델 비교 (`docs/reports/embedding_model_choice.md`, 표본 50건 + 질의 10개 + 처리량 1,000건):

| 모델 (토큰 상한) | 차원 | 크기 | ms/함수 | 72만 건 | 의도한 함수 1위 |
|---|---:|---:|---:|---:|---:|
| jina-embeddings-v2-base-code (128 / 256) | 768 | 644 MB | 49 / 79 | 9.9 h / 15.9 h | 9 / 9 |
| granite-embedding-small-english-r2 (256) | 384 | 197 MB | 26 | 5.2 h | 8 |
| **bge-small-en-v1.5 (256)** | 384 | 67 MB | 26.5 | **5.3 h** (전체 실측 5.4 h = 324.6분) | **10** |

- bge-small 256 은 8시간 안에 들고 (전체 생성 실측 324.6분, 0.75 GB), 참고 지표(의도한 함수 순위)가 가장 좋고, 가장 작다.
- **numpy 파일이 충분하다**: 384차원 72만 건은 float16 0.56 GB 다. float32 로 메모리에 올려 전수 내적 + 상위 K 가 0.035초(합성 벡터 723,087 × 384 실측)라 근사 인덱스(HNSW·IVF)가 필요 없고 결과가 정확하다. Docker·PostgreSQL 설치 없이 이 노트북에서 돈다. 필터는 meta 정수 배열 비교로 0.003초(필터 5개, 합성 메타 72만 행)다.
- 측정하며 바꾼 것: 처음에는 float16 메모리맵을 청크마다 float32 로 바꿔 내적했다 → 요청당 0.45초 중 0.38초가 형 변환이었다 → 서버 시작 때 한 번 float32 로 바꿔 둔다 (요청당 0.035초). 메모리가 모자라면 `SEARCH_SEMANTIC_MMAP=1` 로 예전 방식.

## 대안과 버린 이유

- **jina-embeddings-v2-base-code** — 코드 전용이고 1·2위 간격이 가장 커서 질은 가장 좋아 보인다. 그러나 128 토큰에서도 9.9시간(8시간 초과), 768차원이라 파일·메모리가 두 배다. 다음 데이터셋 버전에서 시간이 있으면 다시 본다.
- **granite-embedding-small-english-r2** — 속도는 같고, 의도한 함수 2개를 10위로 놓쳤다. 유사도가 0.75~0.9 에 몰려 있다.
- **PostgreSQL + pgvector (ADR-009 그대로)** — Docker 가 없고 직접 설치는 서비스 운영·DLL 문제를 다시 만난다 (ADR-022 와 같은 이유). 72만 × 384 는 근사 인덱스가 필요 없는 크기라 pgvector 의 이점(HNSW)이 크지 않다.
- **FAISS / hnswlib / Chroma 등** — 네이티브 모듈이 하나 더 늘고(스마트 앱 컨트롤 위험), 전수 내적이 이미 0.035초다.
- **PARTIAL 도 임베딩** — 함수 일부(5줄 이상 조각)라 질의와 단위가 다르고 건수가 거의 두 배라 시간이 8시간을 넘는다.
- **임베딩을 SQLite 색인에 BLOB 으로** — 전수 내적을 하려면 결국 전부 numpy 로 읽어야 해서 이득이 없다.

## 영향

- 스키마(`DeletionRecord`, §4.4)·분류 체계·파서 인터페이스는 바꾸지 않는다. `/api/search` 에 `mode=keyword|semantic` 파라미터가 늘었다 (기본 keyword, 기존 동작 그대로). `/api/search/meta` 에 `semantic`, `/api/health` 에 `semantic_index` 칸이 늘었다.
- 새 선택 의존성 `embed` (fastembed, numpy). API 서버에서 의미 검색을 쓰려면 필요하다. 테스트는 가짜 모델로 돌아 fastembed 없이 통과한다.
- 서버 메모리가 약 1.1 GB(벡터) + 모델 약 0.1 GB 늘어난다. 서버 시작이 수 초 늘어난다.
- 벡터 파일은 Release 에 올리지 않는다 — 각자 `python -m search.embed` 로 만든다 (모델·입력 sha 가 manifest 에 있어 같은 결과인지 확인할 수 있다). Release 에 올릴지는 시연 준비 때 정한다.
- 모델을 바꾸면 72만 건을 다시 만들어야 한다 (조각의 모델 이름이 달라 건너뛰지 않는다).
- CHARTER v1.22: §4.2 ⑤ (인덱스·모델), §5 표 (ADR-009 행에 대체 표시, ADR-023 행), §7 (DB·임베딩 행), §17 (임베딩 모델 체크), 변경 이력.
