"use client";

import * as React from "react";

interface ToastProps {
  title: string;
  description?: string;
  variant?: "default" | "destructive";
  duration?: number;
}

interface ToastContextValue {
  toast: (props: ToastProps) => void;
}

const ToastContext = React.createContext<ToastContextValue | null>(null);

export function useToast() {
  const context = React.useContext(ToastContext);
  if (!context) {
    // Fallback simple
    return {
      toast: (props: ToastProps) => {
        alert(`${props.title}${props.description ? `\n${props.description}` : ""}`);
      },
    };
  }
  return context;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<(ToastProps & { id: string })[]>([]);

  const toast = React.useCallback((props: ToastProps) => {
    const id = Math.random().toString();
    setToasts((prev) => [...prev, { ...props, id }]);
    
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, props.duration || 3000);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 space-y-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`
              bg-slate-800 border rounded-lg p-4 shadow-lg animate-in slide-in-from-right
              ${t.variant === "destructive" ? "border-red-500 text-red-300" : "border-slate-700 text-white"}
            `}
          >
            <div className="font-semibold">{t.title}</div>
            {t.description && <div className="text-sm text-slate-400">{t.description}</div>}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
