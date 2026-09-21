"""
Content validators — Phase 1.3.

Cleans + validates AI-generated content_pieces and tiktok_package on the
RAW dict (pre-Pydantic), so we apply fixes once and downstream code can
trust the shape.

Design:
- Auto-fix what's safely fixable (strip [Link], strip "Tweet N:" prefixes,
  uppercase overlay, truncate overlay >4 words).
- Detect + log what isn't safely fixable (wrong tweet count, char count
  out of range, AI clichés in the prose).
- Return a ValidationStats object so callers can emit metrics (Phase 1.5)
  and Phase 2/3 can decide whether to retry the AI call.

Public API:
    clean_moment(moment_dict, *, expected_tweets=7) -> ValidationStats
    clean_analysis(result_dict) -> ValidationStats   # aggregate over all moments

W6 (docs/PLAN_CALIDAD.md §4): el juez castiga hook y overlay cuando prometen
algo que el clip no dice — son títulos sobre el TEMA, no frases reales. Acá
viven los chequeos de fidelidad (¿el overlay/hook aparecen dichos en el
clip?) que usa la Pasada B (`services/processor.py::generate_moment_copy_full`)
para decidir si regenerar o caer a un fallback derivado del texto real.
"""
from __future__ import annotations
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


# ── Patterns ──────────────────────────────────────────────────────────────

# [Link], [Link aquí], [Insertar link], [LINK HERE], etc.
_LINK_PATTERN = re.compile(
    r'\[\s*(?:insert(?:ar)?\s+)?link(?:\s+(?:aqu[íi]|here|del clip|al clip))?\s*\]',
    re.IGNORECASE,
)

# "Tweet 1:", "Tweet 1.", "Tweet 1 -", "Tweet 1—" at start of line/segment
_TWEET_PREFIX_PATTERN = re.compile(
    r'(?:^|\n\n)\s*Tweet\s*\d+\s*[:\-—.]?\s*',
    re.IGNORECASE,
)

# Common AI clichés. Each match increments cliche_count; we don't auto-remove
# because surgical removal is hard (could leave dangling sentences).
_AI_CLICHE_PATTERNS = [
    r'\ben el mundo (?:de hoy|actual|digital)\b',
    r'\bes importante (?:destacar|mencionar|recordar|notar)\b',
    r'\bdescubre c[óo]mo\b',
    r'\ben resumen\b',
    r'\ba continuaci[óo]n\b',
    r'\bno te pierdas\b',
    r'\ben esta era (?:digital|moderna)\b',
    r'\btiming es clave\b',
    r'\bun recordatorio de que\b',
    r'\bsumerg(?:e|í)te en\b',
    r"\bin today's (?:world|digital age|fast-paced)\b",
    r'\bin the realm of\b',
    r'\bnavigate the\b',
    r'\bdelve into\b',
    r'\bunlock the (?:secret|power|potential)\b',
]
_AI_CLICHE_RE = re.compile('|'.join(_AI_CLICHE_PATTERNS), re.IGNORECASE)


# ── Stats ─────────────────────────────────────────────────────────────────

