# battleclaws-kl

A pre-registered A/B experiment on [battleclaws.ai](https://battleclaws.ai), an AI-agent battle arena. Two creatures run an identical Python harness and an identical small model (Claude Haiku 4.5 via AWS Bedrock); the only difference between them is a plain-text strategy knowledge file injected into one arm's prompt. A third arm carries deliberately wrong strategy notes as an adversarial control.

## Result

**The knowledge arm won 70 of 100 valid head-to-head battles** (Wilson 95% CI 0.604 to 0.781, exact binomial p = 7.85e-05 vs the 0.5 null), with hypothesis, metric, n, analysis, and stopping rule frozen at git tag `prereg-v1` before any confirmatory battle. The stale arm went 44 of 100 (CI 0.347 to 0.538, p = 0.271), directionally below the control as pre-registered. All 200 battles passed the validity screen; total Phase B model spend was $11.77.

Full story, including the Phase A arc where knowledge-as-advice did nothing and knowledge-as-policy won 70-30: **[docs/writeup.md](docs/writeup.md)**. Pre-registration: [docs/prereg.md](docs/prereg.md). This extends [tenancy-bench-v1](https://github.com/solventgrun/tenancy-bench) (+5.3 pp from a knowledge layer on coding tasks) into an adversarial game environment.

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
