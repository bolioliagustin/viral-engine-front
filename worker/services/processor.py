import os
import json
import base64
from openai import OpenAI
from pathlib import Path
from typing import Optional
from models.schemas import AnalysisResult


def get_video_category(video_info: dict, client=None) -> str:
    """
    Meta-Classifier: Binary classifier — podcast vs business.

    Phase 1.4: Reduced from 5 categories to 2 (the only ones with professionally
    tuned prompts). Anything that isn't clearly a podcast/interview falls back
    to 'business' (broader prompt that handles monologues, keynotes, talks,
    tutorials and general content reasonably).

    Args:
        video_info: Dict with 'title' and optionally 'description'
        client: OpenAI client for OpenRouter (reuses existing connection)

    Returns:
        Category string: 'podcast' or 'business'
    """
    title = video_info.get('title', '')
    description = video_info.get('description', '')[:300]

    # Keyword fallback (used if no client or LLM fails)
    def _keyword_classify() -> str:
        text = (title + ' ' + description).lower()
        podcast_keywords = [
            'podcast', 'entrevista', 'interview', 'conversación', 'conversation',
            'charla con', 'episodio', 'episode', 'invitado', 'invitada',
            'guest', 'host', 'capítulo', 'mesa redonda',
        ]
        if any(k in text for k in podcast_keywords):
            return 'podcast'
        return 'business'

    if not client:
        return _keyword_classify()

    # LLM classification — Gemini Flash with longer timeout for reliability
    try:
        model = os.getenv("OPENROUTER_CLASSIFIER_MODEL", "google/gemini-2.0-flash-001")

        classification_prompt = f"""Clasifica este video en UNA de DOS categorías:

Título: {title}
Descripción: {description}

Categorías:
- podcast: Entrevistas, conversaciones entre 2+ personas, episodios de podcast, mesas redondas, charlas con invitados.
- business: TODO lO DEMÁS — monólogos, keynotes, talks, tutoriales, contenido de un solo orador, vlogs, contenido educativo, comedia, deportes, lifestyle, motivacional, tech.

REGLA: Si NO es claramente una conversación entre 2+ personas, es 'business'.

Responde con UNA SOLA PALABRA: podcast o business."""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Clasificador binario. Respondes con una sola palabra: podcast o business."},
                {"role": "user", "content": classification_prompt}
            ],
            max_tokens=5,
            temperature=0,
            timeout=10,  # Phase 1.4: bumped from 3s — was failing too often
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM devolvió respuesta vacía")
        category = content.strip().lower()

        if category in ('podcast', 'business'):
            return category
        print(f"⚠️ Categoría inválida del LLM ('{category}'), usando fallback de keywords")
        return _keyword_classify()

    except Exception as e:
        print(f"⚠️ LLM classification falló ({str(e)[:60]}), usando keyword fallback")
        return _keyword_classify()



