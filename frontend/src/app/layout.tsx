import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Viral Content Engine | Genera contenido viral automáticamente",
  description: "Transforma cualquier video de YouTube en contenido viral para Twitter y LinkedIn con IA",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body className="antialiased">
        {children}
      </body>
    </html>
  );
}
