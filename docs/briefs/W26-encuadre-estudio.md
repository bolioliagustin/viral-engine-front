# W26 — Encuadre para estudio multicámara

**Rama:** `feat/worker-encuadre-estudio` · **Agente:** visual · **Ola:** 3 · **Base:** `integracion/mejora-ola-3` · **Depende de:** nada en código; las etiquetas de "se_ve_mal" vienen de la Ola 2 · **Categoría:** mejora

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H13). Leé también [`../PROYECTO.md`](../PROYECTO.md) §5.5, punto 9 (W5, `REFRAME_MODE`), y el término Encuadre de [`../../CONTEXT.md`](../../CONTEXT.md). El módulo es `services/reframe.py` (escenas con PySceneDetect, caras con YuNet, layouts Split, Fill y Fit).

En el job `fb287cba` (estudio con varias cámaras, 5 panelistas y público):
- los clips 1 y 5 salieron en **Fit**: el 16:9 en una franja chica y dos tercios de pantalla borrosos;
- los clips 2 y 3 salieron en **Split**, con una mitad que a veces muestra público o espaldas en vez de quien habla;
- el zócalo de la fuente quedó cortado en las dos mitades.

## Parte A — Spike: medir W5 (2 días máximo)

1. Juntá ≥ 30 escenas de clips reales de charla y entrevista: los 5 de `fb287cba` (URLs públicas de R2 en `content_results`) y los del golden set.
2. Para cada escena, guardá una miniatura de la fuente, el layout elegido y la miniatura del resultado 9:16.
3. Etiquetá cada escena: correcto, o incorrecto con el motivo (público, espaldas, cara cortada, plano general en Fit, zócalo). Hacé un primer paso vos mismo mirando las imágenes y pedile a Agustín que valide 10.
4. Reportá el porcentaje correcto por layout y por motivo, en `worker/eval/runs/<fecha>-encuadre-baseline.json`, con un markdown de muestras.

## Parte B — Mejoras (detrás de `ENCUADRE_ESTUDIO=on|off`)

Priorizá según el reporte de A. Candidatas:

1. **Quién habla:** entre las caras estables de la escena, elegí la que tiene más movimiento en la zona de la boca (diferencia entre cuadros muestreados) correlacionado con la energía del audio de esa escena. Fill sobre esa cara.
2. **Público fuera:** una cara cuenta como hablante solo si supera un tamaño mínimo relativo y es estable. Split solo con **dos hablantes** en mitades distintas, nunca con público.
3. **Planos generales** (ninguna cara supera el tamaño mínimo): Fill centrado en la cara más grande, o un Fit con zoom 1,3–1,5× que evite dos tercios borrosos. Medí cuál se ve mejor.
4. **Zócalos de la fuente:** detectá regiones estáticas en el tercio inferior (baja varianza temporal y alta densidad de bordes) y ubicá los subtítulos por encima o fuera de esa región.

## Criterios de aceptación

- [ ] Reporte A commiteado, con muestras.
- [ ] Tests con escenas sintéticas o fixtures: elección del hablante, exclusión de público, plano general, detección de zócalo.
- [ ] Sobre las mismas ≥ 30 escenas: layout correcto ≥ 85 % con `on` (y cuánto era con `off`).
- [ ] Tiempo de render por clip ≤ +20 % en el VPS (medí en la Mac y extrapolá con la relación de CPU que figura en `WORKER.md`, o pedí la medición en el VPS).
- [ ] `PROYECTO.md` §5.5 (punto 9) y `CONTEXT.md` (Encuadre) actualizados.
- [ ] La suite del worker está en verde.

## Fuera de alcance

Seguimiento cuadro a cuadro · diarización · animaciones o B-roll · subtítulos (salvo su posición respecto del zócalo).

## Entrega

Commit y push incremental. PR contra `integracion/mejora-ola-3` con la plantilla de `PLAN_MEJORA.md` §8.4, con miniaturas antes y después en la descripción.
