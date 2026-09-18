"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ThumbsUp, ThumbsDown } from "lucide-react";

// ─── Tipos ────────────────────────────────────────────────────────────────
// Reflejan la forma de GET /admin/usage/feedback (backend/src/routes/admin-usage.js).

export interface FeedbackRow {
  content_result_id: string;
  job_id: string | null;
  posteable: boolean;
  motivo: string | null;
  score_judge: { hook: number; retention: number; shareability: number; reasoning?: string } | null;
  created_at: string;
}

export interface FeedbackAdminSummary {
  period: { from: string; to: string };
  total: number;
  posteable_rate: number | null;
  by_motivo: Record<string, number>;
  judge_avg_posteable: number | null;
  judge_avg_no_posteable: number | null;
  rows: FeedbackRow[];
}

const MOTIVO_LABELS: Record<string, string> = {
  arranca_mal: "Arranca mal",
  termina_mal: "Termina mal",
  momento_flojo: "Momento flojo",
  subtitulos_mal: "Subtítulos mal",
  se_ve_mal: "Se ve mal",
  copy_malo: "Copy malo",
  otro: "Otro",
};

interface StatProps {
  label: string;
  value: string;
  sub?: string;
  accent?: "purple" | "emerald" | "amber" | "slate";
}

// Declarado fuera de FeedbackPanel a propósito: un componente creado dentro
// del render pierde su estado en cada re-render (ver el mismo problema, ya
// existente, en JobsUsageTable.tsx — no lo repetimos acá).
function Stat({ label, value, sub, accent = "slate" }: StatProps) {
  const accentMap = {
    purple: "text-purple-300",
    emerald: "text-emerald-400",
    amber: "text-amber-400",
    slate: "text-slate-300",
  };
  return (
    <div className="rounded-lg bg-slate-950/40 border border-slate-800 px-3 py-2.5">
      <p className="text-[10px] text-slate-500 uppercase tracking-wider font-medium">{label}</p>
      <p className={`text-lg font-bold font-mono mt-0.5 ${accentMap[accent]}`}>{value}</p>
      {sub && <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

interface FeedbackPanelProps {
  data: FeedbackAdminSummary | null;
}

/**
 * Panel "Feedback humano" (W7): % posteable, motivos de rechazo y el juez
 * promedio de los clips posteables vs no posteables. El gap entre esos dos
 * últimos números es la calibración del juez (docs/PLAN_CALIDAD.md §2 y §7
 * decisión 1) — si es chico, el juez no está discriminando calidad real.
 */
export function FeedbackPanel({ data }: FeedbackPanelProps) {
  if (!data || data.total === 0) {
    return (
      <Card className="bg-slate-900/60 border-white/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-lg">Feedback humano</CardTitle>
          <p className="text-xs text-slate-500">
            Sin etiquetas &quot;¿lo publicarías?&quot; en este período todavía.
          </p>
        </CardHeader>
      </Card>
    );
  }

  const motivoEntries = Object.entries(data.by_motivo).sort((a, b) => b[1] - a[1]);
  const judgeGap =
    data.judge_avg_posteable !== null && data.judge_avg_no_posteable !== null
      ? data.judge_avg_posteable - data.judge_avg_no_posteable
      : null;

  return (
    <Card className="bg-slate-900/60 border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Feedback humano</CardTitle>
        <p className="text-xs text-slate-500">
          {data.total} clip{data.total === 1 ? "" : "s"} etiquetado
          {data.total === 1 ? "" : "s"} en el período (última etiqueta por clip)
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat
            label="Posteable"
            value={data.posteable_rate !== null ? `${data.posteable_rate.toFixed(0)}%` : "—"}
            accent="emerald"
          />
          <Stat
            label="Juez / posteables"
            value={data.judge_avg_posteable !== null ? data.judge_avg_posteable.toFixed(1) : "—"}
            accent="purple"
          />
          <Stat
            label="Juez / no posteables"
            value={data.judge_avg_no_posteable !== null ? data.judge_avg_no_posteable.toFixed(1) : "—"}
            accent="amber"
          />
          <Stat
            label="Calibración del juez"
            value={judgeGap !== null ? `${judgeGap >= 0 ? "+" : ""}${judgeGap.toFixed(1)}` : "—"}
            sub={
              judgeGap !== null
                ? judgeGap > 1
                  ? "discrimina bien"
                  : "casi no distingue"
                : undefined
            }
            accent={judgeGap !== null && judgeGap > 1 ? "emerald" : "amber"}
          />
        </div>

        {motivoEntries.length > 0 && (
          <div>
            <p className="text-[11px] text-slate-500 uppercase tracking-wider font-medium mb-1.5">
              Por qué no lo publicarían
            </p>
            <div className="flex flex-wrap gap-1.5">
              {motivoEntries.map(([motivo, count]) => (
                <Badge key={motivo} variant="outline" className="text-[10px] border-red-500/25 text-red-300">
                  {MOTIVO_LABELS[motivo] || motivo} · {count}
                </Badge>
              ))}
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 text-slate-500 text-left">
                <th className="px-2 py-2 font-medium">Clip</th>
                <th className="px-2 py-2 font-medium">Humano</th>
                <th className="px-2 py-2 font-medium">Motivo</th>
                <th className="px-2 py-2 font-medium">Juez</th>
                <th className="px-2 py-2 font-medium">Fecha</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.slice(0, 20).map((r) => {
                const judgeAvg = r.score_judge
                  ? (r.score_judge.hook + r.score_judge.retention + r.score_judge.shareability) / 3
                  : null;
                return (
                  <tr key={r.content_result_id} className="border-b border-white/5">
                    <td className="px-2 py-2 font-mono text-slate-500">
                      {r.content_result_id.slice(0, 8)}
                    </td>
                    <td className="px-2 py-2">
                      {r.posteable ? (
                        <span className="inline-flex items-center gap-1 text-emerald-400">
                          <ThumbsUp className="w-3 h-3" /> Sí
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-red-400">
                          <ThumbsDown className="w-3 h-3" /> No
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-slate-400">
                      {r.motivo ? MOTIVO_LABELS[r.motivo] || r.motivo : "—"}
                    </td>
                    <td className="px-2 py-2 font-mono text-purple-300">
                      {judgeAvg !== null ? judgeAvg.toFixed(1) : "—"}
                    </td>
                    <td className="px-2 py-2 text-slate-500 whitespace-nowrap">
                      {new Date(r.created_at).toLocaleDateString("es-AR", {
                        day: "numeric",
                        month: "short",
                      })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {data.rows.length > 20 && (
            <p className="text-[10px] text-slate-600 px-2 py-2">
              Mostrando 20 de {data.rows.length} clips etiquetados.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
