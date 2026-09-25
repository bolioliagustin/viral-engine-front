# viral-engine — guía para agentes

SaaS que convierte un video (link de YouTube o archivo del creador) en clips verticales 9:16 con subtítulos, copy para redes y scores. Tres componentes: `worker/` (Python 3.12, todo el procesamiento), `backend/` (Express, Node 20), `frontend/` (Next.js). Supabase es la base de datos **y la cola** (`jobs`). Etapa actual: beta cerrada; el objetivo es fiabilidad, no features.

## Leé antes de tocar

- `docs/PROYECTO.md` — entendimiento completo (arquitectura, pipeline, datos, API, plan). Leelo entero antes de cambiar algo fuera de tu componente, y su sección correspondiente antes de tocar la cola, los créditos, la descarga de YouTube, los modelos LLM o la infra.
- `CONTEXT.md` — glosario. Usá esos términos en código, commits, PRs y docs; un concepto nuevo se agrega ahí en el mismo PR.
- `docs/adr/` — decisiones tomadas y por qué. Leé el ADR que toque tu tema antes de proponer otro camino; una decisión nueva que sea cara de revertir va en un ADR nuevo con el número siguiente.
- `worker/WORKER.md` y `docs/INFORME_LLMS.md` — al trabajar en el worker o en prompts/modelos.

## Flujo de trabajo

- Trabajá en una rama `feat/<componente>-<tema>` o `fix/<componente>-<tema>` creada desde `main` (o desde la rama de integración que te indique el coordinador) y abrí un PR. `main` es solo de humanos: el merge y el deploy los decide Agustín. **Mergear a `main` despliega el worker** (`deploy.yml` escucha el push), así que un cambio de comportamiento del worker entra con su flag apagado.
- Una medición nunca lee ni escribe la caché de análisis de producción: el `.env` de la raíz apunta a la base de la beta (`docs/PLAN_MEJORA.md` §4.1).
- Un componente por agente. Los contratos entre componentes son el esquema (§7 de PROYECTO), los endpoints (§8) y las variables de entorno (§11): si tu PR cambia uno, actualizá esa sección en el mismo PR y decilo en la descripción.
- Secretos: `.env`, `frontend/.env.local`, `proxies.txt` y `cookies.txt` viven fuera de git. Los nombres están en `.env.example`; los valores los pone Agustín. Un valor de esos nunca aparece en un commit, log o PR.
- Datos: el desarrollo usa su propio proyecto Supabase. Un worker local apuntado al Supabase de la beta le roba jobs (no hay afinidad de worker): verificá `SUPABASE_URL` antes de correr `worker/main.py`.
- Esquema: solo por Supabase CLI (`supabase migration new …` en `supabase/migrations/`), nunca desde el dashboard (ADR 0006).

## PR listo = todo esto verde

1. Tests del componente pasan (comandos abajo).
2. `docs/PROYECTO.md`, `CONTEXT.md` y ADRs actualizados si cambiaste comportamiento, contratos, términos o decisiones.
3. Si tocaste prompts o `MODEL_ANALYSIS`: `PROMPT_VERSION` subido en `worker/services/analysis_cache.py` y golden set `smoke` corrido (`worker/eval/run_golden_set.py --tier smoke`, requiere claves).
4. Descripción del PR: qué cambió, qué contrato tocó, cómo lo probaste.

## Comandos y gotchas que el repo no confiesa

- Backend y frontend corren con Node ≥ 20 (la imagen de producción es `node:20-slim`; `node@20` está instalado en `/opt/homebrew/opt/node@20/bin` si querés paridad exacta).
- **Los tests del worker necesitan variables dummy** (sin ellas fallan 18/104; por eso el CI del worker está rojo hasta que `ci.yml` las defina):
  - backend: `cd backend && NODE_ENV=test npm test`
  - worker: `cd worker && ENVIRONMENT=development SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test OPENROUTER_API_KEY=test OPENAI_API_KEY=test .venv/bin/python -m pytest tests/ -q` (`ENVIRONMENT=development` pisa el `.env` de la raíz si es el de producción)
  - frontend: `cd frontend && npx tsc --noEmit` (`npm run lint` arrastra 15 errores heredados: no sumes nuevos)
- Worker en local: `worker/.venv` (creado con `uv venv --python 3.12`); `source worker/.venv/bin/activate`. Backend y worker leen el mismo `.env` de la **raíz** del repo.
- `ENVIRONMENT=production` cambia los defaults de descarga (prefiere RapidAPI + proxies, exige `RAPIDAPI_KEY`); en la Mac (IP residencial) yt-dlp funciona sin proxies, así que dejá `ENVIRONMENT=development` en local.
- `content_results` tiene **tres filas por momento** (una por pieza de copy) con los metadatos repetidos; el frontend agrupa por `moment_index`.
- Gemini 3.x y GPT-5.x: se omite `temperature` y los tokens de razonamiento consumen `max_tokens` (`worker/config/llm_chat.py` ya lo resuelve; no lo bypassees).
- URLs de googlevideo van atadas a la IP que las resolvió: el proxy que resuelve es el que descarga (sticky). Cambiar de proxy a mitad de descarga da 403.
- **Render local en la Mac**: el `ffmpeg` de Homebrew viene sin libass, así que el filtro `ass=` (subtítulos y overlay) falla y el clip no se genera. Usá `ffmpeg-full` apuntando `FFMPEG_PATH=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` y `FFPROBE_PATH=/opt/homebrew/opt/ffmpeg-full/bin/ffprobe` en el `.env` de la raíz (el `ffmpeg` de Debian del VPS sí trae libass). Instalar `ffmpeg-full` rompe el `ffmpeg` estándar (dylib de x265): si pasa, `brew reinstall ffmpeg`.
- **Nada de instalar paquetes del sistema** (brew, apt, cambios de PATH global) desde un agente: rompe el entorno compartido de los demás worktrees. Si falta una herramienta, pedila y esperá.
- Comentarios, logs y prompts del worker están en español; mantené el idioma del archivo que tocás.
