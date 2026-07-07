import os
import json
import base64
from openai import OpenAI
from pathlib import Path
from typing import Optional
from models.schemas import AnalysisResult
from config.model_tiers import (
    get_model,
    get_temperature,
    output_language_instruction,
)


def get_video_category(video_info: dict, client=None, transcript_excerpt: str = None) -> str:
    """
    Meta-Classifier: podcast vs business (vs entertainment si está habilitado).

    Phase 1.4: Reduced from 5 categories to 2 (the only ones with professionally
    tuned prompts). Anything that isn't clearly a podcast/interview falls back
    to 'business' (broader prompt that handles monologues, keynotes, talks,
    tutorials and general content reasonably).
    Fase 5: acepta un excerpt del transcript (~1500 chars) para clasificar por
    contenido real, y puede devolver 'entertainment' si
    ENABLE_ENTERTAINMENT_CATEGORY=true.

    Args:
        video_info: Dict with 'title' and optionally 'description'
        client: OpenAI client for OpenRouter (reuses existing connection)
        transcript_excerpt: First ~1500 chars of the transcript (optional)

    Returns:
        Category string: 'podcast', 'business' (or 'entertainment' if enabled)
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
        model = get_model("classifier")

        # Fase 5: clasificar también con el inicio del transcript (el título
        # solo suele ser insuficiente y la descripción de oEmbed viene vacía).
        transcript_block = ""
        if transcript_excerpt:
            transcript_block = f"\nInicio del transcript:\n{transcript_excerpt[:1500]}\n"

        # Fase 5: entertainment reactivable por env (cuando pasada A esté estable)
        allow_entertainment = os.getenv(
            "ENABLE_ENTERTAINMENT_CATEGORY", ""
        ).lower() in ("1", "true", "yes")

        if allow_entertainment:
            categories_block = """Categorías:
- podcast: Entrevistas, conversaciones entre 2+ personas, episodios de podcast, mesas redondas, charlas con invitados.
- entertainment: Comedia, humor, reacciones, gaming, clips de streamers, contenido puramente de entretenimiento sin intención educativa.
- business: TODO LO DEMÁS — monólogos, keynotes, talks, tutoriales, contenido de un solo orador, vlogs, contenido educativo, deportes, lifestyle, motivacional, tech.

REGLA: Si NO es claramente conversación 2+ personas ni humor/entretenimiento puro, es 'business'.

Responde con UNA SOLA PALABRA: podcast, entertainment o business."""
        else:
            categories_block = """Categorías:
- podcast: Entrevistas, conversaciones entre 2+ personas, episodios de podcast, mesas redondas, charlas con invitados.
- business: TODO lO DEMÁS — monólogos, keynotes, talks, tutoriales, contenido de un solo orador, vlogs, contenido educativo, comedia, deportes, lifestyle, motivacional, tech.

REGLA: Si NO es claramente una conversación entre 2+ personas, es 'business'.

Responde con UNA SOLA PALABRA: podcast o business."""

        classification_prompt = f"""Clasifica este video en UNA categoría:

