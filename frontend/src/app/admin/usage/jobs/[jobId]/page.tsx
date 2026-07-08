"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Navbar } from "@/components/Navbar";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, ArrowLeft } from "lucide-react";

interface UsageEvent {
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

interface JobDetail {
  job: {
    id: string;
    video_title?: string;
    status: string;
    created_at: string;
    usage_summary?: Record<string, unknown>;
  };
  events: UsageEvent[];
  comparison: {
    actual_cost_usd: number;
    canvas_happy_path_usd: number;
    canvas_current_config_usd: number;
    delta_vs_happy: number;
    delta_vs_current: number;
  };
}

export default function AdminJobUsagePage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router = useRouter();
  const [data, setData] = useState<JobDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const meRes = await apiFetch("/admin/usage/me");
      if (!meRes.ok) {
        router.replace("/dashboard");
        return;
      }
      const me = await meRes.json();
      if (!me.isAdmin) {
        router.replace("/dashboard");
        return;
      }

      const res = await apiFetch(`/admin/usage/jobs/${jobId}`);
      if (res.ok) {
        setData(await res.json());
      }
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

  const { job, events, comparison } = data;
  const maxCompare = Math.max(
    comparison.actual_cost_usd,
    comparison.canvas_current_config_usd,
    comparison.canvas_happy_path_usd,
    0.001
  );

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />
      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        <Link
          href="/admin/usage"
          className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-purple-300"
        >
          <ArrowLeft className="w-4 h-4" /> Volver al panel
        </Link>

        <div>
          <h1 className="text-xl font-bold">{job.video_title || job.id}</h1>
          <p className="text-slate-500 text-sm mt-1">
            {new Date(job.created_at).toLocaleString("es-AR")} · {job.status}
          </p>
        </div>

        <Card className="bg-slate-900/60 border-white/10">
          <CardHeader>
            <CardTitle className="text-lg">Real vs estimación canvas</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {[
              { label: "Costo real", value: comparison.actual_cost_usd, color: "from-purple-500 to-pink-500" },
              { label: "Canvas config actual (~$0.20)", value: comparison.canvas_current_config_usd, color: "from-slate-500 to-slate-400" },
              { label: "Canvas happy path (~$0.13)", value: comparison.canvas_happy_path_usd, color: "from-emerald-500 to-teal-500" },
            ].map((row) => (
              <div key={row.label}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-slate-300">{row.label}</span>
                  <span className="font-mono text-slate-200">${row.value.toFixed(4)}</span>
                </div>
                <div className="h-3 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full bg-gradient-to-r ${row.color} rounded-full`}
                    style={{ width: `${(row.value / maxCompare) * 100}%` }}
                  />
                </div>
              </div>
            ))}
            <p className="text-xs text-slate-500">
              Δ vs happy path: {comparison.delta_vs_happy >= 0 ? "+" : ""}
              ${comparison.delta_vs_happy.toFixed(4)} · Δ vs config actual:{" "}
              {comparison.delta_vs_current >= 0 ? "+" : ""}
              ${comparison.delta_vs_current.toFixed(4)}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900/60 border-white/10">
          <CardHeader>
            <CardTitle className="text-lg">
              Eventos granulares ({events.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-slate-500 text-left">
                  <th className="px-4 py-3">Hora</th>
                  <th className="px-4 py-3">Task</th>
                  <th className="px-4 py-3">Modelo</th>
                  <th className="px-4 py-3">In/Out</th>
                  <th className="px-4 py-3">Audio</th>
                  <th className="px-4 py-3">USD</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="border-b border-white/5">
                    <td className="px-4 py-2 text-slate-500 whitespace-nowrap">
                      {new Date(e.created_at).toLocaleTimeString("es-AR")}
                    </td>
                    <td className="px-4 py-2">
                      <span className="capitalize text-slate-200">{e.task}</span>
                      {e.moment_index != null && (
                        <span className="text-slate-500 ml-1">m{e.moment_index}</span>
                      )}
                      {e.cache_hit && (
                        <Badge variant="outline" className="ml-2 text-xs border-emerald-500/30 text-emerald-400">
                          cache
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-400 font-mono text-xs max-w-[140px] truncate">
                      {e.model || "—"}
                    </td>
                    <td className="px-4 py-2 text-slate-400 font-mono text-xs">
                      {e.task === "whisper"
                        ? "—"
                        : `${e.input_tokens}/${e.output_tokens}${e.reasoning_tokens ? `+${e.reasoning_tokens}r` : ""}`}
                    </td>
                    <td className="px-4 py-2 text-slate-400 font-mono text-xs">
                      {e.audio_seconds != null ? `${Number(e.audio_seconds).toFixed(1)}s` : "—"}
                    </td>
                    <td className="px-4 py-2 text-purple-300 font-mono">
                      ${Number(e.estimated_cost_usd).toFixed(6)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
