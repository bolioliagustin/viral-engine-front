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
"""
from __future__ import annotations
import re
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


def clean_analysis(result_dict: dict) -> ValidationStats:
    """Apply clean_moment over all viral_moments. Returns aggregate stats."""
    agg = ValidationStats()
    moments = result_dict.get('viral_moments') or []
    if not isinstance(moments, list):
        return agg
    for m in moments:
        if isinstance(m, dict):
            agg.merge(clean_moment(m))
    return agg
