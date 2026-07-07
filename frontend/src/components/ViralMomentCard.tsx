"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { CopyTabs } from "./CopyTabs";
import { EditClipDrawer } from "./EditClipDrawer";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  ChevronDown,
  Download,
  Sparkles,
  Loader2,
  Share2,
  ExternalLink,
  Pencil,
  Clock,
  Heart,
  Target,
  Flame,
} from "lucide-react";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api";
import {
  type WhisperWordsData,
  computeSubtitleCoverage,
  isSubtitleCoverageComplete,
} from "@/types/subtitles";

interface ScoreJustification {
  metric: string;
  score: number;
  reasoning: string;
  improvement_tip?: string | null;
}

interface ViralMomentCardProps {
  momentIndex: number;
  contentResultId: string;
  hook: string;
  clipUrl: string;
  startTime: number;
  endTime: number;
  scores: {
    hook: number;
    retention: number;
    shareability: number;
  };
  justifications?: ScoreJustification[];
  twitterContent?: string;
  tiktokContent?: string;
  linkedinContent?: string;
  scriptContent?: string;
  // Enriched fields
  overlayText?: string;
  emotionalTrigger?: string;
  sentiment?: string;
  pillarType?: string;
  roiTimeSaved?: number;
  whisperWords?: WhisperWordsData | null;
  /** Duración real del MP4 (post-snap); fallback end-start. */
  clipDuration?: number;
  /** Fase 4: first Y last phrase no matchean el audio real del clip. */
  verificationFailed?: boolean;
  /** Fase 4: cobertura de subs 0-1 persistida por el worker. */
  subCoverage?: number;
}

// ─── Pillar config ─────────────────────────────────────────────────────────
const PILLAR_CONFIG: Record<
  string,
  { label: string; color: string; icon: string }
> = {
  authority: {
    label: "Autoridad",
    color: "bg-purple-500/15 text-purple-300 border-purple-500/30",
    icon: "👑",
  },
  utility: {
    label: "Utilidad",
    color: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    icon: "🛠",
  },
  connection: {
    label: "Conexión",
    color: "bg-pink-500/15 text-pink-300 border-pink-500/30",
    icon: "💞",
  },
};

// ─── Score ring ────────────────────────────────────────────────────────────
const ScoreRing = ({ score, label }: { score: number; label: string }) => {
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const progress = (score / 10) * circumference;
  const color =
    score >= 8
      ? "text-green-500"
      : score >= 6
      ? "text-yellow-500"
      : "text-orange-500";

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-12 h-12 flex items-center justify-center">
        <svg className="transform -rotate-90 w-12 h-12">
          <circle
            cx="24"
            cy="24"
            r={radius}
            stroke="currentColor"
            strokeWidth="4"
            fill="transparent"
            className="text-slate-800"
          />
          <circle
            cx="24"
            cy="24"
            r={radius}
            stroke="currentColor"
            strokeWidth="4"
            fill="transparent"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - progress}
            className={`${color} transition-all duration-1000 ease-out`}
            strokeLinecap="round"
          />
        </svg>
        <span className={`absolute text-sm font-bold ${color}`}>{score}</span>
      </div>
      <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">
        {label}
      </span>
    </div>
  );
};

