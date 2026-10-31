import { useEffect, useRef, useState } from "react";
import { getJson } from "../api";
import { KIND_LABEL, REASON_LABEL, SOURCE_BADGE, num } from "../format";
import type { SearchMeta, SearchMode, SearchResponse } from "../types";
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
interface Example {
  label: string;
  q: string;
  filters?: Partial<Filters>;
}

const EXAMPLES: Example[] = [
  { label: "retry backoff", q: "retry backoff" },
  { label: "unused · 사람 확정", q: "unused", filters: { source: "HUMAN" } },
  { label: "replace · 사람 확정 · EXPLICIT", q: "replace", filters: { source: "HUMAN", grade: "EXPLICIT" } },
  { label: "py2 호환 코드", q: "py2" },
];

// 의미 검색 시연용 예시 (#193). 자연어 2개 + 코드 조각 1개 - 결과가 잘 나오는 것을 골랐다 (PR 본문).
const SEMANTIC_EXAMPLES: Example[] = [
  { label: "재시도 대기 (자연어)", q: "wait longer between retries after a failed request" },
  { label: "리다이렉트 인증 헤더 (자연어)", q: "strip authorization header when redirected to another host" },
  {
    label: "코드 조각",
    q: "def load_config(path):\n    with open(path) as f:\n        return yaml.safe_load(f)",
  },
];

interface Request {
  q: string;
  mode: SearchMode;
  filters: Filters;
  offset: number;
}

function toParams({ q, mode, filters, offset }: Request): URLSearchParams {
  const params = new URLSearchParams();
  // 키워드는 앞뒤 공백을 지운다. 의미 검색은 코드 들여쓰기를 지키려고 그대로 보낸다
  if (q.trim()) params.set("q", mode === "semantic" ? q : q.trim());
  // 기본값(keyword)은 주소에 넣지 않는다 - 1단계 링크가 그대로 동작한다
  if (mode === "semantic") params.set("mode", mode);
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
  const mode: SearchMode = params.get("mode") === "semantic" ? "semantic" : "keyword";
  return {
    request: {
      q: params.get("q") ?? "",
      mode,
      filters,
      offset: Number(params.get("offset") ?? 0) || 0,
    },
    id: params.get("id"),
  };
}

