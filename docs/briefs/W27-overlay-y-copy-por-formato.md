# W27 — Overlay y copy por Formato

**Rama:** `feat/worker-overlay-promesa` · **Agente:** copy · **Ola:** 3 · **Base:** `integracion/mejora-ola-3` · **Depende de:** W22 (Formato) · **Categoría:** mejora

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H14). Leé también [`../PLAN_CALIDAD.md`](../PLAN_CALIDAD.md) (W6 fidelidad y W13 títulos), `docs/adr/0002-copy-en-dos-pasadas-desde-el-audio-real.md`, y los términos Overlay, Título, Descripción y Pasada B de [`../../CONTEXT.md`](../../CONTEXT.md).

La regla de W6 exige que el overlay use palabras de los **primeros segundos** del clip. Con un arranque flojo sale literal: "DOMINGO EL MIÉRCOLES ARGENTINO", "ESTÁBAMOS RECORDANDO LA TELEVISIÓN". Y el copy tiene un solo estilo, informativo ("El clip revela…", "Descubre…"), que en humor queda fuera de lugar. El 21-sep, 2 de los 8 rechazos fueron por copy.

## Comportamiento actual

- `generate_moment_copy_full` valida con `overlay_is_faithful(overlay, clip_head)`, `hook_is_faithful` y `title_is_valid`.
- Si el overlay no es fiel tras el reintento, cae a `derive_overlay_from_text(clip_head)`.
- Un solo estilo de copy para todo Formato.

## Comportamiento deseado

Detrás de `COPY_POR_FORMATO=on|off`:

1. **Overlay = promesa.** ≤ 4 palabras que dicen por qué ver el clip, usando palabras que **aparecen en el clip** (en cualquier parte, no solo al arranque). La fidelidad se mide contra el texto completo del clip. Si el overlay coincide con las primeras palabras del clip, se trata como no fiel y se reintenta una vez; el respaldo es la frase más citable del clip, no el arranque.
2. **Estilo por Formato** en la Pasada B:
   - **charla:** título que cita la frase o la situación ("«Porteño, usted tiene 3 problemas»", "El día que Bilardo mandó a tirar piedras al auto de Caniggia"); descripción sin "Descubre" ni "El clip revela"; el caption busca la reacción.
   - **entrevista, monólogo y clase:** las reglas de W13 de hoy (la afirmación concreta, el dato).
3. Las reglas duras de W13 (≤ 60 caracteres, sin moldes vacíos, a lo sumo un "¡…!") valen para todos los Formatos.

## Criterios de aceptación

- [ ] Tests: overlay igual al arranque = no fiel; overlay con palabras de la mitad del clip = fiel; el respaldo usa la frase citable; el estilo cambia según el Formato; las reglas de W13 siguen vigentes.
- [ ] Sobre los clips del golden set (tier `full` o `e2e`, con `--json`):
  - overlays literales del arranque ≈ 0;
  - 0 descripciones de charla con "Descubre" o "El clip revela";
  - `copy_report.py` (W13) sin regresión en los otros Formatos.
- [ ] Con etiquetas de la Ola 3: "copy_malo" ≤ 10 % de los rechazos.
- [ ] `PROMPT_VERSION` subido si cambiaste prompts, y golden set `smoke` corrido (regla de `AGENTS.md`).
- [ ] `CONTEXT.md` (Overlay) y `PROYECTO.md` §6 actualizados.
- [ ] La suite del worker está en verde.

## Fuera de alcance

Selección de momentos · subtítulos · traducciones.

## Entrega

Commit y push incremental. PR contra `integracion/mejora-ola-3` con la plantilla de `PLAN_MEJORA.md` §8.4, con 5 ejemplos antes y después por Formato.