def get_dynamic_prompt(duration: int, tone: str = "profesional", category: str = "business", transcript: dict = None, user_name: str = "Creador", user_title: str = "Experto") -> str:
    """
    Generate dynamic prompt based on video duration, tone, and category.
    
    Args:
        duration: Video duration in seconds
        tone: Content tone/style
        category: Content category (business, entertainment, tech, lifestyle)
        transcript: Optional transcript data (not used in prompt directly, passed for context)
        user_name: User's name for personalization
        user_title: User's professional title
    """
    # Dynamic density based on duration
    if duration < 90:
        moments_instruction = "Identifica EL MEJOR momento viral del video (solo 1, el más potente)."
        num_moments = 1
    elif duration < 300:
        moments_instruction = "Identifica los 3 mejores momentos virales del video."
        num_moments = 3
    else:
        moments_instruction = "Identifica los 5 mejores momentos virales del video."
        num_moments = 5
    
    # Tone mapping
    tone_instructions = {
        "profesional": "Usa un tono profesional, directo y con autoridad.",
        "sarcastico": "Usa un tono sarcástico e irónico, con humor inteligente.",
        "motivador": "Usa un tono motivador y energético, que inspire acción.",
        "casual": "Usa un tono casual y cercano, como hablando con un amigo."
    }
    tone_style = tone_instructions.get(tone.lower(), tone_instructions["profesional"])
    
    # Phase 1.4: Binary routing — podcast OR business (default).
    # Other category values (entertainment/tech/lifestyle) are legacy and now
    # all fall through to the business prompt, which handles general content
    # better than the half-finished alternatives. Entertainment-specific
    # prompt is parked in category_prompts.py for a possible future reactivation.
    if category == 'podcast':
        print(f"🎙️ Using PODCAST strategy")
        import services.podcast_prompt as pp
        try:
            return pp.get_podcast_prompt(duration, num_moments, moments_instruction, tone_style, user_name, user_title)
        except Exception as e:
            print(f"⚠️ Podcast prompt import failed, falling back to business: {e}")

    print(f"💼 Using BUSINESS strategy (category={category})")
    
    return f"""Actúa como un Director de Contenido Viral con 15 años de experiencia en psicología de masas y algoritmos de redes sociales.

MISIÓN CRÍTICA:
Crear contenido diseñado para **Dwell Time** (tiempo de permanencia). Los algoritmos de 2025 priorizan contenido que mantiene al usuario leyendo. Contenido corto = scroll rápido = muerte algorítmica.

CONTEXTO DEL CREADOR:
- Nombre: {user_name}
- Título/Profesión: {user_title}
- Voz de marca: {tone_style}

ANÁLISIS DE AUDIO:
{moments_instruction}
Cada momento debe tener máximo 60 segundos de duración.

Prioriza momentos con:
1. **Contrarian Truths**: Ideas que rompen creencias comunes
2. **High Utility**: Valor accionable inmediato
3. **Deep Vulnerability**: Admisión de errores humanos
4. **The Curiosity Gap**: Declaraciones que abren loops mentales

═══════════════════════════════════════
🐦 TWITTER THREAD - ESTRUCTURA VIRAL REAL
═══════════════════════════════════════

**REGLA DE ORO**: Un hilo viral NO resume el video. Lo CONVIERTE en una historia
que el lector quiere terminar aunque no conozca al creador.

**CRÍTICO**: Cada tweet va separado por \\n\\n. SIN prefijos "Tweet 1:", "Tweet 2:".
SIN placeholders como [Link]. Cada tweet debe funcionar SOLO si se ve fuera del hilo.

**Estructura Obligatoria de 7 Tweets:**

📌 **Tweet 1 - HOOK QUE DETIENE EL SCROLL** (240-260 chars)
- Afirmación contraintuitiva o dato que rompe una creencia
- Sin contexto previo — funciona completamente solo
- Sin hashtags, sin emojis de relleno
- El lector debe pensar "espera, ¿esto es real?" antes de seguir
- Ejemplo: "Pasé 3 años aplicando lo que todos dicen sobre productividad. Me hizo más lento. Lo que nadie te cuenta es que el problema está en la pregunta, no en la respuesta."

🔥 **Tweet 2 - EL DOLOR ESPECÍFICO** (230-260 chars)
- Expón el problema con detalle real, no genérico
- Usa el lenguaje exacto del audio cuando puedas
- Genera urgencia: ¿por qué esto importa AHORA?
- Ejemplo: "La mayoría optimiza su agenda. Yo optimicé 4 años mi agenda. Resultado: hacía más cosas, pero ninguna de las que importaban. El sistema me estaba usando a mí, no al revés."

📊 **Tweet 3 - EL GIRO QUE NADIE VE VENIR** (230-260 chars)
- La insight central del clip — la idea que cambia el marco
- Saca la idea del audio y añade tu perspectiva
- Ejemplo: "El error no es falta de disciplina. Es que optimizamos la ejecución de tareas equivocadas. Como pulir los zapatos antes de una carrera descalzo."

💡 **Tweet 4 - LA PRUEBA O EL MECANISMO** (220-260 chars)
- ¿Por qué funciona esto? Mecanismo, dato o caso concreto
- Si hay números en el audio, úsalos
- Si no, usa una analogía concreta y visual
- Ejemplo: "El sistema límbico procesa información 80.000 veces más rápido que la corteza prefrontal. Tu 'instinto' no es emoción — es procesamiento paralelo masivo que la razón no puede replicar."

🔍 **Tweet 5 - LA APLICACIÓN PRÁCTICA** (220-250 chars)
- ¿Qué hace diferente quien entiende esto?
- Algo accionable, no vago
- Ejemplo: "Lo que cambió todo: antes de planificar mi semana, ahora me hago una sola pregunta: ¿Cuál es la única tarea que haría que todo lo demás sea más fácil o irrelevante? Solo una."

⚡ **Tweet 6 - EL REMATE QUE ESCALA** (220-250 chars)
- Eleva la idea a algo más grande, más universal
- Conecta con algo que el lector ya siente pero nunca articuló
- Ejemplo: "No es productividad. Es claridad. La diferencia entre el que trabaja 12 horas y el que trabaja 4 es que el segundo sabe exactamente qué no tiene que hacer."

✅ **Tweet 7 - CTA CON LOOP MENTAL** (180-220 chars)
- Pregunta abierta que el lector responde con su propia vida
- NO "sígueme", NO "dale RT", NO "comenta abajo"
- La pregunta debe generar incomodidad productiva
- Ejemplo: "¿Cuántas horas por semana dedicas a optimizar tareas que deberías haber eliminado?"

═══════════════════════════════════════
💼 LINKEDIN POST - THOUGHT LEADERSHIP
═══════════════════════════════════════

ESTRUCTURA ESTRATÉGICA:

**Hook (Primeras 3 líneas - antes del "ver más"):**
Usa A.I.D.A.:
- **Atención**: Dato sorprendente o pregunta provocativa
- **Interés**: Promesa de valor claro
- **Deseo**: Insinúa transformación

Ejemplo:
```
He analizado 1,200 perfiles de LinkedIn en mi industria.
El 83% está cometiendo el mismo error.
Y les está costando oportunidades reales.
```

**Cuerpo (Formato Deep-Dive):**
- Párrafos CORTOS (máx 2-3 líneas cada uno)
- Usa saltos de línea estratégicos para respirar
- Incluye bullets (•) o números (1., 2., 3.) para facilitar escaneo
- Integra micro-historias o casos específicos del audio
- Longitud ideal: 800-1200 caracteres

**Cierre (Engagement Loop):**
- Pregunta abierta que invite comentarios
- NO pidas "likes" directamente
- Ejemplo: "¿Cuál de estos errores reconoces en tu perfil? Cuéntame en comentarios."

═══════════════════════════════════════
🎬 SHORT VIDEO SCRIPT - MULTIMODAL STORYTELLING
═══════════════════════════════════════

**FASE C: INTELIGENCIA VISUAL CONTEXTUAL**

Analiza el AUDIO para detectar:
1. **Palabras de énfasis** (repetidas, dichas más fuerte)
2. **Pausas significativas** (silencios dramáticos)
3. **Temas visualizables** (conceptos que piden gráficos)

**BIBLIOTECA DE B-ROLL POR TEMA:**

Si el audio menciona...
• **Dinero/Finanzas** → Billetes quemándose, gráficos subiendo/bajando, calculadora, wallet, cripto
• **Tiempo** → Reloj acelerado, calendario pasando páginas, arena cayendo
• **Crecimiento/Éxito** → Plantas creciendo time-lapse, cohete despegando, gráfico exponencial
• **Fracaso/Error** → Documentos rojos, X gigante, persona frustrada, edificio colapsando
• **Comparación** → Split screen, VS animado, balanza desequilibrada
• **Transformación** → Before/after, metamorfosis, upgrade visual

**MEMES Y REFERENCIAS CULTURALES:**

Según el tema detectado, sugiere:
• **Finanzas** → "Stonks" meme, "This is fine" dog, Breaking Bad money pile
• **Productividad** → Drake pointing meme, distracted boyfriend, galaxy brain
• **Decisiones** → Two buttons meme, expanding brain, trade offer
• **Sorpresa** → Surprised Pikachu, "Wait, what?", mind blown gif

**ESTRUCTURA DETALLADA:**

**[0-3s] HOOK VISUAL + VERBAL**
- Acción visual IMPACTANTE (no genérica)
- Conecta el visual con palabra clave del audio
- Ejemplo específico:
  ```
  [VISUAL: Primer plano de mano estirando billete nuevo sin romperlo, luego ZOOM OUT 
   revelando que el billete se está desvaneciendo como humo desde los bordes]
  [AUDIO: Identifica la frase exacta más impactante del clip]
  [TIMING: 0-3s]
  [EFECTO: Desvanecimiento gradual con partículas]
  ```

**[3-50s] DESARROLLO CON MICRO-SEGMENTOS**

Divide en segmentos de 10-15s cada uno:

**Segmento 1 [3-15s]:**
- VISUAL PRINCIPAL: [Describe escena específica]
- B-ROLL SUGERIDO: [Insertos complementarios]
- TEXTO EN PANTALLA: [Keyword o stat]
- TRANSICIÓN: [Tipo de corte/efecto]
- ÉNFASIS DETECTADO: [Si hay palabra repetida o pausa, marcala]

**Segmento 2 [15-30s]:**
- VISUAL PRINCIPAL: [Nueva escena]
- MEME/REFERENCIA: [Si aplica, meme específico]
- GRÁFICO SUGERIDO: [Si hay datos, tipo de visualización]
- RITMO: [Mantener/Acelerar/Ralentizar]

**Segmento 3 [30-50s]:**
- VISUAL PRINCIPAL: [Clímax visual]
- LLAMADA DE ATENCIÓN: [Hook secondary]
- PREPARACIÓN PARA CIERRE: [Setup del loop]

**[50-60s] CTA + LOOP CIRCULAR**
- **Visual que regresa al inicio** (circularidad)
- **Frase gancho final**
- **Llamado a acción visual** (sutil, no "sígueme")
- Ejemplo:
  ```
  [VISUAL: Volver al billete del inicio, pero ahora con overlay de "valor real" bajando]
  [AUDIO: "La próxima vez que tengas un billete 'indestructible' en la mano..."]
  [JUMP CUT: Al billete quemándose/desvaneciéndose]
  [AUDIO: "...recuerda esto."]
  [END CARD: Logo/handle con animación sutil]
  ```

**REGLAS CRÍTICAS PARA VISUAL STORYTELLING:**

✅ **Especificidad sobre generalidad**: No digas "persona pensando", di "hombre de 30s con expresión confundida mirando pantalla de celular"
✅ **Continuidad visual**: Los elementos del hook deben reaparecer en el cierre
✅ **1 idea = 1 visual**: No cambies el visual si la idea no cambió
✅ **Contraste de ritmo**: Alterna escenas rápidas (3-5s) con lentas (8-10s)
✅ **Texto en pantalla estratégico**: Números, stats o keywords clave (no todo)
✅ **Sonido diegético**: Sugiere sonidos que refuercen (ej: "sonido de billete rasgándose")

**DETECCIÓN DE ÉNFASIS (Analizar el audio):**

Identifica y marca:
• Palabras repetidas 2+ veces → **[ÉNFASIS]**
• Pausas de 2+ segundos → **[PAUSA DRAMÁTICA]** 
• Cambios de tono → **[TONO CAMBIA]**
• Velocidad de habla aumenta → **[ACELERA]**

En esos momentos, el visual debe REFORZAR con:
- Zoom in/out
- Freeze frame momentáneo
- Cambio de color/saturación
- Texto en pantalla que aparece

═══════════════════════════════════════
⚙️ REGLAS TÉCNICAS OBLIGATORIAS
═══════════════════════════════════════

✅ **Responde SOLO en JSON válido**
✅ **Tiempos en segundos enteros**
✅ **{tone_style}**
✅ **NUNCA valores null o vacíos - CREA contenido si no existe**
✅ **UN SOLO pillar_type por momento: "authority" | "utility" | "connection" | "entertainment"**
✅ **Twitter: 7 tweets separados por \\n\\n, SIN prefijos "Tweet 1:", SIN "[Link]"**
✅ **Twitter: cada tweet funciona SOLO fuera del hilo**
✅ **LinkedIn debe tener 800-1200 caracteres totales**

FORMATO JSON DE SALIDA:
{{
  "video_title": "Título magnético del video",
  "summary": "Resumen ejecutivo del valor (max 200 chars)",
  "main_topics": ["tema1", "tema2", "tema3"],
  "viral_moments": [
    {{
      "start_time": 0,
      "end_time": 60,
      "hook": "Frase gancho específica (OBLIGATORIO, forma larga para thread/post)",
      "viral_overlay": "HOOK CORTO MAX 4 PALABRAS UPPERCASE (se quema sobre el clip vertical, debe frenar scroll en <1s. Ej: 'NADIE TE DICE ESTO', 'EL ERROR #1', '92% FALLAN ACÁ', 'SIN INVERTIR UN PESO'. NO resumir el clip, generar un gancho tipo cartel de TikTok)",
      "emotional_trigger": "Curiosidad | Miedo | Codicia | Altruismo",
      "pillar_type": "authority",
      "category": "business",
      "sentiment_detected": "serious",
      "roi_time_saved": 45,
      "scores": {{
        "hook": 8,
        "retention": 7,
        "shareability": 9
      }},
      "score_justifications": [
        {{
          "metric": "hook",
          "score": 8,
          "reasoning": "El hook ataca directamente un miedo financiero común (pérdida de dinero), lo cual genera pausa inmediata en el scroll. Usa lenguaje específico en lugar de vago.",
          "improvement_tip": "Para llegar a 9/10, añadir un dato numérico impactante en la primera línea"
        }},
        {{
          "metric": "retention",
          "score": 7,
          "reasoning": "La estructura mantiene interés pero podría beneficiarse de más 'curiosity gaps' en la mitad del contenido",
          "improvement_tip": "Añadir una pregunta provocativa al 50% del video para re-enganchar"
        }},
        {{
          "metric": "shareability",
          "score": 9,
          "reasoning": "El contenido es altamente compartible porque ofrece valor práctico que hace quedar bien al compartidor. Tiene potencial de 'guardar para después'",
          "improvement_tip": "Perfecto nivel - mantener este enfoque de utilidad clara"
        }}
      ],
      "verification": {{
        "first_phrase_in_audio": "[Primeras 5-8 palabras exactas del clip]",
        "last_phrase_in_audio": "[Últimas 5-8 palabras exactas del clip]",
        "narrative_goal": "[Por qué este fragmento es una idea completa sin contexto previo]"
      }},
      "content_pieces": {{
        "twitter_thread": "[Hook contraintuitivo 240-260 chars]\\n\\n[El dolor específico 230-260 chars]\\n\\n[El giro que nadie ve venir 230-260 chars]\\n\\n[La prueba o mecanismo 220-260 chars]\\n\\n[La aplicación práctica 220-250 chars]\\n\\n[El remate que escala 220-250 chars]\\n\\n[CTA con loop mental 180-220 chars]",
        "linkedin_post": "[Hook 3 líneas]\\n\\n[Cuerpo 800-1200 chars con white space]\\n\\n[Pregunta engagement]",
        "short_video_script": "[0-3s] VISUAL: ... | AUDIO: ...\\n\\n[3-15s] VISUAL: ... | AUDIO: ...\\n\\n[continuar hasta 60s]"
      }}
    }}
  ],
  "overall_virality_score": 8,
  "total_roi_minutes": 135
}}

📊 CÁLCULO DE ROI (Tiempo Ahorrado):
Para cada momento, estima cuántos minutos un humano tardaría en:
1. Escuchar y transcribir el clip (10-15 min)
2. Idear hooks y estructura (15-20 min)
3. Redactar contenido para Twitter (15 min)
4. Adaptar para LinkedIn (10 min)
5. Crear guion de video (10-15 min)
TOTAL PROMEDIO: 60-75 minutos por momento

🎭 DETECCIÓN DE SENTIMIENTO:
Analiza el tono del audio y asigna:
- "sarcastic": Usa ironía o humor crítico
- "serious": Tono profesional y directo
- "motivational": Energético e inspirador
- "casual": Conversacional y relajado

Asegúrate que el contenido generado MANTENGA ese sentimiento.

🎯 RECORDATORIO FINAL: Genera exactamente {num_moments} momento(s) viral(es).
El objetivo NO es resumir, es EXPANDIR el contenido para maximizar tiempo de lectura y engagement."""