@dataclass
class ValidationStats:
    """Per-moment or aggregate validation telemetry."""
    moments_checked: int = 0

    # Auto-fixes applied
    links_stripped: int = 0
    tweet_prefixes_stripped: int = 0
    overlay_truncated: int = 0
    overlay_uppercased: int = 0

    # Warnings (not auto-fixed — caller may decide to retry)
    wrong_tweet_count: int = 0
    tweets_too_long: int = 0       # >280 chars
    tweets_too_short: int = 0      # <100 chars
    cliche_hits: int = 0
    linkedin_out_of_range: int = 0  # <500 or >1500 chars
    missing_twitter: int = 0
    missing_linkedin: int = 0
    missing_tiktok_caption: int = 0

    # Per-moment problem list (for Sentry / logging)
    problems: list = field(default_factory=list)

    def merge(self, other: 'ValidationStats') -> None:
        for f in (
            'moments_checked', 'links_stripped', 'tweet_prefixes_stripped',
            'overlay_truncated', 'overlay_uppercased', 'wrong_tweet_count',
            'tweets_too_long', 'tweets_too_short', 'cliche_hits',
            'linkedin_out_of_range', 'missing_twitter', 'missing_linkedin',
            'missing_tiktok_caption',
        ):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        self.problems.extend(other.problems)

    def summary_line(self) -> str:
        fixes = (
            self.links_stripped + self.tweet_prefixes_stripped
            + self.overlay_truncated + self.overlay_uppercased
        )
        warnings = (
            self.wrong_tweet_count + self.tweets_too_long + self.tweets_too_short
            + self.cliche_hits + self.linkedin_out_of_range
            + self.missing_twitter + self.missing_linkedin + self.missing_tiktok_caption
        )
        return (
            f"checked={self.moments_checked} fixes={fixes} warnings={warnings} "
            f"(links={self.links_stripped} tweet_prefix={self.tweet_prefixes_stripped} "
            f"overlay_fix={self.overlay_truncated + self.overlay_uppercased} "
            f"wrong_tweet_count={self.wrong_tweet_count} "
            f"long_tweets={self.tweets_too_long} short_tweets={self.tweets_too_short} "
            f"cliches={self.cliche_hits} "
            f"missing_tw={self.missing_twitter} missing_li={self.missing_linkedin} "
            f"missing_tk={self.missing_tiktok_caption})"
        )


# ── Helpers ───────────────────────────────────────────────────────────────

def _strip_links(text: str) -> tuple[str, int]:
    """Remove [Link] placeholders. Returns (cleaned, count)."""
    matches = _LINK_PATTERN.findall(text)
    if not matches:
        return text, 0
    cleaned = _LINK_PATTERN.sub('', text)
    # Collapse extra whitespace left behind
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    cleaned = re.sub(r' +\n', '\n', cleaned)
    return cleaned.strip(), len(matches)


def _strip_tweet_prefixes(text: str) -> tuple[str, int]:
    """Remove 'Tweet N:' style prefixes. Returns (cleaned, count)."""
    matches = _TWEET_PREFIX_PATTERN.findall(text)
    if not matches:
        return text, 0
    # Preserve paragraph breaks: replace prefix with \n\n (or empty if at start)
    cleaned = _TWEET_PREFIX_PATTERN.sub(
        lambda m: '\n\n' if m.group(0).startswith('\n\n') else '',
        text,
    )
    return cleaned.strip(), len(matches)


def _count_cliches(text: str) -> int:
    return len(_AI_CLICHE_RE.findall(text))


def _split_tweets(thread: str) -> list[str]:
    """Split a thread by double-newline. Empty chunks dropped."""
    return [t.strip() for t in re.split(r'\n\s*\n', thread) if t.strip()]


def _fix_overlay(overlay: str) -> tuple[str, bool, bool]:
    """
    Apply hard rules for viral_overlay:
    - MAX 4 words
    - ALL UPPERCASE
    - No trailing punctuation

    Returns (fixed_overlay, was_truncated, was_uppercased).
    """
    was_truncated = False
    was_uppercased = False

    # Strip trailing punctuation
    cleaned = overlay.strip().rstrip('.,;:!?')

    # Truncate to 4 words
    words = cleaned.split()
    if len(words) > 4:
        cleaned = ' '.join(words[:4])
        was_truncated = True

    # Force UPPERCASE
    upper = cleaned.upper()
    if upper != cleaned:
        was_uppercased = True
        cleaned = upper

    return cleaned, was_truncated, was_uppercased


# ── Main entry points ─────────────────────────────────────────────────────

