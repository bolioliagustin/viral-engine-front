# Análisis de la competencia: Opus Clip sobre el mismo video que nuestro golden set

**Fecha:** 18-sep-2026. **Fuentes:** carpeta `opus_test/` (fuera de git): captura HAR de `clip.opus.pro` (32 requests, 3 respuestas útiles de `api.opus.pro`), `responde.json` (los 42 clips con su análisis completo), captura de pantalla de la interfaz y el MP4 del clip #1 descargado en HD. **Video:** The Wild Project #373 — Alfredo Corell, hantavirus (`XxoVRjTySsM`, 77,3 min), que es exactamente `podcast_general_01` de nuestro golden set: permite comparar cabeza a cabeza con nuestra corrida `worker/eval/runs/2026-09-18-w1-cortes.json`.

**Condiciones de la prueba de Opus:** cuenta TRIAL, modelo `ClipBasic`, preferencias por defecto (duración 0–180 s, género "Auto", sin prompt), plantilla de marca `preset-fancy-Beasty`, marca de agua activa. Todo lo que sigue distingue entre lo **observado** en los datos y lo **inferido**.

---

## 0. Resumen en diez líneas

1. Opus entregó **42 clips** de un video de 77 min (cubren el 40 % del video en 35 tramos casi sin solaparse). Nosotros entregamos **5**.
2. Sus clips duran de 15 s a 132 s, **mediana 42 s**; **7 de sus 10 mejores duran más de 60 s**, que es nuestro tope duro. Su #1 dura 96,5 s.
3. **42 de 42 clips terminan en fin de oración** (con puntuación); 32 de 42 arrancan con mayúscula. Nosotros, después de W1: 58 % arrancan con mayúscula y 5 % pierden el remate.
4. El score que muestra (83–99) es una **curva sobre un juez de 4 dimensiones** (gancho, flujo, valor, tendencia; 32–37 sobre 40 en crudo) desempatada por ranking. Su juez casi no discrimina; **la presentación** es lo que vende.
5. El render es **layout dividido (dos caras apiladas)** a 1080×1920, 30 fps, 15 Mbps, con detección de caras, de paneles y de quién habla. Nosotros: fondo desenfocado a 720×1280.
6. Subtítulos de **1 a 5 palabras (mediana 2), 0,7 s por bloque**, mayúsculas, fuente cómic con borde grueso, palabra clave resaltada en color. Los nuestros: hasta 4 palabras por línea, sin resaltado.
7. Su transcript trae **puntuación, mayúsculas y marcadores explícitos de silencio** (`__silence`, con duración) por palabra. El nuestro (captions de Supadata) viene en bloques de 3–30 s sin puntuación: es la causa C1 del plan.
8. La vista es **preview en baja resolución** y el HD se descarga a pedido: así pueden permitirse 42 clips.
9. De nuestros 5 momentos, **solo 2 solapan con alguno de sus 42**, y ninguno con su top 20. Su #1 empieza 16 s después de donde nosotros cortamos nuestro m4.
10. Opus también se equivoca: transcribió "hantavirus" como "antivirus" en todo el video, su juez comenta mitad en inglés y mitad en español y da A a casi todo. No hay que copiarlo entero: hay que copiar las cinco cosas que el usuario percibe.

---

## 1. Qué ve el usuario (producto)

De la captura y de `responde.json`:

| Elemento | Opus | Nosotros hoy |
|---|---|---|
| Cantidad de clips | 42, ordenados por ranking | 5 (1/3/5 según plan) |
| Score visible | **99/100** con letras por dimensión: Gancho A · Flujo A · Valor A · Tendencia A | Tres anillos 1–10 del juez (hook/retención/share), promedio ~5 |
| Título | Sí, ≤ 60 caracteres, 37/42 con dos puntos, 21/42 con ¿¡ ("Sarampión vs COVID: ¡La Verdad de la Inmunidad de Grupo!") | No hay título por clip |
| Descripción | 2 oraciones ("Compara la contagiosidad… Descubre cómo…") | 3 piezas de copy (tweet, post, caption) |
| Hashtags | 10 por clip | Dentro del caption |
| Transcript del clip | Visible con rango de tiempo `[17:10–17:42]` | No visible |
| Preview | Baja resolución, con marca "Created with Opus" | El MP4 final |
| Acciones | Publicar en redes · Exportar XML (Premiere/DaVinci) · Descargar HD · Mejorar calidad (117 créditos) · Editar · Herramientas de IA · 9:16 · Duplicar | Descargar · Compartir · Editar |
| Retención | 7 días (`storageExpireAt`), igual que nuestro ADR 0007 | 7 días (decidido) |
| Aviso al terminar | Email | Notificación del navegador |
| Sugerencias | 10 prompts de búsqueda ("find the discussion on the R-zero…") para curar por tema | No |

---

## 2. Cómo lo hace (pipeline reconstruido a partir de los datos)

### 2.1 Ingesta y transcript (observado)

- Descarga el video y lo **normaliza a 1920×1080** (`video.resize.mp4`) más `audio.resize.wav` y `.m4a`.
- Transcript **por palabra con puntuación y mayúsculas** y con **tokens `__silence`** que llevan su propia duración (desde 0,07 s; mediana 0,56 s; el 15 % del tiempo de los clips es silencio marcado). Ejemplo: `"La __silence enfermedad más contagiosa que se conoce en la historia hasta el momento es el sarampión."` con `tr` por palabra.
- Calcula `wordsPerMinute` (190 en este video) y detecta género en tres niveles: `educationalOrInformational / general / podcast`.
- Publica un `transcriptTxtUrl` descargable.

### 2.2 Curación: candidatos (observado + inferido)

- Corren **tres arquitecturas en paralelo** y mezclan: `RAW` (27 clips, duración esperada 15–120 s), `HPv2` (14 clips, 0–180 s) y `TPv3` (1 clip). `autoModelSwitchState: "2"` sugiere que cambian de modelo automáticamente si uno falla o rinde poco.
- Cada clip es un **"screenplay"**: capítulos → líneas (oraciones completas, tipo `verbal`) → palabras con tiempos. Media de 2 capítulos y 6,3 líneas por clip. Los límites del clip son límites de línea: por eso el 100 % termina en puntuación.
- Los 42 clips cubren el 40 % del video (31 min) en 35 tramos; solo 3 pares se solapan más de 1 s → **deduplican por solapamiento**.
- 5 clips tienen dos rangos de tiempo, pero son contiguos (gap −20 ms): no hay cortes internos ("jump cuts"); son fronteras de escena.
- `defects: []` en todos: tienen una etapa de detección de defectos (inferido: audio, encuadre, etc.).
- `sponsorshipScore: 0` en todos: el juez detecta tramos patrocinados para no clipearlos.

### 2.3 Juez (observado)

`judgeResult` por clip:

| Campo | Rango visto | Qué mide (por sus comentarios) |
|---|---|---|
| `hookScore` | 7–9 | Si las primeras palabras enganchan; sugiere cómo mejorarlo |
| `coherenceScore` ("Flujo") | 8–9 | Si la explicación se sigue sola; transiciones |
| `connectionScore` ("Valor") | 7–10 | Valor educativo/emocional para el espectador |
| `trendScore` ("Tendencia") | 7–9 | Actualidad del tema |
| `sponsorshipScore` | 0 | Contenido patrocinado |
| `score` (crudo) | **32–37 de 40** | Suma de las cuatro |
| `curvedScore` (visible) | **83–99** | Curva del crudo, desempate por `rank` (mismo crudo 36 → 98, 97, 96… según ranking) |

Cada dimensión trae un comentario **constructivo** ("…immediately linking this to the current virus's R0 would provide context"). Los comentarios salen 97 de 168 en inglés y el resto en español: no los pulen. El juez es generoso por diseño: un crudo de 32–37 sobre 40 es 80–92 %, y aun así lo curvan hacia arriba. **La discriminación real la hace el ranking, no el número.**

