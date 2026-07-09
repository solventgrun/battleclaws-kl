"""JSONL telemetry writers and running cost ledger.

Outputs:
    results/turns/<handle>.jsonl  - one record per decided turn
    results/battles.jsonl         - one record per completed battle
                                    (per recording creature; dedupe on
                                    battle_id + recorded_by downstream)
    results/spend.json            - running USD total across all runs

All writers are thread-safe within one process (shared instance).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def state_snapshot_hash(state: dict) -> str:
    """Stable short hash of a battle-state dict for dedupe/audit."""
    canon = json.dumps(state, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class Telemetry:
    """Thread-safe JSONL telemetry writer with a USD budget ledger."""

    def __init__(self, results_dir: Path, budget_usd: float = 150.0,
                 budget_warn_usd: float = 100.0):
        self.results_dir = Path(results_dir)
        self.turns_dir = self.results_dir / "turns"
        self.battles_path = self.results_dir / "battles.jsonl"
        self.spend_path = self.results_dir / "spend.json"
        self.budget_usd = budget_usd
        self.budget_warn_usd = budget_warn_usd
        self._lock = threading.Lock()
        self._warned = False
        self._total_usd = self._load_spend()

    # ------------------------------------------------------------- spend

    def _load_spend(self) -> float:
        if self.spend_path.exists():
            try:
                data = json.loads(self.spend_path.read_text(encoding="utf-8"))
                return float(data.get("total_usd", 0.0))
            except (ValueError, OSError) as exc:
                log.warning("Could not read spend ledger: %s", exc)
        return 0.0

    def add_spend(self, usd: float) -> float:
        """Add model spend to the running total; warn past thresholds."""
        with self._lock:
            self._total_usd += usd
            self.spend_path.parent.mkdir(parents=True, exist_ok=True)
            self.spend_path.write_text(
                json.dumps({"total_usd": round(self._total_usd, 6),
                            "updated_at": time.time()}),
                encoding="utf-8")
            total = self._total_usd
        if total >= self.budget_usd:
            log.error("BUDGET EXCEEDED: $%.2f >= $%.2f cap", total,
                      self.budget_usd)
        elif total >= self.budget_warn_usd and not self._warned:
            self._warned = True
            log.warning("Budget warning: $%.2f spent (warn threshold $%.2f, "
                        "cap $%.2f)", total, self.budget_warn_usd,
                        self.budget_usd)
        return total

    @property
    def total_usd(self) -> float:
        with self._lock:
            return self._total_usd

    # ------------------------------------------------------------- records

    def record_turn(self, handle: str, battle_id: str, turn: int,
                    state: dict, decision, wire_latency_ms: float = 0.0,
                    extra: Optional[dict] = None) -> None:
        """Write one per-turn record and account its model spend."""
        record = {
            "ts": time.time(),
            "battle_id": battle_id,
            "turn": turn,
            "creature": handle,
            "state_hash": state_snapshot_hash(state),
            "your_hp": state.get("your_hp"),
            "opponent_hp": state.get("opponent_hp"),
            "your_energy": state.get("your_energy"),
            "opponent_energy": state.get("opponent_energy"),
            "prompt_chars": decision.prompt_chars,
            "knowledge_injected": decision.knowledge_injected,
            "model_response_raw": decision.raw_responses,
            "parsed_move": decision.as_move(),
            "fallback": decision.fallback,
            "clamped": decision.clamped,
            "parse_errors": decision.parse_errors,
            "attempts": decision.attempts,
            "tokens_in": decision.input_tokens,
            "tokens_out": decision.output_tokens,
            "usd_estimate": round(decision.usd_estimate, 8),
            "bedrock_latency_ms": round(decision.bedrock_latency_ms, 1),
            "wire_latency_ms": round(wire_latency_ms, 1),
        }
        if extra:
            record.update(extra)
        with self._lock:
            _append_jsonl(self.turns_dir / f"{handle}.jsonl", record)
        self.add_spend(decision.usd_estimate)

    def record_battle(self, battle_id: str, recorded_by: str,
                      opponent_handle: Optional[str], outcome: Optional[str],
                      turns: Optional[int], total_tokens_in: int,
                      total_tokens_out: int, total_usd: float,
                      started_ts: Optional[float], finished_ts: Optional[float],
                      elo_change=None, elo_after=None,
                      extra: Optional[dict] = None) -> None:
        """Write one per-battle record."""
        record = {
            "ts": time.time(),
            "battle_id": battle_id,
            "recorded_by": recorded_by,
            "opponent_handle": opponent_handle,
            "outcome": outcome,
            "turns": turns,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_usd": round(total_usd, 8),
            "started_ts": started_ts,
            "finished_ts": finished_ts,
            "elo_change": elo_change,
            "elo_after": elo_after,
        }
        if extra:
            record.update(extra)
        with self._lock:
            _append_jsonl(self.battles_path, record)

    # -------------------------------------------------------------- reads

    def completed_battle_ids(self, between_handles=None) -> set:
        """Distinct battle_ids in battles.jsonl, optionally filtered to
        battles where both participants are in between_handles. Used for
        idempotent selfplay resume."""
        ids = set()
        if not self.battles_path.exists():
            return ids
        wanted = set(between_handles) if between_handles else None
        with self.battles_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if wanted is not None:
                    if rec.get("recorded_by") not in wanted:
                        continue
                    if rec.get("opponent_handle") not in wanted:
                        continue
                if rec.get("battle_id"):
                    ids.add(rec["battle_id"])
        return ids
