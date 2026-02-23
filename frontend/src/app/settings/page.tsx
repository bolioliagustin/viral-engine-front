"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { useRouter } from "next/navigation";

export default function SettingsPage() {
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [credits, setCredits] = useState<number | null>(null);
  const router = useRouter();
  const supabase = createClient();

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
        .select("credits, subscription_status")
        .eq("id", user.id)
        .single();
      
      if (dbUser) {
        setCredits(dbUser.credits);
      }
      setLoading(false);
    };

    fetchUserData();
  }, [router, supabase]);

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
                <h3 className="text-xl font-semibold mb-6">Suscripción y Créditos</h3>
                
                <div className="grid md:grid-cols-2 gap-6">
                    <div className="bg-slate-950/50 p-6 rounded-xl border border-white/5">
                        <p className="text-sm text-slate-400 mb-1">Créditos Disponibles</p>
                        <div className="flex items-baseline gap-2">
                            <span className="text-4xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-400">
                                {credits}
                            </span>
                            <span className="text-slate-500">créditos</span>
                        </div>
                        <Button className="mt-4 w-full bg-white/5 hover:bg-white/10" variant="secondary">
                            ⚡ Recargar Créditos
                        </Button>
                    </div>

                    <div className="bg-slate-950/50 p-6 rounded-xl border border-white/5">
                        <p className="text-sm text-slate-400 mb-1">Plan Actual</p>
                        <h4 className="text-2xl font-bold text-white mb-2">Free Tier</h4>
                        <ul className="text-sm text-slate-400 space-y-2 mb-4">
                            <li className="flex items-center gap-2">✓ Videos de hasta 10 min</li>
                            <li className="flex items-center gap-2">✓ 720p Export</li>
                            <li className="flex items-center gap-2">✓ Marcas de agua</li>
                        </ul>
                        <Button className="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white">
                            🚀 Mejorar a PRO
                        </Button>
                    </div>
                </div>
            </div>
        </div>
      </main>
    </div>
  );
}
