"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { Check, Copy } from "lucide-react";

interface CopyTabsProps {
  twitterContent?: string;
  tiktokContent?: string;
  linkedinContent?: string;
  scriptContent?: string;
}

export function CopyTabs({ twitterContent, tiktokContent, linkedinContent, scriptContent }: CopyTabsProps) {
  const { toast } = useToast();
  const [copiedTab, setCopiedTab] = useState<string | null>(null);

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

  return (
    <Tabs defaultValue="twitter" className="w-full flex flex-col h-full">
      <TabsList className="w-full bg-slate-950/50 border-b border-slate-800 flex justify-start gap-0 p-0 rounded-none h-12 overflow-x-auto">
        {twitterContent && (
          <TabsTrigger 
            value="twitter" 
            className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-slate-900 data-[state=active]:text-blue-400 hover:bg-slate-900/50 transition-colors"
          >
            🐦 Twitter
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

      <div className="flex-1 bg-slate-925 relative group">
        
        {/* TWITTER PREVIEW */}
        {twitterContent && (
          <TabsContent value="twitter" className="mt-0 h-full p-4">
             <div className="max-h-[300px] overflow-y-auto pr-2 custom-scrollbar space-y-4">
                {/* Simplified Thread View */}
                {twitterContent.split('\n\n').map((tweet, idx) => (
                  <div key={idx} className="flex gap-3 relative pb-8 border-l-2 border-slate-800 ml-10 pl-8 last:border-0 last:pb-0">
                    <div className="absolute -left-[17px] top-0 w-8 h-8 rounded-full bg-blue-500/10 border border-blue-500/30 flex items-center justify-center text-xs text-blue-400 font-bold shadow-sm backdrop-blur-sm z-10 box-content">
                       {idx + 1}
                    </div>
                    <p className="text-slate-300 text-sm whitespace-pre-wrap leading-relaxed pt-1">
                      {tweet}
                    </p>
                  </div>
                ))}
            </div>
            {/* Floating Copy Button */}
            <div className="absolute bottom-4 right-4">
               <Button
                 size="sm"
                 onClick={() => handleCopy(twitterContent, "Twitter")}
                 className={`shadow-lg transition-all duration-300 ${copiedTab === "Twitter" ? "bg-green-500 text-white hover:bg-green-600" : "bg-blue-600 text-white hover:bg-blue-700"}`}
               >
                 {copiedTab === "Twitter" ? <Check className="w-4 h-4 mr-1" /> : <Copy className="w-4 h-4 mr-1" />}
                 {copiedTab === "Twitter" ? "Copiado" : "Copiar Hilo"}
               </Button>
            </div>
          </TabsContent>
        )}

        {/* Other tabs follow same pattern but simplified for brevity in this replace block */}
        {/* TIKTOK */}
        {tiktokContent && (
           <TabsContent value="tiktok" className="mt-0 h-full p-4 relative">
              <div className="max-h-[500px] overflow-y-auto pr-2 custom-scrollbar">
                 <div className="bg-slate-950 rounded-lg p-4 border border-slate-800 font-mono text-sm text-slate-300 relative">
                    <div className="absolute top-2 right-2 text-pink-500 text-xs">Caption Preview</div>
                    {tiktokContent}
                 </div>
              </div>
              <div className="absolute bottom-4 right-4">
                 <Button
                   size="sm"
                   onClick={() => handleCopy(tiktokContent, "TikTok")}
                   className={`shadow-lg transition-all duration-300 ${copiedTab === "TikTok" ? "bg-green-500 hover:bg-green-600" : "bg-pink-600 hover:bg-pink-700"} text-white`}
                 >
                   {copiedTab === "TikTok" ? <Check className="w-4 h-4 mr-1" /> : <Copy className="w-4 h-4 mr-1" />}
                   {copiedTab === "TikTok" ? "Copiado" : "Copiar Caption"}
                 </Button>
              </div>
           </TabsContent>
        )}

        {/* LINKEDIN */}
        {linkedinContent && (
           <TabsContent value="linkedin" className="mt-0 h-full p-4 relative">
              <div className="max-h-[500px] overflow-y-auto pr-2 custom-scrollbar">
                 <div className="bg-white rounded-lg p-4 text-slate-900 text-sm leading-relaxed shadow-sm">
                    {linkedinContent.split('\n\n').map((p, i) => (
                       <p key={i} className="mb-3 last:mb-0">{p}</p>
                    ))}
                 </div>
              </div>
              <div className="absolute bottom-4 right-4">
                 <Button
                   size="sm"
                   onClick={() => handleCopy(linkedinContent, "LinkedIn")}
                   className={`shadow-lg transition-all duration-300 ${copiedTab === "LinkedIn" ? "bg-green-500 hover:bg-green-600" : "bg-blue-700 hover:bg-blue-800"} text-white`}
                 >
                   {copiedTab === "LinkedIn" ? <Check className="w-4 h-4 mr-1" /> : <Copy className="w-4 h-4 mr-1" />}
                   {copiedTab === "LinkedIn" ? "Copiado" : "Copiar Post"}
                 </Button>
              </div>
           </TabsContent>
        )}

        {/* SCRIPT */}
        {scriptContent && (
           <TabsContent value="script" className="mt-0 h-full p-4 relative">
              <div className="max-h-[500px] overflow-y-auto pr-2 custom-scrollbar space-y-3">
                 {scriptContent.split('\n\n').map((section, idx) => {
                   const timeMatch = section.match(/\[(\d+-\d+s)\]/);
                   const isTimed = timeMatch !== null;
                   return (
                     <div key={idx} className={`text-sm ${isTimed ? 'bg-slate-900/50 p-3 rounded border-l-2 border-orange-500' : ''}`}>
                       {isTimed && <span className="text-orange-400 font-mono text-xs block mb-1">{timeMatch[1]}</span>}
                       <span className="text-slate-300 whitespace-pre-wrap">{section.replace(/\[\d+-\d+s\]/, '').trim()}</span>
                     </div>
                   );
                 })}
              </div>
              <div className="absolute bottom-4 right-4">
                 <Button
                   size="sm"
                   onClick={() => handleCopy(scriptContent, "Script")}
                   className={`shadow-lg transition-all duration-300 ${copiedTab === "Script" ? "bg-green-500 hover:bg-green-600" : "bg-orange-600 hover:bg-orange-700"} text-white`}
                 >
                   {copiedTab === "Script" ? <Check className="w-4 h-4 mr-1" /> : <Copy className="w-4 h-4 mr-1" />}
                   {copiedTab === "Script" ? "Copiado" : "Copiar Script"}
                 </Button>
              </div>
           </TabsContent>
        )}
      </div>
    </Tabs>
  );
}
