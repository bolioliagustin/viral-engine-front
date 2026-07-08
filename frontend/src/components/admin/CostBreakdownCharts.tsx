"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  mapToChartData,
  taskLabel,
  shortModel,
  formatUsd,
  type UsageBreakdown,
} from "@/lib/admin-usage";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";

interface CostBreakdownChartsProps {
  breakdown: UsageBreakdown | null;
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { value: number; payload: { name: string } }[];
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 border border-white/10 rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="text-slate-300">{payload[0].payload.name}</p>
      <p className="text-purple-300 font-mono font-semibold">
        {formatUsd(payload[0].value)}
      </p>
    </div>
  );
}

function HorizontalBarChart({
  data,
  emptyMessage,
}: {
  data: { name: string; value: number; fill: string }[];
  emptyMessage: string;
}) {
  if (!data.length) {
    return (
      <p className="text-sm text-slate-500 py-8 text-center">{emptyMessage}</p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={Math.max(180, data.length * 36)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
        <XAxis
          type="number"
          tick={{ fill: "#64748b", fontSize: 11 }}
          tickFormatter={(v) => `$${v.toFixed(3)}`}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={130}
          tick={{ fill: "#94a3b8", fontSize: 11 }}
        />
        <Tooltip content={<ChartTooltip />} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function CostBreakdownCharts({ breakdown }: CostBreakdownChartsProps) {
  const byTask = mapToChartData(breakdown?.by_task || {}, taskLabel);
  const byModel = mapToChartData(breakdown?.by_model || {}, shortModel);
  const byProvider = mapToChartData(breakdown?.by_provider || {}, (k) => k);

  return (
    <Card className="bg-slate-900/60 border-white/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Desglose de costos</CardTitle>
        <p className="text-xs text-slate-500">
          {breakdown?.event_count ?? 0} eventos en el período
        </p>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="task">
          <TabsList className="bg-slate-800/80 border border-white/5 mb-4">
            <TabsTrigger value="task" className="text-xs data-[state=active]:bg-purple-600">
              Por tarea
            </TabsTrigger>
            <TabsTrigger value="model" className="text-xs data-[state=active]:bg-purple-600">
              Por modelo
            </TabsTrigger>
            <TabsTrigger value="provider" className="text-xs data-[state=active]:bg-purple-600">
              Por proveedor
            </TabsTrigger>
          </TabsList>

          <TabsContent value="task">
            <HorizontalBarChart data={byTask} emptyMessage="Sin eventos por tarea." />
          </TabsContent>

          <TabsContent value="model">
            <HorizontalBarChart data={byModel} emptyMessage="Sin eventos por modelo." />
          </TabsContent>

          <TabsContent value="provider">
            {byProvider.length === 0 ? (
              <p className="text-sm text-slate-500 py-8 text-center">Sin eventos por proveedor.</p>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie
                    data={byProvider}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={2}
                  >
                    {byProvider.map((entry, i) => (
                      <Cell key={entry.key} fill={entry.fill} />
                    ))}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} />
                  <Legend
                    formatter={(value) => (
                      <span className="text-slate-400 text-xs">{value}</span>
                    )}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
