#!/usr/bin/env python3
"""Pre-registered confirmatory analysis for BattleClaws Phase B.

Implements docs/prereg.md sections 4-5 and 7:
  - Mechanical validity screen (rules a-d) over every confirmatory battle.
  - H1/H2 primary tests: exact two-sided binomial vs p=0.5, Wilson 95% CI.
  - H3 adherence (descriptive mediator).
  - Secondary descriptive statistics.

Pure Python stdlib only. Deterministic output (sorted keys, ordered
structures) so repeated runs produce byte-identical JSON.
"""

import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, "results_phaseB")
OUT_JSON = os.path.join(BASE, "analysis", "phaseB_results.json")

HANDLES = ["paarthurnax", "mirmulnir", "alduin"]
TREATMENT = {"H1": "paarthurnax", "H2": "alduin"}
CONTROL = "mirmulnir"

ABILITY_TO_ACTION = {
    "feline_slash": "strike",
    "feline_pounce": "heavy",
    "feline_riposte": "parry",
    "feline_frenzy": "break",
    "feline_vanish": "guard",
}

# kl_v3 play table keyed by own previous action -> prescribed next action.
KL_V3_TABLE = {
    "strike": "break",
    "heavy": "strike",
    "parry": "strike",
    "break": "guard",
    "guard": "parry",
}

REASONING_PREFIX_RE = re.compile(r"^(row\b|e[1-4]\b)", re.IGNORECASE)

MOVE_HISTORY_ENTRY_RE = re.compile(
    r'\{"turn": (\d+), "action_type": "(\w+)", "ability_name": "[^"]*", '
    r'"energy_spend": (\d+), "damage_dealt": \d+, "timed_out": (true|false)\}'
)


# ---------------------------------------------------------------------------
# Statistics (pure stdlib)
# ---------------------------------------------------------------------------

def wilson(p_hat, n, z=1.96):
    """Wilson score 95% interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    denom = 1.0 + z * z / n
    centre = p_hat + z * z / (2.0 * n)
    margin = z * math.sqrt(p_hat * (1.0 - p_hat) / n + z * z / (4.0 * n * n))
    lo = (centre - margin) / denom
    hi = (centre + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


def exact_binomial_two_sided(k, n, p=0.5):
    """Exact two-sided binomial test vs p (method: sum of P(X=j) for all j
    with P(X=j) <= P(X=k)).

    For p = 0.5 the point probabilities share the common factor 2^-n, so the
    comparison P(X=j) <= P(X=k) reduces to the exact integer comparison
    C(n,j) <= C(n,k); no floating-point comparison error is possible. The
    final ratio is computed with exact integers (Fraction) and converted to
    float once, which also guards against any float overflow for large n.
    """
    if n == 0:
        return 1.0
    if p != 0.5:
        raise ValueError("only p=0.5 is needed and implemented exactly")
    ck = math.comb(n, k)
    total = sum(math.comb(n, j) for j in range(n + 1) if math.comb(n, j) <= ck)
    pval = Fraction(total, 2 ** n)
    return min(1.0, float(pval))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_all():
    data = {}
    data["battles"] = load_jsonl(os.path.join(RESULTS, "battles.jsonl"))
    data["turns"] = {h: load_jsonl(os.path.join(RESULTS, "turns", h + ".jsonl")) for h in HANDLES}
    data["wire"] = {h: load_jsonl(os.path.join(RESULTS, "wire", h + ".jsonl")) for h in HANDLES}
    return data


def creature_id_map(wire):
    """creature_id -> handle, from each agent's own /home responses."""
    mapping = {}
    for h, recs in wire.items():
        for r in recs:
            if r["method"] == "GET" and r["path"] == "/home":
                body = r["response_body"]
                if not isinstance(body, str):
                    body = json.dumps(body)
                m = re.search(r'"creature": \{"id": "([0-9a-f-]+)", "handle": "(\w+)"', body)
                if m:
                    mapping[m.group(1)] = m.group(2)
                    break
    return mapping


# ---------------------------------------------------------------------------
# Battle indexing
# ---------------------------------------------------------------------------

