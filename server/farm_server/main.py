from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import binascii
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import connection, data_dir, initialize
from .schemas import AdminPasswordChange, GuestAccess, JobMaterialUpdate, JobQuantityUpdate, JobUpdate
from .schemas import DEFAULT_HOTSPOT_SSID, ColorCatalogCreate, HotspotSettings, JobStateUpdate, MaterialCatalogCreate, MaterialColorsUpdate, MaterialUpdate, PreheatRequest, PrinterCreate, PrinterRegistered, Telemetry


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


THUMBNAIL_BEGIN = re.compile(r"^;\s*thumbnail(?:_PNG|_JPG|_JPEG)?\s+begin\s+(\d+)x(\d+)\s+\d+", re.IGNORECASE)


def extract_thumbnail(gcode_path: Path, job_id: str) -> str | None:
    """Extract the largest embedded Orca/Prusa-style PNG or JPEG thumbnail."""
    candidates: list[tuple[int, str]] = []
    width = height = 0
    encoded: list[str] | None = None
    with gcode_path.open("r", encoding="utf-8", errors="ignore") as source:
        for line_number, line in enumerate(source):
            if line_number > 250_000:
                break
            match = THUMBNAIL_BEGIN.match(line.strip())
            if match:
                width, height = int(match.group(1)), int(match.group(2))
                encoded = []
                continue
            if encoded is not None and re.match(r"^;\s*thumbnail(?:_PNG|_JPG|_JPEG)?\s+end", line.strip(), re.IGNORECASE):
                candidates.append((width * height, "".join(encoded)))
                encoded = None
                continue
            if encoded is not None:
                part = line.strip().removeprefix(";").strip()
                if part and len(part) <= 256:
                    encoded.append(part)
                if sum(map(len, encoded)) > 8_000_000:
                    encoded = None

    previews_dir = data_dir() / "previews"
    for _, value in sorted(candidates, reverse=True):
        try:
            image = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(image) > 6_000_000:
            continue
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = "png"
        elif image.startswith(b"\xff\xd8\xff"):
            extension = "jpg"
        else:
            continue
        previews_dir.mkdir(parents=True, exist_ok=True)
        name = f"{job_id}.{extension}"
        (previews_dir / name).write_bytes(image)
        return name
    return None


def extract_gcode_metadata(gcode_path: Path) -> tuple[str | None, int | None]:
    filament = None
    estimated = None
    with gcode_path.open("r", encoding="utf-8", errors="ignore") as source:
        for line in source:
            clean = line.strip().lstrip(";").strip()
            if filament is None:
                match = re.match(r"filament_type\s*=\s*([^,;]+)", clean, re.IGNORECASE)
                if match:
                    filament = match.group(1).strip()
            if estimated is None and "estimated printing time" in clean.lower():
                value = clean.split("=", 1)[-1]
                hours = re.search(r"(\d+)h", value)
                minutes = re.search(r"(\d+)m", value)
                seconds = re.search(r"(\d+)s", value)
                estimated = (int(hours.group(1)) * 3600 if hours else 0) + (int(minutes.group(1)) * 60 if minutes else 0) + (int(seconds.group(1)) if seconds else 0)
    return filament, estimated or None


