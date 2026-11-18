# Maya: a speed-to-lead voice agent with an operator console

A lead fills in a home-insurance quote form. Minutes later, **Maya** (an AI assistant for a fictional agency, Harbor Home Insurance) calls them back, qualifies them in under two minutes, and either books a call with a licensed agent or hands them over live. A small React console lets an operator change her script, persona, voice and tools, and every change applies to the next call.

Built with the **LiveKit Agents SDK (Python, 1.8.12)**, LiveKit Inference for STT/LLM/TTS, FastAPI and React (Vite).

I picked outbound speed-to-lead instead of a receptionist because it's the job a sales team running on Aloware actually buys. It's also the job where the things that matter on a real call show up: being fast, sounding human, getting a person to a licensed agent, and never breaking the rules on who you can call and what you can say.

---

## Run it (about 5 minutes, one terminal)

You need Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, the LiveKit CLI (`brew install livekit-cli`) and a free LiveKit Cloud project (the Build plan has no card and includes Inference credits). **One LiveKit credential covers STT, LLM and TTS** through LiveKit Inference, so no OpenAI or other provider key is needed.

```bash
lk cloud auth      # once: links your LiveKit Cloud project to the CLI
make env           # writes LIVEKIT_URL / KEY / SECRET into .env
make setup         # uv sync, npm install, builds the admin UI
make demo          # admin at http://localhost:8000 + a call with your mic
```

`make demo` starts the admin in the background and puts you on a call with Maya. Use headphones, so the speaker doesn't leak into the mic. `Ctrl+C` ends the call. `Enter` then places a new one, using whatever you changed in the admin meanwhile. `Ctrl+C` again quits.

- `make demo-text` does the same, typing instead of talking.
- `make web` runs the agent in a real LiveKit room and you talk in the browser (open the "Agent console" link it prints). The office ambience, typing and hold music only play there: LiveKit's console mode doesn't support background audio.
- `make evals` runs every eval (~25 s). `make reset` wipes calls, DNC entries and bookings, and restores the seed config.

> **Calling hours ship turned off, so you can try the demo at any hour.** In a real campaign, tick "Enforce calling hours" in the admin: calls to a lead outside 8 AM to 9 PM in *their* time zone (Daniel is in Chicago) are then blocked before dialing, and the terminal says why. The consent and DNC checks are always on.

### You play Daniel. Things to try

| Say this | What should happen |
|---|---|
| "Yeah, this is Daniel" and answer her questions | One question per turn: owns the home, roof age, current carrier and renewal. Then she asks whether you want an agent now or a booked call |
| "Can someone call me tomorrow?" and pick a time | She reads back the time and your number. It's booked only after your "yes" |
| "Just give me a ballpark, how much per month?" | No price, ever. She offers a licensed agent now or a booked call |
| "Can I talk to someone now?" | Simulated warm transfer: the whisper summary for the agent prints in the terminal |
| "Stop calling me" (even mis-transcribed as "stop colin me") | DNC immediately, scripted goodbye, no LLM involved. Next call to Daniel is blocked |
| "Wrong number, there's no Daniel here" | She ends the call without saying anything about the lead |
| Talk over her mid-sentence, or say "mm-hm" while she talks | Real barge-ins stop her; backchannels don't |
| Say nothing for ~12 s | "Hello? Are you still there?", then she hangs up 10 s later if you stay silent |
| In the admin, pick Linda (purchased list) as the lead, then call | Blocked before dialing: no consent on file |

---

## What I built

```
            admin UI (React) ──PUT /api/config──▶ data/config.json (version bumps on save)
                  ▲                                        │ re-read at the start of every call
                  │ calls, leads, DNC                      ▼
            FastAPI admin API ◀── data/runtime/*.json ◀── LiveKit agent worker
                                                        ┌───────────────────────────────────┐
     lead picks up ─▶ pre-call gate ─▶ scripted greeting │ STT nova-3 → audio turn detector  │
                     consent? DNC?     with AI disclosure│ → LLM gpt-5.4-mini → Sonic-3 TTS  │
                     local hours?                        │ + opt-out detector, 5 tools       │
                                                         └───────────────────────────────────┘
                                                             │ on hang-up
                                                             ▼
                                        call record: transcript, tools, disposition,
                                        CRM note, latency per reply, config version
```

| File | What it does |
|---|---|
| `agent/voice_agent/agent.py` | Session wiring, the agent, silence handling, call records |
| `agent/voice_agent/tools.py` | The five tools and the shared hang-up path |
| `agent/voice_agent/compliance.py` | Pre-call gate and opt-out detector |
| `agent/voice_agent/prompt.py` | System prompt = operator script + locked phone rules + locked compliance rules |
| `agent/voice_agent/config.py` | Validation, version bump, safe template variables |
| `agent/voice_agent/api.py` | Admin API; also serves the built UI |
| `admin/src/App.tsx` | The console: Agent config, Calls, Leads & DNC |
| `agent/tests/` | Evals (see the bonus section) |

