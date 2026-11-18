"""The five tools Maya can use, plus the shared hang-up path.

Design choices worth calling out:
- Tools return pre-spoken strings ("tomorrow at 10:30 AM", phone numbers as digit groups)
  so the model never has to reformat machine data for speech.
- `book_appointment` makes the read-back structural: the first call for a slot never
  books, it hands the model the line to read back. Only a later call, after the caller
  has answered, writes the booking. The model can't skip the step by being confident.
- Every tool that ends the call speaks its own goodbye and returns None. A None output
  means no second LLM round-trip after the tool, which on a call is 1-2 s of dead air
  before the goodbye.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from livekit.agents import (
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    RunContext,
    ToolError,
    function_tool,
)

from . import store
from .compliance import OPT_OUT_GOODBYE
from .speech import spoken_phone, spoken_slot

logger = logging.getLogger("voice-agent.tools")

SLOT_TIMES = [(9, 0), (10, 30), (13, 0), (14, 30), (16, 0)]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

Disposition = Literal["booked", "not_interested", "call_back_later", "wrong_person", "other"]


@dataclass
class CallState:
    call_id: str
    cfg: dict
    lead: dict
    started_at: str
    disposition: str | None = None
    crm_note: str | None = None
    offered_slots: dict[str, str] = field(default_factory=dict)
    appointment: dict | None = None
    # (slot_id, callback number) read back to the caller, and how many user turns existed then
    readback: tuple[tuple[str, str], int] | None = None
    transfer: dict | None = None
    silence_task: asyncio.Task | None = None
    ending: bool = False
    background: BackgroundAudioPlayer | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)

    @property
    def tz(self) -> ZoneInfo:
        return store.lead_tz(self.lead)

    def local_now(self) -> datetime:
        return datetime.now(tz=self.tz)


def hang_up(session: AgentSession, state: CallState, goodbye: str, disposition: str, *, hold_seconds: float = 0) -> None:
    """Say the goodbye (not interruptible), optionally play hold music, then end the session."""
    state.disposition = state.disposition if state.ending else disposition
    if state.ending:
        return
    state.ending = True
    handle = session.say(goodbye, allow_interruptions=False)

    async def _finish() -> None:
        try:
            await handle
            if hold_seconds and state.background is not None:
                state.background.play(AudioConfig(BuiltinAudioClip.HOLD_MUSIC, volume=0.5))
                await asyncio.sleep(hold_seconds)
        finally:
            session.shutdown()

    task = asyncio.create_task(_finish())
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)


# ---------- availability ----------

def _parse_day(text: str, today: date) -> date | None:
    t = (text or "").strip().lower()
    if not t or t in {"any", "anytime", "soon", "earliest", "asap", "this week", "next available"}:
        return None
    if "today" in t:
        return today
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        pass
    for i, name in enumerate(WEEKDAYS):
        if name in t or name[:3] == t[:3]:
            ahead = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    return None


def _slots_for_day(day: date, state: CallState, now: datetime) -> list[datetime]:
    if day.weekday() >= 5:
        return []
    booked = store.booked_slot_ids()
    out = []
    for i, (h, m) in enumerate(SLOT_TIMES):
        if (day.toordinal() + i) % 3 == 0:  # deterministic "already taken" slots
            continue
        dt = datetime(day.year, day.month, day.day, h, m, tzinfo=state.tz)
        if dt <= now + timedelta(minutes=30) or dt.isoformat() in booked:
            continue
        out.append(dt)
    return out


@function_tool()
async def check_availability(context: RunContext[CallState], day: str = "") -> str:
    """Look up open call slots with a licensed agent, in the lead's local time.

    Args:
        day: The day the caller asked for, in their words ("tomorrow", "Thursday",
            "2026-11-24"). Leave empty for the next available slots.
    """
    state = context.userdata
    now = state.local_now()
    wanted = _parse_day(day, now.date())

    async with context.with_filler("One sec, I'm pulling up the calendar.", delay=2.5):
        await asyncio.sleep(0.4)  # stands in for a real calendar API call
        if wanted is not None:
            slots = _slots_for_day(wanted, state, now)[:3]
        else:
            slots = []
            d = now.date()
            while len(slots) < 3 and (d - now.date()).days < 10:
                slots += _slots_for_day(d, state, now)
                d += timedelta(days=1)
            slots = slots[:3]

    if not slots:
        return (
            f"No open slots on {day or 'the next few days'}. Offer the next business day "
            "or ask what other day works."
        )
    for s in slots:
        state.offered_slots[s.isoformat()] = spoken_slot(s, now)
    lines = [f"- slot_id={s.isoformat()} -> say '{spoken_slot(s, now)}'" for s in slots]
    return "Open slots in the lead's local time (offer at most two out loud):\n" + "\n".join(lines)


def _user_turns(context: RunContext) -> int:
    return sum(1 for item in context.session.history.items if item.type == "message" and item.role == "user")


@function_tool()
async def book_appointment(
    context: RunContext[CallState],
    slot_id: str,
    notes: str,
    callback_number: str = "",
) -> str:
    """Book a call with a licensed agent. Call it as soon as the caller picks a time.

    The first call for a slot does not book: it returns the read-back line to say. Call
    it again with the same slot after the caller confirms, and that call books.

    Args:
        slot_id: The slot_id returned by check_availability.
        notes: Qualification answers for the licensed agent (homeowner, roof age,
            current carrier, renewal date, anything else useful).
        callback_number: Digits only (e.g. 5125550142), and only if the caller gave a
            different number than the one on file. Leave empty otherwise.
    """
    state = context.userdata
    digits = "".join(c for c in callback_number if c.isdigit())
    if callback_number.strip() and len(digits) < 10:
        raise ToolError("callback_number must be digits like 5125550142, or empty for the number on file.")
    number = digits or state.lead["phone"]
    spoken_number = spoken_phone(number)

    slot_label = state.offered_slots.get(slot_id)
    if slot_label is None:
        try:
            dt = datetime.fromisoformat(slot_id)
        except ValueError:
            raise ToolError("Unknown slot_id. Call check_availability and use one of its slot_ids.") from None
        now = state.local_now()
        # A slot without an offset means the lead's local time, never the server's.
        local = dt.replace(tzinfo=state.tz) if dt.tzinfo is None else dt.astimezone(state.tz)
        if local <= now or local.weekday() >= 5 or (local.hour, local.minute) not in SLOT_TIMES:
            raise ToolError("That time isn't bookable. Call check_availability and offer one of its slots.")
        slot_id = local.isoformat()
        slot_label = spoken_slot(local, now)

    if slot_id in store.booked_slot_ids():
        raise ToolError("That slot was just taken. Apologize and offer another time.")

    key, turns = (slot_id, spoken_number), _user_turns(context)
    if state.readback is None or state.readback[0] != key or turns <= state.readback[1]:
        state.readback = (key, turns)
        return (
            f"NOT booked yet. Read this back and ask if it's right: '{slot_label}, and the agent "
            f"will call you at {spoken_number}.' If they confirm, call book_appointment again "
            "with the same slot_id. If they change the time or number, use the new details."
        )

    context.disallow_interruptions()  # don't let a cough cancel a half-written booking
    appt = {
        "slot_id": slot_id,
        "spoken": slot_label,
        "lead_id": state.lead["id"],
        "callback_number": number,
        "notes": notes,
        "call_id": state.call_id,
        "booked_at": store.now_iso(),
    }
    store.add_appointment(appt)
    state.appointment = appt
    state.disposition = "booked"
    logger.info("booked %s for %s", slot_label, state.lead["id"])
    return (
        f"Booked: {slot_label}, a licensed agent will call {spoken_number}. A text "
        "confirmation is on its way. Confirm in one short sentence, ask if there's anything "
        "else, then end the call."
    )


@function_tool()
async def transfer_to_licensed_agent(context: RunContext[CallState], reason: str, whisper_summary: str) -> None:
    """Warm-transfer the caller to a licensed agent right now. Only after the caller said
    yes to being connected now (ask first; never transfer just because qualifying is done).

    Args:
        reason: Why the caller is being transferred, in a few words.
        whisper_summary: One or two sentences the licensed agent hears before picking
            up: who the caller is and what they qualified for so far.
    """
    state = context.userdata
    state.transfer = {"reason": reason, "whisper": whisper_summary, "at": store.now_iso()}
    state.crm_note = whisper_summary
    logger.info("SIMULATED WARM TRANSFER -> licensed agent. Whisper: %s", whisper_summary)
    print(
        "\n[transfer] In production this is a LiveKit WarmTransferTask over SIP to the "
        f"licensed agent queue.\n[transfer] Whisper to agent: {whisper_summary}\n",
        flush=True,
    )
    hang_up(
        context.session,
        state,
        "Perfect. I'm connecting you with a licensed agent now, it'll just be a moment.",
        "transferred",
        hold_seconds=4,
    )
    return None


@function_tool()
async def mark_do_not_call(context: RunContext[CallState], reason: str) -> None:
    """Put this number on the Do Not Call list and end the call. Use as soon as the
    person asks not to be contacted again, in any wording.

    Args:
        reason: The person's request, in a few words.
    """
    state = context.userdata
    store.add_to_dnc(state.lead["phone"], reason=reason, source="llm_tool", call_id=state.call_id)
    state.crm_note = f"Opted out: {reason}"
    hang_up(context.session, state, OPT_OUT_GOODBYE, "dnc")
    return None


@function_tool()
async def end_call(context: RunContext[CallState], disposition: Disposition, goodbye: str, crm_note: str) -> None:
    """Say goodbye and hang up. This is the only way to end the call.

    Args:
        disposition: The call outcome.
        goodbye: The last sentence you say, short and warm. It is spoken before hanging up.
        crm_note: One sentence for the CRM timeline: what happened and the next step.
    """
    state = context.userdata
    state.crm_note = crm_note
    hang_up(context.session, state, goodbye, disposition)
    return None


ALL_TOOLS = {
    "check_availability": check_availability,
    "book_appointment": book_appointment,
    "transfer_to_licensed_agent": transfer_to_licensed_agent,
    "mark_do_not_call": mark_do_not_call,
    "end_call": end_call,
}
