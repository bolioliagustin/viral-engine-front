# Runbook de despliegue — Fase 0 (10 líneas de trabajo integradas)

**Fecha:** 20-sep-2026. **Rama verificada:** `docs/runbook-fase-0` (= `origin/integracion/fase-0` + `origin/feat/progreso-dos-fases` + `origin/feat/inicio-de-linea`; `origin/feat/galeria-worker` todavía está en curso y NO entra en este despliegue).

Este documento es la lista completa y verificada de lo que cambia al desplegar la Fase 0 de una sola vez: variables de entorno nuevas, seis migraciones, dependencias de sistema nuevas en la imagen del worker (opencv, scenedetect, una fuente TTF, un modelo ONNX), y el comportamiento por defecto del pipeline (`TRANSCRIPT_SOURCE`, `REFRAME_MODE`, `SUBTITLE_STYLE_DEFAULT`). Cada afirmación de esta sección está marcada como **medida** (se corrió y se citó la fuente), **verificada** (se leyó el código/config y se confirmó) o **estimada** (no se pudo correr — ver §7 Verificación).

Vocabulario: `CONTEXT.md`. Decisiones de fondo: ADR 0001 (jobs = la cola), 0005 (créditos reservados), 0006 (migraciones por Supabase CLI), 0007 (retención 7 días), 0008 (galería + créditos).

---

## 0. Qué se está desplegando (para tener el mapa)

| Línea | Qué cambia | Afecta |
|---|---|---|
| W1/W2/W2-B/W2-C | Cortes anclados a frases, el juez elige, tope 120s, `verification_failed` honesto | worker (sin variable nueva) |
| W1-C | Arranca en el inicio de la Línea (requiere `TRANSCRIPT_SOURCE=whisper_full`) | worker |
| W4 | Transcript puntuado por Whisper completo | worker + `TRANSCRIPT_SOURCE` |
| W5 | Encuadre Split/Fill/Fit | worker + `REFRAME_MODE` + deps de sistema (opencv, scenedetect, modelo ONNX) |
| W6/W10/W11 | Copy fiel, copy por clip + Score visible, subtítulos v2 | worker + `SUBTITLE_STYLE_DEFAULT` + fuente Bangers + 2 migraciones |
| W7 | Feedback humano (posteable) | backend + frontend + 1 migración |
| W9-A | Galería + HD a pedido | backend + frontend + 1 migración |
| F1 | Créditos reservados, tope 90 min, Telegram, Sentry limpio | backend + 1 migración + 2 variables nuevas |
| P1 | Pantalla de progreso en dos fases + editor con v2 | backend + frontend + 1 migración |

---

## 1. Variables de entorno

### 1.1 Diff exacto `.env.example`: main → rama integrada

Comparé los **nombres de variable** (no el diff de texto crudo: el archivo se reescribió completo en la limpieza del 16-sep, así que un `git diff` línea por línea es puro ruido). Ninguna variable de main se sacó. Nuevas:

| Variable | Default / valor recomendado VPS beta | Si falta |
|---|---|---|
| `TRANSCRIPT_SOURCE` | **`whisper_full`** (recomendado para el primer despliegue) | Default `supadata` (captions de YouTube, comportamiento de siempre) — el pipeline funciona igual, pero W1-C (arrancar en la Línea) no tiene Líneas para usar y cae a su respaldo de partículas (peor, ~93% vs conocido) |
| `REFRAME_MODE` | **`auto`** (recomendado) | Default `off` — todos los clips salen en `fit` (fondo desenfocado), sin Split/Fill |
| `SUBTITLE_STYLE_DEFAULT` | **`tiktok_viral_v2`** (ya es el default en código — `worker/main.py:99`) | Cae a `tiktok_viral_v2` igual (mismo default); si se fuerza a un valor viejo, los clips salen con el estilo `tiktok_viral` de antes de W11 |
| `MAX_VIDEO_MINUTES` | `90` (default) | Cae a 90 igual — no hace falta setearla salvo que se quiera otro tope |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Las dos, o ninguna | Sin ambas, `notify()` es no-op silencioso — no rompe nada, pero no hay alertas de jobs fallidos ni de "sin créditos" |
| `CLIP_MARGIN_BEFORE_SEC` / `CLIP_MARGIN_AFTER_SEC` (alias legacy: `CLIP_KEYFRAME_MARGIN_SEC`) | Default 15 / 20 | Cae a esos defaults |
| `CLIP_SYNC_RETRIES`, `CLIP_GEN_RETRIES`, `STRICT_SYNC_VALIDATION` | Defaults (ver `.env.example`) | Comportamiento de siempre |
| `DOWNLOAD_STRATEGY`, `DOWNLOAD_PARALLEL_WORKERS`, `DOWNLOAD_PHASE_BUDGET_SEC` | Defaults | Comportamiento de siempre |
| `COMPACT_TRANSCRIPT` | Default `true` | Transcript sin compactar (más tokens, más costo) |
| `TRANSCRIPT_PUNCTUATE_FALLBACK`, `TRANSCRIPT_LINE_STYLE` | Defaults (`true`, `seconds`) | Ver comentario en `.env.example` — `mmss` está roto con `gemini-3.5-flash` (concatena mm y ss), no usarlo |
| `WORKER_LOG_DIR`, `WORKER_LOG_MAX_BYTES`, `WORKER_LOG_BACKUP_COUNT`, `LOG_FORMAT`, `LOG_LEVEL` | El compose ya setea `WORKER_LOG_DIR=/app/logs` | Logs a stdout nomás si falta `WORKER_LOG_DIR` |
| `FFMPEG_PATH`, `FFPROBE_PATH` | Sin setear (usa el del PATH) | Solo hace falta en Mac si `ffmpeg` no está en el PATH |
| `APIFY_TOKEN`, `USE_APIFY_FALLBACK`, `YTDLP_CLIP_FALLBACK` | `false` / sin setear | Fallback de descarga apagado (default ya es así) |
| `WEBSHARE_PROXY_FILE` | Ya la carga el compose (`./proxies.txt`) | Sin proxies, YouTube bloquea IPs de datacenter (ya documentado, no es nuevo de esta tanda) |

**Nota sobre `ADR 0005`:** las funciones de créditos (`reserve_credit`/`release_credit`/`deduct_user_credit` redefinida) no necesitan variable de entorno — viven en la migración `creditos_reservados` (§2).

### 1.2 Recomendación explícita para el primer despliegue

```
TRANSCRIPT_SOURCE=whisper_full
REFRAME_MODE=auto
SUBTITLE_STYLE_DEFAULT=tiktok_viral_v2   # ya es el default, setearla es solo para que quede explícito
```

Costo/tiempo **medido** (no estimado — ver fuentes):

- `TRANSCRIPT_SOURCE=whisper_full`: **~50 s y US$0.055** por un video de 77 min (`podcast_general_01`), una sola vez por video (cachea en `transcription_cache`/`analysis_cache`; una segunda corrida del mismo video tarda ~1 s). Fuente: `worker/WORKER.md:211`.
- `REFRAME_MODE=auto`: **~1,2 s de CPU por clip** (5 muestras por escena, medido sobre un clip de 30 s). Fuente: `worker/WORKER.md:444`.

El resto de las variables nuevas: default (no hace falta setearlas para el primer despliegue).

### 1.3 Verificación: ningún `os.getenv`/`process.env` sin documentar

Extraje por `grep` todas las variables que el código realmente lee (`os.getenv`/`os.environ` en `worker/**/*.py`, `process.env.` en `backend/src/**/*.js`) y las crucé contra `.env.example`. Resultado:

- **`SUBTITLE_STYLE_DEFAULT` estaba sin documentar** — se usa en `worker/main.py:99` (`os.getenv("SUBTITLE_STYLE_DEFAULT", "tiktok_viral_v2")`) y en `worker/services/clip_edit_processor.py:258`, pero no aparecía en `.env.example`. **La agregué** en esta rama (§8, commit de docs) — no es un cambio de comportamiento, el default en código no se tocó.
- Dos variables que aparecen en el código pero **no van en `.env.example`** a propósito: `EVAL_DRY_RUN` (flag interno del harness de eval, `worker/eval/run_golden_set.py`, no es config de despliegue) y `HTTP_PROXY_URL` (alias interno de fallback de `WEBSHARE_PROXY_URL`, mismo propósito, ya documentado bajo ese nombre).
- `DELIVERY_JUDGE_MIN` y `DELIVERY_MAX_CLIPS` (mencionadas como posibles en el brief de esta tarea) **no existen en el código de esta rama** — no hay ningún `os.getenv`/`process.env` con esos nombres. Probablemente son parte de la mitad worker de W9 (`feat/galeria-worker`, todavía en curso, explícitamente fuera de este despliegue).