def clean_moment(moment: dict, *, expected_tweets: int = 7) -> ValidationStats:
    """
    Clean + validate a single moment dict in-place.
    Safe to call before Pydantic — only touches known keys.
    """
    stats = ValidationStats(moments_checked=1)
    moment_id = moment.get('hook', '<no-hook>')[:40]

    # 1. content_pieces
    cp = moment.get('content_pieces')
    if not isinstance(cp, dict):
        stats.problems.append(f"[{moment_id}] content_pieces missing or wrong type")
        return stats

    # ── twitter_thread ────────────────────────────────────────────────
    tw = cp.get('twitter_thread')
    if not tw or not isinstance(tw, str) or not tw.strip():
        stats.missing_twitter += 1
        stats.problems.append(f"[{moment_id}] missing twitter_thread")
    else:
        tw, n_links = _strip_links(tw)
        tw, n_prefix = _strip_tweet_prefixes(tw)
        stats.links_stripped += n_links
        stats.tweet_prefixes_stripped += n_prefix

        tweets = _split_tweets(tw)
        if len(tweets) != expected_tweets:
            stats.wrong_tweet_count += 1
            stats.problems.append(
                f"[{moment_id}] twitter_thread has {len(tweets)} tweets, expected {expected_tweets}"
            )

        for i, t in enumerate(tweets, 1):
            n = len(t)
            if n > 280:
                stats.tweets_too_long += 1
                stats.problems.append(f"[{moment_id}] tweet {i} too long: {n} chars")
            elif n < 100:
                stats.tweets_too_short += 1
                stats.problems.append(f"[{moment_id}] tweet {i} too short: {n} chars")

        stats.cliche_hits += _count_cliches(tw)
        cp['twitter_thread'] = tw

    # ── linkedin_post ─────────────────────────────────────────────────
    li = cp.get('linkedin_post')
    if not li or not isinstance(li, str) or not li.strip():
        stats.missing_linkedin += 1
        stats.problems.append(f"[{moment_id}] missing linkedin_post")
    else:
        li, n_links = _strip_links(li)
        stats.links_stripped += n_links
        n = len(li)
        if n < 500 or n > 1500:
            stats.linkedin_out_of_range += 1
            stats.problems.append(f"[{moment_id}] linkedin_post out of range: {n} chars (target 800-1200)")
        stats.cliche_hits += _count_cliches(li)
        cp['linkedin_post'] = li

    # ── tiktok_caption ────────────────────────────────────────────────
    tk = cp.get('tiktok_caption')
    if not tk and isinstance(moment.get('tiktok_package'), dict):
        # Sometimes the prompt only fills tiktok_package.caption — promote it
        tk = moment['tiktok_package'].get('caption')
    if not tk or not isinstance(tk, str) or not tk.strip():
        stats.missing_tiktok_caption += 1
        stats.problems.append(f"[{moment_id}] missing tiktok_caption")
    else:
        tk, n_links = _strip_links(tk)
        stats.links_stripped += n_links
        cp['tiktok_caption'] = tk

    # 2. viral_overlay (moment-level)
    overlay = moment.get('viral_overlay')
    if not overlay:
        # Fallback: tiktok_package.overlay_text
        pkg = moment.get('tiktok_package')
        if isinstance(pkg, dict):
            overlay = pkg.get('overlay_text')
    if overlay and isinstance(overlay, str):
        fixed, was_truncated, was_uppercased = _fix_overlay(overlay)
        if was_truncated:
            stats.overlay_truncated += 1
        if was_uppercased:
            stats.overlay_uppercased += 1
        moment['viral_overlay'] = fixed

    return stats


def moment_needs_copy_retry(stats: ValidationStats) -> bool:
    """True when copy should be regenerated (max 1 retry in caller)."""
    return stats.wrong_tweet_count > 0 or stats.linkedin_out_of_range > 0 or stats.cliche_hits > 0


def clean_moment_with_retry(
    moment: dict,
    *,
    expected_tweets: int = 7,
    regenerate_fn=None,
    max_retries: int = 1,
) -> ValidationStats:
    """
    Clean + validate a moment; optionally retry copy regeneration once.

    regenerate_fn(moment_dict) -> None should update content_pieces in-place.
    """
    stats = clean_moment(moment, expected_tweets=expected_tweets)
    retries = 0
    while (
        regenerate_fn
        and moment_needs_copy_retry(stats)
        and retries < max_retries
    ):
        retries += 1
        print(f"   🔄 Copy retry {retries}/{max_retries} for '{moment.get('hook', '')[:30]}'")
        regenerate_fn(moment)
        stats = clean_moment(moment, expected_tweets=expected_tweets)
    return stats


