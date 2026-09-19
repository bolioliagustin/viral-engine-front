"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ViralMomentCard } from "@/components/ViralMomentCard";
import { MomentGalleryCard } from "@/components/MomentGalleryCard";
import { AnalyticsSummary } from "@/components/AnalyticsSummary";
import { ToastProvider } from "@/components/ui/use-toast";
import { ProcessingScreen } from "@/components/ProcessingScreen";
import { ArrowLeft, LayoutDashboard, Sparkles } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { parseWhisperWords, effectiveClipDuration } from "@/types/subtitles";

interface Result {
  id: string;
  type: string;
  content: string;
  clip_url: string;
  start_time: number;
  end_time: number;
  hook: string;
  emotional_trigger: string;
  moment_index: number;
  score_hook: number;
  score_retention: number;
  score_shareability: number;
  score_justifications: string;
  time_saved_minutes?: number;
  roi_time_saved?: number;
  pillar_type?: string;
  sentiment_detected?: string;
  viral_overlay?: string;
  whisper_words?: string | { words: { word: string; start: number; end: number }[] };
  // Fase 4: calidad
  verification_failed?: boolean;
  sub_coverage?: number;
  words_per_sec?: number;
  score_judge?: string | { hook: number; retention: number; shareability: number; reasoning?: string };
  score_llm?: string | { hook: number; retention: number; shareability: number };
  clip_quality_issues?: string | string[];
  clip_generation_error?: string;
  // W10: copy por clip + "Score visible" (docs/PLAN_CALIDAD.md §9 Fase 0)
  title?: string;
  description?: string;
  hashtags?: string[];
  /** Curvado 60-99 por el backend (score-curve.js); null si no hay juez. */
  score_display?: number | null;
  grades?: { hook: string; retention: string; shareability: string } | null;
  // W9-A (docs/adr/0008): galería + HD a pedido.
  /** Preview 480x854 del worker (pendiente — mitad worker de W9). null en todo job de hoy. */
  preview_url?: string | null;
  /** URL del re-render HD ya completado, o null si nunca se pidió. */
  hd_url?: string | null;
  /** 'none' | 'queued' | 'processing' | 'ready' | 'error'. */
  hd_status?: string | null;
}


interface Job {
  id: string;
  videoUrl: string;
  videoTitle: string;
  status: string;
  current_step?: string;
  progress_percentage?: number;
  errorMessage: string | null;
  results: Result[];
}

import { motion, AnimatePresence } from "framer-motion";

