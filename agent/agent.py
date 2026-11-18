"""Entry point: `lk agent console agent.py` (talk with your mic) or `lk agent dev agent.py`."""

import logging
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import AgentServer, JobContext, cli

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

from voice_agent.agent import run_call  # noqa: E402

logging.getLogger("voice-agent").setLevel(logging.INFO)

server = AgentServer()


@server.rtc_session(agent_name="speed-to-lead")
async def entrypoint(ctx: JobContext) -> None:
    await run_call(ctx)


if __name__ == "__main__":
    cli.run_app(server)
