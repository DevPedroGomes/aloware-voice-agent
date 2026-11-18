"""Tests run against a temp copy of data/, seeded with the CURRENT config.json.

So `pytest` answers "does the config the operator just saved still behave?", and never
writes test DNC entries or bookings into the real runtime files.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parent
sys.path.insert(0, str(AGENT_DIR))
load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")

_tmp = Path(tempfile.mkdtemp(prefix="voice-agent-tests-"))
for name in ("config.json", "leads.json"):
    shutil.copy(REPO_ROOT / "data" / name, _tmp / name)
os.environ["VOICE_AGENT_DATA_DIR"] = str(_tmp)

import pytest  # noqa: E402

from voice_agent import store  # noqa: E402


@pytest.fixture(autouse=True)
def clean_runtime():
    shutil.rmtree(store.RUNTIME_DIR, ignore_errors=True)
    yield
    shutil.rmtree(store.RUNTIME_DIR, ignore_errors=True)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET"):
        return
    skip = pytest.mark.skip(reason="LLM evals need LIVEKIT_API_KEY/SECRET (LiveKit Inference)")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)
