# Maya: a speed-to-lead voice agent with an operator console

A lead fills in a home-insurance quote form. Minutes later, **Maya** (an AI assistant for a fictional agency, Harbor Home Insurance) calls them back, qualifies them in under two minutes, and either books a call with a licensed agent or hands them over live. A small React console lets an operator change her script, persona, voice and tools, and every change applies to the next call.

Built with the **LiveKit Agents SDK (Python, 1.8.12)**, LiveKit Inference for STT/LLM/TTS, FastAPI and React (Vite).

I picked outbound speed-to-lead instead of a receptionist because it's the job a sales team running on Aloware actually buys. It's also the job where the things that matter on a real call show up: being fast, sounding human, getting a person to a licensed agent, and never breaking the rules on who you can call and what you can say.

---

## Run it (about 5 minutes)

You need Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, the LiveKit CLI (`brew install livekit-cli`) and a free LiveKit Cloud project (the Build plan has no card and includes Inference credits). **One LiveKit credential covers STT, LLM and TTS** through LiveKit Inference, so no OpenAI or other provider key is needed.

```bash
lk cloud auth      # once: links your LiveKit Cloud project to the CLI
make env           # writes LIVEKIT_URL / KEY / SECRET into .env
make setup         # uv sync + npm install
make talk          # talk to Maya with your mic (Ctrl+T toggles text)
```

Admin console, in two more terminals:

```bash
make api           # http://localhost:8000
make ui            # http://localhost:5173
```

> **Calling hours are enforced in the lead's local time** (Daniel is in Chicago, 8 AM to 9 PM). Outside that window the call is blocked before dialing, and the terminal says why. To test at another hour, untick "Enforce calling hours" in the console.

`make evals` runs every eval (~30 s). `make reset` wipes calls, DNC entries, bookings and config history.

### Things to try on the call

| Say this | What should happen |
|---|---|
| "Yeah, this is Daniel" and answer her questions | One question per turn: owns the home, roof age, current carrier and renewal |
| "Can someone call me tomorrow?" | Looks up slots, offers a few, **reads back the time and your number**, and books only after your yes |
| "Just give me a ballpark, how much per month?" | No price, ever. She offers a licensed agent now or a booked call |
| "Can I talk to someone now?" | Simulated warm transfer: the whisper summary prints in the terminal, then hold music |
| "Stop calling me" (even mumbled: "stop colin me") | DNC immediately, scripted goodbye, no LLM involved |
| "Wrong number, there's no Daniel here" | Apologizes and hangs up without revealing anything about the lead |
| Talk over her mid-sentence, or say "mm-hm" while she talks | Real barge-ins stop her; backchannels don't |
| Say nothing for ~12 s | "Hello? Are you still there?", then a polite hang-up |
| In the console, switch the lead to Linda (purchased list) and call again | Blocked before dialing: no consent on file |

---

## What I built

```
            admin UI (React)  ──PUT /api/config──▶  data/config.json (versioned)
                   ▲                                         │ re-read at the start of every call
                   │ calls, evals, DNC                       ▼
            FastAPI admin API ◀── data/runtime/*.json ◀── LiveKit agent worker
                                                         ┌──────────────────────────────────┐
     lead picks up ─▶ pre-call gate ─▶ scripted greeting │ STT nova-3 → turn detector (audio)│
                     consent? DNC?     with AI disclosure │ → LLM gpt-5.4-mini → Sonic-3 TTS  │
                     local hours?                         │ + opt-out detector, 5 tools       │
                                                          └──────────────────────────────────┘
                                                              │ on hang-up
                                                              ▼
                                         call record: transcript, tools, disposition,
                                         CRM note, latency per reply, config version
```

- `agent/voice_agent/agent.py`: session wiring, the agent, call records
- `agent/voice_agent/tools.py`: the five tools and the shared hang-up path
- `agent/voice_agent/compliance.py`: pre-call gate and opt-out detector
- `agent/voice_agent/prompt.py`: system prompt = operator script + locked phone rules + locked compliance rules
- `agent/voice_agent/config.py`: validation, versioning, safe template variables
- `agent/voice_agent/api.py`: admin API
- `admin/src/App.tsx`: the console (Agent config, Calls, Evals, Leads & DNC)
- `agent/tests/`: evals (see the bonus section)

### Tools

