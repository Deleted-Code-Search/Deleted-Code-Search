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

## 라벨링
`python -m tools.label_cli --labeler sj` (본인 이니셜 `sj`/`jh`/`hs`)로 시작한다. #33이 만든 `datasets/labels/pre200_records.jsonl`의 레코드를 한 건씩 맥락 → 삭제 코드 → 대체 코드 순서로 보여 주고(분류기 출력 `reason.*`는 보여 주지 않는다), 이유 8종·근거 등급·근거·신뢰도·note를 물어 본인 파일 `datasets/labels/sj_pre200.jsonl`의 빈 줄을 [라벨 가이드](docs/labeling_guide.md) §7.2 형식으로 채운다. 잘못된 값은 입력 단계에서 거절한다. 건마다 저장하므로 아무 때나 `:q`로 끄고 다시 실행하면 남은 건부터 이어지고, `:u`는 직전 건 수정, `:r`은 지금 건을 처음부터 다시 입력한다. 라벨이 모이면 `python -m eval.gate1 datasets/labels/*_pre200.jsonl`로 게이트 1 숫자를 낸다 ([docs/evaluation.md](docs/evaluation.md) "게이트 1 측정 방법").

## 개발 규칙 (요약, 자세한 건 CHARTER.md §8)
- `main` 직접 푸시 금지. 이슈 하나 = 브랜치 하나 = PR 하나, 리뷰 1명 필수
- 브랜치 이름 `{type}/{이슈번호}-{설명}-{이니셜}`, 커밋 메시지는 "왜"를 쓰고 `Refs #이슈`
- 코드를 삭제하는 커밋은 삭제 이유를 반드시 적는다 (우리 도구로 우리 레포를 분석한다)
- Claude Code 사용 시 `CLAUDE.md`가 자동으로 읽힌다

## 라이선스
코드 MIT ([LICENSE](LICENSE)). 데이터셋은 CC BY 4.0, 포함 스니펫은 원 저장소 라이선스 유지 (ADR-011).
