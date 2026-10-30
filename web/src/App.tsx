import { useEffect, useState } from "react";
import type { DashboardStats } from "./types";
import { Headline } from "./components/Headline";
import { LabelSources } from "./components/LabelSources";
import { GradeDistribution, ReasonDistribution } from "./components/Distributions";
import { RepoTable } from "./components/RepoTable";
import { Yearly } from "./components/Yearly";
import { Performance } from "./components/Performance";
import { SystemEval } from "./components/SystemEval";
import { SearchTab } from "./components/SearchTab";
import { API_BASE, getJson } from "./api";

type Tab = "dashboard" | "search";

// search.stats SCHEMA_VERSION 과 같아야 한다.
const SCHEMA = "dashboard_stats/1";
const REQUIRED: (keyof DashboardStats)[] = [
  "headline",
  "label_sources",
  "reason_distribution",
  "grade_distribution",
  "repos",
  "yearly",
  "performance",
  "system",
  "checks",
];

// 모양만 본다 - schema 값과 패널이 쓰는 최상위 필드가 있는지.
function validate(body: unknown): DashboardStats {
  if (typeof body !== "object" || body === null) throw new Error("응답이 객체가 아니다");
  const data = body as Record<string, unknown>;
  if (data.schema !== SCHEMA) {
    throw new Error(`schema 가 ${String(data.schema)} 이다 (기대: ${SCHEMA})`);
  }
  const missing = REQUIRED.filter((key) => typeof data[key] !== "object" || data[key] === null);
  if (missing.length) throw new Error(`필수 필드가 없다: ${missing.join(", ")}`);
  return body as DashboardStats;
}

export function App() {
  const [tab, setTab] = useState<Tab>(() =>
    window.location.hash.startsWith("#search") ? "search" : "dashboard",
  );
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJson<unknown>("/api/stats")
      .then(validate)
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
            onClick={() => {
              setTab("dashboard");
              window.history.replaceState(null, "", "#dashboard");
            }}
          >
            대시보드
          </button>
          <button
            role="tab"
            aria-selected={tab === "search"}
            className={tab === "search" ? "tab active" : "tab"}
            onClick={() => {
              setTab("search");
              if (!window.location.hash.startsWith("#search")) {
                window.history.replaceState(null, "", "#search");
              }
            }}
          >
            검색
          </button>
        </nav>
      </header>

      {/* 탭을 오가도 검색 상태가 남게 숨기기만 한다 */}
      <main hidden={tab !== "search"}>
        <SearchTab />
      </main>
      <main hidden={tab !== "dashboard"}>
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
