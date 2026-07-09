"""Decision module: turns battle state into a validated move via Bedrock.

The system prompt is IDENTICAL across experiment arms. The only per-arm
difference is the optional <strategy_knowledge> block injected into the
user message before the battle state. HTML comments in the knowledge text
are stripped before injection so internal file labels never reach the model.

Model access uses the AWS Bedrock Converse API through boto3 with a named
profile. Pricing constants live here so cost accounting has one source.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import boto3

log = logging.getLogger(__name__)

# Claude Haiku 4.5 pricing (USD per million tokens), single source of truth.
INPUT_USD_PER_MTOK = 1.00
OUTPUT_USD_PER_MTOK = 5.00

LEGAL_ENERGY = (0, 25, 50, 75, 100)
REASONING_MAX_CHARS = 280

SYSTEM_PROMPT = (
    "You are an AI agent playing BattleClaws, a simultaneous 5-action "
    "rock-paper-scissors battle game. Each turn both creatures blindly pick "
    "one ability (each ability has an action type: strike, heavy, guard, "
    "parry, or break) plus an energy spend, and the moves resolve at the "
    "same instant. Your goal is to win the battle.\n"
    "Reply with STRICT JSON and nothing else, in exactly this shape:\n"
    '{"ability_id": "<id from your ability list, not on cooldown>", '
    '"energy_spend": 0, "reasoning": "<max 280 chars>"}\n'
    "Rules for your reply:\n"
    "- energy_spend must be exactly one of 0, 25, 50, 75, 100 and must not "
    "exceed your current energy.\n"
    "- ability_id must be one of YOUR abilities with cooldown_remaining 0.\n"
    "- No markdown, no code fences, no text outside the JSON object."
)

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences from model output."""
    text = text.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text)
    return text.strip()


