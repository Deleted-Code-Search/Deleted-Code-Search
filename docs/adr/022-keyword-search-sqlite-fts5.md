# ADR-022: 검색 1단계는 SQLite FTS5 키워드 검색

- 날짜: 2026-10-30
- 상태: 제안 (PR 리뷰 승인으로 채택)
- 관련 이슈: #191, #8(모델 비교·임베딩), ADR-009(PostgreSQL + pgvector), #83(DB 적재는 #8 때 별도 이슈)

## 맥락

- CHARTER §4.2 ⑤ 검색은 "임베딩 → pgvector → 상위 K" 이고 ADR-009 가 저장소를 PostgreSQL + pgvector 로 정했다. 임베딩 모델은 §17 미결정이고 #8(모델 비교)이 끝나야 정해진다.
- 그 전에 시연 영상의 검색 장면(§12.3 "검색 → 이유 → 대체 코드")과 §10.5 "검색 응답 ≤ 1초" 측정이 필요하다. 입력은 Release `dataset-v0.9` 의 jsonl.gz 100개, **1,354,052행**이다.
- 이 작업을 하는 노트북에는 Docker 가 없고, 스마트 앱 컨트롤이 일부 네이티브 DLL(pyarrow 등)을 막는다. PostgreSQL 을 직접 설치·운영하는 것도 같은 문제를 다시 만난다.

## 결정

1. **검색 1단계는 키워드 검색이고, 저장소는 SQLite + FTS5(파이썬 표준 `sqlite3`)다.** 색인은 `python -m search.index` → `data/search/dataset-v0.9.sqlite` (gitignore). jsonl.gz 를 한 줄씩 읽는다.
2. FTS 대상: `function_name`, `file_path`, `context.commit_message`, `context.pr_title`, `context.issue_titles`, `deleted_body`. 순위는 bm25(칼럼 가중치 10·4·3·3·2·1). `LINK_ONLY` 는 Release 에 본문이 없어 본문이 색인되지 않는다.
3. 필터(저장소·이유·등급·출처·FULL/PARTIAL·연도)는 같은 FTS 표의 `facets` 칼럼에 토큰으로 넣고, 키워드가 있든 없든 MATCH 안에서 역색인 교집합으로 푼다. 사용자 검색어는 `{6개 칼럼} : (…)` 로 제한해 facets 토큰에 맞지 않는다. records 표에는 B-tree 색인을 만들지 않는다.
4. 상세 표시용 레코드 원문은 같은 DB 에 zlib 압축 JSON 으로 둔다 (Release 한 줄 그대로).
5. **ADR-009(PostgreSQL + pgvector)는 2단계(임베딩 검색)용으로 그대로 둔다.** 이 ADR 은 ADR-009 를 대체하지 않는다. 2단계에서 하이브리드(임베딩 + 키워드, §4.2 ⑤)를 할 때 이 키워드 색인을 기준선·후보 생성기로 다시 쓸지 정한다.

## 이유

- **Docker·DLL**: 표준 라이브러리만 쓰므로 이 노트북에서 그대로 돈다. 서버 프로세스도 없다 - API 가 파일을 읽기 전용으로 연다.
- **135만 건 키워드 검색에 충분하다** (실측, `docs/reports/search_latency.md`): 색인 생성 4.0분, 파일 7.00 GB. 응답 시간 p50 28ms · p95 388ms · 최대 656ms (질의 20개 × 5회, HTTP 127.0.0.1, 데워진 캐시). 가장 느린 것은 접두어 `get_config*` 와 흔한 단어 `self`.
- 1단계가 필요한 건 "이 단어가 들어간 삭제를 이유와 함께" 다. 의미 검색은 임베딩 모델이 정해진 뒤에야 할 수 있고, 그때 키워드 검색은 §10.3 의 기준선(키워드 매칭)으로도 쓰인다.

측정하며 바꾼 것 (시도 → 문제 → 대안):

- `SELECT …, snippet(…) … ORDER BY bm25 LIMIT 20` 한 번에 → 흔한 단어(`self` 1.3초, `test` 1.1초)는 정렬 전에 맞은 수십만 행 모두에 snippet 을 만든다 → **순위는 rowid 만으로 먼저 정하고 그 쪽 20행에만 snippet** (0.2~0.3초).
- 키워드 + 필터를 records 와 JOIN 해서 → `self` + 저장소 필터 1.2초 (맞은 행마다 records 조회) → **필터 값을 `facets` 칼럼 토큰으로** 넣어 FTS5 안에서 교집합 (0.06초).
- 필터만(q 없음)을 records 칼럼 B-tree 색인으로 → 필터 2개 이상이면 0.65초 → 같은 facets 교집합을 rowid 순으로 (0.02초). records 색인 6개는 지웠다.
- 총 건수를 전부 세면 흔한 단어에서 느려진다 → 10,000 건까지만 세고 "10,000건 이상" 으로 표시.

## 대안과 버린 이유

- **PostgreSQL + pgvector 를 1단계부터 (ADR-009 그대로)** — 노트북에 Docker 가 없고 직접 설치는 DLL·서비스 운영 문제를 다시 만난다. 임베딩이 없는 1단계에서는 pgvector 가 할 일이 없고, PostgreSQL 전문 검색(tsvector)은 SQLite FTS5 와 기능이 비슷하다.
- **PostgreSQL 없이 Elasticsearch / Meilisearch / Tantivy** — 별도 서버·바이너리가 필요하다(같은 설치 문제). ADR-009 에서 Elasticsearch 를 버린 이유(운영 부담)도 그대로다.
- **Parquet + DuckDB(FTS 확장)** — pyarrow·duckdb 네이티브 모듈이 스마트 앱 컨트롤에 막힌다 (#93 때 확인).
- **trigram 토크나이저** — 부분 문자열 검색이 되지만 색인이 여러 배로 커진다. 코드 이름은 `_` 로 나뉘어 unicode61 토큰으로도 대부분 찾힌다.
- **grep 식 선형 검색(jsonl.gz 를 매번 훑기)** — 135만 행을 질의마다 읽으면 1초 목표를 넘는다.

## 영향

- 스키마(`DeletionRecord`, §4.4)·분류 체계·파서 인터페이스는 바꾸지 않는다. 검색 API 3개가 추가된다 (`/api/search`, `/api/search/meta`, `/api/records/{id}`, `search/api.py`).
- 색인 파일이 약 7.00 GB 다 (원문 zlib 2.5GB + FTS 원문 사본 2.2GB + 역색인 0.8GB + 목록 칼럼·페이지 여유분 약 1.4GB). 줄여야 하면 FTS 를 contentless 로 바꾸고 snippet 을 파이썬에서 만드는 방법이 있다 (약 2GB 절약, 이번에는 하지 않았다).
- CHARTER §5 ADR 표에 이 ADR 행을 넣는 것과 §4.2 ⑤ 에 "1단계 키워드 / 2단계 임베딩" 을 적는 것은 팀 확인 뒤 (§13).
- 검색 관련성 평가(Precision@10, §10.3)는 범위 밖이다 (#8 이후).