### 2.4 Copy (observado)

Por clip: `title`, `description` (2 oraciones), `hashtags` (10) y **`autoHook.textOverlay.hookText`**: un cartel de ≤ 6 palabras ("El COVID alcanzó al sarampión") que se muestra los primeros 5 s en una caja blanca con texto negro (Montserrat 700, 40 px, esquinas redondeadas), centrada al 75 % del ancho, sobre la mitad inferior. Es lo mismo que nuestro Overlay, con estilo distinto y 1,5 s más.

### 2.5 Render (observado en `editingScript`)

Modelos declarados en `modelVersions`:

| Componente | Modelo | Para qué |
|---|---|---|
| `asd` | **TalkNet** (`talknet/talknet-base`) | Detección de quién está hablando (active speaker) |
| `objectDetect` | YOLOX | Personas y objetos |
| `panelDetect` | YOLOX-s graphics | Paneles de pantalla compartida / entrevistas remotas |
| `trackingBackend` | FEARTracker | Seguir la cara entre frames |
| `shotDetectBackend` | **PySceneDetect** | Cortes de cámara |
| `faceDetectFps` | 15 | Cadencia de detección |

Por cada keyframe guardan `analysisResult`: caras con `faceness`, `activeness` (score de hablante activo) e `isFake` (descarta caras de fotos/miniaturas: `reduceFakeFace`), personas con `peopleness`, `panels` con confianza 0,98 (el video es una videollamada a dos paneles), `activeSpeaker`, `movingSpeaker`, `docLayoutRegions`.

Con eso eligen un `layoutType` entre `Fill`, `Fit` (4:3 con fondo desenfocado), **`Split`** (dos caras apiladas: el caso de este video), `Three`, `Four`, `Screen` (pantalla compartida + cara). Cada layout es una lista de `cropAreas` en porcentajes sobre el frame original.

Pistas del editor: `KeyFrameTrack` (encuadre), `CaptionTrack` (128 bloques para 96 s), `EmojiTrack` (4 emojis sugeridos, desactivados por preferencia) y `TextOverlayTrack` (el hook).

