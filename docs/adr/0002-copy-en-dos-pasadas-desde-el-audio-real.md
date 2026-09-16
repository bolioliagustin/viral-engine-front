# El copy definitivo se genera en una segunda pasada desde el audio real del clip

Cuando la selección de momentos y el copy salían de una sola llamada sobre el transcript completo, el texto publicado no coincidía con el clip final: los cortes se ajustan después (silencios, límites de oración, ancla del hook) y la transcripción de YouTube no es palabra por palabra. Se separó el pipeline: la Pasada A solo selecciona momentos (con sobre-generación y ranking), y la Pasada B escribe las piezas de copy, el hook y el overlay definitivos a partir de la transcripción palabra por palabra del clip ya cortado, antes de renderizar para que el overlay quemado sea el final. El juez puntúa después sobre ese mismo texto.

## Consequences

- Unas 5 llamadas LLM más por job y el copy deja de ser cacheable (depende del corte real).
- El prompt único original queda como fallback (`TWO_PASS_ANALYSIS=false` o si la Pasada A falla) y su copy se considera borrador.
