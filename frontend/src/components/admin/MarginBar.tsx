"use client";

import { formatUsd } from "@/lib/admin-usage";
import type { UsageSummary } from "@/lib/admin-usage";

interface MarginBarProps {
  summary: UsageSummary;
}

export function MarginBar({ summary }: MarginBarProps) {
  const revenue = summary.revenue_usd || summary.credit_price_usd;
  const cost = summary.total_cost_usd;
  const margin = Math.max(0, revenue - cost);
  const costPct = revenue > 0 ? (cost / revenue) * 100 : 0;
  const marginPct = revenue > 0 ? (margin / revenue) * 100 : 0;

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/60 p-5 space-y-3">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-white">Ingreso vs costo variable</p>
          <p className="text-xs text-slate-500 mt-0.5">
            Período seleccionado · {summary.completed_jobs} créditos consumidos
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-slate-500">Margen bruto</p>
          <p className="text-lg font-bold font-mono text-emerald-400">
            {formatUsd(summary.margin_usd, 2)}
          </p>
        </div>
      </div>

      <div className="h-4 rounded-full overflow-hidden flex bg-slate-800">
        <div
          className="h-full bg-gradient-to-r from-orange-500 to-amber-500 transition-all duration-500"
          style={{ width: `${Math.min(costPct, 100)}%` }}
          title={`Costo: ${formatUsd(cost)}`}
        />
        <div
          className="h-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all duration-500"
          style={{ width: `${Math.min(marginPct, 100)}%` }}
          title={`Margen: ${formatUsd(margin, 2)}`}
        />
      </div>

      <div className="flex justify-between text-xs">
        <span className="text-amber-400 font-mono">
          Costo {formatUsd(cost)} ({costPct.toFixed(1)}%)
        </span>
        <span className="text-emerald-400 font-mono">
          Margen {formatUsd(margin, 2)} ({marginPct.toFixed(1)}%)
        </span>
        <span className="text-slate-400 font-mono">
          Ingreso {formatUsd(revenue, 2)}
        </span>
      </div>
    </div>
  );
}
