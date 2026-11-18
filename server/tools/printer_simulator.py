"""Small ESP32 protocol simulator using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def request(base: str, key: str, method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"X-Printer-Key": key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 204:
                return None
            content_type = response.headers.get_content_type()
            if content_type == "application/json":
                return json.load(response)
            return response.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Server returned {exc.code}: {exc.read().decode()}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--key", required=True, help="Printer API key")
    args = parser.parse_args()

    request(args.server, args.key, "POST", "/api/printer/telemetry", {
        "status": "idle", "progress": 0,
        "hotend_current": 24.5, "bed_current": 23.0,
    })
    job = request(args.server, args.key, "POST", "/api/printer/next-job")
    if job is None:
        print("Queue is empty")
        return
    print(f"Assigned: {job['name']} ({job['size_bytes']} bytes)")

    gcode = request(args.server, args.key, "GET", job["download_url"])
    print(f"Downloaded: {len(gcode)} bytes")
    states = ("downloading", "ready", "heating", "leveling", "waiting_local_approval", "printing")
    for state in states:
        request(args.server, args.key, "POST", f"/api/printer/jobs/{job['id']}/state", {
            "state": state, "progress": 0,
        })
        print(f"State: {state}")
        time.sleep(0.25)


if __name__ == "__main__":
    main()
