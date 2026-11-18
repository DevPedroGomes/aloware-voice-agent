"""Admin API for the React UI. Run: `uv run uvicorn voice_agent.api:app --port 8000`."""

from __future__ import annotations

import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config as config_mod
from . import store
from .compliance import precall_gate
from .prompt import COMPLIANCE_RULES, PHONE_RULES

app = FastAPI(title="Speed-to-lead voice agent admin")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AGENT_DIR = Path(__file__).resolve().parents[1]


@app.get("/api/catalog")
def catalog() -> dict:
    return {
        "voices": {k: v["label"] for k, v in config_mod.VOICES.items()},
        "tools": config_mod.TOOL_CATALOG,
        "template_vars": config_mod.TEMPLATE_VARS,
        "dispositions": config_mod.DISPOSITIONS,
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
        raise HTTPException(status_code=422, detail={"errors": e.args[0]})


@app.get("/api/config/versions")
def versions() -> list[dict]:
    return config_mod.list_versions()


@app.post("/api/config/rollback/{version}")
def rollback(version: int) -> dict:
    try:
        return config_mod.rollback(version)
    except KeyError:
        raise HTTPException(status_code=404, detail="version not found")


class PreviewIn(BaseModel):
    greeting: str
    persona: dict
    lead_id: str


@app.post("/api/preview-greeting")
def preview_greeting(body: PreviewIn) -> dict:
    lead = store.get_lead(body.lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    cfg = {"persona": body.persona}
    text = config_mod.render_template(body.greeting, config_mod.template_values(cfg, lead))
    has_disclosure = bool(config_mod.AI_DISCLOSURE_RE.search(body.greeting))
    return {"text": text, "has_ai_disclosure": has_disclosure}


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
    keep = ("call_id", "started_at", "config_version", "persona", "lead_name", "disposition", "crm_note", "latency", "blocked")
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


class DncRemoveIn(BaseModel):
    reason: str


@app.post("/api/dnc/{phone}/remove")
def dnc_remove(phone: str, body: DncRemoveIn) -> dict:
    if len(body.reason.strip()) < 10:
        raise HTTPException(422, "Removing a number from DNC needs a written reason (10+ characters).")
    if not store.remove_from_dnc(phone, reason=body.reason.strip()):
        raise HTTPException(404, "number not on the list")
    return {"ok": True}


@app.get("/api/appointments")
def appointments() -> list[dict]:
    return store.list_appointments()


# ---------- evals ----------

_eval_lock = threading.Lock()
_eval_status: dict[str, Any] = {"running": False}


def _run_evals(version: int) -> None:
    store.EVALS_DIR.mkdir(parents=True, exist_ok=True)
    junit = store.EVALS_DIR / "latest.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=AGENT_DIR,
        capture_output=True,
        text=True,
        timeout=900,
    )
    cases = []
    if junit.exists():
        for tc in ET.parse(junit).getroot().iter("testcase"):
            failure = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
            skipped = tc.find("skipped")
            cases.append(
                {
                    "name": tc.get("name"),
                    "file": (tc.get("classname") or "").split(".")[-1],
                    "seconds": float(tc.get("time") or 0),
                    "status": "failed" if failure is not None else "skipped" if skipped is not None else "passed",
                    "message": (failure.get("message") if failure is not None else None),
                }
            )
    result = {
        "config_version": version,
        "finished_at": store.now_iso(),
        "exit_code": proc.returncode,
        "passed": sum(c["status"] == "passed" for c in cases),
        "failed": sum(c["status"] == "failed" for c in cases),
        "skipped": sum(c["status"] == "skipped" for c in cases),
        "cases": cases,
        "tail": proc.stdout[-3000:],
    }
    store.write_json(store.EVALS_DIR / "latest.json", result)
    _eval_status["running"] = False


@app.post("/api/evals/run")
def run_evals() -> dict:
    with _eval_lock:
        if _eval_status["running"]:
            return {"started": False, "running": True}
        _eval_status["running"] = True
    version = config_mod.load().get("version")
    threading.Thread(target=_run_evals, args=(version,), daemon=True).start()
    return {"started": True, "running": True, "config_version": version}


@app.get("/api/evals/latest")
def evals_latest() -> dict:
    latest = store.read_json(store.EVALS_DIR / "latest.json", None)
    return {"running": _eval_status["running"], "latest": latest}
