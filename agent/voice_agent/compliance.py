"""Deterministic compliance checks. None of this is left to the LLM.

Two layers:
1. A pre-call gate that runs before the agent says a word: consent on file, not on the
   do-not-call list, inside calling hours in the lead's local time.
2. An opt-out detector that runs on every finished user turn. "Stop calling me" goes
   straight to the DNC list and a scripted goodbye, even if the model would have
   tried to "save" the lead. The LLM still has a `mark_do_not_call` tool for the
   fuzzy cases ("I don't want you people contacting me"), so the regex only needs to
   be precise, not complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from . import store


@dataclass
class GateResult:
    allowed: bool
    code: str | None = None
    reason: str | None = None


def precall_gate(lead: dict, cfg: dict, now: datetime | None = None) -> GateResult:
    consent = lead.get("consent") or {}
    if not consent.get("given"):
        return GateResult(
            False,
            "no_consent",
            "No express written consent on file. AI voice calls need it under the TCPA "
            "(FCC declaratory ruling, Feb 2024).",
        )

    if store.is_on_dnc(lead["phone"]):
        return GateResult(False, "dnc", "Number is on the internal Do Not Call list.")

    comp = cfg.get("compliance", {})
    if comp.get("enforce_calling_hours", True):
        tz = ZoneInfo(lead.get("timezone") or "America/New_York")
        local = (now or datetime.now(tz=ZoneInfo("UTC"))).astimezone(tz)
        start, end = comp.get("calling_hours_start", 8), comp.get("calling_hours_end", 21)
        if not (start <= local.hour < end):
            return GateResult(
                False,
                "outside_hours",
                f"It is {local:%-I:%M %p} for this lead ({tz.key}). Calls are allowed "
                f"{start}:00-{end}:00 local time. To test anyway, turn off "
                "'Enforce calling hours' in the admin UI.",
            )
    return GateResult(True)


# Phrases checked against the final STT transcript. The odd spellings are real STT
# errors for "stop calling", "take me off" and "do not call".
_OPT_OUT_PATTERNS = [
    r"\b(stop|quit|quick)\s+(calling|call|colin|collin|cooling|calling me|call me)\b",
    r"\bdo\s*n[o']?t\s+call\s+(me\s+)?(again|anymore|any more|ever)\b",
    r"\bdon'?t\s+(ever\s+)?(call|contact|phone)\s+(me|this number)\s+(again|anymore|any more|ever)\b",
    r"\bnever\s+(call|contact)\s+(me|this number)\s+again\b",
    r"\b(take|remove|get|delete)\s+(me|my\s+(number|name|info\w*|phone))\s+(off|of|from)\b",
    r"\b(put|add)\s+(me|my number)\s+(on|to)\s+(your|the)\s+do\s*not\s*call\b",
    r"\b(unsubscribe|opt\s*me\s*out|opt\s*out)\b",
    r"\blose\s+(my|this)\s+number\b",
]
_OPT_OUT_RE = re.compile("|".join(_OPT_OUT_PATTERNS), re.IGNORECASE)


def is_opt_out(text: str) -> bool:
    return bool(_OPT_OUT_RE.search(text or ""))


OPT_OUT_GOODBYE = (
    "Understood. I've taken your number off our list, and you won't get another call "
    "from us. Sorry for the interruption, and have a good day."
)
