"use client";

import { motion, AnimatePresence } from "framer-motion";
import { useState } from "react";
import { ChevronDown, ChevronUp, TrendingUp, Clock, Target, Zap, BarChart3 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Result {
  type: string;
  score_hook: number;
  score_retention: number;
  score_shareability: number;
  moment_index: number;
  time_saved_minutes?: number;
  roi_time_saved?: number;
  pillar_type?: string;
}

interface AnalyticsSummaryProps {
  results: Result[];
}

// ===== RADIAL GAUGE COMPONENT =====
const RadialGauge = ({ 
  value, 
  max = 10, 
  label, 
  icon: Icon,
  color = "purple"
}: { 
  value: number; 
  max?: number; 
  label: string; 
  icon: React.ElementType;
  color?: "purple" | "cyan" | "pink" | "green";
}) => {
  const percentage = (value / max) * 100;
  const radius = 36;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;
  
  const colorClasses = {
    purple: { stroke: "stroke-purple-500", text: "text-purple-400", bg: "bg-purple-500/10" },
    cyan: { stroke: "stroke-cyan-500", text: "text-cyan-400", bg: "bg-cyan-500/10" },
    pink: { stroke: "stroke-pink-500", text: "text-pink-400", bg: "bg-pink-500/10" },
    green: { stroke: "stroke-green-500", text: "text-green-400", bg: "bg-green-500/10" },
  };

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-24 h-24">
        <svg className="w-24 h-24 transform -rotate-90">
          {/* Background circle */}
          <circle
            cx="48"
            cy="48"
            r={radius}
            className="stroke-slate-800"
            strokeWidth="6"
            fill="none"
          />
          {/* Progress circle */}
          <motion.circle
            cx="48"
            cy="48"
            r={radius}
            className={colorClasses[color].stroke}
            strokeWidth="6"
            fill="none"
            strokeLinecap="round"
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset }}
            transition={{ duration: 1, ease: "easeOut", delay: 0.2 }}
            style={{ strokeDasharray: circumference }}
          />
        </svg>
        {/* Center content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`text-2xl font-bold ${colorClasses[color].text}`}>
            {typeof value === 'number' && value % 1 !== 0 ? value.toFixed(1) : value}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-1.5 mt-2">
        <div className={`p-1 rounded ${colorClasses[color].bg}`}>
          <Icon className={`w-3 h-3 ${colorClasses[color].text}`} />
        </div>
        <span className="text-xs text-slate-400 font-medium">{label}</span>
      </div>
    </div>
  );
};

// ===== HORIZONTAL BAR =====
const HorizontalBar = ({ 
  label, 
  value, 
  max = 10,
  color 
}: { 
  label: string; 
  value: number; 
  max?: number;
  color: string;
}) => {
  const percentage = (value / max) * 100;
  
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="text-white font-medium">{value.toFixed(1)}</span>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${color}`}
          initial={{ width: 0 }}
          animate={{ width: `${percentage}%` }}
          transition={{ duration: 0.8, ease: "easeOut", delay: 0.3 }}
        />
      </div>
    </div>
  );
};

// ===== HEATMAP CELL =====
const HeatmapCell = ({ value, label }: { value: number; label: string }) => {
  const getColor = (v: number) => {
    if (v >= 9) return "bg-green-500/80 text-green-100";
    if (v >= 8) return "bg-green-600/60 text-green-100";
    if (v >= 7) return "bg-yellow-500/60 text-yellow-100";
    if (v >= 6) return "bg-orange-500/60 text-orange-100";
    return "bg-red-500/60 text-red-100";
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3 }}
      className={`${getColor(value)} rounded-lg p-2 text-center`}
    >
      <div className="text-lg font-bold">{value.toFixed(1)}</div>
      <div className="text-[10px] opacity-80 uppercase tracking-wide">{label}</div>
    </motion.div>
  );
};

// ===== MAIN COMPONENT =====
export function AnalyticsSummary({ results }: AnalyticsSummaryProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // ===== COMPUTE METRICS =====
  const uniqueMoments = [...new Set(results.map(r => r.moment_index))];
  const clipCount = uniqueMoments.length;

  // Get one result per moment (avoid duplicates from different content types)
  const uniqueResults = uniqueMoments.map(idx => 
    results.find(r => r.moment_index === idx && r.type === "twitter_thread") || 
    results.find(r => r.moment_index === idx)!
  );

  // Average Viral Score
  const avgViralScore = uniqueResults.length > 0
    ? uniqueResults.reduce((sum, r) => 
        sum + ((r.score_hook + r.score_retention + r.score_shareability) / 3), 0
      ) / uniqueResults.length
    : 0;

  // Time Saved — usar roi_time_saved (real field) y deduplicar por moment_index
  // para no sumar el mismo minuto X veces (twitter + tiktok + linkedin del mismo momento).
  const totalTimeSaved = uniqueResults.reduce(
    (sum, r) => sum + (r.roi_time_saved || r.time_saved_minutes || 0),
    0
  );

  // Average ROI
  const avgRoi = uniqueResults.length > 0
    ? uniqueResults.reduce((sum, r) => sum + (r.roi_time_saved || 60), 0) / uniqueResults.length
    : 0;

  // Scores by Platform Type
  const scoresByType = {
    twitter: { hook: 0, retention: 0, shareability: 0, count: 0 },
    linkedin: { hook: 0, retention: 0, shareability: 0, count: 0 },
    script: { hook: 0, retention: 0, shareability: 0, count: 0 },
  };

  results.forEach(r => {
    if (r.type === "twitter_thread") {
      scoresByType.twitter.hook += r.score_hook;
      scoresByType.twitter.retention += r.score_retention;
      scoresByType.twitter.shareability += r.score_shareability;
      scoresByType.twitter.count++;
    } else if (r.type === "linkedin_post") {
      scoresByType.linkedin.hook += r.score_hook;
      scoresByType.linkedin.retention += r.score_retention;
      scoresByType.linkedin.shareability += r.score_shareability;
      scoresByType.linkedin.count++;
    } else if (r.type === "short_video_script") {
      scoresByType.script.hook += r.score_hook;
      scoresByType.script.retention += r.score_retention;
      scoresByType.script.shareability += r.score_shareability;
      scoresByType.script.count++;
    }
  });

  const platformScores = [
    { 
      name: "🐦 Twitter", 
      score: scoresByType.twitter.count > 0 
        ? (scoresByType.twitter.hook + scoresByType.twitter.retention + scoresByType.twitter.shareability) / (3 * scoresByType.twitter.count)
        : 0,
      color: "bg-blue-500"
    },
    { 
      name: "💼 LinkedIn", 
      score: scoresByType.linkedin.count > 0 
        ? (scoresByType.linkedin.hook + scoresByType.linkedin.retention + scoresByType.linkedin.shareability) / (3 * scoresByType.linkedin.count)
        : 0,
      color: "bg-cyan-500"
    },
    { 
      name: "🎬 Script", 
      score: scoresByType.script.count > 0 
        ? (scoresByType.script.hook + scoresByType.script.retention + scoresByType.script.shareability) / (3 * scoresByType.script.count)
        : 0,
      color: "bg-pink-500"
    },
  ];

  // Scores by Pillar
  const scoresByPillar: Record<string, { hook: number; retention: number; shareability: number; count: number }> = {};
  
  uniqueResults.forEach(r => {
    const pillar = r.pillar_type || "unknown";
    if (!scoresByPillar[pillar]) {
      scoresByPillar[pillar] = { hook: 0, retention: 0, shareability: 0, count: 0 };
    }
    scoresByPillar[pillar].hook += r.score_hook;
    scoresByPillar[pillar].retention += r.score_retention;
    scoresByPillar[pillar].shareability += r.score_shareability;
    scoresByPillar[pillar].count++;
  });

  const pillarData = Object.entries(scoresByPillar).map(([pillar, data]) => ({
    pillar: pillar.charAt(0).toUpperCase() + pillar.slice(1),
    hook: data.count > 0 ? data.hook / data.count : 0,
    retention: data.count > 0 ? data.retention / data.count : 0,
    shareability: data.count > 0 ? data.shareability / data.count : 0,
  }));

  return (
    <div className="bg-gradient-to-br from-slate-900/80 to-slate-800/50 border border-slate-700/50 rounded-2xl overflow-hidden">
      {/* Collapsed Header Bar */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-6 py-4 flex items-center justify-between hover:bg-slate-800/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-500/20 rounded-xl">
            <BarChart3 className="w-5 h-5 text-purple-400" />
          </div>
          <div className="text-left">
            <h3 className="text-white font-semibold">Analytics del Video</h3>
            <p className="text-slate-400 text-sm">
              Score: <span className="text-purple-400 font-bold">{avgViralScore.toFixed(1)}</span> • 
              {" "}{totalTimeSaved} min ahorrados • 
              {" "}{clipCount} clips
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500 hidden sm:block">
            {isExpanded ? "Colapsar" : "Ver detalles"}
          </span>
          {isExpanded ? (
            <ChevronUp className="w-5 h-5 text-slate-400" />
          ) : (
            <ChevronDown className="w-5 h-5 text-slate-400" />
          )}
        </div>
      </button>

      {/* Expanded Content */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="px-6 pb-6 pt-2 border-t border-slate-700/50">
              {/* Row 1: Hero KPI Gauges */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
                <RadialGauge 
                  value={avgViralScore} 
                  max={10} 
                  label="Viral Score" 
                  icon={TrendingUp}
                  color="purple"
                />
                <RadialGauge 
                  value={totalTimeSaved} 
                  max={300} 
                  label="Min Ahorrados" 
                  icon={Clock}
                  color="cyan"
                />
                <RadialGauge 
                  value={avgRoi} 
                  max={100} 
                  label="ROI %" 
                  icon={Target}
                  color="green"
                />
                <RadialGauge 
                  value={clipCount} 
                  max={10} 
                  label="Clips Virales" 
                  icon={Zap}
                  color="pink"
                />
              </div>

              {/* Row 2: Bar Chart + Heatmap */}
              <div className="grid md:grid-cols-2 gap-6">
                {/* Left: Score by Platform */}
                <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800/50">
                  <h4 className="text-sm font-medium text-slate-300 mb-4">Score por Plataforma</h4>
                  <div className="space-y-3">
                    {platformScores.map((platform) => (
                      <HorizontalBar
                        key={platform.name}
                        label={platform.name}
                        value={platform.score}
                        max={10}
                        color={platform.color}
                      />
                    ))}
                  </div>
                </div>

                {/* Right: Heatmap by Pillar */}
                <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800/50">
                  <h4 className="text-sm font-medium text-slate-300 mb-4">Heatmap por Pilar</h4>
                  {pillarData.length > 0 ? (
                    <div className="space-y-2">
                      {/* Header */}
                      <div className="grid grid-cols-4 gap-2 text-[10px] text-slate-500 uppercase tracking-wide">
                        <div></div>
                        <div className="text-center">Hook</div>
                        <div className="text-center">Ret.</div>
                        <div className="text-center">Share</div>
                      </div>
                      {/* Rows */}
                      {pillarData.map((row) => (
                        <div key={row.pillar} className="grid grid-cols-4 gap-2 items-center">
                          <div className="text-xs text-slate-400 font-medium truncate">{row.pillar}</div>
                          <HeatmapCell value={row.hook} label="" />
                          <HeatmapCell value={row.retention} label="" />
                          <HeatmapCell value={row.shareability} label="" />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-slate-500 text-sm">Sin datos de pilar disponibles</p>
                  )}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
