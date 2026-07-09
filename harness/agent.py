"""Single-creature agent loop.

State machine: idle -> queue -> in_battle -> post_battle -> idle.
The loop polls /home, follows what_to_do_next semantics, submits moves via
brain.decide, answers challenges per policy, posts a post-battle statement,
and auto-requeues in organic mode. All transitions are logged.

Run standalone (organic arena mode):
    python -m harness.agent --creature paarthurnax --mode organic
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from typing import Optional

from .api import BattleClawsClient, BattleClawsError
from .brain import Brain
from .config import Config, CreatureConfig, load_config, setup_logging
from .telemetry import Telemetry

log = logging.getLogger(__name__)


def _challenge_id(ch: dict) -> Optional[str]:
    """Extract a challenge id from a pending_challenges entry defensively
    (the exact shape is not documented in the skill file)."""
    for key in ("id", "challenge_id"):
        if ch.get(key):
            return str(ch[key])
    return None


def _challenger_handle(ch: dict) -> str:
    """Extract the challenger's handle from a pending challenge entry."""
    for key in ("challenger_handle", "from_handle", "from", "handle"):
        value = ch.get(key)
        if isinstance(value, str):
            return value
    challenger = ch.get("challenger")
    if isinstance(challenger, dict):
        return challenger.get("handle", "")
    return ""


