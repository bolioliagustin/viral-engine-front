# W22 — Formatos de contenido

**Rama:** `feat/worker-formatos` · **Agente:** selección · **Ola:** 1 · **Base:** `integracion/mejora-ola-1`, con W21 ya mergeado · **Depende de:** W21 y W19 · **Categoría:** mejora

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H8, H9), §2 (la fila del tono) y §5 (D5). Leé también el término Categoría de [`../../CONTEXT.md`](../../CONTEXT.md) (este trabajo lo reemplaza) y [`../PROYECTO.md`](../PROYECTO.md) §6.

El clasificador lee los primeros 1500 caracteres y elige podcast o business. Un programa de humor con 5 panelistas y público salió "podcast", y el foco de podcast está pensado para entrevistas: pregunta → respuesta sorprendente, revelación del invitado. Además la Pasada A propone historias de 30–50 s aunque el prompt diga 40–90, y no excluye la publicidad (en `B60BHDNFNxM` hay un aviso de DiDi en 3100–3168 s).

## Comportamiento actual

- `get_video_category(video_info, client, transcript_excerpt)` devuelve `podcast` o `business` (con `entertainment` detrás de un flag, pero `get_selection_prompt` solo distingue podcast del resto).
- `get_selection_prompt(duration, num_candidates, category, language)` arma el foco con dos variantes.

## Comportamiento deseado

Detrás de `FORMATOS=on|off` (default `off` hasta pasar G1):

1. **Formato** con cuatro valores:
   - `entrevista`: un host y un invitado, preguntas y respuestas;
   - `charla`: mesa, panel, stream o humor con 3+ voces, cruces y público;
   - `monologo`: una persona (coach, keynote, opinión);
   - `clase`: tutorial o explicación.
2. **Clasificador de Formato:** modelo barato sobre título, canal y **tres extractos** (inicio, mitad y final, ~800 caracteres cada uno). Devuelve un valor de los cuatro y se cachea por video como hoy la categoría. Para no romper consumidores (juez, `content_results`, eval), el campo `category` se sigue llenando con el mapeo `entrevista|charla → podcast`, `monologo|clase → business`.
3. **Foco por Formato** en `get_selection_prompt`:
   - **charla:** anécdota con remate (planteo → desarrollo → remate → reacción), imitación o personaje, cruce o chicana entre panelistas o con el público, frase citable que funciona sola, discusión con posturas claras, confesión inesperada. El clip incluye la reacción del remate (risas, "¡qué bueno!").
   - **entrevista:** el foco podcast de hoy, más "la respuesta completa".
   - **monologo:** el foco business de hoy (verdad contraintuitiva, utilidad, vulnerabilidad, curiosidad).
   - **clase:** un concepto explicado entero, con su ejemplo; el dato con su porqué.
4. **Historia completa (todos los formatos):** una anécdota de 60–120 s se propone entera, en un solo candidato, sin partirla. Duraciones objetivo: charla y entrevista 30–120 s; monólogo y clase 20–90 s. Incluí un ejemplo corto de qué es partir mal una historia.
5. **Publicidad:** la Pasada A no propone tramos de publicidad o auspicio (lectura de marca, "chivo", códigos de descuento). Agregá una verificación posterior barata: un candidato con > 50 % de solape con líneas de publicidad detectadas por palabras clave se descarta y se loguea.
6. **Tono:** sigue solo en el copy. Documentalo en `CONTEXT.md` (entrada Tono): "no influye en qué momentos se eligen".
7. `PROMPT_VERSION` sube a un valor nunca usado, y `FORMATOS` entra en la **versión efectiva de la caché** (mecanismo de W18, `PLAN_MEJORA.md` §4.1).

## Interfaces clave

- `get_video_category` → una función de Formato (nombre a tu criterio, en español); la vieja queda como envoltura mientras haya consumidores.
- `get_selection_prompt(…, formato=…)`: la variante por Formato.
- `golden_set.json` ya trae `formato` por video (W19): esa es la etiqueta para medir el clasificador.

## Criterios de aceptación

- [ ] Clasificador ≥ 90 % de acierto contra `formato` del golden set, con tests de parseo y de valores inválidos (cae a `entrevista` con log).
- [ ] Tests del prompt: cada Formato incluye su foco, la regla de historia completa y la exclusión de publicidad.
- [ ] Test de la verificación posterior de publicidad con las líneas del aviso de DiDi del Anexo B.
- [ ] Medición en el tier `seleccion` con `--reps 3`, `FORMATOS=on` contra W21 solo:
  - en videos de charla, `recall_completo@candidatos` +15 pp;
  - `historias_partidas` ≤ 1 por video;
  - `candidatos_en_exclusion` = 0;
  - sin regresión > 5 pp en otros formatos.
- [ ] ADR `0010-formato-de-contenido.md`. `CONTEXT.md`: **Formato** reemplaza a Categoría (dejá Categoría como "_Avoid_"/obsoleto con referencia), más la aclaración del Tono. `PROYECTO.md` §6 actualizado.
- [ ] La suite del worker está en verde.

## Fuera de alcance

Las rúbricas del juez y de Jev por Formato (W24) · el copy por Formato (W27) · override del Formato en la UI (backlog).

## Entrega

Commit y `git push -u origin feat/worker-formatos` después de cada paso en verde. PR contra `integracion/mejora-ola-1` con la plantilla de `PLAN_MEJORA.md` §8.4.
