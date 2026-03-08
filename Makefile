.PHONY: dev api web scan install check-remote upload-data deploy tunnel refresh refresh-all refresh-nse refresh-sp

# Start both API server and Vite dev server
dev:
	@lsof -ti :8000 | xargs kill -9 2>/dev/null || true
	@echo "Starting API server and Vite dev server..."
	@make api & make web & wait

# Python API server (port 8000)
api:
	uv run python -m src.server --data-dir data/nse --data-dir data/sp --results results/nse/patterns.json

# Vite frontend dev server
web:
	cd web && npm run dev

# CLI sidecar (pass args after --)
# Usage: make cli -- ticker AAPL
#        make cli -- date 2024-01-15
#        make cli -- toggle sma on
cli:
	uv run python -m src.cli $(filter-out $@,$(MAKECMDGOALS))

# Run pattern scanner on all tickers
scan:
	uv run python -m src.scanner

# Install dependencies
install:
	uv sync
	cd web && npm install

# Refresh a single ticker's CSV data (requires tv bridge running)
# Usage: make refresh TICKER=RELIANCE  or  make refresh TICKER=NSE:RELIANCE  or  make refresh TICKER=MSFT
refresh:
	uv run python -m src.refresh $(TICKER)

# Refresh all tickers (NSE + SP)
refresh-all:
	uv run python -m src.refresh --all

# Refresh only NSE tickers
refresh-nse:
	uv run python -m src.refresh --all --exchange nse

# Refresh only S&P tickers
refresh-sp:
	uv run python -m src.refresh --all --exchange sp

# Check remote server readiness (TA-Lib, Node, uv, cloudflared, disk, etc.)
check-remote:
	@bash scripts/check-remote.sh

# Upload data/ and results/nse/ to remote (rsync, skips unchanged files)
upload-data:
	@echo "Syncing data/..."
	@rsync -avz --progress data/ aiadmin@100.88.77.72:~/projects/trading-bot/data/
	@if [ -d results/nse ]; then \
	    echo "Syncing results/nse/ (patterns)..."; \
	    rsync -avz --progress results/nse/ aiadmin@100.88.77.72:~/projects/trading-bot/results/nse/; \
	fi
	@echo "Done."

# Deploy to remote: push code, rsync data, rebuild, restart server (tunnel unaffected)
deploy:
	@bash scripts/deploy.sh

# Start Cloudflare tunnel on remote (reuses existing tunnel if still running)
tunnel:
	@bash scripts/tunnel.sh