def schedule_buffer() -> None:
    """Reserve one compatible upcoming job for printers in earliest-finish order."""
    with connection() as db:
        queued = db.execute(
            "SELECT id, required_material, required_color FROM jobs WHERE status='queued' AND printer_id IS NULL ORDER BY queue_position"
        ).fetchall()
        for job in queued:
            printers = db.execute(
                """SELECT p.id, p.name, p.material, p.material_color, p.status, p.progress, p.last_seen,
                   COALESCE(active.estimated_seconds, 0) estimated_seconds
                   FROM printers p LEFT JOIN jobs active ON active.printer_id=p.id
                     AND active.status IN ('heating','leveling','waiting_local_approval','printing','paused')
                   WHERE p.last_seen IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM jobs future WHERE future.printer_id=p.id
                       AND future.status IN ('assigned','downloading','ready'))"""
            ).fetchall()
            online = [p for p in printers if (datetime.now(timezone.utc) - datetime.fromisoformat(p["last_seen"])).total_seconds() <= 30]
            compatible = [
                p for p in online
                if (not job["required_material"] or p["material"] == job["required_material"])
                and (not job["required_color"] or p["material_color"] == job["required_color"])
            ]
            if not compatible:
                restricted = job["required_material"] or job["required_color"]
                message = "Čeká na tiskárnu se správným filamentem" if restricted else "Čeká na dostupnou tiskárnu"
                db.execute("UPDATE jobs SET scheduling_message=? WHERE id=?", (message, job["id"]))
                continue
            def remaining(printer):
                if printer["status"] in ("idle", "complete"):
                    return 0
                estimate = printer["estimated_seconds"] or 10**9
                return estimate * max(0, 100 - (printer["progress"] or 0)) / 100
            printer = min(compatible, key=remaining)
            db.execute(
                """UPDATE jobs SET printer_id=?, assigned_printer_name=?, status='assigned',
                   scheduling_message='Naplánováno podle nejkratšího zbývajícího času' WHERE id=?""",
                (printer["id"], printer["name"], job["id"]),
            )


ADMIN_PASSWORD_SETTING = "admin_password_hash"
PBKDF2_ITERATIONS = 200_000
# Verified passwords by stored hash, so the dashboard's frequent polling does
# not pay for PBKDF2 on every request. Holds only SHA-256 digests.
_verified_admin: dict[str, str] = {}


def hash_admin_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def admin_password_matches(password: str) -> bool:
    """Checks the password set in Settings, or FARM_ADMIN_KEY while none is set."""
    with connection() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (ADMIN_PASSWORD_SETTING,)).fetchone()
    if row is None:
        expected = os.getenv("FARM_ADMIN_KEY", "")
        if not expected:
            raise HTTPException(503, "FARM_ADMIN_KEY is not configured")
        return hmac.compare_digest(password, expected)
    stored = row[0]
    quick = hashlib.sha256(password.encode("utf-8")).hexdigest()
    if hmac.compare_digest(_verified_admin.get(stored, ""), quick):
        return True
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    if hmac.compare_digest(candidate.hex(), digest):
        _verified_admin.clear()
        _verified_admin[stored] = quick
        return True
    return False


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not x_admin_key or not admin_password_matches(x_admin_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin key")


def guest_access_enabled() -> bool:
    with connection() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key='guest_enabled'").fetchone()
    return row is None or row[0] == "1"


def require_viewer(x_admin_key: str = Header(default=""), x_guest: str = Header(default="")) -> None:
    """Read-only endpoints: the admin, or a guest while guest access is enabled."""
    if x_admin_key:
        require_admin(x_admin_key)
        return
    if x_guest == "1" and guest_access_enabled():
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")


def require_printer(request: Request, x_printer_key: str = Header(default="")):
    if not x_printer_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing printer key")
    with connection() as db:
        row = db.execute(
            "SELECT * FROM printers WHERE api_key_hash = ?",
            (key_hash(x_printer_key),),
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid printer key")
        db.execute(
            "UPDATE printers SET ip_address=?, last_contact=? WHERE id=?",
            (request.client.host if request.client else None, now(), row["id"]),
        )
    return dict(row)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    with connection() as db:
        for job in db.execute("SELECT id, stored_name FROM jobs WHERE thumbnail_name IS NULL").fetchall():
            source = data_dir() / "jobs" / job["stored_name"]
            if source.is_file():
                thumbnail = extract_thumbnail(source, job["id"])
                if thumbnail:
                    db.execute("UPDATE jobs SET thumbnail_name=? WHERE id=?", (thumbnail, job["id"]))
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
            existing_name = db.execute(
                "SELECT rack, rack_slot FROM printers WHERE name=?", (payload.name.strip(),)
            ).fetchone()
            if existing_name:
                position_name = f"{existing_name['rack']}{existing_name['rack_slot']:02d}"
                raise HTTPException(409, f"Tento název už používá tiskárna na pozici {position_name}")
            occupied = {
                (row[0], row[1]) for row in db.execute(
                    "SELECT rack, rack_slot FROM printers WHERE rack IS NOT NULL"
                )
            }
            if (payload.rack is None) != (payload.rack_slot is None):
                raise HTTPException(422, "Regál a pozice musí být zadány společně")
            requested = (payload.rack, payload.rack_slot) if payload.rack else None
            if requested and requested in occupied:
                raise HTTPException(409, "Vybraná pozice je již obsazená")
            position = requested or next(
                ((rack, slot) for rack in ("A", "B", "C") for slot in range(1, 11)
                 if (rack, slot) not in occupied), None,
            )
            if position is None:
                raise HTTPException(409, "V regálech není volná pozice")
            db.execute(
                "INSERT INTO printers(id, name, api_key_hash, rack, rack_slot, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (printer_id, payload.name.strip(), key_hash(api_key), *position, now()),
            )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "Tiskárna s tímto názvem již existuje") from exc
        raise
    return PrinterRegistered(id=printer_id, name=payload.name.strip(), api_key=api_key)


@app.get("/api/printers", dependencies=[Depends(require_viewer)])
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


HOTSPOT_CONNECTION = "PrintFarm-Hotspot"


def hotspot_apply_enabled() -> bool:
    return os.getenv("FARM_HOTSPOT_APPLY", "0") == "1"


def run_nmcli(*args: str, timeout: int = 20, check: bool = True) -> subprocess.CompletedProcess:
    """Run nmcli without a shell; errors never echo the command line (it may contain the Wi-Fi password)."""
    try:
        result = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise HTTPException(500, "Hotspot se nepodařilo aplikovat: nmcli není dostupné") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "Hotspot se nepodařilo aplikovat: NetworkManager neodpověděl včas") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"nmcli skončilo s kódem {result.returncode}"
        raise HTTPException(500, f"Hotspot se nepodařilo aplikovat: {reason}")
    return result


