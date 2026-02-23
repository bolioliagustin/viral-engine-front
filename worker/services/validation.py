"""
Video validation utilities
"""

def validate_video_duration(duration_seconds: int, max_duration: int = 7200) -> None:
    """
    Validate video duration is within allowed limits.
    
    Args:
        duration_seconds: Video duration in seconds
        max_duration: Maximum allowed duration (default 2 hours)
        
    Raises:
        ValueError: If video exceeds max duration
    """
    if duration_seconds > max_duration:
        hours = max_duration / 3600
        raise ValueError(f"Video demasiado largo (máximo {hours:.1f} horas, tienes {duration_seconds/3600:.1f} horas)")


def validate_viral_moment_duration(moment, min_duration: int = 10) -> bool:
    """
    Validate that a viral moment meets minimum duration requirement.
    
    Args:
        moment: Viral moment object with start_time and end_time
        min_duration: Minimum duration in seconds (default 10s)
        
    Returns:
        True if valid, False if too short or missing timestamps
    """
    # Check if attributes exist
    if not hasattr(moment, 'start_time') or not hasattr(moment, 'end_time'):
        return False
    
    # Check if values are not None
    if moment.start_time is None or moment.end_time is None:
        return False
    
    duration = moment.end_time - moment.start_time
    return duration >= min_duration


def validate_durations(viral_moments: list, min_duration: int = 10) -> list:
    """
    Filter viral moments by minimum duration.
    
    Args:
        viral_moments: List of viral moment objects
        min_duration: Minimum duration in seconds
        
    Returns:
        Filtered list of moments that meet duration requirement
    """
    valid_moments = []
    for i, moment in enumerate(viral_moments):
        if validate_viral_moment_duration(moment, min_duration):
            valid_moments.append(moment)
        else:
            # Better error reporting
            start = getattr(moment, 'start_time', None)
            end = getattr(moment, 'end_time', None)
            hook = getattr(moment, 'hook', 'Unknown')[:30]
            
            if start is None or end is None:
                print(f"⚠️ Skipping moment {i+1} '{hook}' (missing timestamps: start={start}, end={end})")
            else:
                duration = end - start
                print(f"⚠️ Skipping moment {i+1} '{hook}' (too short: {duration}s < {min_duration}s)")
    
    return valid_moments
