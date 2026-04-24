"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { Check, Copy, Download } from "lucide-react";
import { motion } from "framer-motion";

interface CopyTabsProps {
  twitterContent?: string;
  tiktokContent?: string;
  linkedinContent?: string;
  scriptContent?: string;
  overlayText?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Robust Twitter thread parser
// Handles ALL these cases:
//   - Tweets separated by \n\n (ideal)
//   - "Tweet 1:", "Tweet 2:" prefixes
//   - "1/", "2/" counters
//   - "1/7", "2/7" counters
//   - Single blob (split by sentence as fallback, capped at ~280 chars)
// ─────────────────────────────────────────────────────────────────────────────
function parseTwitterThread(raw: string): string[] {
  if (!raw) return [];
  const text = raw.trim();

  // Case 1: "Tweet 1:" style
  if (/tweet\s*\d+\s*:?/i.test(text)) {
    const parts = text
      .split(/tweet\s*\d+\s*:?/i)
      .map((t) => t.trim())
      .filter(Boolean);
    if (parts.length >= 2) return parts;
  }

  // Case 2: "1/", "1/7" style counters at start of line
  const counterPattern = /^\s*\d+\s*\/\s*\d*\s*[:\-.)]?\s*/gm;
  if (counterPattern.test(text)) {
    const parts = text
      .split(/^\s*\d+\s*\/\s*\d*\s*[:\-.)]?\s*/gm)
      .map((t) => t.trim())
      .filter(Boolean);
    if (parts.length >= 2) return parts;
  }

  // Case 3: \n\n separated (most common)
  const doubleNewlineParts = text
    .split(/\n\s*\n/)
    .map((t) => t.trim())
    .filter(Boolean);
  if (doubleNewlineParts.length >= 2) return doubleNewlineParts;

  // Case 4: fallback — split into sentences, group into ~250 char chunks
  const sentences = text.match(/[^.!?]+[.!?]+[\])'"`]*|\S+$/g) ?? [text];
  const chunks: string[] = [];
  let cur = "";
  for (const s of sentences) {
    const candidate = cur ? `${cur} ${s.trim()}` : s.trim();
    if (candidate.length > 250 && cur) {
      chunks.push(cur);
      cur = s.trim();
    } else {
      cur = candidate;
    }
  }
  if (cur) chunks.push(cur);
  return chunks.length > 0 ? chunks : [text];
}