def apply_hotspot(ssid: str, password: str, enabled: bool) -> str:
    """Apply hotspot settings on the host through NetworkManager (Raspberry Pi OS Bookworm).

    Only runs when FARM_HOTSPOT_APPLY=1; otherwise the settings are just stored, so the
    form also works in a local preview or in a container without access to the host network.
    """
    if not hotspot_apply_enabled():
        return "saved_not_applied"
    interface = os.getenv("FARM_HOTSPOT_INTERFACE", "wlan0")
    exists = run_nmcli("-t", "-f", "NAME", "connection", "show", HOTSPOT_CONNECTION, timeout=10, check=False).returncode == 0
    if not exists:
        run_nmcli("connection", "add", "type", "wifi", "ifname", interface,
                  "con-name", HOTSPOT_CONNECTION, "autoconnect", "no", "ssid", ssid)
    run_nmcli("connection", "modify", HOTSPOT_CONNECTION,
              "connection.interface-name", interface,
              "connection.autoconnect", "yes" if enabled else "no",
              "802-11-wireless.ssid", ssid, "802-11-wireless.mode", "ap",
              "802-11-wireless.band", "bg", "ipv4.method", "shared",
              "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password)
    if enabled:
        run_nmcli("connection", "up", HOTSPOT_CONNECTION, timeout=45)
        return "active"
    active = run_nmcli("-t", "-f", "NAME", "connection", "show", "--active", timeout=10, check=False).stdout.splitlines()
    if HOTSPOT_CONNECTION in active:
        run_nmcli("connection", "down", HOTSPOT_CONNECTION, timeout=30)
    return "disabled"


@app.get("/api/settings/devices", dependencies=[Depends(require_admin)])
def get_connected_devices():
    with connection() as db:
        rows = db.execute(
            "SELECT id, name, rack, rack_slot, ip_address, last_contact FROM printers ORDER BY name"
        ).fetchall()
    timestamp = datetime.now(timezone.utc)
    devices = []
    for row in rows:
        device = dict(row)
        device["online"] = bool(row["last_contact"] and
            (timestamp - datetime.fromisoformat(row["last_contact"])).total_seconds() <= 30)
        devices.append(device)
    return {"devices": devices}


@app.get("/api/auth/guest")
def get_guest_access():
    """Public: tells the login screen whether to offer guest access."""
    return {"enabled": guest_access_enabled()}


@app.put("/api/settings/guest", dependencies=[Depends(require_admin)])
def set_guest_access(payload: GuestAccess):
    with connection() as db:
        db.execute(
            "INSERT INTO app_settings(key, value) VALUES('guest_enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("1" if payload.enabled else "0",),
        )
    return {"enabled": payload.enabled}


@app.post("/api/settings/admin-password", dependencies=[Depends(require_admin)])
def change_admin_password(payload: AdminPasswordChange):
    if not admin_password_matches(payload.current_password):
        raise HTTPException(403, "Současné heslo není správné")
    if not payload.new_password.strip():
        raise HTTPException(422, "Nové heslo nesmí být prázdné")
    with connection() as db:
        db.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (ADMIN_PASSWORD_SETTING, hash_admin_password(payload.new_password)),
        )
    _verified_admin.clear()
    return {"accepted": True}