def clean_analysis(result_dict: dict, regenerate_fn=None, max_retries: int = 1) -> ValidationStats:
    """Apply clean_moment over all viral_moments. Returns aggregate stats."""
    agg = ValidationStats()
    moments = result_dict.get('viral_moments') or []
    if not isinstance(moments, list):
        return agg
    for m in moments:
        if isinstance(m, dict):
            if regenerate_fn:
                agg.merge(clean_moment_with_retry(
                    m,
                    regenerate_fn=regenerate_fn,
                    max_retries=max_retries,
                ))
            else:
                agg.merge(clean_moment(m))
    return agg


# ═══════════════════════════════════════════════════════════════════════════
# W6 — Fidelidad de hook y overlay contra el texto real del clip
# ═══════════════════════════════════════════════════════════════════════════
#
# Sin dependencias nuevas (nada de spaCy/nltk): stopwords español a mano.
# Lista corta a propósito — solo lo necesario para que "al menos una palabra
# con carga semántica" no cuente artículos/preposiciones/conjunciones como
# match válido.
SPANISH_STOPWORDS: frozenset[str] = frozenset({
    "a", "al", "algo", "algunas", "algunos", "ante", "antes", "aqui", "asi",
    "aun", "aunque", "bien", "cada", "casi", "como", "con", "contra", "cual",
    "cuando", "de", "del", "desde", "donde", "dos", "el", "él", "ella",
    "ellas", "ellos", "en", "entre", "era", "es", "esa", "esas", "ese",
    "esos", "esta", "estas", "este", "esto", "estos", "fue", "fueron", "ha",
    "hace", "hacia", "han", "hasta", "hay", "la", "las", "le", "les", "lo",
    "los", "mas", "más", "me", "mi", "mis", "mucho", "muy", "nada", "ni",
    "no", "nos", "nosotros", "nuestra", "nuestro", "o", "os", "otra",
    "otras", "otro", "otros", "para", "pero", "poco", "por", "porque",
    "pues", "que", "qué", "quien", "quién", "se", "sea", "segun", "según",
    "ser", "si", "sí", "sin", "sobre", "solo", "sólo", "somos", "son", "soy",
    "su", "sus", "tambien", "también", "tan", "te", "ti", "tiene", "tienen",
    "todo", "toda", "todos", "todas", "tu", "tú", "tus", "un", "una", "uno",
    "unos", "unas", "vamos", "van", "ver", "vez", "y", "ya", "yo",
})

# Velocidad de habla estimada — mismo orden de magnitud que la densidad
# plausible que usa worker/eval/eval_metrics.py (1.2-5.0 w/s). Se usa SOLO
# como proxy de "primeros 8 s" cuando la función que valida fidelidad recibe
# texto plano sin timestamps por palabra (generate_moment_copy_full recibe
# clip_text: str, no clip_words — main.py no se toca en este cambio).
WORDS_PER_SECOND_ESTIMATE = 2.8

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_token(word: str) -> str:
    """minúscula + sin acentos + sin puntuación — para comparar palabras."""
    word = _strip_accents(word.lower())
    word = _PUNCT_RE.sub("", word)
    return word.strip()


def tokenize(text: str) -> list[str]:
    """Palabras normalizadas en orden, vacías descartadas."""
    return [t for t in (normalize_token(w) for w in (text or "").split()) if t]


def content_words(text: str) -> list[str]:
    """Tokens normalizados sin stopwords — las que tienen "carga semántica"."""
    return [w for w in tokenize(text) if w not in SPANISH_STOPWORDS]


