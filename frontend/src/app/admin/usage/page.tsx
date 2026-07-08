"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Navbar } from "@/components/Navbar";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, ArrowRight, DollarSign, Briefcase, Mic, TrendingUp } from "lucide-react";

interface Summary {
  period: { from: string; to: string };
  completed_jobs: number;
  total_cost_usd: number;
  avg_cost_per_job: number;
  whisper_minutes: number;
  revenue_usd: number;
  margin_usd: number;
}

interface Breakdown {
  by_task: Record<string, number>;
}

interface JobRow {
  id: string;
  video_title?: string;
  status: string;
  created_at: string;
  total_cost_usd: number;
  whisper_seconds: number;
  event_count: number;
}

function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
}) {
  return (
    <Card className="bg-slate-900/60 border-white/10">
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
            <p className="text-2xl font-bold text-white mt-1">{value}</p>
            {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
          </div>
          <div className="p-2 rounded-lg bg-purple-500/10">
            <Icon className="w-5 h-5 text-purple-400" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function TaskBars({ byTask }: { byTask: Record<string, number> }) {
  const entries = Object.entries(byTask).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, v]) => v), 0.001);

  return (
    <div className="space-y-3">
      {entries.length === 0 && (
        <p className="text-sm text-slate-500">Sin eventos en el período.</p>
      )}
      {entries.map(([task, cost]) => (
        <div key={task}>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-slate-300 capitalize">{task}</span>
            <span className="text-slate-400">${cost.toFixed(4)}</span>
          </div>
          <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-purple-500 to-pink-500 rounded-full"
              style={{ width: `${(cost / max) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function AdminUsageContent() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null);
  const [jobs, setJobs] = useState<JobRow[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    const to = new Date().toISOString();
    const from = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
    const qs = `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;

    const meRes = await apiFetch("/admin/usage/me");
    if (meRes.status === 403 || meRes.status === 401) {
      router.replace("/dashboard");
      return;
    }
    const me = await meRes.json();
    if (!me.isAdmin) {
      router.replace("/dashboard");
      return;
    }

    const [sumRes, brRes, jobsRes] = await Promise.all([
      apiFetch(`/admin/usage/summary${qs}`),
      apiFetch(`/admin/usage/breakdown${qs}`),
      apiFetch(`/admin/usage/jobs${qs}&limit=50`),
    ]);

    if (sumRes.ok) setSummary(await sumRes.json());
    if (brRes.ok) setBreakdown(await brRes.json());
    if (jobsRes.ok) {
      const j = await jobsRes.json();
      setJobs(j.jobs || []);
    }
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
      <main className="max-w-6xl mx-auto px-6 py-8 space-y-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">Costos por job</h1>
            <p className="text-slate-400 text-sm mt-1">
              Métricas reales de LLM y Whisper — panel interno
            </p>
          </div>
          <div className="flex gap-2">
            {[7, 30].map((d) => (
              <Button
                key={d}
                variant={days === d ? "default" : "outline"}
                size="sm"
                onClick={() => setDays(d)}
                className={days === d ? "bg-purple-600" : "border-white/10"}
              >
                {d} días
              </Button>
            ))}
          </div>
        </div>

        {summary && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="Costo total"
              value={`$${summary.total_cost_usd.toFixed(4)}`}
              sub={`${summary.completed_jobs} jobs completados`}
              icon={DollarSign}
            />
            <KpiCard
              label="Promedio / job"
              value={`$${summary.avg_cost_per_job.toFixed(4)}`}
              icon={Briefcase}
            />
            <KpiCard
              label="Whisper"
              value={`${summary.whisper_minutes.toFixed(1)} min`}
              icon={Mic}
            />
            <KpiCard
              label="Margen bruto"
              value={`$${summary.margin_usd.toFixed(2)}`}
              sub={`Ingreso est. $${summary.revenue_usd.toFixed(2)}`}
              icon={TrendingUp}
            />
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card className="bg-slate-900/60 border-white/10">
            <CardHeader>
              <CardTitle className="text-lg">Costo por tarea</CardTitle>
            </CardHeader>
            <CardContent>
              <TaskBars byTask={breakdown?.by_task || {}} />
            </CardContent>
          </Card>

          <Card className="bg-slate-900/60 border-white/10">
            <CardHeader>
              <CardTitle className="text-lg">Jobs recientes</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-slate-500 text-left">
                      <th className="px-6 py-3 font-medium">Título</th>
                      <th className="px-4 py-3 font-medium">Costo</th>
                      <th className="px-4 py-3 font-medium">Fecha</th>
                      <th className="px-4 py-3" />
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((j) => (
                      <tr key={j.id} className="border-b border-white/5 hover:bg-white/5">
                        <td className="px-6 py-3 max-w-[200px] truncate text-slate-200">
                          {j.video_title || j.id.slice(0, 8)}
                        </td>
                        <td className="px-4 py-3 text-purple-300 font-mono">
                          ${j.total_cost_usd.toFixed(4)}
                        </td>
                        <td className="px-4 py-3 text-slate-500">
                          {new Date(j.created_at).toLocaleDateString("es-AR")}
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            href={`/admin/usage/jobs/${j.id}`}
                            className="text-purple-400 hover:text-purple-300 inline-flex items-center gap-1"
                          >
                            Detalle <ArrowRight className="w-3 h-3" />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
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
