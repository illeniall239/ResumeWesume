.PHONY: install api web test contracts check

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

# Regenerate the TypeScript contract from the Pydantic models. CI runs this and
# fails on a dirty tree, so a schema change breaks the build instead of
# surfacing as a runtime mystery in the browser.
contracts:
	cd apps/api && uv run python scripts/gen_contracts.py

check: contracts test
	cd apps/web && npm run typecheck && npm run build
	git diff --exit-code apps/web/src/contracts
