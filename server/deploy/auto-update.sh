#!/usr/bin/env bash
# Updates the PrintFarm server to the latest published GitHub release.
#
# Run periodically by printfarm-update.timer (see install-auto-update.sh).
# Steps: look up the latest release, check out its tag, rebuild the container
# and wait for it to become healthy. If the new release does not come up,
# the previous version is restored and rebuilt.
#
# Docker Compose reads COMPOSE_FILE from .env, so a hotspot setup can set
#   COMPOSE_FILE=compose.yaml:compose.hotspot.yaml
# there and the update keeps using it.
set -euo pipefail

REPO="${PRINTFARM_REPO:-petan86/3DPrintFarm}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HEALTH_TIMEOUT="${PRINTFARM_HEALTH_TIMEOUT:-180}"

log() { echo "[printfarm-update] $*"; }

# The whole run lives in main() so bash has parsed it completely before the
# checkout below replaces this file on disk.
main() {
    cd "$DIR"

    exec 9>"$DIR/.auto-update.lock"
    if ! flock -n 9; then
        log "Another update is running, skipping."
        exit 0
    fi

    latest="$(curl -fsSL --max-time 20 \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null || true)"

    if [ -z "$latest" ]; then
        log "No published release found (or GitHub unreachable), nothing to do."
        exit 0
    fi

    current="$(git describe --tags --exact-match HEAD 2>/dev/null || echo none)"
    if [ "$current" = "$latest" ]; then
        exit 0
    fi

    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        log "Local changes in tracked files, refusing to update to $latest:"
        git status --short --untracked-files=no
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

    git fetch --quiet --force --tags origin
    git -c advice.detachedHead=false checkout --quiet "refs/tags/$latest"

    if docker compose up -d --build --remove-orphans && wait_healthy; then
        docker image prune -f >/dev/null 2>&1 || true
        log "Update to $latest finished, server is healthy."
        exit 0
    fi

    log "Release $latest did not become healthy, rolling back to $previous."
    docker compose logs --tail 40 farm-server || true
    git -c advice.detachedHead=false checkout --quiet "$previous"
    docker compose up -d --build --remove-orphans
    if wait_healthy; then
        log "Rollback finished, running $previous again."
    else
        log "Rollback did not become healthy either, check 'docker compose logs'."
    fi
    exit 1
}

main "$@"
exit
