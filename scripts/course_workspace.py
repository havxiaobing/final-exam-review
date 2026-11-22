"""Directory-bound confirmation for a course workspace."""
import os
from pathlib import Path
import sqlite3

DB_NAME = "learning-records.sqlite3"


def canonical_course(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("--course-dir must be an absolute path")
    return path.resolve()


def identity(path):
    return os.path.normcase(str(canonical_course(path)))


def connect_existing(course, readonly=False):
    db = canonical_course(course) / DB_NAME
    if not db.is_file():
        raise ValueError("Course records are missing; select a course directory and initialize it with init --file PROFILE --confirm-storage.")
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(db.as_uri() + f"?mode={mode}", uri=True, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def require_confirmed(course):
    conn = connect_existing(course, readonly=True)
    try:
        try:
            row = conn.execute("SELECT path FROM storage_confirmation WHERE singleton=1").fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            row = None
        if not row or row[0] != identity(course):
            raise ValueError("This exact course directory is not confirmed. After the user selects it, use init --file PROFILE --confirm-storage for a new course or confirm-storage for an existing course.")
    finally:
        conn.close()


def record_confirmation(conn, course):
    conn.execute("CREATE TABLE IF NOT EXISTS storage_confirmation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), path TEXT NOT NULL)")
    conn.execute("INSERT OR REPLACE INTO storage_confirmation (singleton, path) VALUES (1, ?)", (identity(course),))
