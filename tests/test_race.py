"""Unit tests for the turn resolution race guard (harness/agent.py).

Uses a mocked client only; no network, no Bedrock. Run with either:
    python -m pytest tests/test_race.py
    python tests/test_race.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.agent import TurnRaceGuard  # noqa: E402
from harness.api import BattleClawsError  # noqa: E402
from harness.brain import clamp_energy_spend  # noqa: E402

MOVE = {"ability_id": "draconic_inferno", "energy_spend": 50,
        "reasoning": "test"}
WAITING_RESP = {"turn_resolved": False, "phase": "waiting_opponent",
                "your_move_accepted": True}


class FakeClient:
    """Mocked BattleClawsClient covering home/move/battle_summary."""

    def __init__(self, home_responses=None, move_response=None,
                 move_error=None, summary=None, summary_error=None):
        self.home_responses = list(home_responses or [])
        self.move_response = move_response or {}
        self.move_error = move_error
        self.summary = summary or {}
        self.summary_error = summary_error
        self.move_calls = []
        self.summary_calls = []

    def home(self):
        if len(self.home_responses) > 1:
            return self.home_responses.pop(0)
        return self.home_responses[0] if self.home_responses else {}

    def move(self, battle_id, ability_id, energy_spend, reasoning=""):
        self.move_calls.append(
            {"battle_id": battle_id, "ability_id": ability_id,
             "energy_spend": energy_spend, "reasoning": reasoning})
        if self.move_error is not None:
            raise self.move_error
        return self.move_response

    def battle_summary(self, battle_id):
        self.summary_calls.append(battle_id)
        if self.summary_error is not None:
            raise self.summary_error
        return self.summary


def _battle(turn=3, phase="waiting_opponent", you_submitted=True,
            needs_move=False, battle_id="b1"):
    return {"battle_id": battle_id, "turn_number": turn, "phase": phase,
            "you_submitted": you_submitted, "needs_move": needs_move}


def _armed_guard(client=None, timeout_s=20.0):
    guard = TurnRaceGuard(client or FakeClient(), "tester",
                          timeout_s=timeout_s)
    guard.note_submitted("b1", 3, MOVE, WAITING_RESP, now=0.0)
    return guard


# ---------------------------------------------------------------- detection

def test_resolved_inline_response_does_not_arm():
    guard = TurnRaceGuard(FakeClient(), "tester")
    guard.note_submitted("b1", 3, MOVE, {"turn_resolved": True}, now=0.0)
    assert not guard.check(_battle(), now=100.0)


def test_no_race_before_timeout():
    guard = _armed_guard()
    assert not guard.check(_battle(), now=10.0)


def test_race_detected_after_timeout():
    guard = _armed_guard()
    assert guard.check(_battle(), now=25.0)


def test_turn_advance_clears_watch():
    guard = _armed_guard()
    assert not guard.check(_battle(turn=4), now=25.0)
    # Watch cleared: a later stuck-looking snapshot is no longer a race.
    assert not guard.check(_battle(turn=3), now=100.0)


def test_other_battle_clears_watch():
    guard = _armed_guard()
    assert not guard.check(_battle(battle_id="b2"), now=25.0)
    assert not guard.check(_battle(), now=100.0)


def test_unrecorded_move_is_not_the_race():
    guard = _armed_guard()
    stuck = _battle(phase="waiting_both", you_submitted=False,
                    needs_move=False)
    assert not guard.check(stuck, now=25.0)


# ----------------------------------------------------------------- recovery

def test_recovery_resubmits_identical_move():
    client = FakeClient(
        home_responses=[{"active_battle": _battle(needs_move=True)}],
        move_response={"turn_resolved": True})
    guard = _armed_guard(client)
    assert guard.check(_battle(), now=25.0)
    assert guard.recover(now=25.0) == "resubmitted"
    assert client.move_calls == [
        {"battle_id": "b1", "ability_id": "draconic_inferno",
         "energy_spend": 50, "reasoning": "test"}]
    # Inline resolution on resubmit disarms the watch.
    assert not guard.check(_battle(), now=100.0)


def test_recovery_tolerates_409_duplicate():
    client = FakeClient(
        home_responses=[{"active_battle": _battle(needs_move=True)}],
        move_error=BattleClawsError("dup", status=409,
                                    code="already_submitted"))
    guard = _armed_guard(client)
    assert guard.check(_battle(), now=25.0)
    assert guard.recover(now=25.0) == "resubmitted"
    assert len(client.move_calls) == 1


def test_recovery_detects_resolution_via_summary():
    client = FakeClient(home_responses=[{"active_battle": None}],
                        summary={"battle_id": "b1", "outcome": "win"})
    guard = _armed_guard(client)
    assert guard.check(_battle(), now=25.0)
    assert guard.recover(now=25.0) == "battle_resolved"
    assert client.summary_calls == ["b1"]
    assert not client.move_calls


def test_recovery_turn_advanced_between_checks():
    client = FakeClient(home_responses=[{"active_battle": _battle(turn=4)}])
    guard = _armed_guard(client)
    assert guard.check(_battle(), now=25.0)
    assert guard.recover(now=25.0) == "turn_advanced"
    assert not client.move_calls


def test_recovery_probes_summary_when_still_waiting():
    client = FakeClient(home_responses=[{"active_battle": _battle()}],
                        summary_error=BattleClawsError("nf", status=404))
    guard = _armed_guard(client)
    assert guard.check(_battle(), now=25.0)
    assert guard.recover(now=25.0) == "unresolved"
    assert client.summary_calls == ["b1"]
    assert not client.move_calls


def test_recovery_rearms_when_still_stuck():
    client = FakeClient(
        home_responses=[{"active_battle": _battle(needs_move=True)}],
        move_response=dict(WAITING_RESP))
    guard = _armed_guard(client)
    assert guard.check(_battle(), now=25.0)
    assert guard.recover(now=25.0) == "resubmitted"
    # Clock rearmed at recovery: not a race again until timeout re-elapses.
    assert not guard.check(_battle(), now=30.0)
    assert guard.check(_battle(), now=50.0)


# ------------------------------------------------------------ energy clamp

def test_clamp_energy_spend():
    assert clamp_energy_spend(25, 16) == (0, True)    # observed failure
    assert clamp_energy_spend(40, 100) == (25, True)  # illegal tier
    assert clamp_energy_spend(100, 60) == (50, True)
    assert clamp_energy_spend(50, 50) == (50, False)
    assert clamp_energy_spend(0, 0) == (0, False)
    assert clamp_energy_spend(-5, 50) == (0, True)
    assert clamp_energy_spend("lots", 50) == (0, True)
    assert clamp_energy_spend(75, None) == (75, False)


def _run_all() -> int:
    failures = 0
    for name in sorted(k for k in globals() if k.startswith("test_")):
        try:
            globals()[name]()
            print(f"  [PASS] {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  [FAIL] {name}: {exc}")
    print("OK" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
