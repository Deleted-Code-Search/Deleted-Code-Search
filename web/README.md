# web

React + Vite 대시보드·검색 UI (CHARTER.md §4.2 통계 대시보드, §4.2 ⑤). 담당: 성제.

- **대시보드** 탭 (#93): 헤드라인 숫자, 라벨 출처, 이유·근거 등급 분포(확정 500건 / LLM 표본), 저장소별 표, 연도별 추이, 모델·필터 성능, 시스템 평가
- **검색** 탭: 자리만 있다 ("준비 중", #8)

숫자는 모두 `docs/reports/dashboard_stats.json`(`python -m search.stats` 출력)에서 온다. API(`search/api.py`)가
그 파일을 `GET /api/stats` 로 돌려주고, 이 앱은 그것만 그린다.

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

# 3. API (컨테이너, http://localhost:8000)
docker compose up api
#   확인: http://localhost:8000/api/health
#   컨테이너 안에서 전체 테스트: docker compose run --rm api pytest

# 4. 프론트 (새 PowerShell 창, http://localhost:5173)
cd web; npm install; npm run dev
```

- API 주소를 바꾸려면 `web/.env.local` 에 `VITE_API_BASE=http://localhost:8000` 처럼 적는다
- API 는 요청마다 json 을 다시 읽는다. 2번을 다시 돌리면 브라우저만 새로고침하면 된다
- `search.stats` 는 집계값을 `summary.json`·`repos.csv` 와 맞춰 보고 다르면 종료 코드 2로 끝난다 (`checks`)
- Docker 없이: `pip install -e ".[web]"` 뒤 `uvicorn search.api:app --reload`. 스마트 앱 컨트롤이 켜진
  노트북은 `pydantic_core` DLL 이 막힐 수 있다 (그래서 Docker 가 기본이다)

## 구조

```
web/src/
  App.tsx              탭 2개, /api/stats 불러오기
  types.ts             dashboard_stats.json 모양
  format.ts            숫자 형식, 출처·이유·등급 이름 (CHARTER §4.2 ③)
  components/          패널마다 하나. 모든 패널이 Panel 로 출처·n 을 단다
```

색은 계열 2개(사람 확정 = 파랑, LLM 표본 / PARTIAL = 주황)만 쓴다. 라이트·다크 모두 정의되어 있다.
