"use client";

import { Suspense, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import { submitVideo, redirectToCheckout } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";
import { Sparkles, AlertCircle, Clock, ChevronRight, Youtube, Zap, CreditCard } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface Job {
  id: string;
  video_url: string;
  status: string;
  created_at: string;
  video_title?: string;
  progress_percentage?: number;
  current_step?: string;
  error_message?: string;
}

interface ResultSummary {
  moment_count: number;
  avg_hook: number;
  avg_retention: number;
  avg_shareability: number;
  hooks: string[];
  clip_urls: string[];
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const getVideoId = (url: string) =>
  url.match(/(?:v=|youtu\.be\/|shorts\/)([a-zA-Z0-9_-]{11})/)?.[1];

const getThumbnail = (url: string) => {
  const id = getVideoId(url);
  return id ? `https://img.youtube.com/vi/${id}/mqdefault.jpg` : null;
};

const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("es-AR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

const stepLabel: Record<string, string> = {
  downloading: "Obteniendo transcript...",
  analyzing: "Analizando con IA...",
  clipping: "Preparando clips...",
  generating: "Generando contenido...",
  completed: "Completado",
};

// ─── Score Ring ───────────────────────────────────────────────────────────────

function ScoreRing({ score, label, color }: { score: number; label: string; color: string }) {
  const r = 16;
  const circ = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-10 h-10">
        <svg className="-rotate-90 w-10 h-10">
          <circle cx="20" cy="20" r={r} stroke="#1e293b" strokeWidth="3.5" fill="none" />
          <circle
            cx="20" cy="20" r={r}
            stroke={color} strokeWidth="3.5" fill="none"
            strokeDasharray={circ}
            strokeDashoffset={circ - (score / 10) * circ}
            strokeLinecap="round"
            className="transition-all duration-700"
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-xs font-bold text-white">{score}</span>
      </div>
      <span className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</span>
    </div>
  );
}

// ─── Completed Card ───────────────────────────────────────────────────────────

function CompletedCard({ job, summary, index }: { job: Job; summary: ResultSummary; index: number }) {
  const thumb = getThumbnail(job.video_url);

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.08 }}
      className="group bg-slate-900 border border-white/5 rounded-2xl overflow-hidden hover:border-purple-500/40 transition-all duration-300 shadow-lg hover:shadow-purple-500/10"
    >
      {/* Thumbnail */}
      <div className="relative aspect-video bg-slate-800 overflow-hidden">
        {thumb ? (
          <img src={thumb} alt={job.video_title || "thumbnail"} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Youtube className="w-10 h-10 text-slate-600" />
          </div>
        )}
        {/* Overlay gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-slate-900 via-transparent to-transparent" />
        {/* Moments badge */}
        <div className="absolute top-3 right-3">
          <Badge className="bg-purple-500/90 text-white text-xs font-semibold backdrop-blur-sm border-0">
            <Zap className="w-3 h-3 mr-1" />
            {summary.moment_count} momentos
          </Badge>
        </div>
        <div className="absolute bottom-3 left-3">
          <Badge className="bg-green-500/20 text-green-400 border border-green-500/30 text-xs">
            ✅ Completado
          </Badge>
        </div>
      </div>

      <div className="p-5 space-y-4">
        {/* Title & date */}
        <div>
          <h3 className="font-semibold text-white line-clamp-2 leading-snug mb-1 group-hover:text-purple-300 transition-colors">
            {job.video_title || "Video sin título"}
          </h3>
          <p className="text-xs text-slate-500">{formatDate(job.created_at)}</p>
        </div>

        {/* Scores */}
        <div className="flex items-center justify-around bg-slate-950/50 rounded-xl py-3 border border-white/5">
          <ScoreRing score={Math.round(summary.avg_hook)} label="Gancho" color="#a855f7" />
          <div className="w-px h-8 bg-slate-800" />
          <ScoreRing score={Math.round(summary.avg_retention)} label="Retención" color="#06b6d4" />
          <div className="w-px h-8 bg-slate-800" />
          <ScoreRing score={Math.round(summary.avg_shareability)} label="Viralidad" color="#ec4899" />
        </div>

        {/* Top hooks preview */}
        {summary.hooks.length > 0 && (
          <div className="space-y-1.5">
            {summary.hooks.slice(0, 2).map((hook, i) => (
              <p key={i} className="text-xs text-slate-400 line-clamp-1 pl-2 border-l-2 border-purple-500/40">
                "{hook}"
              </p>
            ))}
          </div>
        )}

        {/* CTA */}
        <Link href={`/results/${job.id}`}>
          <Button className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 rounded-xl text-sm font-medium group/btn">
            Ver Resultados
            <ChevronRight className="w-4 h-4 ml-1 group-hover/btn:translate-x-0.5 transition-transform" />
          </Button>
        </Link>
      </div>
    </motion.div>
  );
}