def index_battles(data):
    """Group battles.jsonl by battle_id; classify into H1/H2 blocks;
    dedupe using the treatment-arm record."""
    by_id = defaultdict(list)
    for r in data["battles"]:
        by_id[r["battle_id"]].append(r)

    battles = {}
    anomalies = []
    for bid in sorted(by_id):
        recs = by_id[bid]
        arms = sorted(r["arm"] for r in recs)
        participants = set(arms)
        for r in recs:
            participants.add(r["opponent_handle"])
        if "paarthurnax" in participants:
            block = "H1"
        elif "alduin" in participants:
            block = "H2"
        else:
            raise AssertionError("battle %s has no treatment arm" % bid)
        treat_recs = [r for r in recs if r["arm"] == TREATMENT[block]]
        assert len(treat_recs) == 1, "battle %s: expected exactly 1 treatment record, got %d" % (
            bid, len(treat_recs))
        if len(recs) != 2:
            anomalies.append(
                "battle %s (block %s, battle_number %d): %d battles.jsonl record(s) "
                "instead of 2 (arms present: %s); treatment record present, used per prereg"
                % (bid, block, treat_recs[0]["battle_number"], len(recs), ",".join(arms)))
        outcomes = {r["arm"]: r["outcome"] for r in recs}
        if len(recs) == 2 and set(outcomes.values()) != {"win", "loss"}:
            anomalies.append("battle %s: inconsistent outcomes %s" % (bid, outcomes))
        battles[bid] = {"block": block, "treatment_record": treat_recs[0], "records": recs}
    return battles, anomalies


def index_turns(data):
    """(battle_id, handle) -> {turn: turn_record}"""
    idx = defaultdict(dict)
    for h in HANDLES:
        for t in data["turns"][h]:
            key = (t["battle_id"], h)
            assert t["turn"] not in idx[key], "duplicate turn telemetry %s %s turn %d" % (
                t["battle_id"], h, t["turn"])
            idx[key][t["turn"]] = t
    return idx


def index_summaries(data):
    """battle_id -> {handle: summary_body} (last successful fetch per handle)."""
    idx = defaultdict(dict)
    for h in HANDLES:
        for r in data["wire"][h]:
            if r["method"] == "GET" and r["path"].endswith("/summary") and r["status"] == 200:
                body = r["response_body"]
                if isinstance(body, str):
                    body = json.loads(body)
                idx[body["battle_id"]][h] = body
    return idx


def extract_server_moves(data, cid_map):
    """Server-credited own-move records, from two sources:

    1. /home state snapshots: the own-move history array visible in the raw
       (byte-truncated) response body, matched per complete entry.
    2. Inline-resolved move POST responses: full turn_result with both
       creatures' moves and the turn number.

    Returns:
      server_moves[(battle_id, handle)][turn] = set of (action_type, energy_spend)
      timed_out_turns: list of (battle_id, handle, turn)
    """
    server_moves = defaultdict(lambda: defaultdict(set))
    timed_out = []

    for h in HANDLES:
        for r in data["wire"][h]:
            if r["method"] == "GET" and r["path"] == "/home":
                body = r["response_body"]
                if not isinstance(body, str):
                    body = json.dumps(body)
                mb = re.search(r'"active_battle": \{"battle_id": "([0-9a-f-]+)"', body)
                if not mb:
                    continue
                bid = mb.group(1)
                iy = body.find('"your_move_history"')
                io = body.find('"opponent_move_history"')
                if iy < 0:
                    continue
                for m in MOVE_HISTORY_ENTRY_RE.finditer(body):
                    pos = m.start()
                    if io >= 0 and io < iy:
                        owner = h if pos > iy else None  # entries before iy belong to opponent
                    else:
                        owner = h if (io < 0 or pos < io) else None
                    if owner is None:
                        continue
                    turn, action, energy, tflag = m.groups()
                    server_moves[(bid, h)][int(turn)].add((action, int(energy)))
                    if tflag == "true":
                        timed_out.append((bid, h, int(turn)))
            elif r["method"] == "POST" and r["path"].endswith("/move") and r["status"] == 200:
                body = r["response_body"]
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except ValueError:
                        continue
                if body.get("turn_resolved") and body.get("turn_result"):
                    tr = body["turn_result"]
                    bid = r["path"].split("/")[2]
                    for side in ("creature_a_move", "creature_b_move"):
                        mv = tr.get(side)
                        if not mv:
                            continue
                        owner = cid_map.get(mv["creature_id"])
                        if owner is None:
                            continue
                        server_moves[(bid, owner)][tr["turn_number"]].add(
                            (mv["action_type"], mv["energy_spend"]))
    return server_moves, timed_out


# ---------------------------------------------------------------------------
# Validity screen (prereg section 4, rules a-d)
# ---------------------------------------------------------------------------

