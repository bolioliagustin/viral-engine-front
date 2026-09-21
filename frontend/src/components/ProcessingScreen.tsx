"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { motion, AnimatePresence } from "framer-motion";

// P1 (docs/PROYECTO.md §7/§8): detalle de progreso dentro de current_step,
// lo escribe el worker (W9-B, pendiente) en jobs.progress_detail. Con jobs
// viejos o mientras el worker no lo escriba, queda undefined/null y la
// pantalla cae al mensaje estático de la fase — no se rompe nada.
interface ProgressDetail {
  current?: number;
  total?: number;
  message?: string;
  clips_ready?: number;
}

interface ProcessingScreenProps {
  currentStep?: string;
  progress?: number;
  progressDetail?: ProgressDetail | null;
}

// Pipeline viejo (pre-W2/W9): se mantiene tal cual para que un job en curso
// de un worker viejo (o ya avanzado antes del deploy de esta pantalla) siga
// viéndose exactamente como hoy — nunca se le aplican los pasos nuevos.
const LEGACY_STEPS = [
  { id: "downloading", icon: "📥", label: "Descarga", color: "blue" },
  { id: "transcribing", icon: "🎙️", label: "Transcripción", color: "purple" },
  { id: "analyzing", icon: "🧠", label: "Análisis IA", color: "green" },
  { id: "clipping", icon: "✂️", label: "Clips", color: "orange" },
  { id: "generating", icon: "✨", label: "Contenido", color: "pink" },
];

// P1: pipeline en dos fases (docs/PROYECTO.md §7) — transcript → clasificar
// → Pasada A (candidatos) → evaluar cada candidato (descarga + Whisper +
// Juez) → rankear → entregar (copy + Clip) los Momentos finalistas. Pasos
// en español, sin jerga interna (CONTEXT.md: Candidato, Momento, Juez,
// Clip) — nada de "Pasada A" ni "evaluating" en la UI.
const NEW_STEPS = [
  { id: "transcribing", icon: "🎙️", label: "Transcripción", color: "purple" },
  { id: "classifying", icon: "🏷️", label: "Clasificación", color: "blue" },
  { id: "analyzing", icon: "🧠", label: "Momentos", color: "green" },
  { id: "evaluating", icon: "🔍", label: "Evaluación", color: "orange" },
  { id: "ranking", icon: "🏆", label: "Selección", color: "pink" },
  { id: "delivering", icon: "✨", label: "Generación", color: "purple" },
  { id: "finalizing", icon: "🎬", label: "Cierre", color: "blue" },
];

const STEP_DESCRIPTIONS: Record<string, string> = {
  // Legacy
  downloading: "Extrayendo pista de audio del video...",
  clipping: "Recortando clips con precisión quirúrgica...",
  generating: "Generando copy optimizado para cada plataforma...",
  // Nuevos (P1)
  transcribing: "Creando el mapa de texto del video...",
  classifying: "Identificando el tipo de contenido...",
  analyzing: "Buscando los mejores momentos del video...",
  evaluating: "Evaluando cada candidato a fondo (descarga, transcripción y Juez)...",
  ranking: "El Juez elige los Momentos finales...",
  delivering: "Generando el copy y renderizando cada Clip...",
  finalizing: "Últimos detalles antes de mostrarte todo...",
  completed: "¡Procesamiento completado!",
};

// Estimación gruesa (no medida) de segundos por ítem, solo para dar una
// referencia de tiempo restante en las fases que traen `total` — a
// propósito simple, sin telemetría real detrás.
const ESTIMATED_SEC_PER_ITEM: Record<string, number> = {
  evaluating: 20,
  delivering: 25,
};
const STATIC_TIME_HINTS: Record<string, string> = {
  transcribing: "~1 min",
  classifying: "menos de 1 min",
  analyzing: "~1 min",
  ranking: "menos de 1 min",
  finalizing: "menos de 1 min",
};

function estimateRemaining(step: string, detail?: ProgressDetail | null): string | null {
  const perItem = ESTIMATED_SEC_PER_ITEM[step];
  if (perItem && detail?.total && detail.current !== undefined) {
    const remaining = Math.max(0, detail.total - detail.current);
    const totalSec = remaining * perItem;
    if (totalSec < 60) return "menos de 1 min";
    return `~${Math.round(totalSec / 60)} min`;
  }
  return STATIC_TIME_HINTS[step] ?? null;
}