// ─── Processing Card ──────────────────────────────────────────────────────────

function ProcessingCard({ job, index }: { job: Job; index: number }) {
  const thumb = getThumbnail(job.video_url);
  const progress = job.progress_percentage ?? 10;
  const step = job.current_step ? (stepLabel[job.current_step] ?? job.current_step) : "Iniciando...";

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.08 }}
      className="bg-slate-900 border border-blue-500/20 rounded-2xl overflow-hidden shadow-lg"
    >
      {/* Thin progress bar at top */}
      <div className="h-1 bg-slate-800">
        <motion.div
          className="h-full bg-gradient-to-r from-blue-500 to-purple-500 rounded-full"
          initial={{ width: "0%" }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </div>

      <div className="p-5 flex gap-4">
        {/* Thumbnail small */}
        <div className="w-20 h-14 rounded-lg bg-slate-800 overflow-hidden flex-shrink-0">
          {thumb ? (
            <img src={thumb} alt="thumb" className="w-full h-full object-cover opacity-60" />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <Youtube className="w-5 h-5 text-slate-600" />
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-medium text-white text-sm line-clamp-1 mb-1">
            {job.video_title || job.video_url.slice(0, 40) + "..."}
          </h3>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
            <span className="text-xs text-blue-400">{step}</span>
            <span className="text-xs text-slate-500 ml-auto">{progress}%</span>
          </div>
        </div>
      </div>
      <div className="px-5 pb-4">
        <Link href={`/results/${job.id}`}>
          <Button variant="outline" size="sm" className="w-full border-blue-500/30 text-blue-400 hover:bg-blue-500/10 text-xs">
            Ver progreso
          </Button>
        </Link>
      </div>
    </motion.div>
  );
}

// ─── Failed Row ───────────────────────────────────────────────────────────────

function FailedRow({ job, index }: { job: Job; index: number }) {
  const thumb = getThumbnail(job.video_url);

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.05 }}
      className="flex items-center gap-3 bg-slate-900/40 border border-red-500/10 rounded-xl px-4 py-3 hover:border-red-500/20 transition-colors"
    >
      {/* Tiny thumb */}
      <div className="w-12 h-9 rounded-md bg-slate-800 overflow-hidden flex-shrink-0 opacity-50">
        {thumb ? (
          <img src={thumb} alt="" className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Youtube className="w-3 h-3 text-slate-600" />
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-slate-400 line-clamp-1">
          {job.video_title || job.video_url.slice(0, 50) + "..."}
        </p>
        {job.error_message && (
          <p className="text-xs text-red-400/70 line-clamp-1 mt-0.5">{job.error_message}</p>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className="text-xs text-slate-600">{formatDate(job.created_at)}</span>
        <AlertCircle className="w-4 h-4 text-red-500/50" />
      </div>
    </motion.div>
  );
}

// ─── Main Dashboard ───────────────────────────────────────────────────────────

function DashboardContent() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [summaries, setSummaries] = useState<Record<string, ResultSummary>>({});
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [credits, setCredits] = useState<number | null>(null);
  const [upgradingPlan, setUpgradingPlan] = useState(false);
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = createClient();
  const { toast } = useToast();

  const hasCredits = credits === null || credits > 0; // null = loading, optimistic

  // Show success toast when coming back from LS checkout
  useEffect(() => {
    if (searchParams.get("upgrade") === "success") {
      toast({
        title: "🎉 ¡Suscripción activada!",
        description: "Ya tenés 40 créditos disponibles. ¡A crear contenido viral!",
      });
    }
  }, [searchParams, toast]);

  useEffect(() => {
    const fetchData = async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) { router.push("/login"); return; }

      // Fetch credits
      const { data: userData } = await supabase
        .from("users")
        .select("credits")
        .eq("id", user.id)
        .single();
      if (userData) setCredits(userData.credits);

      // Fetch jobs
      const { data: jobsData } = await supabase
        .from("jobs")
        .select("*")
        .order("created_at", { ascending: false });

      if (!jobsData) { setLoading(false); return; }
      setJobs(jobsData);

      // Fetch result summaries for completed jobs
      const completedIds = jobsData.filter(j => j.status === "completed").map(j => j.id);
      if (completedIds.length > 0) {
        const { data: results } = await supabase
          .from("content_results")
          .select("job_id, score_hook, score_retention, score_shareability, moment_index, hook, clip_url")
          .in("job_id", completedIds)
          .eq("type", "twitter_thread")
          .order("moment_index");

        if (results) {
          const grouped: Record<string, ResultSummary> = {};
          for (const r of results) {
            if (!grouped[r.job_id]) {
              grouped[r.job_id] = { moment_count: 0, avg_hook: 0, avg_retention: 0, avg_shareability: 0, hooks: [], clip_urls: [] };
            }
            const g = grouped[r.job_id];
            g.moment_count++;
            g.avg_hook += r.score_hook ?? 0;
            g.avg_retention += r.score_retention ?? 0;
            g.avg_shareability += r.score_shareability ?? 0;
            if (r.hook) g.hooks.push(r.hook);
            if (r.clip_url) g.clip_urls.push(r.clip_url);
          }
          // Average
          for (const id of Object.keys(grouped)) {
            const g = grouped[id];
            if (g.moment_count > 0) {
              g.avg_hook = Math.round(g.avg_hook / g.moment_count * 10) / 10;
              g.avg_retention = Math.round(g.avg_retention / g.moment_count * 10) / 10;
              g.avg_shareability = Math.round(g.avg_shareability / g.moment_count * 10) / 10;
            }
          }
          setSummaries(grouped);
        }
      }
      setLoading(false);
    };

    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [router, supabase]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const result = await submitVideo(videoUrl);
      router.push(`/results/${result.jobId}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al procesar el video";
      // 402 = no credits → show upgrade prompt instead of generic error
      if (msg.toLowerCase().includes("crédito") || msg.toLowerCase().includes("credit")) {
        setCredits(0);
        setError("UPGRADE_REQUIRED");
      } else {
        setError(msg);
      }
      setSubmitting(false);
    }
  };

  const handleUpgrade = async () => {
    setUpgradingPlan(true);
    try {
      await redirectToCheckout();
    } catch {
      setUpgradingPlan(false);
    }
  };

  const completed = jobs.filter(j => j.status === "completed");
  const active = jobs.filter(j => j.status === "processing" || j.status === "pending");
  const failed = jobs.filter(j => j.status === "failed");

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />

      <main className="max-w-6xl mx-auto px-6 py-10">

        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold mb-1">Dashboard</h1>
            <p className="text-slate-400 text-sm">
              {completed.length} completados · {active.length} en proceso · {failed.length} con error
            </p>
          </div>
          <Button
            onClick={() => { setShowForm(!showForm); setError(""); }}
            className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 rounded-xl gap-2"
          >
            <Sparkles className="w-4 h-4" />
            {showForm ? "Cancelar" : "Procesar Video"}
            {credits !== null && !showForm && (
              <span className="ml-1 text-xs bg-white/20 px-1.5 py-0.5 rounded-full">
                {credits}
              </span>
            )}
          </Button>
        </div>

        {/* Submit form */}
        {showForm && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-8 p-6 bg-slate-900/70 border border-purple-500/20 rounded-2xl"
          >
            {/* Credits indicator */}
            <div className="flex items-center justify-between mb-4">
              <label className="block text-sm font-medium text-slate-300">
                URL del video de YouTube
              </label>
              {credits !== null && (
                <span className={`text-xs font-medium px-2.5 py-1 rounded-full flex items-center gap-1.5 ${
                  credits > 0
                    ? "bg-purple-500/10 text-purple-300 border border-purple-500/20"
                    : "bg-red-500/10 text-red-400 border border-red-500/20"
                }`}>
                  <Zap className="w-3 h-3" />
                  {credits} crédito{credits !== 1 ? "s" : ""} disponible{credits !== 1 ? "s" : ""}
                </span>
              )}
            </div>

            {/* No credits — upgrade wall */}
            {credits === 0 || error === "UPGRADE_REQUIRED" ? (
              <div className="text-center py-6 px-4 bg-gradient-to-b from-purple-950/30 to-slate-900/50 border border-purple-500/20 rounded-xl">
                <div className="w-12 h-12 bg-purple-500/10 rounded-full flex items-center justify-center mx-auto mb-3">
                  <CreditCard className="w-6 h-6 text-purple-400" />
                </div>
                <h3 className="text-white font-semibold mb-1">Sin créditos disponibles</h3>
                <p className="text-slate-400 text-sm mb-4">
                  Necesitás créditos para procesar videos.<br />
                  El plan Starter incluye 40 créditos / mes.
                </p>
                <Button
                  onClick={handleUpgrade}
                  disabled={upgradingPlan}
                  className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white gap-2"
                >
                  {upgradingPlan ? "⏳ Cargando..." : "🚀 Obtener Starter — $9/mes"}
                </Button>
                <p className="text-xs text-slate-500 mt-3">
                  ¿Ya tenés suscripción?{" "}
                  <Link href="/account" className="text-purple-400 hover:underline">
                    Revisá tu cuenta
                  </Link>
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <input
                  type="url"
                  value={videoUrl}
                  onChange={e => setVideoUrl(e.target.value)}
                  placeholder="https://www.youtube.com/watch?v=..."
                  className="w-full px-4 py-3 bg-slate-800 border border-white/10 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500"
                  required
                  disabled={submitting}
                />
                {error && error !== "UPGRADE_REQUIRED" && (
                  <p className="text-red-400 text-sm">{error}</p>
                )}
                <Button
                  type="submit"
                  disabled={submitting || !videoUrl.trim()}
                  className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 rounded-xl py-3 disabled:opacity-50"
                >
                  {submitting ? "⏳ Enviando..." : "🚀 Procesar Video"}
                </Button>
              </form>
            )}
          </motion.div>
        )}

        {loading ? (
          <div className="flex justify-center py-24">
            <div className="animate-spin w-8 h-8 border-purple-500 border-t-transparent rounded-full border-2" />
          </div>
        ) : jobs.length === 0 ? (
          <div className="text-center py-24 border border-white/5 rounded-2xl bg-white/[0.02]">
            <Sparkles className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-xl mb-2 text-slate-300">Aún no procesaste ningún video</p>
            <p className="text-slate-500 text-sm mb-6">Pegá el link de un video de YouTube y la IA hace el resto</p>
            <Button onClick={() => setShowForm(true)} className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 rounded-xl">
              Procesar mi primer video
            </Button>
          </div>
        ) : (
          <div className="space-y-10">

            {/* Active jobs */}
            {active.length > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                  <Clock className="w-4 h-4 text-blue-400 animate-pulse" />
                  En proceso ({active.length})
                </h2>
                <div className="grid md:grid-cols-2 gap-4">
                  {active.map((job, i) => (
                    <ProcessingCard key={job.id} job={job} index={i} />
                  ))}
                </div>
              </section>
            )}

            {/* Completed jobs */}
            {completed.length > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-purple-400" />
                  Completados ({completed.length})
                </h2>
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
                  {completed.map((job, i) => (
                    <CompletedCard
                      key={job.id}
                      job={job}
                      summary={summaries[job.id] ?? { moment_count: 0, avg_hook: 0, avg_retention: 0, avg_shareability: 0, hooks: [], clip_urls: [] }}
                      index={i}
                    />
                  ))}
                </div>
              </section>
            )}

            {/* Failed jobs */}
            {failed.length > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 text-red-500/60" />
                  Con error ({failed.length})
                </h2>
                <div className="space-y-2">
                  {failed.map((job, i) => (
                    <FailedRow key={job.id} job={job} index={i} />
                  ))}
                </div>
              </section>
            )}

          </div>
        )}
      </main>
    </div>
  );
}

export default function Dashboard() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full" />
      </div>
    }>
      <DashboardContent />
    </Suspense>
  );
}