def clip_text_head_approx(text: str, seconds: float = 8.0) -> str:
    """
    Aproxima "los primeros N segundos" del clip a partir de la cantidad de
    palabras (sin timestamps por palabra disponibles acá). Si el clip es más
    corto que N segundos completos, devuelve el texto entero.
    """
    words = (text or "").split()
    n = max(1, round(seconds * WORDS_PER_SECOND_ESTIMATE))
    return " ".join(words[:n])


def split_sentences(text: str) -> list[str]:
    """Oraciones no vacías, separadas por . ! ? …"""
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def first_sentence(text: str) -> str:
    """
    Primera oración del clip. Sin puntuación de cierre (texto sin Whisper o
    con timestamps corridos), degrada al texto completo — es exactamente el
    comportamiento anterior a W6 (la Pasada B solo veía el texto entero).
    """
    sentences = split_sentences(text)
    return sentences[0] if sentences else (text or "").strip()


def last_sentence(text: str) -> str:
    """Última oración del clip. Misma degradación que `first_sentence`."""
    sentences = split_sentences(text)
    return sentences[-1] if sentences else (text or "").strip()


def overlay_is_faithful(overlay: str, clip_text_start: str) -> bool:
    """
    True si al menos una palabra con carga semántica del overlay aparece
    entre las palabras (normalizadas) de los primeros segundos del clip.
    """
    overlay_words = set(content_words(overlay))
    if not overlay_words:
        return False
    start_words = set(tokenize(clip_text_start))
    return bool(overlay_words & start_words)


def hook_is_faithful(hook: str, clip_text: str, *, max_miss_ratio: float = 0.25) -> bool:
    """
    Cobertura difusa por bolsa de palabras: al menos `1 - max_miss_ratio`
    de las palabras normalizadas del hook tienen que estar entre las del
    clip (multiset — cada palabra del clip cubre como máximo una palabra
    del hook, para que la repetición no infle el match). Tolera parafraseo
    leve (1 de cada 4 palabras puede no matchear literal) y, a propósito,
    NO exige orden: un buen parafraseo suele mover una cláusula al frente
    ("en el pit stop, el error del Ferrari..." en vez de "el error del
    Ferrari en el pit stop...") y sigue siendo 100% fiel. Reemplaza
    comparar substring exacto (que cualquier parafraseo rompe) sin
    necesitar un modelo de similaridad extra.
    """
    needle = tokenize(hook)
    if not needle:
        return False
    available = Counter(tokenize(clip_text))
    if not available:
        return False

    misses = 0
    for word in needle:
        if available[word] > 0:
            available[word] -= 1
        else:
            misses += 1

    return (misses / len(needle)) <= max_miss_ratio


def derive_overlay_from_text(text: str, *, max_words: int = 4) -> str:
    """
    Fallback determinístico cuando el modelo no logra un overlay fiel tras
    reintentar: las `max_words` palabras con carga semántica más salientes
    (más largas primero, orden original como desempate) de los primeros
    segundos del clip, en mayúsculas.
    """
    head = clip_text_head_approx(text)
    words = content_words(head)
    if not words:
        # Sin palabras de contenido (texto muy corto o puras stopwords):
        # cae a las primeras palabras crudas, mejor que un overlay vacío.
        raw = [normalize_token(w) or w for w in (text or "").split()[:max_words]]
        words = [w for w in raw if w]
    # Únicas, preservando la primera aparición, ordenadas por longitud desc.
    seen: dict[str, int] = {}
    for i, w in enumerate(words):
        seen.setdefault(w, i)
    ranked = sorted(seen, key=lambda w: (-len(w), seen[w]))[:max_words]
    # Presentar en el orden en que aparecen en el clip, no por longitud.
    ranked.sort(key=lambda w: seen[w])
    return " ".join(ranked).upper() if ranked else "MOMENTO DESTACADO"


