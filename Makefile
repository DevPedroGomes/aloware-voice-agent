.PHONY: setup env talk talk-text api ui evals evals-fast reset

setup:            ## install Python and Node deps
	cd agent && uv sync
	cd admin && npm install

env:              ## write LIVEKIT_* into .env from your LiveKit Cloud project (after `lk cloud auth`)
	lk app env -w -d .env .

talk:             ## talk to the agent with your mic and speakers
	cd agent && lk agent console agent.py

talk-text:        ## same agent, typing instead of speaking
	cd agent && lk agent console --text agent.py

api:              ## admin API on :8000
	cd agent && uv run uvicorn voice_agent.api:app --port 8000 --reload

ui:               ## admin UI on :5173 (needs `make api` running)
	cd admin && npm run dev

evals:            ## all evals: deterministic guardrails + LLM behavior (~30 s)
	cd agent && uv run pytest -v

evals-fast:       ## deterministic guardrails only, no network (~1 s)
	cd agent && uv run pytest -v tests/test_guardrails.py

reset:            ## wipe calls, DNC, bookings and config history; restore the seed config
	rm -rf data/runtime
	git checkout -- data/config.json
