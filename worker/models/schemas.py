from pydantic import BaseModel, field_validator
from typing import List, Optional


class ContentPieces(BaseModel):
    """Generated content for different platforms"""
    twitter_thread: str
    linkedin_post: Optional[str] = None  # Optional for entertainment category
    tiktok_caption: Optional[str] = None  # For entertainment category
    short_video_script: Optional[str] = None


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
    content_pieces: ContentPieces
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