---

## 2. Migraciones

Seis migraciones nuevas desde la última vez que se corrió `supabase db push` contra la base de la beta (todas después de `20260708000000_remote_schema.sql`, el esquema base). Se aplican con:

```bash
cd /Users/agustinbolioli/orca/viral-engine-front   # o donde esté el repo en la máquina de deploy
supabase link --project-ref <project-ref-de-la-beta>   # una vez, si no está linkeado
supabase db push
```

`supabase db push` aplica las migraciones **en orden por nombre de archivo** (el timestamp del nombre), una por una, dentro de una transacción cada una. No hay `db push` contra la base real en esta tarea — es file-only, per ADR 0006.

| # | Archivo | Qué hace (una línea) | Verificación (SQL, correr en el SQL Editor de Supabase o `psql`) |
|---|---|---|---|
| 1 | `20260918123640_clip_feedback.sql` | Crea la tabla `clip_feedback` (etiqueta humana "¿lo publicarías tal cual?", W7) con RLS (el usuario inserta/ve solo lo suyo) | `SELECT to_regclass('public.clip_feedback');` → no debe dar `NULL` |
| 2 | `20260918223754_copy_por_clip.sql` | Agrega `content_results.title/description/hashtags` (copy por clip, W10) | `SELECT column_name FROM information_schema.columns WHERE table_name='content_results' AND column_name IN ('title','description','hashtags');` → 3 filas |
| 3 | `20260918230146_subtitulos_v2_check.sql` | Amplía el `CHECK` de `clip_edits.subtitle_style` para aceptar `tiktok_viral_v2` (W11) | `SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='clip_edits_subtitle_style_check';` → el texto tiene que incluir `tiktok_viral_v2` |
| 4 | `20260918234140_galeria_hd.sql` | Agrega `content_results.preview_url` y `clip_edits.edit_type` (`style`\|`hd_upgrade`, W9-A) | `SELECT column_name FROM information_schema.columns WHERE table_name='clip_edits' AND column_name='edit_type';` → 1 fila |
| 5 | `20260919040620_creditos_reservados.sql` | Agrega `jobs.credit_reserved`/`failure_alert_sent`; crea `reserve_credit`/`release_credit`; **redefine `deduct_user_credit`** para no descontar dos veces; agrega el trigger `trg_release_credit_on_job_failed` (F1, ADR 0005) | `SELECT proname FROM pg_proc WHERE proname IN ('reserve_credit','release_credit');` → 2 filas. `SELECT tgname FROM pg_trigger WHERE tgname='trg_release_credit_on_job_failed';` → 1 fila |
| 6 | `20260920042408_progreso_detalle.sql` | Agrega `jobs.progress_detail` (jsonb, P1) | `SELECT column_name FROM information_schema.columns WHERE table_name='jobs' AND column_name='progress_detail';` → 1 fila |

### 2.1 Dependencias entre migraciones (verificación manual — no hay `supabase start` disponible, ver §7)

Leí las seis migraciones completas. **No encontré ninguna dependencia dura entre ellas más allá del orden cronológico que ya les da el nombre del archivo**: cada una solo asume columnas/tablas de la base (`remote_schema.sql`) o las que ella misma agrega en el mismo archivo. Puntualmente, sobre la sospecha del brief de esta tarea ("galeria_hd asume columnas de copy_por_clip"): leí `20260918234140_galeria_hd.sql` completo — opera sobre `content_results` (agrega `preview_url`) y `clip_edits` (agrega `edit_type`), sin ninguna referencia a `title`/`description`/`hashtags` de `copy_por_clip`. Conviven en el mismo release por casualidad de fechas, no por una dependencia real; si alguna vez se reordenaran, no se rompería nada a nivel SQL. Sí hay una dependencia **de aplicación** real: la migración 5 (`creditos_reservados`) agrega `jobs.credit_reserved`, que el backend de F1 (`POST /process`) ya espera poder insertar — ver §2.2.

### 2.2 Efecto de `creditos_reservados` sobre jobs en curso durante el despliegue

Esta es la migración más delicada porque **redefine una función que el worker llama en producción sin parar el worker** (`deduct_user_credit`). Análisis:

- **Jobs `processing` en el momento en que se aplica la migración**: fueron creados por el backend VIEJO (sin `credit_reserved`), así que su fila de `jobs` tiene `credit_reserved = false` (el `DEFAULT` de la columna nueva se les aplica retroactivamente). Cuando el worker (viejo o nuevo, da igual) termina esos jobs y llama a `deduct_user_credit`, la función nueva ve `credit_reserved = false` → toma la rama de compatibilidad → **descuenta exactamente como antes**. No hay doble descuento ni descuento faltante.
- **Riesgo real de orden**: si el **backend nuevo** (que llama a `reserve_credit`/inserta `credit_reserved: true`) se despliega **antes** que la migración corra, `POST /process` va a fallar (la columna `credit_reserved` todavía no existe, o la RPC `reserve_credit` todavía no existe) → **todo intento de crear un job da 500** hasta que la migración se aplique. Por eso el orden de §4 es migraciones → backend → worker → frontend, sin excepción para esta migración en particular.
- **Riesgo de rollback** (ver §5): si se revierte la FUNCIÓN `deduct_user_credit` a la versión vieja (descuenta siempre) mientras hay jobs `processing` con `credit_reserved = true` (creados por el backend nuevo), esos jobs se cobrarían **dos veces** (una vez al reservar, otra al completar). No revertir esa función mientras haya jobs en vuelo creados después del deploy — esperar a que terminen (o mirar `SELECT count(*) FROM jobs WHERE status='processing' AND credit_reserved=true;` y esperar a que dé 0) antes de tocarla.

---

## 3. Worker en el VPS

### 3.1 Pasos (siguiendo `deploy/deploy-worker.sh`, ya existente — no se modifica)

```bash
# En el VPS, dentro de ~/viralengine
git fetch origin main
git reset --hard origin/main
docker compose -f docker-compose.worker.yml build --pull --no-cache
docker compose -f docker-compose.worker.yml up -d --remove-orphans --force-recreate
docker compose -f docker-compose.worker.yml ps
```

(`deploy/deploy-worker.sh` ya hace exactamente esto, más chequeos de `proxies.txt`/`GROQ_API_KEY`/`YOUTUBE_COOKIES` en `.env` — correrlo tal cual alcanza: `bash deploy/deploy-worker.sh`.)

### 3.2 Qué trae la imagen nueva (verificado leyendo `worker/Dockerfile` y `worker/requirements.txt`)

- **Paquetes de sistema (`apt-get`) nuevos:** `fonts-liberation`, `fonts-dejavu-core` — libass necesita que la familia "Liberation Sans" exista para el filtro `ass=` de subtítulos/overlay (bug latente pre-W11 que estos paquetes cierran).
- **Paquetes Python nuevos:** `opencv-python-headless` + `click`/`numpy`/`platformdirs`/`tqdm` (deps reales de `scenedetect`) instalados por `requirements.txt`; `scenedetect` se instala **aparte, con `--no-deps`**, en una segunda línea del Dockerfile — es a propósito: `scenedetect` declara `opencv-python` (con GUI, necesita `libGL.so.1`, ausente en esta imagen slim) como dependencia dura, y esa build pisaría los archivos de la variante headless (ambas exponen el mismo paquete `cv2`, no pueden convivir). Verifiqué esta secuencia con `pip` real (no solo `uv`) en un venv limpio en una tarea anterior (W5) — funciona.
- **Fuente Bangers** (`worker/fonts/Bangers-Regular.ttf`, 93 KB, licencia OFL — `worker/fonts/README.md`/`OFL.txt`): **NO se instala a nivel de sistema ni de fontconfig.** Se copia con el resto del código (`COPY . .`; no está en `.dockerignore`) y se pasa como `fontsdir` al filtro `ass=` de FFmpeg en tiempo de render (`services/clip_generator.py::FONTS_DIR`). **Esto cambia la verificación pedida en el brief de esta tarea:** `fc-list | grep -i bangers` **no la va a encontrar aunque todo esté perfecto** — fontconfig nunca se entera de que existe, por diseño. La verificación correcta es que el archivo esté en la imagen: `docker run --rm <imagen> ls /app/fonts/` tiene que listar `Bangers-Regular.ttf`.
- **Modelo YuNet** (`worker/models/face_detection_yunet_2023mar.onnx`, 233 KB, licencia Apache-2.0 — `worker/models/README.md`/`LICENSE`): igual que la fuente, se copia con `COPY . .`, no se descarga en runtime. Verificación: `docker run --rm <imagen> ls /app/models/` tiene que listar el `.onnx`.
- **`worker/.dockerignore` excluye `*.md`, `tests/`, `.venv/`, `__pycache__/`, `downloads/`, `clips/`, `logs/`** — ninguno de esos patrones excluye `fonts/*.ttf` ni `models/*.onnx`, así que ambos SÍ entran en la imagen (verificado leyendo el archivo, no hace falta correr el build para confirmar esto).

