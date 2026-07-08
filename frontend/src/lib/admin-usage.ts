import { apiFetch } from "@/lib/api";

// ─── Types ───────────────────────────────────────────────────────────────────

export interface UsageSummary {
  period: { from: string; to: string };
  completed_jobs: number;
  total_cost_usd: number;
  avg_cost_per_job: number;
  total_input_tokens: number;
  total_output_tokens: number;
  whisper_minutes: number;
  revenue_usd: number;
  margin_usd: number;
  margin_pct: number;
  cost_as_pct_of_revenue: number;
  total_cache_hits: number;
  total_cost_avoided_usd: number;
  credit_price_usd: number;
}

export interface UsageBreakdown {
  period: { from: string; to: string };
  event_count: number;
  by_task: Record<string, number>;
  by_model: Record<string, number>;
  by_provider: Record<string, number>;
}

export interface UsageBenchmarks {
  sample_size: number;
  avg_cost_per_job: number;
  avg_by_task: Record<string, number>;
  avg_by_model: Record<string, number>;
  avg_by_provider: Record<string, number>;
  avg_whisper_seconds: number;
  avg_cache_hits: number;
  avg_cost_avoided_usd: number;
  legacy_estimates: {
    happy_path: number;
    docs_current_config: number;
  };
}

export interface JobUsageRow {
  id: string;
  video_title?: string;
  video_url?: string;
  status: string;
  created_at: string;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  whisper_seconds: number;
  event_count: number;
  cache_hits: number;
  cost_avoided_usd: number;
  by_task: Record<string, number>;
  margin_usd: number;
  margin_pct: number;
}

export interface UsageEvent {
  id: string;
  created_at: string;
  task: string;
  model?: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  audio_seconds?: number;
  estimated_cost_usd: number;
  cache_hit: boolean;
  moment_index?: number;
}

export interface PipelineGroup {
  label: string;
  total_cost_usd: number;
  event_count: number;
  events: UsageEvent[];
  by_moment: Record<string, UsageEvent[]>;
}

export interface JobUsageDetail {
  job: {
    id: string;
    video_title?: string;
    video_url?: string;
    status: string;
    created_at: string;
    user_id?: string;
  };
  usage_summary: Record<string, unknown> | null;
  events: UsageEvent[];
  events_grouped: Record<string, PipelineGroup>;
  comparison: {
    actual_cost_usd: number;
    benchmark_avg_cost_usd: number;
    benchmark_sample_size: number;
    legacy_happy_path_usd: number;
    legacy_docs_config_usd: number;
    delta_vs_benchmark: number;
    delta_vs_happy: number;
    delta_vs_docs: number;
  };
}

// ─── Labels ──────────────────────────────────────────────────────────────────

export const TASK_LABELS: Record<string, string> = {
  classifier: "Clasificador",
  analysis: "Selección de momentos",
  copy: "Copy (pasada B)",
  judge: "Juez de scoring",
  whisper: "Whisper",
};

export const TASK_ORDER = ["classifier", "analysis", "copy", "judge", "whisper"];

export const CHART_COLORS = [
  "#a855f7",
  "#ec4899",
  "#8b5cf6",
  "#6366f1",
  "#14b8a6",
  "#f59e0b",
  "#64748b",
];

// ─── Formatters ──────────────────────────────────────────────────────────────

export function formatUsd(value: number, decimals = 4): string {
  return `$${value.toFixed(decimals)}`;
}

export function formatUsdShort(value: number): string {
  if (value >= 1) return `$${value.toFixed(2)}`;
  if (value >= 0.01) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(4)}`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatPct(n: number): string {
  return `${n.toFixed(1)}%`;
}

export function taskLabel(task: string): string {
  return TASK_LABELS[task] || task;
}

export function shortModel(model?: string): string {
  if (!model) return "—";
  const parts = model.split("/");
  return parts[parts.length - 1] || model;
}

// ─── API helpers ─────────────────────────────────────────────────────────────

export function periodQuery(days: number): string {
  const to = new Date().toISOString();
  const from = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
  return `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
}

export async function fetchAdminUsageData(days: number) {
  const qs = periodQuery(days);
  const meRes = await apiFetch("/admin/usage/me");
  if (!meRes.ok) return { isAdmin: false as const };

  const me = await meRes.json();
  if (!me.isAdmin) return { isAdmin: false as const };

  const [summaryRes, breakdownRes, jobsRes, benchmarksRes] = await Promise.all([
    apiFetch(`/admin/usage/summary${qs}`),
    apiFetch(`/admin/usage/breakdown${qs}`),
    apiFetch(`/admin/usage/jobs${qs}&limit=50`),
    apiFetch("/admin/usage/benchmarks"),
  ]);

  return {
    isAdmin: true as const,
    summary: summaryRes.ok ? ((await summaryRes.json()) as UsageSummary) : null,
    breakdown: breakdownRes.ok ? ((await breakdownRes.json()) as UsageBreakdown) : null,
    jobs: jobsRes.ok
      ? ((await jobsRes.json()) as { jobs: JobUsageRow[] }).jobs
      : [],
    benchmarks: benchmarksRes.ok
      ? ((await benchmarksRes.json()) as UsageBenchmarks)
      : null,
  };
}

export async function fetchJobUsageDetail(jobId: string): Promise<JobUsageDetail | null> {
  const res = await apiFetch(`/admin/usage/jobs/${jobId}`);
  if (!res.ok) return null;
  return res.json();
}

export async function checkIsAdmin(): Promise<boolean> {
  const res = await apiFetch("/admin/usage/me");
  if (!res.ok) return false;
  const data = await res.json();
  return !!data.isAdmin;
}

export function mapToChartData(
  record: Record<string, number>,
  labelFn: (key: string) => string = (k) => k,
) {
  return Object.entries(record)
    .sort((a, b) => b[1] - a[1])
    .map(([key, value], i) => ({
      name: labelFn(key),
      key,
      value,
      fill: CHART_COLORS[i % CHART_COLORS.length],
    }));
}
