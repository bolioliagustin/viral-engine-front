"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ViralMomentCard } from "@/components/ViralMomentCard";
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

            {/* List of Viral Moments */}
            <div className="space-y-8">
              {Object.entries(moments)
                .sort(([a], [b]) => Number(a) - Number(b))
                .map(([momentIndex, results], index) => {
                  const firstResult = results[0];
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

                  return (
                    <motion.div
                      key={momentIndex}
                      initial={{ opacity: 0, y: 30 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.15, duration: 0.5 }}
                    >
                      <ViralMomentCard
                        momentIndex={Number(momentIndex)}
                        contentResultId={firstResult.id}
                        hook={firstResult.hook}
                        clipUrl={firstResult.clip_url}
                        startTime={firstResult.start_time}
                        endTime={firstResult.end_time}
                        scores={{
                          hook: firstResult.score_hook,
                          retention: firstResult.score_retention,
                          shareability: firstResult.score_shareability,
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
                      />
                    </motion.div>
                  );
                })}
            </div>
            
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
