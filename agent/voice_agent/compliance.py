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
        tz = store.lead_tz(lead)
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


# Phrases checked against the final STT transcript. Each one needs calling/contact/list
# context, because a false positive is expensive: "can we do a quick call tomorrow?" must
# not put a hot lead on the DNC list. The odd spellings are common STT errors.
_OPT_OUT_PATTERNS = [
    r"\b(stop|quit)\s+(calling|colin|collin|cooling|call(ing)?\s+me)\b",
    r"\b(don'?t|do\s*not|never)\s+(ever\s+)?(call|contact|phone)\s+(me|this number)\s+(again|anymore|any more|ever)\b",
    r"\b(take|remove|delete|get)\s+(me|my\s+(number|name|info\w*|phone(\s+number)?))\s+(off|of|from)\s+"
    r"(your|the|this)\s+(call(ing)?\s+)?(list|database|system|records)\b",
    r"\b(put|add)\s+(me|my number)\s+(on|to)\s+(your|the)\s+do\s*not\s*call\b",
    r"\bopt\s*(me\s*)?out\b(?!\s+of\b)",
    r"\bopt\s*(me\s*)?out\s+of\s+(these|your|the|this)\s+(calls|call list|list)\b",
    r"\bunsubscribe\b",
    r"\blose\s+(my|this)\s+number\b",
]
_OPT_OUT_RE = re.compile("|".join(_OPT_OUT_PATTERNS), re.IGNORECASE)


def is_opt_out(text: str) -> bool:
    return bool(_OPT_OUT_RE.search(text or ""))


OPT_OUT_GOODBYE = (
    "Understood. I've taken your number off our list, and you won't get another call "
    "from us. Sorry for the interruption, and have a good day."
)
