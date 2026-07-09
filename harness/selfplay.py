"""Head-to-head self-play orchestrator.

Runs two creature agents (the KL arm and the base arm) in threads and
drives N direct-challenge battles between them, alternating the challenger
each battle. Battles are recorded by the agents themselves via telemetry;
the orchestrator paces the experiment, enforces the inter-battle delay,
and resumes idempotently by counting completed battles between the two
arms in results/battles.jsonl.

Stop conditions: N battles reached, SIGINT/SIGTERM, or a stop file at
<results_dir>/STOP.

Usage:
    python -m harness.selfplay --arms paarthurnax mirmulnir --battles 100
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time

from .agent import CreatureAgent, build_agent
from .api import BattleClawsError
from .config import load_config, setup_logging
from .telemetry import Telemetry

log = logging.getLogger(__name__)

IDLE_WAIT_TIMEOUT_S = 300.0
BATTLE_START_TIMEOUT_S = 240.0
BATTLE_FINISH_TIMEOUT_S = 2400.0


class SelfPlayOrchestrator:
    """Paces N challenge battles between two agent arms."""

    def __init__(self, config, telemetry: Telemetry, arm_names: list,
                 n_battles: int, stop_event: threading.Event):
        self.config = config
        self.telemetry = telemetry
        self.arm_names = arm_names
        self.n_battles = n_battles
        self.stop_event = stop_event
        self.stop_file = config.results_dir / "STOP"
        self.agents: dict = {}
        self.threads: list = []

    # ------------------------------------------------------------- helpers

    def _should_stop(self) -> bool:
        if self.stop_event.is_set():
            return True
        if self.stop_file.exists():
            log.info("Stop file %s found, stopping", self.stop_file)
            self.stop_event.set()
            return True
        return False

    def _handles(self) -> list:
        return [self.agents[a].handle for a in self.arm_names]

    def _done_count(self) -> int:
        return len(self.telemetry.completed_battle_ids(
            between_handles=self._handles()))

    def _wait(self, predicate, timeout_s: float, what: str) -> bool:
        """Poll a predicate until true, timeout, or stop. Returns success."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._should_stop():
                return False
            if predicate():
                return True
            self.stop_event.wait(2.0)
        log.warning("Timed out after %.0fs waiting for %s", timeout_s, what)
        return False

    def _both_idle(self) -> bool:
        return all(self.agents[a].status.get("battle_id") is None
                   and self.agents[a].status.get("state") in ("idle", "post_battle")
                   for a in self.arm_names)

    # ---------------------------------------------------------------- run

    def start_agents(self) -> None:
        for arm in self.arm_names:
            agent = build_agent(arm, self.config, self.telemetry,
                                mode="selfplay", stop_event=self.stop_event)
            self.agents[arm] = agent
            thread = threading.Thread(target=agent.run,
                                      name=f"agent-{agent.handle}",
                                      daemon=True)
            thread.start()
            self.threads.append(thread)
        log.info("Started agents: %s", ", ".join(self._handles()))

    def run(self) -> int:
        self.start_agents()
        done = self._done_count()
        log.info("Self-play plan: %d battles total, %d already done",
                 self.n_battles, done)

        while done < self.n_battles and not self._should_stop():
            challenger_arm = self.arm_names[done % 2]
            target_arm = self.arm_names[(done + 1) % 2]
            challenger: CreatureAgent = self.agents[challenger_arm]
            target: CreatureAgent = self.agents[target_arm]

            if not self._wait(self._both_idle, IDLE_WAIT_TIMEOUT_S,
                              "both agents idle"):
                continue

            log.info("Battle %d/%d: %s challenges %s", done + 1,
                     self.n_battles, challenger.handle, target.handle)
            try:
                resp = challenger.client.challenge(target.handle)
                log.info("Challenge response: %s", resp)
            except BattleClawsError as exc:
                log.error("Challenge failed: %s; retrying after delay", exc)
                self.stop_event.wait(30.0)
                continue

            started = self._wait(
                lambda: challenger.status.get("battle_id") is not None,
                BATTLE_START_TIMEOUT_S, "battle to start")
            if not started:
                continue
            battle_id = challenger.status.get("battle_id")
            log.info("Battle %s underway", battle_id)

            finished = self._wait(
                lambda: battle_id in self.telemetry.completed_battle_ids(
                    between_handles=self._handles()),
                BATTLE_FINISH_TIMEOUT_S, f"battle {battle_id} to finish")
            if not finished:
                continue

            done = self._done_count()
            log.info("Progress: %d/%d battles complete, total spend $%.2f",
                     done, self.n_battles, self.telemetry.total_usd)
            if done < self.n_battles:
                log.info("Inter-battle delay %.0fs",
                         self.config.inter_battle_delay_s)
                self.stop_event.wait(self.config.inter_battle_delay_s)

        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=30.0)
        log.info("Self-play finished: %d/%d battles, total spend $%.2f",
                 done, self.n_battles, self.telemetry.total_usd)
        return 0 if done >= self.n_battles else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the head-to-head self-play experiment")
    parser.add_argument("--arms", nargs=2,
                        default=["paarthurnax", "mirmulnir"],
                        help="two creature keys from config")
    parser.add_argument("--battles", type=int, default=None,
                        help="number of battles (default from config)")
    parser.add_argument("--config", default=None, help="config file path")
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)
    n_battles = args.battles or config.selfplay_battles
    telemetry = Telemetry(config.results_dir, config.budget_usd,
                          config.budget_warn_usd)
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        log.info("Signal %s received, shutting down gracefully", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    orchestrator = SelfPlayOrchestrator(config, telemetry, args.arms,
                                        n_battles, stop_event)
    return orchestrator.run()


if __name__ == "__main__":
    raise SystemExit(main())