GITHUB_REPO = os.environ.get("FARM_GITHUB_REPO", "petan86/3DPrintFarm")
_latest_release_cache: dict = {"at": 0.0, "value": None}


def updater_dir() -> Path:
    """Shared with the host updater (server/deploy/auto-update.sh) via the data volume."""
    return data_dir() / "updater"


def latest_release(refresh: bool = False) -> dict:
    """Latest published GitHub release, cached for ten minutes."""
    cached = _latest_release_cache
    if not refresh and cached["value"] is not None and time.monotonic() - cached["at"] < 600:
        return cached["value"]
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "PrintFarm-server"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            release = json.load(response)
        value = {"version": release.get("tag_name"), "name": release.get("name"),
                 "published_at": release.get("published_at"), "url": release.get("html_url"), "error": None}
    except urllib.error.HTTPError as exc:
        value = {"version": None, "error": "Na GitHubu zatím není žádný release" if exc.code == 404 else f"GitHub odpověděl chybou {exc.code}"}
    except (OSError, ValueError):
        value = {"version": None, "error": "GitHub není dostupný"}
    cached.update(at=time.monotonic(), value=value)
    return value


@app.get("/api/settings/update", dependencies=[Depends(require_admin)])
def get_update_status(refresh: bool = False):
    status_file = updater_dir() / "status.json"
    installed = None
    if status_file.is_file():
        try:
            installed = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            installed = {"state": "error", "message": "Stav aktualizace nelze přečíst"}
    latest = latest_release(refresh)
    current = (installed or {}).get("current")
    return {
        "updater_installed": installed is not None,
        "installed": installed,
        "latest": latest,
        "update_available": bool(latest.get("version") and current and current != latest["version"]),
        "request_pending": (updater_dir() / "request").exists(),
    }


@app.post("/api/settings/update", dependencies=[Depends(require_admin)], status_code=202)
def request_update():
    directory = updater_dir()
    if not (directory / "status.json").is_file():
        raise HTTPException(409, "Automatické aktualizace nejsou na Raspberry Pi nainstalované")
    request_file = directory / "request"
    if request_file.exists():
        raise HTTPException(409, "Aktualizace už byla vyžádána")
    try:
        request_file.write_text(now(), encoding="utf-8")
    except OSError:
        raise HTTPException(500, "Požadavek na aktualizaci nelze zapsat")
    return {"accepted": True}


@app.get("/api/settings/hotspot", dependencies=[Depends(require_admin)])
def get_hotspot_settings():
    with connection() as db:
        values = {row["key"]: row["value"] for row in db.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'hotspot_%'"
        )}
    return {
        "enabled": values.get("hotspot_enabled", "0") == "1",
        "ssid": values.get("hotspot_ssid") or DEFAULT_HOTSPOT_SSID,
        "has_password": bool(values.get("hotspot_password")),
        "apply_available": hotspot_apply_enabled(),
    }


@app.put("/api/settings/hotspot", dependencies=[Depends(require_admin)])
def save_hotspot_settings(payload: HotspotSettings):
    with connection() as db:
        current = db.execute("SELECT value FROM app_settings WHERE key='hotspot_password'").fetchone()
        password = payload.password or (current["value"] if current else None)
        if not password:
            raise HTTPException(422, "Při prvním nastavení je nutné zadat heslo")
        values = {
            "hotspot_enabled": "1" if payload.enabled else "0",
            "hotspot_ssid": payload.ssid.strip(),
            "hotspot_password": password,
        }
        db.executemany(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            values.items(),
        )
    state = apply_hotspot(payload.ssid.strip(), password, payload.enabled)
    return {"saved": True, "state": state}


