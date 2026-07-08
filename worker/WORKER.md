# Worker — Funcionamiento y arquitectura ideal

Documento de referencia del **Python Worker** del YouTube Viral Content Engine.  
Describe cómo funciona hoy en producción (OVH VPS) y cuál es el comportamiento ideal esperado.

---

## 1. Rol del worker en el sistema

El worker **no expone API al usuario**. Es un proceso en background que:

1. Lee jobs de la cola en **Supabase** (`jobs.status = pending`)
2. Procesa videos de YouTube (transcripción → IA → clips → copy)
3. Sube clips a **Cloudflare R2**
4. Guarda resultados en **Supabase** (`content_results`)
5. Descuenta **1 crédito** al usuario si el job termina con éxito

### Arquitectura de producción actual

```
Vercel (frontend)
    ↓ JWT
Render (backend API)  →  crea job en Supabase (status: pending)
                              ↑
OVH VPS (worker)      →  poll cada 3s, claim atómico, procesa
                              ↓
                         Cloudflare R2 (clips MP4)
```

| Componente | Responsabilidad |
|------------|-----------------|
| **Frontend** | UI, auth, submit URL |
| **Backend (Render)** | API REST, billing, validación JWT |
| **Worker (OVH)** | Procesamiento pesado de video/IA |
| **Supabase** | Cola, usuarios, créditos, resultados |
| **R2** | Hosting público de clips |

La cola **no usa Redis ni RabbitMQ**: la tabla `jobs` en PostgreSQL es la cola.

---

## 2. Cómo arranca el worker

**Entry point:** [`main.py`](main.py)

Al iniciar (`python main.py` o Docker):

1. Carga `.env` desde la raíz del repo
2. Valida variables obligatorias ([`config/validate_env.py`](config/validate_env.py))
3. Inicializa **Sentry** (errores)
4. Conecta a **Supabase** (service role key)
5. Limpia archivos locales > 24h (`downloads/`, `clips/`)
6. Recupera jobs **zombie** (stuck en `processing` > 20 min → `failed`)
7. Inicia **keepalive** Supabase (ping cada 45s)
8. Entra en `watch_queue()` — loop infinito con polling

### Variables de entorno clave

| Variable | Obligatoria | Uso |
|----------|-------------|-----|
| `SUPABASE_URL` | Sí | Cola y persistencia |
| `SUPABASE_SERVICE_KEY` | Sí | Bypass RLS |
| `OPENROUTER_API_KEY` | Sí | Análisis viral (Gemini) |
| `OPENAI_API_KEY` | Sí | Whisper fallback |
| `GROQ_API_KEY` | Recomendada | Whisper preferido (más rápido) |
| `SUPADATA_API_KEY` | Sí en VPS/cloud | Transcripts (IPs datacenter) |
| `R2_*` | Sí para clips | Upload a Cloudflare R2 |
| `WEBSHARE_PROXY_FILE` | Sí en VPS | Proxies residenciales YouTube |
| `USE_RAPIDAPI_DOWNLOAD` | Recomendada | URLs de stream cuando yt-dlp falla |
| `RAPIDAPI_KEY` | Si USE_RAPIDAPI | API yt-api en RapidAPI |
| `MAX_WORKERS` | No (default 2–3) | Jobs paralelos |

Archivos solo en el VPS (no en Git): `.env`, `proxies.txt`

---

## 2.1 Logs y trazabilidad

El worker escribe logs **persistentes** en el host (no hace falta `docker compose logs` para cada consulta):

| Archivo | Contenido |
|---------|-----------|
| `worker-logs/worker.log` | Todo (rotación 20MB × 5) |
| `worker-logs/worker-error.log` | Solo WARNING+ |

Cada línea incluye contexto cuando aplica: `job=abc12345 m=1 phase=clip`.

### Comandos rápidos (VPS, desde la raíz del repo)

```bash
bash scripts/worker-logs.sh              # follow en vivo (rápido)
bash scripts/worker-logs.sh tail 200     # últimas 200 líneas
bash scripts/worker-logs.sh job abc123   # filtrar por job id (prefijo)
bash scripts/worker-logs.sh edit b05b33  # filtrar clip_edit
bash scripts/worker-logs.sh errors       # últimos errores/warnings
bash scripts/worker-logs.sh docker       # fallback docker compose logs
```

Windows (local):

```powershell
.\scripts\worker-logs.ps1 tail 100
.\scripts\worker-logs.ps1 job abc123
```

Variables opcionales: `LOG_LEVEL=DEBUG`, `LOG_FORMAT=json`, `WORKER_LOG_DIR=/app/logs`.