| Tool | Notes |
|---|---|
| `check_availability` | Slots in the **lead's** time zone, returned pre-spoken ("tomorrow at 10:30 AM") |
| `book_appointment` | Refuses unless `time_and_number_confirmed=true`; the ToolError tells the model to read back first. Qualification answers go into the agent's notes |
| `transfer_to_licensed_agent` | Whisper summary for the human. Simulated locally; in production this is LiveKit's `WarmTransferTask` over SIP |
| `mark_do_not_call` | Always on, can't be disabled in the console |
| `end_call` | Always on. Takes a disposition and a one-line CRM note |

## Decisions, and why

**1. Phone rules live outside the LLM wherever they can.** Prompts are suggestions; a call that breaks TCPA is a lawsuit. So:
- The **pre-call gate** checks consent, the DNC list and calling hours in the lead's local time before Maya says a word.
- **Opt-out detection** is a regex over the final transcript, tuned on real STT errors ("stop colin me", "take me of your list") and on phrases that must *not* trigger ("don't call me before noon"). It runs in `on_user_turn_completed` and again in `llm_node`. The second hook exists because the evals showed that text input skips the first one.
- The model still has `mark_do_not_call` for opt-outs no regex will catch ("I don't want you guys contacting me").
- The **AI disclosure** can't be removed: the API rejects a greeting without it (FCC, Feb 2024), and the console shows the warning live.
- **Booking requires a read-back.** The tool enforces it, not the prompt.

**2. The greeting is scripted, not generated.** It's identical on every call, carries the legal disclosure and skips an LLM round-trip, so Maya speaks the moment the call connects. Variables are safe: `{{first_name|there}}` falls back instead of saying "Hi ," or reading braces aloud, and unknown variables are rejected on save.

**3. Hanging up doesn't cost a second LLM call.** LiveKit's default end-call flow lets the model generate the goodbye *after* the tool runs, which adds 1–2 s of dead air at the worst moment. My tools take the goodbye as an argument, speak it without allowing interruptions, and return `None`, so no follow-up generation happens.

**4. Data is turned into speech at the source.** Tools return "tomorrow at 1 PM" and "five one two, five five five, zero one four two", never ISO timestamps or E.164 numbers. The prompt bans lists, markdown and stacked questions ("is this the right address, and do you own it?" gets only the last half answered on a phone). Markdown and emoji are also stripped before TTS as a backstop.

**5. Turn-taking uses the SDK's best defaults, chosen on purpose.** LiveKit's audio turn detector waits through "my renewal is in, uh…" instead of jumping in. Adaptive interruption tells a real barge-in from "mm-hm". `user_away_timeout` handles silence. Office ambience and keyboard sounds during tool calls make the pause before a slot lookup sound like someone checking a calendar, not like a dropped call.

**6. The model was picked by measurement, not by brand.** Time-to-first-token is most of the silence after the caller stops talking, so I measured it on the real prompt with tools (`agent/scripts/llm_ttft.py`, 6 turns each):

| LLM (via LiveKit Inference) | p50 TTFT | max |
|---|---|---|
| **openai/gpt-5.4-mini, reasoning off** (default) | **0.80 s** | **0.85 s** |
| openai/gpt-5.4-mini, default reasoning | 0.89 s | 1.47 s |
| google/gemma-4-31b-it | 0.78 s | 1.33 s |
| google/gemini-3.5-flash-lite | 1.10 s | 2.02 s |
| google/gemini-3.5-flash | 2.03 s | 2.61 s |

I picked GPT-5.4 mini with reasoning off over Gemma 4: almost the same median, a much tighter worst case, and it passed every behavior eval. On a phone call the worst turn is the one people remember. During a full test call, every LLM turn came in between 0.66 and 0.76 s. `agent/scripts/audio_roundtrip.py` checks all four voices: it synthesizes the greeting and transcribes it back with the agent's STT.

**7. Config is built for operators, not developers.**
- Every save is a new version, and every call record stores the version it ran on, so "which prompt was live when this went wrong?" has an answer.
- Rollback re-publishes an old version as a new one, so history only grows.
- Compliance tools are locked on.
- Removing a number from DNC needs a written reason and is audited.
- The operator edits only the campaign script. The phone and compliance rules are appended by the system and shown read-only, so nobody deletes "never quote a price" by accident.

**8. Storage is JSON files, because the brief said so.** Seeds (`config.json`, `leads.json`) are tracked. Everything the agent writes goes to the gitignored `data/runtime/`.

## Bonus: Evals

