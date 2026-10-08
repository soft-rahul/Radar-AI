.PHONY: install test demo serve live-demo scan-dry report eval mcp check secrets

install:            ## install dependencies (needs uv: https://docs.astral.sh/uv/)
	uv sync

test:               ## run the test suite offline (replays saved responses, 0 searches)
	uv run pytest -q

demo: install       ## dashboard from the saved study: no keys, no searches
	uv run python scripts/demo.py
	uv run uvicorn shelfradar.api:create_app --factory --port 8765

serve:              ## dashboard on the existing database
	uv run uvicorn shelfradar.api:create_app --factory --port 8765

live-demo:          ## ask one question live to all three engines (~4 searches, your SERPAPI_KEY)
	uv run python scripts/scan.py --live-demo

scan-dry:           ## estimate what a new recording would cost (0 searches)
	uv run python scripts/scan.py --mode record --samples 2 --dry-run

report:             ## regenerate docs/report.md from the stored study
	uv run python scripts/write_report.py --run $${RUN:-2}

eval:               ## regenerate docs/eval.md from the human labels
	uv run python scripts/eval.py

mcp:                ## start the MCP server on stdio (for Claude / Cursor)
	uv run shelfradar-mcp

check:              ## confirm keys load and .env is ignored by git (never prints keys)
	uv run shelfradar-check

secrets:            ## fail if any file git would publish contains an API key
	uv run python scripts/secret_scan.py