### 3.3 Tamaño y tiempo de build

**No pude construir la imagen en esta Mac: Docker no está instalado** (`docker: command not found`; tampoco hay Colima, Podman ni Docker Desktop). Por instrucción explícita de esta tarea, no lo instalé. Esto es una **estimación**, no una medición:

- Base `python:3.12-slim` (~150 MB) + `ffmpeg`/`fonts-*` vía apt (+200-300 MB) + paquetes Python (`opencv-python-headless` es el más pesado del lote nuevo, ~30-40 MB comprimido; el resto del lote nuevo es chico) + código y assets (~few MB) → estimo **~1,2-1,6 GB** de imagen final, con un build de **~3-6 min** en el VPS (la mayor parte en `apt-get` + `pip install`, con `--no-cache` como usa `deploy-worker.sh`). **Esto hay que medirlo la primera vez que se corra el deploy real** y anotar el número exacto acá o en `worker/WORKER.md`.

### 3.4 Verificación post-deploy

**Dentro del contenedor, apenas levanta:**

```bash
docker compose -f docker-compose.worker.yml exec worker python -c "import cv2, scenedetect; print('OK', cv2.__version__)"
docker compose -f docker-compose.worker.yml exec worker ls fonts/ models/
```

Debe imprimir `OK 5.x.x` (o la versión que traiga `opencv-python-headless` en ese momento) sin traceback, y listar `Bangers-Regular.ttf` / `face_detection_yunet_2023mar.onnx` respectivamente.

**Job real corto** (procedimiento — no lo corrí, requiere un video real y créditos de las APIs; describo qué hacer y qué mirar):

1. Encolar un video de YouTube de ≤10 min desde el frontend (o `POST /process`) con una cuenta de prueba.
2. Seguir los logs: `bash scripts/worker-logs.sh job <prefijo-del-job-id>` (o `docker compose -f docker-compose.worker.yml logs -f worker`).
3. Confirmar en los logs:
   - Fases nuevas del pipeline en dos fases (P1): líneas con `evaluating`/`ranking`/`delivering` (o los prints correspondientes en español que main.py ya emite: "Evaluando", "Ranking del juez", etc.) — no las viejas `downloading`/`clipping`.
   - `🖼️ Reencuadre (W5): layout=split` o `layout=fill` (si el video tiene caras claras; `layout=fit` es válido si no las detecta) — confirma que `REFRAME_MODE=auto` está activo.
   - `🎬 Subtítulos v2: N bloques, M con palabra clave` — confirma `SUBTITLE_STYLE_DEFAULT=tiktok_viral_v2`.
   - Si `TRANSCRIPT_SOURCE=whisper_full`: log de `transcript_full` con Líneas y wpm (ver `worker/WORKER.md:211` para el formato exacto del log).
4. En Supabase, `SELECT preview_url, clip_url FROM content_results WHERE job_id='<id>';` — `preview_url` **va a estar en `NULL` todavía** (la mitad worker de W9 que lo llena es `feat/galeria-worker`, no entra en este despliegue) — no es un bug, es el estado esperado poscierre de esta Fase 0.

---

## 4. Backend en Render y frontend en Vercel

### 4.1 Variables nuevas por panel

**Render (`viralengine-backend`)** — agregar en el dashboard (Environment) o vía `render.yaml` (ver §8, lo actualicé porque le faltaban estas cuatro):

- `MAX_VIDEO_MINUTES` (opcional, default 90 si no se pone)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (opcionales, sin ellas las alertas son no-op)
- `ADMIN_USER_IDS` (ya existía `ADMIN_EMAILS` en el blueprint; `ADMIN_USER_IDS` faltaba — lo usa `backend/src/middleware/admin.js`)

