import os
import json
import base64
from openai import OpenAI
from pathlib import Path
from typing import Optional
from models.schemas import AnalysisResult


def get_video_category(video_info: dict, client=None) -> str:
    """
    Meta-Classifier: Ultra-fast LLM call to detect video category.
    Uses Gemini Flash for 100% precision on intent detection.
    
    Args:
        video_info: Dict with 'title' and optionally 'description'
        client: OpenAI client for OpenRouter (reuses existing connection)
    
    Returns:
        Category string: 'business', 'entertainment', 'lifestyle', or 'tech'
    """
    import os
    from openai import OpenAI
    
    title = video_info.get('title', '')
    description = video_info.get('description', '')[:200]  # Limit for speed
    
    # Fallback to keyword matching if no client
    if not client:
        # Quick keyword matching fallback
        text = (title + ' ' + description).lower()
        
        comedy_score = sum(1 for k in ['comedia', 'humor', 'chiste', 'risa', 'gracioso', 'meme', 'divertido'] if k in text)
        business_score = sum(1 for k in ['invertir', 'negocio', 'finanzas', 'marketing', 'dinero', 'empresa'] if k in text)
        tech_score = sum(1 for k in ['tutorial', 'cómo', 'programación', 'código', 'app', 'tech'] if k in text)
        lifestyle_score = sum(1 for k in ['vida', 'experiencia', 'historia', 'motivación', 'inspiración'] if k in text)
        podcast_score = sum(1 for k in ['entrevista', 'podcast', 'conversación', 'charlando', 'invitado', 'entre dos'] if k in text)
        sports_score = sum(1 for k in ['fútbol', 'mundial', 'deporte', 'gol', 'partido', 'selección', 'jugador', 'copa'] if k in text)
        
        scores = {
            'entertainment': max(comedy_score, sports_score),  # Sports = entertainment
            'business': business_score,
            'tech': tech_score,
            'lifestyle': lifestyle_score,
            'podcast': podcast_score
        }
        return max(scores.items(), key=lambda x: (x[1], x[0]))[0] if max(scores.values()) > 0 else 'entertainment'
    
    # LLM-based classification (ultra-fast with timeout)
    try:
        model = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
        
        classification_prompt = f"""Clasifica este video en UNA categoría basándote en título.

Título: {title}

Categorías disponibles:
- business: Negocios, finanzas, inversión, marketing, emprendimiento
- entertainment: Comedia, humor, entretenimiento, memes, sketches, deportes, fútbol
- tech: Tutoriales, programación, tecnología, how-to
- lifestyle: Crecimiento personal, motivación, historias de vida
- podcast: Entrevistas, conversaciones, diálogos entre personas

Responde con UNA SOLA PALABRA (la categoría exacta). Sin explicaciones."""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Eres un clasificador de contenido experto. Respondes con una sola palabra."},
                {"role": "user", "content": classification_prompt}
            ],
            max_tokens=10,
            temperature=0,
            timeout=3  # 3 second timeout for speed
        )
        
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM devolvió respuesta vacía para categoría")
        category = content.strip().lower()

        # Validate response
        valid_categories = ['business', 'entertainment', 'tech', 'lifestyle', 'podcast']
        if category in valid_categories:
            return category
        else:
            print(f"⚠️ Invalid category '{category}', using keyword fallback")
            raise ValueError("Invalid category")
            
    except Exception as e:
        print(f"⚠️ LLM classification failed ({str(e)[:50]}), using keyword fallback")
        # Fallback to keyword matching
        text = (title + ' ' + description).lower()
        
        # Enhanced keywords for entertainment
        if any(k in text for k in ['comedia', 'humor', 'chiste', 'risa', 'entrevista', 'suculentas', 
                                     'mortedor', 'podcast', 'charla', 'conversación', 'entretenimiento']):
            return 'entertainment'
        elif any(k in text for k in ['invertir', 'negocio', 'finanzas', 'marketing', 'dinero']):
            return 'business'
        elif any(k in text for k in ['tutorial', 'programación', 'código', 'cómo']):
            return 'tech'
        else:
            # Default to entertainment if no clear match (safer for comedy)
            return 'entertainment'



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
    
    # CATEGORY ROUTING: Different strategy per content type
    if category == "entertainment":
        print(f"🎭 Using ENTERTAINMENT strategy")
        # For entertainment: shorter, teaser-focused, comedy timing
        import services.category_prompts as cp
        try:
            return cp.get_entertainment_prompt(duration, num_moments, moments_instruction, tone_style, user_name, user_title)
        except:
            pass  # Fallback to business if import fails
    
    # Podcast: dialogue-focused, interaction moments
    if category == 'podcast':
        print(f"🎙️ Using PODCAST strategy (category: {category})")
        import services.podcast_prompt as pp
        try:
            return pp.get_podcast_prompt(duration, num_moments, moments_instruction, tone_style, user_name, user_title)
        except Exception as e:
            print(f"⚠️ Podcast prompt import failed, using business fallback: {e}")
            pass  # Fallback to business
    
    # Default to BUSINESS strategy (most robust, Phases A-C)
    print(f"💼 Using BUSINESS strategy (category: {category})")
    
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
✅ **UN SOLO pillar_type por momento: "authority" | "utility" | "connection"**
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
    
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=16000,
                response_format={"type": "json_object"}
            )
            
            response_text = response.choices[0].message.content.strip()
            break
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                print(f"⚠️ Attempt {attempt + 1} failed: {str(e)[:80]}")
                print(f"   Retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                import time
                time.sleep(wait_time)
            else:
                print(f"❌ All {max_retries} retries exhausted")
                raise last_error
    
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
            # repair_json returns the repaired JSON string
            repaired_json = repair_json(response_text)
            result_dict = json.loads(repaired_json)
            print("✅ JSON repaired successfully")
        
        # Debug: Check if pillar_type and scores are in response
        if result_dict.get('viral_moments'):
            first_moment = result_dict['viral_moments'][0]
            print(f"📊 Debug - pillar_type: {first_moment.get('pillar_type', 'NOT FOUND')}")
            print(f"📊 Debug - scores: {first_moment.get('scores', 'NOT FOUND')}")
        
        result = AnalysisResult(**result_dict)
        print(f"✅ Analysis complete: {len(result.viral_moments)} viral moments found")

        # ── Guardar al cache para futuros re-procesos ─────────────────────
        if video_id:
            try:
                save_analysis(
                    video_id=video_id,
                    model=model,
                    result=result_dict,  # serializable
                    tone=tone,
                    category_detected=category,
                    prompt_chars=len(transcript_text),
                )
            except Exception as e:
                print(f"   ⚠️ No se pudo guardar al analysis_cache: {e}")

        return result
    except Exception as e:
        print(f"❌ Failed to parse/validate JSON response: {e}")
        # print(f"Response was: {response_text[:500]}...")
        raise ValueError(f"Invalid JSON response: {e}")
    except Exception as e:
        print(f"❌ Failed to validate response: {e}")
        if 'result_dict' in locals():
            print(f"Raw dict: {result_dict}")
        raise


# Keep old function name for backwards compatibility
def analyze_with_gemini(audio_path: str, video_info: dict, tone: str = "profesional") -> Optional[AnalysisResult]:
    """Wrapper for backwards compatibility - now uses OpenRouter"""
    return analyze_with_openrouter(audio_path, video_info, tone)


def cleanup_uploaded_file(audio_path: str):
    """Cleanup function (no-op for OpenRouter, kept for compatibility)"""
    pass
