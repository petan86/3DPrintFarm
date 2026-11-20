import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.farm_server import main
from server.farm_server.database import connection, initialize

with tempfile.TemporaryDirectory() as directory:
    os.environ['FARM_DATA_DIR'] = directory
    initialize()
    initialize()  # Migration can run repeatedly on existing databases.
    with connection() as db:
        db.execute('INSERT INTO printers(id,name,api_key_hash,created_at) VALUES(?,?,?,?)',
                   ('test', '<test>', main.key_hash('device-key'), main.now()))
    assert main.get_connected_devices()['devices'][0]['online'] is False
    request = Request({'type': 'http', 'client': ('192.168.1.42', 1234)})
    try:
        main.require_printer(request, 'wrong')
        raise AssertionError('Invalid credentials accepted')
    except HTTPException as exc:
        assert exc.status_code == 401
    assert main.get_connected_devices()['devices'][0]['ip_address'] is None
    main.require_printer(request, 'device-key')
    device = main.get_connected_devices()['devices'][0]
    assert device['ip_address'] == '192.168.1.42' and device['online']
    assert 'api_key_hash' not in device
    with connection() as db:
        db.execute("UPDATE printers SET last_contact='2020-01-01T00:00:00+00:00'")
    assert main.get_connected_devices()['devices'][0]['online'] is False
print('devices test passed')
