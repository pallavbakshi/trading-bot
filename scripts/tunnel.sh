#!/usr/bin/env bash
# Start (or show) the Cloudflare tunnel on the remote server.
# Run via: make tunnel
set -euo pipefail

REMOTE="aiadmin@100.88.77.72"
REMOTE_DIR="~/projects/trading-bot"
PORT=8000
LOG_DIR="$REMOTE_DIR/.logs"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
info() { printf "  \033[34m→\033[0m %s\n" "$1"; }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$1"; }

hdr "Cloudflare tunnel"

ssh "$REMOTE" bash <<REMOTE
set -euo pipefail
export PATH="\$HOME/.local/bin:\$PATH"
mkdir -p $LOG_DIR

# Check if tunnel already running and has a URL
EXISTING=\$(grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' $LOG_DIR/cloudflared.log 2>/dev/null | head -1 || true)
if [ -n "\$EXISTING" ] && pkill -0 -f "cloudflared tunnel" 2>/dev/null; then
    echo "  tunnel already running"
    echo "TUNNEL_URL=\$EXISTING"
    exit 0
fi

echo "  starting new tunnel..."
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

nohup cloudflared tunnel --url http://localhost:$PORT \
    > $LOG_DIR/cloudflared.log 2>&1 &

echo "  waiting for URL..."
for i in \$(seq 1 20); do
    URL=\$(grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' $LOG_DIR/cloudflared.log 2>/dev/null | head -1 || true)
    if [ -n "\$URL" ]; then
        echo "TUNNEL_URL=\$URL"
        break
    fi
    sleep 1
done
REMOTE

ok "done"
