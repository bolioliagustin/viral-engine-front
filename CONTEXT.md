# viral-engine

Convierte un video de YouTube en momentos virales listos para publicar: clips verticales con subtítulos más el copy para redes. `viral-engine` es el nombre de trabajo; la marca definitiva está en discusión (la UI muestra "ViralEngine").

## Language

### Producto

**Job**:
El procesamiento de un video pedido por un usuario a partir de una fuente; produce de 1 a 5 momentos. Es exitoso si al menos un momento tiene clip; si ninguno lo tiene, falla y devuelve el crédito.
_Avoid_: video, proceso, trabajo, request

**Fuente**:
Origen del video de un job: un link de YouTube o un archivo de video subido por el usuario. Un archivo puede venir acompañado del link del mismo video en YouTube para obtener el transcript.
_Avoid_: input, origen, upload

**Candidato**:
Fragmento que la Pasada A propone como posible momento; solo los mejores según el Juez se convierten en momentos entregados.
_Avoid_: momento (para los que no se entregan), opción, propuesta

**Momento**:
Fragmento del video (15–60 s) que se entrega al usuario, con su hook, overlay, scores y piezas de copy.
_Avoid_: clip (cuando se habla del fragmento elegido y no del archivo), viral moment, resultado

**Clip**:
El archivo de video vertical 9:16 renderizado a partir de un momento, con subtítulos y overlay quemados.
_Avoid_: video, momento, MP4 (a secas)

**Pieza de copy**:
Texto listo para publicar generado para un momento: hilo de Twitter, post de LinkedIn o caption de TikTok.
_Avoid_: contenido, content result, copy (a secas, cuando se refiere a una pieza concreta)

**Hook**:
Frase gancho de 1–2 líneas que explica por qué el momento frena el scroll; alimenta las piezas de copy.
_Avoid_: título, headline

**Overlay**:
Texto de máximo 4 palabras en mayúsculas que se quema sobre el clip durante sus primeros segundos.
_Avoid_: título del clip, hook corto, viral_overlay

**Edición**:
Cambios que el usuario pide sobre un clip ya generado (overlay, estilo de subtítulos, corrección de palabras, recorte) y que producen un nuevo render de ese clip.
_Avoid_: re-render, regeneración, edit

**Score**:
Puntuación 1–10 de un momento en tres métricas: hook, retención y compartibilidad. El score que ve el usuario es el del Juez.
_Avoid_: rating, nota, viralidad (para la tercera métrica usar "compartibilidad")

**Posteable**:
Etiqueta que pone una persona a un clip cuando lo publicaría tal cual, sin editar. Es la fuente de verdad de calidad; el score del Juez es su aproximación automática.
_Avoid_: bueno, aprobado, viral, válido

### Pipeline de IA

**Transcript**:
Texto con timestamps del video completo. Sale de los subtítulos de YouTube (captions en bloques de 3–30 s) o del reconocimiento de voz sobre el audio completo (`TRANSCRIPT_SOURCE=whisper_full`: palabras con puntuación, silencios y líneas). Es el insumo de la selección de momentos.
_Avoid_: transcripción (reservado para el clip), subtítulos

**Línea**:
Oración del transcript con su tiempo de inicio y fin, tal como la recibe la Pasada A cuando el transcript viene del audio completo (`[mm:ss] Oración.`). Termina en . ? ! … o en una pausa larga; es la unidad con la que se proponen los límites de un momento.
_Avoid_: segmento (reservado para los bloques de Whisper/captions), frase (reservado para la Verificación), bloque

**Silencio**:
Hueco de al menos 0,3 s entre dos palabras del transcript, marcado como un token `__silence` con inicio y fin. Los consumidores de palabras (subtítulos, Verificación, guardas) lo ignoran.
_Avoid_: pausa (cuando se habla del token), gap

**Transcripción del clip**:
Texto palabra por palabra con timestamps obtenido por reconocimiento de voz sobre el audio del clip ya cortado. Es la fuente de verdad para subtítulos, copy y juez.
_Avoid_: transcript, whisper words

**Categoría**:
Tipo de contenido del video que decide la estrategia de selección: podcast (conversación entre 2+ personas) o business (todo lo demás).
_Avoid_: tipo, género, nicho

**Pasada A**:
Selección de momentos sobre el transcript completo: timestamps, hook conceptual, overlay borrador y scores preliminares. No genera copy.
_Avoid_: análisis, analysis

**Pasada B**:
Generación del copy definitivo (piezas de copy, hook y overlay finales) a partir de la transcripción del clip.
_Avoid_: copy generation, regeneración de copy

**Juez**:
Modelo independiente, de otra familia que el de las pasadas A y B, que puntúa el clip final contra una rúbrica fija.
_Avoid_: scorer, evaluador

**Verificación**:
Comprobación de que la primera y la última frase que la IA citó para un momento existen en la transcripción del clip (matching difuso); si alguna de las dos no se ancla, el corte queda marcado para revisar. Desde W2-C (docs/PLAN_CALIDAD.md §9) no incluye señales informativas como el hook tardío o la cola incompleta — esas quedan aparte en `clip_quality_issues`, porque el clip puede arrancar unas palabras antes del hook citado (misma oración) sin que la Verificación haya fallado.
_Avoid_: validación (reservado para reglas de duración y solapamiento), anti-alucinación

**Tono**:
Voz elegida por el usuario para las piezas de copy de un job: profesional, sarcástico, motivador o casual.
_Avoid_: estilo, voz de marca

### Cuenta y facturación

**Usuario**:
Persona autenticada que crea jobs. Puede tener un perfil de creador (nombre y título profesional) que se inyecta en el copy.
_Avoid_: cliente, cuenta, creador (como sinónimo de usuario)

**Crédito**:
Derecho a un job. Se reserva al crear el job y se devuelve si el job falla. Cada usuario nuevo recibe 5; el plan Starter renueva 40 por mes.
_Avoid_: token, saldo, uso

**Plan**:
Nivel de suscripción del usuario: free o starter. Determina los créditos mensuales.
_Avoid_: tier, suscripción (reservado para el contrato con el proveedor de pagos), membresía