Título: {title}
Descripción: {description}
{transcript_block}
{categories_block}"""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Clasificador de contenido. Respondes con una sola palabra (la categoría)."},
                {"role": "user", "content": classification_prompt}
            ],
            max_tokens=5,
            temperature=get_temperature("classifier"),
            timeout=10,  # Phase 1.4: bumped from 3s — was failing too often
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM devolvió respuesta vacía")
        category = content.strip().lower()

        valid = ('podcast', 'business', 'entertainment') if allow_entertainment else ('podcast', 'business')
        if category in valid:
            return category
        print(f"⚠️ Categoría inválida del LLM ('{category}'), usando fallback de keywords")
        return _keyword_classify()

    except Exception as e:
        print(f"⚠️ LLM classification falló ({str(e)[:60]}), usando keyword fallback")
        return _keyword_classify()



def get_dynamic_prompt(duration: int, tone: str = "profesional", category: str = "business", transcript: dict = None, user_name: str = "Creador", user_title: str = "Experto", language: str = None) -> str:
    """
    Generate dynamic prompt based on video duration, tone, and category.
    
    Args:
        duration: Video duration in seconds
        tone: Content tone/style
        category: Content category (business, entertainment, tech, lifestyle)
        transcript: Optional transcript data (not used in prompt directly, passed for context)
        user_name: User's name for personalization
        user_title: User's professional title
        language: Transcript language code — forces output language (Fase 1)
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
    lang_instruction = output_language_instruction(language)

    # Phase 1.4: Binary routing — podcast OR business (default).
    # Fase 5: entertainment reactivable vía ENABLE_ENTERTAINMENT_CATEGORY
    # (el prompt vive en category_prompts.py). Otros valores legacy
    # (tech/lifestyle) caen al prompt de business.
    if category == 'podcast':
        print(f"🎙️ Using PODCAST strategy")
        import services.podcast_prompt as pp
        try:
            return (
                pp.get_podcast_prompt(duration, num_moments, moments_instruction, tone_style, user_name, user_title)
                + f"\n\n{lang_instruction}"
            )
        except Exception as e:
            print(f"⚠️ Podcast prompt import failed, falling back to business: {e}")

    if category == 'entertainment' and os.getenv(
        "ENABLE_ENTERTAINMENT_CATEGORY", ""
    ).lower() in ("1", "true", "yes"):
        print(f"🎭 Using ENTERTAINMENT strategy")
        try:
            from services.category_prompts import get_entertainment_prompt
            return (
                get_entertainment_prompt(duration, num_moments, moments_instruction, tone_style, user_name, user_title)
                + f"\n\n{lang_instruction}"
            )
        except Exception as e:
            print(f"⚠️ Entertainment prompt failed, falling back to business: {e}")

    print(f"💼 Using BUSINESS strategy (category={category})")
    
    return f"""Actúa como un Director de Contenido Viral con 15 años de experiencia en psicología de masas y algoritmos de redes sociales.

{lang_instruction}

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
        "tiktok_caption": "[Caption 1-2 líneas + 3-4 hashtags relevantes]"
      }}
    }}
  ],
  "overall_virality_score": 8,
  "total_roi_minutes": 135
}}

🎭 DETECCIÓN DE SENTIMIENTO:
Analiza el tono del audio y asigna:
- "sarcastic": Usa ironía o humor crítico
- "serious": Tono profesional y directo
- "motivational": Energético e inspirador
- "casual": Conversacional y relajado

Asegúrate que el contenido generado MANTENGA ese sentimiento.

🎯 RECORDATORIO FINAL: Genera exactamente {num_moments} momento(s) viral(es).
El objetivo NO es resumir, es EXPANDIR el contenido para maximizar tiempo de lectura y engagement."""


