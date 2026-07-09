# battleclaw

A controlled experiment on [battleclaws.ai](https://battleclaws.ai), an AI-agent battle arena. Two creatures run an identical Python harness and an identical small model (Claude Haiku 4.5 via AWS Bedrock); the only difference between them is a plain-text strategy knowledge file injected into one arm's prompt. The experiment measures whether that curated knowledge layer causally improves battle performance (win rate in head-to-head self-play, plus organic arena results), with full telemetry and honest reporting either way. An optional third arm carries deliberately stale, wrong strategy notes as an adversarial control.

## Setup

Requires Python 3.11+ and an AWS profile with Bedrock access.

```
pip install -r requirements.txt
cp config.example.json config.json   # optional; edit to taste
aws configure --profile battleclaws  # Bedrock credentials, us-east-1
```

Creature API keys are written by `scripts/register.py` into `.credentials/<handle>.json`, which is gitignored. Nothing in this repo registers creatures or writes to the platform without an explicit human-driven run of that script.

## Smoke test

```
python scripts/smoke_test.py
```

Checks public API reachability, a real Bedrock decision call against a synthetic battle state (with and without the knowledge block), telemetry JSONL round-trip, and the client-side rate limiter. Use `--skip-network` for offline runs.

## Running

```
# Head-to-head experiment (challenges alternate between the two arms):
python -m harness.selfplay --arms paarthurnax mirmulnir --battles 100

# Single creature in the organic matchmaking queue:
python -m harness.agent --creature paarthurnax --mode organic
```

Create a file at `results/STOP` to stop self-play gracefully between battles.

## Module map

| Path | Purpose |
|------|---------|
| `harness/config.py` | config loading; per-creature credentials and knowledge paths |
| `harness/api.py` | BattleClaws HTTP client: rate limiting, retries, wire log |
| `harness/brain.py` | Bedrock Converse decision module, JSON validation, fallback |
| `harness/agent.py` | single-creature loop: poll /home, move, statement, requeue |
| `harness/selfplay.py` | head-to-head orchestrator with idempotent resume |
| `harness/telemetry.py` | per-turn and per-battle JSONL, USD budget ledger |
| `scripts/register.py` | registration CLI with `--dry-run` schema validation |
| `scripts/payloads/` | draft registration payloads (placeholders, do not register) |
| `scripts/smoke_test.py` | no-registration end-to-end checks |
| `knowledge/` | knowledge-layer files (KL arm draft and stale-arm control) |
| `docs/battleclaws-skill.md` | authoritative platform API reference |
| `results/` | telemetry output (turns, battles, spend ledger, wire logs) |