@app.delete("/api/printers/{printer_id}", dependencies=[Depends(require_admin)], status_code=204)
def delete_printer(printer_id: str):
    require_printer_exists(printer_id)
    with connection() as db:
        db.execute("DELETE FROM printer_commands WHERE printer_id=?", (printer_id,))
        db.execute("UPDATE jobs SET printer_id=NULL WHERE printer_id=?", (printer_id,))
        db.execute("DELETE FROM printers WHERE id=?", (printer_id,))
    return Response(status_code=204)


@app.get("/api/materials", dependencies=[Depends(require_viewer)])
def list_materials():
    with connection() as db:
        rows = db.execute(
            "SELECT code, name FROM material_catalog ORDER BY sort_order, name"
        ).fetchall()
        mappings = db.execute(
            "SELECT material_code, color_code FROM material_colors ORDER BY color_code"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["color_codes"] = [m["color_code"] for m in mappings if m["material_code"] == row["code"]]
        result.append(item)
    return result


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


@app.put("/api/materials/{material_code}/colors", dependencies=[Depends(require_admin)])
def set_material_colors(material_code: str, payload: MaterialColorsUpdate):
    unique_codes = list(dict.fromkeys(code.strip() for code in payload.color_codes))
    with connection() as db:
        if db.execute("SELECT 1 FROM material_catalog WHERE code=?", (material_code,)).fetchone() is None:
            raise HTTPException(404, "Materiál nebyl nalezen")
        if unique_codes:
            placeholders = ",".join("?" for _ in unique_codes)
            count = db.execute(
                f"SELECT COUNT(*) FROM color_catalog WHERE code IN ({placeholders})", unique_codes
            ).fetchone()[0]
            if count != len(unique_codes):
                raise HTTPException(422, "Některá barva neexistuje")
        db.execute("DELETE FROM material_colors WHERE material_code=?", (material_code,))
        db.executemany(
            "INSERT INTO material_colors(material_code, color_code) VALUES(?, ?)",
            ((material_code, code) for code in unique_codes),
        )
    return {"material_code": material_code, "color_codes": unique_codes}


@app.get("/api/colors", dependencies=[Depends(require_viewer)])
def list_colors():
    with connection() as db:
        rows = db.execute(
            "SELECT code, name, hex_color FROM color_catalog ORDER BY sort_order, name"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/colors", dependencies=[Depends(require_admin)], status_code=201)
def create_color(payload: ColorCatalogCreate):
    code, name, hex_color = payload.code.strip(), payload.name.strip(), payload.hex_color.upper()
    try:
        with connection() as db:
            order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM color_catalog").fetchone()[0]
            db.execute(
                "INSERT INTO color_catalog(code, name, hex_color, sort_order) VALUES(?, ?, ?, ?)",
                (code, name, hex_color, order),
            )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(409, "Kód nebo název barvy již existuje") from exc
        raise
    return {"code": code, "name": name, "hex_color": hex_color}


@app.patch("/api/printers/{printer_id}/material", dependencies=[Depends(require_admin)])
def set_material(printer_id: str, payload: MaterialUpdate):
    require_printer_exists(printer_id)
    with connection() as db:
        allowed = db.execute(
            "SELECT 1 FROM material_colors WHERE material_code=? AND color_code=?",
            (payload.material.strip(), payload.color.strip()),
        ).fetchone()
        if allowed is None:
            raise HTTPException(422, "Tato barva není pro vybraný materiál povolena")
        db.execute(
            "UPDATE printers SET material=?, material_color=? WHERE id=?",
            (payload.material.strip(), payload.color.strip(), printer_id),
        )
    schedule_buffer()
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


@app.get("/api/printers/{printer_id}/history", dependencies=[Depends(require_viewer)])
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
    schedule_buffer()
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
                   AND (required_material IS NULL OR required_material=?)
                   AND (required_color IS NULL OR required_color=?)
                   ORDER BY queue_position, created_at LIMIT 1""",
                (printer["material"], printer["material_color"]),
            ).fetchone()
            if active is not None:
                db.execute(
                    "UPDATE jobs SET printer_id=?, assigned_printer_name=?, status='assigned' WHERE id=?",
                    (printer["id"], printer["name"], active["id"]),
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


@app.get("/api/jobs/{job_id}/thumbnail", dependencies=[Depends(require_viewer)])
def job_thumbnail(job_id: str):
    with connection() as db:
        job = db.execute("SELECT thumbnail_name FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None or not job["thumbnail_name"]:
        raise HTTPException(404, "Thumbnail not found")
    path = data_dir() / "previews" / job["thumbnail_name"]
    if not path.is_file():
        raise HTTPException(410, "Thumbnail file is missing")
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media_type)


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
    schedule_buffer()
    return {"accepted": True, "job_id": job_id, "state": payload.state}


@app.post("/api/jobs", dependencies=[Depends(require_admin)], status_code=201)
def upload_job(file: UploadFile = File(...), printer_id: str | None = Form(default=None), required_material: str | None = Form(default=None)):
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

    thumbnail_name = extract_thumbnail(destination, job_id)
    detected_filament, estimated_seconds = extract_gcode_metadata(destination)

    with connection() as db:
        position = db.execute(
            "SELECT COALESCE(MAX(queue_position), 0) + 1 FROM jobs"
        ).fetchone()[0]
        job_status = "assigned" if printer_id else "queued"
        printer_name = None
        if printer_id:
            printer_name = db.execute("SELECT name FROM printers WHERE id=?", (printer_id,)).fetchone()[0]
        if not required_material and detected_filament:
            material_row = db.execute(
                "SELECT code FROM material_catalog WHERE UPPER(name)=UPPER(?)", (detected_filament,)
            ).fetchone()
            required_material = material_row[0] if material_row else None
        db.execute(
            """INSERT INTO jobs(id, original_name, stored_name, size_bytes,
               status, printer_id, queue_position, created_at, thumbnail_name, assigned_printer_name,
               required_material, estimated_seconds)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, original_name, stored_name, size, job_status, printer_id, position, now(), thumbnail_name, printer_name, required_material, estimated_seconds),
        )
        if printer_id:
            db.execute("UPDATE printers SET status='queued' WHERE id=?", (printer_id,))
    if not printer_id:
        schedule_buffer()
    return {"id": job_id, "name": original_name, "size_bytes": size,
            "queue_position": position, "printer_id": printer_id, "status": job_status}


