import base64
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.farm_server import main
from server.farm_server.database import connection, initialize
from server.farm_server.schemas import JobMaterialUpdate, PrinterCreate

with tempfile.TemporaryDirectory() as directory:
    os.environ['FARM_DATA_DIR'] = directory
    initialize()
    with connection() as db:
        db.execute("INSERT INTO jobs(id,original_name,stored_name,size_bytes,queue_position,created_at) VALUES('job','part.gcode','part.gcode',1,1,?)", (main.now(),))
    main.set_job_material('job', JobMaterialUpdate(required_material='7'))
    assert main.list_jobs()[0]['required_material'] == '7'
    for job_id, material, code in [('missing', '7', 404), ('job', 'unknown', 422)]:
        try:
            main.set_job_material(job_id, JobMaterialUpdate(required_material=material))
            raise AssertionError('Expected rejection')
        except HTTPException as exc:
            assert exc.status_code == code
    printer = main.register_printer(PrinterCreate(name='ABS'))
    with connection() as db:
        db.execute("UPDATE printers SET material='2' WHERE id=?", (printer.id,))
        device = dict(db.execute('SELECT * FROM printers WHERE id=?', (printer.id,)).fetchone())
    assert main.claim_next_job(device).status_code == 204
    main.set_job_material('job', JobMaterialUpdate(required_material='2'))
    assert main.list_jobs()[0]['printer_id'] == printer.id
    try:
        main.set_job_material('job', JobMaterialUpdate(required_material='7'))
        raise AssertionError('Assigned job changed')
    except HTTPException as exc:
        assert exc.status_code == 409
    fixture = Path('server/tests/fixtures/thumbnail_test.gcode').read_text()
    for marker in ['thumbnail', 'thumbnail_PNG', 'thumbnail_JPG']:
        source = Path(directory) / 'preview.gcode'
        source.write_text(fixture.replace('thumbnail', marker))
        result = main.extract_thumbnail(source, 'preview')
        assert result == 'preview.png'
        assert (Path(directory) / 'previews' / result).read_bytes().startswith(b'\x89PNG')
    source.write_text('; thumbnail begin 1x1 4\n; !!!!\n; thumbnail end\n')
    assert main.extract_thumbnail(source, 'broken') is None
    jpeg = base64.b64encode(b'\xff\xd8\xfftest').decode()
    source.write_text(f'; thumbnail_JPG begin 20x20 {len(jpeg)}\n; {jpeg}\n; thumbnail_JPG end\n')
    assert main.extract_thumbnail(source, 'jpeg') == 'jpeg.jpg'
print('job material and thumbnail tests passed')
