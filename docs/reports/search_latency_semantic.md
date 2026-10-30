# 의미 검색 응답 시간 (#193)

CHARTER §10.5 목표 **≤ 1000ms**. `python -m eval.search_latency --mode semantic` 출력이다.

- 색인: `data/search/dataset-v0.9.sqlite` (7.00 GB)
- 호출: TestClient (같은 프로세스) · 질의 12개 × 5회 (질의마다 첫 호출 1회는 따로)
- 환경: Windows 11, Python 3.12.10, SQLite 3.49.1
- 임베딩: `data/search/semantic-sample` - BAAI/bge-small-en-v1.5 384차원 6,719건 (0.01 GB)
- 서버 시작 때 모델·벡터 읽기(첫 요청): **0.5초** - p50·p95 에 넣지 않았다

| 유형 | n | p50 | p95 | 최대 | ≤ 1초 |
|---|---:|---:|---:|---:|:---:|
| semantic | 25 | 11ms | 12ms | 12ms | O |
| semantic-code | 15 | 12ms | 13ms | 13ms | O |
| semantic+filter | 20 | 10ms | 11ms | 11ms | O |
| all | 60 | 11ms | 12ms | 13ms | O |
| first_call | 12 | 17ms | 18ms | 19ms | O |

## 질의별

| 유형 | 파라미터 | 건수 | 첫 호출 | 중앙값 | 최대 |
|---|---|---:|---:|---:|---:|
| semantic | `q=wait longer between retries after a failed request` | 6,719 | 12ms | 11ms | 12ms |
| semantic | `q=cache downloaded files on disk and reuse them` | 6,719 | 16ms | 11ms | 12ms |
| semantic | `q=parse command line arguments` | 6,719 | 19ms | 11ms | 11ms |
| semantic | `q=compatibility shim for python 2` | 6,719 | 17ms | 11ms | 11ms |
| semantic | `q=close database connection when done` | 6,719 | 17ms | 11ms | 11ms |
| semantic-code | `q=def load_config(path):⏎    with open(path) as f:⏎        return yaml.safe_load(f)` | 6,719 | 13ms | 12ms | 13ms |
| semantic-code | `q=for i in range(len(xs)):⏎    total += xs[i]` | 6,719 | 17ms | 12ms | 13ms |
| semantic-code | `q=async def fetch(session, url):⏎    async with session.get(url) as r:` | 6,719 | 18ms | 12ms | 12ms |
| semantic+filter | `q=remove deprecated api, source=HUMAN` | 50 | 17ms | 11ms | 11ms |
| semantic+filter | `q=thread safety lock, repo=django/django` | 0 | 10ms | 10ms | 10ms |
| semantic+filter | `q=timeout handling, year_from=2022, grade=EXPLICIT` | 6 | 12ms | 10ms | 10ms |
| semantic+filter | `q=security fix, reason=SEC, offset=20` | 1 | 9ms | 10ms | 10ms |

## 해석

표본 임베딩(저장소 3개, 6,719건)으로 잰 값이다. 전체 723,087건에서는 내적이 108배 커진다 - 합성 벡터 723,087 × 384 float32 메모리 내적+상위 K 는 약 0.04초, float16 메모리맵 청크 변환은 약 0.45초였다. 실데이터 측정은 전체 임베딩 후 갱신.
