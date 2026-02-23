"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { submitVideo } from "@/lib/api";

interface Job {
  id: string;
  video_url: string;
  status: string;
  created_at: string;
  video_title?: string;
}

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();
  const supabase = createClient();

  useEffect(() => {
    const fetchJobs = async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) {
        router.push("/login");
        return;
      }

      const { data } = await supabase
        .from("jobs")
        .select("*")
        .order("created_at", { ascending: false });

      if (data) {
        setJobs(data);
      }
      setLoading(false);
    };

    fetchJobs();
    
    const interval = setInterval(fetchJobs, 10000);
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
      setError(err instanceof Error ? err.message : "Error al procesar el video");
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />
      
      <main className="max-w-6xl mx-auto p-6 md:p-12">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold mb-2">Mis Videos</h1>
            <p className="text-slate-400">Gestiona y descarga tus clips generados</p>
          </div>
          <Button 
            onClick={() => { setShowForm(!showForm); setError(""); }}
            className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 rounded-xl"
          >
            {showForm ? "✕ Cancelar" : "✨ Procesar Nuevo Video"}
          </Button>
        </div>

        {showForm && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-8 p-6 bg-slate-900/70 border border-purple-500/20 rounded-2xl"
          >
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  URL del video de YouTube
                </label>
                <input
                  type="url"
                  value={videoUrl}
                  onChange={(e) => setVideoUrl(e.target.value)}
                  placeholder="https://www.youtube.com/watch?v=..."
                  className="w-full px-4 py-3 bg-slate-800 border border-white/10 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500"
                  required
                  disabled={submitting}
                />
              </div>
              {error && (
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
          </motion.div>
        )}

        {loading ? (
          <div className="flex justify-center py-20">
            <div className="animate-spin w-8 h-8 border-purple-500 border-t-transparent rounded-full border-2" />
          </div>
        ) : jobs.length === 0 ? (
          <div className="text-center py-20 border border-white/5 rounded-2xl bg-white/5">
            <p className="text-xl mb-4 text-slate-300">Aún no has procesado ningún video</p>
            <Button onClick={() => setShowForm(true)} variant="outline" className="border-purple-500 text-purple-400 hover:bg-purple-500/10">
              Crear el primero
            </Button>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {jobs.map((job, index) => (
              <motion.div
                key={job.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.1 }}
                className="bg-slate-900/50 border border-white/5 rounded-xl overflow-hidden hover:border-purple-500/30 transition-all group"
              >
                <div className="p-6 space-y-4">
                  <div className="flex items-start justify-between">
                    <Badge className={`
                      ${job.status === 'completed' ? 'bg-green-500/20 text-green-400' : 
                        job.status === 'processing' ? 'bg-blue-500/20 text-blue-400 animate-pulse' : 
                        job.status === 'failed' ? 'bg-red-500/20 text-red-400' : 'bg-slate-700/50 text-slate-400'}
                    `}>
                      {job.status === 'completed' ? '✅ Completado' :
                       job.status === 'processing' ? '⚙️ Procesando' :
                       job.status === 'failed' ? '❌ Error' : '⏳ Pendiente'}
                    </Badge>
                    <span className="text-xs text-slate-500">
                      {new Date(job.created_at).toLocaleDateString()}
                    </span>
                  </div>
                  
                  <div>
                    <h3 className="font-medium line-clamp-2 mb-1" title={job.video_title || job.video_url}>
                      {job.video_title || "Video sin título"}
                    </h3>
                    <p className="text-xs text-slate-500 truncate">{job.video_url}</p>
                  </div>

                  <div className="pt-2">
                    {job.status === 'completed' && (
                      <Link href={`/results/${job.id}`}>
                        <Button variant="secondary" className="w-full bg-white/5 hover:bg-white/10 text-white">
                          Ver Resultados →
                        </Button>
                      </Link>
                    )}
                    {(job.status === 'processing' || job.status === 'pending') && (
                      <Link href={`/results/${job.id}`}>
                        <Button variant="outline" className="w-full border-blue-500/30 text-blue-400 hover:bg-blue-500/10">
                          Ver Progreso
                        </Button>
                      </Link>
                    )}
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