---

## 3. Loop principal (`watch_queue`)

```mermaid
flowchart TB
    subgraph poll [Cada 3 segundos]
        A[Poll jobs pending] --> B{Slots libres?}
        B -->|Sí| C[Claim atómico pending→processing]
        C --> D[ThreadPoolExecutor process_job]
        B -->|No| E[Esperar futures que terminen]
        F[Poll clip_edits queued] --> G[process_clip_edit]
    end
    D --> H[Job completed / failed]
    G --> I[Edit completed / failed]
    H --> poll
    I --> poll
```

### Concurrencia

- `MAX_WORKERS` threads en paralelo (default 3 en OVH)
- **Claim atómico**: `UPDATE jobs SET status='processing' WHERE id=? AND status='pending'`
  - Evita que dos workers procesen el mismo job
- Timeout por job: **30 minutos**
- Cola secundaria: `clip_edits` con `status='queued'` (re-render de clips editados)

---

## 4. Pipeline de un job (`process_job`)

Cada job recibe `id`, `videoUrl`, `user_id` desde Supabase.

### Resumen de pasos

| Step | Progreso | `current_step` | Qué hace |
|------|----------|----------------|----------|
| 1–2 | 10–40% | `downloading` | Transcript + metadata |
| 3 | 50–65% | `analyzing` | IA detecta momentos virales |
| 3.5 | — | — | Filtro de duración mínima (10s) |
| 4 | 70% | `clipping` | Descarga de video |
| 5 | 85% | `generating` | Genera clips + copy por momento |
| 6 | 100% | `completed` | Descuenta crédito, marca job OK |

### Step 1–2: Transcripción

**Módulo:** [`services/yt_transcript.py`](services/yt_transcript.py)

```
YouTube URL
    ↓
1. Supadata API (producción / datacenter)
2. Fallback: youtube-transcript-api (local)
    ↓
Transcript con segmentos + timestamps
    ↓
Cache en transcription_cache (por video_id)
Metadata vía oEmbed (título, autor)
```

**Ideal:** Supadata responde en < 5s con 100% de videos públicos.  
**Sin descarga de video** en este paso — solo texto.

### Step 3: Análisis IA

**Módulo:** [`services/processor.py`](services/processor.py)

```
Transcript + metadata
    ↓
Clasificación: podcast | business (Gemini Flash o keywords)
    ↓
Prompt especializado por categoría
    ↓
OpenRouter → Gemini (OPENROUTER_MODEL)
    ↓
~5 ViralMoment con:
  - start_time / end_time
  - scores (hook, retention, shareability)
  - twitter_thread, linkedin_post, tiktok_caption
  - viral_overlay (texto para el clip)
    ↓
Validadores de contenido + cache en analysis_cache
```

**Ideal:** 5 momentos de 15–60s, scores ≥ 7, copy listo para publicar.

### Step 4: Descarga de video (cascada)

**Módulo:** [`services/downloader.py`](services/downloader.py)

YouTube bloquea IPs de datacenter. Por eso en VPS se usan **proxies residenciales Webshare**.

```mermaid
flowchart TD
    A[Step 4: Descarga] --> B{USE_RAPIDAPI o prod?}
    B -->|Sí| C[RapidAPI stream URLs]
    C --> D[Sticky proxy + partial download]
    D --> E[FFmpeg merge audio+video]
    E --> Z[MP4 muxed único]
    B -->|No| F{Plan A: yt-dlp full}
    F -->|OK| Z
    F -->|Fallo DRM/403| C
    C -->|Fallo| G{Plan C: yt-dlp per-clip}
    G -->|OK| H[Segmento por momento]
    G -->|Fallo| I[Fallback: deep-link YouTube]
    Z --> J[Generar clips]
    H --> J
    I --> K[Solo copy, sin MP4]
```

| Plan | Método | Cuándo funciona | Cuándo falla |
|------|--------|-----------------|--------------|
| **A (prod)** | RapidAPI stream URLs + sticky proxy + partial download | Camino primario en VPS con `USE_RAPIDAPI_DOWNLOAD=true` | HEAD/download error, proxy 402 |
| **B** | yt-dlp full + proxy | Dev sin RapidAPI forzado; videos sin DRM | DRM, PO Token (común con client `tv`) |
| **C** | yt-dlp `download_ranges` per-clip | Clips cortos, proxy OK, sin RapidAPI | Mismo que B |
| **D** | Link `youtube.com/watch?v=X&t=Ns` | Siempre | No hay archivo MP4 |

