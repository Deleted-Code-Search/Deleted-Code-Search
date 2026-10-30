import type { DashboardStats } from "../types";
import { pct } from "../format";
import { Panel } from "./Panel";

export function Performance({ stats }: { stats: DashboardStats }) {
  const { cards, source } = stats.performance;
  const training = stats.repos.rows.filter((r) => r.training_repo).length;
  const others = stats.repos.rows.length - training;
  return (
    <Panel
      title="모델·필터 성능"
      source={source}
      note={`분류기 숫자는 모두 학습과 같은 ${training}개 저장소 안에서 쟀다. 나머지 ${others}개 저장소로 옮겨 쓸 수 없다 (docs/reports/apply100.md §4). 모델만 라벨에 붙일 수 있는 숫자는 "모델만 val" 카드뿐이다.`}
    >
      <div className="cards">
        {cards.map((card) => (
          <div className="card" key={card.key}>
            <div className="tile-label">{card.label}</div>
            <div className="tile-value">{pct(card.value)}</div>
            <div className="tile-sub">
              {card.ci95 && (
                <>
                  95% 구간 {pct(card.ci95[0])}~{pct(card.ci95[1])}
                  <br />
                </>
              )}
              {card.note}
            </div>
            <div className="provenance">
              {card.source}
              {card.n != null && <> · n = {card.n}</>}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