export function ProcessingScreen({ currentStep = "transcribing", progress = 0, progressDetail }: ProcessingScreenProps) {
  const isNewPipeline = NEW_STEPS.some((s) => s.id === currentStep);
  const STEPS = isNewPipeline ? NEW_STEPS : LEGACY_STEPS;
  const currentStepIndex = STEPS.findIndex((s) => s.id === currentStep);
  const activeStep = STEPS[currentStepIndex >= 0 ? currentStepIndex : 0];
  const description = (isNewPipeline && progressDetail?.message) || STEP_DESCRIPTIONS[currentStep] || "Procesando...";
  const clipsReady = isNewPipeline ? progressDetail?.clips_ready ?? 0 : 0;
  const timeHint = isNewPipeline ? estimateRemaining(currentStep, progressDetail) : null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="min-h-screen bg-gradient-to-b from-slate-950 to-slate-900 flex flex-col items-center justify-center p-6"
    >
      <div className="max-w-4xl w-full space-y-12">
        {/* Header */}
        <div className="text-center space-y-6">
          <div className="flex justify-center">
            <div className="relative">
              <motion.div
                key={activeStep.id}
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ type: "spring", stiffness: 200, damping: 20 }}
                className={`w-24 h-24 rounded-full bg-gradient-to-br from-${activeStep.color}-500/20 to-purple-500/20 border-2 border-${activeStep.color}-500/50 flex items-center justify-center`}
              >
                <span className="text-5xl">{activeStep.icon}</span>
              </motion.div>
              {/* Spinning ring */}
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                className={`absolute inset-0 rounded-full border-t-2 border-${activeStep.color}-500`}
              />
              {/* Pulse effect */}
              <motion.div
                animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0, 0.5] }}
                transition={{ duration: 2, repeat: Infinity }}
                className={`absolute inset-0 rounded-full bg-${activeStep.color}-500/20 -z-10`}
              />
            </div>
          </div>

          <div className="space-y-2">
            <h1 className="text-4xl font-bold text-white tracking-tight">Analizando tu video</h1>
            <motion.p
              key={description}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="text-slate-400 text-xl font-light"
            >
              {description}
            </motion.p>
            {(clipsReady > 0 || timeHint) && (
              <div className="flex items-center justify-center gap-2 pt-1">
                {clipsReady > 0 && (
                  <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30 text-xs px-2.5 py-1">
                    {clipsReady} {clipsReady === 1 ? "clip listo" : "clips listos"}
                  </Badge>
                )}
                {timeHint && (
                  <span className="text-slate-500 text-sm">Tiempo estimado restante: {timeHint}</span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Progress Timeline */}
        <Card className="bg-slate-900/50 border-slate-700 p-8 backdrop-blur-sm">
          <div className="space-y-8">
            {/* Progress bar */}
            <div className="relative h-3 bg-slate-800 rounded-full overflow-hidden">
              <motion.div
                className="absolute inset-y-0 left-0 bg-gradient-to-r from-purple-500 to-pink-500 rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${progress}%` }}
                transition={{ type: "spring", stiffness: 50, damping: 20 }}
              >
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent animate-shimmer"></div>
              </motion.div>
            </div>

            {/* Steps */}
            <div className="flex justify-between items-center relative px-2">
              {STEPS.map((step, index) => {
                const isActive = currentStepIndex === index;
                const isCompleted = currentStepIndex > index;

                return (
                  <div key={step.id} className="flex flex-col items-center gap-3 z-10 w-20">
                    <motion.div
                      animate={{
                        scale: isActive ? 1.1 : 1,
                        backgroundColor: isActive || isCompleted ? `var(--${step.color}-500)` : "transparent",
                        borderColor: isActive || isCompleted ? `var(--${step.color}-500)` : "var(--slate-700)",
                      }}
                      className={`w-14 h-14 rounded-full flex items-center justify-center border-2 transition-colors duration-300 relative ${
                        !isActive && !isCompleted ? "bg-slate-800" : `bg-${step.color}-500/20`
                      }`}
                    >
                      <motion.span
                        animate={{ scale: isActive ? 1.2 : 1 }}
                        className="text-2xl"
                      >
                        {step.icon}
                      </motion.span>

                      {isActive && (
                        <motion.div
                          layoutId="activeGlow"
                          className={`absolute inset-0 rounded-full bg-${step.color}-500/50 blur-md -z-10`}
                          transition={{ duration: 0.3 }}
                        />
                      )}
                    </motion.div>

                    <span
                      className={`text-xs font-medium text-center transition-colors duration-300 ${
                        isActive ? `text-${step.color}-400` : isCompleted ? "text-slate-300" : "text-slate-600"
                      }`}
                    >
                      {step.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </Card>

        {/* Viral Tips Section */}
        <ViralTipsCarousel />
      </div>
    </motion.div>
  );
}

// Simple tips carousel component
function ViralTipsCarousel() {
  const [currentTipIndex, setCurrentTipIndex] = useState(0);

  const VIRAL_TIPS = [
    "💡 El 'Cringe' genera un 300% más de comentarios. No le tengas miedo a lo incómodo.",
    "🎣 Los primeros 3 segundos deciden si tu clip vive o muere. El gancho es todo.",
    "📈 La consistencia le gana al talento. Programa tus hilos de X con tiempo.",
    "🔥 Un buen hook rompe creencias comunes. Ataca lo que 'todos piensan'.",
    "⏱️ Videos de 15-30s tienen 2x más retención que videos largos.",
    "💬 Pregunta al final del video para forzar engagement en comentarios.",
    "🎭 La vulnerabilidad auténtica genera más conexión que la perfección.",
    "📊 Los datos específicos (números, %) hacen tus argumentos más creíbles.",
  ];

  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentTipIndex((prev) => (prev + 1) % VIRAL_TIPS.length);
    }, 8000); // Change tip every 8 seconds

    return () => clearInterval(interval);
  }, []);

  return (
    <Card className="bg-gradient-to-br from-purple-900/20 to-pink-900/20 border-purple-500/30 p-6">
      <div className="flex items-start gap-4">
        <div className="text-3xl shrink-0">💡</div>
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-purple-300">TIP VIRAL</h3>
          <p className="text-white text-base leading-relaxed transition-opacity duration-500">
            {VIRAL_TIPS[currentTipIndex]}
          </p>
          <div className="flex gap-1 mt-3">
            {VIRAL_TIPS.map((_, index) => (
              <div
                key={index}
                className={`h-1 rounded-full transition-all duration-300 ${
                  index === currentTipIndex
                    ? "w-8 bg-purple-500"
                    : "w-2 bg-slate-700"
                }`}
              />
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}
