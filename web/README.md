# web

React + Vite 대시보드·검색 UI (CHARTER.md §4.2 통계 대시보드, §4.2 ⑤). 담당: 성제.

- **대시보드** 탭 (#93): 헤드라인 숫자, 라벨 출처, 이유·근거 등급 분포(확정 500건 / LLM 표본), 저장소별 표, 연도별 추이, 모델·필터 성능, 시스템 평가
- **검색** 탭 (#191, 1단계 키워드 검색): 검색창 + 필터(저장소·이유·근거 등급·출처·FULL/PARTIAL·연도) +
  결과 목록(매칭 부분 강조) + 상세 패널(지워진 코드·커밋·PR·이슈·대체 코드·이유·근거 문장·GitHub 링크).
  **"키워드 / 의미" 전환** (#193, 2단계): 의미 모드는 여러 줄 입력(코드 붙여넣기, Ctrl+Enter 로 검색),
  결과에 유사도. FULL_FUNCTION 만 찾는다 (ADR-023). 주소 `#search?…&mode=semantic`

대시보드 숫자는 모두 `docs/reports/dashboard_stats.json`(`python -m search.stats` 출력)에서 온다. API(`search/api.py`)가
그 파일을 `GET /api/stats` 로 돌려주고, 이 앱은 그것만 그린다. 검색은 `data/search/dataset-v0.9.sqlite`
(`python -m search.index` 출력, SQLite FTS5)를 `GET /api/search`·`/api/search/meta`·`/api/records/{id}` 로 읽는다.

## 실행 (Windows PowerShell)

필요한 것: Docker Desktop, Node.js 20 이상, 저장소 루트의 `.venv`(Python 3.12).

```powershell
# 1. Release 데이터 받기 (최초 1회, 약 700MB)
gh release download dataset-v0.9 -p "*.jsonl.gz" -D data/export/dataset-v0.9

# 2. 집계 → docs/reports/dashboard_stats.json  (표준 라이브러리만 쓴다)
.venv\Scripts\activate
python -m search.stats
#   실행 기록(시스템 평가 패널)을 넣으려면 pipeline.run 출력 폴더를 --runs 로 준다 (여러 번 가능)
#   python -m search.stats --runs data/run100_sj --runs data/run100_hs --runs data/run100_jh `
#       --runs data/run_sj --runs data/run_hs --runs data/run_jh --runs data/run_ha

# 3. 검색 색인 → data/search/dataset-v0.9.sqlite  (표준 라이브러리만, 약 5분 · 약 7GB)
python -m search.index

# 3-1. (의미 검색) 임베딩 → data/search/semantic/  (FULL_FUNCTION 72만 건, 약 3~5시간, 이어 하기)
pip install -e ".[embed]"
python -m search.embed
#   표본만: python -m search.embed --repos pallets/click,psf/requests --out data/search/semantic-sample
#   그 폴더를 쓰려면 API 를 띄울 때 $env:SEARCH_SEMANTIC = "data/search/semantic-sample"

# 4. API (컨테이너, http://localhost:8000)
docker compose up api
#   확인: http://localhost:8000/api/health
#   컨테이너 안에서 전체 테스트: docker compose run --rm api pytest

# 5. 프론트 (새 PowerShell 창, http://localhost:5173)
cd web; npm install; npm run dev
```

- API 주소를 바꾸려면 `web/.env.local` 에 `VITE_API_BASE=http://localhost:8000` 처럼 적는다
- API 는 요청마다 json 을 다시 읽고 색인을 다시 연다. 2·3번을 다시 돌리면 브라우저만 새로고침하면 된다
  (색인은 임시 파일에 만든 뒤 바꿔 끼운다). 색인이 없으면 검색 API 는 503 + "python -m search.index 먼저"
- 검색어는 공백으로 나눈 조각마다 AND 다. `retry_backoff` 는 "retry backoff" 구문, 끝의 `*` 는 접두어
  (`retr*`). 대상 칼럼: 함수 이름·경로·커밋 메시지·PR 제목·이슈 제목·지워진 코드(`LINK_ONLY` 는 코드 없음).
  검색어 없이 필터만으로도 찾는다. 총 건수는 10,000 까지만 센다 ("10,000건 이상")
- 의미 검색: 서버가 시작할 때 모델·벡터를 한 번 읽는다 (임베딩을 다 만든 뒤에는 서버를 다시 띄우지 않아도
  첫 요청에 읽는다). 벡터는 float32 로 메모리에 둔다(72만 건 약 1.1 GB). 모자라면
  `$env:SEARCH_SEMANTIC_MMAP = "1"`. 임베딩이 없으면 의미 검색만 503, 키워드는 그대로
- 응답 시간: `python -m eval.search_latency` → `docs/reports/search_latency.md` (CHARTER §10.5 ≤ 1초),
  `--mode semantic` → `docs/reports/search_latency_semantic.md`
- 관련성: `python -m eval.search_relevance export` → 블라인드 시트 `docs/reports/search_relevance_sheet.csv`
  (모드·순위 숨김, 판정자 성제·희수가 `relevant_sj`·`relevant_hs` 를 채운다) + 키
  `search_relevance_key.json`, `python -m eval.search_relevance score` → 판정자별 P@10·MRR·일치도.
  절차는 `docs/reports/search_relevance.md`
- `search.stats` 는 집계값을 `summary.json`·`repos.csv` 와 맞춰 보고 다르면 종료 코드 2로 끝난다 (`checks`)
- Docker 없이: `pip install -e ".[web]"` 뒤 `uvicorn search.api:app --reload`. 스마트 앱 컨트롤이 켜진
  노트북은 `pydantic_core` DLL 이 막힐 수 있다 (그래서 Docker 가 기본이다)

## 구조

```
web/src/
  App.tsx              탭 2개, /api/stats 불러오기
  api.ts               API 주소, getJson (실패 시 API 의 detail 을 그대로)
  types.ts             dashboard_stats.json 모양, 검색 API 응답 모양
  format.ts            숫자 형식, 출처·이유·등급 이름 (CHARTER §4.2 ③)
  components/          패널마다 하나. 모든 패널이 Panel 로 출처·n 을 단다
    SearchTab.tsx      검색 탭 (키워드/의미 전환·검색창·필터·결과·페이지·예시 검색어)
    RecordDetail.tsx   상세 패널 (/api/records/{id})
    ReasonBadges.tsx   이유·등급·출처 배지 - 출처 배지는 항상 단다
```

색은 계열 2개(사람 확정 = 파랑, LLM 표본 / PARTIAL = 주황)만 쓴다. 라이트·다크 모두 정의되어 있다.
