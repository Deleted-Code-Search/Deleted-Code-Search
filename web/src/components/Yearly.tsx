import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DashboardStats } from "../types";
import { num } from "../format";
import { Panel } from "./Panel";

const compact = (v: number) => (v >= 1000 ? `${Math.round(v / 1000)}천` : String(v));

export function Yearly({ stats }: { stats: DashboardStats }) {
  const y = stats.yearly;
  return (
    <Panel
      title="연도별 추이 (author_date)"
      source={y.source}
      n={y.n}
      note={`${y.note}. 마지막 해는 수집 시점까지만 들어 있다.`}
      wide
    >
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={y.rows} margin={{ left: 8, right: 16 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="year"
            stroke="var(--axis)"
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <YAxis
            tickFormatter={compact}
            stroke="var(--axis)"
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <Tooltip
            cursor={{ fill: "var(--hover)" }}
            contentStyle={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
            formatter={(value: number, name: string) => [`${num(value)}건`, name]}
          />
          <Legend wrapperStyle={{ color: "var(--text-secondary)", fontSize: 12 }} />
          <Bar
            dataKey="FULL_FUNCTION"
            name="FULL_FUNCTION"
            stackId="kind"
            fill="var(--series-1)"
            stroke="var(--surface-1)"
            strokeWidth={1}
          />
          <Bar
            dataKey="PARTIAL"
            name="PARTIAL"
            stackId="kind"
            fill="var(--series-2)"
            stroke="var(--surface-1)"
            strokeWidth={1}
            radius={[4, 4, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </Panel>
  );
}
