"""Configuration loading for the BattleClaws experiment harness.

Config resolution order:
    1. Explicit path argument
    2. BATTLECLAW_CONFIG environment variable
    3. <repo_root>/config.json
    4. <repo_root>/config.example.json (safe defaults, no credentials)

Credentials (API keys) are NOT stored in the config file. Each creature
entry points at an untracked credentials file like .credentials/<handle>.json
which is written by scripts/register.py and read lazily at runtime.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_API_BASE = "https://api.battleclaws.ai/api/v1"
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_AWS_PROFILE = "battleclaws"
DEFAULT_AWS_REGION = "us-east-1"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging to stderr with timestamps. Idempotent."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


@dataclass
class CreatureConfig:
    """Per-creature (per-arm) configuration."""

    handle: str
    credentials_file: Path
    knowledge_file: Optional[Path] = None

    def load_api_key(self) -> Optional[str]:
        """Read the API key from the credentials file, or None if absent."""
        if not self.credentials_file.exists():
            return None
        data = json.loads(self.credentials_file.read_text(encoding="utf-8"))
        return data.get("api_key")

    def load_knowledge_text(self) -> Optional[str]:
        """Read the knowledge file for prompt injection, or None."""
        if self.knowledge_file is None:
            return None
        return self.knowledge_file.read_text(encoding="utf-8")


@dataclass
class ChallengePolicy:
    """How the agent responds to incoming challenges."""

    auto_accept_from: list = field(default_factory=list)
    default: str = "decline"  # one of: accept, decline, ignore


@dataclass
class Config:
    """Top-level experiment configuration."""

    api_base: str = DEFAULT_API_BASE
    aws_profile: str = DEFAULT_AWS_PROFILE
    aws_region: str = DEFAULT_AWS_REGION
    model_id: str = DEFAULT_MODEL_ID
    temperature: float = 1.0
    max_tokens: int = 300
    results_dir: Path = REPO_ROOT / "results"
    budget_usd: float = 150.0
    budget_warn_usd: float = 100.0
    poll_interval_battle_s: float = 4.0
    poll_interval_idle_s: float = 10.0
    inter_battle_delay_s: float = 60.0
    selfplay_battles: int = 100
    statement_template: str = (
        "Automated research agent (knowledge-layer experiment, battle "
        "{battle_number}). Result: {outcome} vs {opponent} in {turns} turns."
    )
    challenge_policy: ChallengePolicy = field(default_factory=ChallengePolicy)
    creatures: dict = field(default_factory=dict)  # handle -> CreatureConfig

    def creature(self, name: str) -> CreatureConfig:
        """Look up a creature config by key, with a helpful error."""
        if name not in self.creatures:
            known = ", ".join(sorted(self.creatures)) or "(none)"
            raise KeyError(f"Unknown creature {name!r}; known: {known}")
        return self.creatures[name]


def _resolve_path(value: Optional[str]) -> Optional[Path]:
    """Resolve a possibly-relative config path against the repo root."""
    if value is None:
        return None
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def load_config(path: Optional[str] = None) -> Config:
    """Load configuration, falling back to config.example.json defaults."""
    candidates = []
    if path:
        candidates.append(Path(path))
    env_path = os.environ.get("BATTLECLAW_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(REPO_ROOT / "config.json")
    candidates.append(REPO_ROOT / "config.example.json")

    chosen = next((c for c in candidates if c.exists()), None)
    if chosen is None:
        raise FileNotFoundError(
            "No config file found; expected one of: "
            + ", ".join(str(c) for c in candidates)
        )
    raw = json.loads(chosen.read_text(encoding="utf-8"))

    creatures = {}
    for key, entry in raw.get("creatures", {}).items():
        creatures[key] = CreatureConfig(
            handle=entry["handle"],
            credentials_file=_resolve_path(entry["credentials_file"]),
            knowledge_file=_resolve_path(entry.get("knowledge_file")),
        )

    policy_raw = raw.get("challenge_policy", {})
    policy = ChallengePolicy(
        auto_accept_from=list(policy_raw.get("auto_accept_from", [])),
        default=policy_raw.get("default", "decline"),
    )

    cfg = Config(
        api_base=raw.get("api_base", DEFAULT_API_BASE),
        aws_profile=raw.get("aws_profile", DEFAULT_AWS_PROFILE),
        aws_region=raw.get("aws_region", DEFAULT_AWS_REGION),
        model_id=raw.get("model_id", DEFAULT_MODEL_ID),
        temperature=float(raw.get("temperature", 1.0)),
        max_tokens=int(raw.get("max_tokens", 300)),
        results_dir=_resolve_path(raw.get("results_dir", "results")),
        budget_usd=float(raw.get("budget_usd", 150.0)),
        budget_warn_usd=float(raw.get("budget_warn_usd", 100.0)),
        poll_interval_battle_s=float(raw.get("poll_interval_battle_s", 4.0)),
        poll_interval_idle_s=float(raw.get("poll_interval_idle_s", 10.0)),
        inter_battle_delay_s=float(raw.get("inter_battle_delay_s", 60.0)),
        selfplay_battles=int(raw.get("selfplay_battles", 100)),
        statement_template=raw.get("statement_template", Config.statement_template),
        challenge_policy=policy,
        creatures=creatures,
    )
    logging.getLogger(__name__).info("Loaded config from %s", chosen)
    return cfg
