import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.farm_server import main
from server.farm_server.database import connection, initialize
from server.farm_server.schemas import JobUpdate, PrinterCreate


def rejected(job_id, payload, code):
    try:
        main.update_job(job_id, payload)
    except HTTPException as exc:
        assert exc.status_code == code, (exc.status_code, exc.detail)
        return
    raise AssertionError(f"Expected HTTP {code}")


def job():
    return next(item for item in main.list_jobs() if item["id"] == "job")


def device(printer_id):
    with connection() as db:
        return dict(db.execute("SELECT * FROM printers WHERE id=?", (printer_id,)).fetchone())


with tempfile.TemporaryDirectory() as directory:
    os.environ["FARM_DATA_DIR"] = directory
    initialize()
    with connection() as db:
        material, color, other_color = db.execute(
            """SELECT a.material_code, a.color_code, b.color_code FROM material_colors a
               JOIN material_colors b ON b.material_code=a.material_code AND b.color_code<>a.color_code LIMIT 1"""
        ).fetchone()
        db.execute(
            "INSERT INTO jobs(id,original_name,stored_name,size_bytes,queue_position,created_at) "
            "VALUES('job','part.gcode','part.gcode',1,1,?)", (main.now(),))

    # Quantity, material and colour in one request.
    main.update_job("job", JobUpdate(required_quantity=5, required_material=material, required_color=color))
    current = job()
    assert (current["required_quantity"], current["required_material"], current["required_color"]) == (5, material, color)
    assert current["status"] == "queued"

    # Validation.
    rejected("missing", JobUpdate(required_quantity=2), 404)
    rejected("job", JobUpdate(required_material=None, required_color=color), 422)
    rejected("job", JobUpdate(required_material="unknown"), 422)
    with connection() as db:
        db.execute("DELETE FROM material_colors WHERE material_code=? AND color_code=?", (material, other_color))
    rejected("job", JobUpdate(required_color=other_color), 422)
    with connection() as db:
        db.execute("INSERT INTO material_colors(material_code,color_code) VALUES(?,?)", (material, other_color))

    # A printer with the wrong colour does not get the job, neither by claim nor manually.
    wrong = main.register_printer(PrinterCreate(name="Wrong colour"))
    right = main.register_printer(PrinterCreate(name="Right colour"))
    with connection() as db:
        db.execute("UPDATE printers SET material=?, material_color=? WHERE id=?", (material, other_color, wrong.id))
        db.execute("UPDATE printers SET material=?, material_color=? WHERE id=?", (material, color, right.id))
    assert main.claim_next_job(device(wrong.id)).status_code == 204
    rejected("job", JobUpdate(printer_id=wrong.id), 409)
    rejected("job", JobUpdate(printer_id="no-such-printer"), 404)

    # Manual assignment and back to automatic scheduling.
    main.update_job("job", JobUpdate(printer_id=right.id))
    current = job()
    assert (current["status"], current["printer_id"], current["assigned_printer_name"]) == ("assigned", right.id, "Right colour")
    assert current["scheduling_message"] == "Ručně přiřazeno"
    main.update_job("job", JobUpdate(printer_id=None))
    current = job()
    assert (current["status"], current["printer_id"]) == ("queued", None)

    # Changing the filament of an assigned job releases an incompatible printer.
    main.update_job("job", JobUpdate(printer_id=right.id))
    main.update_job("job", JobUpdate(required_color=other_color))
    current = job()
    assert current["printer_id"] != right.id and current["required_color"] == other_color

    # Once the printer downloads the job only the quantity may change.
    main.update_job("job", JobUpdate(required_color=color, printer_id=right.id))
    with connection() as db:
        db.execute("UPDATE jobs SET status='printing' WHERE id='job'")
    rejected("job", JobUpdate(printer_id=None), 409)
    rejected("job", JobUpdate(required_color=other_color), 409)
    main.update_job("job", JobUpdate(required_quantity=7))
    current = job()
    assert (current["required_quantity"], current["status"], current["printer_id"]) == (7, "printing", right.id)

print("job edit tests passed")