**Subtítulos** (`CaptionTrack` del clip #1): 1–5 palabras por bloque (mediana 2, media 2,4), 0,73 s por bloque de media, `captionStyle: one-line`, posición `auto` (en la costura entre los dos paneles), animación `pop`, **mayúsculas**, fuente Komika Axis 50 px, blanco con borde negro de 16 px y sombra, resaltado de palabras clave en verde `#04f827` / amarillo `#FFFD03` (color 1/2 por palabra). Los bloques de silencio no muestran texto.

**Salida:** H.264 1080×1920 a 29,97 fps y 15,4 Mbps, AAC 260 kbps 48 kHz estéreo. Nuestro render: 720×1280, `crf 23`, AAC 128 kbps.

---

## 3. Cabeza a cabeza sobre el mismo video

Nuestra corrida W1 (`podcast_general_01`, 5 momentos, juez propio 4–7) contra los 42 de Opus:

| Nuestro momento | Rango | Juez nuestro (h/r/s) | Lo que dijo nuestro juez | Solapa con Opus |
|---|---|---|---|---|
| m1 "El peligro al barrer" | 224–254 s | 6/5/6 | "el audio entra tarde… muletillas… remate no contundente" | #24 (218–269 s, score 91) |
| m2 "Inunda tus pulmones" | 405–443 s | 6/5/6 | "tarda en llegar… repeticiones… remate confuso" | ninguno |
| m3 "El virus más peligroso" | 575–609 s | 6/4/5 | "frases mal resueltas… relleno" | ninguno |
| m4 "Pandemia probabilidad cero" | 981–1014 s | 7/4/6 | "arranque desordenado… sin remate limpio" | #27 (998–1030 s, score 90) |
| m5 "Pantallas que te enferman" | 4317–4350 s | 7/6/6 | "relleno/pausas… no cierra" | ninguno |

Lecturas:

- **Ninguno de nuestros 5 está entre sus 20 mejores.** Los dos que coinciden son sus #24 y #27.
- Su **#1** (1030–1127 s, "La enfermedad más contagiosa que se conoce en la historia hasta el momento es el sarampión…") **empieza 16 s después de donde termina nuestro m4** (1014 s). Nosotros elegimos la introducción del argumento (R0) y cortamos a 33 s; Opus tomó el argumento completo (sarampión → COVID Wuhan → Delta → Ómicron → inmunidad de grupo) en 96 s. Con nuestro tope de 60 s ese clip es imposible.
- Las quejas de nuestro juez ("entra tarde", "remate no cierra", "muletillas") describen exactamente lo que Opus evita cortando por líneas de un transcript puntuado. Las muletillas Opus **no** las quita (hay 8 "bueno," y 4 "o sea" en sus textos): elige tramos donde molestan menos.

---

## 4. Dónde está la diferencia, ordenada por lo que el usuario percibe

1. **Cantidad y presentación del score.** 42 clips con 83–99 y letras A, contra 5 clips con 4–6 sobre 10. Aunque nuestros clips fueran iguales, la percepción es opuesta. Su juez es tan poco discriminante como el nuestro (32–37 sobre 40, todo A); la diferencia es que **muestran un ranking relativo curvado**, no una nota absoluta.
2. **Duración.** Tope de 180 s, mediana 42 s, 7 del top 10 por encima de 60 s. Nuestro 10–60 s recorta las ideas completas de un podcast; el propio juez nos lo dice cuando se queja del remate.
3. **Encuadre.** Dos caras apiladas a pantalla completa, con seguimiento, contra un plano ancho sobre fondo desenfocado. Es la diferencia visual más obvia en la captura.
4. **Límites del clip.** 100 % en fin de oración gracias a un transcript puntuado por palabra. W1 nos acercó (remate perdido 45 % → 5 %) pero seguimos en 58 % de arranques con mayúscula porque el transcript base no tiene puntuación.
5. **Subtítulos.** 2 palabras por bloque, mayúsculas, borde grueso, palabra clave en color. Los nuestros son legibles pero "de generador".
6. **Copy por clip.** Título + descripción + hashtags es lo que la persona pega en YouTube Shorts/TikTok. Nuestras tres piezas (tweet/post/caption) son otra cosa; falta el título.
7. **Calidad de salida.** 1080p a 15 Mbps contra 720p. Para la beta 720p está decidido; pero el HD a pedido es un upsell natural (Opus cobra créditos por "Mejorar calidad").
8. **Preview + HD a pedido.** Es lo que hace viable entregar 42 clips: no renderizan 42 HD.
9. Cosas menores que suman: transcript visible por clip con rango de tiempo, exportar XML, emojis sugeridos, prompts de búsqueda por tema, detección de patrocinio, email al terminar.

---

## 5. Lo que NO conviene copiar

- **Su juez "A para todos".** Es marketing. Nosotros necesitamos el juez para *elegir* (W2); lo que sí conviene copiar es la **capa de presentación** (curva + letras + comentario constructivo), separada del score que usamos para rankear.
- **Comentarios en dos idiomas y ASR que confunde "hantavirus" con "antivirus".** No es el estándar.
- **TalkNet + YOLOX + FEARTracker en un VPS de 4 GB.** El stack de encuadre de Opus es pesado; el 80 % del efecto en podcasts a dos paneles se consigue con detección de paneles/caras estática por escena (PySceneDetect + un detector de caras liviano), no con seguimiento cuadro a cuadro.

---

## 6. Qué cambia en nuestro plan (propuesta, ver decisiones en PLAN_CALIDAD §7 y §8)

Ordenado por relación impacto/costo para el ICP (podcasters y coaches en español):

| # | Cambio | Línea del plan | Costo estimado | Efecto que se ve |
|---|---|---|---|---|
| A | **Tope de duración 60 → 120 s** (`validate_durations`, Pasada A, W1 `max_s`) y objetivo de duración por género (podcast: 30–90 s) | W1/W2 | horas | Deja de perderse la idea completa; habilita clips como el #1 de Opus |
| B | **Muchos clips, ranking relativo**: entregar todos los candidatos viables (objetivo ≥ 1 clip cada 2–3 min de video), mostrar percentil curvado + letras + comentario del juez; preview liviano, HD a pedido | nuevo **W9** | 1–2 semanas (worker + front); costo por job sube con el número de clips salvo preview a baja resolución | La sensación de "me dio 30 clips buenos" en vez de "me dio 5 regulares" |
| C | **Transcript puntuado por palabra con silencios** (Whisper del audio completo, `verbose_json` + puntuación; tokens de silencio ≥ 0,3 s) | W4 (sube de prioridad: es la base de A, D y E) | 3–5 días; Groq US$0.04/h | Cortes al 100 % en fin de oración; base para "quitar silencios" |
| D | **Encuadre por paneles/caras** (Split para videollamadas, Fill con cara centrada para un solo hablante, Fit con desenfoque como fallback) | W5 (con alcance acotado: sin seguimiento cuadro a cuadro en la primera versión) | 1–2 semanas | La diferencia visual más grande de la captura |
| E | **Subtítulos estilo corto**: 1–3 palabras, mayúsculas, borde 12–16 px, palabra clave resaltada (elige el modelo: 1–2 por línea) | clip_generator (estilo `tiktok_viral` v2) | 1–2 días | "Se ve como los que veo en TikTok" |
| F | **Título + descripción + hashtags por clip** (además de las piezas actuales) y transcript visible con rango | W6 + front | 1–2 días | Lo que la persona pega al publicar |
| G | **Capa de presentación del score**: curva a 60–99 sobre el ranking del juez, letras por dimensión, comentario constructivo en español | W7/W8 | 1 día | Percepción; no toca el ranking |
| H | Hook overlay: caja blanca/negra 5 s en vez de mayúsculas 3,5 s; validar contra el texto (ya lo hace W6) | W6 | horas | Menor |
| I | Detección de tramos patrocinados y "quitar silencios" como opción del editor | fase 2 | días | Menor para el ICP de la beta |

Lo que **no** cambia: la rueda de medición (W0), las guardas (W3), el ancla a frases (W1) y la etiqueta humana (W7) siguen siendo la base; Opus confirma que la elección del momento y el corte en oraciones completas son el núcleo.

---

## 7. Costos de reproducir lo esencial (estimación gruesa)

- **A + C**: Whisper full por Groq: 77 min ≈ US$0.05; sin cambio de infraestructura. El tope de 120 s sube el tamaño de descarga por clip (~2×) y el tiempo de render (~2×): ~1 min más por job.
- **B**: renderizar 30 previews a 480×854 con `veryfast` en el VPS ≈ 30 × 4 s = 2 min; HD solo de los que la persona descarga. Costo LLM de Pasada B para 30 clips ≈ US$0.20 → conviene generar copy solo al abrir/descargar el clip (lazy), como hace Opus con el HD.
- **D**: PySceneDetect + detección de caras con un modelo liviano (p. ej. YuNet de OpenCV, CPU) a 2 fps en el segmento del clip: ~5–10 s por clip en CPU. Entra en el VPS actual.
- **E, F, G, H**: sin costo de infraestructura.

---

## Apéndice — Cómo se extrajo

Scripts de una sesión de Claude Code sobre `opus_test/` (no versionados; la carpeta contiene datos de una cuenta personal y un MP4 de 188 MB, por eso queda fuera de git). El `engine-clips` del HAR viene en base64; el resto es JSON plano. Frames del MP4 extraídos con `ffmpeg-full` a 0,5/2/6/20/45/70/95 s para leer el layout y los subtítulos.