def analyze_with_openrouter(
    transcript: dict,
    video_info: dict,
    tone: str = "profesional",
    user_name: str = None,
    user_title: str = None,
) -> Optional[AnalysisResult]:
    """
    Analyze transcript with OpenRouter for viral moment detection.
    Uses Whisper transcript with precise timestamps for deterministic results.

    Fase 2: por defecto usa el pipeline de dos pasadas — pasada A (selección
    de momentos con sobre-generación, sin copy) acá, y pasada B (copy completo
    desde el texto Whisper real) en main.py post-clip. El mega-prompt legacy
    queda como fallback si la pasada A falla (TWO_PASS_ANALYSIS=false lo
    fuerza).

    Args:
        transcript: Whisper transcript with segments and timestamps
        video_info: Video metadata
        tone: Voice tone for content generation
        user_name: Nombre real del creador (Fase 5) — default "Creador"
        user_title: Título profesional real (Fase 5) — default "Experto"

    Returns:
        AnalysisResult with viral moments and content
    """
    # Configure OpenRouter client
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY")
    )

    # Fase 1: model tier para análisis (env MODEL_ANALYSIS → legacy → default)
    model = get_model("analysis")
    user_name = (user_name or "").strip() or "Creador"
    user_title = (user_title or "").strip() or "Experto"

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
    # Fase 5: el clasificador recibe también el inicio del transcript (~1500
    # chars) — el título solo no alcanza y la descripción de oEmbed viene vacía.
    def _transcript_excerpt(t: dict, max_chars: int = 1500) -> str:
        parts, total = [], 0
        for sg in (t.get("segments") or []):
            txt = (sg.get("text") or "").strip()
            if not txt:
                continue
            parts.append(txt)
            total += len(txt) + 1
            if total >= max_chars:
                break
        return " ".join(parts)[:max_chars]

    category = None
    if video_id:
        category = get_cached_category(video_id, model)
        if category:
            print(f"✅ Category cached: {category.upper()}")
    if not category:
        print(f"📂 Detecting content category...")
        category = get_video_category(
            video_info, client, transcript_excerpt=_transcript_excerpt(transcript)
        )
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
    
    # Duración efectiva: oEmbed suele devolver 0 — usar último segmento
    duration = video_info.get('duration') or 0
    _segments = transcript.get('segments') or []
    if not duration and _segments:
        duration = float(_segments[-1].get('end', 0))
    duration = duration or 180
    language = transcript.get('language')

    # ── Fase 2: pasada A (selección de momentos, sin copy) ──────────────────
    # Default ON. TWO_PASS_ANALYSIS=false fuerza el mega-prompt legacy.
    # Si la pasada A falla tras retries, caemos al mega-prompt (su copy queda
    # como borrador que la pasada B pisa post-Whisper).
    use_two_pass = os.getenv("TWO_PASS_ANALYSIS", "true").lower() not in ("false", "0", "no")
    result_dict = None
    analysis_mode = "mega_prompt"
    if use_two_pass:
        try:
            from services.moment_selector import select_moments
            result_dict = select_moments(
                transcript_text=transcript_text,
                video_info=video_info,
                duration=duration,
                category=category,
                language=language,
                client=client,
                model=model,
                transcript=transcript,
            )
            analysis_mode = "two_pass"
        except Exception as e:
            print(f"⚠️ Pasada A falló ({str(e)[:150]}) — fallback a mega-prompt legacy")
            result_dict = None

    if result_dict is None:
        # ── Legacy: mega-prompt (selección + copy en una sola llamada) ──────
        dynamic_prompt = get_dynamic_prompt(
            duration, tone, category, transcript,
            user_name=user_name, user_title=user_title, language=language,
        )

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
                    # Fase 1: output estructural — temperature baja (era 0.7)
                    temperature=get_temperature("analysis"),
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
            result_dict = json.loads(response_text)
        except json.JSONDecodeError:
            print("⚠️ Standard JSON parse failed, attempting repair with json_repair...")
            from json_repair import repair_json
            repaired_json = repair_json(response_text)
            result_dict = json.loads(repaired_json)
            print("✅ JSON repaired successfully")

    try:
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
        # Fase 2: en two-pass no hay copy todavía (pasada B lo genera post-
        # Whisper), así que solo validamos overlay y saltamos los checks de copy.
        from services.content_validators import clean_analysis
        validation_stats = clean_analysis(result_dict)
        if analysis_mode != "two_pass":
            print(f"🧹 Content validators: {validation_stats.summary_line()}")
            if validation_stats.problems:
                for p in validation_stats.problems[:5]:
                    print(f"   ⚠️ {p}")
                if len(validation_stats.problems) > 5:
                    print(f"   ... +{len(validation_stats.problems) - 5} more")

        # Phase 1.5: emit metrics to Sentry as breadcrumb + tag.
        # Lets us build a dashboard "% of jobs with wrong_tweet_count > 0" and
        # alert when quality regresses (e.g. after a prompt change).
        # (two-pass: sin copy todavía — la telemetría de copy emitiría falsos
        # "missing"; se salta y la pasada B valida por momento en main.py.)
        if analysis_mode != "two_pass":
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