export default function ResultsPage() {
  const params = useParams();
  const jobId = params.jobId as string;
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // W9-A (docs/adr/0008): galería. null = grilla; un número = detalle abierto.
  const [selectedMoment, setSelectedMoment] = useState<number | null>(null);
  const [galleryFilter, setGalleryFilter] = useState<"all" | "best">("all");
  const BEST_SCORE_THRESHOLD = 85;

  useEffect(() => {
    const fetchJob = async () => {
      try {
        const response = await apiFetch(`/status/${jobId}`);
        if (!response.ok) throw new Error("Failed to fetch job");
        const data = await response.json();
        setJob(data);
        
        if (data.status === "completed" || data.status === "failed") {
          setLoading(false);
        }
      } catch (err) {
        console.error("Error fetching job:", err);
        setError("Error al cargar el trabajo");
        setLoading(false);
      }
    };

    fetchJob();

    const interval = setInterval(() => {
      if (job?.status === "processing") {
        fetchJob();
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [jobId, job?.status]);

  // Helper to render content based on state
  const renderContent = () => {
    // 1. Initial Loading (before first fetch)
    if (loading && !job) {
      return (
        <motion.div 
          key="loading"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="min-h-screen bg-slate-950 text-white flex items-center justify-center"
        >
          <div className="text-center">
            <div className="animate-spin w-12 h-12 border-4 border-purple-500 border-t-transparent rounded-full mx-auto mb-4" />
            <p className="text-lg text-slate-400">Cargando resultados...</p>
          </div>
        </motion.div>
      );
    }

    // 2. Processing Screen
    if (job?.status === "processing") {
      return (
        <ProcessingScreen 
          key="processing"
          currentStep={job.current_step} 
          progress={job.progress_percentage}
        />
      );
    }

    // 3. Error State
    if (error) {
      return (
        <motion.div 
          key="error"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="min-h-screen bg-slate-950 text-white flex items-center justify-center"
        >
          <div className="text-center bg-slate-900 p-8 rounded-2xl border border-slate-800 shadow-2xl max-w-md">
            <div className="w-16 h-16 bg-red-500/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <span className="text-2xl">❌</span>
            </div>
            <h2 className="text-xl font-bold mb-2">Algo salió mal</h2>
            <p className="text-slate-400 mb-6">{error}</p>
            <Button onClick={() => window.location.href = "/dashboard"} className="bg-slate-800 hover:bg-slate-700 w-full">
              Volver al Dashboard
            </Button>
          </div>
        </motion.div>
      );
    }

    // 4. Results State
    if (job) {
      // Group results logic
      const moments = job.results.reduce((acc, result) => {
        const idx = result.moment_index;
        if (!acc[idx]) acc[idx] = [];
        acc[idx].push(result);
        return acc;
      }, {} as Record<number, Result[]>);

      const momentCount = Object.keys(moments).length;
      const avgScore = job.results.length > 0
        ? (job.results
            .filter(r => r.type === "twitter_thread") // take only one type to avoid duplicates in avg
            .reduce((sum, r) => sum + ((r.score_hook + r.score_retention + r.score_shareability) / 3), 0) / momentCount
          ).toFixed(1)
        : "0";

      return (
        <motion.div
            key="results"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: "easeOut" }}
            className="min-h-screen bg-slate-950 pb-20"
        >
          {/* Sticky Header with Glassmorphism */}
          <div className="sticky top-0 z-50 bg-slate-950/80 backdrop-blur-md border-b border-slate-800 shadow-sm">
            <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 sm:py-4">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 md:gap-4">

                {/* Left: Navigation & Title */}
                <div className="flex items-center gap-3 sm:gap-4 min-w-0">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="shrink-0 text-slate-400 hover:text-white hover:bg-slate-800 rounded-full"
                    onClick={() => window.location.href = "/dashboard"}
                  >
                    <ArrowLeft className="w-5 h-5" />
                  </Button>
                  <div className="min-w-0 flex-1">
                    <h1 className="text-base sm:text-lg font-semibold text-white leading-tight line-clamp-1">
                      {job.videoTitle}
                    </h1>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-[10px] sm:text-xs text-slate-500 font-mono">
                        ID: {job.id.substring(0, 8)}...
                      </span>
                      <Badge variant="outline" className="text-[10px] uppercase tracking-wider bg-green-500/10 text-green-400 border-green-500/20 px-2 py-0">
                        Completado
                      </Badge>
                    </div>
                  </div>
                </div>

                {/* Right: Key Stats */}
                <div className="flex items-center gap-2 sm:gap-3">
                   <div className="flex items-center gap-2 sm:gap-3 bg-slate-900/50 rounded-lg p-1.5 border border-slate-800/50 flex-1 md:flex-none">
                      <div className="px-2 sm:px-3 py-1 border-r border-slate-800 text-center flex-1 md:flex-none">
                         <div className="text-[9px] sm:text-[10px] text-slate-500 uppercase tracking-widest font-bold">Momentos</div>
                         <div className="text-base sm:text-lg font-bold text-white">{momentCount}</div>
                      </div>
                      <div className="px-2 sm:px-3 py-1 text-center flex flex-col items-center flex-1 md:flex-none">
                         <div className="text-[9px] sm:text-[10px] text-slate-500 uppercase tracking-widest font-bold">Score</div>
                         <div className="text-base sm:text-lg font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                            {avgScore}
                         </div>
                      </div>
                   </div>
                   <Button
                     size="sm"
                     className="hidden md:flex bg-white text-slate-950 hover:bg-slate-200 font-semibold shadow-lg shadow-purple-500/10 transition-all"
                     onClick={() => window.location.href = "/dashboard"}
                   >
                     <LayoutDashboard className="w-4 h-4 mr-2" />
                     Dashboard
                   </Button>
                </div>
              </div>
            </div>
          </div>

          {/* Content Container */}
          <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6 sm:space-y-8">
            
            {/* Analytics Summary Dashboard */}
            <AnalyticsSummary results={job.results} />

            {/* W9-A (docs/adr/0008): galería — grilla ordenada por Score
                visible con filtro "todos / mejores", o el detalle de UN
                momento a la vez (reproductor + acciones) cuando se abre una
                tarjeta. Jobs viejos (5 Momentos, sin preview_url) usan el
                mismo camino: la grilla se ve igual, solo con menos tarjetas,
                y el botón de descarga cae directo a clipUrl (sin pedir HD)
                porque ya es el entregable final. */}
            {(() => {
              const momentsArray = Object.entries(moments)
                .sort(([a], [b]) => Number(a) - Number(b))
                .map(([momentIndex, results]) => ({
                  momentIndex: Number(momentIndex),
                  results,
                  firstResult: results[0],
                }));

              const selected =
                selectedMoment !== null
                  ? momentsArray.find((m) => m.momentIndex === selectedMoment)
                  : undefined;

              if (selected) {
                const { firstResult, results, momentIndex } = selected;
                const twitterContent = results.find(r => r.type === "twitter_thread")?.content;
                const tiktokContent = results.find(r => r.type === "tiktok_caption")?.content;
                const linkedinContent = results.find(r => r.type === "linkedin_post")?.content;
                const scriptContent = results.find(r => r.type === "short_video_script")?.content;
                const whisperWords = parseWhisperWords(firstResult.whisper_words);
                const clipDuration = effectiveClipDuration(
                  whisperWords,
                  firstResult.end_time - firstResult.start_time
                );

                let justifications = [];
                try {
                  justifications = firstResult.score_justifications
                    ? JSON.parse(firstResult.score_justifications)
                    : [];
                } catch (e) {
                  console.error("Failed to parse justifications:", e);
                }

                const parseJsonField = <T,>(raw: unknown): T | undefined => {
                  if (!raw) return undefined;
                  if (typeof raw === "object") return raw as T;
                  try {
                    return JSON.parse(raw as string) as T;
                  } catch {
                    return undefined;
                  }
                };

                const scoreJudge = parseJsonField<{
                  hook: number;
                  retention: number;
                  shareability: number;
                  reasoning?: string;
                }>(firstResult.score_judge);
                const scoreLlm = parseJsonField<{
                  hook: number;
                  retention: number;
                  shareability: number;
                }>(firstResult.score_llm);
                const clipQualityIssues = parseJsonField<string[]>(
                  firstResult.clip_quality_issues
                );

                return (
                  <div className="space-y-4">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-slate-400 hover:text-white hover:bg-slate-800"
                      onClick={() => setSelectedMoment(null)}
                    >
                      <ArrowLeft className="w-4 h-4 mr-2" />
                      Volver a la galería
                    </Button>
                    <motion.div
                      key={momentIndex}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.3 }}
                    >
                      <ViralMomentCard
                        momentIndex={momentIndex}
                        contentResultId={firstResult.id}
                        hook={firstResult.hook}
                        clipUrl={firstResult.clip_url}
                        startTime={firstResult.start_time}
                        endTime={firstResult.end_time}
                        scores={{
                          hook: firstResult.score_hook ?? 0,
                          retention: firstResult.score_retention ?? 0,
                          shareability: firstResult.score_shareability ?? 0,
                        }}
                        justifications={justifications}
                        twitterContent={twitterContent}
                        tiktokContent={tiktokContent}
                        linkedinContent={linkedinContent}
                        scriptContent={scriptContent}
                        overlayText={firstResult.viral_overlay}
                        emotionalTrigger={firstResult.emotional_trigger}
                        sentiment={firstResult.sentiment_detected}
                        pillarType={firstResult.pillar_type}
                        roiTimeSaved={firstResult.roi_time_saved}
                        whisperWords={whisperWords}
                        clipDuration={clipDuration}
                        verificationFailed={firstResult.verification_failed === true}
                        subCoverage={firstResult.sub_coverage}
                        clipQualityIssues={clipQualityIssues}
                        clipGenerationError={firstResult.clip_generation_error}
                        scoreJudge={scoreJudge}
                        scoreLlm={scoreLlm}
                        title={firstResult.title}
                        description={firstResult.description}
                        hashtags={firstResult.hashtags}
                        scoreDisplay={firstResult.score_display}
                        grades={firstResult.grades}
                        previewUrl={firstResult.preview_url}
                        hdUrl={firstResult.hd_url}
                        hdStatus={firstResult.hd_status}
                      />
                    </motion.div>
                  </div>
                );
              }

              // Grilla: ordenada por Score visible descendente (los que no
              // tienen juez, al final, en su orden original). "Mejores"
              // filtra >= BEST_SCORE_THRESHOLD; sin score_display, un
              // momento nunca puede ser "mejor" (no hay con qué medirlo),
              // pero siempre aparece en "todos".
              const sorted = [...momentsArray].sort((a, b) => {
                const sa = a.firstResult.score_display;
                const sb = b.firstResult.score_display;
                if (sa === null || sa === undefined) return sb === null || sb === undefined ? 0 : 1;
                if (sb === null || sb === undefined) return -1;
                return sb - sa;
              });
              const galleryItems =
                galleryFilter === "best"
                  ? sorted.filter(
                      (m) => (m.firstResult.score_display ?? 0) >= BEST_SCORE_THRESHOLD
                    )
                  : sorted;

              return (
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant={galleryFilter === "all" ? "default" : "outline"}
                      className={
                        galleryFilter === "all"
                          ? "bg-white text-slate-950 hover:bg-slate-200"
                          : "border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
                      }
                      onClick={() => setGalleryFilter("all")}
                    >
                      Todos ({momentsArray.length})
                    </Button>
                    <Button
                      size="sm"
                      variant={galleryFilter === "best" ? "default" : "outline"}
                      className={
                        galleryFilter === "best"
                          ? "bg-white text-slate-950 hover:bg-slate-200"
                          : "border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
                      }
                      onClick={() => setGalleryFilter("best")}
                    >
                      Mejores (score ≥ {BEST_SCORE_THRESHOLD})
                    </Button>
                  </div>

                  {galleryItems.length === 0 ? (
                    <p className="text-slate-500 text-sm py-8 text-center">
                      Ningún Momento supera el score {BEST_SCORE_THRESHOLD} en este job.
                    </p>
                  ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3 sm:gap-4">
                      {galleryItems.map(({ momentIndex, firstResult }, index) => {
                        const whisperWords = parseWhisperWords(firstResult.whisper_words);
                        const clipDuration = effectiveClipDuration(
                          whisperWords,
                          firstResult.end_time - firstResult.start_time
                        );
                        const globalScoreFallback = (
                          ((firstResult.score_hook ?? 0) +
                            (firstResult.score_retention ?? 0) +
                            (firstResult.score_shareability ?? 0)) /
                          3
                        ).toFixed(1);
                        return (
                          <motion.div
                            key={momentIndex}
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: Math.min(index, 10) * 0.04, duration: 0.35 }}
                          >
                            <MomentGalleryCard
                              momentIndex={momentIndex}
                              title={firstResult.title}
                              hook={firstResult.hook}
                              thumbnailUrl={firstResult.preview_url || firstResult.clip_url}
                              duration={clipDuration}
                              scoreDisplay={firstResult.score_display}
                              grades={firstResult.grades}
                              globalScoreFallback={globalScoreFallback}
                              onOpen={() => setSelectedMoment(momentIndex)}
                            />
                          </motion.div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Footer Area */}
            <div className="text-center pt-8 border-t border-slate-800/50">
               <p className="text-slate-600 text-sm">
                  ViralEngine Beta v1.0 • Generado por AI
               </p>
            </div>

          </div>
        </motion.div>
      );
    }
    
    return null;
  };

  return (
    <ToastProvider>
      <AnimatePresence mode="wait">
        {renderContent()}
      </AnimatePresence>
    </ToastProvider>
  );
}
