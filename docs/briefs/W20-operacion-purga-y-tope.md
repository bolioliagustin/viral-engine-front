# W20 — Operación: purga, tope real de duración y runbook

**Responsables:** coordinador + Agustín (purga y runbook) · agente *fiabilidad* (código del tope, rama `fix/worker-tope-duracion`) · **Ola:** 0 · **Base:** `integracion/mejora-ola-0` · **Depende de:** la purga, de nada; el runbook, de W18 (script de purga)

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H1, H2, H15) y §5.2 (A1, A5). El tope de 90 min del backend es "fail open": si no logra leer la duración, deja pasar el video. Así entró uno de 111 min. Y mergear a `main` despliega el worker (`.github/workflows/deploy.yml` escucha el push), aunque `AGENTS.md` diga que el deploy es manual.

## Parte 1 — Purga inmediata (coordinador + Agustín, con OK de Agustín: A1)

1. El coordinador borra en Supabase, para `B60BHDNFNxM` y `fVSgIKS_RSk`:
   - las filas de `analysis_cache` (1 y 3 filas),
   - la fila `B60BHDNFNxM` de `transcription_cache`,
   - `category_cache`, si corresponde.

   Antes lista lo que va a borrar y lo muestra.
2. Agustín corre en el VPS: `docker compose -f docker-compose.worker.yml exec worker sh -c 'ls -la /app/downloads | grep -E "B60BHDNFNxM|fVSgIKS_RSk"; rm -f /app/downloads/B60BHDNFNxM* /app/downloads/fVSgIKS_RSk*'`.
3. El coordinador corre el detector de idioma sobre `analysis_cache` y `transcription_cache` y confirma 0 envenenados. Es la condición de G0.

## Parte 2 — Runbook (coordinador, después de W18)

Una sección en [`../PROYECTO.md`](../PROYECTO.md) §10.3: **"Después de un fix de audio, transcript o idioma"**. Cubre: cómo detectar videos afectados (detector de idioma, jobs del período), `purge-video-cache.py <id>` en la Mac o dentro del contenedor, y verificación. También una nota en `AGENTS.md`: *mergear a `main` despliega el worker; todo cambio de comportamiento entra con flag apagado*.

## Parte 3 — Tope real de duración (agente *fiabilidad*)

**Comportamiento deseado:** el worker conoce la duración real después del transcript. Si supera `MAX_VIDEO_MINUTES`, el job termina `failed` con el mensaje "El video dura N min; el máximo es M min" y el crédito se devuelve por el camino de F1. El default del worker es **150** (A5). El backend sigue "fail open" (UX), pero el worker es la autoridad.

**Criterios de aceptación:**
- [ ] Test: una duración de 151 min con tope 150 da `failed`, el mensaje y la devolución (mocks).
- [ ] Test: una duración desconocida no bloquea (se loguea).
- [ ] `MAX_VIDEO_MINUTES` documentada en `.env.example` y en `PROYECTO.md` §11 para el worker.
- [ ] La suite del worker está en verde.

**Fuera de alcance:** cambiar el backend · avisos en la UI para videos de 90–150 min (backlog).

**Entrega:** PR contra `integracion/mejora-ola-0` con la plantilla de `PLAN_MEJORA.md` §8.4.
