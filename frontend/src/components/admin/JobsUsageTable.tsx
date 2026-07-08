"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatUsd, formatPct, type JobUsageRow } from "@/lib/admin-usage";
import { ArrowRight, ArrowUpDown } from "lucide-react";

type SortKey = "created_at" | "total_cost_usd" | "margin_pct" | "event_count";

interface JobsUsageTableProps {
  jobs: JobUsageRow[];
}

export function JobsUsageTable({ jobs }: JobsUsageTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = useMemo(() => {
    return [...jobs].sort((a, b) => {
      const av = a[sortKey] as number | string;
      const bv = b[sortKey] as number | string;
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });
  }, [jobs, sortKey, sortAsc]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(false);
    }
  }

  function SortHeader({ label, col }: { label: string; col: SortKey }) {
    return (
      <button
        type="button"
        onClick={() => toggleSort(col)}
        className="inline-flex items-center gap-1 hover:text-slate-300 transition-colors"
      >
        {label}
        <ArrowUpDown className="w-3 h-3 opacity-50" />
      </button>
    );
  }

  if (!jobs.length) {
    return (
      <Card className="bg-slate-900/60 border-white/10">
        <CardContent className="py-12 text-center text-slate-500 text-sm">
          No hay jobs con métricas en este período. Procesá un video y volvé a revisar.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="bg-slate-900/60 border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Jobs recientes</CardTitle>
        <p className="text-xs text-slate-500">{jobs.length} jobs en el período</p>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-slate-500 text-left text-xs">
                <th className="px-5 py-3 font-medium">Título</th>
                <th className="px-3 py-3 font-medium">
                  <SortHeader label="Costo" col="total_cost_usd" />
                </th>
                <th className="px-3 py-3 font-medium hidden sm:table-cell">
                  <SortHeader label="Margen" col="margin_pct" />
                </th>
                <th className="px-3 py-3 font-medium hidden md:table-cell">Tokens</th>
                <th className="px-3 py-3 font-medium hidden lg:table-cell">Whisper</th>
                <th className="px-3 py-3 font-medium hidden lg:table-cell">Cache</th>
                <th className="px-3 py-3 font-medium">
                  <SortHeader label="Fecha" col="created_at" />
                </th>
                <th className="px-3 py-3" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((j) => (
                <tr
                  key={j.id}
                  className="border-b border-white/5 hover:bg-white/5 transition-colors"
                >
                  <td className="px-5 py-3 max-w-[180px]">
                    <p className="truncate text-slate-200 font-medium">
                      {j.video_title || j.id.slice(0, 8)}
                    </p>
                    <p className="text-[10px] text-slate-600 font-mono">{j.id.slice(0, 8)}</p>
                  </td>
                  <td className="px-3 py-3 text-purple-300 font-mono text-xs">
                    {formatUsd(j.total_cost_usd)}
                  </td>
                  <td className="px-3 py-3 hidden sm:table-cell">
                    <span
                      className={`font-mono text-xs ${
                        j.margin_pct > 30
                          ? "text-emerald-400"
                          : j.margin_pct > 0
                            ? "text-amber-400"
                            : "text-red-400"
                      }`}
                    >
                      {formatPct(j.margin_pct)}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-slate-500 font-mono text-xs hidden md:table-cell">
                    {(j.total_input_tokens / 1000).toFixed(1)}k /{" "}
                    {(j.total_output_tokens / 1000).toFixed(1)}k
                  </td>
                  <td className="px-3 py-3 text-slate-500 font-mono text-xs hidden lg:table-cell">
                    {j.whisper_seconds > 0
                      ? `${(j.whisper_seconds / 60).toFixed(1)}m`
                      : "—"}
                  </td>
                  <td className="px-3 py-3 hidden lg:table-cell">
                    {j.cache_hits > 0 ? (
                      <Badge
                        variant="outline"
                        className="text-[10px] border-emerald-500/30 text-emerald-400"
                      >
                        {j.cache_hits}
                      </Badge>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-slate-500 text-xs whitespace-nowrap">
                    {new Date(j.created_at).toLocaleDateString("es-AR", {
                      day: "numeric",
                      month: "short",
                    })}
                  </td>
                  <td className="px-3 py-3">
                    <Link
                      href={`/admin/usage/jobs/${j.id}`}
                      className="text-purple-400 hover:text-purple-300 inline-flex items-center gap-1 text-xs"
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
  );
}
