"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { redirectToCheckout, redirectToPortal } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";

export default function SettingsPage() {
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [credits, setCredits] = useState<number | null>(null);
  const [plan, setPlan] = useState<string>("free");
  const [subscriptionStatus, setSubscriptionStatus] = useState<string>("free");
  const [actionLoading, setActionLoading] = useState(false);
  const router = useRouter();
  const supabase = createClient();
  const { toast } = useToast();

  const isStarter = plan === "starter" && subscriptionStatus === "active";

  useEffect(() => {
    const fetchUserData = async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) {
        router.push("/login");
        return;
      }
      setUser(user);

      const { data: dbUser } = await supabase
        .from("users")
        .select("credits, subscription_status, plan")
        .eq("id", user.id)
        .single();

      if (dbUser) {
        setCredits(dbUser.credits);
        setPlan(dbUser.plan || "free");
        setSubscriptionStatus(dbUser.subscription_status || "free");
      }
      setLoading(false);
    };

    fetchUserData();
  }, [router, supabase]);

  const handleUpgrade = async () => {
    setActionLoading(true);
    try {
      await redirectToCheckout();
    } catch (e: any) {
      toast({ title: "❌ Error", description: e.message, variant: "destructive" });
      setActionLoading(false);
    }
  };

  const handleManageBilling = async () => {
    setActionLoading(true);
    try {
      await redirectToPortal();
    } catch (e: any) {
      toast({ title: "❌ Error", description: e.message, variant: "destructive" });
      setActionLoading(false);
    }
  };

  if (loading) {
     return (
        <div className="min-h-screen bg-slate-950 text-white flex items-center justify-center">
            <div className="animate-spin w-8 h-8 border-2 border-purple-500 border-t-transparent rounded-full" />
        </div>
     )
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navbar />

      <main className="max-w-4xl mx-auto p-6 md:p-12">
        <h1 className="text-3xl font-bold mb-8">Configuración de Cuenta</h1>

        <div className="grid gap-8">
            {/* Profile Section */}
            <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-8">
                <div className="flex items-start gap-6">
                    <Avatar className="w-20 h-20 border-2 border-white/10">
                        <AvatarImage src={user?.user_metadata?.avatar_url} />
                        <AvatarFallback className="text-2xl">{user?.email?.charAt(0).toUpperCase()}</AvatarFallback>
                    </Avatar>
                    <div className="space-y-1">
                        <h2 className="text-xl font-semibold">{user?.user_metadata?.full_name || "Usuario"}</h2>
                        <p className="text-slate-400">{user?.email}</p>
                        <Badge variant="outline" className="mt-2 border-purple-500/30 text-purple-300 bg-purple-500/10">
                            {user?.app_metadata?.provider || 'Email'}
                        </Badge>
                    </div>
                </div>
            </div>

            {/* Subscription & Credits */}
            <div className="bg-slate-900/50 border border-white/5 rounded-2xl p-8">
                <div className="flex items-center justify-between mb-6">
                  <h3 className="text-xl font-semibold">Suscripción y Créditos</h3>
                  <Link href="/account">
                    <Button variant="ghost" size="sm" className="text-slate-400 hover:text-purple-300 text-xs">
                      Ver detalle →
                    </Button>
                  </Link>
                </div>

                <div className="grid md:grid-cols-2 gap-6">
                    <div className="bg-slate-950/50 p-6 rounded-xl border border-white/5">
                        <p className="text-sm text-slate-400 mb-1">Créditos Disponibles</p>
                        <div className="flex items-baseline gap-2">
                            <span className="text-4xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400">
                                {credits ?? 0}
                            </span>
                            <span className="text-slate-500">créditos</span>
                        </div>
                        {isStarter && (
                          <p className="text-xs text-slate-500 mt-2">Se renuevan mensualmente</p>
                        )}
                    </div>

                    <div className="bg-slate-950/50 p-6 rounded-xl border border-white/5">
                        <p className="text-sm text-slate-400 mb-1">Plan Actual</p>
                        <h4 className="text-2xl font-bold text-white mb-2 capitalize">
                          {isStarter ? "Starter" : "Free"}
                        </h4>
                        {isStarter ? (
                          <>
                            <ul className="text-sm text-slate-400 space-y-1 mb-4">
                              <li className="flex items-center gap-2">✓ 40 créditos / mes</li>
                              <li className="flex items-center gap-2">✓ Videos hasta 5h</li>
                              <li className="flex items-center gap-2">✓ Clips 9:16 para TikTok</li>
                            </ul>
                            <Button
                              onClick={handleManageBilling}
                              disabled={actionLoading}
                              className="w-full bg-white/5 hover:bg-white/10"
                              variant="secondary"
                            >
                              💳 {actionLoading ? "Cargando…" : "Gestionar facturación"}
                            </Button>
                          </>
                        ) : (
                          <>
                            <ul className="text-sm text-slate-400 space-y-2 mb-4">
                              <li className="flex items-center gap-2">✓ 3 créditos de prueba</li>
                              <li className="flex items-center gap-2">✓ Análisis con IA</li>
                              <li className="flex items-center gap-2">✓ Contenido para redes</li>
                            </ul>
                            <Button
                              onClick={handleUpgrade}
                              disabled={actionLoading}
                              className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white"
                            >
                              🚀 {actionLoading ? "Cargando…" : "Mejorar a Starter — $9/mes"}
                            </Button>
                          </>
                        )}
                    </div>
                </div>
            </div>
        </div>
      </main>
    </div>
  );
}
