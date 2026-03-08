#!/usr/bin/env bash
# Full deploy to remote server.
# Run via: make deploy
set -euo pipefail

REMOTE="aiadmin@100.88.77.72"
REMOTE_DIR="~/projects/trading-bot"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8000
LOG_DIR="$REMOTE_DIR/.logs"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
info() { printf "  \033[34m→\033[0m %s\n" "$1"; }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$1"; }

# ── 1. Push local git changes ─────────────────────────────────────────────────
hdr "1. Git push"
git -C "$LOCAL_DIR" push
ok "pushed"

# ── 2. Rsync data + results (skip if nothing changed) ─────────────────────────
hdr "2. Sync data & results"
if [ -d "$LOCAL_DIR/data" ]; then
    info "syncing data/..."
    rsync -az --delete "$LOCAL_DIR/data/" "$REMOTE:$REMOTE_DIR/data/"
    ok "data/ synced"
else
    info "no local data/ — skipping"
fi

if [ -d "$LOCAL_DIR/results/nse" ]; then
    info "syncing results/nse/ (patterns only)..."
    rsync -az --delete "$LOCAL_DIR/results/nse/" "$REMOTE:$REMOTE_DIR/results/nse/"
    ok "results/nse/ synced"
else
    info "no local results/nse/ — skipping"
fi

# ── 3. Remote: pull, install deps, build frontend, restart server ─────────────
hdr "3. Remote setup & restart"
ssh "$REMOTE" bash <<REMOTE
set -euo pipefail
export PATH="\$HOME/.local/bin:\$PATH"
cd $REMOTE_DIR

echo "  → git pull"
git pull

echo "  → uv sync"
uv sync --quiet

echo "  → npm install & build"
cd web && npm install --silent && npm run build 2>&1 | grep -E 'built|error|warning' || true
cd ..

echo "  → kill existing server (port $PORT)"
lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
sleep 1

mkdir -p $LOG_DIR

echo "  → start server"
PYTHONUNBUFFERED=1 nohup uv run python -m src.server \
    --data-dir data/nse \
    --results results/nse/patterns.json \
    --static-dir web/dist \
    --port $PORT \
    > $LOG_DIR/server.log 2>&1 &

sleep 6
if lsof -ti:$PORT >/dev/null 2>&1; then
    echo "  server running on :$PORT"
else
    echo "  ERROR: server failed to start — check $LOG_DIR/server.log"
    tail -20 $LOG_DIR/server.log
    exit 1
fi
REMOTE
ok "server running"

# ── 4. Cloudflare tunnel ──────────────────────────────────────────────────────
hdr "4. Cloudflare tunnel"
ssh "$REMOTE" bash <<REMOTE
set -euo pipefail
export PATH="\$HOME/.local/bin:\$PATH"
cd $REMOTE_DIR
mkdir -p $LOG_DIR

# Kill any existing tunnel
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

# Start ephemeral tunnel, capture URL
nohup cloudflared tunnel --url http://localhost:$PORT \
    > $LOG_DIR/cloudflared.log 2>&1 &

echo "  waiting for tunnel URL..."
for i in \$(seq 1 20); do
    URL=\$(grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' $LOG_DIR/cloudflared.log 2>/dev/null | head -1 || true)
    if [ -n "\$URL" ]; then
        echo "TUNNEL_URL=\$URL"
        break
    fi
    sleep 1
done
REMOTE

echo ""
printf "\033[32m\033[1mDeployed!\033[0m\n"
echo ""
echo "  Logs:   ssh $REMOTE 'tail -f $REMOTE_DIR/$LOG_DIR/server.log'"
echo "  Status: ssh $REMOTE 'lsof -ti:$PORT'"