**Nota:** `YOUTUBE_COOKIES` ayuda con anti-bot en yt-dlp, pero **no** desbloquea DRM/PO Token. En producción asumir RapidAPI + proxy como camino fiable; yt-dlp full es fallback opcional.

**Config ideal en OVH:**

```env
USE_RAPIDAPI_DOWNLOAD=true
WEBSHARE_PROXY_FILE=/app/proxies.txt   # 20 proxies Webshare Static
SUPADATA_API_KEY=...
```

**Partial download:** descarga bytes `0` hasta el último `end_time` de los momentos (+ buffer 15s), una sola vez para todos los clips. Ahorra bandwidth vs descargar el video completo.

### Step 5: Generación de clips (por cada momento)

**Módulos:** [`services/clip_generator.py`](services/clip_generator.py), [`services/transcriber.py`](services/transcriber.py)

Por cada `ViralMoment` (típicamente 5):

```
1. Cortar segmento del video fuente (muxed o per-clip)
2. Whisper per-clip (Groq → OpenAI fallback)
   - Word-level timestamps para subtítulos sincronizados
   - Prompt con título del video + contexto del transcript
3. generate_clip() pipeline FFmpeg:
   - cut_clip()           → recorte preciso
   - to_vertical_9_16()   → 9:16 con fondo blur
   - burn_subtitles()       → subtítulos palabra por palabra
   - burn_overlay_text()    → hook viral primeros segundos
4. Upload a R2 → clip_url público
5. save_content_result() en Supabase
```

**Salida por momento en `content_results`:**

| Campo | Contenido |
|-------|-----------|
| `type` | `twitter_thread`, `linkedin_post`, `tiktok_caption` |
| `content` | Copy generado |
| `clip_url` | URL pública R2 del MP4 9:16 |
| `raw_clip_url` | Segmento sin overlay (cache para re-edits) |
| `whisper_words` | JSON word-level para el editor |
| `viral_overlay` | Texto del overlay |
| `hook_score`, etc. | Scores de viralidad |

### Step 6: Finalización

- `jobs.status` → `completed`
- `deduct_user_credit(user_id, job_id)` — RPC atómico en Supabase
- Cleanup: borra `downloads/` y `clips/` locales del video
- Si falla: `status=failed`, `error_message`, **no descuenta crédito**

---

## 5. Cola secundaria: clip edits

**Módulo:** [`services/clip_edit_processor.py`](services/clip_edit_processor.py)

Cuando el usuario edita un clip en el frontend (`EditClipDrawer`):

1. Frontend guarda draft en `clip_edits`
2. Usuario pide re-render → `status=queued`
3. Worker hace poll de `clip_edits` igual que jobs
4. Re-genera MP4 con los cambios (overlay, subtítulos, trim)
5. Sube a R2 y actualiza `clip_edits.status=completed`

---

## 6. Caches (ahorro de costos)

| Cache | Tabla | Evita |
|-------|-------|-------|
| Transcript | `transcription_cache` | Re-fetch Supadata mismo video |
| Análisis IA | `analysis_cache` | Re-llamar Gemini mismo video |
| Categoría | `category_cache` | Re-clasificar podcast/business |
| Raw clip | R2 `raw_clips/` | Re-descargar segmento en re-edits |

---

## 6.1 Pipeline de IA (Plan calidad IA, 2026)

### Modelos por tarea (`config/model_tiers.py`)

Documentación detallada: [`docs/INFORME_LLMS.md`](../docs/INFORME_LLMS.md) y [`docs/RECOMENDACION_MODELOS_LLM.md`](../docs/RECOMENDACION_MODELOS_LLM.md).

| Tarea | Env var | Default (jul 2026) | Reasoning (OpenRouter) |
|-------|---------|-------------------|------------------------|
| Selección de momentos | `MODEL_ANALYSIS` | `google/gemini-3.5-flash` | `low` |
| Copy (threads/posts) | `MODEL_COPY` | `google/gemini-3.5-flash` | `minimal` |
| Juez de scoring | `MODEL_JUDGE` | `openai/gpt-5.4-nano` | `none` |
| Clasificador | `MODEL_CLASSIFIER` | `google/gemini-2.5-flash-lite` | — |

Gemini 3.x: temperature omitida (default proveedor). Llamadas centralizadas en
`config/llm_chat.py` con logging de `response.usage` (`LOG_LLM_USAGE`).

Compat: `OPENROUTER_MODEL` / `OPENROUTER_COPY_MODEL` / `OPENROUTER_CLASSIFIER_MODEL`
siguen funcionando como fallback. El worker warnea al arrancar si algún
modelo es `:free` o apunta a Gemini 2.0 (apagado jun 2026).