def validity_screen(battles, turn_idx, summaries, server_moves, timed_out_turns):
    """Return validity[bid] = {"valid": bool, "reasons": [..]} plus
    reconciliation coverage stats."""
    timed_out_set = set()
    for bid, h, turn in timed_out_turns:
        timed_out_set.add(bid)

    validity = {}
    recon = {"turns_with_server_record": 0, "turns_total": 0, "mismatches": []}

    for bid in sorted(battles):
        info = battles[bid]
        arms_in_battle = sorted({r["arm"] for r in info["records"]}
                                | {r["opponent_handle"] for r in info["records"]})
        reasons = []

        # Reference turn count from the treatment record.
        n_turns = info["treatment_record"]["turns"]
        max_turn_seen = 0
        for h in arms_in_battle:
            if turn_idx.get((bid, h)):
                max_turn_seen = max(max_turn_seen, max(turn_idx[(bid, h)]))

        # --- (a) race signature without recovery -------------------------
        # Both arms' move POSTs answered with the waiting shape on the same
        # turn AND the battle ended on that turn AND final HP of both sides
        # > 500 (forfeit-shaped). Recovered races are VALID.
        for turn in range(1, max_turn_seen + 1):
            phases = [
                (turn_idx.get((bid, h), {}).get(turn) or {}).get("move_response_phase")
                for h in arms_in_battle
            ]
            if phases.count("waiting_opponent") == 2 and turn == max_turn_seen:
                final_hps = []
                for h, summ in sorted(summaries.get(bid, {}).items()):
                    final_hps.append(summ["you"]["final_hp"])
                    final_hps.append(summ["opponent"]["final_hp"])
                if final_hps and min(final_hps) > 500:
                    reasons.append(
                        "a_race_without_recovery: both arms waiting on final turn %d, "
                        "final HP both > 500" % turn)

        # --- (b) timeout --------------------------------------------------
        if bid in timed_out_set:
            reasons.append("b_timeout: timed_out flag in server move history")
        for h in arms_in_battle:
            fb = [t for t, rec in sorted(turn_idx.get((bid, h), {}).items()) if rec.get("fallback")]
            if fb:
                reasons.append("b_timeout: fallback forced move by %s on turn(s) %s" % (h, fb))

        # --- (c) server draw ----------------------------------------------
        outcomes = {r["arm"]: r["outcome"] for r in info["records"]}
        if "draw" in outcomes.values():
            reasons.append("c_server_draw: battles.jsonl outcome draw")
        for h, summ in sorted(summaries.get(bid, {}).items()):
            if summ.get("outcome") == "draw":
                reasons.append("c_server_draw: summary outcome draw (%s)" % h)
                break

        # --- (d) tampering -------------------------------------------------
        # Reconcile server-credited moves against our own turn telemetry.
        for h in arms_in_battle:
            ours = turn_idx.get((bid, h), {})
            credited = server_moves.get((bid, h), {})
            for turn in sorted(credited):
                recon["turns_with_server_record"] += 1
                srv = credited[turn]
                if len(srv) > 1:
                    recon["mismatches"].append(
                        "%s %s turn %d: conflicting server records %s" % (bid, h, turn, sorted(srv)))
                    reasons.append("d_tampering: conflicting server move records for %s turn %d" % (h, turn))
                    continue
                action, energy = next(iter(srv))
                mine = ours.get(turn)
                if mine is None:
                    reasons.append(
                        "d_tampering: server credited %s a %s move on turn %d absent from telemetry"
                        % (h, action, turn))
                    recon["mismatches"].append("%s %s turn %d: no telemetry record" % (bid, h, turn))
                    continue
                my_action = ABILITY_TO_ACTION.get(mine["parsed_move"]["ability_id"])
                my_energy = mine["parsed_move"]["energy_spend"]
                if my_action != action or my_energy != energy:
                    reasons.append(
                        "d_tampering: %s turn %d server credited %s/%d but telemetry shows %s/%d"
                        % (h, turn, action, energy, my_action, my_energy))
                    recon["mismatches"].append(
                        "%s %s turn %d: server %s/%d vs telemetry %s/%d"
                        % (bid, h, turn, action, energy, my_action, my_energy))
            recon["turns_total"] += len(ours)

            # Turn-count reconciliation: telemetry turns vs battle record vs summary.
            if len(ours) != n_turns:
                reasons.append(
                    "d_tampering: %s telemetry has %d turns but battle record says %d"
                    % (h, len(ours), n_turns))
            summ = summaries.get(bid, {}).get(h)
            if summ is not None:
                if summ["turn_count"] != n_turns:
                    reasons.append(
                        "d_tampering: summary turn_count %d != battle record %d (%s)"
                        % (summ["turn_count"], n_turns, h))
                # Whole-battle action-distribution reconciliation (covers turns
                # whose history entries were lost to snapshot truncation).
                srv_dist = dict(summ["you"]["action_distribution"])
                my_dist = Counter(
                    ABILITY_TO_ACTION.get(rec["parsed_move"]["ability_id"])
                    for rec in ours.values())
                if srv_dist != dict(my_dist):
                    reasons.append(
                        "d_tampering: %s summary action distribution %s != telemetry %s"
                        % (h, json.dumps(srv_dist, sort_keys=True),
                           json.dumps(dict(my_dist), sort_keys=True)))

        validity[bid] = {"valid": not reasons, "reasons": reasons}
    return validity, recon


