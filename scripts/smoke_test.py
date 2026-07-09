#!/usr/bin/env python3
"""End-to-end smoke test with NO registration and NO authenticated writes.

Checks:
    a) public API reachable (/health, /stats, /leaderboard)
    b) Bedrock decision call works on a synthetic mid-battle state,
       once with the knowledge block and once without
    c) telemetry writes valid JSONL
    d) client-side rate limiter enforces its windows

Usage:
    python scripts/smoke_test.py [--config path] [--skip-network]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.api import BattleClawsClient, BattleClawsError, RateLimiter  # noqa: E402
from harness.brain import Brain, Decision  # noqa: E402
from harness.config import load_config, setup_logging  # noqa: E402
from harness.telemetry import Telemetry  # noqa: E402

# Synthetic mid-battle state modeled on the active_battle example in
# docs/battleclaws-skill.md.
SYNTHETIC_STATE = {
    "battle_id": "smoke-test-battle",
    "opponent_handle": "rival-agent",
    "opponent_creature_name": "Grimjaw",
    "opponent_archetype": "GOLEM",
    "opponent_element": "STONE",
    "your_hp": 850, "opponent_hp": 920,
    "your_max_hp": 1120, "opponent_max_hp": 1000,
    "turn_number": 3, "needs_move": True, "phase": "waiting_both",
    "your_energy": 42, "opponent_energy": 38,
    "your_abilities": [
        {"id": "draconic_breath", "name": "Dragon Breath",
         "action_type": "strike", "power_coefficient": 1.0,
         "cooldown_turns": 0, "cooldown_remaining": 0},
        {"id": "draconic_claw", "name": "Rending Claw",
         "action_type": "heavy", "power_coefficient": 1.3,
         "cooldown_turns": 2, "cooldown_remaining": 0},
        {"id": "draconic_scales", "name": "Scale Shield",
         "action_type": "guard", "power_coefficient": 0.5,
         "cooldown_turns": 0, "cooldown_remaining": 0},
        {"id": "draconic_tailwhip", "name": "Tail Whip",
         "action_type": "parry", "power_coefficient": 1.0,
         "cooldown_turns": 1, "cooldown_remaining": 1},
        {"id": "draconic_inferno", "name": "Inferno",
         "action_type": "break", "power_coefficient": 1.5,
         "cooldown_turns": 2, "cooldown_remaining": 0},
    ],
    "opponent_abilities": [
        {"id": "golem_stonewall", "name": "Stone Wall",
         "action_type": "strike", "cooldown_turns": 0, "cooldown_remaining": 0},
        {"id": "golem_earthquake", "name": "Earthquake",
         "action_type": "heavy", "cooldown_turns": 2, "cooldown_remaining": 2},
        {"id": "golem_fortify", "name": "Fortify",
         "action_type": "guard", "cooldown_turns": 0, "cooldown_remaining": 0},
        {"id": "golem_deflect", "name": "Deflect",
         "action_type": "parry", "cooldown_turns": 1, "cooldown_remaining": 0},
        {"id": "golem_magmacore", "name": "Magma Core",
         "action_type": "break", "cooldown_turns": 2, "cooldown_remaining": 1},
    ],
    "opponent_move_history": [
        {"turn": 1, "action_type": "strike", "ability_name": "Stone Wall",
         "energy_spend": 0, "damage_dealt": 0, "timed_out": False},
        {"turn": 2, "action_type": "heavy", "ability_name": "Earthquake",
         "energy_spend": 25, "damage_dealt": 150, "timed_out": False},
    ],
    "your_move_history": [
        {"turn": 1, "action_type": "heavy", "ability_name": "Rending Claw",
         "energy_spend": 0, "damage_dealt": 98, "timed_out": False},
        {"turn": 2, "action_type": "guard", "ability_name": "Scale Shield",
         "energy_spend": 0, "damage_dealt": 0, "timed_out": False},
    ],
    "energy_tiers": [
        {"spend": 0, "multiplier": 1.0}, {"spend": 25, "multiplier": 1.4},
        {"spend": 50, "multiplier": 1.8}, {"spend": 75, "multiplier": 2.3},
        {"spend": 100, "multiplier": 3.0},
    ],
    "triangle_hint": ("STRIKE beats BREAK,PARRY | HEAVY beats STRIKE,GUARD | "
                      "GUARD beats PARRY,STRIKE | PARRY beats HEAVY,BREAK | "
                      "BREAK beats HEAVY,GUARD"),
    "last_turn_result": {
        "turn_number": 2, "you_won_turn": False, "turn_outcome": "loss",
        "you_action": "guard", "you_energy_spend": 0, "you_damage_dealt": 0,
        "you_damage_taken": 150, "you_hp_after": 850, "opp_action": "heavy",
        "opp_energy_spend": 25, "opp_hp_after": 920,
    },
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def test_public_api(config) -> bool:
    print("\n(a) Public API reachability")
    client = BattleClawsClient(config.api_base, handle="smoke-public",
                               wire_log_dir=config.results_dir / "smoke" / "wire")
    ok = True
    try:
        health = client.health()
        ok &= check("/health", health.get("status") == "ok", json.dumps(health))
    except BattleClawsError as exc:
        ok &= check("/health", False, str(exc))
    for name, fn in (("/stats", client.stats),
                     ("/leaderboard", client.leaderboard)):
        try:
            data = fn()
            ok &= check(name, isinstance(data, (dict, list)),
                        f"{str(data)[:120]}...")
        except BattleClawsError as exc:
            ok &= check(name, False, str(exc))
    return ok


def test_bedrock_decisions(config) -> bool:
    print("\n(b) Bedrock decision calls (Converse API)")
    brain = Brain(config.model_id, config.aws_profile, config.aws_region,
                  max_tokens=config.max_tokens, temperature=config.temperature)
    knowledge = (REPO_ROOT / "knowledge" / "kl_v0_draft.md").read_text(
        encoding="utf-8")
    ok = True
    decisions = {}
    for label, ktext in (("with_knowledge", knowledge),
                         ("without_knowledge", None)):
        decision = brain.decide(SYNTHETIC_STATE, ktext)
        decisions[label] = decision
        print(f"\n  --- {label} ---")
        print(f"  raw response: {decision.raw_responses[-1] if decision.raw_responses else '(none)'}")
        print(f"  parsed move:  {decision.as_move()}")
        print(f"  fallback={decision.fallback} attempts={decision.attempts} "
              f"tokens_in={decision.input_tokens} tokens_out={decision.output_tokens} "
              f"latency={decision.bedrock_latency_ms:.0f}ms "
              f"usd=${decision.usd_estimate:.6f} "
              f"prompt_chars={decision.prompt_chars}")
        legal = {a["id"] for a in SYNTHETIC_STATE["your_abilities"]
                 if a["cooldown_remaining"] == 0}
        ok &= check(f"{label}: valid ability", decision.ability_id in legal,
                    decision.ability_id)
        ok &= check(f"{label}: valid energy",
                    decision.energy_spend in (0, 25, 50, 75, 100)
                    and decision.energy_spend <= SYNTHETIC_STATE["your_energy"],
                    str(decision.energy_spend))
        ok &= check(f"{label}: no fallback", not decision.fallback)
    ok &= check("knowledge changes prompt size",
                decisions["with_knowledge"].prompt_chars
                > decisions["without_knowledge"].prompt_chars,
                f"{decisions['with_knowledge'].prompt_chars} vs "
                f"{decisions['without_knowledge'].prompt_chars} chars")
    return ok


def test_telemetry(config) -> bool:
    print("\n(c) Telemetry JSONL round-trip")
    smoke_dir = config.results_dir / "smoke"
    telemetry = Telemetry(smoke_dir, budget_usd=1.0, budget_warn_usd=0.5)
    decision = Decision(ability_id="draconic_breath", energy_spend=25,
                        reasoning="smoke", attempts=1, input_tokens=100,
                        output_tokens=50, usd_estimate=0.00035,
                        bedrock_latency_ms=500.0,
                        raw_responses=['{"ability_id": "draconic_breath"}'],
                        knowledge_injected=True, prompt_chars=1234)
    telemetry.record_turn("smoketester", "smoke-battle", 3,
                          SYNTHETIC_STATE, decision, wire_latency_ms=42.0)
    telemetry.record_battle("smoke-battle", "smoketester", "rival-agent",
                            "win", 17, 1000, 500, 0.0035,
                            time.time() - 60, time.time(), elo_change=25)
    ok = True
    for path in (smoke_dir / "turns" / "smoketester.jsonl",
                 smoke_dir / "battles.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            records = [json.loads(line) for line in lines]
            ok &= check(f"{path.name} valid JSONL", len(records) >= 1,
                        f"{len(records)} record(s)")
        except (OSError, ValueError) as exc:
            ok &= check(f"{path.name} valid JSONL", False, str(exc))
    ok &= check("spend ledger updated", telemetry.total_usd > 0,
                f"${telemetry.total_usd:.6f}")
    resumed = telemetry.completed_battle_ids()
    ok &= check("battle id resume scan", "smoke-battle" in resumed)
    return ok


def test_rate_limiter() -> bool:
    print("\n(d) Rate limiter windows")
    limiter = RateLimiter(global_per_min=3, writes_per_min=2, window_s=1.0)
    started = time.monotonic()
    for _ in range(4):
        limiter.acquire(write=False)
    global_elapsed = time.monotonic() - started
    ok = check("global window blocks 4th request", global_elapsed >= 0.9,
               f"{global_elapsed:.2f}s for 4 acquires at 3/1.0s")

    limiter = RateLimiter(global_per_min=100, writes_per_min=2, window_s=1.0)
    started = time.monotonic()
    for _ in range(3):
        limiter.acquire(write=True)
    write_elapsed = time.monotonic() - started
    ok &= check("write window blocks 3rd write", write_elapsed >= 0.9,
                f"{write_elapsed:.2f}s for 3 writes at 2/1.0s")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Harness smoke test")
    parser.add_argument("--config", default=None)
    parser.add_argument("--skip-network", action="store_true",
                        help="skip the public API and Bedrock checks")
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)
    print(f"Smoke test using api_base={config.api_base} "
          f"model={config.model_id} profile={config.aws_profile}")

    results = {}
    if not args.skip_network:
        results["public_api"] = test_public_api(config)
        results["bedrock"] = test_bedrock_decisions(config)
    results["telemetry"] = test_telemetry(config)
    results["rate_limiter"] = test_rate_limiter()

    print("\n=== Summary ===")
    all_ok = True
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
        all_ok &= ok
    print("SMOKE TEST " + ("PASSED" if all_ok else "FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
