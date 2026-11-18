"""The outbound speed-to-lead agent and its session wiring."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from livekit.agents import (
    Agent,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    StopResponse,
    TurnHandlingOptions,
    UserStateChangedEvent,
    inference,
    llm,
)

from . import config as config_mod
from . import store
from .compliance import OPT_OUT_GOODBYE, is_opt_out, precall_gate
from .prompt import build_instructions
from .tools import ALL_TOOLS, CallState, hang_up

logger = logging.getLogger("voice-agent")


class OutboundLeadAgent(Agent):
    def __init__(self, state: CallState) -> None:
        enabled = config_mod.enabled_tools(state.cfg)
        super().__init__(
            instructions=build_instructions(state.cfg, state.lead, enabled),
            tools=[ALL_TOOLS[name] for name in enabled],
        )
        self._state = state

    async def on_enter(self) -> None:
        # The opener is scripted, not generated: it is the same every call, it carries
        # the legally required AI disclosure, and it skips an LLM round-trip so the
        # agent speaks the moment the call connects.
        greeting = config_mod.render_template(
            self._state.cfg["greeting"], config_mod.template_values(self._state.cfg, self._state.lead)
        )
        self.session.say(greeting, allow_interruptions=True)

    def _handle_opt_out(self, text: str) -> bool:
        """Deterministic opt-out: DNC + scripted goodbye, no LLM involved."""
        if not is_opt_out(text):
            return False
        logger.info("opt-out detected: %r", text)
        store.add_to_dnc(
            self._state.lead["phone"], reason=text[:200], source="opt_out_detector", call_id=self._state.call_id
        )
        self._state.crm_note = f'Opted out: "{text[:120]}"'
        hang_up(self.session, self._state, OPT_OUT_GOODBYE, "dnc")
        return True

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        # Voice path: runs as soon as the caller's turn ends, before any LLM call.
        if self._state.ending or self._handle_opt_out(new_message.text_content or ""):
            raise StopResponse()

    async def llm_node(self, chat_ctx: llm.ChatContext, tools, model_settings):
        # Every path to the model goes through here, including text input and
        # session.run() (which skip on_user_turn_completed). Same check, second gate.
        if self._state.ending:
            return
        last_user = next((m for m in reversed(chat_ctx.items) if m.type == "message" and m.role == "user"), None)
        is_fresh_turn = bool(chat_ctx.items) and chat_ctx.items[-1] is last_user
        if last_user is not None and is_fresh_turn and self._handle_opt_out(last_user.text_content or ""):
            return
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk


def new_call_state(cfg: dict, lead: dict) -> CallState:
    return CallState(
        call_id=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        cfg=cfg,
        lead=lead,
        started_at=store.now_iso(),
    )


def make_llm(cfg: dict) -> inference.LLM:
    model = cfg.get("models", {}).get("llm", "openai/gpt-5.4-mini")
    # Reasoning off for OpenAI models: measured p50 time-to-first-token 0.80 s vs 0.89 s
    # with the default, and a much tighter max (0.85 s vs 1.47 s). See scripts/llm_ttft.py.
    if model.startswith("openai/gpt-5"):
        return inference.LLM(model=model, reasoning="none")
    return inference.LLM(model=model)


def build_session(state: CallState) -> AgentSession[CallState]:
    cfg = state.cfg
    voice = config_mod.VOICES[cfg["persona"]["voice"]]
    models = cfg.get("models", {})
    return AgentSession[CallState](
        userdata=state,
        stt=inference.STT(model=models.get("stt", "deepgram/nova-3"), language="en"),
        llm=make_llm(cfg),
        tts=inference.TTS(model=voice["model"], voice=voice["voice_id"], language="en"),
        turn_handling=TurnHandlingOptions(
            # Audio end-of-turn model: hears "my renewal is in, uh..." as unfinished.
            turn_detection=inference.TurnDetector(),
            # Tells a real barge-in from "mm-hm" / "yeah" while the agent is talking.
            interruption={"mode": "adaptive"},
        ),
        tts_text_transforms=["filter_markdown", "filter_emoji"],
        max_tool_steps=4,
        # Silence: after 12 s, "are you still there?", then hang up 10 s later.
        user_away_timeout=12.0,
    )


def call_record(state: CallState, session: AgentSession | None, *, blocked: dict | None = None) -> dict:
    transcript, tool_calls, turns = [], [], []
    if session is not None:
        for item in session.history.items:
            if item.type == "message" and item.role in ("user", "assistant"):
                text = item.text_content or ""
                if not text:
                    continue
                transcript.append({"role": item.role, "text": text, "interrupted": item.interrupted})
                m = dict(item.metrics or {})
                if item.role == "assistant" and m:
                    turns.append(
                        {
                            "text": text[:80],
                            "e2e_latency": m.get("e2e_latency"),
                            "llm_ttft": m.get("llm_node_ttft"),
                            "tts_ttfb": m.get("tts_node_ttfb"),
                        }
                    )
            elif item.type == "function_call":
                tool_calls.append({"name": item.name, "arguments": item.arguments})
            elif item.type == "function_call_output":
                for tc in reversed(tool_calls):
                    if tc["name"] == item.name and "output" not in tc:
                        tc["output"] = item.output
                        tc["is_error"] = item.is_error
                        break
    e2e = sorted(t["e2e_latency"] for t in turns if t["e2e_latency"] is not None)
    return {
        "call_id": state.call_id,
        "config_version": state.cfg.get("version"),
        "persona": state.cfg["persona"]["name"],
        "voice": state.cfg["persona"]["voice"],
        "lead_id": state.lead["id"],
        "lead_name": " ".join(p for p in [state.lead.get("first_name"), state.lead.get("last_name")] if p),
        "started_at": state.started_at,
        "ended_at": store.now_iso(),
        "disposition": state.disposition or ("blocked" if blocked else "hung_up"),
        "crm_note": state.crm_note,
        "blocked": blocked,
        "appointment": state.appointment,
        "transfer": state.transfer,
        "transcript": transcript,
        "tool_calls": tool_calls,
        "latency": {
            "turns": turns,
            "e2e_p50": e2e[len(e2e) // 2] if e2e else None,
            "e2e_max": e2e[-1] if e2e else None,
        },
    }


async def run_call(ctx: JobContext) -> None:
    # Re-read config and lead on every call: this is how an admin UI change applies to
    # the next conversation without restarting the worker.
    cfg = config_mod.load()
    lead = store.get_lead(cfg["active_lead_id"])
    if lead is None:
        raise RuntimeError(f"Active lead {cfg['active_lead_id']} not found")
    state = new_call_state(cfg, lead)
    logger.info("call %s | config v%s | lead %s", state.call_id, cfg.get("version"), lead["id"])

    gate = precall_gate(lead, cfg)
    if not gate.allowed:
        banner = f"CALL BLOCKED BEFORE DIALING [{gate.code}] {gate.reason}"
        logger.warning(banner)
        print(f"\n{'=' * 70}\n{banner}\n{'=' * 70}\n", flush=True)
        store.save_call(call_record(state, None, blocked={"code": gate.code, "reason": gate.reason}))
        await asyncio.sleep(1.5)  # let the console show the reason before the job goes away
        ctx.shutdown(reason=f"blocked:{gate.code}")
        return

    session = build_session(state)

    async def _hang_up_if_still_silent() -> None:
        await asyncio.sleep(10)
        if session.user_state == "away" and not state.ending:
            state.crm_note = "Lead went silent mid-call."
            hang_up(session, state, "Sounds like I lost you. I'll try you another time. Bye for now.", "no_response")

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        # The SDK fires "away" once per silence, so the hang-up is our own timer,
        # cancelled as soon as the caller speaks again.
        if state.ending:
            return
        if ev.new_state == "away":
            session.say("Hello? Are you still there?", allow_interruptions=True)
            state.silence_task = asyncio.create_task(_hang_up_if_still_silent())
        elif state.silence_task is not None:
            state.silence_task.cancel()
            state.silence_task = None

    @session.on("close")
    def _on_close(_ev) -> None:
        store.save_call(call_record(state, session))
        logger.info("call %s saved, disposition=%s", state.call_id, state.disposition)
        ctx.shutdown(reason="session closed")

    await session.start(agent=OutboundLeadAgent(state), room=ctx.room)

    background = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.35),
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.6, probability=0.7),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.5, probability=0.3),
        ],
    )
    try:
        await background.start(room=ctx.room, agent_session=session)
        state.background = background
    except Exception:  # background audio is a nicety; never fail the call over it
        logger.exception("background audio unavailable")
