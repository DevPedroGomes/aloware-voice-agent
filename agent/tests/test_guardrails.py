"""Deterministic evals: no LLM, run in about a second, never flaky.

These cover the parts of the call that must not depend on model behavior: who we are
allowed to call, opt-out detection on noisy transcripts, the AI disclosure, and how
templates and data are turned into speech.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from voice_agent import config, store
from voice_agent.compliance import is_opt_out, precall_gate
from voice_agent.speech import spoken_phone, spoken_slot

CHICAGO = ZoneInfo("America/Chicago")


def lead(**over):
    base = store.get_lead("lead_daniel")
    return {**base, **over}


# ---------- pre-call gate ----------

def test_gate_allows_consented_lead_in_hours():
    cfg = config.load()
    noon = datetime(2026, 11, 18, 12, 0, tzinfo=CHICAGO)
    assert precall_gate(lead(), cfg, now=noon).allowed


def test_gate_blocks_lead_without_consent():
    cfg = config.load()
    noon = datetime(2026, 11, 18, 12, 0, tzinfo=CHICAGO)
    result = precall_gate(store.get_lead("lead_purchased_list"), cfg, now=noon)
    assert not result.allowed and result.code == "no_consent"


def test_gate_blocks_number_on_dnc():
    cfg = config.load()
    store.add_to_dnc(lead()["phone"], reason="test", source="test")
    noon = datetime(2026, 11, 18, 12, 0, tzinfo=CHICAGO)
    assert precall_gate(lead(), cfg, now=noon).code == "dnc"


def enforced(cfg):
    cfg["compliance"].update(enforce_calling_hours=True, calling_hours_start=8, calling_hours_end=21)
    return cfg


@pytest.mark.parametrize("hour", [7, 21, 23])
def test_gate_blocks_outside_lead_local_hours(hour):
    cfg = enforced(config.load())
    t = datetime(2026, 11, 18, hour, 30, tzinfo=CHICAGO)
    assert precall_gate(lead(), cfg, now=t).code == "outside_hours"


def test_gate_uses_lead_timezone_not_server_timezone():
    # 9:30 PM in New York is 8:30 PM in Chicago: still allowed for a Chicago lead.
    cfg = enforced(config.load())
    t = datetime(2026, 11, 18, 21, 30, tzinfo=ZoneInfo("America/New_York"))
    assert precall_gate(lead(), cfg, now=t).allowed


# ---------- opt-out detection on real-world STT output ----------

@pytest.mark.parametrize(
    "text",
    [
        "Stop calling me.",
        "stop colin me man",  # STT error for "calling"
        "Please take me off your list",
        "take me of your list",  # STT error for "off"
        "remove my number from your system",
        "Don't call me again.",
        "do not call me anymore",
        "Put me on your do not call list",
        "I want to opt out",
        "lose my number",
        "Yeah never call me again",
        "opt me out of these calls",
    ],
)
def test_opt_out_detected(text):
    assert is_opt_out(text)


@pytest.mark.parametrize(
    "text",
    [
        "Can you call me back later?",
        "Don't call me before noon, I work nights",
        "I already called my agent",
        "We took off the old roof last year",
        "Is this on a do not call list or something?",
        "Stop, let me grab a pen",
        "I'm not interested in flood coverage, just the home",
        "Can we do a quick call tomorrow?",
        "Can I opt out of the flood coverage?",
        "I need to take my name off the old policy first",
        "Remove me from the mortgage, my ex is on it",
        "You can get my info from the form",
    ],
)
def test_opt_out_not_triggered_by_normal_speech(text):
    assert not is_opt_out(text)


# ---------- config: disclosure lock, templates, locked tools ----------

def test_current_config_is_valid():
    assert config.validate(config.load()) == []


def test_greeting_without_ai_disclosure_is_rejected():
    cfg = config.load()
    cfg["greeting"] = "Hi {{first_name}}, this is Maya from Harbor Home Insurance!"
    assert any("AI" in e for e in config.validate(cfg))


def test_compliance_tools_cannot_be_disabled():
    cfg = config.load()
    cfg["tools"]["mark_do_not_call"] = False
    assert any("cannot be disabled" in e for e in config.validate(cfg))


def test_unknown_template_variable_is_rejected():
    cfg = config.load()
    cfg["greeting"] = "Hi {{firstname}}, this is an AI assistant."
    assert any("Unknown variable" in e for e in config.validate(cfg))


def test_calling_hours_cannot_exceed_tcpa_window():
    cfg = config.load()
    cfg["compliance"]["calling_hours_end"] = 22
    assert any("TCPA" in e for e in config.validate(cfg))


def test_greeting_falls_back_when_lead_has_no_name():
    cfg = config.load()
    nameless = store.get_lead("lead_no_name")
    text = config.render_template(cfg["greeting"], config.template_values(cfg, nameless))
    assert "{{" not in text and "Hi there," in text


def test_save_bumps_version():
    cfg = config.load()
    v = cfg["version"]
    cfg["persona"]["name"] = "Ava"
    assert config.save(cfg)["version"] == v + 1
    assert config.load()["persona"]["name"] == "Ava"


def test_booking_requires_availability_lookup():
    cfg = config.load()
    cfg["tools"].update(book_appointment=True, check_availability=False)
    assert any("Booking needs" in e for e in config.validate(cfg))


# ---------- speech formatting ----------

def test_phone_numbers_are_spoken_in_groups():
    assert spoken_phone("+15125550142") == "five one two, five five five, zero one four two"


def test_slots_are_spoken_like_a_person_would():
    now = datetime(2026, 11, 18, 12, 0, tzinfo=CHICAGO)  # a Wednesday
    assert spoken_slot(datetime(2026, 11, 19, 10, 30, tzinfo=CHICAGO), now) == "tomorrow at 10:30 AM"
    assert spoken_slot(datetime(2026, 11, 20, 14, 0, tzinfo=CHICAGO), now) == "Friday at 2 PM"
