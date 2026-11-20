#!/usr/bin/env bash
# Installs a systemd timer that keeps the PrintFarm server on the latest
# GitHub release. Run once on the Raspberry Pi from the repository root:
#   sudo server/deploy/install-auto-update.sh
# Remove again with:
#   sudo systemctl disable --now printfarm-update.timer
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$DIR")}"

if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx docker; then
    echo "User $RUN_USER is not in the docker group: sudo usermod -aG docker $RUN_USER" >&2
    exit 1
fi

# The container runs as UID 10001 and must be able to write its data.
mkdir -p "$DIR/server/data"
chown -R 10001:10001 "$DIR/server/data"
chmod +x "$DIR/server/deploy/auto-update.sh"

cat > /etc/systemd/system/printfarm-update.service <<EOF
[Unit]
Description=Update PrintFarm server to the latest GitHub release
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$DIR
ExecStart=$DIR/server/deploy/auto-update.sh
TimeoutStartSec=30min
EOF

cat > /etc/systemd/system/printfarm-update.timer <<EOF
[Unit]
Description=Check for PrintFarm server releases every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
RandomizedDelaySec=60
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now printfarm-update.timer

echo "Auto-update installed for user $RUN_USER in $DIR."
echo "Run a check now:   sudo systemctl start printfarm-update.service"
echo "Show the log:      journalctl -u printfarm-update.service -n 50"
