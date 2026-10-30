import { useState } from "react";
import type { DashboardStats } from "../types";
import { duration, num, pct } from "../format";
import { Panel } from "./Panel";

export function SystemEval({ stats }: { stats: DashboardStats }) {
  const s = stats.system;
  const [open, setOpen] = useState(false);
  const title = `시스템 평가 · 실행 기록 ${s.n}/${s.expected_repos}`;
  if (!s.available || !s.summary) {
    return (
      <Panel title={title} source="pipeline.run *_run.json (--runs)" n={0} wide>
        <p className="empty">{s.note}</p>
      </Panel>
    );
  }
  const sum = s.summary;
  // 처리 시간이 없는 실행(실패 등)도 빼지 않는다 - 맨 아래에 "-" 로
  const slowest = [...s.runs].sort(
    (a, b) => (b.elapsed_seconds ?? -1) - (a.elapsed_seconds ?? -1),
  );
  const versions = [...new Set(s.runs.map((r) => r.filter_rule_version ?? "-"))].sort();
  const tiles = [
    { label: "실패율", value: pct(sum.failure_rate), sub: `실패 ${sum.failed} / ${sum.repos}` },
    { label: "재시도한 저장소", value: num(sum.retried), sub: "시도 2회 이상" },
    {
      label: "저장소당 처리 시간 (중앙값)",
      value: duration(sum.median_elapsed_seconds),
      sub: `최장 ${duration(sum.max_elapsed_seconds)}`,
    },
    {
      label: "처리 시간 합계",
      value: duration(sum.total_elapsed_seconds),
      sub: "저장소별 시간의 합 (병렬 실행이라 벽시계 시간이 아니다)",
    },
  ];
  return (
    <Panel
      title={title}
      source="pipeline.run *_run.json (--runs)"
      n={s.n}
      note={`${s.note}. 채굴 규칙 버전: ${versions.join(", ")} (기존 20개는 #82 때 실행). 처리 시간은 재시도 대기를 포함한다.`}
      wide
    >
      <div className="tiles">
        {tiles.map((tile) => (
          <div className="tile" key={tile.label}>
            <div className="tile-label">{tile.label}</div>
            <div className="tile-value">{tile.value}</div>
            <div className="tile-sub">{tile.sub}</div>
          </div>
        ))}
      </div>
      <button type="button" className="link" onClick={() => setOpen(!open)}>
        {open ? "저장소별 기록 접기" : `저장소별 기록 보기 (${s.runs.length})`}
      </button>
      {open && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>저장소</th>
                <th>상태</th>
                <th className="numeric">처리 시간</th>
                <th className="numeric">시도</th>
                <th className="numeric">커밋</th>
                <th>규칙</th>
              </tr>
            </thead>
            <tbody>
              {slowest.map((r) => (
                <tr key={r.repo}>
                  <td>{r.repo}</td>
                  <td>{r.status === "SUCCESS" ? "성공" : `실패 (${r.failure_stage ?? "-"})`}</td>
                  <td className="numeric">{duration(r.elapsed_seconds)}</td>
                  <td className="numeric">{r.attempt_count ?? "-"}</td>
                  <td className="numeric">{r.commit_count == null ? "-" : num(r.commit_count)}</td>
                  <td>{r.filter_rule_version ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