class CreatureAgent:
    """Battle loop for one creature. Thread-safe status for orchestrators."""

    def __init__(self, arm_name: str, creature_cfg: CreatureConfig,
                 config: Config, client: BattleClawsClient, brain: Brain,
                 telemetry: Telemetry, mode: str = "selfplay",
                 stop_event: Optional[threading.Event] = None):
        if mode not in ("selfplay", "organic"):
            raise ValueError(f"mode must be selfplay or organic, got {mode!r}")
        self.arm_name = arm_name
        self.handle = creature_cfg.handle
        self.config = config
        self.client = client
        self.brain = brain
        self.telemetry = telemetry
        self.mode = mode
        self.stop_event = stop_event or threading.Event()
        self.knowledge_text = creature_cfg.load_knowledge_text()

        self.state = "idle"
        self.status: dict = {"state": "idle", "battle_id": None, "elo": None}
        self._battle_acc: dict = {}      # battle_id -> token/usd accumulators
        self._last_submitted: tuple = (None, None)  # (battle_id, turn)
        self._recorded_battles = set(
            bid for bid in telemetry.completed_battle_ids())
        self._battle_counter = self._count_own_battles()

    # ------------------------------------------------------------ helpers

    def _count_own_battles(self) -> int:
        """Count battles this handle already recorded (idempotent resume)."""
        count = 0
        path = self.telemetry.battles_path
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("recorded_by") == self.handle:
                    count += 1
        return count

    def _transition(self, new_state: str, detail: str = "") -> None:
        if new_state != self.state:
            log.info("[%s] state %s -> %s %s", self.handle, self.state,
                     new_state, detail)
            self.state = new_state
            self.status["state"] = new_state

    def _acc(self, battle_id: str) -> dict:
        if battle_id not in self._battle_acc:
            self._battle_acc[battle_id] = {
                "tokens_in": 0, "tokens_out": 0, "usd": 0.0,
                "started_ts": time.time(), "elo_before": self.status.get("elo"),
            }
        return self._battle_acc[battle_id]

    # ---------------------------------------------------------- challenges

    def _handle_challenges(self, home: dict) -> None:
        for ch in home.get("pending_challenges") or []:
            cid = _challenge_id(ch)
            if cid is None:
                log.warning("[%s] pending challenge without id: %r",
                            self.handle, ch)
                continue
            challenger = _challenger_handle(ch)
            policy = self.config.challenge_policy
            if challenger in policy.auto_accept_from:
                decision = "accept"
            else:
                decision = policy.default
            log.info("[%s] challenge %s from %r -> %s", self.handle, cid,
                     challenger, decision)
            if decision == "ignore":
                continue
            try:
                self.client.respond_challenge(cid, accept=(decision == "accept"))
            except BattleClawsError as exc:
                log.warning("[%s] respond_challenge failed: %s", self.handle, exc)

    # -------------------------------------------------------------- battle

    def _play_turn(self, battle: dict) -> None:
        battle_id = battle["battle_id"]
        turn = battle.get("turn_number")
        if self._last_submitted == (battle_id, turn):
            return  # already submitted this turn, waiting for resolution
        decision = self.brain.decide(battle, self.knowledge_text)
        wire_started = time.monotonic()
        try:
            resp = self.client.move(battle_id, decision.ability_id,
                                    decision.energy_spend, decision.reasoning)
        except BattleClawsError as exc:
            if exc.status == 409:
                log.info("[%s] move already submitted for turn %s", self.handle, turn)
                self._last_submitted = (battle_id, turn)
                return
            log.error("[%s] move submission failed: %s", self.handle, exc)
            resp = {"error": str(exc)}
        wire_ms = (time.monotonic() - wire_started) * 1000.0
        self._last_submitted = (battle_id, turn)

        acc = self._acc(battle_id)
        acc["tokens_in"] += decision.input_tokens
        acc["tokens_out"] += decision.output_tokens
        acc["usd"] += decision.usd_estimate
        self.telemetry.record_turn(
            self.handle, battle_id, turn, battle, decision,
            wire_latency_ms=wire_ms,
            extra={"arm": self.arm_name,
                   "move_response_phase": resp.get("phase"),
                   "turn_resolved_inline": resp.get("turn_resolved")})
        log.info("[%s] turn %s: %s energy=%d fallback=%s ($%.5f)",
                 self.handle, turn, decision.ability_id,
                 decision.energy_spend, decision.fallback,
                 decision.usd_estimate)

    # --------------------------------------------------------- post-battle

    def _finish_battle(self, completed: dict, home: dict) -> None:
        battle_id = completed.get("battle_id")
        if not battle_id or battle_id in self._recorded_battles:
            return
        self._transition("post_battle", f"battle {battle_id}")
        summary = {}
        try:
            summary = self.client.battle_summary(battle_id)
        except BattleClawsError as exc:
            log.warning("[%s] battle summary failed: %s", self.handle, exc)

        self._battle_counter += 1
        outcome = completed.get("outcome") or summary.get("outcome")
        opponent = (summary.get("opponent") or {}).get("handle")
        turns = completed.get("turn_count") or summary.get("turn_count")
        statement = self.config.statement_template.format(
            battle_number=self._battle_counter, outcome=outcome,
            opponent=opponent or "unknown", turns=turns or "?",
            handle=self.handle)[:280]
        try:
            self.client.post_statement(statement, battle_id=battle_id)
        except BattleClawsError as exc:
            log.warning("[%s] statement failed: %s", self.handle, exc)

        acc = self._battle_acc.pop(battle_id, {})
        elo_after = ((home.get("creature") or {}).get("ranking") or {}).get("elo")
        self.telemetry.record_battle(
            battle_id=battle_id, recorded_by=self.handle,
            opponent_handle=opponent, outcome=outcome, turns=turns,
            total_tokens_in=acc.get("tokens_in", 0),
            total_tokens_out=acc.get("tokens_out", 0),
            total_usd=acc.get("usd", 0.0),
            started_ts=acc.get("started_ts"), finished_ts=time.time(),
            elo_change=completed.get("elo_change") or summary.get("elo_change"),
            elo_after=elo_after,
            extra={"arm": self.arm_name, "mode": self.mode,
                   "elo_before": acc.get("elo_before"),
                   "battle_number": self._battle_counter})
        self._recorded_battles.add(battle_id)
        log.info("[%s] battle %s complete: %s in %s turns (elo_change=%s)",
                 self.handle, battle_id, outcome, turns,
                 completed.get("elo_change"))

    # ------------------------------------------------------------ leveling

    def _maybe_allocate_points(self, home: dict) -> None:
        """Spend skill points with a fixed balanced spread so all arms stay
        statistically identical.

        Disabled unless config.allocate_skill_points is true: win/loss XP
        asymmetry (500 vs 150) makes the winning arm level first, so any
        allocation policy tied to leveling creates a stat snowball that
        confounds the knowledge-layer comparison. Stats are frozen at
        parity for the experiment; points accrue unspent.
        """
        if not getattr(self.config, "allocate_skill_points", False):
            return
        stats = ((home.get("creature") or {}).get("stats") or {})
        points = stats.get("unspent_skill_points") or 0
        if points <= 0:
            return
        order = ["hp", "attack", "defense", "speed", "wit", "stamina"]
        alloc = {k: points // 6 for k in order}
        for i in range(points % 6):
            alloc[order[i]] += 1
        log.info("[%s] allocating %d skill points: %s", self.handle, points, alloc)
        try:
            self.client.allocate_stats(alloc)
        except BattleClawsError as exc:
            log.warning("[%s] allocate_stats failed: %s", self.handle, exc)

    # ----------------------------------------------------------------- run

    def run(self) -> None:
        """Main loop; returns when stop_event is set."""
        log.info("[%s] agent starting (arm=%s mode=%s knowledge=%s)",
                 self.handle, self.arm_name, self.mode,
                 bool(self.knowledge_text))
        while not self.stop_event.is_set():
            try:
                interval = self._step()
            except BattleClawsError as exc:
                log.error("[%s] API error in loop: %s", self.handle, exc)
                interval = self.config.poll_interval_idle_s
            except Exception:
                log.exception("[%s] unexpected error in loop", self.handle)
                interval = self.config.poll_interval_idle_s
            self.stop_event.wait(interval)
        self._transition("stopped", "(shutdown)")
        log.info("[%s] agent stopped", self.handle)

    def _step(self) -> float:
        """One poll cycle. Returns the next poll interval in seconds."""
        home = self.client.home()
        creature = home.get("creature") or {}
        self.status["elo"] = (creature.get("ranking") or {}).get("elo")
        next_actions = home.get("what_to_do_next") or []
        if next_actions:
            log.debug("[%s] what_to_do_next: %s", self.handle,
                      next_actions[0].get("action"))

        self._handle_challenges(home)

        battle = home.get("active_battle")
        if battle:
            self._transition("in_battle", f"battle {battle.get('battle_id')}")
            self.status["battle_id"] = battle.get("battle_id")
            self._acc(battle["battle_id"])  # ensure started_ts is set
            if battle.get("needs_move"):
                self._play_turn(battle)
            return self.config.poll_interval_battle_s

        self.status["battle_id"] = None
        completed = home.get("last_completed_battle")
        if completed:
            self._finish_battle(completed, home)

        self._maybe_allocate_points(home)

        queue_info = home.get("queue") or {}
        if queue_info.get("in_queue"):
            self._transition("queue")
            return self.config.poll_interval_idle_s

        self._transition("idle")
        if self.mode == "organic":
            log.info("[%s] entering matchmaking queue", self.handle)
            try:
                self.client.queue()
                self._transition("queue")
            except BattleClawsError as exc:
                log.warning("[%s] queue failed: %s", self.handle, exc)
        return self.config.poll_interval_idle_s


def build_agent(arm_name: str, config: Config, telemetry: Telemetry,
                mode: str, stop_event: threading.Event,
                brain: Optional[Brain] = None) -> CreatureAgent:
    """Wire up client + brain + agent for one configured creature."""
    creature_cfg = config.creature(arm_name)
    api_key = creature_cfg.load_api_key()
    if not api_key:
        raise RuntimeError(
            f"No API key for {arm_name!r}; expected credentials at "
            f"{creature_cfg.credentials_file}. Run scripts/register.py first.")
    client = BattleClawsClient(
        config.api_base, api_key=api_key, handle=creature_cfg.handle,
        wire_log_dir=config.results_dir / "wire")
    if brain is None:
        brain = Brain(config.model_id, config.aws_profile, config.aws_region,
                      max_tokens=config.max_tokens,
                      temperature=config.temperature)
    return CreatureAgent(arm_name, creature_cfg, config, client, brain,
                         telemetry, mode=mode, stop_event=stop_event)


def main() -> int:
    """CLI entrypoint for a single-creature organic run."""
    parser = argparse.ArgumentParser(description="Run one creature agent")
    parser.add_argument("--creature", required=True,
                        help="creature key from config (e.g. paarthurnax)")
    parser.add_argument("--mode", choices=["organic", "selfplay"],
                        default="organic")
    parser.add_argument("--config", default=None, help="config file path")
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)
    telemetry = Telemetry(config.results_dir, config.budget_usd,
                          config.budget_warn_usd)
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        log.info("Signal %s received, shutting down gracefully", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    agent = build_agent(args.creature, config, telemetry, args.mode, stop_event)
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
