import { useEffect, useRef, useState } from "react";
import { getJson } from "../api";
import { KIND_LABEL, REASON_LABEL, SOURCE_BADGE, num } from "../format";
import type { SearchMeta, SearchResponse } from "../types";
import { Highlight } from "./Highlight";
import { ReasonBadges } from "./ReasonBadges";
import { RecordDetail } from "./RecordDetail";

const PAGE = 20;

interface Filters {
  repo: string;
  reason: string;
  grade: string;
  source: string;
  kind: string;
  year_from: string;
  year_to: string;
}

const NO_FILTERS: Filters = {
  repo: "",
  reason: "",
  grade: "",
  source: "",
  kind: "",
  year_from: "",
  year_to: "",
};

// 시연용 예시 (#191). 실제 색인(dataset-v0.9)에서 결과가 잘 나오는 것을 골랐다 - PR 본문 참고.
const EXAMPLES: { label: string; q: string; filters?: Partial<Filters> }[] = [
  { label: "retry backoff", q: "retry backoff" },
  { label: "unused · 사람 확정", q: "unused", filters: { source: "HUMAN" } },
  { label: "replace · 사람 확정 · EXPLICIT", q: "replace", filters: { source: "HUMAN", grade: "EXPLICIT" } },
  { label: "py2 호환 코드", q: "py2" },
];

interface Request {
  q: string;
  filters: Filters;
  offset: number;
}

function toParams({ q, filters, offset }: Request): URLSearchParams {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  if (offset) params.set("offset", String(offset));
  return params;
}

// 주소의 #search?q=…&repo=…&id=… 로 검색 상태를 되살린다 - 시연·스크린샷·링크 공유, 뒤로/앞으로 가기.
// 사용자가 검색·결과 선택·페이지를 바꾸면 기록을 하나 쌓고(pushState), 주소가 바뀌면(hashchange: 뒤로/앞으로,
// 주소창 수정) 다시 읽는다. 주소에서 읽은 상태는 기록을 쌓지 않고 주소 모양만 맞춘다(replaceState).
function fromHash(): { request: Request | null; id: string | null } {
  const hash = window.location.hash;
  if (!hash.startsWith("#search?")) return { request: null, id: null };
  const params = new URLSearchParams(hash.slice("#search?".length));
  const filters = { ...NO_FILTERS };
  for (const key of Object.keys(filters) as (keyof Filters)[]) filters[key] = params.get(key) ?? "";
  return {
    request: { q: params.get("q") ?? "", filters, offset: Number(params.get("offset") ?? 0) || 0 },
    id: params.get("id"),
  };
}