# ═══════════════════════════════════════════════════════════════════════════
# W13 — Título que informa, no "Tema: ¡La verdad sobre X!"
# ═══════════════════════════════════════════════════════════════════════════
#
# docs/PLAN_CALIDAD.md §9 W10, etiquetas del 21-sep-2026: Agustín rechazó dos
# clips publicables porque "los títulos y la descripción no explican de qué
# hablan" (A-m11) / "las redacciones no son buenas" (A-m8). El patrón que se
# ve en la salida real es "Tema: ¡La verdad sobre X!" — dice el TEMA, no la
# afirmación concreta que el clip entrega. Mismo enfoque que W6 con
# hook/overlay: reglas duras + una fidelidad difusa contra el texto real,
# reintento único en generate_moment_copy_full, fallback determinístico acá.
TITLE_MAX_CHARS = 60
TITLE_MAX_EXCLAMATIONS = 1

# Fórmulas que describen el TEMA en vez de la afirmación concreta. Frases
# completas (no palabras sueltas) para no marcar falsos positivos como
# "peligro" o "verdad" usadas de otra forma.
_TITLE_FORBIDDEN_PHRASES = [
    "la verdad sobre",
    "la verdad de",
    "la verdad detrás",
    "el peligro de",
    "los peligros de",
    "lo que nadie te dice",
    "lo que nadie te cuenta",
    "nadie te dice esto",
    "esto es lo que",
    "no vas a creer",
    "el secreto de",
    "los secretos de",
    "lo que no sabias",
    "lo que no sabías",
    "lo que no te dijeron",
    "por que nadie habla de",
    "por qué nadie habla de",
]
_TITLE_FORBIDDEN_RE = re.compile(
    "|".join(re.escape(p) for p in _TITLE_FORBIDDEN_PHRASES), re.IGNORECASE
)


def title_has_forbidden_pattern(title: str) -> bool:
    """True si el título usa una fórmula de "tema" en vez de una afirmación
    concreta ("La verdad sobre...", "El peligro de...", etc.)."""
    return bool(_TITLE_FORBIDDEN_RE.search(title or ""))


def count_exclamations(title: str) -> int:
    """Cuenta signos de cierre "!" — un "¡...!" cuenta como 1 (lo normal en
    español), dos frases exclamativas separadas cuentan como 2."""
    return (title or "").count("!")


def title_is_faithful(title: str, clip_text: str) -> bool:
    """True si al menos una palabra con carga semántica del título aparece
    en el texto real del clip (misma lógica que `overlay_is_faithful`, pero
    contra el clip completo en vez de solo los primeros segundos: un título
    puede legítimamente citar el remate, no solo la apertura)."""
    title_words = set(content_words(title))
    if not title_words:
        return False
    clip_words = set(tokenize(clip_text))
    return bool(title_words & clip_words)


def title_is_valid(
    title: str | None, clip_text: str, *, max_chars: int = TITLE_MAX_CHARS
) -> tuple[bool, list[str]]:
    """
    Valida un título contra las 4 reglas de W13. Devuelve (ok, motivos) —
    motivos en {"vacio", "formula_prohibida", "demasiados_signos_exclamacion",
    "supera_60_caracteres", "no_fiel_al_texto"}.
    """
    if not title or not title.strip():
        return False, ["vacio"]
    reasons: list[str] = []
    if title_has_forbidden_pattern(title):
        reasons.append("formula_prohibida")
    if count_exclamations(title) > TITLE_MAX_EXCLAMATIONS:
        reasons.append("demasiados_signos_exclamacion")
    if len(title) > max_chars:
        reasons.append("supera_60_caracteres")
    if not title_is_faithful(title, clip_text):
        reasons.append("no_fiel_al_texto")
    return (len(reasons) == 0), reasons


def derive_title_from_text(text: str, *, max_chars: int = TITLE_MAX_CHARS) -> str:
    """
    Fallback determinístico cuando el título no pasa la validación (ni en el
    intento original ni en el reintento con corrección): la primera oración
    real del clip, recortada a `max_chars` sin partir una palabra a la mitad
    (mismo criterio que el resto de los fallbacks de esta sección: preferir
    texto real del clip a una plantilla genérica).
    """
    sentence = first_sentence(text).strip()
    if not sentence:
        return "Momento destacado"
    if len(sentence) <= max_chars:
        return sentence
    truncated = sentence[:max_chars]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(" ,;:—-")
