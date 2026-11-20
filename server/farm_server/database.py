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
                material_color TEXT,
                rack TEXT,
                rack_slot INTEGER,
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
                thumbnail_name TEXT,
                assigned_printer_name TEXT,
                required_material TEXT,
                required_color TEXT,
                estimated_seconds INTEGER,
                scheduling_message TEXT,
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

            CREATE TABLE IF NOT EXISTS material_catalog (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS color_catalog (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                hex_color TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS material_colors (
                material_code TEXT NOT NULL,
                color_code TEXT NOT NULL,
                PRIMARY KEY (material_code, color_code),
                FOREIGN KEY (material_code) REFERENCES material_catalog(code) ON DELETE CASCADE,
                FOREIGN KEY (color_code) REFERENCES color_catalog(code) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        default_materials = (
            ("1", "ASA", 1), ("2", "ABS", 2), ("3", "CPE HG100", 3),
            ("4", "CPE CF112", 4), ("5", "TPU 92 A", 5),
            ("6", "TPU 98 A", 6), ("7", "PLA", 7),
            ("0", "Dle domluvy", 8),
        )
        db.executemany(
            "INSERT OR IGNORE INTO material_catalog(code, name, sort_order) VALUES(?, ?, ?)",
            default_materials,
        )
        default_colors = (
            ("A", "Natural", "#DFC9A8", 1), ("B", "Traffic black", "#1D2024", 2),
            ("C", "Metallic gray", "#8A9198", 3), ("D", "Traffic white", "#F4F4F2", 4),
            ("E", "Traffic red", "#D92F2F", 5), ("F", "Traffic yellow", "#FFD229", 6),
            ("G", "Pistachio green", "#93C572", 7), ("H", "Sky blue", "#70C4E3", 8),
            ("J", "Vivid pink", "#F34D9B", 9), ("K", "Dijon mustard", "#C99B24", 10),
            ("L", "Green grass", "#49A942", 11), ("X", "Libovolná", "#7C5CFC", 12),
            ("Z", "Dle domluvy", "#B7BEC8", 13),
        )
        db.executemany(
            "INSERT OR IGNORE INTO color_catalog(code, name, hex_color, sort_order) VALUES(?, ?, ?, ?)",
            default_colors,
        )
        if db.execute("SELECT COUNT(*) FROM material_colors").fetchone()[0] == 0:
            db.execute(
                """INSERT INTO material_colors(material_code, color_code)
                   SELECT material_catalog.code, color_catalog.code
                   FROM material_catalog CROSS JOIN color_catalog"""
            )

        # Lightweight migration for databases created by an earlier prototype.
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "required_quantity" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN required_quantity INTEGER NOT NULL DEFAULT 1")
        if "status_message" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN status_message TEXT")
        if "thumbnail_name" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN thumbnail_name TEXT")
        if "assigned_printer_name" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN assigned_printer_name TEXT")
        if "required_material" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN required_material TEXT")
        if "required_color" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN required_color TEXT")
        if "estimated_seconds" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN estimated_seconds INTEGER")
        if "scheduling_message" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN scheduling_message TEXT")
        db.execute(
            """UPDATE jobs SET assigned_printer_name=(
                SELECT name FROM printers WHERE printers.id=jobs.printer_id
            ) WHERE assigned_printer_name IS NULL AND printer_id IS NOT NULL"""
        )
        printer_columns = {row[1] for row in db.execute("PRAGMA table_info(printers)")}
        if "ip_address" not in printer_columns:
            db.execute("ALTER TABLE printers ADD COLUMN ip_address TEXT")
        if "last_contact" not in printer_columns:
            db.execute("ALTER TABLE printers ADD COLUMN last_contact TEXT")
        if "material_color" not in printer_columns:
            db.execute("ALTER TABLE printers ADD COLUMN material_color TEXT")
        if "rack" not in printer_columns:
            db.execute("ALTER TABLE printers ADD COLUMN rack TEXT")
        if "rack_slot" not in printer_columns:
            db.execute("ALTER TABLE printers ADD COLUMN rack_slot INTEGER")

        positioned = {
            (row[0], row[1]) for row in db.execute(
                "SELECT rack, rack_slot FROM printers WHERE rack IS NOT NULL AND rack_slot IS NOT NULL"
            )
        }
        unpositioned = db.execute(
            "SELECT id FROM printers WHERE rack IS NULL OR rack_slot IS NULL ORDER BY created_at, name"
        ).fetchall()
        free_positions = (
            (rack, slot) for rack in ("A", "B", "C") for slot in range(1, 11)
            if (rack, slot) not in positioned
        )
        for printer, position in zip(unpositioned, free_positions):
            db.execute(
                "UPDATE printers SET rack=?, rack_slot=? WHERE id=?",
                (*position, printer[0]),
            )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_printers_rack_slot ON printers(rack, rack_slot)"
        )

