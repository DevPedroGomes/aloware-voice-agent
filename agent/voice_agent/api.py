"""Admin API, and the built admin UI at `/`.

`make demo` runs this in the background next to the voice console. During UI work,
`make api` + `make ui` run it with the Vite dev server instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config as config_mod
from . import store
from .compliance import precall_gate
from .prompt import COMPLIANCE_RULES, PHONE_RULES

app = FastAPI(title="Speed-to-lead voice agent admin")

ADMIN_DIST = Path(__file__).resolve().parents[2] / "admin" / "dist"


@app.get("/api/catalog")
def catalog() -> dict:
    return {
        "voices": {k: v["label"] for k, v in config_mod.VOICES.items()},
        "tools": config_mod.TOOL_CATALOG,
        "template_vars": config_mod.TEMPLATE_VARS,
        "system_rules": PHONE_RULES + "\n\n" + COMPLIANCE_RULES,
    }


@app.get("/api/config")
def get_config() -> dict:
    return config_mod.load()


@app.put("/api/config")
def put_config(cfg: dict[str, Any]) -> dict:
    try:
        return config_mod.save(cfg)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"errors": e.args[0]}) from None


class PreviewIn(BaseModel):
    greeting: str
    persona: dict
    lead_id: str


@app.post("/api/preview-greeting")
def preview_greeting(body: PreviewIn) -> dict:
    lead = store.get_lead(body.lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    values = config_mod.template_values({"persona": body.persona}, lead)
    return {
        "text": config_mod.render_template(body.greeting, values),
        "has_ai_disclosure": bool(config_mod.AI_DISCLOSURE_RE.search(body.greeting)),
    }


@app.get("/api/leads")
def leads() -> list[dict]:
    cfg = config_mod.load()
    out = []
    for lead in store.list_leads():
        gate = precall_gate(lead, cfg)
        out.append({**lead, "gate": {"allowed": gate.allowed, "code": gate.code, "reason": gate.reason}})
    return out


@app.get("/api/calls")
def calls() -> list[dict]:
    keep = ("call_id", "started_at", "config_version", "lead_name", "disposition", "latency")
    return [{k: c.get(k) for k in keep} for c in store.list_calls()]


@app.get("/api/calls/{call_id}")
def call(call_id: str) -> dict:
    record = store.get_call(call_id)
    if record is None:
        raise HTTPException(404, "call not found")
    return record


@app.get("/api/dnc")
def dnc() -> list[dict]:
    return store.list_dnc()


# Serve the compiled admin UI last, so every /api route above takes precedence.
if ADMIN_DIST.exists():
    app.mount("/", StaticFiles(directory=ADMIN_DIST, html=True), name="admin")
