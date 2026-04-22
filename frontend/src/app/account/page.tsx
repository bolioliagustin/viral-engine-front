"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { redirectToCheckout, redirectToPortal } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";
import { CreditCard, Zap, ArrowRight, RotateCcw } from "lucide-react";

interface UserData {
  credits: number;
  plan: string;
  subscription_status: string;
}

export default function AccountPage() {
  const [userData, setUserData] = useState<UserData | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = createClient();
  const { toast } = useToast();

  const isStarter = userData?.plan === "starter" && userData?.subscription_status === "active";

  useEffect(() => {
    const load = async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();

      if (!user) {
        router.push("/login");
        return;
      }

      const { data } = await supabase
        .from("users")
        .select("credits, plan, subscription_status")
        .eq("id", user.id)
        .single();

      if (data) setUserData(data);
      setLoading(false);
    };

    load();

    // Show toast if coming back from a successful upgrade
    if (searchParams.get("upgrade") === "success") {
      toast({
        title: "🎉 ¡Suscripción activada!",
        description: "Ya tienes 40 créditos disponibles. ¡A crear contenido viral!",
      });
    }
  }, [router, supabase, searchParams, toast]);

  const handleUpgrade = async () => {
    setActionLoading(true);
    try {
      await redirectToCheckout();
    } catch (e: any) {
      toast({
        title: "❌ Error",
        description: e.message,
        variant: "destructive",
      });
      setActionLoading(false);
    }
  };

  const handleManage = async () => {
    setActionLoading(true);
    try {
      await redirectToPortal();
    } catch (e: any) {
      toast({
        title: "❌ Error",
        description: e.message,
        variant: "destructive",
      });
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-white flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />

      <main className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="text-3xl font-bold mb-2">Mi cuenta</h1>
        <p className="text-slate-400 mb-10">Gestiona tu suscripción y créditos</p>

        <div className="grid gap-6">
          {/* ── Plan card ── */}
          <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-8">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <p className="text-sm text-slate-400 uppercase tracking-wider mb-2">Plan actual</p>
                <div className="flex items-center gap-3">
                  <h2 className="text-2xl font-bold capitalize">
                    {isStarter ? "Starter" : "Free"}
                  </h2>
                  {isStarter ? (
                    <Badge className="bg-purple-500/20 text-purple-300 border border-purple-500/30">
                      <Zap className="w-3 h-3 mr-1" />
                      Activo
                    </Badge>
                  ) : (
                    <Badge
                      variant="outline"
                      className="border-slate-700 text-slate-400"
                    >
                      Gratis
                    </Badge>
                  )}
                </div>
                {isStarter && (
                  <p className="text-sm text-slate-500 mt-1">40 créditos / mes</p>
                )}
              </div>

              {isStarter ? (
                <Button
                  variant="outline"
                  className="border-slate-700 hover:border-slate-500 text-slate-300 gap-2"
                  onClick={handleManage}
                  disabled={actionLoading}
                >
                  <CreditCard className="w-4 h-4" />
                  {actionLoading ? "Cargando…" : "Gestionar suscripción"}
                </Button>
              ) : (
                <Button
                  className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 gap-2"
                  onClick={handleUpgrade}
                  disabled={actionLoading}
                >
                  {actionLoading ? (
                    <RotateCcw className="w-4 h-4 animate-spin" />
                  ) : (
                    <ArrowRight className="w-4 h-4" />
                  )}
                  {actionLoading ? "Cargando…" : "Actualizar a Starter — $9/mes"}
                </Button>
              )}
            </div>

            {/* Feature comparison */}
            {!isStarter && (
              <div className="mt-6 p-4 bg-purple-950/20 border border-purple-500/20 rounded-xl text-sm">
                <p className="text-purple-300 font-medium mb-2">
                  ✨ Con Starter obtienes:
                </p>
                <ul className="text-slate-400 space-y-1">
                  <li>• 40 créditos / mes (≈ 10 / semana)</li>
                  <li>• Videos de hasta 5 horas</li>
                  <li>• Clips 9:16 listos para TikTok y Reels</li>
                  <li>• Copy automático para todas las redes</li>
                </ul>
              </div>
            )}
          </div>

          {/* ── Credits card ── */}
          <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-8">
            <p className="text-sm text-slate-400 uppercase tracking-wider mb-4">Créditos disponibles</p>
            <div className="flex items-baseline gap-2">
              <span className="text-5xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                {userData?.credits ?? 0}
              </span>
              <span className="text-slate-500 text-lg">créditos</span>
            </div>
            {isStarter && (
              <p className="text-sm text-slate-500 mt-2">
                Se renuevan automáticamente cada mes con tu suscripción.
              </p>
            )}
            {!isStarter && (
              <p className="text-sm text-slate-500 mt-2">
                Actualiza a Starter para obtener 40 créditos mensuales.
              </p>
            )}

            <Link href="/dashboard" className="inline-block mt-4">
              <Button
                variant="outline"
                className="border-slate-700 hover:border-purple-500 hover:text-purple-300"
              >
                Ir al dashboard →
              </Button>
            </Link>
          </div>

          {/* ── Billing portal note ── */}
          {isStarter && (
            <div className="text-center text-sm text-slate-500">
              <p>
                Para cancelar, cambiar método de pago o ver facturas,{" "}
                <button
                  onClick={handleManage}
                  className="text-purple-400 hover:underline cursor-pointer"
                  disabled={actionLoading}
                >
                  accede al portal de facturación
                </button>
                .
              </p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