# ---------------------------------------------------------------------------
# Primary tests
# ---------------------------------------------------------------------------

def primary_test(battles, validity, block):
    bids = sorted(b for b in battles if battles[b]["block"] == block)
    valid_bids = [b for b in bids if validity[b]["valid"]]
    wins = sum(1 for b in valid_bids if battles[b]["treatment_record"]["outcome"] == "win")
    losses = sum(1 for b in valid_bids if battles[b]["treatment_record"]["outcome"] == "loss")
    n = len(valid_bids)
    assert wins + losses == n, "%s: wins(%d)+losses(%d) != n_valid(%d)" % (block, wins, losses, n)
    p_hat = wins / n if n else 0.0
    lo, hi = wilson(p_hat, n)
    return {
        "arm": TREATMENT[block],
        "n_valid": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(p_hat, 6),
        "wilson_95ci": [round(lo, 6), round(hi, 6)],
        "exact_binomial_p": round(exact_binomial_two_sided(wins, n), 10),
    }


# ---------------------------------------------------------------------------
# H3 adherence
# ---------------------------------------------------------------------------

def h3_adherence(battles, validity, turn_idx):
    n = strict = reason_ok = either = 0
    for bid in sorted(battles):
        if battles[bid]["block"] != "H1" or not validity[bid]["valid"]:
            continue
        turns = turn_idx.get((bid, "paarthurnax"), {})
        for t in sorted(turns):
            if t < 3 or (t - 1) not in turns:
                continue
            prev_action = ABILITY_TO_ACTION.get(turns[t - 1]["parsed_move"]["ability_id"])
            cur_action = ABILITY_TO_ACTION.get(turns[t]["parsed_move"]["ability_id"])
            if prev_action is None or cur_action is None:
                continue
            n += 1
            s = cur_action == KL_V3_TABLE[prev_action]
            reasoning = (turns[t]["parsed_move"].get("reasoning") or "").strip()
            rm = bool(REASONING_PREFIX_RE.match(reasoning))
            strict += s
            reason_ok += rm
            either += (s or rm)
    return {
        "n_turns": n,
        "strict_table_match": strict,
        "strict_table_match_rate": round(strict / n, 6) if n else None,
        "reasoning_prefix_match": reason_ok,
        "reasoning_prefix_match_rate": round(reason_ok / n, 6) if n else None,
        "either_match": either,
        "either_match_rate": round(either / n, 6) if n else None,
    }


# ---------------------------------------------------------------------------
# Secondaries
# ---------------------------------------------------------------------------

