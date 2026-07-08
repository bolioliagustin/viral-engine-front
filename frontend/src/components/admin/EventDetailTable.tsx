"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatUsd,
  shortModel,
  taskLabel,
  type UsageEvent,
} from "@/lib/admin-usage";

interface EventDetailTableProps {
  events: UsageEvent[];
}

export function EventDetailTable({ events }: EventDetailTableProps) {
  if (!events.length) return null;

  return (
    <Card className="bg-slate-900/60 border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Todos los eventos ({events.length})</CardTitle>
        <p className="text-xs text-slate-500">Vista granular para debugging</p>
      </CardHeader>
      <CardContent className="p-0 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-slate-500 text-left text-xs">
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
              <tr key={e.id} className="border-b border-white/5 hover:bg-white/5">
                <td className="px-4 py-2 text-slate-500 whitespace-nowrap text-xs">
                  {new Date(e.created_at).toLocaleTimeString("es-AR")}
                </td>
                <td className="px-4 py-2">
                  <span className="text-slate-200 text-xs">{taskLabel(e.task)}</span>
                  {e.moment_index != null && (
                    <span className="text-slate-600 ml-1 text-xs">m{e.moment_index}</span>
                  )}
                  {e.cache_hit && (
                    <Badge
                      variant="outline"
                      className="ml-2 text-[9px] border-emerald-500/30 text-emerald-400"
                    >
                      cache
                    </Badge>
                  )}
                </td>
                <td className="px-4 py-2 text-slate-400 font-mono text-xs max-w-[120px] truncate">
                  {shortModel(e.model)}
                </td>
                <td className="px-4 py-2 text-slate-400 font-mono text-xs">
                  {e.task === "whisper"
                    ? "—"
                    : `${e.input_tokens}/${e.output_tokens}${e.reasoning_tokens ? `+${e.reasoning_tokens}r` : ""}`}
                </td>
                <td className="px-4 py-2 text-slate-400 font-mono text-xs">
                  {e.audio_seconds != null
                    ? `${Number(e.audio_seconds).toFixed(1)}s`
                    : "—"}
                </td>
                <td className="px-4 py-2 text-purple-300 font-mono text-xs">
                  {formatUsd(Number(e.estimated_cost_usd), 6)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
