import type { DashboardStats } from "../types";
import { SOURCE_LABEL, num, pct, share } from "../format";
import { Panel } from "./Panel";

// 5개 출처, 한 계열 - 막대 하나에 값 라벨을 직접 단다. 작은 값(HUMAN 500)도 숫자로 보이게
// 막대 길이는 비율, 라벨은 건수·비율.
export function LabelSources({ stats }: { stats: DashboardStats }) {
  const { counts, n, source } = stats.label_sources;
  const max = Math.max(...Object.values(counts));
  const training = stats.repos.rows.filter((r) => r.training_repo).length;
  const others = stats.repos.rows.length - training;
  return (
    <Panel
      title="라벨 출처 분포"
      source={source}
      n={n}
      note={`사람 확정·LLM 포함 표본만 이유 분포에 쓴다. 모델만 라벨은 학습 저장소 ${training}개에만 남겼고, 나머지 ${others}개는 이유 미구분이다. PARTIAL 은 분류 대상이 아니다.`}
    >
      <div className="hbars">
        {Object.entries(counts).map(([key, count]) => (
          <div className="hbar-row" key={key} title={`${key}: ${num(count)}건`}>
            <div className="hbar-label">
              {SOURCE_LABEL[key] ?? key}
              <span className="code">{key}</span>
            </div>
            <div className="hbar-track">
              <div
                className="hbar-fill"
                style={{ width: `${Math.max(share(count, max) * 100, 0.6)}%` }}
              />
            </div>
            <div className="hbar-value">
              {num(count)} <span className="muted">({pct(share(count, n), share(count, n) < 0.01 ? 2 : 1)})</span>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