export function SearchTab() {
  const [initial] = useState(fromHash);
  const [meta, setMeta] = useState<SearchMeta | null>(null);
  const [input, setInput] = useState(initial.request?.q ?? "");
  const [mode, setMode] = useState<SearchMode>(initial.request?.mode ?? "keyword");
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
      setMode(next.request?.mode ?? "keyword");
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
    // 의미 검색은 질의가 있어야 한다 (API 422) - 필터만으로는 찾지 않는다
    if (request.mode === "semantic" && !request.q.trim()) {
      setResult(null);
      setError(null);
      return;
    }
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

  const run = (q: string, next: Filters, offset = 0, nextMode: SearchMode = mode) => {
    byUser.current = true;
    setRequest({ q, mode: nextMode, filters: next, offset });
    if (offset === 0) setSelected(null);
  };

  const setFilter = (key: keyof Filters, value: string) => {
    const next = { ...filters, [key]: value };
    setFilters(next);
    run(input, next);
  };

  const example = (ex: Example) => {
    const next = { ...NO_FILTERS, ...ex.filters };
    setInput(ex.q);
    setFilters(next);
    run(ex.q, next);
  };

  // 모드를 바꾸면 같은 검색어·필터로 다시 찾는다. 이미 요청이 있으면(필터만 검색 포함) 검색어가
  // 비어도 다시 보낸다 - 화면·요청·주소의 모드가 어긋나지 않게 (CodeRabbit #194)
  const switchMode = (next: SearchMode) => {
    if (next === mode) return;
    setMode(next);
    if (input.trim() || request) run(input, filters, 0, next);
  };

  const years: number[] = [];
  if (meta?.year_min != null && meta.year_max != null) {
    for (let y = meta.year_max; y >= meta.year_min; y--) years.push(y);
  }
  const offset = request?.offset ?? 0;
  const total = result?.total ?? 0;
  const semantic = mode === "semantic";
  const semanticMissing = semantic && meta !== null && meta.semantic === null;

  return (
    <div className="search">
      {/* 토글 버튼 묶음 - radio 역할은 화살표 키 이동까지 요구해 aria-pressed 로 (CodeRabbit #194) */}
      <div className="mode-switch" role="group" aria-label="검색 방식">
        {(["keyword", "semantic"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={mode === value}
            className={mode === value ? "active" : ""}
            onClick={() => switchMode(value)}
          >
            {value === "keyword" ? "키워드" : "의미"}
          </button>
        ))}
        <span className="muted">
          {semantic
            ? meta?.semantic
              ? `자연어 설명이나 코드 조각과 뜻이 가까운 삭제 함수 (FULL_FUNCTION ${num(meta.semantic.count)}건 임베딩)`
              : "자연어 설명이나 코드 조각과 뜻이 가까운 삭제 함수"
            : "낱말이 들어간 삭제 (함수 이름·경로·커밋·PR·이슈·코드)"}
        </span>
      </div>

      <form
        className={semantic ? "search-bar semantic" : "search-bar"}
        onSubmit={(event) => {
          event.preventDefault();
          run(input, filters);
        }}
      >
        {semantic ? (
          <textarea
            value={input}
            rows={4}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              // Enter 는 줄바꿈 (코드 붙여넣기), Ctrl/Cmd+Enter 로 검색
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                run(input, filters);
              }
            }}
            placeholder={
              "하는 일을 자연어로 적거나 코드 조각을 붙여넣는다 (Ctrl+Enter 로 검색)\n" +
              "예: wait longer between retries after a failed request"
            }
            aria-label="의미 검색 질의"
            spellCheck={false}
          />
        ) : (
          <input
            type="search"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="함수 이름, 경로, 커밋 메시지, PR·이슈 제목, 지워진 코드에서 찾기 (예: retry backoff)"
            aria-label="검색어"
          />
        )}
        <button type="submit">검색</button>
      </form>

      <div className="examples">
        <span className="muted">예시:</span>
        {(semantic ? SEMANTIC_EXAMPLES : EXAMPLES).map((ex) => (
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

      {semanticMissing && (
        <div className="error">
          의미 검색 임베딩이 아직 없다 — <code>python -m search.embed</code> 를 먼저 돌린다. 키워드 검색은
          그대로 된다.
        </div>
      )}
      {error && <div className="error">검색하지 못했다: {error}</div>}

      {semantic && request && !request.q.trim() && !error && (
        <p className="empty">의미 검색은 질의가 있어야 한다. 필터만으로 찾으려면 키워드 모드를 쓴다.</p>
      )}

      {!request && !error && (
        <p className="empty">
          {meta ? `${num(meta.records)}건 (${meta.dataset_version}) 에서 찾는다. ` : ""}
          검색어를 넣거나 필터를 고른다. 검색어 없이 필터만으로도 된다.
        </p>
      )}

      {request && result && !(request.mode === "semantic" && !request.q.trim()) && (
        <div className={selected ? "results-layout with-detail" : "results-layout"}>
          <div className="results">
            <p className="result-count muted">
              {num(total)}
              {result.total_capped ? "건 이상" : "건"} · {result.elapsed_ms.toFixed(0)}ms
              {result.mode === "semantic"
                ? " · 유사도 순 (FULL_FUNCTION 만)"
                : result.match
                  ? ""
                  : " · 검색어 없음, 필터만 (색인 순)"}
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
                      {hit.score != null && (
                        <span className="score" title="질의와의 코사인 유사도">
                          유사도 {hit.score.toFixed(3)}
                        </span>
                      )}
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
