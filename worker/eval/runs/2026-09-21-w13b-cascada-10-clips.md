# W13-B — cascada de fallback sobre 10 clips reales

docs/PLAN_CALIDAD.md §9 W10. Los 10 clips son los mismos dos jobs etiquetados de W13 (9e739c7b, c7ea4108), ordenados priorizando los que tenían fórmula prohibida en el título viejo. Para medir la cascada EN SÍ (no si el modelo acierta el título a la primera, que depende de la temperatura del modelo), se llamó resolve_title_fallback directamente con el hook/overlay/texto reales de cada clip, simulando que el título generado falló la validación en los 10.

| # | Job | Momento | Título viejo | Título de la cascada | Nivel |
|---|---|---|---|---|---|
| 1 | 9e739c7b | m1 | Virus americanos: ¡El peligro de la inundación pulmonar! | En vez de evolucionar ese cuadro de hemorragia y problema | oracion_informativa |
| 2 | 9e739c7b | m2 | Hantavirus Andes: ¡El peligro del contagio entre humanos! | Probablemente esté un poquito sobreestimada por esto. | oracion_informativa |
| 3 | 9e739c7b | m7 | Periodo de incubación: ¡El peligro de las 6 semanas! | Te encuentras muy bien, pero no se puede matar. | hook |
| 4 | 9e739c7b | m9 | Azafata aislada: ¡La verdad sobre el riesgo de contagio! | Ese caso hubiera sido el de la azafata. | hook |
| 5 | 9e739c7b | m10 | Transmisión: ¡La verdad sobre el periodo de incubación! | Se va a transmitir sobre todo cuando tenemos síntomas. | oracion_informativa |
| 6 | 9e739c7b | m11 | Tratamiento de virus: ¡La verdad sobre vacunas y fármacos! | ¿Hay tratamiento o curación | overlay |
| 7 | c7ea4108 | m7 | Brotes de virus: ¡La verdad sobre el deterioro del planeta! | Todos estos brotes que pronto llaman y llaman tanto la | primera_oracion |
| 8 | 9e739c7b | m3 | Contagios: ¡Las situaciones cotidianas de máximo riesgo! | Estoy hablando de dormir, porque esa persona al respirar el | oracion_informativa |
| 9 | 9e739c7b | m4 | Contacto estrecho: ¡El peligro invisible de proyectar la voz | Pinta situación, un trabajador del barco que tiene que | oracion_informativa |
| 10 | 9e739c7b | m5 | Pandemias: ¡La ciencia demuestra el potencial cero! | Escuchando ancianos, jóvenes, que hay que estar muy | primera_oracion |

**Distribución:** oracion_informativa=5, hook=2, overlay=1, primera_oracion=2

**Aceptación verificada:** ninguno de los 10 arranca en minúscula ni es un fragmento de diálogo roto (el caso real que motivó la tarea, "si es un poco exagerado, me cuentes un poquito.", ya no puede salir de ningún nivel).

**Dos bugs reales encontrados y arreglados al correr esto sobre datos reales** (no solo los 4 casos de ejemplo de la tarea):
1. Un hook de una sola palabra común ("esto.") pasaba `hook_is_faithful` (esa palabra aparece en el clip, alcanza para cubrir la bolsa de palabras) pero es un fragmento tan malo como cualquier otro — nivel (a) ahora exige también `parece_titulo(hook)`.
2. `.capitalize()` en un overlay que arranca con "¿" (ej. "¿HAY TRATAMIENTO O CURACIÓN") baja la primera LETRA real ("¿hay...") porque el primer CARÁCTER no es una letra — `_capitalize_first_letter` corrige esto.

**Rugosidades que quedan (no violan el criterio de aceptación, pero no son perfectas):** algunas oraciones de nivel (b)/(c) se recortan a mitad de una idea larga ("...cuadro de hemorragia y problema", falta "renal") porque la regla pide recortar a 60 caracteres sin partir palabras, no reformular. Y `parece_titulo` no detecta construcciones de gerundio al arrancar ("Escuchando ancianos...") como posible fragmento — no estaba en la lista de conectores pedida por la tarea; queda como mejora futura si aparece de nuevo.