// docs/reports/dashboard_stats.json (search.stats, schema dashboard_stats/1) 의 모양.

export type Counts = Record<string, number>;

export interface SourceDist {
  n: number;
  counts: Counts;
}

export interface RepoRow {
  repo: string;
  kept: number;
  full_function: number;
  partial: number;
  noise_rows: number | null;
  training_repo: boolean;
  recent_only: boolean;
  human_n: number;
  llm_n: number;
  llm_recovered: number;
  llm_recovery_rate: number | null;
}

export interface PerfCard {
  key: string;
  label: string;
  value: number;
  n: number | null;
  ci95: [number, number] | null;
  source: string;
  note: string;
}

export interface RunRow {
  repo: string;
  status: string;
  elapsed_seconds: number | null;
  attempt_count: number | null;
  commit_count: number | null;
  recent_only: boolean | null;
  filter_rule_version: string | null;
  failure_stage: string | null;
}

export interface DashboardStats {
  schema: string;
  dataset_version: string;
  filter_rule_version: string;
  headline: {
    source: string;
    repos: number;
    deleted_before_filter: number;
    noise_rows: number;
    kept: number;
    full_function: number;
    partial: number;
    with_context: number;
  };
  label_sources: { source: string; n: number; counts: Counts };
  reason_distribution: {
    source: string;
    note: string;
    excluded_sources: string[];
    labels: string[];
    HUMAN: SourceDist;
    MODEL_LLM: SourceDist;
  };
  grade_distribution: {
    source: string;
    note: string;
    grades: string[];
    explicit_rate_human: number | null;
    HUMAN: SourceDist;
    MODEL_LLM: SourceDist;
  };
  repos: { source: string; n: number; note: string; rows: RepoRow[] };
  yearly: {
    source: string;
    n: number;
    note: string;
    recent_only_repos: string[];
    rows: { year: string; FULL_FUNCTION: number; PARTIAL: number }[];
  };
  performance: { source: string; cards: PerfCard[] };
  system: {
    available: boolean;
    n: number;
    expected_repos: number;
    note: string;
    runs: RunRow[];
    summary: {
      repos: number;
      failed: number;
      failure_rate: number;
      retried: number;
      total_elapsed_seconds: number;
      median_elapsed_seconds: number | null;
      max_elapsed_seconds: number | null;
    } | null;
  };
  checks: { ok: boolean; against: string; mismatches: unknown[] };
}

// 검색 1단계 (#191)·2단계 (#193). search/api.py 의 /api/search, /api/search/meta, /api/records/{id}.

export type SearchMode = "keyword" | "semantic";

// 의미 검색 임베딩 정보 (search.embed manifest). 임베딩 파일이 없으면 null.
export interface SemanticMeta {
  model: string;
  dim: number;
  count: number;
  created_at: string;
  repos: string[];
}

export interface SearchMeta {
  dataset_version: string;
  records: number;
  repos: string[];
  reasons: string[];
  grades: string[];
  sources: string[];
  kinds: string[];
  year_min: number | null;
  year_max: number | null;
  fts_columns: string[];
  semantic: SemanticMeta | null;
}

export interface SearchHit {
  id: string;
  repo: string;
  function_name: string | null;
  file_path: string;
  year: number | null;
  deletion_kind: string;
  reason_label: string | null;
  evidence_grade: string | null;
  source: string;
  code_policy: string;
  is_test_code: boolean;
  commit_first_line: string | null;
  // 강조 구간은 \u0002 … \u0003 로 감싸 온다 (키워드 검색일 때만)
  snippet: string | null;
  // 의미 검색일 때만 - 질의와의 코사인 유사도
  score?: number;
}

export interface SearchResponse {
  q: string;
  mode: SearchMode;
  match: string | null;
  total: number;
  total_capped: boolean;
  limit: number;
  offset: number;
  results: SearchHit[];
  elapsed_ms: number;
}

// Release jsonl 한 줄 (CHARTER §4.4 DeletionRecord + code_policy·classification). 화면이 쓰는 칸만.
export interface DeletionRecord {
  id: string;
  repo: string;
  commit_sha: string;
  author_date: string | null;
  file_path: string;
  start_line: number | null;
  end_line: number | null;
  function_name: string | null;
  function_signature: string | null;
  deleted_body: string | null;
  deletion_kind: string;
  is_test_code: boolean;
  context: {
    commit_message: string | null;
    pr_number: number | null;
    pr_title: string | null;
    pr_body: string | null;
    pr_labels: string[] | null;
    issue_numbers: number[] | null;
    issue_titles: string[] | null;
  } | null;
  replacement: { code: string | null; match_method: string | null; confidence: number | null } | null;
  reason: {
    label: string;
    evidence_grade: string;
    evidence_text: string | null;
    confidence: number | null;
    classifier_version: string | null;
  } | null;
  source_url: string;
  code_policy: string;
  classification: { source: string; classifier_version: string | null } | null;
}

export interface RecordDetailResponse {
  record: DeletionRecord;
  review_comment_count: number | null;
}
