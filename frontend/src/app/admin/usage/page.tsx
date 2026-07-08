"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { UsageKpiGrid } from "@/components/admin/UsageKpiGrid";
import { MarginBar } from "@/components/admin/MarginBar";
import { CostBreakdownCharts } from "@/components/admin/CostBreakdownCharts";
import { JobsUsageTable } from "@/components/admin/JobsUsageTable";
import {
  fetchAdminUsageData,
  type UsageSummary,
  type UsageBreakdown,
  type JobUsageRow,
  type UsageBenchmarks,
} from "@/lib/admin-usage";
import { Loader2 } from "lucide-react";

function AdminUsageContent() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [breakdown, setBreakdown] = useState<UsageBreakdown | null>(null);
  const [jobs, setJobs] = useState<JobUsageRow[]>([]);
  const [benchmarks, setBenchmarks] = useState<UsageBenchmarks | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const data = await fetchAdminUsageData(days);

    if (!data.isAdmin) {
      router.replace("/dashboard");
      return;
    }

    setSummary(data.summary);
    setBreakdown(data.breakdown);
    setJobs(data.jobs);
    setBenchmarks(data.benchmarks);
    setLoading(false);
  }, [days, router]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-purple-400" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />
      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <p className="text-xs text-slate-600 uppercase tracking-widest">Admin</p>
              {benchmarks && benchmarks.sample_size > 0 && (
                <Badge
                  variant="outline"
                  className="text-[10px] border-purple-500/30 text-purple-400"
                >
                  {benchmarks.sample_size} jobs medidos
                </Badge>
              )}
            </div>
            <h1 className="text-2xl font-bold">Costos por job</h1>
            <p className="text-slate-400 text-sm mt-1">
              Métricas reales de LLM y Whisper — panel interno de operaciones
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            {[7, 30].map((d) => (
              <Button
                key={d}
                variant={days === d ? "default" : "outline"}
                size="sm"
                onClick={() => setDays(d)}
                className={days === d ? "bg-purple-600 hover:bg-purple-700" : "border-white/10 text-slate-400"}
              >
                {d} días
              </Button>
            ))}
          </div>
        </div>

        {/* KPIs */}
        {summary && <UsageKpiGrid summary={summary} />}

        {/* Margin bar */}
        {summary && summary.completed_jobs > 0 && <MarginBar summary={summary} />}

        {/* Charts + table */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <CostBreakdownCharts breakdown={breakdown} />
          <JobsUsageTable jobs={jobs} />
        </div>

        {/* Benchmarks footer */}
        {benchmarks && benchmarks.sample_size > 0 && (
          <div className="rounded-xl border border-white/5 bg-slate-900/40 px-5 py-4 text-xs text-slate-500">
            <span className="text-slate-400 font-medium">Benchmark rolling </span>
            (últimos {benchmarks.sample_size} jobs): promedio{" "}
            <span className="font-mono text-purple-300">
              ${benchmarks.avg_cost_per_job.toFixed(4)}
            </span>
            {" · "}
            referencia docs{" "}
            <span className="font-mono">
              ${benchmarks.legacy_estimates.docs_current_config.toFixed(2)}
            </span>
            {" · "}
            canvas legacy{" "}
            <span className="font-mono">
              ${benchmarks.legacy_estimates.happy_path.toFixed(2)}
            </span>
          </div>
        )}
      </main>
    </div>
  );
}

export default function AdminUsagePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-slate-950 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-purple-400" />
        </div>
      }
    >
      <AdminUsageContent />
    </Suspense>
  );
}
