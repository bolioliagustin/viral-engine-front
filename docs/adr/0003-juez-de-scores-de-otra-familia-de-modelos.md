# El juez de scores es de otra familia de modelos que el generador

El modelo que elegía los momentos también se ponía la nota (scores 7–9 sistemáticos, inútiles para el usuario). Se agregó un juez independiente (`MODEL_JUDGE`, hoy OpenAI mientras la selección y el copy usan Gemini) que puntúa el clip final contra una rúbrica con anclas explícitas y usando solo el texto real del clip. Se persisten ambos scores (`score_llm` y `score_judge`) para calibrar, y la interfaz muestra el del juez. Cruzar familias evita que un modelo favorezca la salida de su propia familia.

## Consequences

- Dependencia de dos proveedores a través de OpenRouter; costo marginal (~US$0.001 por job medido en julio de 2026).
- Si el juez falla, el clip conserva el score del generador; el ROI mostrado es una fórmula determinística, no lo estima ningún modelo.
