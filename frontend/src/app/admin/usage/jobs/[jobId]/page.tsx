"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Navbar } from "@/components/Navbar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { JobCostComparison } from "@/components/admin/JobCostComparison";
import { JobPipelineView } from "@/components/admin/JobPipelineView";
import { EventDetailTable } from "@/components/admin/EventDetailTable";
import {
  checkIsAdmin,
  fetchJobUsageDetail,
  formatUsd,
  type JobUsageDetail,
} from "@/lib/admin-usage";
import { ArrowLeft, Loader2 } from "lucide-react";

export default function AdminJobUsagePage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router = useRouter();
  const [data, setData] = useState<JobUsageDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const isAdmin = await checkIsAdmin();
      if (!isAdmin) {
        router.replace("/dashboard");
        return;
      }

      const detail = await fetchJobUsageDetail(jobId);
      setData(detail);
      setLoading(false);
    })();
  }, [jobId, router]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-purple-400" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <Navbar />
        <main className="max-w-4xl mx-auto px-6 py-12 text-center text-slate-400">
          Job no encontrado
        </main>
      </div>
    );
  }

  const { job, usage_summary, events, events_grouped, comparison } = data;
  const summary = usage_summary as Record<string, unknown> | null;
  const costAvoided = Number(summary?.cost_avoided_usd || 0);
  const cacheHits = Number(summary?.cache_hits || 0);
  const whisperProvider = summary?.whisper_provider as string | undefined;

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />
      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        {/* Breadcrumb */}
        <Link
          href="/admin/usage"
          className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-purple-300 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Volver al panel
        </Link>

        {/* Job header */}
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <p className="text-xs text-slate-600 uppercase tracking-widest mb-1">Detalle de job</p>
            <h1 className="text-xl font-bold leading-snug">
              {job.video_title || job.id}
            </h1>
            <p className="text-slate-500 text-sm mt-1">
              {new Date(job.created_at).toLocaleString("es-AR")} ·{" "}
              <Badge variant="outline" className="text-[10px] border-white/10 text-slate-400">
                {job.status}
              </Badge>
            </p>
          </div>
          <div className="text-right shrink-0">
            <p className="text-xs text-slate-500">Costo total medido</p>
            <p className="text-2xl font-bold font-mono text-purple-300">
              {formatUsd(comparison.actual_cost_usd)}
            </p>
            <p className="text-xs text-slate-600 mt-0.5 font-mono">{job.id.slice(0, 8)}</p>
          </div>
        </div>

        {/* Summary stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: "Eventos", value: String(summary?.event_count || events.length) },
            {
              label: "Tokens in/out",
              value: `${((Number(summary?.total_input_tokens) || 0) / 1000).toFixed(1)}k / ${((Number(summary?.total_output_tokens) || 0) / 1000).toFixed(1)}k`,
            },
            {
              label: "Whisper",
              value: summary?.whisper_seconds
                ? `${(Number(summary.whisper_seconds) / 60).toFixed(1)} min`
                : "—",
            },
            {
              label: "Proveedor whisper",
              value: whisperProvider || "—",
            },
          ].map((stat) => (
            <Card key={stat.label} className="bg-slate-900/60 border-white/10">
              <CardContent className="pt-4 pb-4">
                <p className="text-[10px] text-slate-500 uppercase tracking-wider">{stat.label}</p>
                <p className="text-sm font-mono text-white mt-1">{stat.value}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Cache savings */}
        {costAvoided > 0 && (
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-5 py-4 flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-emerald-300">Ahorro por cache</p>
              <p className="text-xs text-emerald-600 mt-0.5">
                {cacheHits} cache hit{cacheHits !== 1 ? "s" : ""} en este job
              </p>
            </div>
            <p className="text-lg font-mono font-bold text-emerald-400">
              {formatUsd(costAvoided)} evitados
            </p>
          </div>
        )}

        {/* Comparison chart */}
        <JobCostComparison comparison={comparison} />

        {/* Pipeline */}
        <div>
          <h2 className="text-sm font-semibold text-slate-300 mb-3">Pipeline por tarea</h2>
          <JobPipelineView eventsGrouped={events_grouped} />
        </div>

        {/* Full event table */}
        <EventDetailTable events={events} />
      </main>
    </div>
  );
}
