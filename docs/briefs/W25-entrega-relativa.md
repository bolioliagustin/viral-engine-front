# W25 — Entrega por calidad, nunca rotos

**Rama:** `feat/worker-entrega-relativa` · **Agente:** ranking · **Ola:** 2 · **Base:** `integracion/mejora-ola-2` · **Depende de:** W18 (el piso ya excluye rotos) y W24 (rankeador y etiquetas por Formato) · **Categoría:** mejora

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §0 y §3 (la definición de "1000 %": capturar lo mejor del video entero, sin cuota de cantidad), §1 (H4, H11), §5 (D7) y el Anexo A.2. Leé también [`../PLAN_CALIDAD.md`](../PLAN_CALIDAD.md) §9, "Calibración del umbral de entrega" (INT-5), `worker/eval/umbral_entrega.py`, `worker/eval/calibracion.py`, y los términos Job, Posteable y Score visible de [`../../CONTEXT.md`](../../CONTEXT.md).

Con `DELIVERY_JUDGE_MIN=15` en la escala de Jev, solo 5 de 30 candidatos pasan aun con el pipeline sano, y se entrega el piso de 5. Ese umbral absoluto no significa lo mismo con otro formato o rankeador. Además, el piso rellena para llegar a un número. El objetivo no es entregar más, sino entregar **todo lo excelente y nada flojo**.

## Comportamiento actual

`select_finalists(candidates, target)` entrega todo lo que tenga `score_candidate ≥ DELIVERY_JUDGE_MIN` hasta `DELIVERY_MAX_CLIPS`, y completa hasta `max(target, 3)` con los siguientes (desde W18, sin rotos).

## Comportamiento deseado

Detrás de `ENTREGA=calidad|umbral` (default `umbral` hasta pasar G2):

1. **Piso de calidad calibrado** por rankeador y Formato. Es la nota a partir de la cual, según las etiquetas de Posteable, la precisión de lo que está por encima es ≥ 80 %. `calibracion.py` lo calcula y lo reporta con su n y su intervalo. Se guarda como configuración versionada: un JSON en `worker/config/` o variables de entorno por Formato. Donde no haya etiquetas suficientes (n < 15 por Formato), se usa el piso global y se marca como provisorio en el log.
2. **Entregables:** usables, sin señales de nivel FUERTE, sin conflicto de diversidad (la regla de hoy) y con `score_candidate ≥ piso`. Se entregan **todos**, ordenados por score.
3. **Sin cuota ni relleno.** La cantidad sale del contenido.
   - Tope solo por costo: `DELIVERY_MAX_CLIPS`, 20 en videos largos.
   - Si hay 0 entregables, el job falla y devuelve el crédito, como hoy.
   - Si hay 1 o 2, se entregan 1 o 2.
   - `target_moment_count` y `DELIVERY_MIN_CLIPS` dejan de rellenar con este modo.
4. El Score visible (percentil curvado) no cambia.
5. `umbral_entrega.py` gana el modo `calidad`, para simular sobre `candidates_all` con el piso calibrado, sin llamar a ningún LLM.

## Interfaces clave

- `select_finalists(candidates, target)`: mismo retorno. El piso por Formato entra como parámetro o configuración.
- Variables de entorno o configuración nuevas en `.env.example` y en `PROYECTO.md` §11.

## Criterios de aceptación

- [ ] Tests:
  - 0 entregables → falla con devolución;
  - 1 entregable → entrega 1, sin relleno;
  - 25 entregables → tope de costo;
  - rotos excluidos;
  - diversidad;
  - piso por Formato, y global cuando faltan etiquetas.
- [ ] Reporte de calibración del piso por Formato commiteado (n, precisión arriba del piso, intervalo).
- [ ] Simulación con `umbral_entrega.py --modo calidad` sobre el golden set, commiteada: clips por video, **captura de lo mejor** contra Referencias, precisión con etiquetas donde existan, y costo estimado de la Pasada B.
- [ ] Con las etiquetas disponibles al cierre de la Ola 2: captura de lo mejor ≥ 60 %, precisión ≥ 75 %, 0 rotos, y costo dentro de US$0,003 por minuto de video.
- [ ] ADR `0013-entrega-por-calidad.md`, que actualiza lo que corresponda de la ADR 0008. `CONTEXT.md` gana **Piso de calidad** y el Job pasa a "entrega lo que supera el piso de calidad".
- [ ] La suite del worker está en verde.

## Fuera de alcance

Rúbricas (W24) · presentación del score en el frontend · créditos y precios (decisión A3).

## Entrega

Commit y push incremental. PR contra `integracion/mejora-ola-2` con la plantilla de `PLAN_MEJORA.md` §8.4.