export function SearchTab() {
  const [initial] = useState(fromHash);
  const [meta, setMeta] = useState<SearchMeta | null>(null);
  const [input, setInput] = useState(initial.request?.q ?? "");
  const [request, setRequest] = useState<Request | null>(initial.request);
  const [filters, setFilters] = useState<Filters>(initial.request?.filters ?? NO_FILTERS);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(initial.id);
  // 사용자가 바꾼 상태인가. 그러면 다음 주소 맞추기가 기록을 하나 쌓는다. 처음 열 때·주소에서 읽은 상태는 아니다.
  const byUser = useRef(false);

  useEffect(() => {
    const onHashChange = () => {
      // #dashboard 로 가면 검색 상태는 그대로 둔다 (탭만 바뀐다, App)
      if (!window.location.hash.startsWith("#search")) return;
      const next = fromHash();
      byUser.current = false;
      setInput(next.request?.q ?? "");
      setFilters(next.request?.filters ?? NO_FILTERS);
      setRequest(next.request);
      setSelected(next.id);
      if (!next.request) {
        setResult(null);
        setError(null);
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    getJson<SearchMeta>("/api/search/meta")
      .then(setMeta)
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    setLoading(true);
    const params = toParams(request);
    params.set("limit", String(PAGE));
    getJson<SearchResponse>(`/api/search?${params}`, controller.signal)
      .then((body) => {
        setResult(body);
        setError(null);
        setLoading(false);
      })
      .catch((err: Error) => {
        if (err.name === "AbortError") return;
        setError(err.message);
        setLoading(false);
      });
    return () => controller.abort();
  }, [request]);

  useEffect(() => {
    const push = byUser.current;
    byUser.current = false;
    if (!request || !window.location.hash.startsWith("#search")) return;
    const params = toParams(request);
    if (selected) params.set("id", selected);
    const hash = `#search?${params}`;
    if (hash === window.location.hash) return;
    if (push) window.history.pushState(null, "", hash);
    else window.history.replaceState(null, "", hash);
  }, [request, selected]);

  const select = (id: string | null) => {
    byUser.current = true;
    setSelected(id);
  };

  const run = (q: string, next: Filters, offset = 0) => {
    byUser.current = true;
    setRequest({ q, filters: next, offset });
    if (offset === 0) setSelected(null);
  };

  const setFilter = (key: keyof Filters, value: string) => {
    const next = { ...filters, [key]: value };
    setFilters(next);
    run(input, next);
  };

  const example = (ex: (typeof EXAMPLES)[number]) => {
    const next = { ...NO_FILTERS, ...ex.filters };
    setInput(ex.q);
    setFilters(next);
    run(ex.q, next);
  };

  const years: number[] = [];
  if (meta?.year_min != null && meta.year_max != null) {
    for (let y = meta.year_max; y >= meta.year_min; y--) years.push(y);
  }
  const offset = request?.offset ?? 0;
  const total = result?.total ?? 0;

  return (
    <div className="search">
      <form
        className="search-bar"
        onSubmit={(event) => {
          event.preventDefault();
          run(input, filters);
        }}
      >
        <input
          type="search"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="함수 이름, 경로, 커밋 메시지, PR·이슈 제목, 지워진 코드에서 찾기 (예: retry backoff)"
          aria-label="검색어"
        />
        <button type="submit">검색</button>
      </form>

      <div className="examples">
        <span className="muted">예시:</span>
        {EXAMPLES.map((ex) => (
          <button key={ex.label} type="button" className="chip" onClick={() => example(ex)}>
            {ex.label}
          </button>
        ))}
      </div>

      <div className="filters">
        <label>
          저장소
          <select value={filters.repo} onChange={(e) => setFilter("repo", e.target.value)}>
            <option value="">전체</option>
            {meta?.repos.map((repo) => (
              <option key={repo} value={repo}>
                {repo}
              </option>
            ))}
          </select>
        </label>
        <label>
          이유
          <select value={filters.reason} onChange={(e) => setFilter("reason", e.target.value)}>
            <option value="">전체</option>
            {meta?.reasons.map((reason) => (
              <option key={reason} value={reason}>
                {REASON_LABEL[reason] ?? reason}
              </option>
            ))}
          </select>
        </label>
        <label>
          근거 등급
          <select value={filters.grade} onChange={(e) => setFilter("grade", e.target.value)}>
            <option value="">전체</option>
            {meta?.grades.map((grade) => (
              <option key={grade} value={grade}>
                {grade}
              </option>
            ))}
          </select>
        </label>
        <label>
          출처
          <select value={filters.source} onChange={(e) => setFilter("source", e.target.value)}>
            <option value="">전체</option>
            {meta?.sources.map((source) => (
              <option key={source} value={source}>
                {SOURCE_BADGE[source] ?? source}
              </option>
            ))}
          </select>
        </label>
        <label>
          범위
          <select value={filters.kind} onChange={(e) => setFilter("kind", e.target.value)}>
            <option value="">전체</option>
            {meta?.kinds.map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABEL[kind] ?? kind}
              </option>
            ))}
          </select>
        </label>
        <label>
          연도
          <span className="year-range">
            <select
              value={filters.year_from}
              onChange={(e) => setFilter("year_from", e.target.value)}
              aria-label="시작 연도"
            >
              <option value="">처음</option>
              {[...years].reverse().map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
            ~
            <select
              value={filters.year_to}
              onChange={(e) => setFilter("year_to", e.target.value)}
              aria-label="끝 연도"
            >
              <option value="">끝</option>
              {years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </span>
        </label>
      </div>

      {error && <div className="error">검색하지 못했다: {error}</div>}

      {!request && !error && (
        <p className="empty">
          {meta ? `${num(meta.records)}건 (${meta.dataset_version}) 에서 찾는다. ` : ""}
          검색어를 넣거나 필터를 고른다. 검색어 없이 필터만으로도 된다.
        </p>
      )}

      {request && result && (
        <div className={selected ? "results-layout with-detail" : "results-layout"}>
          <div className="results">
            <p className="result-count muted">
              {num(total)}
              {result.total_capped ? "건 이상" : "건"} · {result.elapsed_ms.toFixed(0)}ms
              {result.match ? "" : " · 검색어 없음, 필터만 (색인 순)"}
              {loading && " · 불러오는 중…"}
            </p>
            {result.results.length === 0 && <p className="empty">결과가 없다.</p>}
            <ol className="hits" start={offset + 1}>
              {result.results.map((hit) => (
                <li key={hit.id}>
                  <button
                    type="button"
                    className={hit.id === selected ? "hit active" : "hit"}
                    onClick={() => select(hit.id)}
                  >
                    <span className="hit-title">
                      <code>{hit.function_name ?? "(이름 없음)"}</code>
                      <span className="muted">
                        {" "}
                        {hit.repo} · {hit.year ?? "?"}
                      </span>
                    </span>
                    <span className="hit-path">
                      <code>{hit.file_path}</code>
                      {hit.deletion_kind === "PARTIAL" && <span className="tag">PARTIAL</span>}
                      {hit.is_test_code && <span className="tag">테스트</span>}
                      {hit.code_policy === "LINK_ONLY" && <span className="tag">코드 미제공</span>}
                    </span>
                    <ReasonBadges
                      source={hit.source}
                      label={hit.reason_label}
                      grade={hit.evidence_grade}
                    />
                    {hit.commit_first_line && (
                      <span className="hit-commit">{hit.commit_first_line}</span>
                    )}
                    {hit.snippet && (
                      <span className="snippet">
                        <Highlight text={hit.snippet} />
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ol>
            {total > PAGE && (
              <div className="pager">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => run(request.q, request.filters, Math.max(0, offset - PAGE))}
                >
                  ← 이전
                </button>
                <span className="muted">
                  {num(offset + 1)}–{num(Math.min(offset + PAGE, total))} / {num(total)}
                  {result.total_capped ? "+" : ""}
                </span>
                <button
                  type="button"
                  disabled={offset + PAGE >= total}
                  onClick={() => run(request.q, request.filters, offset + PAGE)}
                >
                  다음 →
                </button>
              </div>
            )}
          </div>
          {selected && <RecordDetail id={selected} onClose={() => select(null)} />}
        </div>
      )}
    </div>
  );
}
