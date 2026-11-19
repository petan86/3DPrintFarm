import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["FARM_DATA_DIR"] = tempfile.mkdtemp(prefix="3dprintfarm-hotspot-")
os.environ.pop("FARM_HOTSPOT_APPLY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException
from pydantic import ValidationError

from server.farm_server import main
from server.farm_server.database import initialize
from server.farm_server.schemas import HotspotSettings


initialize()

# Local preview: nothing is applied, but settings are stored.
assert main.get_hotspot_settings() == {"enabled": False, "ssid": "PrintFarm", "has_password": False, "apply_available": False}
try:
    main.save_hotspot_settings(HotspotSettings(enabled=True, ssid="Farm"))
    raise AssertionError("first save without password must fail")
except HTTPException as exc:
    assert exc.status_code == 422
assert main.save_hotspot_settings(HotspotSettings(enabled=True, ssid=" Farm ", password="tajneheslo")) == {"saved": True, "state": "saved_not_applied"}
assert main.save_hotspot_settings(HotspotSettings(enabled=False, ssid="Farm2")) == {"saved": True, "state": "saved_not_applied"}
settings = main.get_hotspot_settings()
assert settings["ssid"] == "Farm2" and settings["has_password"] and not settings["enabled"]
try:
    HotspotSettings(ssid="   ")
    raise AssertionError("blank SSID must be rejected")
except ValidationError:
    pass

# Raspberry Pi mode: nmcli calls are issued, the password never leaks into errors.
calls = []
active = []


def fake_run(cmd, **kwargs):
    calls.append(cmd)
    args = cmd[1:]
    if args[:3] == ["-t", "-f", "NAME"]:
        if "--active" in args:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(active), "")
        return subprocess.CompletedProcess(cmd, 10, "", "Error: no such connection profile.")
    if args[:2] == ["connection", "up"]:
        return subprocess.CompletedProcess(cmd, 4, "", "Error: Connection activation failed: No suitable device found.")
    return subprocess.CompletedProcess(cmd, 0, "", "")


os.environ["FARM_HOTSPOT_APPLY"] = "1"
main.subprocess.run = fake_run
assert main.apply_hotspot("Farm", "tajneheslo", False) == "disabled"
assert not any(cmd[1:3] == ["connection", "down"] for cmd in calls), "inactive hotspot must not be brought down"
assert any(cmd[1:3] == ["connection", "add"] for cmd in calls)
modify = next(cmd for cmd in calls if cmd[1:3] == ["connection", "modify"])
assert modify[modify.index("connection.autoconnect") + 1] == "no"
try:
    main.apply_hotspot("Farm", "tajneheslo", True)
    raise AssertionError("failed activation must raise")
except HTTPException as exc:
    assert exc.status_code == 500
    assert "tajneheslo" not in exc.detail and "No suitable device" in exc.detail
print("hotspot test passed")
