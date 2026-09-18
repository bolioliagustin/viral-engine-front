# Corridas del tier `e2e`

Una línea por corrida. El JSON completo (por clip y agregados) queda al lado;
el `.log` no se versiona (`*.log` está en `.gitignore`); si hace falta compartirlo, adjuntarlo al PR. Cómo correr y comparar:
[`../README.md`](../README.md) (sección "Tier e2e"). La regla de oro de la
rueda de mejora ([`docs/PLAN_CALIDAD.md`](../../../docs/PLAN_CALIDAD.md) §5):
**no se cambia nada del pipeline de IA sin un run e2e antes y después.**

Convención de nombre: `<fecha>-<PROMPT_VERSION>[-nota].json`.

| Fecha | Archivo | PROMPT_VERSION | Modelos (analysis / copy / judge / classifier) | Commit | Qué se cambió | Resultado (juez avg · ≥7 · verif_failed · densidad fuera · costo · tiempo) |
|---|---|---|---|---|---|---|
| 2026-09-18 | [`2026-09-18-v4-baseline.json`](2026-09-18-v4-baseline.json) (el `.log` de 230 KB queda local: `*.log` está en `.gitignore`) | v4 | gemini-3.5-flash / gemini-3.5-flash / gpt-5.4-nano / gemini-2.5-flash-lite | `030c835` | **Baseline** (W0): pipeline sin cambios, 4 videos (las 4 Pasadas A cacheadas), 20/20 clips renderizados, Mac con ffmpeg-full | juez 5.05 / 4.45 / 4.85 → **4.78** · ≥7 en las tres **0 %** · verification_failed **100 %** (late_hook 40 %, whisper_mismatch_last 45 %) · mayúscula inicial 35 % · densidad fuera de rango 0 % · duración 35.9 s → 30.9 s · US$0.152 · 8.0 min |
