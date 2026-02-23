"""
Migration script to add current_step and progress_percentage columns to jobs table
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "backend" / "data" / "app.db"

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(jobs)")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Add current_step column if it doesn't exist
        if 'current_step' not in columns:
            print("Adding current_step column...")
            cursor.execute("ALTER TABLE jobs ADD COLUMN current_step TEXT")
            print("✅ current_step column added")
        else:
            print("ℹ️  current_step column already exists")
        
        # Add progress_percentage column if it doesn't exist
        if 'progress_percentage' not in columns:
            print("Adding progress_percentage column...")
            cursor.execute("ALTER TABLE jobs ADD COLUMN progress_percentage INTEGER")
            print("✅ progress_percentage column added")
        else:
            print("ℹ️  progress_percentage column already exists")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
