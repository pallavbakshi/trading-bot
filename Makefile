.PHONY: dev api web scan install

# Start both API server and Vite dev server
dev:
	@lsof -ti :8000 | xargs kill -9 2>/dev/null || true
	@echo "Starting API server and Vite dev server..."
	@make api & make web & wait

# Python API server (port 8000)
api:
	uv run python -m src.server --data-dir data/nse --results results/nse/patterns.json

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
