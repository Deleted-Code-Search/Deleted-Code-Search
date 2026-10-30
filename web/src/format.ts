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
