"""Audio smoke test: TTS each configured voice, then transcribe it back with the agent's STT.

Run: uv run python scripts/audio_roundtrip.py
Proves the voice IDs, the TTS and the STT models all work through LiveKit Inference,
and measures TTS time-to-first-audio per voice.
"""

import asyncio
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
load_dotenv(ROOT / ".env")

from livekit import rtc  # noqa: E402
from livekit.agents import inference  # noqa: E402
from livekit.agents.stt import SpeechEventType  # noqa: E402
from livekit.agents.utils import http_context  # noqa: E402

from voice_agent import config  # noqa: E402

TEXT = "Hi Daniel, this is Maya, an AI assistant with Harbor Home Insurance. Did I catch you at an okay time?"


async def transcribe(stt, frames) -> str:
    stream = stt.stream(language="en")
    silence = rtc.AudioFrame(b"\x00\x00" * 480, frames[0].sample_rate, 1, 480)

    async def push() -> None:
        for f in frames:
            stream.push_frame(f)
            await asyncio.sleep(f.samples_per_channel / f.sample_rate / 4)
        for _ in range(100):  # trailing silence so the STT finalizes
            stream.push_frame(silence)
            await asyncio.sleep(0.005)
        stream.end_input()

    task = asyncio.create_task(push())
    parts = []
    async for ev in stream:
        if ev.type == SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
            parts.append(ev.alternatives[0].text)
    await task
    return " ".join(parts)


async def main() -> None:
    cfg = config.load()
    stt = inference.STT(model=cfg["models"]["stt"], language="en")
    for key, voice in config.VOICES.items():
        tts = inference.TTS(model=voice["model"], voice=voice["voice_id"], language="en")
        t0 = time.perf_counter()
        first = None
        frames: list[rtc.AudioFrame] = []
        async with tts.synthesize(TEXT) as stream:
            async for ev in stream:
                if first is None:
                    first = time.perf_counter() - t0
                frames.append(ev.frame)
        seconds = sum(f.samples_per_channel for f in frames) / frames[0].sample_rate
        heard = await transcribe(stt, frames)
        print(f"{key:11} ttfb={first:.2f}s audio={seconds:.1f}s heard: {heard!r}")
        await tts.aclose()
    await stt.aclose()


async def run() -> None:
    async with http_context.open():
        await main()


asyncio.run(run())