def analyze_with_openrouter(transcript: dict, video_info: dict, tone: str = "profesional") -> Optional[AnalysisResult]:
    """
    Analyze transcript with OpenRouter for viral moment detection.
    Uses Whisper transcript with precise timestamps for deterministic results.
    
    Args:
        transcript: Whisper transcript with segments and timestamps
        video_info: Video metadata
        tone: Voice tone for content generation
        
    Returns:
        AnalysisResult with viral moments and content
    """
    # Configure OpenRouter client
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY")
    )

    # Get model from env or use default
    model = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")

    # ── Cache lookup: evita re-llamar al modelo si ya analizamos ────────────
    # Si reprocesamos el mismo video con la misma config, devolvemos el
    # AnalysisResult guardado.
    video_id = video_info.get("id")
    from services.analysis_cache import (
        get_cached_analysis, save_analysis,
        get_cached_category, save_category,
    )
    if video_id:
        cached = get_cached_analysis(video_id, model, tone)
        if cached:
            try:
                return AnalysisResult(**cached)
            except Exception as e:
                # Cache row corrupto/desactualizado — seguir y re-analizar
                print(f"   ⚠️ Cached analysis no valida ({e}), re-analizando")

    # PHASE 0: META-CLASSIFIER - Detect content category (con cache)
    category = None
    if video_id:
        category = get_cached_category(video_id, model)
        if category:
            print(f"✅ Category cached: {category.upper()}")
    if not category:
        print(f"📂 Detecting content category...")
        category = get_video_category(video_info, client)
        print(f"✅ Category detected: {category.upper()}")
        if video_id:
            save_category(video_id, model, category)

    # Format transcript: usar formato compacto (50% menos tokens, sin perder
    # información clave). Override con env var COMPACT_TRANSCRIPT=false si es
    # necesario debugear con el formato anterior.
    from services.transcriber import (
        format_transcript_for_prompt,
        format_transcript_for_prompt_compact,
    )
    if os.getenv("COMPACT_TRANSCRIPT", "true").lower() in ("false", "0", "no"):
        transcript_text = format_transcript_for_prompt(transcript)
    else:
        transcript_text = format_transcript_for_prompt_compact(transcript)
    print(f"   📝 Transcript prompt: {len(transcript_text)} chars")
    
    # Get dynamic prompt based on video duration AND CATEGORY
    duration = video_info.get('duration', 180)
    dynamic_prompt = get_dynamic_prompt(duration, tone, category, transcript)
    
    # Context about the video
    context = f"""
VIDEO INFO:
- Título original: {video_info.get('title', 'Desconocido')}
- Duración: {duration} segundos
- Canal: {video_info.get('uploader', 'Desconocido')}
- Idioma: {transcript.get('language', 'es')}

📜 TRANSCRIPCIÓN OFICIAL CON TIMESTAMPS:
{transcript_text}

🎯 INSTRUCCIÓN CRÍTICA:
- Lee la transcripción línea por línea
- Los timestamps son EXACTOS (generados por Whisper)
- NO adivines los segundos, COPIA los valores del mapa
- Si mencionas una frase, usa los timestamps del segmento que la contiene
"""
    
    # Prepare messages (TEXT-ONLY, no audio)
    messages = [
        {
            "role": "system",
            "content": dynamic_prompt
        },
        {
            "role": "user",
            "content": f"{context}\n\nAnaliza esta transcripción y genera el contenido viral. Responde SOLO con JSON válido."
        }
    ]
    
    # Generate analysis with retry (Q5: exponential backoff)
    print(f"🧠 Analyzing with {model}...")
    
    max_retries = 3
    last_error = None
    
    response_text = None
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=16000,
                response_format={"type": "json_object"}
            )

            # Defensive: Gemini Pro occasionally returns choices[0].message.content = None
            # (safety filter trip, rate limit, or upstream timeout). Treat as failure
            # and retry instead of crashing with `NoneType has no attribute strip`.
            raw = response.choices[0].message.content if response.choices else None
            if not raw or not raw.strip():
                finish = response.choices[0].finish_reason if response.choices else "no_choices"
                raise ValueError(f"LLM returned empty content (finish_reason={finish})")

            response_text = raw.strip()
            break
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                print(f"⚠️ Attempt {attempt + 1} failed: {str(e)[:120]}")
                print(f"   Retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                import time
                time.sleep(wait_time)
            else:
                print(f"❌ All {max_retries} retries exhausted")
                raise last_error

    if not response_text:
        # Should never happen — loop above either sets it or raises — but guard anyway
        raise RuntimeError("analyze_with_openrouter: no response_text after retry loop")
    
    # Clean up response if wrapped in markdown
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    
    response_text = response_text.strip()
    
    try:
        try:
            result_dict = json.loads(response_text)
        except json.JSONDecodeError:
            print("⚠️ Standard JSON parse failed, attempting repair with json_repair...")
            from json_repair import repair_json
            repaired_json = repair_json(response_text)
            result_dict = json.loads(repaired_json)
            print("✅ JSON repaired successfully")

        # Si Gemini envolvió el objeto raíz en un array, desenvuelve
        if isinstance(result_dict, list):
            if len(result_dict) == 1 and isinstance(result_dict[0], dict):
                print("⚠️ Gemini devolvió array raíz — desenvuelto a objeto")
                result_dict = result_dict[0]
            else:
                raise ValueError(f"Respuesta JSON es un array de {len(result_dict)} elementos (esperado: objeto)")

        # Sanitizar campos de objeto anidado que Gemini a veces devuelve
        # como lista de un elemento [{...}] en vez de objeto {...}.
        # Campos afectados: content_pieces, surgical_clipping, tiktok_package,
        # scores, verification.
        _NESTED_OBJECT_FIELDS = {
            'content_pieces', 'surgical_clipping', 'tiktok_package',
            'scores', 'verification',
        }

        def _unwrap_single_item_lists(moment: dict) -> dict:
            for field in _NESTED_OBJECT_FIELDS:
                val = moment.get(field)
                if isinstance(val, list) and len(val) == 1 and isinstance(val[0], dict):
                    print(f"   ⚠️ Campo '{field}' era lista de 1 elemento — desenvuelto a objeto")
                    moment[field] = val[0]
            return moment

        moments = result_dict.get('viral_moments', [])
        if isinstance(moments, list):
            result_dict['viral_moments'] = [
                _unwrap_single_item_lists(m) if isinstance(m, dict) else m
                for m in moments
            ]

        # ── Schema migration (Phase 1.1): surgical_clipping → flat fields ────
        # Canonical schema uses moment.start_time / moment.end_time as the
        # single source of truth. Some prompts still emit surgical_clipping
        # (nested). We migrate here so downstream code (validators, main.py,
        # subtitle alignment) never has to handle both shapes.
        for _m in result_dict.get('viral_moments', []):
            if not isinstance(_m, dict):
                continue
            _sc = _m.get('surgical_clipping')
            if isinstance(_sc, dict):
                # Migrate timestamps only if the canonical fields are missing.
                # Use floor for start (capture full setup) and ceil for end
                # (capture full reaction) — matches what int() does for start
                # but rounds up for end so we never truncate the punchline.
                import math as _math
                if _m.get('start_time') is None and _sc.get('start_time') is not None:
                    _m['start_time'] = int(_math.floor(float(_sc['start_time'])))
                if _m.get('end_time') is None and _sc.get('end_time') is not None:
                    _m['end_time'] = int(_math.ceil(float(_sc['end_time'])))
                if _m.get('clipping_reason') is None and _sc.get('reason'):
                    _m['clipping_reason'] = _sc['reason']

        # Debug rápido
        if result_dict.get('viral_moments'):
            first_moment = result_dict['viral_moments'][0]
            print(f"📊 Debug - pillar_type: {first_moment.get('pillar_type', 'NOT FOUND')}")
            print(f"📊 Debug - scores: {first_moment.get('scores', 'NOT FOUND')}")

        # ── Phase 1.3: content validators ────────────────────────────────────
        # Auto-clean what's safely fixable ([Link], "Tweet N:" prefixes, overlay
        # formatting) and emit telemetry on what needs attention (wrong tweet
        # count, char overflow, AI clichés). The cleaned dict is then validated
        # by Pydantic. Future Phase 2/3 may trigger retries based on warnings.
        from services.content_validators import clean_analysis
        validation_stats = clean_analysis(result_dict)
        print(f"🧹 Content validators: {validation_stats.summary_line()}")
        if validation_stats.problems:
            for p in validation_stats.problems[:5]:
                print(f"   ⚠️ {p}")
            if len(validation_stats.problems) > 5:
                print(f"   ... +{len(validation_stats.problems) - 5} more")

        # Phase 1.5: emit metrics to Sentry as breadcrumb + tag.
        # Lets us build a dashboard "% of jobs with wrong_tweet_count > 0" and
        # alert when quality regresses (e.g. after a prompt change).
        try:
            import sentry_sdk as _sentry
            _sentry.add_breadcrumb(
                category="content_quality",
                message="content validators",
                level="warning" if validation_stats.problems else "info",
                data={
                    "moments_checked": validation_stats.moments_checked,
                    "model": model,
                    "category": category,
                    "links_stripped": validation_stats.links_stripped,
                    "tweet_prefixes_stripped": validation_stats.tweet_prefixes_stripped,
                    "overlay_fixes": validation_stats.overlay_truncated + validation_stats.overlay_uppercased,
                    "wrong_tweet_count": validation_stats.wrong_tweet_count,
                    "tweets_too_long": validation_stats.tweets_too_long,
                    "tweets_too_short": validation_stats.tweets_too_short,
                    "cliche_hits": validation_stats.cliche_hits,
                    "linkedin_out_of_range": validation_stats.linkedin_out_of_range,
                    "missing_twitter": validation_stats.missing_twitter,
                    "missing_linkedin": validation_stats.missing_linkedin,
                    "missing_tiktok_caption": validation_stats.missing_tiktok_caption,
                },
            )
            # Tag the scope so the metric is queryable in Sentry's UI per-job
            _sentry.set_tag("content_quality.has_problems", bool(validation_stats.problems))
            _sentry.set_tag("content_quality.category", category)
        except Exception as _e:
            print(f"   (sentry telemetry skipped: {_e})")

        result = AnalysisResult(**result_dict)
        print(f"✅ Analysis complete: {len(result.viral_moments)} viral moments found")

        # ── Guardar al cache para futuros re-procesos ─────────────────────
        if video_id:
            try:
                save_analysis(
                    video_id=video_id,
                    model=model,
                    result=result_dict,
                    tone=tone,
                    category_detected=category,
                    prompt_chars=len(transcript_text),
                )
            except Exception as e:
                print(f"   ⚠️ No se pudo guardar al analysis_cache: {e}")

        return result
    except Exception as e:
        print(f"❌ Failed to parse/validate JSON response: {e}")
        if 'result_dict' in locals():
            # Log los primeros 300 chars de cada momento para debuggear
            moments_preview = []
            for m in (result_dict.get('viral_moments') or [])[:3]:
                moments_preview.append({k: str(v)[:80] for k, v in (m or {}).items()})
            print(f"   Momentos (preview): {moments_preview}")
        raise ValueError(f"Invalid JSON response: {e}")


# Keep old function name for backwards compatibility
def analyze_with_gemini(audio_path: str, video_info: dict, tone: str = "profesional") -> Optional[AnalysisResult]:
    """Wrapper for backwards compatibility - now uses OpenRouter"""
    return analyze_with_openrouter(audio_path, video_info, tone)


def cleanup_uploaded_file(audio_path: str):
    """Cleanup function (no-op for OpenRouter, kept for compatibility)"""
    pass