@app.patch("/api/jobs/{job_id}/quantity", dependencies=[Depends(require_admin)])
def set_job_quantity(job_id: str, payload: JobQuantityUpdate):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "Úloha neexistuje")
        if job["status"] in ("complete", "error"):
            raise HTTPException(409, "Počet kusů dokončené úlohy nelze změnit")
        db.execute("UPDATE jobs SET required_quantity=? WHERE id=?", (payload.required_quantity, job_id))
    return {"accepted": True, "required_quantity": payload.required_quantity}


@app.patch("/api/jobs/{job_id}/material", dependencies=[Depends(require_admin)])
def set_job_material(job_id: str, payload: JobMaterialUpdate):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "Úloha neexistuje")
        if job["status"] != "queued":
            raise HTTPException(409, "Materiál lze změnit pouze před přidělením úlohy tiskárně")
        if payload.required_material is not None and db.execute(
            "SELECT 1 FROM material_catalog WHERE code=?", (payload.required_material,)
        ).fetchone() is None:
            raise HTTPException(422, "Neznámý materiál")
        db.execute(
            """UPDATE jobs SET required_material=?, scheduling_message=NULL,
               required_color=CASE WHEN ? IS NOT NULL AND EXISTS (SELECT 1 FROM material_colors
                 WHERE material_code=? AND color_code=jobs.required_color) THEN required_color END
               WHERE id=?""",
            (payload.required_material, payload.required_material, payload.required_material, job_id))
    schedule_buffer()
    return {"accepted": True, "required_material": payload.required_material}