def _extract_json_object(text: str) -> Optional[dict]:
    """Parse a JSON object from model output, tolerating surrounding prose."""
    text = strip_code_fences(text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _format_abilities(abilities: list) -> str:
    lines = []
    for a in abilities or []:
        lines.append(
            "  - id={id} name={name} type={action_type} "
            "coeff={power_coefficient} cooldown={cooldown_turns} "
            "cooldown_remaining={cooldown_remaining}".format(
                id=a.get("id"), name=a.get("name"),
                action_type=a.get("action_type"),
                power_coefficient=a.get("power_coefficient", "?"),
                cooldown_turns=a.get("cooldown_turns", 0),
                cooldown_remaining=a.get("cooldown_remaining", 0)))
    return "\n".join(lines) if lines else "  (none listed)"


def _format_history(history: list) -> str:
    lines = []
    for m in history or []:
        lines.append(
            "  turn {turn}: {action} ({ability}) energy={energy} "
            "damage_dealt={dmg}{timeout}".format(
                turn=m.get("turn"), action=m.get("action_type"),
                ability=m.get("ability_name"),
                energy=m.get("energy_spend"),
                dmg=m.get("damage_dealt"),
                timeout=" TIMED_OUT" if m.get("timed_out") else ""))
    return "\n".join(lines) if lines else "  (no moves yet)"


def serialize_state(state: dict) -> str:
    """Compact plain-text serialization of active_battle for the prompt."""
    tiers = state.get("energy_tiers") or []
    tier_text = ", ".join(
        f"{t.get('spend')}->x{t.get('multiplier')}" for t in tiers)
    last = state.get("last_turn_result")
    if last:
        last_text = (
            "turn {t}: you played {ya} (energy {ye}) vs opponent {oa} "
            "(energy {oe}); outcome={out}; you dealt {dd}, took {dt}; "
            "your hp {hp}, opponent hp {ohp}".format(
                t=last.get("turn_number"), ya=last.get("you_action"),
                ye=last.get("you_energy_spend"), oa=last.get("opp_action"),
                oe=last.get("opp_energy_spend"),
                out=last.get("turn_outcome"),
                dd=last.get("you_damage_dealt"),
                dt=last.get("you_damage_taken"),
                hp=last.get("you_hp_after"), ohp=last.get("opp_hp_after")))
    else:
        last_text = "(first turn, no result yet)"

    return (
        "BATTLE STATE\n"
        f"turn_number: {state.get('turn_number')}\n"
        f"your_hp: {state.get('your_hp')}/{state.get('your_max_hp')}\n"
        f"opponent_hp: {state.get('opponent_hp')}/{state.get('opponent_max_hp')}\n"
        f"your_energy: {state.get('your_energy')}\n"
        f"opponent_energy: {state.get('opponent_energy')}\n"
        f"opponent: {state.get('opponent_creature_name')} "
        f"({state.get('opponent_archetype')}/{state.get('opponent_element')}, "
        f"handle {state.get('opponent_handle')})\n"
        f"action_beats: {state.get('triangle_hint')}\n"
        f"energy_tiers (damage multiplier): {tier_text}\n"
        "your_abilities:\n"
        f"{_format_abilities(state.get('your_abilities'))}\n"
        "opponent_abilities:\n"
        f"{_format_abilities(state.get('opponent_abilities'))}\n"
        "your_move_history:\n"
        f"{_format_history(state.get('your_move_history'))}\n"
        "opponent_move_history:\n"
        f"{_format_history(state.get('opponent_move_history'))}\n"
        f"last_turn_result: {last_text}\n"
    )


def build_prompt(state: dict, knowledge_text: Optional[str] = None,
                 error_note: Optional[str] = None) -> list:
    """Build Converse-API messages for one decision.

    If knowledge_text is provided it is injected in a delimited
    <strategy_knowledge> block BEFORE the state. HTML comments in the
    knowledge text are stripped so internal labels are not shown to the
    model. error_note is appended on retry after an invalid reply.
    """
    parts = []
    if knowledge_text:
        cleaned = _HTML_COMMENT_RE.sub("", knowledge_text).strip()
        parts.append(
            "<strategy_knowledge>\n" + cleaned + "\n</strategy_knowledge>")
    parts.append(serialize_state(state))
    parts.append(
        "Choose your move for this turn. Reply with the strict JSON object "
        "only.")
    if error_note:
        parts.append(f"NOTE: your previous reply was invalid ({error_note}). "
                     "Reply again with a single valid JSON object and "
                     "nothing else.")
    return [{"role": "user", "content": [{"text": "\n\n".join(parts)}]}]


def legal_ability_ids(state: dict) -> list:
    """Ability ids usable this turn (present and not on cooldown)."""
    return [a["id"] for a in state.get("your_abilities", [])
            if a.get("cooldown_remaining", 0) == 0]


def fallback_move(state: dict) -> dict:
    """Safe default: basic strike-type ability (or any usable one), 0 energy."""
    usable = [a for a in state.get("your_abilities", [])
              if a.get("cooldown_remaining", 0) == 0]
    strike = next((a for a in usable if a.get("action_type") == "strike"), None)
    chosen = strike or (usable[0] if usable else None)
    return {
        "ability_id": chosen["id"] if chosen else "unknown",
        "energy_spend": 0,
        "reasoning": "fallback move after invalid model output",
    }


def validate_move(parsed: Any, state: dict) -> Optional[str]:
    """Return an error string if the parsed move needs a model retry.

    Only ability problems (unknown id, on cooldown) are retryable. Energy
    bets are never retried: clamp_energy_spend adjusts them instead, which
    is arm-neutral and avoids burning model calls on affordable mistakes.
    """
    if not isinstance(parsed, dict):
        return "reply was not a JSON object"
    ability = parsed.get("ability_id")
    legal = legal_ability_ids(state)
    if ability not in legal:
        return f"ability_id {ability!r} not in legal list {legal}"
    return None


def clamp_energy_spend(requested: Any, current_energy: Any) -> tuple:
    """Clamp a requested energy bet to the largest affordable legal tier.

    Returns (energy_spend, clamped): energy_spend is the largest value in
    LEGAL_ENERGY that is <= min(requested, current_energy). A request that
    is not a real number clamps to 0. clamped is True when the result
    differs from the request.
    """
    if isinstance(requested, bool) or not isinstance(requested, (int, float)):
        return 0, True
    ceiling = requested
    if isinstance(current_energy, (int, float)) \
            and not isinstance(current_energy, bool):
        ceiling = min(ceiling, current_energy)
    spend = max((t for t in LEGAL_ENERGY if t <= ceiling), default=0)
    return spend, spend != requested


@dataclass
class Decision:
    """Result of one decide() call, including telemetry fields."""

    ability_id: str
    energy_spend: int
    reasoning: str
    fallback: bool = False
    clamped: bool = False
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd_estimate: float = 0.0
    bedrock_latency_ms: float = 0.0
    raw_responses: list = field(default_factory=list)
    parse_errors: list = field(default_factory=list)
    knowledge_injected: bool = False
    prompt_chars: int = 0

    def as_move(self) -> dict:
        return {"ability_id": self.ability_id,
                "energy_spend": self.energy_spend,
                "reasoning": self.reasoning}


class Brain:
    """Bedrock-backed decision maker. One instance may serve many arms;
    knowledge_text is passed per call so the arms stay code-identical."""

    def __init__(self, model_id: str, aws_profile: str, aws_region: str,
                 max_tokens: int = 300, temperature: float = 1.0):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        session = boto3.Session(profile_name=aws_profile,
                                region_name=aws_region)
        self._client = session.client("bedrock-runtime")

    def _converse(self, messages: list) -> tuple:
        """One Converse call; returns (text, in_tokens, out_tokens, latency_ms)."""
        started = time.monotonic()
        resp = self._client.converse(
            modelId=self.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            inferenceConfig={
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        )
        wall_ms = (time.monotonic() - started) * 1000.0
        content = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(c.get("text", "") for c in content)
        usage = resp.get("usage", {})
        metrics_ms = resp.get("metrics", {}).get("latencyMs", wall_ms)
        return (text, usage.get("inputTokens", 0),
                usage.get("outputTokens", 0), float(metrics_ms))

    def decide(self, state: dict, knowledge_text: Optional[str] = None) -> Decision:
        """Get a validated move for the given battle state.

        Calls the model, parses and validates the JSON reply. Illegal or
        cooldown abilities retry once with the validation error appended;
        on a second failure it returns the safe fallback move with
        fallback=True. Unaffordable or off-tier energy bets never retry:
        they are clamped to the largest affordable legal tier (arm-neutral,
        applied identically to both arms) with clamped=True recorded.
        """
        decision = Decision(ability_id="", energy_spend=0, reasoning="",
                            knowledge_injected=bool(knowledge_text))
        error_note: Optional[str] = None

        for attempt in range(2):
            messages = build_prompt(state, knowledge_text, error_note)
            decision.prompt_chars = len(messages[0]["content"][0]["text"])
            decision.attempts = attempt + 1
            try:
                text, tok_in, tok_out, latency = self._converse(messages)
            except Exception as exc:  # botocore errors, throttling, etc.
                log.error("Bedrock converse failed (attempt %d): %s",
                          attempt + 1, exc)
                decision.parse_errors.append(f"bedrock_error: {exc}")
                error_note = "model call failed"
                continue
            decision.raw_responses.append(text)
            decision.input_tokens += tok_in
            decision.output_tokens += tok_out
            decision.bedrock_latency_ms += latency
            decision.usd_estimate = (
                decision.input_tokens * INPUT_USD_PER_MTOK
                + decision.output_tokens * OUTPUT_USD_PER_MTOK) / 1e6

            parsed = _extract_json_object(text)
            error = ("could not parse JSON object from reply"
                     if parsed is None else validate_move(parsed, state))
            if error is None:
                decision.ability_id = parsed["ability_id"]
                energy, clamped = clamp_energy_spend(
                    parsed.get("energy_spend"), state.get("your_energy"))
                if clamped:
                    log.info("Clamped energy bet %r to %d (current energy %s)",
                             parsed.get("energy_spend"), energy,
                             state.get("your_energy"))
                decision.clamped = clamped
                decision.energy_spend = energy
                decision.reasoning = str(
                    parsed.get("reasoning", ""))[:REASONING_MAX_CHARS]
                return decision
            log.warning("Invalid model move (attempt %d): %s", attempt + 1, error)
            decision.parse_errors.append(error)
            error_note = error

        fb = fallback_move(state)
        decision.ability_id = fb["ability_id"]
        decision.energy_spend = fb["energy_spend"]
        decision.reasoning = fb["reasoning"]
        decision.fallback = True
        log.warning("Falling back to default move: %s", fb["ability_id"])
        return decision
