# Fuentes embebidas

## Bangers-Regular.ttf

Fuente cómic gruesa para el estilo de subtítulos `tiktok_viral_v2` (W11,
docs/PLAN_CALIDAD.md §9 Fase 1 — imitar el look de los clips virales tipo
TikTok/Opus Clip: mayúsculas, borde grueso, energía).

- **Fuente:** [Google Fonts — Bangers](https://fonts.google.com/specimen/Bangers)
  (repo [googlefonts/bangers](https://github.com/googlefonts/bangers)),
  bajada de `github.com/google/fonts/ofl/bangers/`.
- **Licencia:** SIL Open Font License 1.1 (`OFL.txt`, incluida en esta
  carpeta) — uso, embebido y redistribución libres, incluso comercial;
  la única restricción es no vender la fuente por sí sola sin modificarla.
  Compatible con embeberla en la imagen Docker del worker y en los MP4
  renderizados.
- **Nombre interno** (el que usa `ass=`/libass para resolverla, `fc-scan`
  → `family:`): `Bangers`.
- **Cómo se usa:** no se instala a nivel sistema (ni en este Mac ni en el
  contenedor) — se pasa como `fontsdir` al filtro `ass=` de FFmpeg
  (`services/clip_generator.py::FONTS_DIR`), que le indica a libass dónde
  buscar además de fontconfig. Evita tocar paquetes del sistema.

Si se agrega otra fuente acá, sumarla a esta tabla con su fuente y licencia.
