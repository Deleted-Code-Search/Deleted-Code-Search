import { useEffect, useRef, useState } from "react";
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

const tabOf = (hash: string): Tab => (hash.startsWith("#search") ? "search" : "dashboard");

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
  const [tab, setTab] = useState<Tab>(() => tabOf(window.location.hash));
  // 검색 탭으로 돌아올 때 마지막 검색 주소로 간다 - 맨 "#search" 로 가면 SearchTab 이 상태를 비운다.
  const lastSearch = useRef("#search");
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 탭은 주소를 따른다 (뒤로/앞으로 가기, 주소창 수정). 탭 단추는 주소만 바꾼다.
  useEffect(() => {
    const onHashChange = () => setTab(tabOf(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

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
              if (tab === "dashboard") return;
              lastSearch.current = window.location.hash;
              window.location.hash = "#dashboard";
            }}
          >
            대시보드
          </button>
          <button
            role="tab"
            aria-selected={tab === "search"}
            className={tab === "search" ? "tab active" : "tab"}
            onClick={() => {
              if (tab !== "search") window.location.hash = lastSearch.current;
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
