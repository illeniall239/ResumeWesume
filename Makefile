.PHONY: dev install api web test check

# One command, both servers. `make` is not on Windows unless you install it,
# so this target is a convenience -- `uv run scripts/dev.py` is the thing, and
# it is what the README tells people to run.
dev:
	uv run scripts/dev.py

install:
	cd apps/api && uv sync --extra dev && uv run playwright install chromium
	cd apps/web && npm install

api:
	cd apps/api && uv run uvicorn studio.main:app --reload --port 8000

web:
	cd apps/web && npm run dev

test:
	cd apps/api && uv run pytest
	cd apps/web && npm run test

check: test
	cd apps/web && npm run typecheck && npm run build
	git diff --exit-code apps/web/src/contracts
