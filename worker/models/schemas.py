from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional
import ast


class ContentPieces(BaseModel):
    """Generated content for different platforms.

    Fase 2 (two-pass): en la pasada A los momentos llegan SIN copy —
    twitter_thread pasa a Optional. La pasada B (post-Whisper, main.py)
    llena todos los campos desde el texto real del clip.
    """
    twitter_thread: Optional[str] = None
    linkedin_post: Optional[str] = None  # Optional for entertainment category
    tiktok_caption: Optional[str] = None  # For entertainment category
    short_video_script: Optional[str] = None  # DEPRECATED — kept for compat


class SurgicalClipping(BaseModel):
    """Flexible duration clip timing - replaces fixed 60s"""
    start_time: float  # Can include decimal (e.g., 71.5s)
    end_time: float
    duration: float
    reason: Optional[str] = None  # Why this specific timing


class TikTokPackage(BaseModel):
    """TikTok/Reels viral strategy (entertainment category)"""
    overlay_text: str  # Max 8 words to overlay on video
    caption: str  # 1-2 lines with slang + hashtags
    visual_hook: Optional[str] = None  # What happens in second 0
    visual_hook_description: Optional[str] = None  # Sprint 3: Describe what viewer sees in second 0
    sound_hook: Optional[str] = None  # Memorable audio bite


class EditingCue(BaseModel):
    """Specific editing instruction with timestamp"""
    time: float
    action: str  # "ZOOM", "SURGICAL START", "PUNCHLINE", etc.
    detail: str  # Description of what to do


class ViralScores(BaseModel):
    """Virality scores for a moment"""
    hook: int  # 1-10
    retention: int  # 1-10
    shareability: int  # 1-10

    @staticmethod
    def _coerce_score(value, default: int = 7) -> int:
        if value is None:
            return default
        try:
            return max(1, min(10, int(round(float(value)))))
        except (TypeError, ValueError):
            return default

    @model_validator(mode="before")
    @classmethod
    def fill_missing_metrics(cls, data):
        """Gemini a veces omite shareability (u otros) — no debe tumbar el job."""
        if data is None:
            return {"hook": 7, "retention": 7, "shareability": 7}
        if isinstance(data, str):
            try:
                data = ast.literal_eval(data)
            except (ValueError, SyntaxError):
                try:
                    import json
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    data = {}
        if not isinstance(data, dict):
            return {"hook": 7, "retention": 7, "shareability": 7}

        hook = data.get("hook")
        retention = data.get("retention")
        shareability = data.get("shareability")

        hook_i = cls._coerce_score(hook, default=7) if hook is not None else None
        retention_i = cls._coerce_score(retention, default=7) if retention is not None else None
        shareability_i = (
            cls._coerce_score(shareability, default=7) if shareability is not None else None
        )

        if shareability_i is None:
            if hook_i is not None and retention_i is not None:
                shareability_i = max(1, min(10, round((hook_i + retention_i) / 2)))
            elif retention_i is not None:
                shareability_i = retention_i
            elif hook_i is not None:
                shareability_i = hook_i
            else:
                shareability_i = 7

        if hook_i is None:
            hook_i = shareability_i
        if retention_i is None:
            retention_i = shareability_i

        return {
            "hook": hook_i,
            "retention": retention_i,
            "shareability": shareability_i,
        }


class ScoreJustification(BaseModel):
    """Explainable AI - Justification for a score"""
    metric: str  # "hook", "retention", "shareability"
    score: int  # 1-10
    reasoning: str  # Why this score was assigned
    improvement_tip: Optional[str] = None  # How to improve this metric


class Verification(BaseModel):
    """Verification keys to ensure AI didn't hallucinate content (Sprint 2)"""
    first_phrase_in_audio: str  # First 5-8 words of the clip
    last_phrase_in_audio: str  # Last 5-8 words of the clip
    # narrative_goal es solo informativo — algunos modelos lo omiten al
    # devolver el JSON. Lo dejamos opcional para no romper el job entero.
    narrative_goal: Optional[str] = None


class ViralMoment(BaseModel):
    """A viral moment identified in the video.

    Canonical schema (Phase 1.1 — unified Podcast + Business):
    - start_time / end_time are the ONLY source of truth for clip timing.
      surgical_clipping is kept Optional for backward-compat but migrated
      to flat fields in processor.py before validation.
    - content_pieces always contains twitter_thread, linkedin_post and
      tiktok_caption (each prompt is responsible for filling them).
    - tiktok_package is filled by every category (TikTok overlay is universal).
    """
    start_time: Optional[int] = None  # Canonical clip start (seconds)
    end_time: Optional[int] = None    # Canonical clip end (seconds)
    clipping_reason: Optional[str] = None  # Why this specific [start,end] range was chosen
    hook: str        # Viral hook/headline (long form, for thread/post)
    viral_overlay: Optional[str] = None  # MAX 4 words UPPERCASE — burnt onto vertical video
    emotional_trigger: str  # Why this moment is viral-worthy
    pillar_type: Optional[str] = None  # 'authority' | 'utility' | 'connection' | 'entertainment'
    category: Optional[str] = None  # 'podcast' | 'business' (canonical) — anything else falls back to business
    scores: Optional[ViralScores] = None
    score_justifications: Optional[List[ScoreJustification]] = None  # Explainable scores
    roi_time_saved: Optional[int] = None  # Minutes saved vs manual creation
    sentiment_detected: Optional[str] = None  # "sarcastic" | "serious" | "motivational" | "casual"
    time_saved_estimate: Optional[int] = None  # DEPRECATED — use roi_time_saved
    # Back-compat fields (still accepted, migrated to canonical fields pre-validation)
    surgical_clipping: Optional[SurgicalClipping] = None
    tiktok_package: Optional[TikTokPackage] = None  # Universal TikTok strategy
    editing_cues: Optional[List[EditingCue]] = None  # Informational only
    content_pieces: ContentPieces = Field(default_factory=ContentPieces)
    # Fase 4: flag de verificación anti-alucinación (first Y last phrase fallaron)
    verification_failed: Optional[bool] = None
    # Sprint 2: Fidelity & Verification
    verification: Optional[Verification] = None  # Validates AI didn't hallucinate
    
    # Validators to convert float to int for timestamps
    @field_validator('start_time', 'end_time', mode='before')
    @classmethod
    def convert_timestamp_to_int(cls, v):
        if v is None:
            return v
        if isinstance(v, float):
            return int(round(v))
        return v


class AnalysisResult(BaseModel):
    """Complete analysis result from AI"""
    video_title: str
    summary: str
    main_topics: Optional[List[str]] = None
    viral_moments: List[ViralMoment]
    overall_virality_score: Optional[float] = None  # 1-10 (can be decimal)
    total_roi_minutes: Optional[int] = None  # Phase B: Total time saved across all moments
