"use client";

import { Badge } from "@/components/ui/badge";
import { Play } from "lucide-react";

// Duplicado a propósito (no exportado desde ViralMomentCard.tsx) para no
// acoplar la card chica al componente grande — mismos colores que la vista
// de detalle (GradeChip/GRADE_COLORS).
const GRADE_COLORS: Record<string, string> = {
  A: "text-green-400 border-green-500/40 bg-green-500/10",
  B: "text-blue-400 border-blue-500/40 bg-blue-500/10",
  C: "text-yellow-400 border-yellow-500/40 bg-yellow-500/10",
  D: "text-orange-400 border-orange-500/40 bg-orange-500/10",
};

const fmtDuration = (sec: number) => {
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
};

interface MomentGalleryCardProps {
  momentIndex: number;
  title?: string;
  hook: string;
  thumbnailUrl?: string | null;
  duration: number;
  scoreDisplay?: number | null;
  grades?: { hook: string; retention: string; shareability: string } | null;
  globalScoreFallback: string;
  onOpen: () => void;
}

/**
 * Tarjeta chica de la galería (W9-A, docs/adr/0008): grilla de miniaturas
 * ordenada por Score visible, pensada para aguantar 30+ Momentos sin
 * volverse inmanejable. Al hacer click abre el detalle (ViralMomentCard).
 */
export function MomentGalleryCard({
  momentIndex,
  title,
  hook,
  thumbnailUrl,
  duration,
  scoreDisplay,
  grades,
  globalScoreFallback,
  onOpen,
}: MomentGalleryCardProps) {
  const hasScoreDisplay = scoreDisplay !== undefined && scoreDisplay !== null && Boolean(grades);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="group text-left bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-lg hover:border-purple-500/40 hover:shadow-purple-500/10 transition-all"
    >
      <div className="relative aspect-[9/16] bg-black overflow-hidden">
        {thumbnailUrl ? (
          <video
            src={thumbnailUrl}
            preload="metadata"
            muted
            playsInline
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-600 text-xs">
            Sin preview
          </div>
        )}

        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/0 to-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
          <div className="w-11 h-11 rounded-full bg-white/90 flex items-center justify-center">
            <Play className="w-5 h-5 text-slate-900 ml-0.5" fill="currentColor" />
          </div>
        </div>

        <Badge className="absolute bottom-1.5 right-1.5 bg-black/70 text-white border-0 text-[10px] font-mono px-1.5 py-0">
          {fmtDuration(duration)}
        </Badge>

        <Badge className="absolute top-1.5 left-1.5 bg-purple-950/70 text-purple-300 border border-purple-500/40 text-[10px] px-1.5 py-0 font-bold">
          #{String(momentIndex).padStart(2, "0")}
        </Badge>

        {hasScoreDisplay ? (
          <div className="absolute top-1.5 right-1.5 flex items-center gap-0.5 bg-black/70 rounded-full px-2 py-0.5">
            <span className="text-xs font-black bg-gradient-to-r from-purple-300 to-pink-300 bg-clip-text text-transparent">
              {scoreDisplay}
            </span>
          </div>
        ) : (
          <div className="absolute top-1.5 right-1.5 bg-black/70 rounded-full px-2 py-0.5">
            <span className="text-xs font-bold text-purple-300">{globalScoreFallback}</span>
          </div>
        )}
      </div>

      <div className="p-2.5 space-y-1.5">
        <p className="text-xs font-semibold text-white leading-snug line-clamp-2 min-h-[2rem]">
          {title || hook}
        </p>
        {hasScoreDisplay && grades && (
          <div className="flex items-center gap-1">
            {(["hook", "retention", "shareability"] as const).map((k) => (
              <span
                key={k}
                className={`w-4 h-4 rounded-full border flex items-center justify-center text-[9px] font-bold ${
                  GRADE_COLORS[grades[k]] || GRADE_COLORS.D
                }`}
                title={k}
              >
                {grades[k]}
              </span>
            ))}
          </div>
        )}
      </div>
    </button>
  );
}