// ─────────────────────────────────────────────────────────────────────────────
// Twitter logo (official X logo)
// ─────────────────────────────────────────────────────────────────────────────
const TwitterIcon = ({ className = "" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
  </svg>
);

const VerifiedBadge = ({ className = "" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" className={className} fill="currentColor">
    <path d="M22.5 12.5c0-1.58-.875-2.95-2.148-3.6.154-.435.238-.905.238-1.4 0-2.21-1.71-3.998-3.818-3.998-.47 0-.92.084-1.336.25C14.818 2.415 13.51 1.5 12 1.5s-2.816.917-3.437 2.25c-.415-.165-.866-.25-1.336-.25-2.11 0-3.818 1.79-3.818 4 0 .494.083.964.237 1.4-1.272.65-2.147 2.018-2.147 3.6 0 1.495.782 2.798 1.942 3.486-.02.17-.032.34-.032.514 0 2.21 1.708 4 3.818 4 .47 0 .92-.086 1.335-.25.62 1.334 1.926 2.25 3.437 2.25 1.512 0 2.818-.916 3.437-2.25.415.163.865.248 1.336.248 2.11 0 3.818-1.79 3.818-4 0-.174-.012-.344-.033-.513 1.158-.687 1.943-1.99 1.943-3.484zm-6.616-3.334l-4.334 6.5c-.145.217-.382.334-.625.334-.143 0-.288-.04-.416-.126l-.115-.094-2.415-2.415c-.293-.293-.293-.768 0-1.06s.768-.294 1.06 0l1.77 1.767 3.825-5.74c.23-.345.696-.436 1.04-.207.346.23.44.696.21 1.04z" />
  </svg>
);

// ─────────────────────────────────────────────────────────────────────────────
export function CopyTabs({
  twitterContent,
  tiktokContent,
  linkedinContent,
  scriptContent,
  overlayText,
}: CopyTabsProps) {
  const { toast } = useToast();
  const [copiedTab, setCopiedTab] = useState<string | null>(null);

  const tweets = useMemo(
    () => (twitterContent ? parseTwitterThread(twitterContent) : []),
    [twitterContent]
  );

  const handleCopy = async (content: string, tabName: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedTab(tabName);
      toast({
        title: "✅ Copiado al portapapeles",
        description: `El contenido de ${tabName} está listo para pegar.`,
        duration: 2000,
      });
      setTimeout(() => setCopiedTab(null), 2000);
    } catch (err) {
      toast({
        title: "❌ Error",
        description: "No se pudo copiar el contenido.",
        variant: "destructive",
        duration: 2000,
      });
    }
  };

  const handleDownloadTxt = (content: string, filename: string) => {
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Determine default tab (first available)
  const defaultTab = twitterContent
    ? "twitter"
    : tiktokContent
    ? "tiktok"
    : linkedinContent
    ? "linkedin"
    : "script";

  return (
    <Tabs defaultValue={defaultTab} className="w-full flex flex-col h-full">
      <TabsList className="w-full bg-slate-950/50 border-b border-slate-800 flex justify-start gap-0 p-0 rounded-none h-12 overflow-x-auto">
        {twitterContent && (
          <TabsTrigger
            value="twitter"
            className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-slate-900 data-[state=active]:text-blue-400 hover:bg-slate-900/50 transition-colors"
          >
            <TwitterIcon className="w-4 h-4 mr-1.5" />
            Twitter/X
          </TabsTrigger>
        )}
        {tiktokContent && (
          <TabsTrigger
            value="tiktok"
            className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-pink-500 data-[state=active]:bg-slate-900 data-[state=active]:text-pink-400 hover:bg-slate-900/50 transition-colors"
          >
            📱 TikTok
          </TabsTrigger>
        )}
        {linkedinContent && (
          <TabsTrigger
            value="linkedin"
            className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-700 data-[state=active]:bg-slate-900 data-[state=active]:text-blue-300 hover:bg-slate-900/50 transition-colors"
          >
            💼 LinkedIn
          </TabsTrigger>
        )}
        {scriptContent && (
          <TabsTrigger
            value="script"
            className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-orange-500 data-[state=active]:bg-slate-900 data-[state=active]:text-orange-400 hover:bg-slate-900/50 transition-colors"
          >
            🎬 Script
          </TabsTrigger>
        )}
      </TabsList>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* TWITTER — Platform-fidelity preview                                 */}
      {/* ═══════════════════════════════════════════════════════════════════ */}
      {twitterContent && (
        <TabsContent value="twitter" className="mt-0 flex flex-col min-h-[400px] bg-slate-925">
          <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
            <div className="bg-black rounded-xl border border-slate-800 overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2 sticky top-0 bg-black z-10">
                <TwitterIcon className="w-4 h-4 text-white" />
                <span className="text-slate-400 text-xs">
                  Vista previa • {tweets.length} {tweets.length === 1 ? "tweet" : "tweets"}
                </span>
              </div>

              {tweets.map((tweet, idx) => (
                <motion.div
                  key={idx}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.05 }}
                  className="p-4 border-b border-slate-800 last:border-0"
                >
                  <div className="flex gap-3">
                    <div className="flex flex-col items-center shrink-0">
                      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold text-sm">
                        T
                      </div>
                      {idx < tweets.length - 1 && (
                        <div className="w-0.5 bg-slate-700 flex-1 mt-2 min-h-8" />
                      )}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1 mb-1 flex-wrap">
                        <span className="font-bold text-white text-sm">Tu Marca</span>
                        <VerifiedBadge className="w-4 h-4 text-blue-400" />
                        <span className="text-slate-500 text-sm">@tumarca</span>
                        <span className="text-slate-500 text-sm">·</span>
                        <span className="text-slate-500 text-sm">ahora</span>
                        {tweets.length > 1 && (
                          <span className="ml-auto text-slate-500 text-xs font-mono">
                            {idx + 1}/{tweets.length}
                          </span>
                        )}
                      </div>
                      <p className="text-white whitespace-pre-wrap break-words text-[14px] leading-relaxed">
                        {tweet}
                      </p>
                    </div>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>

          <div className="border-t border-slate-800 p-3 flex gap-2 bg-slate-950/40">
            <Button
              size="sm"
              onClick={() => handleCopy(twitterContent, "Twitter")}
              className={`flex-1 transition-all ${
                copiedTab === "Twitter"
                  ? "bg-green-500 hover:bg-green-600"
                  : "bg-blue-600 hover:bg-blue-700"
              } text-white`}
            >
              {copiedTab === "Twitter" ? (
                <Check className="w-4 h-4 mr-1" />
              ) : (
                <Copy className="w-4 h-4 mr-1" />
              )}
              {copiedTab === "Twitter" ? "Copiado" : "Copiar hilo"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleDownloadTxt(twitterContent, "twitter_thread")}
              className="border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
            >
              <Download className="w-4 h-4 mr-1" />
              .txt
            </Button>
          </div>
        </TabsContent>
      )}

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* TIKTOK — Mobile phone frame preview                                 */}
      {/* ═══════════════════════════════════════════════════════════════════ */}
      {tiktokContent && (
        <TabsContent value="tiktok" className="mt-0 flex flex-col min-h-[400px] bg-slate-925">
          <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
            <div className="bg-gradient-to-br from-slate-900 via-slate-950 to-black rounded-xl border border-pink-500/20 overflow-hidden">
              <div className="bg-gradient-to-r from-purple-500/10 to-pink-500/10 px-4 py-2 border-b border-pink-500/20">
                <span className="text-xs font-semibold text-pink-300 uppercase tracking-wider">
                  📱 TikTok / Reels
                </span>
              </div>

              {overlayText && (
                <div className="p-5 text-center border-b border-slate-800/50">
                  <div className="text-[10px] uppercase tracking-widest text-pink-400/70 mb-2">
                    Overlay en el video
                  </div>
                  <h3 className="text-2xl md:text-3xl font-black text-white leading-tight">
                    {overlayText}
                  </h3>
                </div>
              )}

              <div className="p-4">
                <div className="text-[10px] uppercase tracking-widest text-pink-400/70 mb-2">
                  Caption
                </div>
                <p className="text-slate-200 text-sm whitespace-pre-wrap leading-relaxed">
                  {tiktokContent}
                </p>
              </div>
            </div>
          </div>

          <div className="border-t border-slate-800 p-3 flex gap-2 bg-slate-950/40">
            <Button
              size="sm"
              onClick={() => handleCopy(tiktokContent, "TikTok")}
              className={`flex-1 transition-all ${
                copiedTab === "TikTok"
                  ? "bg-green-500 hover:bg-green-600"
                  : "bg-pink-600 hover:bg-pink-700"
              } text-white`}
            >
              {copiedTab === "TikTok" ? (
                <Check className="w-4 h-4 mr-1" />
              ) : (
                <Copy className="w-4 h-4 mr-1" />
              )}
              {copiedTab === "TikTok" ? "Copiado" : "Copiar caption"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleDownloadTxt(tiktokContent, "tiktok_caption")}
              className="border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
            >
              <Download className="w-4 h-4 mr-1" />
              .txt
            </Button>
          </div>
        </TabsContent>
      )}

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* LINKEDIN — Real platform-look preview (light card, LinkedIn blue)    */}
      {/* ═══════════════════════════════════════════════════════════════════ */}
      {linkedinContent && (
        <TabsContent value="linkedin" className="mt-0 flex flex-col min-h-[400px] bg-slate-925">
          <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
            <div className="bg-white rounded-xl border border-slate-300 overflow-hidden text-slate-900 shadow-sm">
              <div className="p-4 flex items-start gap-3 border-b border-slate-100">
                <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-600 to-blue-800 flex items-center justify-center text-white font-bold shrink-0">
                  T
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-slate-900 text-sm">Tu Nombre</div>
                  <div className="text-xs text-slate-500">Founder & CEO</div>
                  <div className="text-xs text-slate-400">ahora · 🌐</div>
                </div>
              </div>

              <div className="p-4">
                {linkedinContent.split("\n\n").map((paragraph, i) => (
                  <p
                    key={i}
                    className="text-sm text-slate-800 mb-3 last:mb-0 leading-relaxed whitespace-pre-wrap"
                  >
                    {paragraph}
                  </p>
                ))}
              </div>

              <div className="px-4 py-2 border-t border-slate-100 text-xs text-slate-500 flex items-center gap-2">
                <span className="flex -space-x-1">
                  <span className="w-4 h-4 rounded-full bg-blue-500 text-white flex items-center justify-center text-[8px]">
                    👍
                  </span>
                  <span className="w-4 h-4 rounded-full bg-red-500 text-white flex items-center justify-center text-[8px]">
                    ❤
                  </span>
                </span>
                <span>Tú y otras personas</span>
              </div>

              <div className="border-t border-slate-100 grid grid-cols-4 text-xs text-slate-600">
                {["👍 Recomendar", "💬 Comentar", "🔄 Compartir", "📤 Enviar"].map((a) => (
                  <button
                    key={a}
                    className="py-2 hover:bg-slate-50 transition-colors text-[11px]"
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="border-t border-slate-800 p-3 flex gap-2 bg-slate-950/40">
            <Button
              size="sm"
              onClick={() => handleCopy(linkedinContent, "LinkedIn")}
              className={`flex-1 transition-all ${
                copiedTab === "LinkedIn"
                  ? "bg-green-500 hover:bg-green-600"
                  : "bg-blue-700 hover:bg-blue-800"
              } text-white`}
            >
              {copiedTab === "LinkedIn" ? (
                <Check className="w-4 h-4 mr-1" />
              ) : (
                <Copy className="w-4 h-4 mr-1" />
              )}
              {copiedTab === "LinkedIn" ? "Copiado" : "Copiar post"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleDownloadTxt(linkedinContent, "linkedin_post")}
              className="border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
            >
              <Download className="w-4 h-4 mr-1" />
              .txt
            </Button>
          </div>
        </TabsContent>
      )}

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* SCRIPT — Terminal/monospace style with time markers                 */}
      {/* ═══════════════════════════════════════════════════════════════════ */}
      {scriptContent && (
        <TabsContent value="script" className="mt-0 flex flex-col min-h-[400px] bg-slate-925">
          <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
            <div className="bg-slate-950 rounded-xl border border-slate-800 overflow-hidden font-mono">
              <div className="px-4 py-2 bg-slate-900 border-b border-slate-800 flex items-center gap-2">
                <span className="text-red-500 text-xs">●</span>
                <span className="text-yellow-500 text-xs">●</span>
                <span className="text-green-500 text-xs">●</span>
                <span className="text-slate-400 text-xs ml-2">script.md</span>
              </div>
              <div className="p-4 space-y-3 text-sm">
                {scriptContent.split("\n\n").map((section, idx) => {
                  const timeMatch = section.match(/\[(\d+-\d+s)\]/);
                  const isTimed = timeMatch !== null;
                  return (
                    <div
                      key={idx}
                      className={`${
                        isTimed
                          ? "bg-slate-900/50 p-3 rounded border-l-2 border-orange-500"
                          : ""
                      }`}
                    >
                      {isTimed && (
                        <span className="text-orange-400 font-mono text-xs block mb-1">
                          {timeMatch[1]}
                        </span>
                      )}
                      <span className="text-slate-300 whitespace-pre-wrap">
                        {section.replace(/\[\d+-\d+s\]/, "").trim()}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="border-t border-slate-800 p-3 flex gap-2 bg-slate-950/40">
            <Button
              size="sm"
              onClick={() => handleCopy(scriptContent, "Script")}
              className={`flex-1 transition-all ${
                copiedTab === "Script"
                  ? "bg-green-500 hover:bg-green-600"
                  : "bg-orange-600 hover:bg-orange-700"
              } text-white`}
            >
              {copiedTab === "Script" ? (
                <Check className="w-4 h-4 mr-1" />
              ) : (
                <Copy className="w-4 h-4 mr-1" />
              )}
              {copiedTab === "Script" ? "Copiado" : "Copiar script"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleDownloadTxt(scriptContent, "script")}
              className="border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
            >
              <Download className="w-4 h-4 mr-1" />
              .txt
            </Button>
          </div>
        </TabsContent>
      )}
    </Tabs>
  );
}
