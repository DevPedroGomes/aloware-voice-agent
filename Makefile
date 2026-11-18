.PHONY: setup env demo demo-text web evals evals-fast api ui reset

setup:        ## install Python + Node deps and build the admin UI
	cd agent && uv sync
	cd admin && npm install && npm run build

env:          ## write LIVEKIT_* into .env from your LiveKit Cloud project (after `lk cloud auth`)
	lk app env -w -d .env .

demo:         ## ONE terminal: admin at http://localhost:8000 + a voice call with your mic
	./scripts/demo.sh

demo-text:    ## same, typing instead of speaking
	./scripts/demo.sh --text

web:          ## admin + agent in a real LiveKit room; talk in the browser (background audio plays here)
	./scripts/demo.sh web

evals:        ## all evals: deterministic guardrails + LLM behavior (~30 s)
	cd agent && uv run pytest -v

evals-fast:   ## deterministic guardrails only, no network (~1 s)
	cd agent && uv run pytest -v tests/test_guardrails.py

api:          ## (UI development) admin API alone on :8000
	cd agent && uv run uvicorn voice_agent.api:app --port 8000 --reload

ui:           ## (UI development) Vite dev server on :5173 with hot reload, proxies /api to :8000
	cd admin && npm run dev

reset:        ## wipe calls, DNC, bookings and config history; restore the seed config
	rm -rf data/runtime
	git checkout -- data/config.json
