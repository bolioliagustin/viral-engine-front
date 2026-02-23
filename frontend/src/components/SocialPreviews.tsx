"use client";

import { motion } from "framer-motion";

interface TwitterPreviewProps {
  content: string;
  username?: string;
  handle?: string;
  avatar?: string;
}

export function TwitterPreview({ content, username = "Tu Marca", handle = "@tumarca", avatar }: TwitterPreviewProps) {
  // Split content into tweets (assuming format "Tweet 1: ...\n\nTweet 2: ...")
  const tweets = content
    .split(/Tweet \d+:?/i)
    .filter(t => t.trim())
    .map(t => t.trim());

  return (
    <div className="bg-black rounded-xl border border-slate-700 overflow-hidden max-h-96 overflow-y-auto">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-700 flex items-center gap-2 sticky top-0 bg-black z-10">
        <svg viewBox="0 0 24 24" className="w-5 h-5 text-white" fill="currentColor">
          <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
        </svg>
        <span className="text-slate-400 text-sm">Vista previa del hilo • {tweets.length} tweets</span>
      </div>

      {/* Tweets */}
      <div className="relative">
        {tweets.map((tweet, index) => (
          <motion.div
            key={index}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.1 }}
            className="p-4 hover:bg-slate-900/50 transition-colors border-b border-slate-800 last:border-b-0"
          >
            <div className="flex gap-3">
              {/* Avatar with connecting line */}
              <div className="flex flex-col items-center">
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold flex-shrink-0">
                  {avatar ? (
                    <img src={avatar} alt="" className="w-full h-full rounded-full object-cover" />
                  ) : (
                    username.charAt(0).toUpperCase()
                  )}
                </div>
                {/* Connecting line (show for all but last tweet) */}
                {index < tweets.length - 1 && (
                  <div className="w-0.5 bg-slate-700 flex-1 mt-2 min-h-4" />
                )}
              </div>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1 mb-1 flex-wrap">
                  <span className="font-bold text-white">{username}</span>
                  <svg viewBox="0 0 24 24" className="w-4 h-4 text-blue-400 flex-shrink-0" fill="currentColor">
                    <path d="M22.5 12.5c0-1.58-.875-2.95-2.148-3.6.154-.435.238-.905.238-1.4 0-2.21-1.71-3.998-3.818-3.998-.47 0-.92.084-1.336.25C14.818 2.415 13.51 1.5 12 1.5s-2.816.917-3.437 2.25c-.415-.165-.866-.25-1.336-.25-2.11 0-3.818 1.79-3.818 4 0 .494.083.964.237 1.4-1.272.65-2.147 2.018-2.147 3.6 0 1.495.782 2.798 1.942 3.486-.02.17-.032.34-.032.514 0 2.21 1.708 4 3.818 4 .47 0 .92-.086 1.335-.25.62 1.334 1.926 2.25 3.437 2.25 1.512 0 2.818-.916 3.437-2.25.415.163.865.248 1.336.248 2.11 0 3.818-1.79 3.818-4 0-.174-.012-.344-.033-.513 1.158-.687 1.943-1.99 1.943-3.484zm-6.616-3.334l-4.334 6.5c-.145.217-.382.334-.625.334-.143 0-.288-.04-.416-.126l-.115-.094-2.415-2.415c-.293-.293-.293-.768 0-1.06s.768-.294 1.06 0l1.77 1.767 3.825-5.74c.23-.345.696-.436 1.04-.207.346.23.44.696.21 1.04z" />
                  </svg>
                  <span className="text-slate-500 text-sm">{handle}</span>
                  <span className="text-slate-500">·</span>
                  <span className="text-slate-500 text-sm">ahora</span>
                </div>
                <p className="text-white whitespace-pre-wrap break-words text-[15px] leading-relaxed">{tweet}</p>
                
                {/* Engagement buttons */}
                <div className="flex items-center gap-6 mt-3 text-slate-500">
                  <button className="flex items-center gap-1.5 hover:text-blue-400 transition-colors group">
                    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
                      <path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z" />
                    </svg>
                    <span className="text-xs">{12 + index * 3}</span>
                  </button>
                  <button className="flex items-center gap-1.5 hover:text-green-400 transition-colors">
                    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
                      <path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.432 9.48.068 8.02 4.5 3.88zM16.5 6H11V4h5.5c2.209 0 4 1.79 4 4v8.45l2.068-1.93 1.364 1.46-4.432 4.14-4.432-4.14 1.364-1.46 2.068 1.93V8c0-1.1-.896-2-2-2z" />
                    </svg>
                    <span className="text-xs">{48 + index * 12}</span>
                  </button>
                  <button className="flex items-center gap-1.5 hover:text-pink-400 transition-colors">
                    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
                      <path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91z" />
                    </svg>
                    <span className="text-xs">{234 + index * 50}</span>
                  </button>
                  <button className="flex items-center gap-1.5 hover:text-blue-400 transition-colors ml-auto">
                    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
                      <path d="M12 2.59l5.7 5.7-1.41 1.42L13 6.41V16h-2V6.41l-3.3 3.3-1.41-1.42L12 2.59zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

interface LinkedInPreviewProps {
  content: string;
  username?: string;
  title?: string;
  avatar?: string;
}

export function LinkedInPreview({ content, username = "Tu Nombre", title = "Founder & CEO", avatar }: LinkedInPreviewProps) {
  // Split at paragraph breaks and get first 3 lines for "ver más" effect
  const lines = content.split('\n').filter(l => l.trim());
  const previewLines = lines.slice(0, 3).join('\n');
  const hasMore = lines.length > 3;

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden text-black">
      {/* Header */}
      <div className="p-4 flex items-start gap-3">
        {/* Avatar */}
        <div className="flex-shrink-0">
          <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-600 to-blue-800 flex items-center justify-center text-white font-bold">
            {avatar ? (
              <img src={avatar} alt="" className="w-full h-full rounded-full object-cover" />
            ) : (
              username.charAt(0).toUpperCase()
            )}
          </div>
        </div>

        {/* User info */}
        <div className="flex-1">
          <div className="flex items-center gap-1">
            <span className="font-semibold text-gray-900">{username}</span>
            <span className="text-gray-400">• 1º</span>
          </div>
          <p className="text-xs text-gray-500">{title}</p>
          <p className="text-xs text-gray-400 flex items-center gap-1">
            ahora • 
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z" />
            </svg>
          </p>
        </div>

        {/* More button */}
        <button className="text-gray-400 hover:text-gray-600">
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="5" r="2" />
            <circle cx="12" cy="12" r="2" />
            <circle cx="12" cy="19" r="2" />
          </svg>
        </button>
      </div>

      {/* Content */}
      <div className="px-4 pb-2">
        <div className="text-gray-800 whitespace-pre-wrap">
          {previewLines}
          {hasMore && (
            <span className="text-blue-600 font-medium cursor-pointer hover:underline">
              ...ver más
            </span>
          )}
        </div>
      </div>

      {/* Engagement counts */}
      <div className="px-4 py-2 flex items-center justify-between text-xs text-gray-500 border-t border-gray-100">
        <div className="flex items-center gap-1">
          <div className="flex -space-x-1">
            <span className="w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center text-white text-[8px]">👍</span>
            <span className="w-4 h-4 rounded-full bg-red-500 flex items-center justify-center text-white text-[8px]">❤️</span>
            <span className="w-4 h-4 rounded-full bg-green-500 flex items-center justify-center text-white text-[8px]">👏</span>
          </div>
          <span>2,847</span>
        </div>
        <span>142 comentarios · 89 compartidos</span>
      </div>

      {/* Action buttons */}
      <div className="px-2 py-1 border-t border-gray-100 flex justify-around">
        {["👍 Recomendar", "💬 Comentar", "🔄 Compartir", "📤 Enviar"].map((action) => (
          <button key={action} className="flex-1 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded transition-colors">
            {action}
          </button>
        ))}
      </div>
    </div>
  );
}

interface ScriptPreviewProps {
  content: string;
}

export function ScriptPreview({ content }: ScriptPreviewProps) {
  // Parse script sections like [0-3s], [VISUAL], [AUDIO]
  const parseScript = (text: string) => {
    return text.split('\n').map((line, i) => {
      const trimmed = line.trim();
      if (!trimmed) return null;

      // Time markers
      const timeMatch = trimmed.match(/^\[(\d+-?\d*s?)\]/);
      if (timeMatch) {
        return { type: 'time', time: timeMatch[1], content: trimmed.replace(timeMatch[0], '').trim() };
      }

      // Visual cues
      if (trimmed.toLowerCase().includes('[visual]')) {
        return { type: 'visual', content: trimmed.replace(/\[visual\]/gi, '').trim() };
      }

      // Audio cues
      if (trimmed.toLowerCase().includes('[audio]') || trimmed.toLowerCase().includes('[v.o.]')) {
        return { type: 'audio', content: trimmed.replace(/\[(audio|v\.o\.)\]/gi, '').trim() };
      }

      return { type: 'text', content: trimmed };
    }).filter(Boolean);
  };

  const lines = parseScript(content);

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 overflow-hidden font-mono">
      {/* Header */}
      <div className="px-4 py-3 bg-slate-800 border-b border-slate-700 flex items-center gap-2">
        <span className="text-red-500">●</span>
        <span className="text-yellow-500">●</span>
        <span className="text-green-500">●</span>
        <span className="text-slate-400 text-sm ml-2">script.txt</span>
      </div>

      {/* Script content */}
      <div className="p-4 space-y-2 text-sm">
        {lines.map((line: any, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.05 }}
            className="flex gap-3"
          >
            {line.type === 'time' && (
              <>
                <span className="text-cyan-400 font-bold w-16">[{line.time}]</span>
                <span className="text-slate-300">{line.content}</span>
              </>
            )}
            {line.type === 'visual' && (
              <>
                <span className="text-purple-400 font-bold w-16">[VIS]</span>
                <span className="text-purple-200">{line.content}</span>
              </>
            )}
            {line.type === 'audio' && (
              <>
                <span className="text-green-400 font-bold w-16">[AUD]</span>
                <span className="text-green-200">{line.content}</span>
              </>
            )}
            {line.type === 'text' && (
              <span className="text-slate-400 ml-16">{line.content}</span>
            )}
          </motion.div>
        ))}
      </div>
    </div>
  );
}
