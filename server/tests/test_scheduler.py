import os
import sys
import tempfile
from pathlib import Path

os.environ["FARM_DATA_DIR"] = tempfile.mkdtemp(prefix="3dprintfarm-scheduler-")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.farm_server.database import connection, initialize
from server.farm_server.main import extract_gcode_metadata, now, register_printer, schedule_buffer
from server.farm_server.schemas import PrinterCreate


initialize()
assert extract_gcode_metadata(Path("server/tests/fixtures/thumbnail_test.gcode")) == ("PLA", 3723)
printer = register_printer(PrinterCreate(name="PLA printer"))
with connection() as db:
    db.execute("UPDATE printers SET material='7', status='printing', progress=90, last_seen=? WHERE id=?", (now(), printer.id))
    db.execute("INSERT INTO jobs(id, original_name, stored_name, size_bytes, status, printer_id, queue_position, created_at, estimated_seconds) VALUES('active', 'a.gcode', 'a', 1, 'printing', ?, 1, ?, 1000)", (printer.id, now()))
    db.execute("INSERT INTO jobs(id, original_name, stored_name, size_bytes, status, queue_position, created_at, required_material) VALUES('next', 'b.gcode', 'b', 1, 'queued', 2, ?, '7')", (now(),))
schedule_buffer()
with connection() as db:
    scheduled = db.execute("SELECT status, printer_id FROM jobs WHERE id='next'").fetchone()
assert scheduled["status"] == "assigned"
assert scheduled["printer_id"] == printer.id
print("scheduler test passed")
