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
import type { SourceDist } from "../types";
import { num, pct, share } from "../format";

interface Props {
  keys: string[];
  labels: Record<string, string>;
  human: SourceDist;
  llm: SourceDist;
}

const HUMAN_NAME = "사람 확정";
const LLM_NAME = "LLM 포함 표본";

// 두 출처의 n 이 달라(500 / 2,891) 비율로 나란히 그린다. 건수는 툴팁에.
export function PairedBars({ keys, labels, human, llm }: Props) {
  const data = keys.map((key) => ({
    key,
    name: labels[key] ?? key,
    human: share(human.counts[key] ?? 0, human.n),
    llm: share(llm.counts[key] ?? 0, llm.n),
    humanCount: human.counts[key] ?? 0,
    llmCount: llm.counts[key] ?? 0,
  }));
  const height = Math.max(180, keys.length * 44);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24 }} barGap={2}>
        <CartesianGrid horizontal={false} stroke="var(--grid)" />
        <XAxis
          type="number"
          tickFormatter={(v: number) => pct(v, 0)}
          stroke="var(--axis)"
          tick={{ fill: "var(--text-muted)", fontSize: 12 }}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={150}
          stroke="var(--axis)"
          tick={{ fill: "var(--text-secondary)", fontSize: 12 }}
        />
        <Tooltip
          cursor={{ fill: "var(--hover)" }}
          contentStyle={{
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
          formatter={(value: number, name: string, item) => {
            const row = item.payload as (typeof data)[number];
            const count = name.startsWith(HUMAN_NAME) ? row.humanCount : row.llmCount;
            return [`${pct(value)} (${num(count)}건)`, name];
          }}
        />
        <Legend wrapperStyle={{ color: "var(--text-secondary)", fontSize: 12 }} />
        <Bar
          dataKey="human"
          name={`${HUMAN_NAME} (n=${num(human.n)})`}
          fill="var(--series-1)"
          radius={[0, 4, 4, 0]}
          stroke="var(--surface-1)"
          strokeWidth={1}
        />
        <Bar
          dataKey="llm"
          name={`${LLM_NAME} (n=${num(llm.n)})`}
          fill="var(--series-2)"
          radius={[0, 4, 4, 0]}
          stroke="var(--surface-1)"
          strokeWidth={1}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
