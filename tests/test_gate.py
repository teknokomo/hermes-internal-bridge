"""Trigger gate (spec §7.2): forwarded/relay never reach the agent; fail policy FR-013."""

import pytest

from helpers import FakeEvent

from hermes_internal_bridge.gate import TriggerGate
from hermes_internal_bridge.ledger import Ledger
from hermes_internal_bridge.db import Database


@pytest.fixture()
def ledger(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    yield Ledger(db)
    db.close()


def test_forwarded_with_mention_is_skipped(ledger):
    """§8.3: user forwards a message with @bot → agent not started."""
    event = FakeEvent("пересланное с @bot mention", forward_origin={"sender": "someone"})
    gate = TriggerGate(ledger)
    verdict = gate.evaluate(event)
    assert verdict.action == "skip"
    assert verdict.reason == "relay_filtered_forwarded"
    assert ledger.count_events("relay_filtered_forwarded") == 1


def test_forwarded_on_raw_message_is_skipped(ledger):
    class Raw:
        forward_from = "user"

    event = FakeEvent("текст с @bot", raw_message=Raw())
    verdict = TriggerGate(ledger).evaluate(event)
    assert verdict.action == "skip"


def test_relay_marker_message_is_skipped(ledger):
    event = FakeEvent("[hib:r=11111111-2222-4333-8444-555555555555;h=1] @bot ответь")
    verdict = TriggerGate(ledger).evaluate(event)
    assert verdict.action == "skip"
    assert verdict.reason == "relay_filtered_relay"


def test_hop_above_limit_is_hop_limit_error(ledger):
    event = FakeEvent("[hib:r=11111111-2222-4333-8444-555555555555;h=2] @bot")
    verdict = TriggerGate(ledger, max_hops=1).evaluate(event)
    assert verdict.action == "skip"
    assert verdict.reason == "relay_hop_limit"
    assert ledger.count_events("relay_hop_limit") == 1


def test_ordinary_mention_is_allowed(ledger):
    event = FakeEvent("просто сообщение с @bot упоминанием")
    verdict = TriggerGate(ledger).evaluate(event)
    assert verdict.action == "allow"
    assert verdict.reason == ""


def test_gate_error_fails_open_for_ordinary_messages(ledger):
    """FR-013: internal gate error → ordinary message passes, gate_degraded recorded."""
    event = FakeEvent("обычное сообщение")
    broken_ledger = BrokenLedger()
    gate = TriggerGate(broken_ledger)
    gate.detect = _explode  # the detection path itself fails
    verdict = gate.evaluate(event)
    assert verdict.action == "allow"
    assert verdict.reason == "gate_degraded"
    assert broken_ledger.events == ["gate_degraded"]


def test_gate_error_fails_closed_for_relay_marked(ledger):
    """FR-013: relay metadata must never reach the agent even when the gate errors."""
    event = FakeEvent("[hib:r=11111111-2222-4333-8444-555555555555;h=1] @bot")
    gate = TriggerGate(BrokenLedger())
    gate.detect = _explode
    verdict = gate.evaluate(event)
    assert verdict.action == "skip"


def test_gate_error_fails_closed_for_forwarded(ledger):
    event = FakeEvent("forwarded @bot", forward_origin={"x": "y"})
    gate = TriggerGate(BrokenLedger())
    gate.detect = _explode
    assert gate.evaluate(event).action == "skip"


def _explode(event):
    raise RuntimeError("detection exploded")


def test_ignore_forwarded_mentions_false_lets_plain_forwarded_through(ledger):
    """FR-009 escape hatch: operator set relay.ignore_forwarded_mentions=false."""
    event = FakeEvent("пересланное с @bot", forward_origin={"sender": "someone"})
    gate = TriggerGate(ledger, ignore_forwarded_mentions=False)
    assert gate.evaluate(event).action == "allow"
    # but a true relay marker is still filtered
    marked = FakeEvent("[hib:r=11111111-2222-4333-8444-555555555555;h=1] @bot")
    assert gate.evaluate(marked).action == "skip"


class BrokenLedger:
    """A ledger whose writes always explode; the gate must survive it."""

    def __init__(self):
        self.events = []

    def event(self, relay_id, event, *, route_id="", details=None):
        self.events.append(event)
        raise RuntimeError("ledger unavailable")
