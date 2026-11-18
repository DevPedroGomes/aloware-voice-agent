"""Measure LLM time-to-first-token on the real agent prompt, per model.

Run: uv run python scripts/llm_ttft.py
Used to pick the default model: on a call, TTFT is most of the silence after the
caller stops talking.
"""

import asyncio
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
load_dotenv(ROOT / ".env")

from livekit.agents import inference, llm  # noqa: E402

from voice_agent import config, store  # noqa: E402
from voice_agent.prompt import build_instructions  # noqa: E402
from voice_agent.tools import ALL_TOOLS  # noqa: E402

CANDIDATES = [
    ("openai/gpt-5.4-mini", None),
    ("openai/gpt-5.4-mini", "none"),
    ("google/gemini-3.5-flash", None),
    ("google/gemini-3.5-flash-lite", None),
    ("google/gemma-4-31b-it", None),
]
TURNS = [
    "Yeah, this is Daniel. I've got a couple minutes.",
    "Yes I own it, roof is maybe ten years old.",
    "How much is this gonna cost me per month?",
]


async def ttft(model: str, reasoning, text: str) -> float:
    cfg = config.load()
    lead = store.get_lead("lead_daniel")
    kwargs = {"reasoning": reasoning} if reasoning else {}
    async with inference.LLM(model=model, **kwargs) as m:
        ctx = llm.ChatContext()
        ctx.add_message(role="system", content=build_instructions(cfg, lead, config.enabled_tools(cfg)))
        ctx.add_message(role="assistant", content="Hi Daniel, this is Maya, an AI assistant with Harbor Home Insurance. Did I catch you at an okay time?")
        ctx.add_message(role="user", content=text)
        t0 = time.perf_counter()
        stream = m.chat(chat_ctx=ctx, tools=list(ALL_TOOLS.values()))
        first = None
        async for chunk in stream:
            if first is None and chunk.delta and (chunk.delta.content or chunk.delta.tool_calls):
                first = time.perf_counter() - t0
        await stream.aclose()
        return first or float("nan")


async def main() -> None:
    for model, reasoning in CANDIDATES:
        times = []
        for _ in range(2):
            for t in TURNS:
                try:
                    times.append(await ttft(model, reasoning, t))
                except Exception as e:  # noqa: BLE001
                    print(f"{model} {reasoning}: error {e}")
                    break
        if times:
            print(f"{model:32} reasoning={str(reasoning):5} p50={statistics.median(times):.2f}s max={max(times):.2f}s n={len(times)}")


asyncio.run(main())