`PROMPT_VERSION` en `analysis_cache.py`: bump a `v4` tras migración de modelos.

### Dos pasadas (`TWO_PASS_ANALYSIS=true`, default)

1. **Pasada A** (`services/moment_selector.py`): prompt corto enfocado SOLO en
   seleccionar momentos. Sobre-genera `min(12, minutos)` candidatos con scores
   preliminares, rankea y conserva los top N. Sin copy.
2. **Pasada B** (`generate_moment_copy_full` en `processor.py`): post-Whisper,
   genera TODO el copy (thread, LinkedIn, caption, hook final, viral_overlay)
   desde el texto REAL del clip recortado. Corre antes de `generate_clip` para
   que el overlay quemado sea el final. Si el clip cae a fallback de YouTube,
   hay un "copy rescue" con el slice del transcript.

Con `TWO_PASS_ANALYSIS=false` (o si la pasada A falla) se usa el mega-prompt
legacy y su copy queda como borrador que la pasada B pisa.

### Refinamiento de cortes (Fase 3)

- `refine_bounds_to_sentences` (clip_generator): tras Whisper, los límites del
  clip se ajustan a boundaries de oración (puntuación + gaps >0.6s + fin de
  segmentos). Fragmentos iniciales (arranque en minúscula) se dropean y
  oraciones finales incompletas se recortan.
- `validate_durations(transcript=...)`: el truncado a 60s snapea al fin de
  segmento más cercano en vez de cortar seco.
- Ancla de verificación: si `first_phrase_in_audio` aparece desplazada dentro
  del clip, el inicio se ajusta al match real.

### Scoring calibrado y ROI honesto (Fase 4)

- `services/scorer.py`: juez independiente (`MODEL_JUDGE`) puntúa el clip
  final contra una rúbrica anclada. Se persisten `score_judge` y `score_llm`
  (columnas JSONB en `content_results`) para calibrar; los scores mostrados
  son los del juez.
- `roi_time_saved` = `8 + 0.5×seg_clip + 15×piezas_de_copy` (determinístico).
- `verification_failed` (bool): first Y last phrase no matchean el audio real
  — visible como badge "⚠ Verificar corte" en la card.
- `sub_coverage` y `words_per_sec` se persisten como métricas de calidad.
- Requiere `supabase_migration_ai_quality.sql`.

### Personalización (Fase 5)

- `jobs.tone` (selector en el dashboard) + `users.display_name` /
  `users.professional_title` se inyectan en los prompts de copy.
- El clasificador recibe los primeros ~1500 chars del transcript.
- `ENABLE_ENTERTAINMENT_CATEGORY=true` reactiva el prompt de entertainment.

### Eval loop (Fase 6)

```bash
python worker/eval/run_golden_set.py           # análisis-only
python worker/eval/run_golden_set.py --tier full   # + pasada B + juez
python worker/eval/run_golden_set.py --tier smoke  # pre-deploy (~1 video)
python worker/eval/run_golden_set.py --json    # output para CI
```

Ver **`worker/eval/README.md`** para tiers, plan de trabajo y comandos VPS.

Corre el golden set (`worker/eval/golden_set.json`) y sale con exit code 1 si
alguna métrica queda bajo los `thresholds` — usable antes de deploy.

---

## 7. Funcionamiento ideal vs realidad actual

### Flujo ideal (happy path)

```
Usuario pega URL → job pending (< 1s)
    ↓
Worker claim (< 3s)
    ↓
Supadata transcript (5–15s)
    ↓
Gemini análisis 5 momentos (20–40s)
    ↓
RapidAPI URLs + partial download (1–5 min según duración)
    ↓
5 clips × (Whisper 10s + FFmpeg 30s) = ~3 min
    ↓
Upload R2 + save results
    ↓
Job completed — usuario ve clips en dashboard
```

**Tiempo total ideal:** 5–15 min para video de 30–60 min.

### Puntos de fallo conocidos

| Problema | Síntoma | Mitigación |
|----------|---------|------------|
| Proxy Webshare sin créditos | `402 Payment Required` | Renovar plan Static $6/mes |
| Video DRM | yt-dlp Plan A falla | Plan B RapidAPI (automático) |
| Supabase pausado | `/health` 503, worker no conecta | Reactivar proyecto Supabase |
| OOM (RAM) | Job zombie > 20 min | `MAX_WORKERS=2` si 12GB RAM apurada |
| OpenRouter model 404 | Clasificador usa keywords | Verificar `OPENROUTER_MODEL` |
| Partial download bug | `vid_total` / timeout | Mantener worker actualizado (git pull) |

