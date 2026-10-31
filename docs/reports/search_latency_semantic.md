# 의미 검색 응답 시간 (#193)

CHARTER §10.5 목표 **≤ 1000ms**. `python -m eval.search_latency --mode semantic` 출력이다.

- 색인: `data/search/dataset-v0.9.sqlite` (7.00 GB)
- 호출: TestClient (같은 프로세스) · 질의 12개 × 5회 (질의마다 첫 호출 1회는 따로)
- 환경: Windows 11, Python 3.12.10, SQLite 3.49.1
- 임베딩: `data/search/semantic` - BAAI/bge-small-en-v1.5 384차원 723,087건 (0.75 GB)
- 서버 시작 때 모델·벡터 읽기(첫 요청): **1.3초** - p50·p95 에 넣지 않았다

| 유형 | n | p50 | p95 | 최대 | ≤ 1초 |
|---|---:|---:|---:|---:|:---:|
| semantic | 25 | 46ms | 49ms | 50ms | O |
| semantic-code | 15 | 48ms | 51ms | 52ms | O |
| semantic+filter | 20 | 43ms | 45ms | 45ms | O |
| all | 60 | 46ms | 50ms | 52ms | O |
| first_call | 12 | 46ms | 49ms | 50ms | O |

## 질의별

| 유형 | 파라미터 | 건수 | 받은 행 | 첫 호출 | 중앙값 | 최대 |
|---|---|---:|---:|---:|---:|---:|
| semantic | `q=wait longer between retries after a failed request, mode=semantic` | 723,087 | 20 | 46ms | 46ms | 50ms |
| semantic | `q=cache downloaded files on disk and reuse them, mode=semantic` | 723,087 | 20 | 48ms | 46ms | 50ms |
| semantic | `q=parse command line arguments, mode=semantic` | 723,087 | 20 | 47ms | 47ms | 48ms |
| semantic | `q=compatibility shim for python 2, mode=semantic` | 723,087 | 20 | 46ms | 45ms | 46ms |
| semantic | `q=close database connection when done, mode=semantic` | 723,087 | 20 | 44ms | 45ms | 49ms |
| semantic-code | `q=def load_config(path):⏎    with open(path) as f:⏎        return yaml.safe_load(f), mode=semantic` | 723,087 | 20 | 50ms | 49ms | 51ms |
| semantic-code | `q=for i in range(len(xs)):⏎    total += xs[i], mode=semantic` | 723,087 | 20 | 48ms | 46ms | 52ms |
| semantic-code | `q=async def fetch(session, url):⏎    async with session.get(url) as r:, mode=semantic` | 723,087 | 20 | 46ms | 48ms | 48ms |
| semantic+filter | `q=remove deprecated api, source=HUMAN, mode=semantic` | 500 | 20 | 44ms | 44ms | 45ms |
| semantic+filter | `q=thread safety lock, repo=psf/requests, mode=semantic` | 2,085 | 20 | 43ms | 43ms | 43ms |
| semantic+filter | `q=timeout handling, year_from=2022, grade=INFERRED, mode=semantic` | 96,410 | 20 | 46ms | 43ms | 45ms |
| semantic+filter | `q=remove unused helper, reason=DEAD, offset=20, mode=semantic` | 8,155 | 20 | 42ms | 43ms | 44ms |

## 해석

전체 임베딩(dataset-v0.9 FULL_FUNCTION 723,087건, bge-small-en-v1.5 384차원, 0.75 GB, 생성 324.6분)으로 잰 값이다. 서버 시작 때 모델·벡터를 한 번 읽어 float32 로 메모리에 둔다(첫 요청 칸, p50·p95 에 넣지 않음). 그 뒤 요청은 질의 임베딩 + 723,087 × 384 전수 내적 + 상위 K + 키워드 색인에서 목록 칸 조회다. 필터는 마스크만 바꿔 전수 내적 비용은 그대로라 필터 유무로 차이가 거의 없다. 표본(6,719건) p50 16ms → 전체 약 46ms. CHARTER §10.5 ≤ 1초 충족.
