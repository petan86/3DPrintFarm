import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.farm_server import main
from server.farm_server.database import initialize

with tempfile.TemporaryDirectory() as directory:
    os.environ["FARM_DATA_DIR"] = directory
    os.environ["FARM_ADMIN_KEY"] = "env-admin-key"
    initialize()
    client = TestClient(main.app)
    admin = {"X-Admin-Key": "env-admin-key"}
    guest = {"X-Guest": "1"}

    # Guest access is on by default and read-only.
    assert client.get("/api/auth/guest").json() == {"enabled": True}
    for path in ["/api/printers", "/api/jobs", "/api/materials", "/api/colors"]:
        assert client.get(path, headers=guest).status_code == 200, path
    assert client.get("/api/settings/hotspot", headers=guest).status_code == 401
    assert client.get("/api/settings/update", headers=guest).status_code == 401
    assert client.post("/api/printers", headers=guest, json={"name": "x", "rack": "A", "rack_slot": 1}).status_code == 401
    assert client.patch("/api/jobs/any", headers=guest, json={"required_quantity": 2}).status_code == 401
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers={"X-Admin-Key": "wrong", "X-Guest": "1"}).status_code == 401

    # Admin can switch guest access off.
    assert client.put("/api/settings/guest", headers=guest, json={"enabled": False}).status_code == 401
    assert client.put("/api/settings/guest", headers=admin, json={"enabled": False}).status_code == 200
    assert client.get("/api/auth/guest").json() == {"enabled": False}
    assert client.get("/api/jobs", headers=guest).status_code == 401

    # Password change: wrong current password, too short, then success.
    change = "/api/settings/admin-password"
    assert client.post(change, headers=admin, json={"current_password": "nope", "new_password": "new-password-1"}).status_code == 403
    assert client.post(change, headers=admin, json={"current_password": "env-admin-key", "new_password": "short"}).status_code == 422
    assert client.post(change, headers=admin, json={"current_password": "env-admin-key", "new_password": "new-password-1"}).status_code == 200
    assert client.get("/api/jobs", headers=admin).status_code == 401
    new_admin = {"X-Admin-Key": "new-password-1"}
    assert client.get("/api/jobs", headers=new_admin).status_code == 200
    assert client.get("/api/jobs", headers=new_admin).status_code == 200  # cached verification

    # Reset tool brings back FARM_ADMIN_KEY.
    import importlib
    importlib.import_module("server.farm_server.reset_admin_password")
    assert client.get("/api/jobs", headers=admin).status_code == 200
    assert client.get("/api/jobs", headers=new_admin).status_code == 401

print("access tests passed")