**Vercel (frontend)** — **ninguna variable nueva.** Verifiqué: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` son las únicas `NEXT_PUBLIC_*` que usa el código (grep en `frontend/src`), y ninguna de las 10 líneas de trabajo agregó una nueva — todo lo que cambió en el frontend (galería, pantalla de progreso, editor v2) consume el mismo backend por la misma URL de siempre.

### 4.2 Orden correcto y por qué

1. **Migraciones** (`supabase db push`) — primero siempre. El backend nuevo asume columnas (`credit_reserved`, `preview_url`, `progress_detail`) y RPCs (`reserve_credit`) que no existen hasta que corren; desplegarlo antes rompe `POST /process` con 500 (ver §2.2).
2. **Backend (Render)** — segundo. Una vez que el esquema soporta el código nuevo, el backend puede desplegarse solo: sigue sirviendo jobs viejos sin romperse (todos los campos nuevos son opcionales/con default) y ya empieza a reservar créditos, aplicar el tope de duración y devolver `progress_detail`/`partial` en `/status`.
3. **Worker (VPS)** — tercero. El worker puede quedarse en la versión vieja mientras el backend ya está nuevo sin problema (sigue llamando a `deduct_user_credit` con la misma firma, sigue escribiendo `current_step`/`progress_percentage` de siempre — el frontend ya sabe degradar sin `progress_detail`). Desplegarlo después evita que un worker nuevo (que podría emitir fases nuevas de `current_step`) le llegue a un backend viejo que todavía no sabe pasarlas — aunque en la práctica `current_step` es TEXT libre y el backend siempre lo pasó tal cual, así que este paso específico no tiene un riesgo fuerte; se mantiene el orden por prolijidad y porque es el que ya sigue `deploy-worker.sh` respecto de Render (Render se autodespliega por git push a `main`, independiente del VPS).
4. **Frontend (Vercel)** — último. Sin variables nuevas, se autodespliega por integración Git; no tiene sentido adelantarlo porque sus componentes nuevos (galería con banner parcial, pantalla de progreso en dos fases, selector v2) ya degradan bien contra un backend viejo (campos opcionales) — pero desplegarlo último de todas formas evita mostrarle al usuario UI para funciones que el backend todavía no sirve.

---

## 5. Rollback

**Principio general: todas las migraciones de esta tanda son aditivas** (`ADD COLUMN`, `CREATE TABLE`, `CREATE OR REPLACE FUNCTION`, ampliar un `CHECK`) — ninguna hace `DROP`. La forma más segura de "volver atrás" casi siempre es **revertir el código, no el esquema**: columnas de más no rompen nada, y revertir un `ADD COLUMN` innecesariamente arriesga perder datos que el código nuevo ya escribió.

| Pieza | Cómo volver atrás |
|---|---|
| **Worker (imagen Docker)** | `deploy-worker.sh` reconstruye con `--no-cache` desde el código del commit actual — **no guarda la imagen anterior con un tag**. Rollback real: `git reset --hard <sha-anterior>` en `~/viralengine` y volver a correr `deploy-worker.sh` (rebuildea desde ese commit). Mejora pendiente, no implementada acá (fuera de alcance): taggear la imagen con el SHA antes de cada `force-recreate` para poder hacer `docker compose up -d` directo a una imagen vieja sin rebuild. |
| **Variables de entorno** | Sacar o revertir el valor en Render/`.env` del VPS y reiniciar (`docker compose up -d --force-recreate` para el worker; Render redeploya solo al cambiar una env var desde el dashboard) |
| **Backend (Render)** | Redeploy al commit anterior desde el dashboard de Render (o `git revert` + push a `main`) |
| **Frontend (Vercel)** | Rollback a un deployment anterior desde el dashboard de Vercel (un click, Vercel guarda todos los deployments) |
| **Migración `clip_feedback`** | Reversible sin riesgo: `DROP TABLE IF EXISTS public.clip_feedback;` (nadie más depende de ella) |
| **Migración `copy_por_clip`** | Reversible: `ALTER TABLE content_results DROP COLUMN IF EXISTS title, DROP COLUMN IF EXISTS description, DROP COLUMN IF EXISTS hashtags;` — pierde el copy por clip ya generado si se revierte después de generarlo |
| **Migración `subtitulos_v2_check`** | Reversible: volver a crear el `CHECK` sin `tiktok_viral_v2` — pero si algún `clip_edits.subtitle_style` ya quedó en `tiktok_viral_v2`, el `ALTER TABLE ... ADD CONSTRAINT` va a fallar hasta migrar esas filas a otro valor primero |
| **Migración `galeria_hd`** | Reversible en columnas (`DROP COLUMN preview_url`, `DROP COLUMN edit_type`) — pero `hd_url`/`hd_status` del backend dependen de `edit_type`, revertir esto rompe el botón de HD de la galería |
| **Migración `creditos_reservados`** | **La más delicada — ver §2.2.** No revertir `deduct_user_credit` mientras haya jobs `processing` con `credit_reserved=true` (riesgo de doble cobro). Las columnas (`credit_reserved`, `failure_alert_sent`) y el trigger son seguros de dejar aunque se revierta el backend: sin el backend nuevo llamando a `reserve_credit`, todos los jobs nuevos quedan con `credit_reserved=false` y la función redefinida los trata exactamente como la vieja lo hacía |
| **Migración `progreso_detalle`** | Reversible sin riesgo: `ALTER TABLE jobs DROP COLUMN IF EXISTS progress_detail;` — el frontend ya maneja `null`/ausente sin romperse |

---

## 6. Checklist para Agustín (10 líneas)

1. [ ] Backup/snapshot de la base de Supabase de la beta antes de arrancar (por las dudas, aunque las migraciones son aditivas).
2. [ ] `supabase link` al proyecto de la beta (si no está linkeado) y `supabase db push` — confirmar las 6 migraciones con las queries de §2.
3. [ ] Cargar en Render: `MAX_VIDEO_MINUTES`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ADMIN_USER_IDS` (ya están en `render.yaml`, solo falta poner el valor en el dashboard).
4. [ ] Deploy del backend (push a `main` o redeploy manual en Render) — confirmar `GET /health` en verde.
5. [ ] En el VPS: cargar `TRANSCRIPT_SOURCE=whisper_full`, `REFRAME_MODE=auto` en `.env` (`SUBTITLE_STYLE_DEFAULT` no hace falta, ya es el default).
6. [ ] `bash deploy/deploy-worker.sh` (o los 3 comandos de §3.1 a mano) — anotar tamaño y tiempo real de build.
7. [ ] Verificar dentro del contenedor: `import cv2, scenedetect` sin error, `fonts/`/`models/` con los archivos (§3.4) — NO usar `fc-list` para Bangers, no la va a encontrar aunque esté todo bien.
8. [ ] Encolar un video corto (≤10 min) real y seguir los logs — confirmar Split/Fill, subtítulos v2, y que el job termina.
9. [ ] Confirmar que Vercel ya tiene el frontend nuevo desplegado (autodeploy) y que la galería/pantalla de progreso se ven bien contra un job real.
10. [ ] Avisar cuándo terminó — a partir de acá, cualquier rollback de `creditos_reservados` espera a que no haya jobs `processing` con `credit_reserved=true` (§5).

