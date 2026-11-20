import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.farm_server import main
from server.farm_server.database import connection, initialize
from server.farm_server.schemas import JobQuantityUpdate

with tempfile.TemporaryDirectory() as directory:
    os.environ['FARM_DATA_DIR'] = directory
    initialize()
    with connection() as db:
        db.execute("INSERT INTO jobs(id,original_name,stored_name,size_bytes,queue_position,created_at) VALUES('job','part.gcode','part.gcode',1,1,?)", (main.now(),))
    assert main.list_jobs()[0]['required_quantity'] == 1
    main.set_job_quantity('job', JobQuantityUpdate(required_quantity=12))
    initialize()
    assert main.list_jobs()[0]['required_quantity'] == 12
    assert len(main.list_jobs()) == 1  # Quantity does not create print jobs.
    for invalid in [0, -1, 1.5, True, 100001]:
        try:
            JobQuantityUpdate(required_quantity=invalid)
            raise AssertionError('Invalid quantity accepted')
        except ValidationError:
            pass
    with connection() as db:
        db.execute("UPDATE jobs SET status='complete' WHERE id='job'")
    for job_id, code in [('missing', 404), ('job', 409)]:
        try:
            main.set_job_quantity(job_id, JobQuantityUpdate(required_quantity=2))
            raise AssertionError('Expected rejection')
        except HTTPException as exc:
            assert exc.status_code == code
print('job quantity tests passed')
