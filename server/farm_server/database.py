from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def data_dir() -> Path:
    path = Path(os.getenv("FARM_DATA_DIR", "./data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "farm.db"


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(database_path())
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS printers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                api_key_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'offline',
                progress REAL NOT NULL DEFAULT 0,
                hotend_current REAL,
                hotend_target REAL,
                bed_current REAL,
                bed_target REAL,
                material TEXT,
                last_seen TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                printer_id TEXT,
                queue_position INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                status_message TEXT,
                FOREIGN KEY (printer_id) REFERENCES printers(id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_queue
            ON jobs(status, queue_position);

            CREATE TABLE IF NOT EXISTS printer_commands (
                id TEXT PRIMARY KEY,
                printer_id TEXT NOT NULL,
                command TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                acknowledged_at TEXT,
                FOREIGN KEY (printer_id) REFERENCES printers(id)
            );
            """
        )

        # Lightweight migration for databases created by an earlier prototype.
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "status_message" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN status_message TEXT")

