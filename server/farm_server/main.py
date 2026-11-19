from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import connection, data_dir, initialize
from .schemas import JobStateUpdate, MaterialCatalogCreate, MaterialUpdate, PreheatRequest, PrinterCreate, PrinterRegistered, Telemetry


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
            occupied = {
                (row[0], row[1]) for row in db.execute(
                    "SELECT rack, rack_slot FROM printers WHERE rack IS NOT NULL"
                )
            }
            position = next(
                ((rack, slot) for rack in ("A", "B", "C") for slot in range(1, 11)
                 if (rack, slot) not in occupied),
                None,
            )
            if position is None:
                raise HTTPException(409, "V regálech není volná pozice")
            db.execute(
                "INSERT INTO printers(id, name, api_key_hash, rack, rack_slot, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (printer_id, payload.name.strip(), key_hash(api_key), *position, now()),
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
            "bed_current, bed_target, material, material_color, rack, rack_slot, last_seen, created_at "
            "FROM printers ORDER BY rack, rack_slot"
        ).fetchall()
    return [dict(row) for row in rows]


def require_printer_exists(printer_id: str):
    with connection() as db:
        row = db.execute("SELECT id, name FROM printers WHERE id=?", (printer_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Printer not found")
    return row


@app.delete("/api/printers/{printer_id}", dependencies=[Depends(require_admin)], status_code=204)
def delete_printer(printer_id: str):
    require_printer_exists(printer_id)
    with connection() as db:
        db.execute("DELETE FROM printer_commands WHERE printer_id=?", (printer_id,))
        db.execute("UPDATE jobs SET printer_id=NULL WHERE printer_id=?", (printer_id,))
        db.execute("DELETE FROM printers WHERE id=?", (printer_id,))
    return Response(status_code=204)


@app.get("/api/materials", dependencies=[Depends(require_admin)])
def list_materials():
    with connection() as db:
        rows = db.execute(
            "SELECT code, name FROM material_catalog ORDER BY sort_order, name"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/materials", dependencies=[Depends(require_admin)], status_code=201)
def create_material(payload: MaterialCatalogCreate):
    code, name = payload.code.strip(), payload.name.strip()
    try:
        with connection() as db:
            order = db.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM material_catalog"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO material_catalog(code, name, sort_order) VALUES(?, ?, ?)",
                (code, name, order),
            )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "Kód nebo název materiálu již existuje") from exc
        raise
    return {"code": code, "name": name}


@app.patch("/api/printers/{printer_id}/material", dependencies=[Depends(require_admin)])
def set_material(printer_id: str, payload: MaterialUpdate):
    require_printer_exists(printer_id)
    with connection() as db:
        db.execute(
            "UPDATE printers SET material=?, material_color=? WHERE id=?",
            (payload.material.strip(), payload.color.strip(), printer_id),
        )
    return {"accepted": True, "material": payload.material.strip(), "color": payload.color.strip()}


@app.post("/api/printers/{printer_id}/preheat", dependencies=[Depends(require_admin)], status_code=202)
def preheat(printer_id: str, payload: PreheatRequest):
    require_printer_exists(printer_id)
    command_id = str(uuid.uuid4())
    with connection() as db:
        db.execute(
            "INSERT INTO printer_commands(id, printer_id, command, payload, created_at) VALUES(?, ?, ?, ?, ?)",
            (command_id, printer_id, "preheat", json.dumps(payload.model_dump()), now()),
        )
    return {"accepted": True, "command_id": command_id}


@app.get("/api/printers/{printer_id}/history", dependencies=[Depends(require_admin)])
def printer_history(printer_id: str):
    require_printer_exists(printer_id)
    with connection() as db:
        rows = db.execute(
            """SELECT id, original_name, size_bytes, status, created_at, started_at,
               finished_at, status_message FROM jobs WHERE printer_id=?
               ORDER BY COALESCE(finished_at, started_at, created_at) DESC LIMIT 100""",
            (printer_id,),
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


@app.post("/api/printer/next-job")
def claim_next_job(printer=Depends(require_printer)):
    """Atomically assign the oldest queued job to this printer."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute(
            """SELECT id, original_name, size_bytes, status FROM jobs
               WHERE printer_id=? AND status NOT IN ('complete', 'error')
               ORDER BY queue_position LIMIT 1""",
            (printer["id"],),
        ).fetchone()
        if active is None:
            active = db.execute(
                """SELECT id, original_name, size_bytes, status FROM jobs
                   WHERE status='queued' AND printer_id IS NULL
                   ORDER BY queue_position, created_at LIMIT 1"""
            ).fetchone()
            if active is not None:
                db.execute(
                    "UPDATE jobs SET printer_id=?, status='assigned' WHERE id=?",
                    (printer["id"], active["id"]),
                )
                db.execute(
                    "UPDATE printers SET status='queued', last_seen=? WHERE id=?",
                    (now(), printer["id"]),
                )
        if active is None:
            db.execute(
                "UPDATE printers SET last_seen=? WHERE id=?",
                (now(), printer["id"]),
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    return {
        "id": active["id"],
        "name": active["original_name"],
        "size_bytes": active["size_bytes"],
        "status": active["status"] if active["status"] != "queued" else "assigned",
        "download_url": f"/api/printer/jobs/{active['id']}/file",
    }


@app.get("/api/printer/jobs/{job_id}/file")
def download_job(job_id: str, printer=Depends(require_printer)):
    with connection() as db:
        job = db.execute(
            """SELECT original_name, stored_name FROM jobs
               WHERE id=? AND printer_id=?""",
            (job_id, printer["id"]),
        ).fetchone()
    if job is None:
        raise HTTPException(404, "Job not assigned to this printer")
    path = data_dir() / "jobs" / job["stored_name"]
    if not path.is_file():
        raise HTTPException(410, "G-code file is missing")
    return FileResponse(path, filename=job["original_name"], media_type="text/x.gcode")


@app.post("/api/printer/jobs/{job_id}/state")
def update_job_state(
    job_id: str,
    payload: JobStateUpdate,
    printer=Depends(require_printer),
):
    with connection() as db:
        job = db.execute(
            "SELECT id FROM jobs WHERE id=? AND printer_id=?",
            (job_id, printer["id"]),
        ).fetchone()
        if job is None:
            raise HTTPException(404, "Job not assigned to this printer")

        started_at = now() if payload.state == "printing" else None
        finished_at = now() if payload.state in ("complete", "error") else None
        db.execute(
            """UPDATE jobs SET status=?, status_message=?,
               started_at=COALESCE(started_at, ?),
               finished_at=COALESCE(finished_at, ?)
               WHERE id=?""",
            (payload.state, payload.message, started_at, finished_at, job_id),
        )
        db.execute(
            "UPDATE printers SET status=?, progress=?, last_seen=? WHERE id=?",
            (payload.state, payload.progress, now(), printer["id"]),
        )
    return {"accepted": True, "job_id": job_id, "state": payload.state}


@app.post("/api/jobs", dependencies=[Depends(require_admin)], status_code=201)
def upload_job(file: UploadFile = File(...), printer_id: str | None = Form(default=None)):
    original_name = Path(file.filename or "").name
    if not original_name.lower().endswith((".gcode", ".gco", ".gc")):
        raise HTTPException(415, "Only G-code files are accepted")

    if printer_id:
        require_printer_exists(printer_id)

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
        job_status = "assigned" if printer_id else "queued"
        db.execute(
            """INSERT INTO jobs(id, original_name, stored_name, size_bytes,
               status, printer_id, queue_position, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, original_name, stored_name, size, job_status, printer_id, position, now()),
        )
        if printer_id:
            db.execute("UPDATE printers SET status='queued' WHERE id=?", (printer_id,))
    return {"id": job_id, "name": original_name, "size_bytes": size,
            "queue_position": position, "printer_id": printer_id, "status": job_status}


@app.get("/api/jobs", dependencies=[Depends(require_admin)])
def list_jobs():
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM jobs ORDER BY queue_position, created_at"
        ).fetchall()
    return [dict(row) for row in rows]


dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

