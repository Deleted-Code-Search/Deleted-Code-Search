# 의미 검색 응답 시간 (#193)

CHARTER §10.5 목표 **≤ 1000ms**. `python -m eval.search_latency --mode semantic` 출력이다.

- 색인: `data/search/dataset-v0.9.sqlite` (7.00 GB)
- 호출: TestClient (같은 프로세스) · 질의 12개 × 5회 (질의마다 첫 호출 1회는 따로)
- 환경: Windows 11, Python 3.12.10, SQLite 3.49.1
- 임베딩: `data/search/semantic-sample` - BAAI/bge-small-en-v1.5 384차원 6,719건 (0.01 GB)
- 서버 시작 때 모델·벡터 읽기(첫 요청): **0.8초** - p50·p95 에 넣지 않았다

| 유형 | n | p50 | p95 | 최대 | ≤ 1초 |
|---|---:|---:|---:|---:|:---:|
| semantic | 25 | 14ms | 18ms | 18ms | O |
| semantic-code | 15 | 16ms | 18ms | 18ms | O |
| semantic+filter | 20 | 16ms | 20ms | 20ms | O |
| all | 60 | 16ms | 18ms | 20ms | O |
| first_call | 12 | 17ms | 23ms | 24ms | O |

## 질의별

| 유형 | 파라미터 | 건수 | 받은 행 | 첫 호출 | 중앙값 | 최대 |
|---|---|---:|---:|---:|---:|---:|
| semantic | `q=wait longer between retries after a failed request, mode=semantic` | 6,719 | 20 | 17ms | 16ms | 17ms |
| semantic | `q=cache downloaded files on disk and reuse them, mode=semantic` | 6,719 | 20 | 17ms | 14ms | 18ms |
| semantic | `q=parse command line arguments, mode=semantic` | 6,719 | 20 | 14ms | 16ms | 18ms |
| semantic | `q=compatibility shim for python 2, mode=semantic` | 6,719 | 20 | 16ms | 16ms | 16ms |
| semantic | `q=close database connection when done, mode=semantic` | 6,719 | 20 | 16ms | 14ms | 14ms |
| semantic-code | `q=def load_config(path):⏎    with open(path) as f:⏎        return yaml.safe_load(f), mode=semantic` | 6,719 | 20 | 24ms | 17ms | 18ms |
| semantic-code | `q=for i in range(len(xs)):⏎    total += xs[i], mode=semantic` | 6,719 | 20 | 21ms | 15ms | 16ms |
| semantic-code | `q=async def fetch(session, url):⏎    async with session.get(url) as r:, mode=semantic` | 6,719 | 20 | 17ms | 16ms | 18ms |
| semantic+filter | `q=remove deprecated api, source=HUMAN, mode=semantic` | 50 | 20 | 17ms | 16ms | 19ms |
| semantic+filter | `q=thread safety lock, repo=psf/requests, mode=semantic` | 2,085 | 20 | 15ms | 17ms | 20ms |
| semantic+filter | `q=timeout handling, year_from=2022, grade=INFERRED, mode=semantic` | 2,072 | 20 | 16ms | 15ms | 20ms |
| semantic+filter | `q=remove unused helper, reason=DEAD, offset=20, mode=semantic` | 268 | 20 | 19ms | 16ms | 17ms |

## 해석

표본 임베딩(저장소 3개, 6,719건)으로 잰 값이다. 전체 723,087건에서는 내적이 108배 커진다 - 합성 벡터 723,087 × 384 float32 메모리 내적+상위 K 는 약 0.04초, float16 메모리맵 청크 변환은 약 0.45초였다. 필터 질의 4개는 표본·전체 모두 결과가 나오는 값이다(받은 행 칸). 실데이터 측정은 전체 임베딩 후 갱신.