def _clip_text_from_words(words: list[dict]) -> str:
    """Build plain transcript from whisper words."""
    return " ".join((w.get("word") or "").strip() for w in words if (w.get("word") or "").strip())


def generate_moment_copy_full(
    moment,
    clip_text: str,
    *,
    category: str = "business",
    tone: str = "profesional",
    language: str = None,
    user_name: str = "Creador",
    user_title: str = "Experto",
    client=None,
) -> bool:
    """
    Pasada B (Fase 2): genera TODO el copy del momento desde el texto real
    del clip (Whisper post-corte, o slice del transcript si no hay Whisper).

    Genera: twitter_thread, linkedin_post, tiktok_caption, hook final y
    viral_overlay. Mutates moment in-place. El copy previo (si existía, del
    mega-prompt) queda como fallback si esta pasada falla.

    Returns True si el copy se regeneró OK.
    """
    if not clip_text or not clip_text.strip():
        return False

    if client is None:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )

    model = get_model("copy")
    hook_draft = getattr(moment, 'hook', '') or ''
    overlay_draft = getattr(moment, 'viral_overlay', '') or ''
    trigger = getattr(moment, 'emotional_trigger', '') or ''
    lang_instruction = output_language_instruction(language)

    tone_map = {
        "profesional": "profesional, directo y con autoridad",
        "sarcastico": "sarcástico e irónico, con humor inteligente",
        "motivador": "motivador y energético, que inspira acción",
        "casual": "casual y cercano, como hablando con un amigo",
    }
    tone_desc = tone_map.get((tone or "profesional").lower(), tone_map["profesional"])

    prompt = f"""Eres un copywriter viral senior. Genera el paquete COMPLETO de copy para este clip, usando SOLO el texto real del audio (no inventes contenido que no esté en el transcript).

{lang_instruction}

CLIP TRANSCRIPT (texto exacto del audio del clip final):
{clip_text[:4000]}

CONTEXTO:
- Creador: {user_name} ({user_title})
- Tono de marca: {tone_desc}
- Categoría: {category}
- Trigger emocional detectado: {trigger}
- Hook borrador (mejóralo si puedes, siempre anclado al transcript): {hook_draft}
- Overlay borrador: {overlay_draft}

REGLAS POR PIEZA:
1. twitter_thread: EXACTAMENTE 7 tweets separados por \\n\\n. Sin prefijos "Tweet 1:", sin [Link], sin hashtags de relleno. Cada tweet 180-280 chars y funciona solo fuera del hilo. Estructura: hook contraintuitivo → dolor específico → giro → prueba/mecanismo → aplicación práctica → remate que escala → CTA con pregunta abierta.
2. linkedin_post: 800-1200 caracteres. Hook de 3 líneas antes del "ver más", párrafos de máx 2-3 líneas, bullets si aplica, pregunta de engagement al final. Sin pedir likes.
3. tiktok_caption: 1-2 líneas coloquiales + 3-4 hashtags relevantes al tema.
4. hook: frase gancho del momento (1-2 líneas, forma larga) fiel al contenido real del clip.
5. viral_overlay: MÁXIMO 4 PALABRAS EN MAYÚSCULAS. Cartel TikTok que frena el scroll en <1s (ej: "NADIE TE DICE ESTO"). NO resume el clip.

PROHIBIDO: clichés de IA ("en el mundo de hoy", "descubre cómo", "es importante destacar", "sumérgete").

Responde SOLO JSON:
{{"twitter_thread": "...", "linkedin_post": "...", "tiktok_caption": "...", "hook": "...", "viral_overlay": "..."}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Copywriter viral senior. Respondes solo JSON válido."},
                {"role": "user", "content": prompt},
            ],
            temperature=get_temperature("copy"),
            max_tokens=4000,
            response_format={"type": "json_object"},
            timeout=60,
        )
        raw = response.choices[0].message.content if response.choices else None
        if not raw or not raw.strip():
            print("   ⚠️ Pasada B: LLM devolvió vacío")
            return False

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            from json_repair import repair_json
            data = json.loads(repair_json(raw.strip()))

        cp = moment.content_pieces
        if data.get("twitter_thread"):
            cp.twitter_thread = data["twitter_thread"]
        if data.get("linkedin_post"):
            cp.linkedin_post = data["linkedin_post"]
        if data.get("tiktok_caption"):
            cp.tiktok_caption = data["tiktok_caption"]
        if data.get("hook"):
            moment.hook = data["hook"]
        if data.get("viral_overlay"):
            moment.viral_overlay = data["viral_overlay"]
        print(f"   ✅ Pasada B: copy completo generado desde texto real ({len(clip_text)} chars, model={model})")
        return True
    except Exception as e:
        print(f"   ⚠️ Pasada B falló: {str(e)[:150]}")
        return False


def regenerate_moment_copy(
    moment,
    clip_text: str,
    *,
    category: str = "business",
    tone: str = "profesional",
    client=None,
) -> None:
    """
    Lightweight second-pass LLM: regenerate twitter_thread + linkedin_post
    using ONLY the clip's actual whisper text (post-Whisper in main.py).
    Mutates moment.content_pieces in-place.
    """
    if not clip_text or not clip_text.strip():
        return

    if client is None:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )

    model = get_model("copy")
    hook = getattr(moment, 'hook', '') or ''
    viral_overlay = getattr(moment, 'viral_overlay', '') or ''

    prompt = f"""Genera copy viral SOLO a partir del texto real del clip (no inventes fuera de este audio).

