#!/usr/bin/env bash
# Checks remote server readiness for deploying trading-bot.
# Run via: make check-remote
set -euo pipefail

REMOTE="aiadmin@100.88.77.72"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m~\033[0m %s\n" "$1"; }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$1"; }

echo "Checking remote: $REMOTE"

ssh "$REMOTE" bash <<'REMOTE_SCRIPT'
set -uo pipefail

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m~\033[0m %s\n" "$1"; }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$1"; }

# ── TA-Lib C library ──────────────────────────────────────────────────────────
hdr "TA-Lib C library"
TALIB_SO=$(ldconfig -p 2>/dev/null | grep -i 'libta_lib\|libTA_Lib' | head -1 || true)
TALIB_PKG=$(dpkg -l libta-lib-dev 2>/dev/null | grep '^ii' || true)
TALIB_H=$(find /usr -name "ta_libc.h" 2>/dev/null | head -1 || true)

if [ -n "$TALIB_SO" ]; then
    ok "Shared library found: $TALIB_SO"
elif [ -n "$TALIB_PKG" ]; then
    ok "libta-lib-dev installed via dpkg"
else
    fail "libta-lib-dev NOT found (needed for ta-lib Python package)"
    echo "       Fix: sudo apt-get install -y libta-lib-dev"
fi

if [ -n "$TALIB_H" ]; then
    ok "Header found: $TALIB_H"
else
    warn "ta_libc.h not found — C headers may be missing"
fi

# ── Python / uv ───────────────────────────────────────────────────────────────
hdr "Python / uv"
UV_PATH=$(command -v uv 2>/dev/null || echo "")
if [ -n "$UV_PATH" ]; then
    UV_VER=$(uv --version 2>&1)
    ok "uv found: $UV_VER ($UV_PATH)"
else
    fail "uv NOT found"
    echo "       Fix: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# Test ta-lib Python import (only if uv is available and project exists)
PROJECT_DIR="$HOME/projects/trading-bot"
if [ -n "$UV_PATH" ] && [ -d "$PROJECT_DIR" ]; then
    hdr "ta-lib Python binding"
    TALIB_IMPORT=$(cd "$PROJECT_DIR" && uv run python -c "import talib; print(talib.__version__)" 2>&1 || true)
    if echo "$TALIB_IMPORT" | grep -qE '^[0-9]'; then
        ok "import talib OK — version $TALIB_IMPORT"
    else
        fail "import talib FAILED"
        echo "       Error: $TALIB_IMPORT"
    fi
elif [ -n "$UV_PATH" ]; then
    warn "Project not cloned yet — skipping Python import test"
fi

# ── Node / npm ────────────────────────────────────────────────────────────────
hdr "Node / npm"
NODE_PATH=$(command -v node 2>/dev/null || echo "")
if [ -n "$NODE_PATH" ]; then
    NODE_VER=$(node --version)
    ok "node $NODE_VER ($NODE_PATH)"
    # Warn if < v18
    NODE_MAJOR=$(echo "$NODE_VER" | sed 's/v\([0-9]*\).*/\1/')
    if [ "$NODE_MAJOR" -lt 18 ]; then
        warn "Node $NODE_VER is old — recommend v20+"
    fi
else
    fail "node NOT found"
    echo "       Fix: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
fi

NPM_PATH=$(command -v npm 2>/dev/null || echo "")
if [ -n "$NPM_PATH" ]; then
    ok "npm $(npm --version)"
else
    fail "npm NOT found"
fi

# ── cloudflared ───────────────────────────────────────────────────────────────
hdr "cloudflared"
CF_PATH=$(command -v cloudflared 2>/dev/null || echo "")
if [ -n "$CF_PATH" ]; then
    CF_VER=$(cloudflared --version 2>&1 | head -1)
    ok "$CF_VER ($CF_PATH)"
else
    fail "cloudflared NOT found"
    echo "       Fix: see https://pkg.cloudflare.com/index.html"
fi

# ── git ───────────────────────────────────────────────────────────────────────
hdr "git"
GIT_PATH=$(command -v git 2>/dev/null || echo "")
if [ -n "$GIT_PATH" ]; then
    ok "git $(git --version | awk '{print $3}')"
else
    fail "git NOT found"
    echo "       Fix: sudo apt-get install -y git"
fi

# ── Disk space ────────────────────────────────────────────────────────────────
hdr "Disk space"
df -h "$HOME" | awk 'NR==2 {
    avail=$4; used=$3; pct=$5;
    printf "  available: %s  used: %s  (%s)\n", avail, used, pct
}'

# ── Project directory ─────────────────────────────────────────────────────────
hdr "Project directory"
if [ -d "$PROJECT_DIR" ]; then
    ok "$PROJECT_DIR exists"
    if [ -d "$PROJECT_DIR/.git" ]; then
        BRANCH=$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || echo "unknown")
        COMMIT=$(git -C "$PROJECT_DIR" log -1 --format="%h %s" 2>/dev/null || echo "unknown")
        ok "git repo — branch: $BRANCH | last commit: $COMMIT"
    else
        warn "Directory exists but is not a git repo"
    fi
    DATA_DIR="$PROJECT_DIR/data"
    if [ -d "$DATA_DIR" ]; then
        FILE_COUNT=$(find "$DATA_DIR" -name "*.csv" 2>/dev/null | wc -l)
        ok "data/ exists ($FILE_COUNT CSV files)"
    else
        warn "data/ directory not found — you'll need to rsync it"
    fi
else
    warn "$PROJECT_DIR does not exist yet — needs git clone"
fi

echo ""

REMOTE_SCRIPT
