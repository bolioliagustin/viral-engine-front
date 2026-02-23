"use client";

import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface TikTokPreviewProps {
  overlayText: string;
  caption: string;
  visualHook?: string;
  soundHook?: string;
}

export function TikTokPreview({ overlayText, caption, visualHook, soundHook }: TikTokPreviewProps) {
  // Extract hashtags from caption
  const hashtagRegex = /#\w+/g;
  const hashtags = caption.match(hashtagRegex) || [];
  const captionWithoutHashtags = caption.replace(hashtagRegex, '').trim();

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="w-full max-w-md mx-auto"
    >
      <Card className="bg-gradient-to-br from-slate-900 via-slate-950 to-black border-slate-700/50 overflow-hidden">
        {/* TikTok-style header */}
        <div className="bg-gradient-to-r from-purple-500/10 to-pink-500/10 border-b border-purple-500/20 px-4 py-2">
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 24 24">
              <path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z"/>
            </svg>
            <span className="text-white font-semibold text-sm">TikTok/Reels</span>
          </div>
        </div>

        {/* Overlay Text - Large and Bold */}
        <div className="p-6 text-center bg-gradient-to-b from-transparent to-slate-950/50">
          <h3 className="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-purple-400 via-pink-400 to-purple-400 leading-tight mb-4">
            {overlayText}
          </h3>
        </div>

        {/* Caption */}
        <div className="px-6 pb-4">
          <p className="text-white text-base leading-relaxed mb-3">
            {captionWithoutHashtags}
          </p>

          {/* Hashtags */}
          {hashtags.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {hashtags.map((tag, i) => (
                <Badge 
                  key={i} 
                  variant="outline" 
                  className="text-xs text-blue-400 border-blue-400/30 bg-blue-400/5"
                >
                  {tag}
                </Badge>
              ))}
            </div>
          )}

          {/* Visual/Sound Hooks */}
          <div className="space-y-2 mt-4 pt-4 border-t border-slate-700/50">
            {visualHook && (
              <div className="flex items-start gap-2">
                <span className="text-purple-400 text-xs font-semibold">📹 Visual:</span>
                <span className="text-slate-400 text-xs">{visualHook}</span>
              </div>
            )}
            {soundHook && (
              <div className="flex items-start gap-2">
                <span className="text-pink-400 text-xs font-semibold">🎵 Sound:</span>
                <span className="text-slate-400 text-xs italic">"{soundHook}"</span>
              </div>
            )}
          </div>
        </div>

        {/* Viral indicators */}
        <div className="bg-gradient-to-r from-purple-500/10 to-pink-500/10 px-6 py-3 flex items-center justify-between">
          <Badge className="bg-purple-500/20 text-purple-300 border-purple-400/30">
            🚀 Optimizado para viralidad
          </Badge>
        </div>
      </Card>
    </motion.div>
  );
}

// Compact version for preview
export function MiniTikTokPreview({ overlayText, caption }: Pick<TikTokPreviewProps, 'overlayText' | 'caption'>) {
  const shortCaption = caption.length > 100 ? caption.substring(0, 100) + '...' : caption;
  
  return (
    <Card className="bg-gradient-to-br from-slate-900 to-slate-950 border-slate-700/50 p-4">
      <div className="flex items-start gap-3">
        <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center flex-shrink-0">
          <svg className="w-6 h-6 text-white" fill="currentColor" viewBox="0 0 24 24">
            <path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z"/>
          </svg>
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="font-bold text-white text-sm mb-1">{overlayText}</h4>
          <p className="text-slate-400 text-xs line-clamp-2">{shortCaption}</p>
        </div>
      </div>
    </Card>
  );
}
