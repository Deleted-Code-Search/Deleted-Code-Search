# CLAUDE.md — Claude Code 지침

이 파일은 Claude Code가 세션마다 자동으로 읽는다. 팀 합의 사항은 `CHARTER.md` §8.6에 있고, 이 파일은 그 요약이다. 충돌하면 CHARTER.md가 우선한다.

## 세션 시작 시 반드시
1. **`CHARTER.md`를 먼저 읽는다.** 범위(§3)·용어(§15)·스키마(§4.4)·분류 체계(§4.2 ③)는 CHARTER.md가 기준이다. 다른 이름을 만들지 않는다.
2. **`STATUS.md`를 읽고** 현재 어디까지 됐는지 파악한 뒤 작업을 시작한다.
3. 사용자가 "이슈 #N 작업"이라고 말하면 그 이슈만 다룬다. 이슈 하나 = 브랜치 하나 = 세션 하나. 다른 이슈를 섞자는 요청에는 새 세션을 권한다.
4. 작업 시작 전 `git pull --rebase`로 최신 main을 반영한다.

## 실행·테스트·린트
```bash
# DB (PostgreSQL 16 + pgvector)
cp .env.example .env        # 최초 1회, 값 채우기
docker compose up -d
docker compose ps           # deleted-code-search-db 가 healthy 이면 정상

# Python 3.12
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 테스트
pytest

# 린트·포맷 (CI에서 강제)
ruff check .
ruff format --check .       # 자동 수정: ruff format .
```

## 절대 하지 말 것
- `main`에 직접 푸시. 모든 변경은 이슈별 브랜치 → PR → 최소 1명 리뷰 (§8.2)
- 스키마(`DeletionRecord`, §4.4)·이유 분류 체계(8종 + 근거 3등급, §4.2 ③)·파서 인터페이스(`pipeline/parsers/base.py`)·API 스펙을 임의로 변경. 필요하면 **"변경 제안"**으로 표시하고 §13 절차(이슈 → 회의 → ADR → CHARTER 갱신)를 안내한다
- 테스트 없는 필터 규칙 변경. 필터·분류 규칙을 바꾸는 PR에는 테스트와 정밀도 재측정 결과가 함께 있어야 한다 (§8.4)
- LLM(자기 자신 포함)의 출력을 라벨 정답으로 저장하는 코드. LLM은 후보 제안과 근거 문장 작성까지만 (ADR-005). 라벨링은 사람이 한다
- 비밀(GitHub 토큰, LLM API 키, DB 비밀번호)을 코드나 커밋에 넣기. `.env`에만, `.env.example`만 커밋 (§8.4)
- 핵심 4개(파이프라인·필터·분류·데이터셋) 완료 전에 확장 항목(검색 UI·진단 API·개인 레포 모델·플러그인) 구현을 제안하기 (ADR-008)

## 담당 영역 경계 (§6.1)
| 영역 | 담당 | 경로 |
|---|---|---|
| 파이프라인·파서·필터·DB 스키마 | 재헌 (jh) | `pipeline/` (context.py 제외) |
| 맥락 결합·대체 코드·분류·검색 | 희수 (hs) | `pipeline/context.py`, `classify/`, `search/` |
| 저장소 선정·대시보드·웹·평가·문서 | 성제 (sj) | `web/`, `docs/`, `datasets/export/`, 평가 스크립트 |

다른 팀원 영역의 파일을 수정할 땐 **인터페이스만** 건드리고, PR 본문에 그 사실을 명시한다. 인터페이스 파일: `pipeline/parsers/base.py`, `DeletionRecord` 스키마, API 스펙.

## 커밋·PR 규칙
- 브랜치: `{type}/{이슈번호}-{짧은-설명}-{이니셜}` — 예: `feat/12-python-adapter-jh`. type: feat / fix / refactor / exp / docs / chore
- 커밋 메시지 (§8.3):
  ```
  type(scope): 요약 (50자 이내)

  본문: 왜 이 변경인가 (무엇이 아니라 왜)
  Refs #이슈번호
  ```
- **코드를 삭제하는 커밋은 본문에 삭제 이유를 반드시 적는다.** 우리 도구가 우리 레포를 분석할 때 EXPLICIT이 나와야 한다 (도그푸딩)
- 대량 파일 변경(10개 이상)은 PR을 나눈다
- 실험 코드는 `exp/` 브랜치 또는 `notebooks/`에. main에는 재현 가능한 스크립트만

## 세션 종료 전 반드시
1. `pytest` + `ruff check .` 실행
2. 커밋 (사람이 코드를 읽고 이해한 뒤. 이해 못 한 코드는 커밋하지 않는다)
3. PR 생성 또는 초안 PR
4. `STATUS.md`·이슈 보드 갱신이 필요한지 확인

## 판단 기준
- 모르는 결정은 CHARTER.md §17 미결정 목록을 확인하고, 없으면 팀 확인을 권한다
- 솔직하게 평가한다. 약한 부분을 돌려 말하지 않는다
- 시도했다가 버린 접근은 기록한다 (`docs/weekly/` 또는 ADR). 이 프로젝트의 주제가 그것이다