def secondaries(data, battles, turn_idx):
    out = {}

    # Per-arm battle-level aggregates from each arm's own battle records.
    per_arm = {}
    for arm in HANDLES:
        recs = sorted((r for r in data["battles"] if r["arm"] == arm),
                      key=lambda r: r["battle_number"])
        turns_list = [r["turns"] for r in recs]
        usd_list = [r["total_usd"] for r in recs]
        per_arm[arm] = {
            "battles_recorded": len(recs),
            "mean_turns_per_battle": round(sum(turns_list) / len(recs), 4),
            "mean_usd_per_battle": round(sum(usd_list) / len(recs), 6),
            "total_usd": round(sum(usd_list), 6),
            "total_tokens_in": sum(r["total_tokens_in"] for r in recs),
            "total_tokens_out": sum(r["total_tokens_out"] for r in recs),
            "elo_trajectory": {
                "first_battle": {"battle_number": recs[0]["battle_number"],
                                 "elo_before": recs[0]["elo_before"],
                                 "elo_after": recs[0]["elo_after"]},
                "last_battle": {"battle_number": recs[-1]["battle_number"],
                                "elo_before": recs[-1]["elo_before"],
                                "elo_after": recs[-1]["elo_after"]},
            },
        }
    out["per_arm"] = per_arm

    # Per-block mean turns (turn count is a battle-level property).
    for block in ("H1", "H2"):
        tl = [battles[b]["treatment_record"]["turns"]
              for b in sorted(battles) if battles[b]["block"] == block]
        out["mean_turns_per_battle_%s" % block] = round(sum(tl) / len(tl), 4)

    # Energy bet distribution per arm over all recorded turns.
    energy = {}
    fallbacks = {}
    for arm in HANDLES:
        c = Counter()
        fb = 0
        total = 0
        for t in data["turns"][arm]:
            spend = t["parsed_move"]["energy_spend"]
            c[spend] += 1
            total += 1
            fb += 1 if t.get("fallback") else 0
        energy[arm] = {
            "n_turns": total,
            "shares": {str(k): round(c.get(k, 0) / total, 6) for k in (0, 25, 50, 75, 100)},
            "counts": {str(k): c.get(k, 0) for k in sorted(c)},
        }
        fallbacks[arm] = fb
    out["energy_bet_distribution"] = energy
    out["fallback_move_count"] = fallbacks

    # Race telemetry from the run logs.
    race = {}
    for log in ("phaseB_h1.log", "phaseB_h2.log"):
        path = os.path.join(RESULTS, log)
        detected = 0
        outcomes = Counter()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if "turn resolution race detected" in line:
                    detected += 1
                m = re.search(r"race recovery outcome: (\w+)", line)
                if m:
                    outcomes[m.group(1)] += 1
        race[log] = {"race_detected_lines": detected,
                     "recovery_outcomes": {k: outcomes[k] for k in sorted(outcomes)}}
    out["race_recovery"] = race

    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    data = load_all()
    cid_map = creature_id_map(data["wire"])
    assert sorted(cid_map.values()) == sorted(HANDLES), "creature id map incomplete: %s" % cid_map

    battles, anomalies = index_battles(data)
    turn_idx = index_turns(data)
    summaries = index_summaries(data)
    server_moves, timed_out_turns = extract_server_moves(data, cid_map)

    validity, recon = validity_screen(battles, turn_idx, summaries, server_moves, timed_out_turns)

    # Every battle classified; every excluded battle has a reason.
    for bid in battles:
        assert bid in validity
        if not validity[bid]["valid"]:
            assert validity[bid]["reasons"], "excluded battle %s lacks a reason" % bid

    result = {
        "generated_by": "analysis/compute_stats.py",
        "prereg": "docs/prereg.md (frozen 2026-07-09, tag prereg-v1)",
        "H1": primary_test(battles, validity, "H1"),
        "H2": primary_test(battles, validity, "H2"),
        "validity": {},
        "H3_adherence": h3_adherence(battles, validity, turn_idx),
        "secondaries": secondaries(data, battles, turn_idx),
        "tampering_reconciliation": {
            "turns_with_server_side_record": recon["turns_with_server_record"],
            "telemetry_turns_total": recon["turns_total"],
            "mismatches": sorted(recon["mismatches"]),
        },
        "data_anomalies": sorted(anomalies),
    }

    for block in ("H1", "H2"):
        bids = sorted(b for b in battles if battles[b]["block"] == block)
        excluded = {b: validity[b]["reasons"] for b in bids if not validity[b]["valid"]}
        result["validity"][block] = {
            "total_battles": len(bids),
            "valid": sum(1 for b in bids if validity[b]["valid"]),
            "excluded": len(excluded),
            "excluded_battles": excluded,
        }

    # Per-battle validity table (full, for the appendix).
    result["per_battle_validity"] = {
        bid: {
            "block": battles[bid]["block"],
            "battle_number": battles[bid]["treatment_record"]["battle_number"],
            "treatment_outcome": battles[bid]["treatment_record"]["outcome"],
            "turns": battles[bid]["treatment_record"]["turns"],
            "valid": validity[bid]["valid"],
            "reasons": validity[bid]["reasons"],
        }
        for bid in sorted(battles)
    }

    text = json.dumps(result, indent=2, sort_keys=True)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