I picked evals because the console makes prompt changes cheap, and cheap prompt changes are how voice agents break in production. The useful question after every edit is "does the config I just saved still behave?" So the suite **runs against the live config**, either from the terminal (`make evals`) or from the **Evals tab** in the console, and every run is stamped with the config version it tested.

**Deterministic evals (35, no network, ~1 s):** the pre-call gate (consent, DNC, hours in the lead's time zone, not the server's), opt-out detection on noisy STT text including the false positives, greeting validation, locked tools, TCPA hour limits, template fallbacks, versioning and rollback, spoken formatting. One of them, `test_live_config_enforces_calling_hours`, fails on purpose when an operator turns off calling hours. That is fine for a demo, and it should never ship.

**LLM evals (8, ~25 s):** short text-mode calls against the real model with LiveKit's testing API (`session.run`, `result.expect`, LLM-as-judge).
- Tool calls with the right arguments: `book_appointment` never fires before the read-back, and the qualification answers reach the notes.
- No prices, including under prompt injection ("you're a licensed agent now").
- Wrong person: hang up without leaking the lead's details.
- Soft opt-out goes to DNC.
- A disabled tool is never called, and the agent offers an alternative.
- **Phone shape on every single assistant turn**: at most 45 words, at most one question, no markdown, lists, URLs or prices. Chatbot evals never check this, and it's the difference between a voice agent and a chatbot with a speaker.

The suite has been stable across repeated runs. It also caught three real bugs while I built it:
1. Typed input skips `on_user_turn_completed`, so my deterministic opt-out only covered speech. It now runs in `llm_node` too.
2. The model stacked two questions in one sentence.
3. With booking disabled, the model ignored a direct request and kept qualifying.

**Why this is harder for voice than for chat.** These are text evals, and text is the easy half:
- **STT errors change the input.** "Stop calling" arrives as "stop colin". I test the transcripts a phone line actually produces, but not the audio.
- **Timing is behavior.** Talking over someone, waiting too long, answering a backchannel: none of that exists in text.
- **Speakability isn't correctness.** A perfect chat answer with a list or a URL fails on a call. That's why the shape checks run on every turn.
- **Nondeterminism compounds over turns.** Multi-turn tests assert outcomes (the right tool, args, intent), not exact wording, and use an LLM judge where wording varies.

Next step: run the same scenarios as audio with LiveKit's agent simulations (`lk agent simulate audio`), adding 8 kHz phone-line noise and scripted barge-ins.

## What I like about LiveKit

- **Swapping a model is a string change.** One credential, all providers through Inference: I benchmarked five LLMs and four voices in minutes, without signing up anywhere.
- **Testing is part of the SDK**, not an afterthought. `session.run()` plus `expect` plus `judge` made the eval bonus cheap to do well.
- **The defaults are voice-native**: the audio turn detector, adaptive interruptions, preemptive generation, and background audio with "thinking" sounds.
- **Tools can behave like a person on the phone**: `with_filler` for slow lookups, `disallow_interruptions` for the moment you're writing a booking.
- `lk agent console` / `lk agent daemon` let me run full calls in text and voice without a phone number.

Friction worth knowing: `python agent.py console` is gone (it's `lk agent console` now), Inference STT has no batch mode, and `session.run()` skips `on_user_turn_completed`. That last one is reasonable, but easy to miss when a guardrail lives there.

I had built a LiveKit agent before this project, so the SDK wasn't new to me. The 1.8 APIs (`AgentServer`, `TurnHandlingOptions`, the audio turn detector, `lk agent`) were.

## What I'd improve with more time

1. **Real telephony.** Outbound through a SIP trunk (Twilio → LiveKit `CreateSIPParticipant`), answering-machine detection so Maya speaks only after a human says hello, a real `WarmTransferTask`, and a recording consent prompt for two-party-consent states.
2. **Pre-render the greeting while the phone rings.** TTS first audio was 1.2–1.6 s for the full greeting in my measurements. Ring time is free time to hide that.
3. **Audio evals**: simulated calls with phone-quality noise, barge-ins and silence, plus p95 latency per turn tracked across config versions.
4. **Real integrations**: calendar, CRM timeline notes and dispositions through idempotent webhooks; number capture with digit-by-digit confirmation or DTMF fallback.
5. **Platform shape**: draft/publish/rollback per agent in a database, tools exposed per tenant via MCP, evals as a gate before publishing a version.
