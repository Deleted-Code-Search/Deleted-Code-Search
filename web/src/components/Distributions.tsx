import type { DashboardStats } from "../types";
import { GRADE_LABEL, REASON_LABEL, pct } from "../format";
import { Panel } from "./Panel";
import { PairedBars } from "./PairedBars";

export function ReasonDistribution({ stats }: { stats: DashboardStats }) {
  const r = stats.reason_distribution;
  return (
    <Panel
      title="이유 분포"
      source={r.source}
      n={`사람 확정 ${r.HUMAN.n} / LLM 포함 표본 ${r.MODEL_LLM.n.toLocaleString("ko-KR")}`}
      note={`${r.note}. 제외한 출처: ${r.excluded_sources.join(", ")}.`}
    >
      <PairedBars keys={r.labels} labels={REASON_LABEL} human={r.HUMAN} llm={r.MODEL_LLM} />
    </Panel>
  );
}

export function GradeDistribution({ stats }: { stats: DashboardStats }) {
  const g = stats.grade_distribution;
  return (
    <Panel
      title="근거 등급 분포"
      source={g.source}
      n={`사람 확정 ${g.HUMAN.n} / LLM 포함 표본 ${g.MODEL_LLM.n.toLocaleString("ko-KR")}`}
      note={
        <>
          {g.note}: <strong>{pct(g.explicit_rate_human)}</strong>.
          LLM 포함 표본의 등급은 분류기가 매긴 것이다 (docs/reports/apply100.md §2).
        </>
      }
    >
      <PairedBars keys={g.grades} labels={GRADE_LABEL} human={g.HUMAN} llm={g.MODEL_LLM} />
    </Panel>
  );
}
