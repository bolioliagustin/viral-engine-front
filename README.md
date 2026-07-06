# YouTube Viral Content Engine

SaaS que convierte videos de YouTube en clips virales 9:16 con copy para redes sociales (Twitter, TikTok, LinkedIn).

## Arquitectura de producción (actual)

```
Vercel (frontend)  →  Render (backend API)  →  Supabase (cola)
                                                    ↑
                                            OVH VPS (worker)
                                                    ↓
                                            Cloudflare R2 (clips)
```

| Componente | Dónde corre | URL / acceso |
|------------|-------------|--------------|
| Frontend | Vercel | `NEXT_PUBLIC_API_URL` → Render |
| Backend API | Render | `https://viral-engine-backend.onrender.com` |
| Worker | OVH VPS (Docker) | Solo worker — ver abajo |
| DB + Auth | Supabase | Cloud |
| Clips | Cloudflare R2 | Cloud |

### Worker en OVH (solo worker, sin backend local)

En el VPS, usar el compose de solo worker:

```bash
cd ~/viralengine
bash deploy/worker-only.sh
# o manualmente:
docker compose -f docker-compose.worker.yml up -d --build
docker compose -f docker-compose.worker.yml logs -f
```

Para volver a levantar backend + worker en OVH (si migrás la API):

```bash
docker compose -f docker-compose.worker.yml down
docker compose up -d --build
```

## Arquitectura local

| Servicio | Carpeta | Puerto |
|----------|---------|--------|
| Frontend | `frontend/` | 3001 (dev) |
| Backend API | `backend/` | 3000 |
| Worker | `worker/` | — (polling Supabase) |

## Desarrollo local

### Prerrequisitos

- Node.js 20+, Python 3.12+, FFmpeg en PATH
- Cuenta Supabase con migraciones SQL aplicadas (archivos `supabase_migration_*.sql` en la raíz)
- Archivo `.env` en la raíz (copiar de `.env.example`)

### Variables frontend (`frontend/.env.local`)

```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_API_URL=http://localhost:3000
```

### Levantar servicios (3 terminales)

```bash
# Terminal 1 — Backend
cd backend && npm install && npm run dev

# Terminal 2 — Worker
cd worker && pip install -r requirements.txt && python main.py

# Terminal 3 — Frontend
cd frontend && npm install && npx next dev -p 3001
```

Abrir http://localhost:3001

### Docker (backend + worker)

```bash
cp .env.example .env   # completar valores
docker compose up -d --build
curl http://127.0.0.1:3000/health
```

---

## Migración a producción — OVHcloud VPS

### Servidor objetivo

| Parámetro | Valor |
|-----------|-------|
| Proveedor | OVHcloud VPS-3 2027 |
| Datacenter | BHS — Beauharnois, Canadá |
| CPU / RAM / Disco | 6 vCore / 12 GB / 100 GB NVMe |
| OS | Ubuntu 26.04 LTS |

### Paso 1 — Provisionar VPS en OVH

1. Crear el VPS con Ubuntu 26.04 en datacenter BHS (Canadá)
2. Anotar la **IP pública**
3. Configurar **firewall OVH** (panel de red): abrir puertos **22**, **80**, **443**
4. Apuntar un subdominio al VPS, ej. `api.tudominio.com` → IP del VPS

### Paso 2 — Setup inicial del servidor (una sola vez)

Conectarse por SSH y ejecutar:

```bash
ssh root@<IP-VPS>

# Clonar repo
git clone https://github.com/<tu-usuario>/mvp_p1.git /opt/viralengine
cd /opt/viralengine

# Instalar Docker, UFW, Caddy
bash deploy/setup-vps.sh
```

### Paso 3 — Configurar `.env` de producción

```bash
cp .env.example .env
nano .env
```

Variables **obligatorias** en producción:

| Variable | Dónde obtenerla |
|----------|-----------------|
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Supabase → Settings → API |
| `OPENROUTER_API_KEY` | openrouter.ai |
| `OPENAI_API_KEY` | platform.openai.com |
| `GROQ_API_KEY` | console.groq.com (recomendado) |
| `R2_ACCOUNT_ID` + keys R2 | Cloudflare → R2 |
| `SUPADATA_API_KEY` | supadata.ai |
| `WEBSHARE_PROXY_URL` | webshare.io (proxy residencial) |
| `LEMONSQUEEZY_*` | Lemon Squeezy dashboard |
| `FRONTEND_URL` | URL de Vercel, ej. `https://tu-app.vercel.app` |

### Paso 4 — Configurar Caddy (HTTPS)

```bash
cp deploy/Caddyfile.example /etc/caddy/Caddyfile
nano /etc/caddy/Caddyfile   # reemplazar api.YOURDOMAIN.com
systemctl reload caddy
```

### Paso 5 — Levantar servicios

```bash
cd /opt/viralengine
docker compose up -d --build
docker compose logs -f   # verificar que backend y worker arrancan
curl http://127.0.0.1:3000/health
curl https://api.tudominio.com/health
```

### Paso 6 — Actualizar servicios externos

| Servicio | Qué actualizar |
|----------|----------------|
| **Vercel** | `NEXT_PUBLIC_API_URL=https://api.tudominio.com` |
| **Lemon Squeezy** | Webhook URL → `https://api.tudominio.com/billing/webhook` |
| **Supabase** | Auth redirect URLs si cambiaron dominios |

### Paso 7 — Smoke test

1. Login en el frontend
2. Pegar una URL de YouTube corta (~5 min)
3. Verificar progreso en dashboard
4. Confirmar clips en la página de resultados y en R2

### Deploy automático (GitHub Actions)

Configurar secrets en el repo de GitHub:

| Secret | Valor |
|--------|-------|
| `VPS_HOST` | IP del VPS OVH |
| `VPS_USER` | `root` |
| `VPS_SSH_KEY` | Clave privada SSH |

Cada push a `main` ejecuta `deploy/deploy-docker.sh` en el VPS.

Deploy manual:

```bash
ssh root@<IP-VPS> "cd /opt/viralengine && bash deploy/deploy-docker.sh"
```

---

## Tests

```bash
cd backend && npm test
cd worker && python -m pytest tests/ -v
```

## Documentación adicional

- [API_DOCUMENTATION.md](API_DOCUMENTATION.md) — Endpoints del backend
- [MEJORAS_PROYECTO.md](MEJORAS_PROYECTO.md) — Backlog de mejoras

## Estructura del proyecto

```
mvp_p1/
├── frontend/          Next.js 16 — UI del SaaS
├── backend/           Express API — jobs, billing, auth
├── worker/            Python — descarga, IA, clips, R2
├── deploy/            Scripts de deploy y setup VPS
├── docker-compose.yml Producción (backend + worker)
└── supabase_*.sql     Migraciones de base de datos
```