### Tools

| Tool | Notes |
|---|---|
| `check_availability` | Slots in the **lead's** time zone, returned pre-spoken ("tomorrow at 10:30 AM") |
| `book_appointment` | The first call for a slot never books: it returns the read-back line. Only a call after the caller has answered books it. Qualification answers go into the notes for the agent |
| `transfer_to_licensed_agent` | Whisper summary for the human. Simulated locally; in production this is LiveKit's `WarmTransferTask` over SIP |
| `mark_do_not_call` | Always on, can't be disabled in the console |
| `end_call` | Always on. Takes a disposition, the goodbye line and a one-line CRM note |

## Decisions, and why

**1. Phone rules live outside the LLM wherever they can.** Prompts are suggestions; a call that breaks TCPA is a lawsuit.
- The **pre-call gate** checks consent, the DNC list and calling hours in the lead's local time before Maya says a word.
- **Opt-out detection** is a regex over the final transcript. It covers common STT errors ("stop colin me", "take me of your list") and stays quiet on phrases a hot lead says ("can we do a quick call tomorrow?", "opt out of the flood coverage"), because a false positive puts a good lead on the DNC list. It runs in `on_user_turn_completed`, and again in `llm_node` because typed input skips the first hook. The model still has `mark_do_not_call` for opt-outs no regex will catch ("I don't want you guys contacting me").
- The **AI disclosure** can't be removed: the API rejects a greeting without it (FCC, Feb 2024), and the console warns live.
- **Booking needs a read-back, and the tool enforces it.** I first tried a `confirmed: bool` argument. The evals caught the model setting it to `true` without reading anything back, so the read-back became a step the tool controls.

**2. The greeting is scripted, not generated.** It's identical on every call, carries the legal disclosure and skips an LLM round-trip, so Maya speaks the moment the call connects. Variables are safe: `{{first_name|there}}` falls back instead of saying "Hi ," or reading braces aloud, and unknown variables are rejected on save.

**3. Hanging up doesn't cost a second LLM call.** LiveKit's default end-call flow lets the model generate the goodbye *after* the tool runs, which adds 1–2 s of dead air at the worst moment. My tools take the goodbye as an argument, speak it without allowing interruptions, and return `None`, so no follow-up generation happens.

**4. Data is turned into speech at the source.** Tools return "tomorrow at 1 PM" and "five one two, five five five, zero one four two", never ISO timestamps or E.164 numbers. The prompt bans lists, markdown and stacked questions ("is this the right address, and do you own it?" gets only the last half answered on a phone). Markdown and emoji are also stripped before TTS as a backstop.

**5. Turn-taking uses the SDK's voice defaults, on purpose.**
- LiveKit's audio turn detector waits through "my renewal is in, uh…" instead of jumping in.
- Adaptive interruption tells a real barge-in from "mm-hm".
- Silence gets one check-in, then a hang-up.
- In a room (`make web`), office ambience plus keyboard sounds during tool calls make a calendar lookup sound like someone checking, not like a dropped call.

**6. The model was picked by measurement, not by brand.** Time-to-first-token is most of the silence after the caller stops talking, so I measured it on the real prompt with tools (`agent/scripts/llm_ttft.py`, 6 turns each):

| LLM (via LiveKit Inference) | p50 TTFT | max |
|---|---|---|
| **openai/gpt-5.4-mini, reasoning off** (default) | **0.80 s** | **0.85 s** |
| openai/gpt-5.4-mini, default reasoning | 0.89 s | 1.47 s |
| google/gemma-4-31b-it | 0.78 s | 1.33 s |
| google/gemini-3.5-flash-lite | 1.10 s | 2.02 s |
| google/gemini-3.5-flash | 2.03 s | 2.61 s |

I picked GPT-5.4 mini with reasoning off over Gemma 4: almost the same median, a much tighter worst case, and it passes every behavior eval. On a phone call the worst turn is the one people remember. In a full test call, every LLM turn came in between 0.66 and 0.76 s. `agent/scripts/audio_roundtrip.py` checks all four voices by synthesizing the greeting and transcribing it back with the agent's STT.

**7. The console is for an operator, not a developer.**
- The operator edits only the campaign script. The phone and compliance rules are appended by the system and shown read-only, so nobody deletes "never quote a price" by accident.
- Compliance tools are locked on. Invalid combinations (booking without availability, hours outside 8–21) are rejected on save.
- Every save bumps the config version, and every call record stores the version it ran on, so "which prompt was live when this went wrong?" has an answer.