CLIP TRANSCRIPT (texto exacto del audio del clip):
{clip_text[:4000]}

Hook del momento: {hook}
Overlay TikTok: {viral_overlay}
Categoría: {category}
Tono: {tone}

REGLAS:
- twitter_thread: exactamente 7 tweets separados por \\n\\n
- Sin prefijos "Tweet 1:", sin [Link]
- Cada tweet 180-280 caracteres, funciona solo fuera del hilo
- linkedin_post: 800-1200 caracteres, párrafos cortos, pregunta al final
- Sin clichés ("en el mundo de hoy", "descubre cómo", "es importante destacar")
- Usa SOLO ideas y frases presentes en el transcript del clip

Responde SOLO JSON:
{{"twitter_thread": "...", "linkedin_post": "..."}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Copywriter viral. Respondes solo JSON válido."},
                {"role": "user", "content": prompt},
            ],
            temperature=get_temperature("copy"),
            max_tokens=4000,
            response_format={"type": "json_object"},
            timeout=45,
        )
        raw = response.choices[0].message.content if response.choices else None
        if not raw or not raw.strip():
            print("   ⚠️ Copy regen: LLM devolvió vacío")
            return

        data = json.loads(raw.strip())
        cp = moment.content_pieces
        if data.get("twitter_thread"):
            cp.twitter_thread = data["twitter_thread"]
        if data.get("linkedin_post"):
            cp.linkedin_post = data["linkedin_post"]
        print(f"   ✅ Copy regenerado desde whisper ({len(clip_text)} chars)")
    except Exception as e:
        print(f"   ⚠️ Copy regen falló: {str(e)[:120]}")


def regenerate_moment_copy_dict(moment_dict: dict, clip_text: str, **kwargs) -> None:
    """Dict-friendly wrapper for content_validators retry."""
    from models.schemas import ViralMoment
    try:
        m = ViralMoment(**moment_dict)
        regenerate_moment_copy(m, clip_text, **kwargs)
        moment_dict["content_pieces"] = m.content_pieces.model_dump()
    except Exception as e:
        print(f"   ⚠️ Copy regen dict falló: {str(e)[:80]}")


# Keep old function name for backwards compatibility
def analyze_with_gemini(audio_path: str, video_info: dict, tone: str = "profesional") -> Optional[AnalysisResult]:
    """Wrapper for backwards compatibility - now uses OpenRouter"""
    return analyze_with_openrouter(audio_path, video_info, tone)


def cleanup_uploaded_file(audio_path: str):
    """Cleanup function (no-op for OpenRouter, kept for compatibility)"""
    pass