// ─── Chip ──────────────────────────────────────────────────────────────────
const Chip = ({
  icon,
  label,
  value,
  color = "slate",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color?: string;
}) => (
  <div
    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-900/60 border border-slate-800 text-xs`}
  >
    <span className="text-slate-500">{icon}</span>
    <span className="text-slate-500 font-medium uppercase tracking-wider text-[10px]">
      {label}
    </span>
    <span className="text-slate-200 font-semibold">{value}</span>
  </div>
);

// ─── Format time mm:ss ────────────────────────────────────────────────────
const fmt = (sec: number) => {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
};

// ═══════════════════════════════════════════════════════════════════════════
export function ViralMomentCard({
  momentIndex,
  contentResultId,
  hook,
  clipUrl,
  startTime,
  endTime,
  scores,
  justifications = [],
  twitterContent,
  tiktokContent,
  linkedinContent,
  scriptContent,
  overlayText,
  emotionalTrigger,
  sentiment,
  pillarType,
  roiTimeSaved,
  whisperWords,
  clipDuration: clipDurationProp,
  verificationFailed,
  subCoverage,
}: ViralMomentCardProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  // Si hay un re-render completado, este reemplaza al clipUrl original.
  const [renderedOverride, setRenderedOverride] = useState<string | null>(null);
  const { toast } = useToast();

  // Al montar, ver si ya existe un re-render completado para este clip
  // (persistente entre recargas de página).
  useEffect(() => {
    if (!contentResultId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(`/api/clips/${contentResultId}/edit`);
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        const edit = data.edit;
        if (edit?.status === "completed" && edit.rendered_clip_url) {
          setRenderedOverride(edit.rendered_clip_url);
        }
      } catch {
        // silencio - no es crítico
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [contentResultId]);

  const effectiveClipUrl = renderedOverride || clipUrl;

  const globalScore = (
    (scores.hook + scores.retention + scores.shareability) /
    3
  ).toFixed(1);
  const duration = clipDurationProp ?? endTime - startTime;
  const pillar = pillarType ? PILLAR_CONFIG[pillarType.toLowerCase()] : null;
  const subtitleCoverage = computeSubtitleCoverage(whisperWords ?? null, duration);
  const subsComplete = isSubtitleCoverageComplete(subtitleCoverage);
  const improvementTips = justifications
    .map((j) => j.improvement_tip)
    .filter((tip): tip is string => Boolean(tip?.trim()));

  const isYouTubeUrl = Boolean(
    effectiveClipUrl &&
      (effectiveClipUrl.includes("youtube.com") ||
        effectiveClipUrl.includes("youtu.be"))
  );

  const getYouTubeEmbedUrl = (url: string): string | null => {
    const videoIdMatch = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
    const timeMatch = url.match(/[?&]t=(\d+)s?/);
    const videoId = videoIdMatch?.[1];
    const time = timeMatch?.[1] ?? "0";
    if (!videoId) return null;
    return `https://www.youtube.com/embed/${videoId}?start=${time}&rel=0`;
  };

  const handleDownload = async () => {
    try {
      setIsDownloading(true);
      const response = await fetch(effectiveClipUrl!);
      if (!response.ok) throw new Error("Network response was not ok");

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.style.display = "none";
      a.href = url;
      a.download = `clip_${momentIndex}_${hook
        .substring(0, 20)
        .replace(/\s+/g, "_")}.mp4`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);

      toast({
        title: "✅ Descarga completada",
        description: "El clip se ha guardado en tu dispositivo.",
      });
    } catch (error) {
      console.error("Download failed:", error);
      toast({
        title: "❌ Error en descarga",
        description: "No se pudo descargar el video. Intenta nuevamente.",
        variant: "destructive",
      });
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <>
      <Card className="bg-slate-900 border-slate-700 overflow-hidden shadow-xl transition-all hover:border-purple-500/30">
        {/* ════════════════════ PREMIUM HEADER ════════════════════ */}
        <CardHeader className="bg-gradient-to-b from-slate-950 to-slate-900 border-b border-slate-800 p-4 sm:p-5">
          <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
            <div className="flex-1 min-w-0 space-y-3">
              {/* Index + metadata row */}
              <div className="flex items-center gap-2 flex-wrap">
                <Badge className="bg-purple-950/40 text-purple-300 border border-purple-500/40 text-[11px] px-2.5 py-1 rounded-full font-bold">
                  #{String(momentIndex).padStart(2, "0")}
                </Badge>
                <span className="text-slate-500 text-xs font-mono">
                  {fmt(startTime)} → {fmt(endTime)}
                </span>
                <span className="text-slate-700">•</span>
                <span className="text-slate-500 text-xs font-mono">
                  {duration}s
                </span>

                {pillar && (
                  <Badge
                    className={`${pillar.color} text-[10px] px-2 py-0.5 font-semibold border`}
                  >
                    <span className="mr-1">{pillar.icon}</span>
                    {pillar.label}
                  </Badge>
                )}
                {subtitleCoverage !== null && (
                  <Badge
                    className={
                      subsComplete
                        ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30 text-[10px] px-2 py-0.5 font-semibold border"
                        : "bg-amber-500/15 text-amber-300 border-amber-500/30 text-[10px] px-2 py-0.5 font-semibold border"
                    }
                  >
                    {subsComplete ? "Subs completos" : "Subs parciales"}
                  </Badge>
                )}
                {verificationFailed && (
                  <Badge
                    className="bg-red-500/15 text-red-300 border-red-500/30 text-[10px] px-2 py-0.5 font-semibold border"
                    title="El audio del clip no coincide con el momento detectado por la IA — revisá el corte antes de publicar"
                  >
                    ⚠ Verificar corte
                  </Badge>
                )}
                {subCoverage !== undefined && subCoverage !== null && subCoverage < 0.85 && (
                  <Badge
                    className="bg-amber-500/15 text-amber-300 border-amber-500/30 text-[10px] px-2 py-0.5 font-semibold border"
                    title={`Los subtítulos cubren el ${Math.round(subCoverage * 100)}% del clip`}
                  >
                    Subs {Math.round(subCoverage * 100)}%
                  </Badge>
                )}
              </div>

              {/* Hook */}
              <h3 className="text-lg md:text-xl font-bold text-white leading-snug">
                &ldquo;{hook}&rdquo;
              </h3>

              {/* Chips row: trigger, sentiment, roi */}
              <div className="flex flex-wrap items-center gap-2">
                {emotionalTrigger && (
                  <Chip
                    icon={<Heart className="w-3 h-3" />}
                    label="Trigger"
                    value={emotionalTrigger}
                  />
                )}
                {sentiment && (
                  <Chip
                    icon={<Flame className="w-3 h-3" />}
                    label="Tono"
                    value={sentiment}
                  />
                )}
                {roiTimeSaved && roiTimeSaved > 0 && (
                  <Chip
                    icon={<Clock className="w-3 h-3" />}
                    label="ROI"
                    value={`${roiTimeSaved} min ahorrados`}
                  />
                )}
              </div>
            </div>

            {/* Score block — wrap en mobile para no overflow */}
            <div className="flex flex-wrap items-stretch gap-2 sm:gap-3">
              <div className="flex flex-col items-center justify-center px-3 sm:px-4 py-2 rounded-xl bg-gradient-to-br from-purple-600/20 to-pink-600/20 border border-purple-500/30">
                <div className="text-[9px] uppercase tracking-widest text-purple-300 font-bold">
                  Viral Score
                </div>
                <div className="text-2xl sm:text-3xl font-black bg-gradient-to-r from-purple-300 to-pink-300 bg-clip-text text-transparent leading-none mt-0.5">
                  {globalScore}
                </div>
                <div className="text-[9px] text-slate-500 mt-0.5">/ 10</div>
              </div>
              <div className="flex items-center gap-2 sm:gap-3 bg-slate-900/70 p-2 sm:p-2.5 rounded-xl border border-slate-800">
                <ScoreRing score={scores.hook} label="Gancho" />
                <ScoreRing score={scores.retention} label="Reten." />
                <ScoreRing score={scores.shareability} label="Viral." />
              </div>
            </div>
          </div>
        </CardHeader>

        {/* ════════════════════ BODY ════════════════════ */}
        <CardContent className="p-0">
          <div className="grid lg:grid-cols-[300px_1fr] divide-y lg:divide-y-0 lg:divide-x divide-slate-800">
            {/* LEFT: sticky video + actions */}
            <div className="p-4 sm:p-5 bg-slate-925 flex flex-col gap-3">
              <div className="relative rounded-xl overflow-hidden bg-black shadow-lg border border-slate-800 aspect-[9/16]">
                {isYouTubeUrl ? (
                  <iframe
                    src={getYouTubeEmbedUrl(effectiveClipUrl!) ?? undefined}
                    className="w-full h-full"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowFullScreen
                    title={`Clip momento ${momentIndex}`}
                  />
                ) : effectiveClipUrl ? (
                  <video
                    key={effectiveClipUrl}
                    src={effectiveClipUrl}
                    controls
                    playsInline
                    className="w-full h-full object-contain"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-slate-500 text-sm">
                    Sin clip disponible
                  </div>
                )}

                {/* Overlay preview hint */}
                {overlayText && !isYouTubeUrl && (
                  <div className="absolute top-2 left-2 right-2 pointer-events-none">
                    <div className="bg-black/60 backdrop-blur-sm rounded px-2 py-1 border border-white/10">
                      <div className="text-[9px] text-pink-300 uppercase tracking-wider">
                        Título en el video
                      </div>
                      <div className="text-white font-bold text-xs leading-tight line-clamp-1">
                        {overlayText}
                      </div>
                    </div>
                  </div>
                )}

                {/* Re-render badge: clip ya fue editado */}
                {renderedOverride && (
                  <div className="absolute bottom-2 right-2 pointer-events-none">
                    <Badge className="bg-emerald-500/90 text-white border-0 text-[10px] uppercase tracking-wider shadow-lg">
                      <Sparkles className="w-3 h-3 mr-1" />
                      Editado
                    </Badge>
                  </div>
                )}
              </div>

              {/* Action buttons */}
              <div className="grid grid-cols-1 gap-2">
                {isYouTubeUrl ? (
                  <Button
                    variant="outline"
                    className="w-full border-slate-700 bg-slate-800 text-slate-200 hover:bg-purple-600 hover:text-white hover:border-purple-500 transition-all"
                    onClick={() => window.open(effectiveClipUrl, "_blank")}
                  >
                    <ExternalLink className="mr-2 h-4 w-4" />
                    Ver en YouTube
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    className="w-full border-slate-700 bg-slate-800 text-slate-200 hover:bg-purple-600 hover:text-white hover:border-purple-500 transition-all"
                    onClick={handleDownload}
                    disabled={isDownloading || !effectiveClipUrl}
                  >
                    {isDownloading ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Download className="mr-2 h-4 w-4" />
                    )}
                    {isDownloading ? "Descargando..." : "Descargar MP4"}
                  </Button>
                )}

                <div className="grid grid-cols-2 gap-2">
                  <Button
                    variant="outline"
                    className="w-full border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800 transition-all"
                    onClick={() => setEditOpen(true)}
                    disabled={isYouTubeUrl || !clipUrl}
                    title={
                      isYouTubeUrl
                        ? "Editor solo disponible para clips descargados"
                        : "Editar título, estilo y posición de subtítulos"
                    }
                  >
                    <Pencil className="mr-1.5 h-3.5 w-3.5" />
                    <span className="text-xs">Editar</span>
                  </Button>
                  <Button
                    variant="outline"
                    className="w-full border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
                    onClick={async () => {
                      const shareUrl = effectiveClipUrl;
                      if (!shareUrl) return;
                      const shareData = {
                        title: hook ? `"${hook}"` : `Clip viral #${momentIndex}`,
                        text: hook
                          ? `Mirá este momento viral: "${hook}"`
                          : `Clip generado con IA`,
                        url: shareUrl,
                      };
                      try {
                        // Web Share API (mobile + supported browsers)
                        if (
                          typeof navigator !== "undefined" &&
                          typeof navigator.share === "function"
                        ) {
                          await navigator.share(shareData);
                          return;
                        }
                        // Fallback: copy URL to clipboard
                        await navigator.clipboard.writeText(shareUrl);
                        toast({
                          title: "🔗 Link copiado",
                          description: "El URL del clip está en tu portapapeles.",
                        });
                      } catch (e: unknown) {
                        // User cancelled share — silencio
                        if (e instanceof Error && e.name === "AbortError") return;
                        toast({
                          title: "❌ No se pudo compartir",
                          description: e instanceof Error ? e.message : "Error",
                          variant: "destructive",
                        });
                      }
                    }}
                    disabled={!effectiveClipUrl || isYouTubeUrl}
                    title={
                      isYouTubeUrl
                        ? "Compartir solo disponible para clips MP4"
                        : "Compartir clip o copiar link"
                    }
                  >
                    <Share2 className="mr-1.5 h-3.5 w-3.5" />
                    <span className="text-xs">Compartir</span>
                  </Button>
                </div>
              </div>
            </div>

            {/* RIGHT: content tabs */}
            <div className="bg-slate-900 flex flex-col">
              <div className="p-3 border-b border-slate-800 bg-slate-950/40 shrink-0">
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
                  <Target className="w-3.5 h-3.5 text-purple-400" />
                  Contenido generado por IA
                </h4>
              </div>
              <div className="flex-1 min-h-[400px]">
                <CopyTabs
                  twitterContent={twitterContent}
                  tiktokContent={tiktokContent}
                  linkedinContent={linkedinContent}
                  scriptContent={scriptContent}
                  overlayText={overlayText}
                />
              </div>
            </div>
          </div>

          {/* ════════════════════ SUGERENCIAS ════════════════════ */}
          {improvementTips.length > 0 && (
            <div className="border-t border-slate-800 bg-green-950/10 px-4 sm:px-5 py-4">
              <h4 className="text-xs font-semibold text-green-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5" />
                Sugerencias
              </h4>
              <ul className="space-y-1.5">
                {improvementTips.map((tip, i) => (
                  <li
                    key={i}
                    className="text-xs text-green-300/90 flex gap-2 leading-relaxed"
                  >
                    <span className="shrink-0">💡</span>
                    <span>{tip}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* ════════════════════ COLLAPSIBLE AI ANALYSIS ════════════════════ */}
          {justifications.length > 0 && (
            <div className="border-t border-slate-800 bg-slate-950/30">
              <Collapsible open={isOpen} onOpenChange={setIsOpen} className="w-full">
                <CollapsibleTrigger asChild>
                  <Button
                    variant="ghost"
                    className="w-full justify-between items-center p-4 h-auto hover:bg-slate-900/50 group text-purple-400 hover:text-purple-300 rounded-none"
                  >
                    <span className="flex items-center gap-2 text-sm font-medium">
                      <Sparkles className="w-4 h-4" />
                      Análisis de viralidad con IA
                      <span className="text-slate-500 text-xs font-normal ml-2 hidden sm:inline-block">
                        • Descubre por qué este clip funciona
                      </span>
                    </span>
                    <ChevronDown
                      className={`w-5 h-5 transition-transform duration-300 text-slate-500 group-hover:text-purple-400 ${
                        isOpen ? "rotate-180" : ""
                      }`}
                    />
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <motion.div
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="p-4 sm:p-5 pt-2 grid md:grid-cols-3 gap-3 sm:gap-4 bg-slate-900/20"
                  >
                    {justifications.map((just, idx) => (
                      <div
                        key={idx}
                        className="bg-slate-900 rounded-lg p-4 border border-slate-800 hover:border-purple-500/20 transition-colors"
                      >
                        <div className="flex items-center justify-between mb-3">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-purple-300 px-2 py-0.5 bg-purple-500/10 rounded-full ring-1 ring-purple-500/20">
                            {just.metric}
                          </span>
                          <span
                            className={`text-sm font-bold ${
                              just.score >= 8
                                ? "text-green-400"
                                : just.score >= 6
                                ? "text-yellow-400"
                                : "text-orange-400"
                            }`}
                          >
                            {just.score}/10
                          </span>
                        </div>
                        <p className="text-slate-300 text-xs leading-relaxed mb-3 min-h-[3rem]">
                          {just.reasoning}
                        </p>
                        {just.improvement_tip && (
                          <div className="flex gap-2 text-[11px] text-green-400/90 bg-green-950/10 p-2 rounded border border-green-500/10">
                            <span className="shrink-0">💡</span>
                            <span>{just.improvement_tip}</span>
                          </div>
                        )}
                      </div>
                    ))}
                  </motion.div>
                </CollapsibleContent>
              </Collapsible>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ════════════════════ EDIT DRAWER (skeleton) ════════════════════ */}
      <EditClipDrawer
        open={editOpen}
        onOpenChange={setEditOpen}
        momentIndex={momentIndex}
        contentResultId={contentResultId}
        clipUrl={effectiveClipUrl}
        overlayText={overlayText}
        clipDuration={duration}
        whisperWords={whisperWords?.words}
        onRendered={(url) => {
          setRenderedOverride(url);
          toast({
            title: "✨ Clip actualizado",
            description: "Reemplazamos el original con tu versión editada.",
          });
        }}
      />
    </>
  );
}