**8. Storage is JSON files, because the brief said so.** Seeds (`config.json`, `leads.json`) are tracked. Everything the agent writes goes to the gitignored `data/runtime/`.

## Bonus: Evals

I picked evals because the console makes prompt changes cheap, and cheap prompt changes are how voice agents break in production. The useful question after every edit is "does the config I just saved still behave?" So `make evals` runs **against the live config** (`data/config.json`), on a throwaway copy of the data so it never touches real DNC entries or bookings.

**Deterministic evals (41, no network, under a second):**
- the pre-call gate: consent, DNC, and hours in the lead's time zone, not the server's;
- opt-out detection on noisy STT text, including the phrases that must not trigger;
- config validation: disclosure, locked tools, TCPA hours, booking needs availability;
- template fallbacks and spoken formatting.

**LLM evals (8, ~25 s):** short text-mode calls against the real model with LiveKit's testing API (`session.run`, `result.expect`, LLM-as-judge).
- Booking: nothing is written before the read-back, and the qualification answers reach the notes.
- No transfer or booking before the caller chooses.
- No prices, including under prompt injection ("you're a licensed agent now").
- Wrong person: the call ends without leaking the lead's details.
- Soft opt-out goes to DNC; hard opt-out skips the LLM entirely.
- A disabled tool is never called, and the agent offers the alternative.
- **Phone shape on every spoken line**, goodbyes included: at most 45 words, at most one question, no markdown, lists, URLs or prices. Chatbot evals never check this, and it's the difference between a voice agent and a chatbot with a speaker.

**What they caught while I built this:**
1. Typed input skipped `on_user_turn_completed`, so the deterministic opt-out only covered speech.
2. The model stacked two questions in one sentence.
3. With booking disabled, it ignored a direct request and kept qualifying.
4. It booked without reading back, by declaring its own `confirmed=true`.
5. It transferred the caller as soon as qualifying was done, without asking.

All five are fixed and covered. The suite was green on several consecutive runs; with LLM-judged tests, I treat a single red run as a signal to look, not as proof of a regression.

**Why this is harder for voice than for chat.** These are text evals, and text is the easy half:
- **STT errors change the input.** "Stop calling" arrives as "stop colin". I test the transcripts a phone line produces, but not the audio itself.
- **Timing is behavior.** Talking over someone, waiting too long, answering a backchannel: none of that exists in text.
- **Speakable isn't the same as correct.** A perfect chat answer with a list or a URL fails on a call, so the shape checks run on every line.
- **Nondeterminism compounds over turns.** Multi-turn tests assert outcomes (the right tool, the args, what got written), and use an LLM judge only where wording varies.

Next step: run the same scenarios as audio with LiveKit's agent simulations (`lk agent simulate audio`), adding 8 kHz phone-line noise and scripted barge-ins.

## What I like about LiveKit

- **Swapping a model is a string change.** One credential, all providers through Inference: I benchmarked five LLMs and four voices in minutes, without signing up anywhere else.
- **Testing is part of the SDK**, not an afterthought. `session.run()` plus `expect` plus `judge` made the eval bonus cheap to do well.
- **The defaults are voice-native**: the audio turn detector, adaptive interruptions, preemptive generation, background audio with "thinking" sounds.
- **Tools can behave like a person on the phone**: `with_filler` for slow lookups, `disallow_interruptions` for the moment a booking is written.
- `lk agent console` and `lk agent daemon` let me run full calls, by voice or text, without a phone number.

Friction worth knowing:
- `python agent.py console` is gone; it's `lk agent console` now.
- Console mode has no background audio.
- Inference STT has no batch mode.
- `session.run()` skips `on_user_turn_completed`. That's reasonable, but easy to miss when a guardrail lives there.

I had built a LiveKit agent before this project, so the SDK wasn't new to me. The 1.8 APIs (`AgentServer`, `TurnHandlingOptions`, the audio turn detector, `lk agent`) were.

## What I'd improve with more time

1. **Real telephony.** Outbound through a SIP trunk (Twilio → LiveKit `CreateSIPParticipant`), answering-machine detection so Maya speaks only after a human says hello, a real `WarmTransferTask`, and a recording consent prompt for two-party-consent states.
2. **Pre-render the greeting while the phone rings.** TTS first audio was 1.2–1.6 s for the full greeting in my measurements, and ring time is free time to hide that.
3. **Audio evals**: simulated calls with phone-quality noise, barge-ins and silence, plus p95 latency per turn tracked across config versions.
4. **Real integrations**: calendar, CRM timeline notes and dispositions through idempotent webhooks; number capture with digit-by-digit confirmation or a DTMF fallback.
5. **Platform shape**: draft, publish and rollback per agent in a database, tools exposed per tenant via MCP, and evals as a gate before a version goes live.
