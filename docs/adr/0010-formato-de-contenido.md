# La Pasada A elige momentos según el Formato del video, no según una Categoría binaria

Estado: **propuesta, detrás de `FORMATOS` (default `off`) hasta pasar G1.** Fecha: 2026-09-28. Línea: W22 ([brief](../briefs/W22-formatos-de-contenido.md), [`PLAN_MEJORA.md`](../PLAN_MEJORA.md) §6 Ola 1). Construye sobre el [ADR 0009](0009-seleccion-por-ventanas.md).

## Contexto

- El clasificador leía los primeros 1500 caracteres y elegía **podcast** o **business**. Un programa de humor con 5 panelistas y público (B60BHDNFNxM, Blender, user_recommended_01) salía "podcast" y recibía el foco de una entrevista: pregunta → respuesta sorprendente, revelación del invitado. Ese foco no busca anécdotas con remate, cruces ni imitaciones.
- La validación de Agustín del 28-sep (`worker/eval/runs/2026-09-28-analisis-validacion.md` §3 y §5) dejó un criterio distinto por formato:
  - **clase:** impacto, automatismo, resultado y el **cómo**, con el contexto antes del resultado; se descarta lo que solo se muestra.
  - **monólogo:** idea completa con conclusión, sin frases sueltas.
  - **charla:** anécdota con remate, entendible sola, un solo tema; sin rutinas, publicidad ni reacciones largas.
  - **entrevista:** acepta casi todo (96 %).
- "Corto / sin conclusión" es el motivo de descarte más frecuente. La mediana del núcleo es de 43 s en los aceptados y de 29 s en los descartados.
- La Pasada A no excluía la publicidad: en B60BHDNFNxM hay un aviso de DiDi en 3100–3168 s.

## Decisión

Con `FORMATOS=on`:

1. **Formato con cuatro valores** (`entrevista`, `charla`, `monologo`, `clase`), clasificado por el modelo barato (`classifier`) sobre título, canal y **tres extractos** de ~800 caracteres: inicio, mitad y final (`processor.clasificar_formato`). Un valor inválido o una falla del modelo caen a `entrevista`, con log. El Formato se cachea en `category_cache` con la clave de modelo `formato:<modelo>`, sin cambiar el esquema.
2. **Compatibilidad:** `category` se sigue llenando con el mapeo `entrevista|charla → podcast` y `monologo|clase → business`. El juez, `content_results` y el eval no cambian. `get_video_category` queda como envoltura mientras tenga consumidores.
3. **Foco por Formato** en `get_selection_prompt(…, formato=…)`, con el criterio de Agustín. Rige en la pasada única y en cada Ventana. Duraciones objetivo: charla y entrevista 30–120 s; monólogo y clase 20–90 s.
4. **Historia completa**, en todos los Formatos: ideas de ≥ ~30 s. Una anécdota de 60–120 s va entera en un solo candidato. El prompt trae un ejemplo de cómo **no** partir una historia.
5. **Publicidad:** el prompt la excluye. Después, una verificación barata sin modelo (`services/formatos.descartar_publicidad`) detecta tramos de aviso por palabras clave:
   - una marca fuerte ("voy a tirar un chivo", "auspiciado por", "código de descuento") o dos débiles distintas ("descargá la app", "la conseguís en"), extendidas por la marca comercial repetida;
   - descarta el candidato con > 50 % de su duración dentro de un aviso y lo loguea en `_pasada_a.descartados_publicidad`.
6. **Tono:** sigue solo en el copy y no influye en qué momentos se eligen.
7. **Caché:** `PROMPT_VERSION` v10 → v11, y el flag entra en la versión efectiva (`+formatos`, mecanismo de W18).

## Alternativas descartadas

- **Más categorías sin cambiar el foco** (reactivar `entertainment`). El problema no es el nombre: con cualquier Formato, el prompt solo distinguía podcast del resto.
- **Clasificar con el inicio del video.** Los programas arrancan distinto de como siguen (saludo, presentación del invitado); tres extractos cuestan ~1500 tokens más con el modelo barato.
- **Detectar publicidad con un modelo.** Otra llamada por video para algo que las palabras clave resuelven en los avisos del golden set.
  - Medido sobre los 7 transcripts: detecta DiDi, Oslava, YPF, NaranjaX y la promo del libro, y no pisa ninguna Referencia validada.
  - "Publicidad" suelta no es marca: en una charla se habla de publicidades sin estar pasando una. Solo cuenta la marca fuerte.
  - Se escapan los avisos sin palabras clave (Mortal Kombat, Crupier). Para esos queda la instrucción del prompt.
- **Cambiar el esquema** (una columna `formato` en `category_cache` o `analysis_cache`). Guardar el Formato en `category_cache` con otra clave de modelo alcanza y no obliga a una migración.

## Consecuencias

- El Formato es la etiqueta que las líneas siguientes usan por formato: W24 (rúbrica del juez y de Jev) y W27 (copy). `category` queda como dato derivado hasta que no tenga consumidores.
- Si el clasificador se equivoca, el video recibe el foco de otro Formato. Se mide contra `formato` del golden set, con un umbral de ≥ 90 % de acierto; `g1_seleccion.py` lo reporta.
- El detector de publicidad puede descartar un buen momento que hable como un aviso (dos marcas débiles juntas). Si pasa, se ve en `_pasada_a.descartados_publicidad`. Si todos los candidatos caerían, se conservan todos.
- Pedir ideas de ≥ ~30 s baja la cantidad de frases cortas. Las "excepcionales" siguen permitidas, pero el modelo las propone menos.
