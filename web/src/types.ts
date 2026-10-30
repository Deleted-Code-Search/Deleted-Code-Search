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
