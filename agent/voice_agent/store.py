"""JSON-file persistence.

The brief says no database, so everything lives in `data/`. Seed files (config, leads)
are tracked in git; anything the agent writes at runtime goes to `data/runtime/`, which
is gitignored, so `rm -rf data/runtime` resets the demo.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("VOICE_AGENT_DATA_DIR", REPO_ROOT / "data"))
RUNTIME_DIR = DATA_DIR / "runtime"

CONFIG_PATH = DATA_DIR / "config.json"
LEADS_PATH = DATA_DIR / "leads.json"
DNC_PATH = RUNTIME_DIR / "dnc.json"
APPOINTMENTS_PATH = RUNTIME_DIR / "appointments.json"
CALLS_DIR = RUNTIME_DIR / "calls"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write, so the admin API never reads a half-written file from the agent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, path)


# ---------- leads ----------

def list_leads() -> list[dict]:
    return read_json(LEADS_PATH, [])


def get_lead(lead_id: str) -> dict | None:
    return next((lead for lead in list_leads() if lead["id"] == lead_id), None)


def lead_tz(lead: dict) -> ZoneInfo:
    return ZoneInfo(lead.get("timezone") or "America/New_York")


# ---------- do-not-call list ----------

def list_dnc() -> list[dict]:
    return read_json(DNC_PATH, [])


def is_on_dnc(phone: str) -> bool:
    return any(entry["phone"] == phone for entry in list_dnc())


def add_to_dnc(phone: str, *, reason: str, source: str, call_id: str | None = None) -> None:
    entries = list_dnc()
    if any(e["phone"] == phone for e in entries):
        return
    entries.append({"phone": phone, "reason": reason, "source": source, "call_id": call_id, "added_at": now_iso()})
    write_json(DNC_PATH, entries)


# ---------- appointments ----------

def list_appointments() -> list[dict]:
    return read_json(APPOINTMENTS_PATH, [])


def booked_slot_ids() -> set[str]:
    return {a["slot_id"] for a in list_appointments()}


def add_appointment(appt: dict) -> None:
    write_json(APPOINTMENTS_PATH, [*list_appointments(), appt])


# ---------- call records ----------

def save_call(record: dict) -> None:
    write_json(CALLS_DIR / f"{record['call_id']}.json", record)


def list_calls() -> list[dict]:
    if not CALLS_DIR.exists():
        return []
    calls = [read_json(p, {}) for p in CALLS_DIR.glob("*.json")]
    return sorted(calls, key=lambda c: c.get("started_at", ""), reverse=True)


def get_call(call_id: str) -> dict | None:
    path = CALLS_DIR / f"{call_id}.json"
    return read_json(path, None) if path.exists() else None
