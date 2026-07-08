"use client";

import { Card, CardContent } from "@/components/ui/card";
import { formatUsd, formatUsdShort, formatPct } from "@/lib/admin-usage";
import type { UsageSummary } from "@/lib/admin-usage";
import {
  DollarSign,
  Briefcase,
  Mic,
  TrendingUp,
  Percent,
  Database,
} from "lucide-react";

interface UsageKpiGridProps {
  summary: UsageSummary;
}

function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
  accent = "purple",
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  accent?: "purple" | "emerald" | "amber" | "slate";
}) {
  const accentMap = {
    purple: "bg-purple-500/10 text-purple-400",
    emerald: "bg-emerald-500/10 text-emerald-400",
    amber: "bg-amber-500/10 text-amber-400",
    slate: "bg-slate-500/10 text-slate-400",
  };

  return (
    <Card className="bg-slate-900/60 border-white/10">
      <CardContent className="pt-5 pb-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] text-slate-500 uppercase tracking-wider font-medium">
              {label}
            </p>
            <p className="text-xl font-bold text-white mt-1 font-mono truncate">
              {value}
            </p>
            {sub && (
              <p className="text-xs text-slate-400 mt-1 leading-snug">{sub}</p>
            )}
          </div>
          <div className={`p-2 rounded-lg shrink-0 ${accentMap[accent]}`}>
            <Icon className="w-4 h-4" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function UsageKpiGrid({ summary }: UsageKpiGridProps) {
  const marginTone = summary.margin_pct > 30 ? "emerald" : summary.margin_pct > 0 ? "amber" : "slate";

  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
      <KpiCard
        label="Costo total"
        value={formatUsd(summary.total_cost_usd)}
        sub={`${summary.completed_jobs} jobs completados`}
        icon={DollarSign}
      />
      <KpiCard
        label="Promedio / job"
        value={formatUsd(summary.avg_cost_per_job)}
        icon={Briefcase}
      />
      <KpiCard
        label="Whisper"
        value={`${summary.whisper_minutes.toFixed(1)} min`}
        sub="audio transcrito"
        icon={Mic}
      />
      <KpiCard
        label="Margen bruto"
        value={formatUsdShort(summary.margin_usd)}
        sub={`Ingreso ${formatUsdShort(summary.revenue_usd)}`}
        icon={TrendingUp}
        accent={marginTone}
      />
      <KpiCard
        label="Margen %"
        value={formatPct(summary.margin_pct)}
        sub={`Costo = ${formatPct(summary.cost_as_pct_of_revenue)} del ingreso`}
        icon={Percent}
        accent={marginTone}
      />
      <KpiCard
        label="Ahorro cache"
        value={formatUsd(summary.total_cost_avoided_usd)}
        sub={`${summary.total_cache_hits} hits en período`}
        icon={Database}
        accent={summary.total_cost_avoided_usd > 0 ? "emerald" : "slate"}
      />
    </div>
  );
}
