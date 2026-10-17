"""Database backup script for Debug Dojo.

Creates an ISO-timestamped, transactionally-safe backup copy of the SQLite
database inside the git-ignored backups/ directory using SQLite's online
backup API, and verifies database integrity.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings


def backup_database() -> Path:
    """Safely back up the SQLite database to backups/ with an ISO timestamp.

    Returns:
        Path: Destination path of the verified database backup.
    """
    settings = get_settings()
    src_path = Path(settings.DATABASE_PATH)
    if not src_path.is_absolute():
        src_path = PROJECT_ROOT / src_path

    if not src_path.exists():
        raise FileNotFoundError(f"Source database not found at {src_path}")

    backups_dir = PROJECT_ROOT / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    # ISO 8601 compact timestamp suitable for filenames across OSes
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup_file = backups_dir / f"debugdojo_{timestamp}.db"

    # Use SQLite's online backup API to ensure consistent checkpointing
    src_conn = sqlite3.connect(str(src_path))
    try:
        dst_conn = sqlite3.connect(str(backup_file))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # Verify backup integrity
    verify_conn = sqlite3.connect(str(backup_file))
    try:
        cur = verify_conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        row = cur.fetchone()
        integrity_status = row[0] if row else "unknown"
    finally:
        verify_conn.close()

    if integrity_status != "ok":
        raise RuntimeError(f"Database backup integrity check failed: {integrity_status}")

    file_size = backup_file.stat().st_size
    print(f"=== Database Backup Successful ===")
    print(f"Source:      {src_path}")
    print(f"Backup file: {backup_file}")
    print(f"Size:        {file_size:,} bytes")
    print(f"Timestamp:   {now.isoformat()}")
    print(f"Integrity:   {integrity_status}")

    return backup_file


if __name__ == "__main__":
    try:
        backup_database()
        sys.exit(0)
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        sys.exit(1)
