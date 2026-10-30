# 검색 응답 시간 (#191)

CHARTER §10.5 목표 **≤ 1000ms**. `python -m eval.search_latency` 출력이다.

- 색인: `data/search/dataset-v0.9.sqlite` (7.00 GB)
- 호출: HTTP http://127.0.0.1:8000 · 질의 20개 × 5회 (질의마다 첫 호출 1회는 따로)
- 환경: Windows 11, Python 3.12.10, SQLite 3.49.1

| 유형 | n | p50 | p95 | 최대 | ≤ 1초 |
|---|---:|---:|---:|---:|:---:|
| keyword | 45 | 69ms | 644ms | 656ms | O |
| keyword+filter | 30 | 19ms | 78ms | 84ms | O |
| filter | 25 | 15ms | 27ms | 31ms | O |
| all | 100 | 28ms | 388ms | 656ms | O |
| first_call | 20 | 31ms | 448ms | 699ms | O |

## 질의별

| 유형 | 파라미터 | 건수 | 첫 호출 | 중앙값 | 최대 |
|---|---|---:|---:|---:|---:|
| keyword | `q=retry backoff` | 2,120 | 34ms | 20ms | 38ms |
| keyword | `q=py2` | 2,262 | 9ms | 26ms | 31ms |
| keyword | `q=deprecated` | 10,000+ | 101ms | 79ms | 83ms |
| keyword | `q=memory leak` | 2,479 | 36ms | 37ms | 40ms |
| keyword | `q=thread safe` | 4,460 | 39ms | 30ms | 38ms |
| keyword | `q=cache` | 10,000+ | 76ms | 73ms | 82ms |
| keyword | `q=self` | 10,000+ | 435ms | 366ms | 375ms |
| keyword | `q=test` | 10,000+ | 275ms | 273ms | 282ms |
| keyword | `q=get_config*` | 6,979 | 699ms | 644ms | 656ms |
| keyword+filter | `q=unused, source=HUMAN` | 33 | 7ms | 10ms | 24ms |
| keyword+filter | `q=replace, source=HUMAN, grade=EXPLICIT` | 13 | 20ms | 18ms | 37ms |
| keyword+filter | `q=deprecated, grade=EXPLICIT` | 75 | 15ms | 16ms | 19ms |
| keyword+filter | `q=migration, repo=django/django` | 426 | 26ms | 17ms | 38ms |
| keyword+filter | `q=timeout, kind=FULL_FUNCTION, year_from=2022` | 10,000+ | 84ms | 73ms | 84ms |
| keyword+filter | `q=self, repo=home-assistant/core, year_to=2018` | 8,931 | 42ms | 54ms | 65ms |
| filter | `source=HUMAN` | 500 | 29ms | 20ms | 29ms |
| filter | `reason=SEC` | 9 | 18ms | 11ms | 19ms |
| filter | `repo=apache/airflow, kind=PARTIAL` | 10,000+ | 5ms | 8ms | 17ms |
| filter | `year_from=2025, grade=EXPLICIT` | 502 | 19ms | 16ms | 31ms |
| filter | `source=UNDISTINGUISHED, year_to=2015, offset=200` | 4,425 | 16ms | 15ms | 18ms |

## 해석

- **1초를 넘는 유형은 없다.** 가장 느린 것은 접두어 검색 `get_config*`(약 0.6초 - 접두어가 펼쳐지는 토큰이 많다)와 흔한 단어 `self`·`test`(0.25~0.35초 - 맞은 수십만 건을 bm25 로 정렬한다)다. 필터만·키워드+필터는 0.1초 아래다.
- 측정 조건: uvicorn 1프로세스, 색인을 만든 직후라 OS 파일 캐시가 데워진 상태다. **재부팅 직후(콜드 캐시)는 재지 않았다** - 7GB 파일의 첫 질의는 디스크 읽기만큼 더 걸릴 수 있다.
- `http://localhost:8000` 으로 파이썬(urllib)에서 부르면 **요청마다 약 2초가 더 붙는다** (첫 측정 p50 2,064ms). Windows 가 `localhost` 를 IPv6 `::1` 부터 시도하고 uvicorn 은 127.0.0.1 에만 떠 있기 때문이다. 그래서 측정은 `127.0.0.1` 로 했다. 브라우저(Chromium)는 첫 연결에만 약 0.3초가 붙고 그 뒤에는 연결을 재사용한다 (확인: 첫 요청 317ms, 다음 요청 11ms).
- 같은 프로세스 TestClient 측정(네트워크 없음)은 p50 9ms · p95 334ms 였다.
- 고치기 전 숫자 (같은 색인 크기): snippet 을 정렬 전에 모든 행에 만들면 `self` 1.3초·`test` 1.1초, 키워드+필터를 records JOIN 으로 풀면 `self`+저장소 1.2초, 필터만 여러 개를 records 칼럼 색인으로 풀면 0.65초였다. 고친 방법은 ADR-022.
