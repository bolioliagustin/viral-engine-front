"use client";

import { motion } from "framer-motion";
import { UserMenu } from "@/components/UserMenu";
import Link from "next/link";

export function Navbar() {
  return (
    <nav className="relative z-10 px-6 py-4 flex items-center justify-between border-b border-white/5 bg-slate-950/50 backdrop-blur-sm">
      <Link href="/">
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          className="flex items-center gap-2"
        >
          <div className="w-8 h-8 bg-gradient-to-br from-purple-500 to-pink-500 rounded-lg flex items-center justify-center font-bold text-white shadow-lg shadow-purple-500/20">
            V
          </div>
          <span className="font-bold text-white tracking-tight">ViralEngine</span>
        </motion.div>
      </Link>

      <motion.div
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: 1, x: 0 }}
        className="flex items-center gap-6"
      >
        <Link
          href="/pricing"
          className="hidden md:inline text-sm text-slate-400 hover:text-purple-300 transition-colors font-medium"
        >
          Precios
        </Link>
        <UserMenu />
      </motion.div>
    </nav>
  );
}
