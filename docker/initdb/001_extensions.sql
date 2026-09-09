-- 최초 DB 생성 시 1회 실행 (docker-entrypoint-initdb.d).
-- 테이블 스키마(DeletionRecord, CHARTER.md §4.4)는 담당 이슈에서 마이그레이션으로 추가한다. 담당: 재헌.
CREATE EXTENSION IF NOT EXISTS vector;