@app.patch("/api/jobs/{job_id}", dependencies=[Depends(require_admin)])
def update_job(job_id: str, payload: JobUpdate):
    """Edit quantity, material, colour and printer assignment of a job in one request.

    Material, colour and printer can change only while the printer has not
    started downloading the job (status queued or assigned). An explicit
    printer must match the required material and colour; when only the
    filament changes and the current printer no longer matches, the job
    returns to automatic scheduling.
    """
    fields = payload.model_fields_set
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute(
            """SELECT status, printer_id, required_quantity, required_material, required_color,
               scheduling_message FROM jobs WHERE id=?""", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(404, "Úloha neexistuje")
        quantity = payload.required_quantity if "required_quantity" in fields else job["required_quantity"]
        material = payload.required_material if "required_material" in fields else job["required_material"]
        color = payload.required_color if "required_color" in fields else job["required_color"]
        printer_id = payload.printer_id if "printer_id" in fields else job["printer_id"]
        if "required_quantity" in fields:
            if quantity is None:
                raise HTTPException(422, "Počet kusů je povinný")
            if quantity != job["required_quantity"] and job["status"] in ("complete", "error"):
                raise HTTPException(409, "Počet kusů dokončené úlohy nelze změnit")
        filament_changed = (material, color) != (job["required_material"], job["required_color"])
        printer_changed = printer_id != job["printer_id"]
        if (filament_changed or printer_changed) and job["status"] not in ("queued", "assigned"):
            raise HTTPException(409, "Materiál, barvu a tiskárnu lze změnit jen u úlohy, kterou tiskárna ještě nezačala stahovat")
        if job["status"] not in ("queued", "assigned"):
            db.execute("UPDATE jobs SET required_quantity=? WHERE id=?", (quantity, job_id))
            return {"accepted": True, "required_quantity": quantity, "required_material": material,
                    "required_color": color, "printer_id": job["printer_id"], "status": job["status"]}
        if filament_changed:
            if material is not None and db.execute(
                "SELECT 1 FROM material_catalog WHERE code=?", (material,)
            ).fetchone() is None:
                raise HTTPException(422, "Neznámý materiál")
            if color is not None:
                if material is None:
                    raise HTTPException(422, "Barvu lze zadat jen spolu s materiálem")
                if db.execute(
                    "SELECT 1 FROM material_colors WHERE material_code=? AND color_code=?", (material, color)
                ).fetchone() is None:
                    raise HTTPException(422, "Barva není pro tento materiál dostupná")
        printer = None
        if printer_id is not None:
            printer = db.execute(
                "SELECT id, name, material, material_color FROM printers WHERE id=?", (printer_id,)
            ).fetchone()
            if printer is None:
                raise HTTPException(404, "Tiskárna neexistuje")
            compatible = ((not material or printer["material"] == material)
                          and (not color or printer["material_color"] == color))
            if not compatible:
                if printer_changed:
                    raise HTTPException(409, f"Tiskárna {printer['name']} nemá založený požadovaný materiál a barvu")
                printer = None  # filament changed under an existing assignment: back to the scheduler
        if printer is None:
            status_value, assigned_id, assigned_name = "queued", None, None
        else:
            status_value, assigned_id, assigned_name = "assigned", printer["id"], printer["name"]
        if printer_changed and printer is not None:
            message = "Ručně přiřazeno"
        elif filament_changed or printer_changed:
            message = None  # the scheduler fills in a fresh message
        else:
            message = job["scheduling_message"]
        db.execute(
            """UPDATE jobs SET required_quantity=?, required_material=?, required_color=?,
               status=?, printer_id=?, assigned_printer_name=?, scheduling_message=? WHERE id=?""",
            (quantity, material, color, status_value, assigned_id, assigned_name, message, job_id),
        )
        if printer_changed and printer is not None:
            db.execute("UPDATE printers SET status='queued' WHERE id=? AND status IN ('idle','ready','complete')",
                       (printer["id"],))
    if filament_changed or printer_changed:
        schedule_buffer()
    return {"accepted": True, "required_quantity": quantity, "required_material": material,
            "required_color": color, "printer_id": assigned_id, "status": status_value}


@app.get("/api/jobs", dependencies=[Depends(require_viewer)])
def list_jobs():
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM jobs ORDER BY queue_position, created_at"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["thumbnail_url"] = f"/api/jobs/{item['id']}/thumbnail" if item.get("thumbnail_name") else None
        result.append(item)
    return result


dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

