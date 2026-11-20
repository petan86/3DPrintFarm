import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.farm_server import main
from server.farm_server.database import initialize

responses = []


def fake_urlopen(request, timeout):
    result = responses.pop(0)
    if isinstance(result, Exception):
        raise result
    return io.BytesIO(json.dumps(result).encode())


main.urllib.request.urlopen = fake_urlopen

with tempfile.TemporaryDirectory() as directory:
    os.environ["FARM_DATA_DIR"] = directory
    initialize()

    # Updater not installed: version unknown, install refused.
    responses.append({"tag_name": "v0.2.0", "html_url": "https://example.invalid"})
    info = main.get_update_status(refresh=True)
    assert not info["updater_installed"] and info["latest"]["version"] == "v0.2.0"
    assert not info["update_available"]
    try:
        main.request_update()
        raise AssertionError("Expected rejection")
    except HTTPException as exc:
        assert exc.status_code == 409

    # Installed older version: update offered, cached GitHub answer reused.
    updater = Path(directory) / "updater"
    updater.mkdir()
    (updater / "status.json").write_text(json.dumps({"state": "current", "current": "v0.1.0"}))
    info = main.get_update_status()
    assert info["installed"]["current"] == "v0.1.0" and info["update_available"]

    main.request_update()
    assert (updater / "request").exists() and main.get_update_status()["request_pending"]
    try:
        main.request_update()
        raise AssertionError("Expected duplicate rejection")
    except HTTPException as exc:
        assert exc.status_code == 409

    # Up to date, then GitHub without releases or unreachable.
    (updater / "status.json").write_text(json.dumps({"state": "updated", "current": "v0.2.0"}))
    assert not main.get_update_status()["update_available"]
    responses.append(urllib.error.HTTPError("url", 404, "Not Found", {}, None))
    assert main.get_update_status(refresh=True)["latest"]["error"] == "Na GitHubu zatím není žádný release"
    responses.append(OSError("offline"))
    info = main.get_update_status(refresh=True)
    assert info["latest"]["error"] == "GitHub není dostupný" and not info["update_available"]

print("update tests passed")
