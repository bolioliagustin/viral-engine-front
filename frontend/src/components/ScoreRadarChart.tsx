"use client";

import { motion } from "framer-motion";

interface ScoreRadarChartProps {
  hook: number;
  retention: number;
  shareability: number;
  size?: number;
}

export function ScoreRadarChart({ hook, retention, shareability, size = 200 }: ScoreRadarChartProps) {
  // Convert scores (0-10) to percentages (0-100) for radius calculation
  const maxRadius = size / 2 - 20;
  const centerX = size / 2;
  const centerY = size / 2;

  // Calculate points for the three metrics (triangle)
  const angles = [
    -90, // Top (Hook)
    30,  // Bottom right (Retention)
    150, // Bottom left (Shareability)
  ];

  const getPoint = (angle: number, value: number) => {
    const radius = (value / 10) * maxRadius;
    const rad = (angle * Math.PI) / 180;
    return {
      x: centerX + radius * Math.cos(rad),
      y: centerY + radius * Math.sin(rad),
    };
  };

  // Background grid circles
  const gridLevels = [2, 4, 6, 8, 10];
  
  // Data points
  const hookPoint = getPoint(angles[0], hook);
  const retentionPoint = getPoint(angles[1], retention);
  const shareabilityPoint = getPoint(angles[2], shareability);

  // Create path for filled area
  const dataPath = `M ${hookPoint.x},${hookPoint.y} L ${retentionPoint.x},${retentionPoint.y} L ${shareabilityPoint.x},${shareabilityPoint.y} Z`;

  // Labels
  const labels = [
    { text: "🎣 Gancho", angle: angles[0], value: hook },
    { text: "⏳ Retención", angle: angles[1], value: retention },
    { text: "🚀 Viralidad", angle: angles[2], value: shareability },
  ];

  return (
    <div className="flex items-center justify-center">
      <svg width={size} height={size} className="overflow-visible">
        <defs>
          <radialGradient id="scoreGradient" cx="50%" cy="50%">
            <stop offset="0%" stopColor="#a855f7" stopOpacity="0.6" />
            <stop offset="50%" stopColor="#ec4899" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#06b6d4" stopOpacity="0.2" />
          </radialGradient>
        </defs>

        {/* Background grid circles */}
        {gridLevels.map((level) => (
          <circle
            key={level}
            cx={centerX}
            cy={centerY}
            r={(level / 10) * maxRadius}
            fill="none"
            stroke="rgba(148, 163, 184, 0.2)"
            strokeWidth="1"
            strokeDasharray={level === 10 ? "0" : "4 2"}
          />
        ))}

        {/* Grid lines to each metric */}
        {angles.map((angle, i) => {
          const endPoint = getPoint(angle, 10);
          return (
            <line
              key={i}
              x1={centerX}
              y1={centerY}
              x2={endPoint.x}
              y2={endPoint.y}
              stroke="rgba(148, 163, 184, 0.2)"
              strokeWidth="1"
            />
          );
        })}

        {/* Filled data area */}
        <motion.path
          d={dataPath}
          fill="url(#scoreGradient)"
          stroke="#a855f7"
          strokeWidth="2"
          initial={{ opacity: 0, scale: 0 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
        />

        {/* Data points */}
        {[hookPoint, retentionPoint, shareabilityPoint].map((point, i) => (
          <motion.circle
            key={i}
            cx={point.x}
            cy={point.y}
            r="6"
            fill="#a855f7"
            stroke="white"
            strokeWidth="2"
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ delay: 0.3 + i * 0.1, type: "spring" }}
          />
        ))}

        {/* Labels with values */}
        {labels.map((label, i) => {
          const labelPoint = getPoint(label.angle, 11.5);
          return (
            <g key={i}>
              <text
                x={labelPoint.x}
                y={labelPoint.y}
                textAnchor="middle"
                className="text-xs font-medium fill-slate-300"
              >
                {label.text}
              </text>
              <text
                x={labelPoint.x}
                y={labelPoint.y + 14}
                textAnchor="middle"
                className="text-sm font-bold fill-white"
              >
                {label.value}/10
              </text>
            </g>
          );
        })}

        {/* Center point */}
        <circle cx={centerX} cy={centerY} r="3" fill="rgba(148, 163, 184, 0.5)" />
      </svg>
    </div>
  );
}

// Compact version for cards
export function MiniScoreRadar({ hook, retention, shareability }: Omit<ScoreRadarChartProps, 'size'>) {
  return (
    <div className="inline-block">
      <ScoreRadarChart hook={hook} retention={retention} shareability={shareability} size={120} />
    </div>
  );
}
