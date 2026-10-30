import type { DashboardStats } from "../types";
import { num, pct, share } from "../format";
import { Panel } from "./Panel";

export function Headline({ stats }: { stats: DashboardStats }) {
  const h = stats.headline;
  const tiles = [
    { label: "저장소", value: num(h.repos), sub: `데이터셋 ${stats.dataset_version}` },
    {
      label: "필터 전 삭제 함수",
      value: num(h.deleted_before_filter),
      sub: `필터 제외 ${num(h.noise_rows)}`,
    },
    {
      label: "필터 후 KEPT",
      value: num(h.kept),
      sub: `필터 전의 ${pct(share(h.kept, h.deleted_before_filter))} · 규칙 ${stats.filter_rule_version}`,
    },
    {
      label: "FULL_FUNCTION / PARTIAL",
      value: `${num(h.full_function)} / ${num(h.partial)}`,
      sub: "함수 전체 삭제 / 일부 삭제",
    },
    {
      label: "맥락 결합",
      value: num(h.with_context),
      sub: `KEPT 의 ${pct(share(h.with_context, h.kept))} (FULL_FUNCTION 만 결합)`,
    },
  ];
  return (
    <Panel title="헤드라인 숫자" source={h.source} n={h.kept} wide>
      <div className="tiles">
        {tiles.map((tile) => (
          <div className="tile" key={tile.label}>
            <div className="tile-label">{tile.label}</div>
            <div className="tile-value">{tile.value}</div>
            <div className="tile-sub">{tile.sub}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
