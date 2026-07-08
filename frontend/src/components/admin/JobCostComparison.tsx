"use client";

import { formatUsd } from "@/lib/admin-usage";
import type { JobUsageDetail } from "@/lib/admin-usage";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

interface JobCostComparisonProps {
  comparison: JobUsageDetail["comparison"];
}

const COMPARISON_COLORS = ["#a855f7", "#6366f1", "#64748b", "#14b8a6"];

export function JobCostComparison({ comparison }: JobCostComparisonProps) {
  const data = [
    {
      name: "Costo real",
      value: comparison.actual_cost_usd,
      fill: COMPARISON_COLORS[0],
    },
    {
      name: `Promedio (${comparison.benchmark_sample_size} jobs)`,
      value: comparison.benchmark_avg_cost_usd,
      fill: COMPARISON_COLORS[1],
    },
    {
      name: "Docs jul 2026",
      value: comparison.legacy_docs_config_usd,
      fill: COMPARISON_COLORS[2],
    },
    {
      name: "Canvas legacy",
      value: comparison.legacy_happy_path_usd,
      fill: COMPARISON_COLORS[3],
    },
  ];

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/60 p-5 space-y-4">
      <div>
        <p className="text-sm font-medium text-white">Real vs referencias</p>
        <p className="text-xs text-slate-500 mt-0.5">
          Comparación del costo medido contra benchmarks y estimaciones históricas
        </p>
      </div>

      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="name"
            tick={{ fill: "#94a3b8", fontSize: 10 }}
            interval={0}
            angle={-15}
            textAnchor="end"
            height={50}
          />
          <YAxis
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickFormatter={(v) => `$${v.toFixed(3)}`}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-xs">
                  <p className="text-slate-300">{payload[0].payload.name}</p>
                  <p className="text-purple-300 font-mono font-semibold">
                    {formatUsd(payload[0].value as number)}
                  </p>
                </div>
              );
            }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={entry.name} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      <div className="grid grid-cols-3 gap-3 text-xs">
        <div className="rounded-lg bg-slate-800/50 px-3 py-2">
          <p className="text-slate-500">Δ vs promedio</p>
          <p
            className={`font-mono font-semibold mt-0.5 ${
              comparison.delta_vs_benchmark <= 0 ? "text-emerald-400" : "text-amber-400"
            }`}
          >
            {comparison.delta_vs_benchmark >= 0 ? "+" : ""}
            {formatUsd(comparison.delta_vs_benchmark)}
          </p>
        </div>
        <div className="rounded-lg bg-slate-800/50 px-3 py-2">
          <p className="text-slate-500">Δ vs docs</p>
          <p className="font-mono font-semibold mt-0.5 text-slate-300">
            {comparison.delta_vs_docs >= 0 ? "+" : ""}
            {formatUsd(comparison.delta_vs_docs)}
          </p>
        </div>
        <div className="rounded-lg bg-slate-800/50 px-3 py-2">
          <p className="text-slate-500">Δ vs canvas legacy</p>
          <p className="font-mono font-semibold mt-0.5 text-slate-300">
            {comparison.delta_vs_happy >= 0 ? "+" : ""}
            {formatUsd(comparison.delta_vs_happy)}
          </p>
        </div>
      </div>
    </div>
  );
}
