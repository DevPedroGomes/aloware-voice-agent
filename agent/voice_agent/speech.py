"""Turning data into things that sound right when spoken.

The TTS reads exactly what it gets. "+15125550142" comes out as "plus one five billion..."
and "2026-11-24T10:30" is worse. So tools return pre-spoken strings and the session
strips markdown and emojis before synthesis.
"""

from __future__ import annotations

from datetime import datetime

_DIGITS = "zero one two three four five six seven eight nine".split()


def spoken_phone(e164: str) -> str:
    """+15125550142 -> 'five one two, five five five, zero one four two'."""
    digits = [c for c in e164 if c.isdigit()]
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return " ".join(_DIGITS[int(d)] for d in digits)
    groups = [digits[:3], digits[3:6], digits[6:]]
    return ", ".join(" ".join(_DIGITS[int(d)] for d in g) for g in groups)


def spoken_slot(dt: datetime, now: datetime) -> str:
    """'tomorrow at 10:30 AM' / 'Thursday at 2 PM', in the lead's time zone. TTS reads these naturally."""
    days = (dt.date() - now.date()).days
    if days == 0:
        day = "today"
    elif days == 1:
        day = "tomorrow"
    elif days < 7:
        day = dt.strftime("%A")
    else:
        day = dt.strftime("%A, %B %-d")
    minute = "" if dt.minute == 0 else f":{dt.minute:02d}"
    return f"{day} at {dt.strftime('%-I')}{minute} {dt.strftime('%p')}"
