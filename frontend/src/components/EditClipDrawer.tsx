"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  X,
  Type,
  Palette,
  Music,
  Wand2,
  Info,
  Sparkles,
  Lock,
  Check,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";

type SubtitleStyle = "tiktok_viral" | "clean" | "podcast";
type EditStatus = "draft" | "queued" | "processing" | "completed" | "failed";

interface ClipEdit {
  id: string;
  status: EditStatus;
  overlay_text?: string | null;
  subtitle_style?: SubtitleStyle | null;
  rendered_clip_url?: string | null;
  error_message?: string | null;
  updated_at?: string;
}

interface EditClipDrawerProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  momentIndex: number;
  contentResultId: string;
  clipUrl?: string;
  overlayText?: string;
  /**
   * Notifica al padre cuando un re-render completa con una URL nueva,
   * para que pueda reemplazar el clip mostrado en la card.
   */
  onRendered?: (url: string) => void;
}

/**
 * Fase 3 — skeleton del editor post-clip.
 *
 * Por ahora, todas las acciones de re-render están BLOQUEADAS (esperando
 * endpoint `POST /api/clips/:id/regenerate`). La UI sirve para validar
 * el flujo con usuarios y guardar sus preferencias en `clip_edits`.
 */
export function EditClipDrawer({
  open,
  onOpenChange,
  momentIndex,
  contentResultId,
  clipUrl,
  overlayText,
  onRendered,
}: EditClipDrawerProps) {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("text");
  const [titleDraft, setTitleDraft] = useState(overlayText ?? "");
  const [selectedStyle, setSelectedStyle] =
    useState<SubtitleStyle>("tiktok_viral");

  // Backend wiring state
  const [latestEdit, setLatestEdit] = useState<ClipEdit | null>(null);
  const [loadingEdit, setLoadingEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const isLocked =
    latestEdit?.status === "queued" ||
    latestEdit?.status === "processing";

  // Sync title when overlayText changes
  useEffect(() => {
    if (overlayText) setTitleDraft(overlayText);
  }, [overlayText]);

  // ─── Load latest draft when opening ────────────────────────────────
  useEffect(() => {
    if (!open || !contentResultId) return;
    let cancelled = false;
    (async () => {
      try {
        setLoadingEdit(true);
        const res = await apiFetch(`/api/clips/${contentResultId}/edit`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        const edit: ClipEdit | null = data.edit ?? null;
        setLatestEdit(edit);
        if (edit) {
          if (edit.overlay_text) setTitleDraft(edit.overlay_text);
          if (edit.subtitle_style) setSelectedStyle(edit.subtitle_style);
          // Si ya hay un re-render completado, notificar al padre para
          // que reemplace el clip mostrado en la card.
          if (edit.status === "completed" && edit.rendered_clip_url) {
            onRendered?.(edit.rendered_clip_url);
          }
        }
      } catch (e) {
        console.warn("Failed to load latest edit", e);
      } finally {
        if (!cancelled) setLoadingEdit(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, contentResultId]);

  // ─── Poll status while queued/processing ──────────────────────────
  // Mientras el drawer esté abierto y el edit esté en cola/render,
  // refrescamos cada 4s para ver cuándo completa o falla.
  useEffect(() => {
    if (!open || !contentResultId) return;
    if (!latestEdit) return;
    if (latestEdit.status !== "queued" && latestEdit.status !== "processing")
      return;

    let cancelled = false;
    const poll = async () => {
      try {
        const res = await apiFetch(`/api/clips/${contentResultId}/edit`);
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        const edit: ClipEdit | null = data.edit ?? null;
        if (edit) {
          setLatestEdit((prev) => {
            // Toast cuando transiciona a estado terminal
            if (prev && prev.status !== edit.status) {
              if (edit.status === "completed") {
                toast({
                  title: "🎉 Clip listo",
                  description: "Tu nuevo clip ya reemplaza al original.",
                });
                if (edit.rendered_clip_url) {
                  onRendered?.(edit.rendered_clip_url);
                }
              } else if (edit.status === "failed") {
                toast({
                  title: "❌ Re-render falló",
                  description: edit.error_message || "Intentá de nuevo.",
                  variant: "destructive",
                });
              }
            }
            return edit;
          });
        }
      } catch (e) {
        // silencio - reintentaremos en el próximo tick
      }
    };

    const id = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // Re-suscribe sólo cuando cambia status o id (no en cada setLatestEdit)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, contentResultId, latestEdit?.id, latestEdit?.status]);

  // ─── Save draft ────────────────────────────────────────────────────
  const handleSaveDraft = async () => {
    try {
      setSaving(true);
      const res = await apiFetch(`/api/clips/${contentResultId}/edit`, {
        method: "POST",
        body: JSON.stringify({
          overlay_text: titleDraft.trim() || null,
          subtitle_style: selectedStyle,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setLatestEdit(data.edit);
      setSavedAt(Date.now());
      toast({
        title: "✅ Cambios guardados",
        description: "Tu borrador quedó listo para regenerar.",
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Error desconocido";
      toast({
        title: "❌ No se pudo guardar",
        description: msg,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  // ─── Queue regenerate ──────────────────────────────────────────────
  const handleRegenerate = async () => {
    try {
      setRegenerating(true);
      // Save first (always persist current draft)
      const saveRes = await apiFetch(`/api/clips/${contentResultId}/edit`, {
        method: "POST",
        body: JSON.stringify({
          overlay_text: titleDraft.trim() || null,
          subtitle_style: selectedStyle,
        }),
      });
      const saveData = await saveRes.json();
      if (!saveRes.ok) throw new Error(saveData.error || `HTTP ${saveRes.status}`);

      // Then queue
      const qRes = await apiFetch(
        `/api/clips/${contentResultId}/regenerate`,
        { method: "POST" }
      );
      const qData = await qRes.json();
      if (!qRes.ok) throw new Error(qData.error || `HTTP ${qRes.status}`);
      setLatestEdit(qData.edit);
      toast({
        title: "🚀 Re-render en cola",
        description:
          "El worker tomará tu clip pronto. Te avisaremos cuando esté listo.",
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Error desconocido";
      toast({
        title: "❌ No se pudo encolar",
        description: msg,
        variant: "destructive",
      });
    } finally {
      setRegenerating(false);
    }
  };

  // ESC to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  // Lock body scroll while drawer open
  useEffect(() => {
    if (open) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[100]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => onOpenChange(false)}
          />

          {/* Drawer */}
          <motion.aside
            key="drawer"
            className="fixed top-0 right-0 bottom-0 w-full md:w-[600px] bg-slate-950 border-l border-slate-800 z-[101] flex flex-col shadow-2xl"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 28, stiffness: 300 }}
          >
            {/* Header */}
            <div className="flex items-center justify-between p-5 border-b border-slate-800 bg-gradient-to-r from-purple-950/30 to-pink-950/30">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-purple-500/20 border border-purple-500/40 flex items-center justify-center">
                  <Wand2 className="w-5 h-5 text-purple-300" />
                </div>
                <div>
                  <h3 className="text-white font-bold text-lg leading-none">
                    Editor de clip
                  </h3>
                  <p className="text-slate-400 text-xs mt-1">
                    Momento #{String(momentIndex).padStart(2, "0")} — retoca
                    título, subs y estilo
                  </p>
                </div>
              </div>
              <button
                onClick={() => onOpenChange(false)}
                className="w-9 h-9 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-white flex items-center justify-center transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Status banner — refleja estado real del clip_edit */}
            {latestEdit?.status === "queued" && (
              <div className="px-5 py-2.5 bg-blue-500/10 border-b border-blue-500/20 flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-blue-400 shrink-0 animate-spin" />
                <span className="text-xs text-blue-200">
                  <strong className="font-semibold">En cola</strong> · el worker
                  tomará tu clip pronto. Volvé en unos minutos.
                </span>
              </div>
            )}
            {latestEdit?.status === "processing" && (
              <div className="px-5 py-2.5 bg-purple-500/10 border-b border-purple-500/20 flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-purple-400 shrink-0 animate-spin" />
                <span className="text-xs text-purple-200">
                  <strong className="font-semibold">Procesando…</strong> el
                  re-render está en curso.
                </span>
              </div>
            )}
            {latestEdit?.status === "completed" && (
              <div className="px-5 py-2.5 bg-emerald-500/10 border-b border-emerald-500/20 flex items-center gap-2">
                <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                <span className="text-xs text-emerald-200">
                  <strong className="font-semibold">Listo</strong> · el clip
                  editado ya reemplazó al original en la card
                  {latestEdit.rendered_clip_url && (
                    <>
                      {" "}·{" "}
                      <a
                        href={latestEdit.rendered_clip_url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline hover:text-emerald-100"
                      >
                        abrir en pestaña
                      </a>
                    </>
                  )}
                </span>
              </div>
            )}
            {latestEdit?.status === "failed" && (
              <div className="px-5 py-2.5 bg-red-500/10 border-b border-red-500/20 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                <span className="text-xs text-red-200">
                  <strong className="font-semibold">Falló</strong> ·{" "}
                  {latestEdit.error_message || "intentá regenerar de nuevo."}
                </span>
              </div>
            )}
            {!latestEdit && (
              <div className="px-5 py-2.5 bg-amber-500/10 border-b border-amber-500/20 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-amber-400 shrink-0" />
                <span className="text-xs text-amber-200">
                  <strong className="font-semibold">Beta</strong> · el worker
                  re-render aún no está activo. Guardá preferencias y encolá; se
                  aplicarán cuando se lance.
                </span>
              </div>
            )}

            {/* Tabs */}
            <Tabs
              value={activeTab}
              onValueChange={setActiveTab}
              className="flex-1 flex flex-col overflow-hidden"
            >
              <TabsList className="w-full bg-slate-900/60 border-b border-slate-800 flex p-0 h-12 rounded-none">
                <TabsTrigger
                  value="text"
                  className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-purple-500 data-[state=active]:bg-slate-900 data-[state=active]:text-purple-300"
                >
                  <Type className="w-4 h-4 mr-1.5" />
                  Texto
                </TabsTrigger>
                <TabsTrigger
                  value="style"
                  className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-pink-500 data-[state=active]:bg-slate-900 data-[state=active]:text-pink-300"
                >
                  <Palette className="w-4 h-4 mr-1.5" />
                  Estilo
                </TabsTrigger>
                <TabsTrigger
                  value="audio"
                  className="flex-1 h-full rounded-none border-b-2 border-transparent data-[state=active]:border-emerald-500 data-[state=active]:bg-slate-900 data-[state=active]:text-emerald-300"
                >
                  <Music className="w-4 h-4 mr-1.5" />
                  Audio
                </TabsTrigger>
              </TabsList>

              {/* ══ TEXTO ══ */}
              <TabsContent
                value="text"
                className="flex-1 overflow-y-auto custom-scrollbar p-5 space-y-5 mt-0"
              >
                <div>
                  <label className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-2 block">
                    Título en el video (max 4 palabras, UPPERCASE)
                  </label>
                  <Input
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value.toUpperCase())}
                    placeholder="EJ: NO VAS A CREER"
                    maxLength={35}
                    disabled={isLocked}
                    className="bg-slate-900 border-slate-800 text-white disabled:opacity-60"
                  />
                  <p className="text-xs text-slate-500 mt-1.5 flex items-center gap-1">
                    <Info className="w-3 h-3" />
                    {titleDraft.length}/35 caracteres ·{" "}
                    {titleDraft.trim().split(/\s+/).filter(Boolean).length} palabras
                  </p>
                </div>

                <div className="space-y-2">
                  <label className="text-xs uppercase tracking-wider text-slate-400 font-semibold block">
                    Subtítulos
                  </label>
                  <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4 flex items-start gap-3">
                    <Lock className="w-4 h-4 text-slate-600 shrink-0 mt-0.5" />
                    <div>
                      <p className="text-sm text-slate-300 font-medium">
                        Edición palabra por palabra
                      </p>
                      <p className="text-xs text-slate-500 mt-1">
                        Pronto: corregir palabras mal transcritas, ajustar
                        timing, cambiar agrupaciones.
                      </p>
                    </div>
                  </div>
                </div>
              </TabsContent>

              {/* ══ ESTILO ══ */}
              <TabsContent
                value="style"
                className="flex-1 overflow-y-auto custom-scrollbar p-5 space-y-5 mt-0"
              >
                <div>
                  <label className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-3 block">
                    Preset de estilo
                  </label>
                  <div className="grid gap-3">
                    {[
                      {
                        id: "tiktok_viral" as const,
                        name: "TikTok Viral",
                        desc: "Blanco bold + borde negro. El clásico viral.",
                        tag: "🔥 Recomendado",
                        color: "border-pink-500/40 bg-pink-500/5",
                      },
                      {
                        id: "clean" as const,
                        name: "Clean",
                        desc: "Fondo semi-transparente. Para business y entrevistas.",
                        tag: "Formal",
                        color: "border-blue-500/40 bg-blue-500/5",
                      },
                      {
                        id: "podcast" as const,
                        name: "Podcast",
                        desc: "Amarillo clásico. Energía de podcast / vlog.",
                        tag: "Energético",
                        color: "border-yellow-500/40 bg-yellow-500/5",
                      },
                    ].map((s) => (
                      <button
                        key={s.id}
                        onClick={() => setSelectedStyle(s.id)}
                        disabled={isLocked}
                        className={`text-left p-4 rounded-lg border transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                          selectedStyle === s.id
                            ? `${s.color} ring-2 ring-purple-500/40`
                            : "border-slate-800 bg-slate-900/40 hover:border-slate-700"
                        }`}
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-white font-semibold text-sm">
                            {s.name}
                          </span>
                          <Badge
                            variant="outline"
                            className="text-[10px] bg-slate-900 border-slate-700 text-slate-400"
                          >
                            {s.tag}
                          </Badge>
                        </div>
                        <p className="text-xs text-slate-400">{s.desc}</p>
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-3 block">
                    Posición del título
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    {["Arriba", "Centro", "Abajo"].map((pos, i) => (
                      <button
                        key={pos}
                        disabled
                        className="p-3 rounded-lg border border-slate-800 bg-slate-900/40 text-slate-500 text-sm opacity-50 cursor-not-allowed"
                      >
                        {pos}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-slate-600 mt-2 flex items-center gap-1">
                    <Lock className="w-3 h-3" />
                    Disponible al lanzamiento del editor
                  </p>
                </div>
              </TabsContent>

              {/* ══ AUDIO ══ */}
              <TabsContent
                value="audio"
                className="flex-1 overflow-y-auto custom-scrollbar p-5 space-y-5 mt-0"
              >
                <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-5 text-center">
                  <Music className="w-8 h-8 text-emerald-400 mx-auto mb-3" />
                  <h4 className="text-white font-semibold mb-1">
                    Música y audio
                  </h4>
                  <p className="text-xs text-slate-400 max-w-xs mx-auto">
                    Próximamente: recortar start/end del clip, agregar música
                    de fondo libre de copyright, normalizar volumen.
                  </p>
                  <Badge className="mt-3 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    En desarrollo
                  </Badge>
                </div>

                <div className="space-y-2">
                  <label className="text-xs uppercase tracking-wider text-slate-400 font-semibold block">
                    Recortar clip (próximamente)
                  </label>
                  <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4 opacity-50">
                    <div className="h-8 bg-slate-800 rounded relative overflow-hidden">
                      <div className="absolute inset-y-0 left-[10%] right-[15%] bg-purple-500/40 border-x-2 border-purple-400" />
                    </div>
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1.5 font-mono">
                      <span>0:00</span>
                      <span>0:30</span>
                    </div>
                  </div>
                </div>
              </TabsContent>
            </Tabs>

            {/* Footer */}
            <div className="p-4 border-t border-slate-800 bg-slate-950 flex flex-col gap-2">
              {savedAt && (
                <p className="text-[11px] text-emerald-400 flex items-center gap-1">
                  <Check className="w-3 h-3" />
                  Borrador guardado · {new Date(savedAt).toLocaleTimeString()}
                </p>
              )}
              <div className="flex gap-3">
                <Button
                  variant="outline"
                  className="flex-1 border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
                  onClick={() => onOpenChange(false)}
                  disabled={saving || regenerating}
                >
                  Cerrar
                </Button>
                <Button
                  variant="outline"
                  className="flex-1 border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800"
                  onClick={handleSaveDraft}
                  disabled={
                    saving || regenerating || isLocked || loadingEdit ||
                    !titleDraft.trim()
                  }
                >
                  {saving ? (
                    <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
                  ) : (
                    <Check className="w-4 h-4 mr-1.5" />
                  )}
                  Guardar
                </Button>
                <Button
                  className="flex-1 bg-gradient-to-r from-purple-500 to-pink-500 text-white hover:from-purple-600 hover:to-pink-600 disabled:opacity-50 disabled:cursor-not-allowed"
                  onClick={handleRegenerate}
                  disabled={
                    regenerating || saving || isLocked || loadingEdit ||
                    !titleDraft.trim()
                  }
                  title={
                    isLocked
                      ? "Ya hay un re-render en curso"
                      : "Encolar re-render"
                  }
                >
                  {regenerating ? (
                    <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
                  ) : (
                    <Sparkles className="w-4 h-4 mr-1.5" />
                  )}
                  Regenerar
                </Button>
              </div>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
