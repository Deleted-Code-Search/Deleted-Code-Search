import { GRADE_LABEL, REASON_LABEL, SOURCE_BADGE, SOURCE_NOTE } from "../format";

interface Props {
  source: string;
  label: string | null;
  grade: string | null;
}

// 이유 표시 규칙 (#191, 대시보드와 같은 원칙): 출처 배지는 항상. 이유가 없으면 출처가 이유 자리를 대신한다.
export function ReasonBadges({ source, label, grade }: Props) {
  return (
    <span className="badges">
      {label ? (
        <span className={`badge reason${source === "HUMAN" ? " human" : ""}`}>
          {REASON_LABEL[label] ?? label}
        </span>
      ) : null}
      {label && grade ? (
        <span className="badge grade" title={GRADE_LABEL[grade]}>
          {grade}
        </span>
      ) : null}
      <span className={`badge source source-${source}`} title={SOURCE_NOTE[source]}>
        {SOURCE_BADGE[source] ?? source}
      </span>
    </span>
  );
}