### Fallback degradado

Si todo falla en descarga, el job **igual completa** con:
- Copy para redes (Twitter, LinkedIn, TikTok)
- Links deep-link a YouTube con `&t=XXXs`
- **Sin** archivos MP4 en R2

El usuario ve resultados pero sin clips descargables.

---

## 8. Deploy y operación (OVH)

### Docker worker-only

```bash
docker compose -f docker-compose.worker.yml up -d --build
docker compose -f docker-compose.worker.yml logs -f
```

### Deploy automático

```
git push origin main → GitHub Actions → SSH VPS → deploy/deploy-worker.sh
```

### Comandos útiles en el VPS

```bash
# Estado
docker compose -f docker-compose.worker.yml ps

# Logs en vivo
docker compose -f docker-compose.worker.yml logs -f

# Reiniciar sin rebuild
docker compose -f docker-compose.worker.yml restart

# Ver último commit deployado
cd ~/viralengine && git log -1 --oneline
```

### Archivos críticos solo en VPS

```
~/viralengine/.env           # API keys
~/viralengine/proxies.txt    # 20 proxies Webshare (http://user:pass@host:port)
```

---

## 9. Estructura de módulos

```
worker/
├── main.py                    # Entry, watch_queue, process_job
├── config/
│   ├── validate_env.py        # Startup validation
│   └── logging_config.py
├── models/
│   └── schemas.py             # ViralMoment, AnalysisResult, Pydantic
├── services/
│   ├── yt_transcript.py       # Supadata + transcript cache
│   ├── processor.py           # Clasificación + Gemini análisis
│   ├── downloader.py          # yt-dlp, RapidAPI, partial download, proxies
│   ├── transcriber.py         # Whisper Groq/OpenAI word-level
│   ├── clip_generator.py      # FFmpeg 9:16 + subtítulos + overlay
│   ├── clipper.py             # Legacy (supersedido por clip_generator)
│   ├── supabase_client.py     # DB, R2 upload, créditos, progress
│   ├── storage_client.py      # Cloudflare R2 boto3
│   ├── clip_edit_processor.py # Re-render cola clip_edits
│   ├── transcript_cache.py
│   ├── analysis_cache.py
│   ├── validation.py          # Filtro duraciones
│   └── content_validators.py  # Calidad copy IA
├── downloads/                 # Temp video (cleanup 24h)
├── clips/                     # Temp clips (cleanup 24h)
└── tests/                     # pytest unitarios
```

---

## 10. Diagrama end-to-end completo

```mermaid
sequenceDiagram
    participant U as Usuario
    participant F as Vercel Frontend
    participant B as Render API
    participant S as Supabase
    participant W as OVH Worker
    participant Y as YouTube APIs
    participant AI as OpenRouter/Groq
    participant R2 as Cloudflare R2

    U->>F: Pega URL YouTube
    F->>B: POST /process (JWT)
    B->>S: INSERT job pending
    B-->>F: job_id

    loop cada 3s
        W->>S: SELECT pending jobs
        W->>S: UPDATE processing (claim)
    end

    W->>Y: Supadata transcript
    W->>AI: Gemini análisis viral
    W->>Y: RapidAPI + proxy download
    W->>AI: Whisper per-clip
    W->>W: FFmpeg 9:16 + subtítulos
    W->>R2: Upload MP4 clips
    W->>S: INSERT content_results
    W->>S: job completed + deduct credit

    U->>F: Ver resultados
    F->>B: GET /status/:jobId
    B->>S: SELECT results
    B-->>F: clips + copy
```

---

## 11. Métricas de éxito

Un job se considera **exitoso ideal** cuando:

- [ ] `jobs.status = completed`
- [ ] 5 filas en `content_results` con `clip_url` válido (R2)
- [ ] Clips reproducibles en el dashboard (9:16, subtítulos, overlay)
- [ ] Copy para Twitter, LinkedIn y TikTok presente
- [ ] 1 crédito descontado en `transactions`
- [ ] Tiempo total < 20 min para video < 1 hora
- [ ] Sin fallback a deep-links de YouTube

---

## 12. Referencias

- Deploy VPS: [`deploy/SETUP-DEPLOY.md`](../deploy/SETUP-DEPLOY.md)
- API backend: [`API_DOCUMENTATION.md`](../API_DOCUMENTATION.md)
- README general: [`README.md`](../README.md)
