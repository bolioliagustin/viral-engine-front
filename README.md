# viral-engine

SaaS que convierte un video de YouTube (y, en la próxima etapa, el archivo del creador) en clips verticales 9:16 con subtítulos, copy para redes y scores de viralidad. Español-first; cliente objetivo: podcasters y coaches.

| Leer | Para qué |
|---|---|
| [`docs/PROYECTO.md`](docs/PROYECTO.md) | Entendimiento completo: producto, arquitectura, pipeline, datos, API, infra, estado, plan |
| [`CONTEXT.md`](CONTEXT.md) | Glosario del dominio |
| [`docs/adr/`](docs/adr/) | Decisiones de arquitectura y por qué |
| [`AGENTS.md`](AGENTS.md) | Reglas de trabajo para agentes IA (Claude Code, Antigravity, OpenCode) |
| [`worker/WORKER.md`](worker/WORKER.md) | Detalle operativo del worker |

## Arquitectura

```
Vercel (frontend Next.js)  →  Render (API Express)  →  Supabase (Postgres + Auth; la tabla `jobs` es la cola)
                                                            ↑
                                                   OVH VPS (worker Python, Docker)  →  Cloudflare R2 (clips)
```

| Componente | Carpeta | Runtime | Puerto local |
|---|---|---|---|
| Frontend | `frontend/` | Node ≥ 20, Next.js 16 | 3001 |
| API | `backend/` | Node ≥ 20 (imagen `node:20-slim`), Express | 3000 |
| Worker | `worker/` | Python 3.12, FFmpeg | — (sondea Supabase cada 3 s) |

## Desarrollo local (macOS)

Requisitos: Node ≥ 20, [`uv`](https://docs.astral.sh/uv/) (instala Python 3.12), FFmpeg, [Supabase CLI](https://supabase.com/docs/guides/cli). Un solo `.env` en la raíz alimenta API y worker (copiar de [`.env.example`](.env.example)); el frontend usa `frontend/.env.local` (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`). En local usá un proyecto Supabase **de desarrollo**: un worker local apuntado al Supabase de la beta le roba jobs al del VPS.

```bash
# API
cd backend && npm ci && npm run dev
NODE_ENV=test npm test

# Worker
cd worker && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt pytest
source .venv/bin/activate && python main.py
ENVIRONMENT=development SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test \
  OPENROUTER_API_KEY=test OPENAI_API_KEY=test python -m pytest tests/ -q

# Frontend
cd frontend && npm ci && npx next dev -p 3001
npx tsc --noEmit
```

En una IP residencial (tu Mac) yt-dlp descarga de YouTube sin proxies ni RapidAPI; dejá `ENVIRONMENT=development` en local. En el VPS (`ENVIRONMENT=production`) el worker exige `RAPIDAPI_KEY` y proxies residenciales (`proxies.txt`).

## Base de datos

El esquema se versiona con Supabase CLI (`supabase/migrations/`, ver [ADR 0006](docs/adr/0006-esquema-versionado-con-supabase-cli.md)): `supabase link --project-ref <ref>` → `supabase db pull` (primera vez) → `supabase migration new <nombre>` → `supabase db push`. Los SQL históricos aplicados a mano están en [`supabase/legacy/`](supabase/legacy/).

## Deploy

- **Frontend:** Vercel desde `main`.
- **API:** Render desde `main` con [`render.yaml`](render.yaml); variables en el dashboard de Render.
- **Worker:** [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) hace SSH al VPS y corre [`deploy/deploy-worker.sh`](deploy/deploy-worker.sh) (`git reset --hard origin/main` + rebuild de `docker-compose.worker.yml`). Secrets del repo: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (clave privada cuyo `.pub` está en `~/.ssh/authorized_keys` del VPS; generar con `ssh-keygen -t ed25519 -f ~/.ssh/viralengine_deploy`).
- **Manual en el VPS:** `ssh ubuntu@<ip> && cd ~/viralengine && bash deploy/deploy-worker.sh`. Solo worker: `bash deploy/worker-only.sh`. Provisión de un VPS nuevo: `deploy/setup-vps.sh`. Proxies Webshare: `bash deploy/format-proxies.sh proxies-raw.txt > proxies.txt`. Logs: `bash scripts/worker-logs.sh tail 200`.
- Archivos que viven solo en el VPS: `.env`, `proxies.txt`, `cookies.txt` (opcional), `worker-logs/`.

Salud: `GET /health` (readiness con Supabase) y `GET /health/live` en la API.
