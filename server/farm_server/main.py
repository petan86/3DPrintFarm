from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status

from .database import connection, data_dir, initialize
from .schemas import PrinterCreate, PrinterRegistered, Telemetry


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_admin(x_admin_key: str = Header(default="")) -> None:
    expected = os.getenv("FARM_ADMIN_KEY", "")
    if not expected:
        raise HTTPException(503, "FARM_ADMIN_KEY is not configured")
    if not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin key")


def require_printer(x_printer_key: str = Header(default="")):
    if not x_printer_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing printer key")
    with connection() as db:
        row = db.execute(
            "SELECT * FROM printers WHERE api_key_hash = ?",
            (key_hash(x_printer_key),),
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid printer key")
    return dict(row)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(title="3DPrintFarm API", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": now()}


@app.post(
    "/api/printers",
    response_model=PrinterRegistered,
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_201_CREATED,
)
def register_printer(payload: PrinterCreate):
    printer_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)
    try:
        with connection() as db:
            db.execute(
                "INSERT INTO printers(id, name, api_key_hash, created_at) VALUES(?, ?, ?, ?)",
                (printer_id, payload.name.strip(), key_hash(api_key), now()),
            )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "Printer name already exists") from exc
        raise
    return PrinterRegistered(id=printer_id, name=payload.name.strip(), api_key=api_key)


@app.get("/api/printers", dependencies=[Depends(require_admin)])
def list_printers():
    with connection() as db:
        rows = db.execute(
            "SELECT id, name, status, progress, hotend_current, hotend_target, "
            "bed_current, bed_target, material, last_seen, created_at "
            "FROM printers ORDER BY name"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/printer/telemetry")
def update_telemetry(payload: Telemetry, printer=Depends(require_printer)):
    with connection() as db:
        db.execute(
            """UPDATE printers SET status=?, progress=?, hotend_current=?,
               hotend_target=?, bed_current=?, bed_target=?, material=?, last_seen=?
               WHERE id=?""",
            (
                payload.status, payload.progress, payload.hotend_current,
                payload.hotend_target, payload.bed_current, payload.bed_target,
                payload.material, now(), printer["id"],
            ),
        )
    return {"accepted": True}


@app.post("/api/jobs", dependencies=[Depends(require_admin)], status_code=201)
def upload_job(file: UploadFile = File(...)):
    original_name = Path(file.filename or "").name
    if not original_name.lower().endswith((".gcode", ".gco", ".gc")):
        raise HTTPException(415, "Only G-code files are accepted")

    job_id = str(uuid.uuid4())
    stored_name = f"{job_id}.gcode"
    jobs_dir = data_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    destination = jobs_dir / stored_name

    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    size = destination.stat().st_size
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "G-code file is empty")

    with connection() as db:
        position = db.execute(
            "SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs"
        ).fetchone()[0]
        db.execute(
            """INSERT INTO jobs(id, original_name, stored_name, size_bytes,
               queue_position, created_at) VALUES(?, ?, ?, ?, ?, ?)""",
            (job_id, original_name, stored_name, size, position, now()),
        )
    return {"id": job_id, "name": original_name, "size_bytes": size, "queue_position": position}


@app.get("/api/jobs", dependencies=[Depends(require_admin)])
def list_jobs():
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM jobs ORDER BY queue_position, created_at"
        ).fetchall()
    return [dict(row) for row in rows]

