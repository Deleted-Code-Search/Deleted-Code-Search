# Deleted Code Search

오픈소스에서 **삭제된 코드**만 모아, "왜 지워졌는지"와 "대신 무엇이 들어갔는지"를 함께 검색하는 엔진.
단국대 소프트웨어학과 캡스톤디자인(실무중심산학협력프로젝트2), 2026-09 ~ 2026-11.

모든 배경·목표·설계·일정·규칙은 **[CHARTER.md](CHARTER.md)** 한 곳에 있다. 현재 진행 상태는 [STATUS.md](STATUS.md).

## 구조 (CHARTER.md §8.1)
| 경로 | 내용 | 담당 |
|---|---|---|
| `pipeline/` | 클론 → 커밋 순회 → 삭제 헝크 추출 → 필터 → 맥락 결합. `parsers/`는 언어 어댑터 | 재헌 (context.py는 희수) |
| `classify/` | 이유 8종 분류기, 기준선 A/B | 희수 |
| `search/` | 임베딩·pgvector 인덱스·API (확장) | 희수 |
| `web/` | React 대시보드·검색 UI (확장) | 성제 |
| `datasets/` | 수동 라벨 500건, 공개 데이터셋 빌드 | 성제 |
| `docs/` | ADR, 회의록, 주간 로그, 라벨 가이드, 필터 규칙, 평가, 제출 문서 | 성제 |
| `tests/` | pytest | 전원 |

## 시작하기
```bash
git clone https://github.com/honghonghonggit/Deleted-Code-Search.git
cd Deleted-Code-Search
cp .env.example .env            # GITHUB_TOKEN 등 채우기. .env 는 커밋 금지
docker compose up -d            # PostgreSQL 16 + pgvector
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest && ruff check .
```

## 개발 규칙 (요약, 자세한 건 CHARTER.md §8)
- `main` 직접 푸시 금지. 이슈 하나 = 브랜치 하나 = PR 하나, 리뷰 1명 필수
- 브랜치 이름 `{type}/{이슈번호}-{설명}-{이니셜}`, 커밋 메시지는 "왜"를 쓰고 `Refs #이슈`
- 코드를 삭제하는 커밋은 삭제 이유를 반드시 적는다 (우리 도구로 우리 레포를 분석한다)
- Claude Code 사용 시 `CLAUDE.md`가 자동으로 읽힌다

## 라이선스
코드 MIT ([LICENSE](LICENSE)). 데이터셋은 CC BY 4.0, 포함 스니펫은 원 저장소 라이선스 유지 (ADR-011).
