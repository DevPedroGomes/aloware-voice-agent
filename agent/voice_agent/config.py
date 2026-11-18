"""Agent configuration: catalog, validation, versioning and safe template rendering.

The admin UI edits `data/config.json`. The agent re-reads it at the start of every
call, so a saved change applies to the next conversation without a restart. Every save
bumps `version`, and each call record stores the version it ran with, so "which prompt
was live when this call went wrong?" has an answer.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from . import store

# Cartesia Sonic 3 voices through LiveKit Inference. IDs checked against the Cartesia API.
VOICES: dict[str, dict[str, str]] = {
    "jacqueline": {
        "label": "Jacqueline: calm, reassuring (female, US)",
        "model": "cartesia/sonic-3",
        "voice_id": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
    },
    "katie": {
        "label": "Katie: friendly, upbeat (female, US)",
        "model": "cartesia/sonic-3",
        "voice_id": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    },
    "morgan": {
        "label": "Morgan: polished, executive (female, US)",
        "model": "cartesia/sonic-3",
        "voice_id": "0ee8beaa-db49-4024-940d-c7ea09b590b3",
    },
    "nolan": {
        "label": "Nolan: warm, conversational (male, US)",
        "model": "cartesia/sonic-3",
        "voice_id": "65209f8e-6140-4a20-b819-3cc2e21da19b",
    },
}

# `locked` tools are compliance or call-control primitives. An operator can switch off
# booking or transfers for a campaign, but never the caller's way to opt out.
TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "check_availability": {
        "label": "Check licensed-agent availability",
        "description": "Looks up open call slots with a licensed agent, in the lead's time zone.",
        "locked": False,
    },
    "book_appointment": {
        "label": "Book a call with a licensed agent",
        "description": "Books an offered slot, only after reading the time and callback number back to the caller.",
        "locked": False,
    },
    "transfer_to_licensed_agent": {
        "label": "Live transfer to a licensed agent",
        "description": "Warm transfer with a whisper summary (simulated locally; SIP in production).",
        "locked": False,
    },
    "mark_do_not_call": {
        "label": "Mark Do Not Call",
        "description": "Adds the number to the DNC list and ends the call. Always on: TCPA.",
        "locked": True,
    },
    "end_call": {
        "label": "End call with disposition",
        "description": "Says goodbye, hangs up and records the outcome. Always on.",
        "locked": True,
    },
}

TEMPLATE_VARS = {
    "first_name": "Lead's first name (may be empty)",
    "last_name": "Lead's last name (may be empty)",
    "city": "Lead's city",
    "agent_name": "Persona name",
    "company": "Company name",
    "product": "Product the lead asked about",
}

AI_DISCLOSURE_RE = re.compile(
    r"\b(AI|A\.I\.|artificial intelligence|virtual assistant|automated assistant)\b", re.IGNORECASE
)
_TEMPLATE_RE = re.compile(r"\{\{\s*(\w+)\s*(?:\|\s*([^}]*?)\s*)?\}\}")

# Spoken fallbacks used when a variable is empty and the template gave none.
_DEFAULT_FALLBACKS = {"first_name": "there", "last_name": "", "city": "your area"}


def render_template(text: str, values: dict[str, str]) -> str:
    """Fill `{{var}}` / `{{var|fallback}}` without ever saying a raw placeholder.

    "Hi {{first_name}}" with no name becomes "Hi there" instead of "Hi" + silence or,
    worse, the TTS reading curly braces out loud.
    """

    def sub(m: re.Match[str]) -> str:
        name, fallback = m.group(1), m.group(2)
        value = (values.get(name) or "").strip()
        if value:
            return value
        if fallback is not None:
            return fallback
        return _DEFAULT_FALLBACKS.get(name, "")

    rendered = _TEMPLATE_RE.sub(sub, text)
    rendered = re.sub(r"\s+([,.?!])", r"\1", rendered)
    return re.sub(r"\s{2,}", " ", rendered).strip()


def template_values(cfg: dict, lead: dict) -> dict[str, str]:
    return {
        "first_name": lead.get("first_name", ""),
        "last_name": lead.get("last_name", ""),
        "city": lead.get("city", ""),
        "agent_name": cfg["persona"]["name"],
        "company": cfg["persona"]["company"],
        "product": lead.get("product", "insurance"),
    }


def validate(cfg: dict) -> list[str]:
    errors: list[str] = []
    persona = cfg.get("persona", {})
    if not persona.get("name", "").strip():
        errors.append("Persona name is required.")
    if not persona.get("company", "").strip():
        errors.append("Company name is required.")
    if persona.get("voice") not in VOICES:
        errors.append(f"Unknown voice '{persona.get('voice')}'.")

    greeting = cfg.get("greeting", "")
    if not greeting.strip():
        errors.append("Greeting is required.")
    for m in _TEMPLATE_RE.finditer(greeting):
        if m.group(1) not in TEMPLATE_VARS:
            errors.append(f"Unknown variable {{{{{m.group(1)}}}}} in greeting.")
    if not AI_DISCLOSURE_RE.search(greeting):
        errors.append(
            "Greeting must tell the person they are talking to an AI (FCC, Feb 2024: "
            "AI voices count as 'artificial' under the TCPA). Add e.g. 'an AI assistant'."
        )
    if len(greeting.split()) > 60:
        errors.append("Greeting is too long for a phone opener (60 words max).")

    if not cfg.get("instructions", "").strip():
        errors.append("Instructions are required.")

    tools = cfg.get("tools", {})
    for name in tools:
        if name not in TOOL_CATALOG:
            errors.append(f"Unknown tool '{name}'.")
    for name, meta in TOOL_CATALOG.items():
        if meta["locked"] and not tools.get(name, False):
            errors.append(f"'{meta['label']}' is a compliance tool and cannot be disabled.")
    if tools.get("book_appointment") and not tools.get("check_availability"):
        errors.append("Booking needs 'Check licensed-agent availability' turned on: it only books offered slots.")

    comp = cfg.get("compliance", {})
    start, end = comp.get("calling_hours_start", 8), comp.get("calling_hours_end", 21)
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= 24):
        errors.append("Calling hours must be whole hours with start < end.")
    elif start < 8 or end > 21:
        errors.append("TCPA allows calls only between 8 AM and 9 PM in the lead's local time.")

    if cfg.get("active_lead_id") and not store.get_lead(cfg["active_lead_id"]):
        errors.append(f"Unknown lead '{cfg['active_lead_id']}'.")
    return errors


def load() -> dict:
    cfg = store.read_json(store.CONFIG_PATH, None)
    if cfg is None:
        raise RuntimeError(f"Missing config at {store.CONFIG_PATH}")
    # Tools added to the catalog after the config was written default to off,
    # except locked ones, which are always on.
    for name, meta in TOOL_CATALOG.items():
        cfg.setdefault("tools", {}).setdefault(name, meta["locked"])
    return cfg


def save(new_cfg: dict) -> dict:
    errors = validate(new_cfg)
    if errors:
        raise ValueError(errors)
    cfg = copy.deepcopy(new_cfg)
    cfg["version"] = int(load().get("version", 0)) + 1
    cfg["updated_at"] = store.now_iso()
    store.write_json(store.CONFIG_PATH, cfg)
    return cfg


def enabled_tools(cfg: dict) -> list[str]:
    return [name for name in TOOL_CATALOG if cfg.get("tools", {}).get(name)]
