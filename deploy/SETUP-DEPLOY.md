# Guía de deploy optimizado

## Arquitectura

```
Tu PC  →  git push main  →  GitHub  →  Actions SSH  →  OVH VPS (worker)
Vercel (frontend)  →  Render (API)  →  Supabase  ←  OVH worker
```

## Opción A — Automático (recomendado)

**Una vez configurado, solo hacés `git push origin main` y el VPS se actualiza solo.**

### Setup único (~10 min)

#### 1. Clave SSH para GitHub Actions (en tu PC)

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\viralengine_deploy" -N '""'
```

#### 2. Instalar clave pública en el VPS

```powershell
type "$env:USERPROFILE\.ssh\viralengine_deploy.pub" | ssh ubuntu@51.79.50.95 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

#### 3. Agregar secrets en GitHub

Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|--------|-------|
| `VPS_HOST` | `51.79.50.95` |
| `VPS_USER` | `ubuntu` |
| `VPS_SSH_KEY` | Contenido de `viralengine_deploy` (clave **privada**) |

#### 4. Flujo diario

```powershell
cd C:\Users\Usuario\Desktop\mvp_p1
# ... editás código ...
git add .
git commit -m "fix: descripción"
git push origin main
# → GitHub Actions deploya al VPS en ~1-2 min
```

Ver progreso: GitHub → **Actions** → "Deploy Worker to VPS"

---

## Opción B — Un solo comando desde tu PC

Si Actions no está configurado o querés forzar deploy manual:

```powershell
cd C:\Users\Usuario\Desktop\mvp_p1
.\deploy\push-deploy.ps1 -Message "fix: mi cambio"
```

Hace: commit (si hay cambios) → push → SSH al VPS → `git pull` → `docker compose` rebuild.

**Importante:** trabajá en `main` para que el VPS reciba los cambios (`git reset --hard origin/main`).

---

## Opción C — Solo en el VPS (sin PC)

```bash
ssh ubuntu@51.79.50.95
cd ~/viralengine
git pull origin main
bash deploy/deploy-worker.sh
```

---

## Qué NO subir a Git

| Archivo | Dónde vive |
|---------|------------|
| `.env` | Solo VPS (`~/viralengine/.env`) |
| `proxies.txt` | Solo VPS (`~/viralengine/proxies.txt`) |
| `frontend/.env.local` | Solo Vercel / local |

Estos archivos ya están en `.gitignore`.

---

## Branches — simplificar

Para un solo desarrollador, lo más simple:

1. Trabajá directo en **`main`**, o
2. Feature branch → **merge a main** → auto-deploy

El VPS siempre sigue `origin/main`. Los branches de Cursor no se deployan solos.

---

## Verificar deploy

```bash
ssh ubuntu@51.79.50.95 "cd ~/viralengine && git log -1 --oneline && docker compose -f docker-compose.worker.yml ps"
```
