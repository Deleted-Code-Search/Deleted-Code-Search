# Deleted Code Search — 지워진 코드 검색엔진

**성공은 검색되고, 실패는 삭제된다. 우리는 삭제된 코드를 검색한다.**
*Search the code open source deleted — and why it was deleted.*

[![License: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)
[![Dataset: CC BY 4.0](https://img.shields.io/badge/dataset-CC%20BY%204.0-lightgrey)](datasets/dataset-v0.9/README.md)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue)](pyproject.toml)

## 📎 제출 자료

캡스톤 페스티벌 2026 · 팀 **깻잎**

| 자료 | 링크 |
|---|---|
| 결과보고서 PDF | [docs/final/report.pdf](docs/final/report.pdf) ([GitHub에서 읽기](docs/final/report.md)) |
| 포스터 PDF | (11/15 추가 예정) |
| 시연 영상 | (11/15 추가 예정) |
| 데이터셋 | [Release `dataset-v0.9`](https://github.com/Deleted-Code-Search/Deleted-Code-Search/releases/tag/dataset-v0.9) |
| 실행 방법 | [`run.ps1`](run.ps1) ([바로 써보기](#바로-써보기)) |

## 🧭 심사위원용 안내

| 심사 항목 | 배점 | 보면 되는 곳 |
|---|---:|---|
| 창의성·문제해결력 | 25 | [문제 정의](docs/final/report.md#1-배경과-문제-정의), [차별점](docs/final/report.md#32-경쟁과-차별점), [버린 시도](docs/final/report.md#83-버린-시도) |
| 시장성·수익모델 | 20 | [보고서 3장](docs/final/report.md#3-시장성과-수익모델), [1~2쪽 요약](docs/final/business.md) |
| 실현 가능성 (사업화 계획) | 20 | [보고서 4장](docs/final/report.md#4-사업화-계획과-실현-가능성) |
| 기술성·완성도 | 25 | [아키텍처](docs/final/report.md#5-시스템-설계), [평가 결과](docs/final/report.md#7-평가), 코드 [`pipeline/`](pipeline/) · [`classify/`](classify/) · [`search/`](search/) · [`web/`](web/) · [`tests/`](tests/) |
| 팀 구성·역할 | 5 | [역할](docs/final/report.md#10-팀-구성과-역할), [협업 기록](docs/final/report.md#84-협업), [이슈](https://github.com/Deleted-Code-Search/Deleted-Code-Search/issues?q=is%3Aissue) · [PR](https://github.com/Deleted-Code-Search/Deleted-Code-Search/pulls?q=is%3Apr) |
| 발표역량 | 5 | 현장 발표. 데모는 [`run.ps1`](run.ps1) |

## 소개

코드 검색과 AI 코딩 도구는 살아남은 코드만 봅니다. "이렇게 해 봤는데 이래서 버렸다"는 지식은 커밋 히스토리에 묻혀 있습니다.
이 프로젝트는 오픈소스 Python 저장소 100개에서 **지워진 함수**를 모아, **왜 지워졌는지**와 **대신 무엇이 들어왔는지**를 함께 찾아 줍니다.
결과물은 공개 데이터셋, 통계 대시보드, 키워드·의미 검색입니다. 단국대 소프트웨어학과 캡스톤디자인(2026-09 ~ 2026-11) 프로젝트입니다.

| 대시보드 | 의미 검색 |
|---|---|
| ![통계 대시보드: 헤드라인 숫자와 라벨 출처, 모델·필터 성능](docs/screenshots/dashboard_93_top.jpg) | ![의미 검색: 자연어로 찾은 지워진 함수 목록](docs/screenshots/semantic_results.jpg) |

## 숫자로 보기

| 항목 | 결과 | 출처 |
|---|---:|---|
| 저장소 | **100개** | [데이터셋 문서](datasets/dataset-v0.9/README.md) |
| 삭제 함수 → 의미 있는 삭제 | 5,383,893 (538만) → **1,354,052 (135만)** | [대시보드 집계](docs/reports/dashboard_stats.json) |
| 이유 분류 8종 | **73.0%** (4묶음 85.0%, 게이트 2 60.0%에서 개선) | [분류기 개선](docs/reports/classifier_improvement.md) |
| 의미 검색 P@10 | **0.625** (키워드 0.325) | [검색 관련성](docs/reports/search_relevance.md) |
| 의미 검색 응답 시간 | p50 **46ms** · p95 50ms | [응답 시간](docs/reports/search_latency_semantic.md) |

숫자를 읽을 때 알아 둘 점입니다.

- **73.0%·85.0%는 게이트 결과가 아닙니다.** 사전 등록한 1회 측정은 [60.0%로 게이트 2 미달](docs/meetings/gate-2.md)이고, 이 판정은 그대로입니다. 개선 숫자는 같은 test 100건을 보며 설정을 고른 값이라 새 데이터에서는 더 낮을 수 있습니다. 데이터셋 라벨에는 개선 분류기를 적용하지 않았습니다.
- **필터 정밀도는 76.0% (v0.8 기준, v0.9 는 새 표본 재측정 전)로 목표 90%에 못 미칩니다.** 135만 건 안에 의미 없는 삭제도 섞여 있습니다.
- P@10은 질의 10개, 판정자 2명의 평균입니다.

## 어떻게 동작하나

```mermaid
flowchart LR
    A[채굴] --> B[필터]
    B --> C[맥락 결합]
    C --> D[이유 분류]
    D --> E[데이터셋]
    D --> F[검색]
    D --> G[대시보드]
```

1. **채굴** — 저장소의 커밋을 순회해 diff에서 삭제된 코드를 함수 단위로 뽑습니다.
2. **필터** — 이동·리네임·포맷 변경·생성 코드처럼 배울 게 없는 삭제를 걸러냅니다.
3. **맥락 결합** — 함수 전체 삭제에 커밋 메시지·PR·이슈·리뷰와 대체 코드를 붙입니다.
4. **이유 분류** — 왜 지웠는지를 8종(버그·성능·보안·라이브러리 교체·미사용·설계·기능 제거·모름)과 근거 등급으로 나눕니다.
5. **데이터셋·검색·대시보드** — 공개 데이터셋으로 내보내고, 키워드(SQLite FTS5)와 의미(`bge-small-en-v1.5`) 검색, 통계 화면으로 보여 줍니다.

## 기존 도구와 다른 점

- **Sourcegraph diff 검색**은 지워진 문자열을 찾습니다. 우리는 함수 단위로 이유·대체 코드까지 묶고 뜻으로도 찾습니다.
- **fossil-code·deadcode-archaeologist**는 내 저장소를 청소하는 CLI입니다. 우리는 남의 저장소 100개에서 배우는 검색엔진입니다.
- **MSR 연구**는 논문이고 **Software Heritage**는 아카이브입니다. "삭제 함수 + 이유 + 대체 코드"를 묶은 데이터셋과 검색은 선행 조사에서 찾지 못했습니다 (2026-09-07, [CHARTER §2.3](CHARTER.md#23-기존-것들과의-차이-선행-조사-결과-2026-09-07)).

## 바로 써보기

**Windows는 [`run.ps1`](run.ps1) 하나면 됩니다.** Python 3.12와 Node.js 20 이상이 필요합니다. 저장소 폴더에서 실행하세요.

```powershell
git clone https://github.com/Deleted-Code-Search/Deleted-Code-Search.git
cd Deleted-Code-Search
.\run.ps1
```

실행 정책 때문에 막히면 아래처럼 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

| `run.ps1`이 하는 일 | 내용 |
|---|---|
| 파이썬 환경 | `.venv`가 없으면 만들고 `pip install -e ".[web,embed]"` |
| 키워드 검색 색인 | 없으면 Release jsonl.gz를 받고(`gh` 로그인 상태일 때 자동, 약 700MB) `python -m search.index` (약 5분 · 약 7GB) |
| 의미 검색 임베딩 | 없으면 안내만 합니다. 키워드 검색은 동작하고, 의미 검색은 `python -m search.embed`(약 5시간) 뒤에 켜집니다 |
| API·웹 | 새 창 두 개에 API(`http://127.0.0.1:8000`)와 웹(`http://localhost:5173`)을 띄우고 브라우저를 엽니다 |

데이터가 없어도 대시보드는 바로 뜹니다. 끝낼 때는 새로 열린 창 두 개를 닫습니다.

<details>
<summary>직접 실행하기 (run.ps1 없이)</summary>

**데이터셋 받기** — 데이터셋은 [Release `dataset-v0.9`](https://github.com/Deleted-Code-Search/Deleted-Code-Search/releases/tag/dataset-v0.9)에 있습니다. 사람이 확정한 라벨 500건은 [`datasets/dataset-v0.9/main500.jsonl`](datasets/dataset-v0.9/main500.jsonl)에도 있습니다. 필드와 주의점은 [데이터셋 문서](datasets/dataset-v0.9/README.md)를 보세요.

```powershell
# jsonl.gz 전체, 약 700MB
gh release download dataset-v0.9 -p "*.jsonl.gz" -D data/export/dataset-v0.9
```

**대시보드·검색 실행 (Windows PowerShell)** — 대시보드 숫자는 저장소에 들어 있어 데이터 없이 바로 뜹니다. 아래 명령은 저장소 폴더에서 실행합니다.

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[web]"

# 키워드 검색 색인 (위에서 받은 데이터셋 필요, 약 5분 · 약 7GB)
python -m search.index

# API (http://127.0.0.1:8000). 스마트 앱 컨트롤이 막으면 Docker Desktop에서 docker compose up api
uvicorn search.api:app --host 127.0.0.1 --port 8000

# 새 PowerShell 창을 열고 저장소 폴더에서 화면 실행 (http://localhost:5173)
cd web; npm install; npm run dev
```

의미 검색은 임베딩을 한 번 만들어야 합니다 (`pip install -e ".[embed]"` 뒤 `python -m search.embed`, 약 5.4시간). 자세한 내용은 [web/README.md](web/README.md)에 있습니다.

</details>

## 문서 지도

| 분류 | 문서 | 내용 |
|---|---|---|
| 보고서 | [결과보고서](docs/final/report.md) ([PDF](docs/final/report.pdf)) | 문제·시장성·사업화·설계·구현·평가·과정·팀을 한곳에 |
| 보고서 | [business.md](docs/final/business.md) | 시장성·수익모델·사업화 계획 요약 |
| 설계 | [CHARTER.md](CHARTER.md) | 배경·목표·설계·일정·규칙 전체, ADR-001~011 요약 |
| 설계 | [docs/adr/](docs/adr/README.md) | 설계 결정 기록 (ADR-012~), 버린 대안까지 |
| 설계 | [docs/filter_rules.md](docs/filter_rules.md) | 필터 규칙 `v0.9` |
| 설계 | [docs/labeling_guide.md](docs/labeling_guide.md) | 라벨링 가이드 |
| 설계 | [데이터셋 문서](datasets/dataset-v0.9/README.md) | 필드, 라이선스, 알려진 한계 |
| 평가 | [docs/evaluation.md](docs/evaluation.md) | 평가 계획·사전 등록·측정 기록 |
| 평가 | [docs/reports/classifier_improvement.md](docs/reports/classifier_improvement.md) | 게이트 2 이후 분류기 개선 |
| 평가 | [docs/reports/search_relevance.md](docs/reports/search_relevance.md) | 검색 관련성 (P@10) |
| 평가 | [docs/reports/filter_precision_v2.md](docs/reports/filter_precision_v2.md) | 필터 정밀도 2차 |
| 평가 | [docs/reports/labels_main500.md](docs/reports/labels_main500.md) | 확정 라벨 500건과 이유 회수율 |
| 과정 기록 | [STATUS.md](STATUS.md) | 최종 상태, 측정된 숫자, 향후 |
| 과정 기록 | [docs/meetings/gate-2.md](docs/meetings/gate-2.md) | 게이트 2 판정 (60.0%, 미달). [게이트 1](docs/meetings/gate-1.md)도 있습니다 |
| 과정 기록 | [docs/weekly/](docs/weekly/W1.md) | 주간 로그, 시도했다가 버린 것 |
| 코드 | [`pipeline/`](pipeline/) | 채굴·파서·필터·맥락 결합·조립·내보내기 |
| 코드 | [`classify/`](classify/) | 이유 분류기, 기준선 A/B |
| 코드 | [`search/`](search/) · [`web/`](web/) | 색인·임베딩·API, 대시보드·검색 화면 |
| 코드 | [`eval/`](eval/) · [`tools/`](tools/) · [`tests/`](tests/) | 평가·게이트 판정, 라벨링 CLI·보고서 변환, pytest |

<details>
<summary>폴더 구조와 개발 규칙</summary>

| 경로 | 내용 | 담당 |
|---|---|---|
| `pipeline/` | 채굴·필터·조립·내보내기 (`context.py`는 맥락 결합) | 재헌 (`context.py`는 희수) |
| `classify/` | 이유 분류기, 기준선 A/B | 희수 |
| `search/` | 색인·임베딩·검색·통계 API | 희수, 성제 |
| `eval/` | 평가·게이트 판정 스크립트 | 성제 |
| `tools/` | 라벨링 CLI 등 보조 도구 | 성제 |
| `web/` | React 대시보드·검색 화면 | 성제 |
| `datasets/` | 확정 라벨 500건, 공개 데이터셋 문서 | 성제 |
| `docs/` | ADR·회의록·주간 로그·보고서 | 성제 |
| `tests/` | pytest | 전원 |

라벨링 화면은 `python -m tools.label_cli --labeler sj`(본인 이니셜 `sj`/`jh`/`hs`)로 엽니다. 테스트와 린트는 `pip install -e ".[dev,web]"` 뒤 `pytest`, `ruff check .`, `ruff format --check .`입니다. 브랜치·커밋·PR 규칙은 [CHARTER §8](CHARTER.md#8-저장소-구조와-개발-규칙)에 있고, Claude Code는 [CLAUDE.md](CLAUDE.md)를 자동으로 읽습니다.

</details>

## 팀

| 이름 | 역할 |
|---|---|
| 성제 (조장) | 프로덕트·평가 — 저장소 선정, 라벨링 총괄, 대시보드·웹, 게이트 판정 |
| 재헌 | 파이프라인 — 채굴, 파서, 필터, 데이터셋 내보내기 |
| 희수 | 분류·검색 — 맥락 결합, 대체 코드, 이유 분류기, 기준선 |

## 라이선스

코드는 [MIT](LICENSE), 데이터셋은 CC BY 4.0입니다. 데이터셋에 포함된 코드 스니펫은 원 저장소의 라이선스를 따르고, 행마다 출처 링크가 있습니다 (ADR-011).
