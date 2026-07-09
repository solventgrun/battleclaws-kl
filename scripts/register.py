#!/usr/bin/env python3
"""Registration CLI for BattleClaws creatures.

DO NOT RUN without human sign-off: registration is a one-shot write
(3 registrations/IP/24h, API key shown once, description immutable).

Usage:
    python scripts/register.py scripts/payloads/paarthurnax.json --dry-run
    python scripts/register.py scripts/payloads/paarthurnax.json

--dry-run fetches GET /creatures/params/schema and validates the payload
locally (required fields, enums, hex colors, numeric clamps, handle rules)
without making any write call.

On real registration the returned api_key is saved to
.credentials/<handle>.json (mode 0600 where the OS supports it) and the
creature's stats/archetype/element are printed so duplicate-seed arms can
be compared.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.api import BattleClawsClient, BattleClawsError  # noqa: E402
from harness.config import load_config, setup_logging  # noqa: E402

HANDLE_RE = re.compile(r"^[a-z0-9-]{3,20}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_payload(payload: dict, schema: dict) -> list:
    """Validate a registration payload against the live params schema.

    Returns a list of error strings (empty means valid). Numeric clamp
    violations are reported as warnings prefixed WARN (the API clamps
    rather than rejects them).
    """
    errors = []
    handle = payload.get("handle", "")
    if not HANDLE_RE.match(handle):
        errors.append(f"handle {handle!r} must match {HANDLE_RE.pattern}")
    if not payload.get("creature_name") and not payload.get("name"):
        errors.append("creature_name is required")
    if not payload.get("dna_seed"):
        errors.append("dna_seed is required")
    description = payload.get("description", "")
    if len(description) > 1000:
        errors.append("description exceeds 1000 chars")
    for forbidden in ("archetype", "element", "stats"):
        if forbidden in payload:
            errors.append(f"forbidden field present: {forbidden}")

    params = payload.get("creature_params")
    if not isinstance(params, dict):
        errors.append("creature_params object is required")
        return errors

    for spec in schema.get("required_fields", []):
        field = spec["field"]
        value = params.get(field)
        if value is None:
            errors.append(f"creature_params.{field} is required")
            continue
        if spec["type"] == "enum" and value not in spec["allowed"]:
            errors.append(
                f"creature_params.{field}={value!r} not in {spec['allowed']}")
        elif spec["type"] == "hex_color" and not (
                isinstance(value, str) and HEX_COLOR_RE.match(value)):
            errors.append(
                f"creature_params.{field}={value!r} is not #RRGGBB")

    for field, clamp in schema.get("numeric_clamps", {}).items():
        if field not in params:
            continue
        value = params[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"creature_params.{field}={value!r} is not numeric")
            continue
        if clamp.get("integer") and int(value) != value:
            errors.append(f"WARN creature_params.{field}={value} should be "
                          "an integer (API will clamp)")
        if value < clamp["min"] or value > clamp["max"]:
            errors.append(
                f"WARN creature_params.{field}={value} outside "
                f"[{clamp['min']}, {clamp['max']}] (API will clamp)")
    return errors


def save_credentials(handle: str, agent: dict) -> Path:
    """Write the API key file with restrictive permissions where possible."""
    cred_dir = REPO_ROOT / ".credentials"
    cred_dir.mkdir(exist_ok=True)
    path = cred_dir / f"{handle}.json"
    record = {
        "handle": handle,
        "api_key": agent.get("api_key"),
        "agent_id": agent.get("id"),
    }
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 on POSIX
    except OSError:
        pass
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a creature")
    parser.add_argument("payload", help="path to the registration payload JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate locally against the live schema only")
    parser.add_argument("--config", default=None, help="config file path")
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    handle = payload.get("handle", "unknown")

    client = BattleClawsClient(config.api_base, handle=f"register-{handle}",
                               wire_log_dir=config.results_dir / "wire")
    schema = client.params_schema()
    problems = validate_payload(payload, schema)
    hard_errors = [p for p in problems if not p.startswith("WARN")]
    for p in problems:
        print(("WARNING: " if p.startswith("WARN") else "ERROR:   ") +
              p.removeprefix("WARN "))
    if hard_errors:
        print(f"\nPayload INVALID: {len(hard_errors)} error(s).")
        return 1
    print(f"Payload for {handle!r} passes local validation "
          f"({len(problems)} warning(s)).")
    if args.dry_run:
        print("Dry run: no registration performed.")
        return 0

    if "PLACEHOLDER" in payload.get("description", ""):
        print("REFUSING to register: description still contains PLACEHOLDER. "
              "Finalize the description first.")
        return 1

    try:
        resp = client.register(payload)
    except BattleClawsError as exc:
        print(f"Registration FAILED: {exc}\n{json.dumps(exc.payload, indent=2, default=str)}")
        return 1

    agent = resp.get("agent") or {}
    creature = resp.get("creature") or {}
    if not agent.get("api_key"):
        print("WARNING: no api_key found at response.agent.api_key; "
              "full response follows so the key is not lost:")
        print(json.dumps(resp, indent=2, default=str))
    cred_path = save_credentials(handle, agent)
    print(f"API key saved to {cred_path} (shown once by the API, keep it safe)")

    print("\n=== Creature ===")
    print(f"handle:      {handle}")
    print(f"name:        {creature.get('name')}")
    print(f"archetype:   {creature.get('archetype')}")
    print(f"element:     {creature.get('element')}")
    print(f"stats:       {json.dumps(creature.get('stats'), default=str)}")
    profile = resp.get("profile_url") or creature.get("profile_url")
    print(f"profile_url: {profile}")
    print("\nShow the profile URL to the human.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
