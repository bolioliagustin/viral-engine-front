"use client";

import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { 
  Sparkles, 
  Zap, 
  Copy, 
  Video, 
  ArrowRight, 
  Play,
  CheckCircle2,
  Twitter,
  Linkedin,
  Youtube
} from "lucide-react";
import Link from "next/link";


// ===== ANIMATED GRADIENT MESH BACKGROUND =====
const GradientMesh = () => {
  return (
    <div className="absolute inset-0 overflow-hidden -z-10">
      {/* Base gradient */}
      <div className="absolute inset-0 bg-slate-950" />
      
      {/* Animated gradient orbs */}
      <motion.div
        className="absolute -top-1/4 -left-1/4 w-[800px] h-[800px] rounded-full"
        style={{
          background: "radial-gradient(circle, rgba(139, 92, 246, 0.15) 0%, transparent 70%)",
        }}
        animate={{
          x: [0, 100, 0],
          y: [0, 50, 0],
          scale: [1, 1.2, 1],
        }}
        transition={{ duration: 20, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute -bottom-1/4 -right-1/4 w-[900px] h-[900px] rounded-full"
        style={{
          background: "radial-gradient(circle, rgba(6, 182, 212, 0.12) 0%, transparent 70%)",
        }}
        animate={{
          x: [0, -80, 0],
          y: [0, -60, 0],
          scale: [1.2, 1, 1.2],
        }}
        transition={{ duration: 25, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full"
        style={{
          background: "radial-gradient(circle, rgba(236, 72, 153, 0.08) 0%, transparent 60%)",
        }}
        animate={{
          scale: [1, 1.3, 1],
          opacity: [0.5, 0.8, 0.5],
        }}
        transition={{ duration: 15, repeat: Infinity, ease: "easeInOut" }}
      />

      {/* Grid overlay */}
      <div 
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)`,
          backgroundSize: '60px 60px',
        }}
      />

      {/* Noise texture (simulated) */}
      <div className="absolute inset-0 opacity-[0.015] mix-blend-overlay bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iMzAwIj48ZmlsdGVyIGlkPSJhIiB4PSIwIiB5PSIwIj48ZmVUdXJidWxlbmNlIGJhc2VGcmVxdWVuY3k9Ii43NSIgc3RpdGNoVGlsZXM9InN0aXRjaCIgdHlwZT0iZnJhY3RhbE5vaXNlIi8+PGZlQ29sb3JNYXRyaXggdHlwZT0ic2F0dXJhdGUiIHZhbHVlcz0iMCIvPjwvZmlsdGVyPjxyZWN0IHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGZpbHRlcj0idXJsKCNhKSIvPjwvc3ZnPg==')]" />
    </div>
  );
};

// ===== ANIMATED TEXT REVEAL =====
const AnimatedHeadline = () => {
  const words = ["Transforma", "videos", "en", "contenido", "viral"];
  
  return (
    <h1 className="text-5xl md:text-7xl lg:text-8xl font-bold text-white leading-[1.1] tracking-tight">
      {words.map((word, i) => (
        <motion.span
          key={i}
          initial={{ opacity: 0, y: 50, filter: "blur(10px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          transition={{ 
            delay: 0.3 + i * 0.1, 
            duration: 0.8, 
            ease: [0.25, 0.46, 0.45, 0.94] 
          }}
          className={`inline-block mr-4 ${
            word === "viral" 
              ? "bg-gradient-to-r from-purple-400 via-pink-400 to-cyan-400 bg-clip-text text-transparent" 
              : ""
          }`}
        >
          {word}
        </motion.span>
      ))}
    </h1>
  );
};

// ===== FEATURE CARD =====
const FeatureCard = ({ 
  icon: Icon, 
  title, 
  description, 
  gradient,
  delay 
}: { 
  icon: React.ElementType; 
  title: string; 
  description: string; 
  gradient: string;
  delay: number;
}) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-100px" }}
      transition={{ delay, duration: 0.6, ease: "easeOut" }}
      whileHover={{ 
        y: -8, 
        transition: { duration: 0.3 } 
      }}
      className="group relative"
    >
      {/* Glow effect on hover */}
      <div className={`absolute -inset-0.5 ${gradient} rounded-3xl blur-xl opacity-0 group-hover:opacity-30 transition-opacity duration-500`} />
      
      <div className="relative backdrop-blur-xl bg-white/[0.03] border border-white/[0.08] rounded-3xl p-8 h-full overflow-hidden">
        {/* Icon container */}
        <div className={`w-14 h-14 rounded-2xl ${gradient} p-[1px] mb-6`}>
          <div className="w-full h-full rounded-2xl bg-slate-950 flex items-center justify-center">
            <Icon className="w-6 h-6 text-white" />
          </div>
        </div>
        
        <h3 className="text-xl font-semibold text-white mb-3 group-hover:text-transparent group-hover:bg-gradient-to-r group-hover:from-white group-hover:to-purple-200 group-hover:bg-clip-text transition-all duration-300">
          {title}
        </h3>
        <p className="text-slate-400 leading-relaxed">
          {description}
        </p>
      </div>
    </motion.div>
  );
};

// ===== STEP INDICATOR =====
const StepItem = ({ 
  number, 
  title, 
  description, 
  delay 
}: { 
  number: string; 
  title: string; 
  description: string; 
  delay: number;
}) => {
  return (
    <motion.div
      initial={{ opacity: 0, x: -30 }}
      whileInView={{ opacity: 1, x: 0 }}
      viewport={{ once: true }}
      transition={{ delay, duration: 0.6 }}
      className="flex gap-6 items-start"
    >
      <div className="relative">
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold text-lg shadow-lg shadow-purple-500/25">
          {number}
        </div>
        {/* Connecting line */}
        <div className="absolute left-1/2 top-14 w-px h-16 bg-gradient-to-b from-purple-500/50 to-transparent -translate-x-1/2 hidden md:block" />
      </div>
      <div className="flex-1 pb-12">
        <h3 className="text-xl font-semibold text-white mb-2">{title}</h3>
        <p className="text-slate-400">{description}</p>
      </div>
    </motion.div>
  );
};

// ===== MAIN LANDING PAGE =====
export default function LandingPage() {
  return (
    <div className="relative min-h-screen bg-slate-950 text-white overflow-x-hidden">
      <GradientMesh />

      {/* ===== NAVBAR ===== */}
      <motion.nav 
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="fixed top-0 left-0 right-0 z-50 backdrop-blur-xl bg-slate-950/70 border-b border-white/5"
      >
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2 group">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center shadow-lg shadow-purple-500/20 group-hover:shadow-purple-500/40 transition-shadow">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">
              ViralEngine
            </span>
          </Link>
          
          <div className="hidden md:flex items-center gap-8 text-sm text-slate-400">
            <a href="#features" className="hover:text-white transition-colors">Características</a>
            <a href="#how-it-works" className="hover:text-white transition-colors">Cómo Funciona</a>
            <a href="#pricing" className="hover:text-white transition-colors">Precios</a>
          </div>

          <div className="flex items-center gap-3">
            <Link href="/login">
              <Button variant="ghost" className="text-slate-300 hover:text-white hover:bg-white/5">
                Iniciar Sesión
              </Button>
            </Link>
            <Link href="/login">
              <Button className="bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white shadow-lg shadow-purple-500/25 hover:shadow-purple-500/40 transition-all">
                Empezar Gratis
                <ArrowRight className="w-4 h-4 ml-2" />
              </Button>
            </Link>
          </div>
        </div>
      </motion.nav>

      {/* ===== HERO SECTION ===== */}
      <section className="relative min-h-screen flex items-center justify-center pt-20 px-6">
        <div className="max-w-5xl mx-auto text-center">
          {/* Badge */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.1, duration: 0.5 }}
            className="mb-8"
          >
            <Badge className="px-4 py-2 bg-purple-500/10 border-purple-500/20 text-purple-300 backdrop-blur-sm">
              <Zap className="w-3 h-3 mr-2" />
              Potenciado por IA Generativa
            </Badge>
          </motion.div>

          {/* Headline */}
          <AnimatedHeadline />

          {/* Subheadline */}
          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 1.0, duration: 0.6 }}
            className="mt-8 text-xl md:text-2xl text-slate-400 max-w-3xl mx-auto leading-relaxed"
          >
            Extrae los mejores momentos de tus videos de YouTube. Obtén clips listos para 
            <span className="text-purple-300"> TikTok</span>, 
            <span className="text-blue-300"> Twitter</span> y 
            <span className="text-cyan-300"> LinkedIn</span> en segundos.
          </motion.p>

          {/* CTA Buttons */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 1.2, duration: 0.6 }}
            className="mt-12 flex flex-col sm:flex-row items-center justify-center gap-4"
          >
            <Link href="/login">
              <Button size="lg" className="h-14 px-8 text-lg bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 shadow-2xl shadow-purple-500/30 hover:shadow-purple-500/50 transition-all group">
                Empezar Gratis
                <motion.span
                  animate={{ x: [0, 4, 0] }}
                  transition={{ duration: 1.5, repeat: Infinity, delay: 2 }}
                >
                  <ArrowRight className="w-5 h-5 ml-2" />
                </motion.span>
              </Button>
            </Link>
            <Button size="lg" variant="outline" className="h-14 px-8 text-lg border-slate-700 text-slate-300 hover:bg-white/5 hover:border-slate-600">
              <Play className="w-5 h-5 mr-2" />
              Ver Demo
            </Button>
          </motion.div>

          {/* Social Proof */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 1.5, duration: 0.8 }}
            className="mt-16 flex flex-col items-center gap-4"
          >
            <p className="text-sm text-slate-500">Usado por creadores en</p>
            <div className="flex items-center gap-8 opacity-50">
              <Youtube className="w-8 h-8 text-slate-400" />
              <Twitter className="w-7 h-7 text-slate-400" />
              <Linkedin className="w-7 h-7 text-slate-400" />
            </div>
          </motion.div>
        </div>

        {/* Scroll indicator - positioned outside hero content for independent animation */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 2.0, duration: 0.5 }}
          className="absolute bottom-10 left-1/2 -translate-x-1/2"
        >
          <motion.div
            animate={{ y: [0, 10, 0] }}
            transition={{ duration: 2, repeat: Infinity, delay: 2.5 }}
            className="w-6 h-10 rounded-full border-2 border-slate-700 flex items-start justify-center p-2"
          >
            <motion.div
              animate={{ opacity: [0.5, 1, 0.5] }}
              transition={{ duration: 2, repeat: Infinity, delay: 2.5 }}
              className="w-1.5 h-2.5 bg-slate-500 rounded-full"
            />
          </motion.div>
        </motion.div>
      </section>


      {/* ===== FEATURES SECTION ===== */}
      <section id="features" className="py-32 px-6">
        <div className="max-w-6xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-20"
          >
            <Badge className="mb-6 bg-cyan-500/10 border-cyan-500/20 text-cyan-300">
              Características
            </Badge>
            <h2 className="text-4xl md:text-5xl font-bold mb-6">
              Todo lo que necesitas para{" "}
              <span className="bg-gradient-to-r from-cyan-400 to-purple-400 bg-clip-text text-transparent">
                dominar las redes
              </span>
            </h2>
            <p className="text-xl text-slate-400 max-w-2xl mx-auto">
              IA avanzada que analiza, recorta y genera contenido optimizado automáticamente.
            </p>
          </motion.div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            <FeatureCard
              icon={Video}
              title="Análisis Inteligente"
              description="Nuestra IA escanea tu video completo, detectando los momentos de mayor engagement y potencial viral."
              gradient="bg-gradient-to-br from-purple-500 to-pink-500"
              delay={0}
            />
            <FeatureCard
              icon={Zap}
              title="Clips Quirúrgicos"
              description="Recortes precisos de 15-60 segundos, optimizados para cada plataforma con hooks impactantes."
              gradient="bg-gradient-to-br from-cyan-500 to-blue-500"
              delay={0.1}
            />
            <FeatureCard
              icon={Copy}
              title="Copy en 1 Click"
              description="Genera hilos de Twitter, captions de TikTok y posts de LinkedIn listos para publicar."
              gradient="bg-gradient-to-br from-orange-500 to-red-500"
              delay={0.2}
            />
          </div>
        </div>
      </section>

      {/* ===== HOW IT WORKS ===== */}
      <section id="how-it-works" className="py-32 px-6 relative">
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-purple-950/10 to-transparent" />
        
        <div className="max-w-4xl mx-auto relative">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-center mb-20"
          >
            <Badge className="mb-6 bg-pink-500/10 border-pink-500/20 text-pink-300">
              Proceso
            </Badge>
            <h2 className="text-4xl md:text-5xl font-bold mb-6">
              Cómo funciona
            </h2>
            <p className="text-xl text-slate-400">
              De video largo a contenido viral en 3 simples pasos.
            </p>
          </motion.div>

          <div className="space-y-2">
            <StepItem
              number="1"
              title="Pega el link de YouTube"
              description="Copia la URL de tu video y pégala en nuestra plataforma. Soportamos videos de cualquier duración."
              delay={0}
            />
            <StepItem
              number="2"
              title="Nuestra IA analiza el contenido"
              description="Procesamos el audio, video y contexto para identificar los momentos con mayor potencial de engagement."
              delay={0.15}
            />
            <StepItem
              number="3"
              title="Descarga y publica"
              description="Obtén clips editados, textos optimizados y estrategias de publicación. Todo listo para tus redes."
              delay={0.3}
            />
          </div>
        </div>
      </section>

      {/* ===== FINAL CTA ===== */}
      <section className="py-32 px-6 relative overflow-hidden">
        {/* Glow effect */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-[600px] h-[600px] bg-purple-500/20 rounded-full blur-[150px]" />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="max-w-4xl mx-auto text-center relative"
        >
          <h2 className="text-4xl md:text-6xl font-bold mb-8">
            ¿Listo para crear contenido{" "}
            <span className="bg-gradient-to-r from-purple-400 via-pink-400 to-cyan-400 bg-clip-text text-transparent">
              que explota?
            </span>
          </h2>
          <p className="text-xl text-slate-400 mb-12 max-w-2xl mx-auto">
            Únete a miles de creadores que ya están ahorrando horas de edición cada semana.
          </p>
          
          <Link href="/login">
            <Button size="lg" className="h-16 px-12 text-xl bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 shadow-2xl shadow-purple-500/40 hover:shadow-purple-500/60 transition-all group">
              Comenzar Ahora — Es Gratis
              <ArrowRight className="w-6 h-6 ml-3 group-hover:translate-x-1 transition-transform" />
            </Button>
          </Link>

          <div className="mt-8 flex items-center justify-center gap-6 text-sm text-slate-500">
            <span className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-green-400" />
              Sin tarjeta de crédito
            </span>
            <span className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-green-400" />
              Cancela cuando quieras
            </span>
          </div>
        </motion.div>
      </section>

      {/* ===== FOOTER ===== */}
      <footer className="py-12 px-6 border-t border-white/5">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <span className="font-semibold text-slate-400">ViralEngine</span>
          </div>
          
          <p className="text-sm text-slate-600">
            © 2025 ViralEngine. Creado con 💜 para creadores de contenido.
          </p>

          <div className="flex items-center gap-4">
            <a href="#" className="text-slate-500 hover:text-white transition-colors">
              <Twitter className="w-5 h-5" />
            </a>
            <a href="#" className="text-slate-500 hover:text-white transition-colors">
              <Youtube className="w-5 h-5" />
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
