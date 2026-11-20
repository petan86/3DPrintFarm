#!/usr/bin/env bash
# Updates the PrintFarm server to the latest published GitHub release.
#
# Run periodically by printfarm-update.timer (see install-auto-update.sh).
# Steps: look up the latest release, check out its tag, rebuild the container
# and wait for it to become healthy. If the new release does not come up,
# the previous version is restored and rebuilt.
#
# Progress is written to server/data/updater/status.json, which the dashboard
# shows in Settings. The dashboard requests an immediate update by creating
# server/data/updater/request (watched by printfarm-update.path).
#
# Docker Compose reads COMPOSE_FILE from .env, so a hotspot setup can set
#   COMPOSE_FILE=compose.yaml:compose.hotspot.yaml
# there and the update keeps using it.
set -euo pipefail

REPO="${PRINTFARM_REPO:-petan86/3DPrintFarm}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HEALTH_TIMEOUT="${PRINTFARM_HEALTH_TIMEOUT:-180}"

STATUS_DIR="$DIR/server/data/updater"

log() { echo "[printfarm-update] $*"; }

# write_status STATE MESSAGE: shares the outcome with the dashboard.
write_status() {
    [ -d "$STATUS_DIR" ] || return 0
    STATE="$1" MESSAGE="$2" CURRENT="$(installed_version)" LATEST="${latest:-}" python3 - "$STATUS_DIR" <<'PY' || true
import json, os, sys, tempfile
from datetime import datetime, timezone
directory = sys.argv[1]
status = {key.lower(): os.environ[key] or None for key in ("STATE", "MESSAGE", "CURRENT", "LATEST")}
status["checked_at"] = datetime.now(timezone.utc).isoformat()
handle, path = tempfile.mkstemp(dir=directory)
with os.fdopen(handle, "w", encoding="utf-8") as output:
    json.dump(status, output, ensure_ascii=False)
os.chmod(path, 0o644)
os.replace(path, os.path.join(directory, "status.json"))
PY
}

installed_version() {
    git -C "$DIR" describe --tags --exact-match HEAD 2>/dev/null         || git -C "$DIR" describe --tags --always HEAD 2>/dev/null || echo unknown
}

# The whole run lives in main() so bash has parsed it completely before the
# checkout below replaces this file on disk.
main() {
    cd "$DIR"

    exec 9>"$DIR/.auto-update.lock"
    if ! flock -n 9; then
        log "Another update is running, skipping."
        exit 0
    fi
    rm -f "$STATUS_DIR/request"
    local response http_code
    response="$(mktemp)"
    http_code="$(curl -sSL --max-time 20 -o "$response" -w '%{http_code}' \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null || true)"
    latest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$response" 2>/dev/null || true)"
    rm -f "$response"

    if [ -z "$latest" ]; then
        if [ "$http_code" = "404" ]; then
            log "No published release found, nothing to do."
            write_status idle "Na GitHubu zatím není žádný release"
        else
            log "GitHub unreachable (HTTP ${http_code:-none}), will retry later."
            write_status error "GitHub není z Raspberry Pi dostupný, zkontrolujte připojení k internetu"
        fi
        exit 0
    fi

    current="$(git describe --tags --exact-match HEAD 2>/dev/null || echo none)"
    if [ "$current" = "$latest" ]; then
        write_status current "Nainstalovaná verze je aktuální"
        exit 0
    fi

    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        log "Local changes in tracked files, refusing to update to $latest:"
        git status --short --untracked-files=no
        write_status failed "Na Raspberry Pi jsou lokální změny, aktualizace se nespustila"
        exit 1
    fi

    wait_healthy() {
        local id status waited=0
        id="$(docker compose ps -q farm-server)"
        [ -n "$id" ] || return 1
        while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing)"
            case "$status" in
                healthy) return 0 ;;
                unhealthy|exited|dead|missing) return 1 ;;
            esac
            sleep 5
            waited=$((waited + 5))
        done
        return 1
    }

    previous="$(git rev-parse HEAD)"
    log "Updating from $current ($previous) to $latest."
    write_status updating "Instaluji $latest…"

    git fetch --quiet --force --tags origin
    git -c advice.detachedHead=false checkout --quiet "refs/tags/$latest"

    if docker compose up -d --build --remove-orphans && wait_healthy; then
        docker image prune -f >/dev/null 2>&1 || true
        log "Update to $latest finished, server is healthy."
        write_status updated "Aktualizace na $latest proběhla úspěšně"
        exit 0
    fi

    log "Release $latest did not become healthy, rolling back to $previous."
    docker compose logs --tail 40 farm-server || true
    git -c advice.detachedHead=false checkout --quiet "$previous"
    docker compose up -d --build --remove-orphans
    if wait_healthy; then
        log "Rollback finished, running $previous again."
        write_status failed "Verze $latest nenaběhla, vráceno na předchozí verzi"
    else
        log "Rollback did not become healthy either, check 'docker compose logs'."
        write_status failed "Aktualizace selhala a předchozí verze nenaběhla, zkontrolujte docker compose logs"
    fi
    exit 1
}

main "$@"
exit
