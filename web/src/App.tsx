import { useEffect, useState } from "react";
import type { DashboardStats } from "./types";
import { Headline } from "./components/Headline";
import { LabelSources } from "./components/LabelSources";
import { GradeDistribution, ReasonDistribution } from "./components/Distributions";
import { RepoTable } from "./components/RepoTable";
import { Yearly } from "./components/Yearly";
import { Performance } from "./components/Performance";
import { SystemEval } from "./components/SystemEval";

// API 는 docker compose 의 api 서비스 (localhost:8000). CORS 로 5173 에서 부른다.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

type Tab = "dashboard" | "search";

export function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/stats`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail ?? `HTTP ${res.status}`);
        }
        return res.json() as Promise<DashboardStats>;
      })
      .then(setStats)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <div className="app">
      <header>
        <div>
          <h1>Deleted Code Search</h1>
          <p className="subtitle">오픈소스에서 지워진 코드 — 왜 지웠고, 대신 무엇이 들어갔나</p>
        </div>
        <nav role="tablist">
          <button
            role="tab"
            aria-selected={tab === "dashboard"}
            className={tab === "dashboard" ? "tab active" : "tab"}
            onClick={() => setTab("dashboard")}
          >
            대시보드
          </button>
          <button role="tab" aria-selected={false} className="tab" disabled title="준비 중 (#8)">
            검색 <span className="soon">준비 중</span>
          </button>
        </nav>
      </header>

      <main>
        {error && (
          <div className="error">
            통계를 불러오지 못했다: {error}
            <br />
            API 가 떠 있는지 확인한다 (<code>docker compose up api</code>, {API_BASE}).
          </div>
        )}
        {!error && !stats && <p className="empty">불러오는 중…</p>}
        {stats && (
          <>
            {!stats.checks.ok && (
              <div className="error">
                집계값이 {stats.checks.against} 와 다르다. python -m search.stats 출력을 확인한다.
              </div>
            )}
            <div className="grid">
              <Headline stats={stats} />
              <LabelSources stats={stats} />
              <Performance stats={stats} />
              <ReasonDistribution stats={stats} />
              <GradeDistribution stats={stats} />
              <Yearly stats={stats} />
              <RepoTable stats={stats} />
              <SystemEval stats={stats} />
            </div>
          </>
        )}
      </main>
      <footer>
        {stats && (
          <>
            {stats.dataset_version} · 필터 규칙 {stats.filter_rule_version} · {stats.schema}
          </>
        )}
      </footer>
    </div>
  );
}
