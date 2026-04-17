"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { CopyTabs } from "./CopyTabs";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ChevronDown, Download, Sparkles, Loader2, Share2, ExternalLink } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/hooks/use-toast";

interface ScoreJustification {
  metric: string;
  score: number;
  reasoning: string;
  improvement_tip?: string | null;
}

interface ViralMomentCardProps {
  momentIndex: number;
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
}

const ScoreRing = ({ score, label }: { score: number; label: string }) => {
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const progress = (score / 10) * circumference;
  const color = score >= 8 ? "text-green-500" : score >= 6 ? "text-yellow-500" : "text-orange-500";
  
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
      <span className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">{label}</span>
    </div>
  );
};

export function ViralMomentCard({
  momentIndex,
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
}: ViralMomentCardProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const { toast } = useToast();

  const avgScore = ((scores.hook + scores.retention + scores.shareability) / 3).toFixed(1);
  const duration = endTime - startTime;

  // Detect if clipUrl is a YouTube link (worker fallback when video download is blocked)
  const isYouTubeUrl = Boolean(clipUrl && (clipUrl.includes('youtube.com') || clipUrl.includes('youtu.be')));

  const getYouTubeEmbedUrl = (url: string): string | null => {
    const videoIdMatch = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
    const timeMatch = url.match(/[?&]t=(\d+)s?/);
    const videoId = videoIdMatch?.[1];
    const time = timeMatch?.[1] ?? '0';
    if (!videoId) return null;
    return `https://www.youtube.com/embed/${videoId}?start=${time}&rel=0`;
  };

  const handleDownload = async () => {
    try {
      setIsDownloading(true);
      const response = await fetch(clipUrl);
      if (!response.ok) throw new Error("Network response was not ok");
      
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.style.display = "none";
      a.href = url;
      a.download = `clip_${momentIndex}_${hook.substring(0, 20).replace(/\s+/g, "_")}.mp4`;
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
    <Card className="bg-slate-900 border-slate-700 overflow-hidden shadow-xl transition-all hover:border-purple-500/30">
      {/* Premium Header */}
      <CardHeader className="bg-slate-950 border-b border-slate-800 p-5">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="bg-purple-950/30 text-purple-400 border-purple-500/30 text-xs px-2 py-0.5 rounded-full">
                MOMENTO #{momentIndex}
              </Badge>
              <span className="text-slate-500 text-xs font-mono">• {duration} segundos</span>
            </div>
            <h3 className="text-lg md:text-xl font-bold text-white leading-tight">
              &quot;{hook}&quot;
            </h3>
          </div>
          
          {/* Key Metrics Visualization */}
          <div className="flex items-center gap-4 bg-slate-900/50 p-2 rounded-xl border border-slate-800">
            <ScoreRing score={scores.hook} label="Gancho" />
            <ScoreRing score={scores.retention} label="Retención" />
            <ScoreRing score={scores.shareability} label="Viralidad" />
          </div>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        <div className="grid lg:grid-cols-[35%_65%] divider-x divider-slate-800">
          

          {/* LEFT: Video & Actions */}
          <div className="p-5 border-r border-slate-800 bg-slate-925 flex flex-col gap-4">
            <div className="relative rounded-xl overflow-hidden bg-black shadow-lg border border-slate-800 aspect-video group">
              {isYouTubeUrl ? (
                <iframe
                  src={getYouTubeEmbedUrl(clipUrl) ?? undefined}
                  className="w-full h-full"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                  title={`Clip momento ${momentIndex}`}
                />
              ) : clipUrl ? (
                <video
                  src={clipUrl}
                  controls
                  playsInline
                  className="w-full h-full object-contain"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-slate-500 text-sm">
                  Sin clip disponible
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              {isYouTubeUrl ? (
                <Button
                  variant="outline"
                  className="w-full border-slate-600 bg-slate-800 text-slate-200 hover:bg-purple-600 hover:text-white hover:border-purple-500 transition-all shadow-lg"
                  onClick={() => window.open(clipUrl, '_blank')}
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  Ver en YouTube
                </Button>
              ) : (
                <Button
                  variant="outline"
                  className="w-full border-slate-600 bg-slate-800 text-slate-200 hover:bg-purple-600 hover:text-white hover:border-purple-500 transition-all shadow-lg"
                  onClick={handleDownload}
                  disabled={isDownloading || !clipUrl}
                >
                  {isDownloading ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Download className="mr-2 h-4 w-4" />
                  )}
                  {isDownloading ? "Bajando..." : "Descargar MP4"}
                </Button>
              )}
              <Button variant="ghost" className="w-full hover:bg-slate-800 text-slate-400" disabled>
                <Share2 className="mr-2 h-4 w-4" />
                Compartir (Pronto)
              </Button>
            </div>
          </div>

          {/* RIGHT: Content Generator */}
          <div className="bg-slate-900 border-l border-slate-800 flex flex-col">
             <div className="p-4 border-b border-slate-800 bg-slate-950/30 shrink-0">
                <h4 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
                   🎨 Contenido Generado
                </h4>
             </div>
             <div className="p-0 flex-1 min-h-[300px]">
                <CopyTabs
                  twitterContent={twitterContent}
                  tiktokContent={tiktokContent}
                  linkedinContent={linkedinContent}
                  scriptContent={scriptContent}
                />
             </div>
          </div>
        </div>

        {/* BOTTOM: AI Insights Collapsible (Full Width) */}
        {justifications.length > 0 && (
          <div className="border-t border-slate-800 bg-slate-950/30">
            <Collapsible open={isOpen} onOpenChange={setIsOpen} className="w-full">
              <CollapsibleTrigger asChild>
                <Button variant="ghost" className="w-full justify-between items-center p-4 h-auto hover:bg-slate-900/50 group text-purple-400 hover:text-purple-300 rounded-none">
                  <span className="flex items-center gap-2 text-sm font-medium">
                      <Sparkles className="w-4 h-4" />
                      Análisis de Viralidad con IA
                      <span className="text-slate-500 text-xs font-normal ml-2 hidden sm:inline-block">
                        • Descubre por qué este clip funciona
                      </span>
                  </span>
                  <ChevronDown className={`w-5 h-5 transition-transform duration-300 text-slate-500 group-hover:text-purple-400 ${isOpen ? "rotate-180" : ""}`} />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="p-5 pt-2 grid md:grid-cols-3 gap-4 bg-slate-900/20">
                  {justifications.map((just, idx) => (
                    <div key={idx} className="bg-slate-900 rounded-lg p-4 border border-slate-800 hover:border-purple-500/20 transition-colors">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-purple-300 px-2 py-0.5 bg-purple-500/10 rounded-full ring-1 ring-purple-500/20">
                          {just.metric}
                        </span>
                        <span className={`text-sm font-bold ${just.score >= 8 ? "text-green-400" : just.score >= 6 ? "text-yellow-400" : "text-orange-400"}`}>
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
                </div>
              </CollapsibleContent>
            </Collapsible>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