---

## 7. Verificación — qué se corrió y qué no

- **Docker:** no está instalado en esta Mac (`docker: command not found`; tampoco Colima/Podman). Por instrucción explícita de la tarea, no lo instalé. No pude construir la imagen, medir tamaño/tiempo reales, ni correr `python -c "import cv2, scenedetect"` ni `fc-list` dentro de un contenedor real. §3.2/§3.3 quedan basados en lectura de código (Dockerfile, requirements.txt, .dockerignore) y en una verificación de `pip`/`uv` real hecha en una tarea anterior (W5, fuera de Docker) que reproduce la misma secuencia de instalación.
- **`supabase start`** (para aplicar las 6 migraciones sobre una base local vacía): también requiere Docker — falla con `LegacyDockerLifecycleInspectError: docker: command not found (podman also not found)`. La verificación de dependencias entre migraciones (§2.1) se hizo leyendo las seis a mano.
- **Grep de variables de entorno** (§1.3): sí se corrió completo — todas las `os.getenv`/`os.environ.get` de `worker/**/*.py` y `process.env.` de `backend/src/**/*.js`, cruzadas contra `.env.example`. Encontró 1 variable real sin documentar (`SUBTITLE_STYLE_DEFAULT`, corregido).
- **Suite de tests** (no pedida explícitamente por esta tarea, pero se corrió como parte de verificar que la rama integrada está sana antes de escribir el runbook sobre ella): worker 390/390, backend 102/102, ambos en verde sobre `docs/runbook-fase-0` con los tres merges ya hechos.
