import { useMemo, useState } from "react";
import type { DashboardStats, RepoRow } from "../types";
import { num, pct } from "../format";
import { Panel } from "./Panel";

type Key = keyof RepoRow;
// 이보다 작은 표본은 회수율을 흐리게 (v0.9 사후 필터로 표본이 줄어든 저장소가 있다)
const LOW_N = 10;

const COLUMNS: { key: Key; label: string; numeric?: boolean }[] = [
  { key: "repo", label: "저장소" },
  { key: "kept", label: "KEPT", numeric: true },
  { key: "noise_rows", label: "필터 제외", numeric: true },
  { key: "training_repo", label: "학습 저장소" },
  { key: "llm_n", label: "LLM 표본 n", numeric: true },
  { key: "llm_recovery_rate", label: "이유 회수율 (LLM 표본)", numeric: true },
];

function cell(row: RepoRow, key: Key): string {
  const value = row[key];
  if (key === "llm_recovery_rate") return pct(value as number | null, 0);
  if (key === "training_repo") return value ? "예" : "";
  if (typeof value === "number") return num(value);
  return value == null ? "-" : String(value);
}

function compare(a: RepoRow, b: RepoRow, key: Key): number {
  const x = a[key];
  const y = b[key];
  if (x == null && y == null) return 0;
  if (x == null) return 1; // 값 없는 행은 늘 아래로
  if (y == null) return -1;
  if (typeof x === "string") return x.localeCompare(y as string);
  return Number(x) - Number(y);
}

export function RepoTable({ stats }: { stats: DashboardStats }) {
  const { rows, source, n, note } = stats.repos;
  const [sortKey, setSortKey] = useState<Key>("kept");
  const [desc, setDesc] = useState(true);
  const sorted = useMemo(() => {
    const out = [...rows].sort((a, b) => compare(a, b, sortKey));
    return desc ? out.reverse() : out;
  }, [rows, sortKey, desc]);

  const onSort = (key: Key) => {
    if (key === sortKey) setDesc(!desc);
    else {
      setSortKey(key);
      setDesc(key !== "repo");
    }
  };

  return (
    <Panel
      title="저장소별 표"
      source={source}
      n={n}
      note={
        <>
          {note}. LLM 표본이 {LOW_N}건 미만인 저장소 {rows.filter((r) => r.llm_n < LOW_N).length}개는
          회수율을 흐리게 표시했다 (v0.9 사후 필터 NOISE_GENERATED 로 표본이 빠졌다). 회수율 = 근거 등급 EXPLICIT+INFERRED 비율(§15). 학습 저장소 = 확정 500건을
          뽑은 {rows.filter((r) => r.training_repo).length}개. <span className="tag">2015~</span> 는 2015년 이후 커밋만 순회한 recent_only
          저장소다.
        </>
      }
      wide
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className={col.numeric ? "numeric" : undefined}
                  aria-sort={sortKey === col.key ? (desc ? "descending" : "ascending") : "none"}
                >
                  <button type="button" onClick={() => onSort(col.key)}>
                    {col.label}
                    <span className="sort">{sortKey === col.key ? (desc ? "▼" : "▲") : ""}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.repo}>
                {COLUMNS.map((col) => (
                  <td
                    key={col.key}
                    className={[
                      col.numeric ? "numeric" : "",
                      col.key === "llm_recovery_rate" && row.llm_n < LOW_N ? "low-n" : "",
                    ].join(" ")}
                  >
                    {col.key === "repo" ? (
                      <>
                        <a
                          href={`https://github.com/${row.repo}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {row.repo}
                        </a>
                        {row.recent_only && <span className="tag">2015~</span>}
                      </>
                    ) : (
                      cell(row, col.key)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
