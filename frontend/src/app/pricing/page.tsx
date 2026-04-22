"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { Check, Zap, Sparkles, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Navbar } from "@/components/Navbar";
import { redirectToCheckout } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { useToast } from "@/hooks/use-toast";

// ─── Plan definitions ────────────────────────────────────────────────────────

const FREE_FEATURES = [
  "3 créditos para probar",
  "5 clips por video",
  "Transcripción con IA",
  "Contenido para redes sociales",
  "Análisis de viralidad",
];

const STARTER_FEATURES = [
  "40 créditos / mes (≈ 10 / semana)",
  "Videos de hasta 5 horas",
  "5 clips virales por video",
  "Clips 9:16 listos para TikTok / Reels",
  "Transcripción automática",
  "Copy para Twitter, TikTok y LinkedIn",
  "Análisis de viralidad con IA",
  "Soporte prioritario",
];

// ─── Component ───────────────────────────────────────────────────────────────

export default function PricingPage() {
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const supabase = createClient();
  const { toast } = useToast();

  const handleUpgrade = async () => {
    setLoading(true);
    try {
      // Make sure user is logged in
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (!session) {
        router.push("/login?redirect=/pricing");
        return;
      }

      await redirectToCheckout();
      // redirectToCheckout() navigates away — no need to setLoading(false)
    } catch (error: any) {
      toast({
        title: "❌ Error al iniciar pago",
        description: error.message || "Intenta de nuevo.",
        variant: "destructive",
      });
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />

      <main className="max-w-5xl mx-auto px-6 py-20">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-center mb-16"
        >
          <Badge
            variant="outline"
            className="mb-4 border-purple-500/30 text-purple-300 bg-purple-500/10"
          >
            <Sparkles className="w-3 h-3 mr-1" />
            Lanzamiento Early Adopter
          </Badge>
          <h1 className="text-4xl md:text-5xl font-bold mb-4 bg-gradient-to-r from-white to-slate-400 bg-clip-text text-transparent">
            Precios simples y transparentes
          </h1>
          <p className="text-slate-400 text-lg max-w-xl mx-auto">
            Convierte tus podcasts y charlas en clips virales listos para
            publicar. Sin contratos, cancela cuando quieras.
          </p>
        </motion.div>

        {/* Plans grid */}
        <div className="grid md:grid-cols-2 gap-8 max-w-3xl mx-auto">
          {/* ── Free Plan ── */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="bg-slate-900/50 border border-white/5 rounded-2xl p-8 flex flex-col"
          >
            <div className="mb-6">
              <h2 className="text-xl font-bold mb-1">Free</h2>
              <div className="flex items-baseline gap-1 mt-3">
                <span className="text-4xl font-bold">$0</span>
                <span className="text-slate-500 text-sm">/ mes</span>
              </div>
              <p className="text-slate-500 text-sm mt-2">
                Para probar la herramienta
              </p>
            </div>

            <ul className="space-y-3 flex-1 mb-8">
              {FREE_FEATURES.map((f) => (
                <li key={f} className="flex items-start gap-3 text-sm text-slate-300">
                  <Check className="w-4 h-4 text-slate-500 shrink-0 mt-0.5" />
                  {f}
                </li>
              ))}
            </ul>

            <Link href="/dashboard">
              <Button
                variant="outline"
                className="w-full border-slate-700 hover:border-slate-500 text-slate-300"
              >
                Empezar gratis
              </Button>
            </Link>
          </motion.div>

          {/* ── Starter Plan ── */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="relative bg-gradient-to-b from-purple-950/40 to-slate-900/50 border border-purple-500/30 rounded-2xl p-8 flex flex-col shadow-xl shadow-purple-900/20"
          >
            {/* Popular badge */}
            <div className="absolute -top-3 left-1/2 -translate-x-1/2">
              <Badge className="bg-gradient-to-r from-purple-600 to-pink-600 text-white border-0 px-4 py-1 text-xs font-semibold">
                ⭐ Más popular
              </Badge>
            </div>

            <div className="mb-6">
              <h2 className="text-xl font-bold mb-1 flex items-center gap-2">
                Starter
                <Zap className="w-4 h-4 text-yellow-400" />
              </h2>
              <div className="flex items-baseline gap-1 mt-3">
                <span className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                  $9
                </span>
                <span className="text-slate-400 text-sm">USD / mes</span>
              </div>
              <p className="text-slate-400 text-sm mt-2">
                Para podcasters y coaches activos
              </p>
            </div>

            <ul className="space-y-3 flex-1 mb-8">
              {STARTER_FEATURES.map((f) => (
                <li key={f} className="flex items-start gap-3 text-sm text-slate-200">
                  <Check className="w-4 h-4 text-purple-400 shrink-0 mt-0.5" />
                  {f}
                </li>
              ))}
            </ul>

            <Button
              onClick={handleUpgrade}
              disabled={loading}
              className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white font-semibold shadow-lg shadow-purple-900/30"
            >
              {loading ? (
                <>
                  <span className="animate-spin mr-2">⏳</span>
                  Cargando…
                </>
              ) : (
                <>
                  Suscribirme ahora
                  <ArrowRight className="w-4 h-4 ml-2" />
                </>
              )}
            </Button>

            <p className="text-center text-xs text-slate-500 mt-3">
              Cancela en cualquier momento · Sin permanencia
            </p>
          </motion.div>
        </div>

        {/* FAQ / reassurance */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.4 }}
          className="mt-20 text-center text-slate-500 text-sm space-y-2"
        >
          <p>💳 Pagos seguros procesados por Stripe</p>
          <p>
            ¿Preguntas?{" "}
            <a
              href="mailto:hola@viralengine.app"
              className="text-purple-400 hover:underline"
            >
              Contáctanos
            </a>
          </p>
        </motion.div>
      </main>
    </div>
  );
}
