export const num = (value: number): string => value.toLocaleString("ko-KR");

export const pct = (value: number | null | undefined, digits = 1): string =>
  value == null ? "-" : `${(value * 100).toFixed(digits)}%`;

export const share = (count: number, total: number): number => (total ? count / total : 0);

export function duration(seconds: number | null): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(0)}초`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(0)}분`;
  return `${(minutes / 60).toFixed(1)}시간`;
}

// 출처 라벨 (CHARTER §4.4 classification.source)
export const SOURCE_LABEL: Record<string, string> = {
  HUMAN: "사람 확정",
  MODEL_LLM: "LLM 포함 표본",
  MODEL_ONLY: "모델만",
  UNDISTINGUISHED: "이유 미구분",
  NOT_CLASSIFIED: "분류 대상 아님 (PARTIAL)",
};

// 이유 분류 8종 (CHARTER §4.2 ③)
export const REASON_LABEL: Record<string, string> = {
  BUG: "BUG 버그 수정",
  PERF: "PERF 성능",
  SEC: "SEC 보안",
  LIB: "LIB 라이브러리 교체",
  DEAD: "DEAD 죽은 코드",
  DESIGN: "DESIGN 설계 변경",
  FEAT: "FEAT 기능 제거",
  UNK: "UNK 불명",
};

export const GRADE_LABEL: Record<string, string> = {
  EXPLICIT: "EXPLICIT 문장으로 있음",
  INFERRED: "INFERRED 추론",
  UNKNOWN: "UNKNOWN 판단 불가",
};

// 검색 결과의 출처 배지 (#191). 대시보드와 같은 원칙 - 출처를 항상 단다.
export const SOURCE_BADGE: Record<string, string> = {
  HUMAN: "사람 확정",
  MODEL_LLM: "LLM 표본",
  MODEL_ONLY: "모델만 (val 52%)",
  UNDISTINGUISHED: "이유 미구분",
  NOT_CLASSIFIED: "분류 대상 아님(PARTIAL)",
};

export const SOURCE_NOTE: Record<string, string> = {
  HUMAN: "확정 라벨 500건 - 사람 2인 라벨 + 제3자 판정",
  MODEL_LLM: "LLM 포함 분류기 표본 - 사람이 확인하지 않은 모델 출력",
  MODEL_ONLY: "모델만 분류기(m2) 출력. 검증 정확도 52% - 이유 분포로 쓰지 않는다",
  UNDISTINGUISHED: "신규 80개 저장소 - 이유를 구분하지 않았다 (reason = null)",
  NOT_CLASSIFIED: "PARTIAL 은 이유 분류 대상이 아니다",
};

export const KIND_LABEL: Record<string, string> = {
  FULL_FUNCTION: "FULL 함수 전체",
  PARTIAL: "PARTIAL 일부",
};

// snippet 강조 구분자 (search/api.py MARK_OPEN·MARK_CLOSE)
export const MARK_OPEN = "\u0002";
export const MARK_CLOSE = "\u0003";
