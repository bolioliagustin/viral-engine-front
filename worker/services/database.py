import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent.parent / "backend" / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    """Get SQLite connection with WAL mode enabled"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('PRAGMA journal_mode=WAL')
    conn.row_factory = sqlite3.Row
    return conn


def update_job_status(job_id: str, status: str) -> None:
    """Update job status"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, job_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_job_progress(
    job_id: str, 
    current_step: Optional[str] = None, 
    progress_percentage: Optional[int] = None
) -> None:
    """Update job processing step and progress for real-time UI updates
    
    Args:
        job_id: The job ID to update
        current_step: Current processing step (e.g., 'downloading', 'transcribing', 'analyzing', 'clipping', 'generating')
        progress_percentage: Progress from 0-100
    """
    conn = get_connection()
    try:
        updates = []
        params = []
        
        if current_step is not None:
            updates.append("current_step = ?")
            params.append(current_step)
        
        if progress_percentage is not None:
            updates.append("progress_percentage = ?")
            params.append(progress_percentage)
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(job_id)
            
            query = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
            conn.execute(query, params)
            conn.commit()
    finally:
        conn.close()


def update_job_error(job_id: str, error_message: str) -> None:
    """Mark job as failed with error message"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (error_message, job_id)
        )
        conn.commit()
    finally:
        conn.close()


def save_content_result(job_id: str, content_type: str, content: str, metadata: Optional[str] = None) -> str:
    """Save a content result for a job"""
    import uuid
    result_id = str(uuid.uuid4())
    
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO content_results (id, job_id, type, content, metadata) VALUES (?, ?, ?, ?, ?)",
            (result_id, job_id, content_type, content, metadata)
        )
        conn.commit()
    finally:
        conn.close()
    
    return result_id


def get_job(job_id: str) -> Optional[dict]:
    """Get job by ID"""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()
