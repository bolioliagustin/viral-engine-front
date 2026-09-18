# viral-engine — Documento de entendimiento del proyecto

**Fecha:** 16 de septiembre de 2026 · **Estado del proyecto:** parado desde el 8-jul-2026, arrancando nueva etapa (beta cerrada) · **Autor del código:** Agustín Bolioli (único desarrollador) · **Vocabulario:** [`CONTEXT.md`](../CONTEXT.md) · **Decisiones:** [`docs/adr/`](adr/) · **Guía para agentes:** [`AGENTS.md`](../AGENTS.md) · **Calidad de clips (diagnóstico y plan):** [`PLAN_CALIDAD.md`](PLAN_CALIDAD.md)

Este documento es la fuente única de entendimiento del proyecto: qué es, para quién, cómo funciona por dentro, en qué estado está y qué se decidió para la próxima etapa. Se escribió leyendo el 100 % del código (≈25 k líneas), las 15 migraciones SQL, los 127 commits y verificando la infraestructura en vivo. Cuando algo del código contradice a la documentación previa, manda el código y se señala.

---

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Producto y negocio](#2-producto-y-negocio)
3. [Mapa conceptual](#3-mapa-conceptual)
4. [Arquitectura](#4-arquitectura)
5. [El worker: pipeline paso a paso](#5-el-worker-pipeline-paso-a-paso)
6. [Capa de IA](#6-capa-de-ia)
7. [Modelo de datos](#7-modelo-de-datos)
8. [API del backend](#8-api-del-backend)
9. [Frontend](#9-frontend)
10. [Infraestructura, deploy y operación](#10-infraestructura-deploy-y-operación)
11. [Configuración](#11-configuración)
12. [Calidad y observabilidad](#12-calidad-y-observabilidad)
13. [Estado actual y hallazgos](#13-estado-actual-y-hallazgos)
14. [Deuda técnica y backlog](#14-deuda-técnica-y-backlog)
15. [Plan de la nueva etapa](#15-plan-de-la-nueva-etapa)
16. [Trabajo con agentes IA](#16-trabajo-con-agentes-ia)
- [Apéndice A — Comandos](#apéndice-a--comandos)
- [Apéndice B — Mapa de archivos](#apéndice-b--mapa-de-archivos)
- [Apéndice C — Cronología](#apéndice-c--cronología)

---

## 1. Resumen ejecutivo

**Qué es.** Un SaaS que recibe un video de YouTube (y, a partir de la nueva etapa, también el archivo del creador) y devuelve de 1 a 5 *momentos virales*: por cada uno, un **clip vertical 9:16** con subtítulos palabra por palabra y un hook de ≤4 palabras quemado en pantalla, más **copy listo para publicar** (hilo de 7 tweets, post de LinkedIn, caption de TikTok) y un **score de viralidad** calibrado por un modelo juez independiente. Incluye un editor post-clip (título, estilo de subtítulos, corrección de palabras, recorte) que re-renderiza el clip.

**Para quién.** Podcasters y coaches hispanohablantes que publican episodios largos y necesitan clips cortos para TikTok/Reels/Shorts sin editar a mano. El clip 9:16 es el valor primario; el copy es secundario.

**Cómo se cobra.** 1 crédito = 1 job. 5 créditos gratis al registrarse; plan Starter US$9/mes = 40 créditos (Lemon Squeezy). Costo de IA medido: ~US$0.18 por job frente a US$0.225 de ingreso por crédito.

**Cómo está hecho.** Tres piezas: frontend Next.js en Vercel, API Express en Render, y un worker Python en un VPS de OVH que hace todo el trabajo pesado (transcript, LLMs, descarga de YouTube, Whisper, FFmpeg, subida a Cloudflare R2). Supabase (Postgres + Auth) es la base de datos **y la cola**: la tabla `jobs` es la cola de trabajo. Diez servicios externos participan en cada job.

**Por qué se paró.** Falta de tiempo y, sobre todo, la fiabilidad de la descarga de YouTube desde un datacenter: con proxies residenciales lentos (30 KB/s) los jobs terminaban "completados" con links a YouTube en vez de clips.

**Estado hoy (verificado el 16-sep-2026).** Frontend publicado; backend vivo en Render free (cold start de 25 s); **Supabase pausado** por inactividad, por lo que el producto está caído end-to-end; VPS encendido pero el worker casi seguro detenido; todas las cuentas de terceros activas. En local (Mac) el proyecto instala y sus tests pasan (backend 27/27, worker 104/104, frontend sin errores de tipos); en GitHub Actions el CI está en rojo desde siempre por variables de entorno faltantes, y el deploy al VPS nunca dependió de él.

**Qué se decidió para la nueva etapa.** Objetivo: **beta cerrada estable** con 5–10 invitados, ~10 h/semana del dueño más agentes IA en paralelo. Decisiones clave: la **subida directa del archivo** pasa a ser el camino garantizado para obtener clips (ADR 0007); los **créditos se reservan al encolar** y se devuelven si el job falla (ADR 0005); un job es exitoso si produce **al menos un clip**; tope de **90 minutos** por video; esquema versionado con **Supabase CLI** (ADR 0006); Render pasa a plan pago; proyecto Supabase **separado para desarrollo**; trabajo en ramas con PR y deploy manual; resolución de clips **720×1280** durante la beta. Métrica de éxito: **≥90 % de los jobs con todos sus clips en <15 min durante 2 semanas**. Plan en cuatro fases (§15).

---

## 2. Producto y negocio

### 2.1 Qué recibe el usuario por cada job

| Entregable | Detalle | De dónde sale |
|---|---|---|
| 1–5 momentos | 1 si el video dura <90 s, 3 si <5 min, 5 si ≥5 min. Cada uno de 10–60 s (ideal 15–55 s), sin solapamiento >50 % | Pasada A (§6) + validadores |
| Clip MP4 9:16 por momento | 720×1280, fondo desenfocado del propio video, subtítulos blancos tipo TikTok sincronizados por palabra, overlay de ≤4 palabras en mayúsculas durante los primeros 3,5 s | FFmpeg en una sola pasada (§5.5) |
| Hilo de Twitter/X | Exactamente 7 tweets de 180–280 caracteres, sin prefijos ni "[Link]" | Pasada B |
| Post de LinkedIn | 800–1200 caracteres, hook de 3 líneas, pregunta final | Pasada B |
| Caption de TikTok | 1–2 líneas coloquiales + 3–4 hashtags | Pasada B |
| Scores | Hook, retención y compartibilidad 1–10 con justificación en texto | Juez (§6) |
| ROI estimado | Minutos "ahorrados": 8 + 0,5 × segundos de clip + 15 × piezas de copy (fórmula fija) | `scorer.py` |
| Metadatos de calidad | `verification_failed`, cobertura de subtítulos, palabras/segundo, flags (`incomplete_tail`, `late_hook`, `clip_not_rendered`…) | Pipeline por clip |
| Editor post-clip | Cambiar overlay y posición, estilo de subtítulos (`tiktok_viral`, `clean`, `podcast`), corregir palabras, resaltar palabras, recortar inicio/fin → nuevo render | Cola `clip_edits` (§5.7) |
| Personalización | Tono del copy (profesional, sarcástico, motivador, casual) elegido por job; nombre y título profesional del creador inyectados en los prompts (sin UI todavía, §13) | `jobs.tone`, `users.display_name/professional_title` |

La landing promete además "Ver demo" (botón sin acción), "usado por miles de creadores" y "videos de cualquier duración": son textos aspiracionales, no funcionalidad.

### 2.2 Cliente objetivo y propuesta de valor

- **ICP confirmado (sep 2026):** podcasters y coaches que hablan español y publican en YouTube. El clasificador del pipeline solo distingue dos categorías, *podcast* (conversación de 2+ personas) y *business* (todo lo demás: monólogos, keynotes, tutoriales), y el copy sale en el idioma del video.
- **Propuesta de valor:** "pegá el link (o subí tu episodio) y en menos de 15 minutos tenés 5 clips verticales subtitulados listos para publicar, con el texto para cada red". El diferencial frente a herramientas gratuitas es la calidad en español del corte (límites de oración, hook al inicio, verificación anti-alucinación) y de los subtítulos.
- **Inglés y otros idiomas:** el pipeline ya sigue el idioma del transcript, pero no se prioriza ni se prueba en esta etapa.

### 2.3 Créditos, planes y facturación

| Concepto | Hoy en el código | Decidido para la nueva etapa |
|---|---|---|
| Alta de usuario | Trigger `handle_new_user` en `auth.users` crea la fila en `public.users` con **5 créditos** | Igual (la página de precios dice "3": corregir) |
| Consumo | Se valida `credits > 0` al encolar; el worker descuenta 1 al completar con el RPC atómico `deduct_user_credit` (registra fila en `transactions`) | **Reservar al encolar, devolver si el job falla** (ADR 0005). Job exitoso = ≥1 clip renderizado |
| Plan Starter | US$9/mes, 40 créditos; Lemon Squeezy: `subscription_created` → plan `starter`, 40 créditos; `subscription_payment_success` (renovación) → resetea a 40; `subscription_updated/cancelled/expired` → plan `free` | Igual; sin cobro real hasta cumplir la métrica de la beta |
| Precio por crédito | `CREDIT_PRICE_USD = 9/40 = 0,225` (usado por el panel admin para margen) | Igual |
| Duplicados | Misma URL completada en los últimos 7 días → 409 | Igual |
| Rate limit | 5 `POST /process` cada 15 min por usuario | Igual |

### 2.4 Economía unitaria (medida en julio de 2026, 1 job de ~30–60 min, 5 clips)

| Componente | Costo por job | Notas |
|---|---|---|
| Clasificador (Gemini 2.5 Flash-Lite) | ~US$0.0001 | 1 llamada |
| Pasada A (Gemini 3.5 Flash, reasoning low) | ~US$0.14 | 1 llamada, 20–50 k tokens de entrada |
| Pasada B (Gemini 3.5 Flash) ×5 | ~US$0.035 | ~US$0.007 por clip |
| Juez (GPT-5.4 nano) ×5 | ~US$0.0014 | |
| Whisper (Groq large-v3-turbo) ×5 | ~US$0.0025 | US$0.04/hora de audio |
| **Total IA** | **~US$0.18** | vs. US$0.225 de ingreso por crédito → ~20 % de margen bruto solo sobre IA |
| No medido por job | Supadata, RapidAPI, proxies Webshare, R2, VPS | Costos fijos mensuales (§10) |

Fuente: `docs/RECOMENDACION_MODELOS_LLM.md` §5 y `job_usage_events`. El panel `/admin/usage` lo muestra en vivo.

### 2.5 Marca y nombre

El producto se llama "ViralEngine" en la interfaz, "YouTube Viral Content Engine" en README y docs, `viral-engine-front` en GitHub y `mvp_p1` en rutas antiguas de Windows. **El nombre definitivo está en discusión**; el nombre de trabajo es `viral-engine`. Atención: `viralengine.app` pertenece a un tercero y la página de precios usa `hola@viralengine.app` como contacto (dominio ajeno): hay que reemplazarlo. La decisión de marca condiciona el dominio propio, que a su vez habilita mover el API al VPS (§15).

---

## 3. Mapa conceptual

El vocabulario canónico vive en [`CONTEXT.md`](../CONTEXT.md). Relaciones entre conceptos:

```mermaid
flowchart LR
    U[Usuario] -->|tiene| C[Créditos]
    U -->|tiene| P[Plan free / starter]
    U -->|crea| J[Job]
    J -->|usa 1| C
    J -->|desde una| F[Fuente: link YouTube o archivo]
    F -->|produce| T[Transcript]
    T -->|Pasada A| M[Momento ×1..5]
    M -->|render| K[Clip 9:16]
    M -->|Pasada B| Y[Piezas de copy: hilo, post, caption]
    M -->|Juez| S[Score hook / retención / compartibilidad]
    K -->|el usuario pide| E[Edición]
    E -->|nuevo render| K2[Clip editado]
    J -->|elige| TN[Tono]
    U -->|perfil de creador| Y
```

Reglas de dominio que el código ya aplica o que se decidieron:

- Un job pertenece a un usuario; solo él lo ve (RLS) y solo él puede reintentarlo o borrarlo.
- Un job en `pending`/`processing` no se puede borrar; uno `failed` o `completed` se puede reintentar (vuelve a `pending`, mismo id).
- Un momento sin clip renderizado muestra un link a YouTube con timestamp (degradación explícita).
- **Nuevo:** un job sin ningún clip es `failed` y devuelve el crédito.
- Una edición siempre parte del último borrador guardado; mientras está `queued`/`processing` no se puede volver a encolar.

---

## 4. Arquitectura

### 4.1 Componentes

```mermaid
flowchart TB
    subgraph Cliente
        B[Navegador]
    end
    subgraph Vercel
        FE[Frontend Next.js 16]
    end
    subgraph Render
        API[Backend Express]
    end
    subgraph Supabase
        AUTH[Auth]
        DB[(Postgres: users, jobs = cola, content_results, clip_edits, caches, job_usage_events)]
    end
    subgraph OVH VPS
        W[Worker Python 3.12 en Docker]
    end
    subgraph Cloudflare
        R2[(R2: clips, raw_clips, clip_edits)]
    end
    subgraph Terceros
        SD[Supadata transcripts]
        YT[YouTube / googlevideo]
        RA[RapidAPI yt-api]
        PX[Proxies Webshare]
        OR[OpenRouter: Gemini, GPT]
        GQ[Groq Whisper]
        OA[OpenAI Whisper fallback]
        LS[Lemon Squeezy]
        SE[Sentry]
    end
    B --> FE
    FE -->|JWT| API
    FE -->|lectura directa con RLS| DB
    FE --> AUTH
    API --> DB
    API --> LS
    LS -->|webhook| API
    W -->|poll cada 3 s, claim atómico| DB
    W --> SD
    W --> RA
    W --> PX --> YT
    W --> OR
    W --> GQ
    W --> OA
    W --> R2
    B -->|reproduce / descarga| R2
    API --> SE
    W --> SE
```

| Componente | Responsabilidad | No hace |
|---|---|---|
| **Frontend** (`frontend/`) | UI, login (Google OAuth y magic link), dashboard, resultados, editor, precios, cuenta, panel admin. Lee `jobs`, `users`, `content_results` **directo de Supabase** con la anon key y RLS; las mutaciones van por el API | Nada de IA ni de video |
| **Backend** (`backend/`) | Validar JWT, crear jobs, estado, reintentar/borrar, créditos, checkout y webhook de Lemon Squeezy, guardar ediciones y encolarlas, métricas admin | No procesa video; no llama LLMs; no descuenta créditos |
| **Worker** (`worker/`) | Todo el procesamiento: transcript, clasificación, selección, descarga, Whisper, copy, juez, FFmpeg, R2, guardar resultados, descontar crédito, re-renders, métricas de costo | No expone API (solo un `/health` opcional si `PORT` está seteado) |
| **Supabase** | Auth, datos, cola, RPCs (`deduct_user_credit`, `check_duplicate_job`, `get_job_usage_summary`), trigger de alta | — |
| **R2** | Hosting público de MP4 (`{job_id}/clip_{n}.mp4`), cache de segmentos crudos (`raw_clips/{job_id}_{n}.mp4`), clips editados (`clip_edits/{edit_id}.mp4`) | — |

### 4.2 Flujo end-to-end de un job

```mermaid
sequenceDiagram
    participant U as Usuario
    participant F as Frontend (Vercel)
    participant A as API (Render)
    participant S as Supabase
    participant W as Worker (VPS)
    participant X as Terceros
    participant R as R2

    U->>F: pega URL + elige tono
    F->>A: POST /process (Bearer JWT)
    A->>S: auth.getUser(JWT) · check_duplicate_job · credits mayor a 0
    A->>S: INSERT jobs (status=pending, tone)
    A-->>F: 201 {jobId}
    F->>U: /results/:jobId (pantalla de progreso, poll cada 3 s)
    loop cada 3 s
        W->>S: SELECT pending LIMIT slots · UPDATE processing WHERE status=pending
    end
    W->>X: Supadata transcript + oEmbed título
    W->>X: OpenRouter clasificador → Pasada A
    W->>X: RapidAPI stream URLs / yt-dlp (vía proxy residencial)
    loop por momento (1..5)
        W->>W: pre-corte FFmpeg
        W->>X: Groq Whisper (words)
        W->>W: snap silencios · límites de oración · ancla del hook · verificación
        W->>X: OpenRouter Pasada B (copy) → Juez
        W->>R: raw clip (cache)
        W->>W: FFmpeg 9:16 + subs + overlay
        W->>R: clip_{n}.mp4
        W->>S: INSERT content_results ×3 (twitter_thread, linkedin_post, tiktok_caption)
    end
    W->>S: UPDATE jobs completed · RPC deduct_user_credit · usage_summary
    F->>A: GET /status/:jobId → resultados
    U->>F: ver clips, copiar copy, descargar, editar
```

### 4.3 Decisiones de arquitectura ya registradas

- [ADR 0001](adr/0001-la-tabla-jobs-es-la-cola.md): la tabla `jobs` es la cola (sin Redis).
- [ADR 0002](adr/0002-copy-en-dos-pasadas-desde-el-audio-real.md): el copy se escribe desde el audio real del clip (dos pasadas).
- [ADR 0003](adr/0003-juez-de-scores-de-otra-familia-de-modelos.md): juez de otra familia de modelos.
- [ADR 0004](adr/0004-worker-en-vps-propio-con-proxies-residenciales.md): worker en VPS propio con proxies residenciales; API en Render.

---

## 5. El worker: pipeline paso a paso

Entry point: [`worker/main.py`](../worker/main.py). Documentación previa del autor: [`worker/WORKER.md`](../worker/WORKER.md) (buena, mantenerla alineada con este documento).

### 5.1 Arranque

1. Carga `.env` desde la raíz del repo.
2. Inicializa Sentry (`SENTRY_DSN_WORKER`, con DSN hardcodeado de fallback: §13).
3. `setup_logging()`: redirige todos los `print()` a logging con contexto (`job=… m=… phase=…`) y archivos rotativos `worker-logs/worker.log` (20 MB × 5) y `worker-error.log`.
4. `validate_env()`: exige `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`; en `ENVIRONMENT=production` exige además `RAPIDAPI_KEY` y advierte si faltan proxies, Groq, Supadata, R2 o cookies; imprime los modelos LLM resueltos y avisa si alguno es `:free` o Gemini 2.0 (apagado en junio de 2026).
5. `cleanup_old_files()`: borra archivos >24 h en `downloads/` y `clips/`.
6. `recover_stale_jobs()`: jobs en `processing` con `updated_at` >20 min → `failed` ("Worker crashed…").
7. `start_keepalive()`: ping a Supabase cada 45 s (evita cierre de conexión idle). **Efecto colateral útil:** mientras el worker corre, el proyecto Supabase free nunca se pausa por inactividad.
8. `watch_queue()`: loop infinito.

### 5.2 Loop y reclamo de jobs (`watch_queue`)

- `ThreadPoolExecutor(max_workers=MAX_WORKERS)`; default en código 2, en `docker-compose*.yml` 3.
- Cada 3 s (`POLL_INTERVAL`): si hay slots libres, `SELECT id, video_url, user_id, tone FROM jobs WHERE status='pending' ORDER BY created_at LIMIT slots`; por cada uno, `UPDATE jobs SET status='processing' WHERE id=? AND status='pending'`; si el UPDATE no devuelve fila, otro worker lo ganó y se salta.
- Después de los jobs, reclama **como máximo una** edición (`claim_next_clip_edit`) por iteración.
- Timeout por job: 30 min (`threading.Timer` + `check_timeout()` entre pasos). No mata el hilo: el job sigue hasta el siguiente checkpoint.
- No hay afinidad de worker: dos workers contra la misma base se reparten los jobs (por eso dev usa su propio proyecto Supabase, §15).

### 5.3 Pasos de un job (`_process_job_inner`)

| Paso | `current_step` / progreso | Módulo | Qué pasa |
|---|---|---|---|
| 1–2 Transcript | `downloading` 10 → 40 | `services/yt_transcript.py` | Supadata (`/v1/youtube/transcript`) → segmentos con timestamps; fallback `youtube-transcript-api` solo fuera de producción. Metadatos por oEmbed (título, autor; **duración = 0**, se infiere del último segmento + 5 s). Guarda en `transcription_cache` (nunca se lee después, §13) |
| 3 Análisis | `analyzing` 50 → 65 | `services/processor.py` | Perfil del usuario (`display_name`, `professional_title`) + tono → `analyze_with_openrouter`: cache `analysis_cache` (clave `video_id + modelo + tono + PROMPT_VERSION=v4`) → clasificador (cache `category_cache`) → transcript compacto (~50 % menos tokens) → **Pasada A** (`moment_selector.py`) → saneo del JSON (json_repair, arrays de 1 elemento, `surgical_clipping` → `start_time/end_time`, `shareability` faltante) → validadores de contenido → `AnalysisResult` (Pydantic) → guarda en cache |
| 3.5 Filtro | — | `services/validation.py` | `validate_durations` (10–60 s; el truncado a 60 s snapea al fin de segmento) · `filter_overlapping_moments` (>50 % → descarta) · `validate_against_transcript` (frases citadas). Si no queda ningún momento, el job falla |
| 4 Descarga | `downloading` 70 → 80 | `main.py` + `services/downloader.py` | Selector de estrategia (§5.4) y descarga upfront o per-clip. Exige `RAPIDAPI_KEY` en producción |
| 5 Clips | `generating` 85 | `clip_generator.py`, `transcriber.py`, `processor.py`, `scorer.py`, `supabase_client.py` | Por cada momento: sub-pipeline §5.5 |
| 6 Cierre | `completed` 100 | `supabase_client.py`, `usage_tracker.py` | `UPDATE jobs completed` → `deduct_user_credit` (RPC) → `usage_summary` → limpieza de archivos. Si cualquier paso lanza excepción: `failed` + `error_message`, sin descuento |

Los pasos `transcribing` y `clipping` que muestra la pantalla de progreso del frontend **nunca los emite el worker** (usa `downloading` dos veces): detalle cosmético a corregir.

### 5.4 Estrategias de descarga de video (el punto crítico)

YouTube bloquea IPs de datacenter; el worker combina cuatro mecanismos: **yt-dlp** (con cascada de `player_client` tv/ios/android/web y cookies opcionales), **RapidAPI yt-api** (devuelve URLs de stream de googlevideo), **proxies residenciales Webshare** (lista en `proxies.txt`; las URLs de googlevideo quedan atadas a la IP que las resolvió, por eso se usa el mismo proxy "sticky" para resolver y descargar) y **Apify** (actor externo, opcional). En producción (`ENVIRONMENT=production` o `USE_RAPIDAPI_DOWNLOAD=true`) se prefiere RapidAPI + proxy.

Selector automático (`_select_download_strategy`, override con `DOWNLOAD_STRATEGY`):

| Estrategia | Cuándo | Qué hace |
|---|---|---|
| `full_ytdlp` | Video ≤30 min, RapidAPI no forzado, hay proxies | yt-dlp baja el video completo con cascada de formatos (720p avc1 → … → `worst`) |
| `upfront_partial` | Clips concentrados en el primer 35 % del video y poco tiempo total de clip | Stream URLs (yt-dlp+proxy o RapidAPI) → descarga secuencial de bytes 0→`max_end` de video y audio (audio completo) → mux FFmpeg → valida frames hasta `max_end`; si el truncado por ratio deja corto el video, re-descarga el stream completo |
| `per_clip_parallel` | Clips dispersos o video largo | yt-dlp `download_ranges` ±8 s de margen de keyframe, 3 en paralelo, un proxy distinto por clip, con presupuesto total `DOWNLOAD_PHASE_BUDGET_SEC=600` |

Si el paso 4 no dejó video listo, cada clip intenta en orden (`_resolve_moment_video_source`): segmento per-clip cacheado → video muxeado → yt-dlp per-clip (rotando proxy en reintentos) → Apify (si `USE_APIFY_FALLBACK`) → stream partial (solo si el clip está en el primer 20 % del video) → **fallback final: link a YouTube con `&t=`** (el momento queda sin MP4). Los tiempos de descarga, MB y clips fallidos se registran como evento `download` en `job_usage_events`.

Perillas: `DOWNLOAD_STRATEGY`, `DOWNLOAD_PARALLEL_WORKERS=3`, `CLIP_MARGIN_BEFORE_SEC=15` / `CLIP_MARGIN_AFTER_SEC=20` (alias legacy `CLIP_KEYFRAME_MARGIN_SEC`), `DOWNLOAD_PHASE_BUDGET_SEC=600`, `CLIP_SYNC_RETRIES=2`, `STRICT_SYNC_VALIDATION=true`, `YTDLP_CLIP_FALLBACK`, `USE_APIFY_FALLBACK` + `APIFY_TOKEN`, `WEBSHARE_PROXY_FILE|LIST|URL`, `YOUTUBE_COOKIES` (base64 o texto Netscape), `RAPIDAPI_KEY`, `USE_RAPIDAPI_DOWNLOAD`.

**No hay tope de duración efectivo:** `validate_video_duration` (2 h) solo se llama desde `download_audio`, un camino legacy que ya no se usa. Se decidió un tope de 90 min para la beta.

### 5.5 Sub-pipeline por momento (el corazón de la calidad)

Desde W1 ([`PLAN_CALIDAD.md`](PLAN_CALIDAD.md) §4) **la verdad son las frases de Verificación** (`first_phrase_in_audio` / `last_phrase_in_audio`, texto real del transcript); `start_time`/`end_time` solo dicen qué descargar. Orden: segmento ancho → Whisper → frases → límites → corte final. Si el momento no trae frases (jobs legacy, prompt sin verificación) corre el flujo numérico anterior (`_refine_bounds_legacy`: snap → oraciones → anclas → duración mínima).

1. **Fuente y segmento ancho** (`_resolve_moment_video_source` → `_MomentSource` con el rango absoluto disponible): el segmento per-clip se descarga con márgenes asimétricos `CLIP_MARGIN_BEFORE_SEC=15` / `CLIP_MARGIN_AFTER_SEC=20` (`CLIP_KEYFRAME_MARGIN_SEC` sigue como alias que setea ambos); `upfront_partial` descarga hasta `max(end_time) + 20 + 25 s`. Se pre-corta `wide.mp4` = `[start_time − 15, end_time + 20] ∩ disponible` (`cut_clip`, re-encode).
2. **Whisper sobre el segmento ancho** (`_transcribe_with_guards` → `transcriber.py`): extrae audio (loudnorm, 16 kHz mono), prompt de contexto (vocabulario de marca + título + slice del transcript del rango ancho, ≤800 chars) y **Groq `whisper-large-v3-turbo`** con `timestamp_granularities=[segment, word]`; fallback OpenAI `whisper-1`. Post-proceso: filtra alucinaciones y palabras fuera de rango, ghost words, correcciones fonéticas de marca. Las palabras quedan en la línea de tiempo del segmento ancho. Guardas W3 (`assess_whisper_words`): `bad_segment` → una re-descarga con otro proxy y, si persiste, corte numérico **sin subtítulos**; `timestamps_suspect` → se re-transcribe **una vez con el otro proveedor** (`provider="openai"|"groq"`) y, si el segundo también es sospechoso, corte numérico sin subtítulos + `subs_disabled_timestamps`.
3. **Frases → límites** (`validation.py`, función pura `compute_clip_bounds`): `locate_phrase` (matching fuzzy en orden, sin acentos ni puntuación, tolera 1 de 4 palabras distinta y 2 insertadas; `prefer="first"` para la primera frase, `"last"` para la última buscada **después** de la primera) y `sentence_bounds_around` (retrocede/avanza hasta puntuación, gap >0,6 s o fin de segmento Whisper, con tope de 10 s). Inicio = inicio de oración de la primera frase − 0,25 s; fin = fin de oración de la última + 0,40 s. Si la última no aparece y el segmento no llega al final del video, se **re-descarga una vez con +25 s** (`margin_extended`) y se repite 2–3; si sigue sin aparecer → `payoff_not_found` y el primer fin de oración en o después de `end_time` que deje 15–60 s. Si la primera no aparece → `hook_not_found` y ancla de hook/overlay cerca de `start_time`, o el inicio de oración más cercano ≥ `start_time − 3 s`. Reglas duras: 15–60 s (<15: extender al siguiente fin de oración; >60: mover el inicio al siguiente inicio de oración antes que perder el remate; si igual no entra, cortar al fin de oración ≤60 s y flaggear), nunca arrancar en minúscula si hay un inicio de oración ≤2 s antes.
4. **Corte final y verificación**: `cut_clip(wide.mp4, start_rel, end_rel)` → `precut.mp4`; las palabras y segmentos se desplazan y filtran a la línea de tiempo del clip final (0-based, `shift_words_timeline` + `filter_whisper_words`), que es lo que ven los subtítulos, el cache raw y `whisper_words` (el editor sigue funcionando igual). La Verificación pasa **por construcción** cuando ambas frases se localizaron (`whisper_mismatch_first|last` solo si una no apareció); el chequeo textual clásico (primeras/últimas 5 palabras) se loguea como evidencia. `late_hook` = la primera frase aparece >3 s después del inicio del clip; `incomplete_tail` como antes (omitido si el fin es un fin de oración localizado). Métricas: `sub_coverage`, `words_per_sec`.
5. **Reintento de sincronía**: si `sub_coverage < 0,9` y `STRICT_SYNC_VALIDATION`, re-descarga el segmento con otro proxy (hasta `CLIP_SYNC_RETRIES`); `bad_segment` re-descarga **una** vez; la extensión de margen por remate ausente es otra re-descarga como máximo. Un mismatch de frases con buena cobertura **no** re-descarga (indica análisis viejo, no descarga mala).
6. **Pasada B** (`generate_moment_copy_full`): genera hilo, post, caption, hook y overlay definitivos desde el texto Whisper del clip (≤4000 chars), con tono y perfil del creador; `clean_moment` valida (7 tweets, rango de LinkedIn, `[Link]`, prefijos, clichés) y reintenta una vez si falla el conteo/longitud. Si no hay texto de clip, "copy rescue" con el slice del transcript. **Fidelidad (W6, `PLAN_CALIDAD.md` §4):** el prompt recibe además la primera y la última oración del clip (derivadas del propio texto; sin puntuación detectable, ambas caen al texto completo) y exige que el hook sea algo que la persona REALMENTE dice, no una promesa del tema. Tras la respuesta se valida overlay (al menos una palabra sin stopword tiene que estar en los primeros ~8 s, aproximados por cantidad de palabras porque esta función no recibe timestamps por palabra) y hook (subsecuencia difusa contra el texto completo, tolera 1 de 4 palabras distinta) con `services/content_validators.py`; si alguno falla se regenera **una** vez con una corrección explícita en el prompt, y si persiste se cae a un fallback determinístico (`derive_overlay_from_text` / primera oración) marcando `overlay_no_fiel` / `hook_no_fiel` en `moment.clip_quality_issues`.
7. **Juez** (`judge_moment_scores`): puntúa el texto real del clip + overlay + hook contra una rúbrica con anclas; devuelve `hook/retention/shareability/reasoning` (sin truncar — se muestra completo en la UI como "Por qué este score", W7). Si falla, quedan los scores de la Pasada A (`score_llm`).
8. **Cache de raw clip**: sube `precut/snapped.mp4` a `raw_clips/{job}_{n}.mp4` y guarda `whisper_words` para que el editor re-renderice sin volver a YouTube ni a Whisper ("Plan C").
9. **Render final** (`generate_clip`, una sola llamada FFmpeg): `split` → fondo escalado+recortado a 720×1280 con `boxblur` → primer plano a ancho completo centrado → filtro `ass=` de subtítulos (SRT generado desde palabras, ≤4 palabras por línea, convertido a ASS con estilo `tiktok_viral`) → filtro `ass=` del overlay (3,5 s, arriba) → `libx264 veryfast crf 23`, `aac 128k`, `+faststart`. Valida dimensiones. Estilos disponibles: subtítulos `tiktok_viral|clean|podcast`, overlay `tiktok_viral|question|stat`, posición `top|center|bottom`.
10. **Subida y persistencia**: `upload_clip_to_storage` → `{job_id}/clip_{n}.mp4` en R2 → `save_content_result` ×3 (una fila por tipo de pieza: `twitter_thread`, `linkedin_post`, `tiktok_caption`) **repitiendo en cada fila** todos los metadatos del momento (clip_url, tiempos, hook, scores, overlay, raw_clip_url, whisper_words, métricas de calidad). Si faltan columnas de calidad (migración `ai_quality` sin correr), reintenta sin ellas.
11. **Degradación**: cualquier excepción en el clip → `clip_url = https://www.youtube.com/watch?v=…&t=Ns`, `clip_generation_error`, flags `clip_not_rendered`; el copy se genera igual ("rescue"). Los scores mostrados solo son los del juez, o los de la Pasada A si el clip se renderizó; sin clip y sin juez no se muestran scores inflados.
    Guardas de sanidad sobre Whisper (`assess_whisper_words`, `enforce_min_duration`; PLAN_CALIDAD §4 W3), persistidas en `clip_quality_issues`:
    - `timestamps_suspect`: el texto Whisper tiene densidad normal pero los tiempos están comprimidos (>5 palabras/s sobre el tramo con habla, o hueco inicial >40 % del clip con ≥1,5 palabras/s). El snap por silencio y el refinamiento se omiten y el clip queda entero (antes: 33 s → 9 s con 72 palabras).
    - `bad_segment`: el segmento descargado no tiene habla plausible (<1,2 palabras/s, <8 palabras únicas, <35 % de palabras únicas o una secuencia de ≥4 palabras repetida que cubre >40 % del texto). Se re-descarga **una** vez con otro proxy/estrategia; si persiste, el clip se renderiza **sin subtítulos** y con el flag (antes: "O R m Y TleK E" se entregaba sin aviso).
    - `min_duration_reverted`: snap + oraciones + anclas dejaron el clip <15 s; se vuelve al último conjunto de límites que cumplía (o a los originales).
    Cortes anclados a frases (`compute_clip_bounds`, W1):
    - `hook_not_found`: `first_phrase_in_audio` no apareció en el segmento ancho (o quedó afuera al mover el inicio para respetar 60 s); el inicio salió de la ancla de hook o de `start_time − 3 s`.
    - `payoff_not_found`: `last_phrase_in_audio` no apareció ni tras extender el margen, o no entró en los 60 s; el fin es un fin de oración de respaldo.
    - `margin_extended`: se re-descargó el segmento con +25 s al final para buscar la última frase.
    - `subs_disabled_timestamps`: Groq y OpenAI dieron timestamps sospechosos; el clip se renderizó sin subtítulos y con corte numérico.
    Fidelidad de copy (`generate_moment_copy_full`, W6), guardadas en `moment.clip_quality_issues`:
    - `overlay_no_fiel`: el overlay no tenía ninguna palabra con carga semántica en los primeros ~8 s del clip ni tras regenerar una vez; se reemplazó por un overlay derivado del texto real.
    - `hook_no_fiel`: el hook no aparecía como subsecuencia difusa del texto del clip ni tras regenerar una vez; se reemplazó por la primera oración real.
    Estos dos flags viven en el `moment` (no en la lista local `clip_quality_issues` que arma el paso 10 de acá abajo para `save_content_result`); falta un merge de una línea en `main.py` para que lleguen a `content_results.clip_quality_issues` — ver PR de W6.

### 5.6 Cierre del job

`update_job_progress(completed, 100)` → `update_job_status(completed)` → `deduct_credit` (RPC `deduct_user_credit`: `UPDATE users SET credits = credits - 1 WHERE credits > 0` + fila en `transactions`; si falla solo se loguea) → `finalize_job_usage` (rollup en `jobs.usage_summary`) → limpieza (`cleanup_all`, `cleanup_clips`). Un job con 0 MP4 hoy se marca `completed` igual y **consume crédito**: cambia en la nueva etapa (§15).

### 5.7 Cola secundaria: ediciones (`clip_edit_processor.py`)

1. Frontend guarda un borrador en `clip_edits` (`POST /api/clips/:id/edit`, status `draft`) y pide `POST /api/clips/:id/regenerate` → `queued`.
2. El worker reclama la más antigua (`queued` → `processing`), resuelve `content_result` y `job`, y obtiene el segmento: **cache R2** (`raw_clip_url` o `raw_clips/{job}_{n}.mp4` vía S3) → stream partial RapidAPI (solo clips tempranos) → yt-dlp `download_ranges` → stream partial.
3. Palabras: `whisper_words` cacheadas (con lógica para raw pre-snap vs post-snap y jobs legacy) o Whisper de nuevo.
4. Aplica `word_corrections` (por timestamp con tolerancia 0,05 s, fallback por índice) y `word_styles` (ASS por palabra: `default|highlight|emphasis`, color), `trim_start_offset`/`trim_end_offset`, `overlay_text`, `overlay_position`, `subtitle_style`.
5. `generate_clip` a 720×1280 → `clip_edits/{edit_id}.mp4` → `completed` + `rendered_clip_url`; el frontend reemplaza el clip mostrado. Errores → `failed` + `error_message`. `music_track_id` existe en la tabla pero la música de fondo es un placeholder "en desarrollo".

### 5.8 Caches

| Cache | Dónde | Clave | Ahorra | Estado |
|---|---|---|---|---|
| Transcript | `transcription_cache` | `video_id` | Llamada a Supadata | **Se escribe pero no se lee** en el pipeline (solo en `eval/`) |
| Análisis | `analysis_cache` | `video_id + model + tone + prompt_version` | Pasada A (~30–60 s y ~US$0.14) | Activo; invalidar subiendo `PROMPT_VERSION` en `analysis_cache.py` o con `worker/scripts/invalidate-analysis-cache.py` |
| Categoría | `category_cache` | `video_id + model` | Clasificador | Activo |
| Raw clip + words | R2 `raw_clips/` + `content_results.raw_clip_url/whisper_words` | por momento | Re-descarga y Whisper en ediciones | Activo |

### 5.9 Medición de uso y costo (`usage_tracker.py`, `pricing.py`, `context/job_context.py`)

Cada llamada LLM (vía `log_llm_usage`), cada Whisper, cada fase de descarga y cada cache hit inserta una fila en `job_usage_events` (task, modelo, proveedor, tokens de entrada/salida/razonamiento, segundos de audio, costo estimado con la tabla de precios de `pricing.py` —override con `PRICING_OVERRIDES_JSON`—, latencia, `moment_index`, metadata). Al terminar el job se escribe el rollup en `jobs.usage_summary`. `PERSIST_USAGE_EVENTS=false` lo apaga. El contexto (job, usuario, momento, edición) viaja por `contextvars`, así que las ediciones también se atribuyen al job padre.

### 5.10 Constantes y límites

| Constante | Valor | Dónde |
|---|---|---|
| Poll de la cola | 3 s | `main.py` |
| Timeout por job | 30 min | `main.py` |
| Job zombie | `processing` >20 min al arrancar → `failed` | `main.py` |
| Limpieza de archivos | >24 h | `main.py` |
| Momentos finales | 1 / 3 / 5 según duración | `moment_selector.py` |
| Candidatos Pasada A | `min(12, minutos)`, al menos target+1 | `moment_selector.py` |
| Duración de momento | 10–60 s | `validation.py` |
| Solapamiento máximo | 50 % (el prompt pide 20 %) | `validation.py` |
| Resolución de clip | 720×1280 | `main.py`, `clip_edit_processor.py` |
| Overlay | 3,5 s, arriba, ≤4 palabras | `clip_generator.py` |
| Whisper por trozos | audio >20 min, trozos de 2 min | `transcriber.py` |
| Presupuesto de descarga | 600 s | env |
| Memoria del contenedor | 10 GB | `docker-compose*.yml` |
| Tope de duración de video | **ninguno** (2 h solo en código legacy) | — |

---

## 6. Capa de IA

Detalle completo en [`docs/INFORME_LLMS.md`](INFORME_LLMS.md) y [`docs/RECOMENDACION_MODELOS_LLM.md`](RECOMENDACION_MODELOS_LLM.md). Configuración central en [`worker/config/model_tiers.py`](../worker/config/model_tiers.py) y [`worker/config/llm_chat.py`](../worker/config/llm_chat.py). Todas las llamadas de chat van por **OpenRouter** con el SDK de OpenAI.

| Tarea | Env | Default (jul 2026) | Temperatura | `max_tokens` | Reasoning | Llamadas/job | Cache |
|---|---|---|---|---|---|---|---|
| Clasificador | `MODEL_CLASSIFIER` | `google/gemini-2.5-flash-lite` | 0.0 | 5 | — | 1 | `category_cache` |
| Pasada A | `MODEL_ANALYSIS` (legacy `OPENROUTER_MODEL`) | `google/gemini-3.5-flash` | omitida (Gemini 3.x) | 16000 | `low` | 1 | `analysis_cache` |
| Pasada B | `MODEL_COPY_WRITING` / `MODEL_COPY` | `google/gemini-3.5-flash` | omitida | 4000 | `minimal` | ~5 (+reintentos) | no |
| Juez | `MODEL_JUDGE` | `openai/gpt-5.4-nano` | omitida (GPT-5.x) | 400 | `none` | ~5 | no |
| Whisper | `GROQ_API_KEY` → `OPENAI_API_KEY` | `whisper-large-v3-turbo` → `whisper-1` | — | — | — | ~5 | `whisper_words` (solo ediciones) |

- Override de reasoning por tarea: `MODEL_{ANALYSIS|COPY|JUDGE}_REASONING`. Gemini 3.x y GPT-5.x razonan por defecto y los tokens de razonamiento consumen `max_tokens` (por eso Pasada A subió a 16000).
- Prompts: Pasada A en `moment_selector.py` (podcast vs business); mega-prompt legacy en `processor.get_dynamic_prompt` (+ `podcast_prompt.py`, `category_prompts.py` para entertainment, apagado salvo `ENABLE_ENTERTAINMENT_CATEGORY=true`); Pasada B en `processor.generate_moment_copy_full`; juez en `scorer.py`. Todos fuerzan el idioma de salida al del transcript (`output_language_instruction`).
- `PROMPT_VERSION = "v4"` en `analysis_cache.py`: subirlo invalida el cache de análisis. Hay que subirlo cada vez que cambian prompts o el modelo de la Pasada A.
- `TWO_PASS_ANALYSIS=false` fuerza el mega-prompt legacy; `COMPACT_TRANSCRIPT=false` desactiva el transcript compacto.
- **Riesgo:** los proveedores retiran modelos con poco aviso (Gemini 2.0 se apagó el 1-jun-2026 y el clasificador cayó en silencio al fallback de keywords hasta julio). Verificar los ids en OpenRouter al reactivar y correr el golden set.

### Golden set (`worker/eval/`)

`run_golden_set.py --tier smoke|analysis|full` sobre `golden_set.json` (4 videos habilitados: business ES, podcast, un video de regresión "claude_hacks", etc.). Métricas: `category_accuracy`, `duration_pass_rate`, `phrase_anchor_pass_rate` (clave), `copy_clean_rate`, `judge_llm_delta_avg`, `judge_response_rate`, con umbrales por tier; sale con código 1 si no se cumplen. **Nunca se corrió un baseline.** Requiere `OPENROUTER_API_KEY`, `SUPABASE_*` y (tier `full`) Groq/OpenAI.

---

## 7. Modelo de datos

Postgres en Supabase. **El esquema base no está versionado** (las tablas `users`, `jobs`, `content_results`, `transactions` se crearon a mano); solo existen las migraciones incrementales `supabase_migration_*.sql` en la raíz, que se ejecutaron manualmente en el SQL Editor. Se decidió adoptar Supabase CLI (ADR 0006). Columnas reconstruidas desde el código:

| Tabla | Columnas relevantes | Notas |
|---|---|---|
| `users` | `id` (= `auth.users.id`), `email`, `name`, `avatar_url`, `credits` (default 5 vía trigger), `subscription_status` (`free|basic|pro|active|canceled|cancelled|expired|unpaid|paused`), `plan` (`free|starter`), `billing_subscription_id`, `display_name`, `professional_title` | Trigger `on_auth_user_created` → `handle_new_user()`. RLS: el usuario ve y edita solo su fila |
| `jobs` | `id` (uuid), `user_id`, `video_url`, `video_title`, `status` (`pending|processing|completed|failed`), `error_message`, `current_step`, `progress_percentage`, `tone`, `usage_summary` (jsonb), `created_at`, `updated_at` | **Es la cola.** Índices por status y current_step. RLS: select/insert/update propios |
| `content_results` | `id`, `job_id`, `type` (`twitter_thread|linkedin_post|tiktok_caption|short_video_script`…), `content`, `clip_url`, `start_time`, `end_time`, `hook`, `emotional_trigger`, `moment_index`, `pillar_type`, `score_hook/retention/shareability`, `sentiment_detected`, `roi_time_saved`, `score_justifications`, `viral_overlay`, `raw_clip_url`, `whisper_words` (jsonb), `score_llm`, `score_judge`, `verification_failed`, `sub_coverage`, `words_per_sec`, `clip_quality_issues`, `clip_generation_error`, `created_at` | **3 filas por momento** (una por tipo de pieza) con los metadatos del momento repetidos; el frontend agrupa por `moment_index`. Mezcla los conceptos Momento y Pieza de copy: deuda (§14). RLS: select si el job es del usuario |
| `transactions` | `user_id`, `type` (`usage`), `credits` (−1), `description` | Solo la escribe el RPC `deduct_user_credit`. RLS: select propios |
| `transcription_cache` | `video_id` (PK), `transcript` (jsonb), `language`, `duration_seconds` | Se escribe, no se lee |
| `analysis_cache` | `video_id`, `model`, `tone`, `prompt_version`, `result` (jsonb), `category_detected`, `prompt_chars`; unique sobre los 4 primeros | |
| `category_cache` | `video_id`, `model`, `category`; PK compuesta | |
| `clip_edits` | `id`, `content_result_id` (FK cascade), `user_id`, `overlay_text`, `overlay_position`, `subtitle_style`, `overlay_style`, `word_corrections` (jsonb), `word_styles` (jsonb), `trim_start_offset`, `trim_end_offset`, `music_track_id`, `status` (`draft|queued|processing|completed|failed`), `rendered_clip_url`, `error_message` | Cola secundaria. Sin políticas RLS para usuarios (solo service role) |
| `job_usage_events` | `job_id` (FK cascade), `user_id`, `event_type`, `provider`, `task`, `model`, `moment_index`, `input_tokens`, `output_tokens`, `reasoning_tokens`, `audio_seconds`, `estimated_cost_usd`, `cache_hit`, `latency_ms`, `metadata` | RLS activo sin políticas → solo service role. RPC `get_job_usage_summary` |

RPCs: `deduct_user_credit(p_user_id, p_job_id, p_description)`, `check_duplicate_job(p_user_id, p_video_url, p_days_back)`, `get_job_usage_summary(p_job_id)`. Accesos: el frontend usa la **anon key + RLS** (lee `jobs`, `users`, `content_results`); backend y worker usan la **service role key** (bypass RLS). Backend y worker requieren Supabase; el antiguo fallback a SQLite se eliminó en la limpieza del 16-sep-2026.

---

## 8. API del backend

Express en `backend/src/app.js` (helmet, compression, CORS restringido a `localhost:3000/3001`, `FRONTEND_URL` y `*.vercel.app`, `express.json` con `rawBody` para el webhook), logging Winston (JSON en producción + archivos `logs/`), Sentry (`tracesSampleRate 0.2`). Auth: `requireAuth` verifica el JWT con `supabase.auth.getUser(token)` y **sobrescribe `req.body.userId`** con el id verificado; `optionalAuth` lo intenta sin bloquear. Admin: allowlist `ADMIN_USER_IDS` / `ADMIN_EMAILS`.

| Método y ruta | Auth | Qué hace |
|---|---|---|
| `GET /` | — | Nombre y versión |
| `GET /health` | — | Readiness: comprueba Supabase (`jobs`) y cuenta `pending`; 503 si falla |
| `GET /health/live` | — | Liveness (Docker/Render) |
| `GET /debug-sentry` | — | Lanza un error de prueba (**quitar**) |
| `POST /process` `{videoUrl, tone?}` | JWT + rate limit 5/15 min | Valida regex de YouTube (`watch?v=`, `youtu.be/`, `shorts/`), tono, duplicado (7 días, `409`), créditos (`402`), inserta job `pending` (`201 {jobId}`) |
| `GET /status/:jobId` | opcional | Job + `content_results` ordenados por `moment_index`. Dueño obligatorio si el job tiene `user_id`; jobs sin dueño son públicos |
| `GET /jobs` | JWT | Últimos 50 jobs del usuario |
| `GET /user/me/credits` · `GET /user/:userId/credits` | JWT | `{credits, subscription}` del usuario del token (ignora el param) |
| `POST /jobs/:jobId/retry` | JWT dueño | `failed|completed` → `pending` (mismo id, sin cobrar) |
| `DELETE /jobs/:jobId` | JWT dueño | Borra `content_results` + job (no si está `pending|processing`); los MP4 quedan en R2 |
| `POST /billing/create-checkout` | JWT | Crea checkout en Lemon Squeezy con `custom.user_id`; redirige a `/dashboard?upgrade=success` |
| `POST /billing/create-portal` | JWT | URL del portal de cliente de la suscripción |
| `POST /billing/webhook` | firma HMAC `x-signature` | `subscription_created` → starter + 40 créditos; `subscription_payment_success` (renovación) → 40; `subscription_updated` → active/free; `subscription_cancelled|expired` → free. Devuelve 200 aunque falle (loguea) |
| `GET /api/clips/:contentResultId/edit` | JWT dueño | Último borrador de edición |
| `POST /api/clips/:contentResultId/edit` | JWT dueño | Inserta borrador (`draft`) |
| `POST /api/clips/:contentResultId/regenerate` | JWT dueño | `draft|failed` → `queued` (202) |
| `GET /admin/usage/me` | JWT | `{isAdmin}` |
| `GET /admin/usage/summary?from&to` | admin | KPIs del período: jobs, costo, tokens, minutos Whisper, ingreso y margen |
| `GET /admin/usage/jobs?from&to&limit&offset` | admin | Tabla de jobs con costo y margen |
| `GET /admin/usage/jobs/:jobId` | admin | Eventos, agrupación por pipeline, comparación vs benchmark rolling (últimos 20 jobs) y estimaciones legacy |
| `GET /admin/usage/breakdown?from&to` | admin | Costo por tarea / modelo / proveedor |
| `GET /admin/usage/benchmarks` | admin | Promedios de los últimos N jobs |

Este cuadro es la referencia del API (no hay OpenAPI; el antiguo `API_DOCUMENTATION.md` se eliminó por desactualizado).

---

## 9. Frontend

Next.js 16 (App Router, React 19, Tailwind 4, componentes shadcn en `components/ui`, framer-motion, lucide, recharts). `middleware.ts` refresca la sesión de Supabase y protege todo salvo `/`, `/login`, `/auth/*`, `/pricing`. `lib/api.ts` adjunta el JWT a cada llamada al backend (`NEXT_PUBLIC_API_URL`).

| Ruta | Qué hace |
|---|---|
| `/` | Landing (redirige a `/dashboard` si hay sesión) |
| `/login` | Google OAuth y magic link → `/auth/callback` (intercambio de código) → `/dashboard` |
| `/dashboard` | Lee **directo de Supabase** jobs, créditos y un resumen de `content_results` (poll cada 10 s); formulario inline de URL + selector de tono; secciones completados/en proceso/con error; stats; búsqueda; reintentar y borrar (vía API); notificaciones del navegador al terminar un job; CTA de upgrade cuando no hay créditos |
| `/results/[jobId]` | `GET /status` con poll cada 3 s mientras `processing`; `ProcessingScreen` (pasos y progreso); `AnalyticsSummary`; una `ViralMomentCard` por momento: reproductor 9:16, descarga MP4, compartir (Web Share / portapapeles), badge "⚠ Verificar corte", scores del juez con razonamiento, pestañas de copy (`CopyTabs`: copiar por tweet, post, caption), botón Editar → `EditClipDrawer` |
| `EditClipDrawer` | Pestañas texto (overlay + posición), subtítulos (`WordSubtitleEditor`: corregir y resaltar palabras), estilo, recorte (sliders sobre `clipDuration`), música (placeholder). Guarda borrador y encola; poll del estado; reemplaza el clip al completar |
| `/pricing` | Free vs Starter; textos desactualizados (3 créditos, 5 horas, "Stripe") |
| `/account`, `/settings` | Créditos, plan, upgrade/portal, cerrar sesión. **No permiten editar nombre ni título profesional** |
| `/admin/usage`, `/admin/usage/jobs/[jobId]` | Panel de costos (KPIs, gráficos, tabla, detalle por job con pipeline colapsable) solo para admins |

---

## 10. Infraestructura, deploy y operación

### 10.1 Proveedores

| Proveedor | Rol | Plan / costo | Estado (16-sep-2026) |
|---|---|---|---|
| Vercel | Frontend `https://viral-engine-front.vercel.app` | Free | Publicado |
| Render | Backend `https://viral-engine-backend.onrender.com` | **Free** (cold start ~25 s) → pasa a pago | Vivo; `/health` 503 por Supabase |
| Supabase | Auth + Postgres + cola | Free | **Pausado** → reactivar; crear un 2.º proyecto para dev |
| OVH VPS `51.79.50.95` (BHS, Canadá) | Worker Docker (`docker-compose.worker.yml`), Caddy | 6 vCPU / 12 GB / 100 GB, Ubuntu 26.04 | Encendido (SSH ok); worker probablemente detenido |
| Cloudflare R2 | Clips públicos (`R2_PUBLIC_URL`) | Pago por uso | Activo |
| OpenRouter | Gemini / GPT | Pago por uso | Activo |
| Groq | Whisper | Pago por uso | Activo |
| OpenAI | Whisper fallback | Pago por uso | Activo |
| Supadata | Transcripts | Plan | Activo |
| RapidAPI (yt-api) | Stream URLs | Plan | Activo |
| Webshare | Proxies residenciales (`proxies.txt`, formato con `deploy/format-proxies.sh`) | ~US$6/mes | Activo |
| Lemon Squeezy | Suscripciones | Comisión | Activo, sin cobro en beta |
| Sentry | Errores backend y worker | Free | DSN hardcodeados |
| GitHub | Repo público `bolioliagustin/viral-engine-front`, Actions | Free | CI y deploy configurados |
| Apify | Descarga de clips (opcional) | Pago por uso | Apagado (`USE_APIFY_FALLBACK`) |

### 10.2 Flujos de deploy

- **Frontend:** Vercel desde `main` (integración Git; variables `NEXT_PUBLIC_*` en Vercel).
- **Backend:** Render desde `main` (`render.yaml`, solo el backend, plan `starter`, health check en `/health/live`). Variables en el dashboard de Render.
- **Worker:** `.github/workflows/deploy.yml` → en cada push a `main` (o manual) hace SSH al VPS y corre `deploy/deploy-worker.sh` (`git reset --hard origin/main`, `docker compose -f docker-compose.worker.yml build --no-cache && up -d`). **No espera a que pase el CI** (`ci.yml` corre tests de backend y worker y un build de Docker, pero es independiente). Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`.
- Archivos que viven solo en el VPS: `~/viralengine/.env`, `~/viralengine/proxies.txt`, opcional `cookies.txt`. Logs persistentes en `~/viralengine/worker-logs/`.
- Otros scripts: `deploy/setup-vps.sh` (provisión de un VPS nuevo: Docker, UFW 22/80/443, Caddy), `deploy/worker-only.sh` (levanta solo el worker), `deploy/format-proxies.sh` (proxies Webshare), `docker-compose.yml` + `deploy/Caddyfile.example` (backend + worker en el VPS, para cuando el API deje Render; requiere dominio). Los scripts de Windows y del auto-deploy por webhook de la era Hetzner se eliminaron en la limpieza del 16-sep-2026.

### 10.3 Operación

```bash
ssh ubuntu@51.79.50.95
cd ~/viralengine
docker compose -f docker-compose.worker.yml ps          # estado
bash scripts/worker-logs.sh tail 200                     # logs (o: job <id>, edit <id>, errors)
docker compose -f docker-compose.worker.yml restart      # reinicio sin rebuild
bash deploy/deploy-worker.sh                             # deploy manual
```

Salud: `GET /health` (readiness con Supabase) y `GET /health/live` en el backend; el worker solo expone `/health` si `PORT` está seteado (Render). Errores en Sentry (backend y worker). Costos en `/admin/usage`.

---

## 11. Configuración

Plantilla completa en [`.env.example`](../.env.example). Un solo `.env` en la raíz alimenta backend (`dotenv` desde `../../.env`) y worker (`load_dotenv(parent/.env)`); el frontend usa `frontend/.env.local`.

| Grupo | Variables | Obligatoria |
|---|---|---|
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (backend + worker); `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (frontend) | Sí |
| Backend | `PORT=3000`, `NODE_ENV`, `FRONTEND_URL` (CORS y redirecciones), `LOG_LEVEL`, `SENTRY_DSN_BACKEND`, `ADMIN_USER_IDS`/`ADMIN_EMAILS`, `LEMONSQUEEZY_API_KEY/STORE_ID/VARIANT_ID/WEBHOOK_SECRET` | Sí (LS solo para cobrar) |
| Frontend | `NEXT_PUBLIC_API_URL` | Sí |
| Worker: IA | `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `MODEL_*`, `MODEL_*_REASONING`, `LOG_LLM_USAGE`, `TWO_PASS_ANALYSIS`, `COMPACT_TRANSCRIPT`, `ENABLE_ENTERTAINMENT_CATEGORY` | Las dos primeras sí |
| Worker: YouTube | `SUPADATA_API_KEY`, `RAPIDAPI_KEY`, `USE_RAPIDAPI_DOWNLOAD`, `WEBSHARE_PROXY_FILE|LIST|URL`, `YOUTUBE_COOKIES`, `DOWNLOAD_*`, `CLIP_*`, `STRICT_SYNC_VALIDATION`, `YTDLP_CLIP_FALLBACK`, `USE_APIFY_FALLBACK`, `APIFY_TOKEN` | En producción: Supadata, RapidAPI y proxies |
| Worker: salida | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL` | Sí para clips |
| Worker: runtime | `ENVIRONMENT` (`production` cambia defaults de descarga), `MAX_WORKERS`, `FFMPEG_PATH`/`FFPROBE_PATH` (opcional), `LOG_LEVEL`, `LOG_FORMAT`, `WORKER_LOG_DIR`, `SENTRY_DSN_WORKER`, `PERSIST_USAGE_EVENTS`, `PRICING_OVERRIDES_JSON`, `PORT` (solo Render) | No |

---

## 12. Calidad y observabilidad

- **Tests backend** (`backend/__tests__`, Jest + Supertest, Supabase mockeado): auth, validación de URL, health, integración del ciclo `/process`. 27 tests, pasan en local con Node 20 y 26; no necesitan variables de entorno.
- **Tests worker** (`worker/tests`, pytest): validación de env, limpieza, timeouts, selector de estrategia, DRM, sticky proxy, sincronía de subtítulos, correcciones de palabras, snap/cobertura, precios, usage tracker, y tests de FFmpeg (9:16, subtítulos, overlay, generate_clip, upload R2 —mockeado o con fixtures—). 104 tests, pasan en local con Python 3.12.
- **Frontend:** sin tests; `tsc --noEmit` limpio.
- **La suite del worker necesita variables dummy** (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, y `ENVIRONMENT=development` si el `.env` de la raíz es el de producción): sin ellas fallan 18/104. `ci.yml` no las define, por lo que **el CI está en rojo en todos los runs** (verificado en la API de GitHub: los 155 runs de "CI/CD" terminan en `failure` en "Run tests", mientras "Deploy Worker to VPS" termina en `success`). Tras la limpieza el job de backend ya no depende de variables; el del worker sigue necesitando un bloque `env:` en `ci.yml`.
- **Golden set:** §6; sin baseline.
- **Métricas persistidas por clip:** `score_llm`, `score_judge`, `verification_failed`, `sub_coverage`, `words_per_sec`, `clip_quality_issues`, `clip_generation_error`; por job: `usage_summary` y eventos `download` (estrategia, MB, clips fallidos). Con eso se puede medir la tasa de éxito real de cada estrategia de descarga sin tocar código.
- **Logs:** worker en archivo con contexto por job/momento/fase; backend Winston; Sentry con breadcrumbs de calidad de contenido (`content_quality.*`).

---

## 13. Estado actual y hallazgos

### 13.1 Verificado el 16-sep-2026

| Elemento | Estado |
|---|---|
| `https://viral-engine-front.vercel.app` | 200, título correcto |
| `https://viral-engine-backend.onrender.com/health/live` | 200 tras ~25 s de cold start |
| `…/health` | 503: `database` y `queue` → `TypeError: fetch failed` (Supabase inaccesible) |
| VPS `51.79.50.95` | puerto 22 abierto; puerto 80 → 502 (Caddy sin backend, modo worker-only) |
| Supabase | Pausado por inactividad (free tier). Implica que el worker del VPS no estaba corriendo (su keepalive lo habría mantenido activo) |
| Repo GitHub | Público |
| Mac local | Node 26 (Node 20 instalado aparte para el backend), Python 3.12 vía `uv` en `worker/.venv`, deps instaladas, Supabase CLI 2.117, ffmpeg 8.1; sin Docker |

### 13.2 Hallazgos (ordenados por impacto en la beta)

1. **Sin Supabase no hay producto**: reactivar es el paso 0. Si el proyecto fue borrado en vez de pausado, hay que reconstruir el esquema base (no versionado) desde el código de §7.
2. **Fuga de créditos** (validación al encolar, descuento al completar) y **jobs sin MP4 que cobran crédito**. Resuelto por diseño en ADR 0005 y la definición de job exitoso.
3. **Sin tope de duración** en el pipeline activo; el último trabajo fue sobre videos de 1 h y 12 h.
4. **Descarga de YouTube** como único camino a los clips (ADR 0007 lo cambia).
5. **Render free**: cold start de 25 s en el primer request y en el webhook de pagos.
6. **Deploy sin gate**: push a `main` despliega al VPS sin esperar al CI; Vercel también.
7. **CI en rojo desde siempre**: los tests fallan en GitHub Actions por falta de variables dummy en `ci.yml`; nunca hubo un gate real.
8. **Pricing desactualizado**: "3 créditos" (son 5), "videos hasta 5 horas", "pagos por Stripe", email de contacto en dominio ajeno.
9. **Personalización sin UI**: `display_name` y `professional_title` no se pueden cargar.
10. **`transcription_cache` no se lee**; cada reintento vuelve a llamar a Supadata.
11. **`/debug-sentry`** expuesto y **DSN de Sentry hardcodeados** como fallback en `app.js` y `main.py`.
12. **Modelos LLM sin validar** tras la migración de julio (baseline del golden set pendiente); riesgo de ids retirados.
13. **`content_results` triplica** cada momento (una fila por pieza de copy) y el frontend agrupa; funciona, pero complica cualquier cambio.
14. ✔ *Resuelto en la limpieza del 16-sep-2026:* `render.yaml` declaraba un worker en Render inexistente; `deploy/webhook_server.py`, `deploy.sh`, `migrate-ovh.sh`, `push-deploy.ps1`, `SETUP-DEPLOY.md` y `worker-logs.ps1` eran de etapas anteriores.
15. ✔ *Resuelto en la limpieza del 16-sep-2026:* se eliminaron el fallback SQLite (backend y worker), `clipper.py`, `logging_config.py`, `test_cobalt.py`, `migrate_add_progress_fields.py`, `temp_page_backup.tsx`, las funciones sin referencias del worker (`download_audio`, `download_video*`, `_download_with_progress`, `download_clip_segment`, `regenerate_moment_copy*`, `analyze_with_gemini`, etc.), `better-sqlite3`, `watchdog`, `@radix-ui/react-progress` y los SVG de plantilla del frontend. Quedan a propósito: la categoría entertainment (feature flag) y el campo `short_video_script` (compatibilidad con filas viejas).
16. **Pantalla de progreso**: los pasos `transcribing` y `clipping` nunca se activan.
17. **`/status/:jobId`** permite leer jobs sin dueño sin autenticación (no hay jobs así en la práctica).
18. **Clips huérfanos en R2** al borrar jobs (sin lifecycle rule).
19. ✔ *Resuelto:* `better-sqlite3` (no compilaba en Node 26) se eliminó con el fallback SQLite; el backend corre con Node ≥ 20.
20. ✔ *Resuelto:* `API_DOCUMENTATION.md` y `MEJORAS_PROYECTO.md` (desactualizados) se eliminaron; el README se reescribió; `WORKER.md` se mantiene.
21. **Landing** con afirmaciones no respaldadas ("miles de creadores", "Ver demo" sin acción, "cualquier duración").
22. **`/account` y `/settings`** son dos páginas casi iguales (créditos, plan, upgrade/portal), ambas enlazadas desde el menú.
23. **ESLint** del frontend arrastra 15 errores heredados (`any`, `setState` dentro de efectos, componentes creados en render); nunca corrió en CI.

---

## 14. Deuda técnica y backlog

**Congelado durante la beta (no se hace):** música de fondo, links públicos para compartir, categoría entertainment, inglés/otros idiomas, clips 1080p, nuevas redes.

| Prioridad | Ítem | Notas |
|---|---|---|
| Fase 1 | Créditos reservados (ADR 0005); job exitoso = ≥1 clip; tope 90 min; sacar `/debug-sentry` y DSN hardcodeados; leer `transcription_cache`; alerta por Telegram de jobs fallidos; corregir pasos de la pantalla de progreso | Ver §15 |
| Fase 2 | Subida directa (ADR 0007); endpoint admin para cargar créditos; textos del pricing y email de contacto; UI de perfil de creador (si sobra tiempo) | |
| Backlog | Lifecycle rule en R2; normalizar `content_results` (1 fila por momento + tabla de piezas); unificar `/account` y `/settings`; corregir los 15 errores de ESLint y sumar `lint` al CI; OpenAPI para el API; tests de frontend; rate limit con Redis si hay varias instancias del API; `/status` sin auth para jobs sin dueño; mover el API al VPS cuando haya dominio; textos de la landing | |

---

## 15. Plan de la nueva etapa

### 15.1 Decisiones tomadas el 16-sep-2026

| Tema | Decisión | Registro |
|---|---|---|
| Objetivo | Beta cerrada estable para el dueño + 5–10 invitados; sin cobro real hasta cumplir la métrica | este doc |
| ICP | Podcasters y coaches en español; el clip 9:16 es el valor primario | este doc |
| Fuente de video | Subida directa como camino garantizado; YouTube como atajo; transcript híbrido (link opcional → Supadata; sin link → Whisper Groq por trozos); archivo borrado a los 7 días | ADR 0007 |
| Tope de duración | 90 min (ambas fuentes) | config |
| Créditos | Reservar al encolar, devolver si falla; reintento vuelve a reservar | ADR 0005 |
| Job exitoso | ≥1 clip renderizado; 0 clips → `failed` + devolución; momentos sin clip muestran link degradado | `CONTEXT.md` |
| Backend | Render plan pago (sin cold starts); mover al VPS cuando haya dominio | este doc |
| Datos | Proyecto Supabase separado para dev; esquema versionado con Supabase CLI | ADR 0006 |
| Git/deploy | Ramas + PR + CI obligatorio; deploy al VPS manual (`workflow_dispatch`) o por tag; `main` protegido | `AGENTS.md` |
| Resolución | 720×1280 en beta; medir tiempos antes de subir a 1080 | config |
| Agentes | Claude Code, Antigravity y OpenCode orquestados por "orca"; reglas únicas en `AGENTS.md` (importado por `CLAUDE.md`); un componente por agente | `AGENTS.md` |
| Beta | Registro abierto sin difusión; créditos cargados a mano por endpoint admin; gratis; feedback por grupo de chat; 2 usuarios "canarios" al final de la Fase 1 | este doc |
| Alertas | Telegram (bot + chat id) | Fase 1 |
| Golden set | `smoke` + `analysis` como gate antes de la beta; `full` si cambian modelos de copy/juez | Fase 0/1 |
| Marca | Pendiente; reemplazar el email `@viralengine.app` | Fase 2 |

### 15.2 Fases (~10 h/semana + agentes en paralelo)

| Fase | Semanas | Entregable | Criterio de salida |
|---|---|---|---|
| **0 Reactivar** | 1 | Supabase activo y `.env` validado; SSH al VPS y diagnóstico del worker; `supabase db pull` → esquema base versionado; proyecto Supabase de dev creado desde las migraciones; todo corriendo en la Mac con un video corto; `ci.yml` con variables dummy para que el CI pase; tests y golden set `smoke` en verde; ids de modelos verificados en OpenRouter | Un job end-to-end en local con los 5 clips |
| **1 Estabilizar** | 2–3 | ADR 0005 implementado; job exitoso ≥1 clip; tope 90 min; medición por estrategia de descarga en `/admin/usage`; fixes de §14 Fase 1; Render pago; `render.yaml` limpio; ramas + PR + deploy manual; `main` protegido; 2 canarios con videos cortos | 10 jobs seguidos exitosos en el VPS con videos de 20–90 min |
| **2 Subida directa** | 4–6 | Upload multipart a R2 desde el navegador; worker con fuente "archivo" y transcript híbrido; endpoint admin de créditos; textos de pricing y email; UI de perfil si sobra tiempo | Un invitado sube un episodio de 60 min y recibe sus clips |
| **3 Beta cerrada** | 7–10 | 5–10 invitados usando el producto; grupo de feedback; seguimiento semanal de la métrica | **≥90 % de jobs con todos sus clips en <15 min (videos ≤90 min) durante 2 semanas; 0 jobs zombie** → decidir apertura del cobro |

### 15.3 Calidad de los clips (agregado el 17-sep-2026)

Con el pipeline estable (3/3 corridas reales, 15/15 clips), el foco pasa a la calidad: el juez promedia **4.7/10** sobre 20 clips y ninguno llega a 7 en las tres métricas. El diagnóstico con evidencia (cortes a mitad de oración por resolución de captions, refinamiento que solo recorta, ranking ciego, juez que llega tarde, sin guardas de plausibilidad) y el plan en 8 líneas de trabajo paralelizables con una rueda de mejora continua están en [`PLAN_CALIDAD.md`](PLAN_CALIDAD.md). Las fases 1–3 de arriba siguen vigentes; la calidad corre en paralelo desde la Fase 1.

### 15.4 Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| YouTube cambia y rompe yt-dlp/RapidAPI | Subida directa (ADR 0007); mantener `yt-dlp` actualizado en cada build (el Dockerfile instala la última) |
| Proxies lentos o sin crédito (`402`) | Alertas; medir por estrategia; Apify como fallback pago si hace falta |
| Retiro de modelos LLM | `validate_env` avisa; golden set `smoke` antes de cada deploy; pinnear ids |
| Supabase free se pausa | Con el worker corriendo no se pausa; alerta si `/health` da 503 |
| Un solo desarrollador | Agentes en paralelo con reglas claras; documentación viva (este doc + ADRs + `CONTEXT.md`) |
| Marca/dominio | Decisión explícita en Fase 2; hasta entonces no se difunde |

---

## 16. Trabajo con agentes IA

Las reglas operativas están en [`AGENTS.md`](../AGENTS.md) (importado por `CLAUDE.md`). Resumen: un agente por componente (`worker/` Python, `backend/` Node, `frontend/` Next, `docs/`+infra), cada uno en su rama, PR con CI verde, sin tocar `.env` ni `main`, tests antes de abrir el PR, vocabulario de `CONTEXT.md`, decisiones nuevas como ADR. Contratos entre componentes: el esquema (migraciones Supabase CLI), los endpoints de §8 y las variables de §11; cualquier cambio en un contrato se documenta en el PR y en este archivo.

---

## Apéndice A — Comandos

### Mac (desarrollo local)

```bash
# Requisitos ya instalados: Homebrew, Node 26 (global) y node@20 (keg-only), uv + Python 3.12, ffmpeg, Supabase CLI
export PATH="/opt/homebrew/opt/node@20/bin:$PATH"   # backend y frontend con Node 20 (paridad con Docker/CI)

# Backend
cd backend && npm ci && npm run dev                  # http://localhost:3000
npm test                                             # Jest (27 tests)

# Worker (Python 3.12 en worker/.venv, creado con uv)
cd worker && source .venv/bin/activate && python main.py
python -m pytest tests/ -q                           # 104 tests
python eval/run_golden_set.py --tier smoke           # requiere OPENROUTER + SUPABASE

# Frontend
cd frontend && npm ci && npx next dev -p 3001        # http://localhost:3001 (frontend/.env.local con NEXT_PUBLIC_*)
npx tsc --noEmit && npm run lint

# Supabase CLI (Fase 0)
supabase login
supabase link --project-ref <ref>
supabase db pull                                     # genera supabase/migrations/<ts>_remote_schema.sql
supabase db push                                     # aplica migraciones al proyecto linkeado (dev)
```

### VPS

Ver §10.3. Deploy manual: `bash deploy/deploy-worker.sh`. Golden set en el VPS: `docker compose -f docker-compose.worker.yml exec worker python eval/run_golden_set.py --tier smoke`.

### Cache y utilidades

```bash
cd worker && python scripts/invalidate-analysis-cache.py <video_id>   # invalidar análisis de un video
bash deploy/format-proxies.sh proxies-raw.txt > proxies.txt      # convertir proxies Webshare
```

---

## Apéndice B — Mapa de archivos

```
.
├── AGENTS.md / CLAUDE.md          Reglas para agentes (CLAUDE.md importa AGENTS.md)
├── CONTEXT.md                     Glosario del dominio
├── README.md                      Setup local, base de datos y deploy
├── .env.example                   Plantilla de configuración
├── docker-compose.yml             Backend + worker (VPS con dominio, futuro)
├── docker-compose.worker.yml      Solo worker (modo actual del VPS)
├── render.yaml                    Blueprint de Render (solo backend)
├── supabase/
│   ├── legacy/                    SQL histórico aplicado a mano (con README)
│   └── migrations/                Migraciones Supabase CLI (a crear en Fase 0)
├── docs/
│   ├── PROYECTO.md                Este documento
│   ├── adr/                       Decisiones de arquitectura
│   ├── INFORME_LLMS.md            Fichas de cada modelo IA
│   └── RECOMENDACION_MODELOS_LLM.md  Migración de modelos jul 2026 y costos
├── deploy/                        deploy-worker.sh, worker-only.sh, setup-vps.sh, format-proxies.sh, Caddyfile.example
├── scripts/worker-logs.sh         Logs del worker en el VPS
├── .github/workflows/             ci.yml (tests + build), deploy.yml (VPS)
├── backend/
│   ├── src/app.js                 Express, CORS, health, Sentry
│   ├── src/index.js               Arranque
│   ├── src/routes/                jobs, billing, clip-edits, admin-usage
│   ├── src/middleware/            auth (JWT), admin (allowlist)
│   ├── src/lib/                   supabase, logger, cost-benchmarks
│   └── __tests__/                 Jest
├── worker/
│   ├── main.py                    Loop, claim, pipeline por job
│   ├── WORKER.md                  Doc del autor
│   ├── config/                    model_tiers, llm_chat, pricing, validate_env, logging
│   ├── context/job_context.py     contextvars job/usuario/momento/edición
│   ├── models/schemas.py          Pydantic: ViralMoment, AnalysisResult
│   ├── services/
│   │   ├── yt_transcript.py       Supadata / youtube-transcript-api + oEmbed
│   │   ├── processor.py           Clasificador, mega-prompt legacy, Pasada B
│   │   ├── moment_selector.py     Pasada A
│   │   ├── scorer.py              Juez + ROI
│   │   ├── downloader.py          yt-dlp, RapidAPI, proxies, partial download, Apify
│   │   ├── transcriber.py         Whisper Groq/OpenAI, transcript compacto
│   │   ├── clip_generator.py      FFmpeg: corte, 9:16, SRT/ASS, overlay, generate_clip
│   │   ├── validation.py          Duraciones, solapamiento, verificación de frases, anclas
│   │   ├── content_validators.py  Limpieza y validación del copy
│   │   ├── clip_edit_processor.py Cola de ediciones
│   │   ├── supabase_client.py     DB, créditos, R2, claim de ediciones
│   │   ├── storage_client.py      R2 (boto3)
│   │   ├── usage_tracker.py       job_usage_events + usage_summary
│   │   ├── analysis_cache.py / transcript_cache.py
│   │   └── podcast_prompt.py / category_prompts.py
│   ├── scripts/invalidate-analysis-cache.py
│   ├── eval/                      Golden set
│   └── tests/                     pytest
└── frontend/src/
    ├── middleware.ts              Sesión y rutas protegidas
    ├── app/                       Rutas (§9)
    ├── components/                ViralMomentCard, CopyTabs, EditClipDrawer, WordSubtitleEditor, ProcessingScreen, AnalyticsSummary, admin/*, ui/*
    ├── lib/api.ts                 Cliente HTTP con JWT
    ├── lib/supabase/              Clientes browser/server
    └── types/subtitles.ts         Tipos de palabras/correcciones
```

---

## Apéndice C — Cronología (según git)

| Fecha | Hito |
|---|---|
| 2026-02-23 | v1.0 "production ready": backend + worker + frontend; Render free; Sentry, R2 |
| 2026-03-29 | Transcript vía `youtube-transcript-api` (evita descargar audio) |
| 2026-04-17 | Supadata para transcripts (bloqueo de IPs de datacenter); rediseño del dashboard; hilos de Twitter "reales" |
| 2026-04-20 | Sprint 1.1: clips MP4 verticales con subtítulos y overlay; RapidAPI; proxies residenciales |
| 2026-04-22 | Facturación con Lemon Squeezy; FFmpeg en una pasada (4× más rápido); 720×1280 por RAM de Render |
| 2026-04-24 | Descarga selectiva por rangos; Whisper por clip; subtítulos por palabra; webhook de deploy en Hetzner; gate de créditos |
| 2026-04-27 | Groq Whisper; cola `clip_edits` y editor; cache de raw clip + words ("Plan C") |
| 2026-04-29 | `analysis_cache` + transcript compacto; dashboard tier 2 (compartir, búsqueda, stats, notificaciones); mobile |
| 2026-05-08 | 6 fixes de seguridad/estabilidad; reconexión Supabase thread-safe; keepalive |
| 2026-05-11 | Fase 1 "producto profesional" (podcast + business); golden set; guardas de timestamps |
| 2026-05-13 | Pipeline de descarga robusto para Hetzner; rotación de proxies; yt-dlp full como primario |
| 2026-07-06 | Migración a OVH; modo worker-only; deploy por GitHub Actions; editor de subtítulos por palabra; pipeline de calidad de contenido; fixes de sincronía y DRM |
| 2026-07-07 | Fixes de Whisper y 403 |
| 2026-07-08 | Actualización del analizador; migración de modelos LLM (Gemini 3.5 Flash, GPT-5.4 nano); plan de medición; panel de costos `/admin/usage`; fixes para videos de 1 h y 12 h. **Último commit** |
| 2026-09-16 | Análisis completo, decisiones de la nueva etapa, este documento |
