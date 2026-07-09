"""BattleClaws experiment harness.

Modules:
    config     - configuration loading (config.json / config.example.json)
    api        - thin HTTP client for the BattleClaws API
    brain      - Bedrock-backed decision module
    agent      - single-creature battle loop
    selfplay   - head-to-head experiment orchestrator
    telemetry  - JSONL telemetry writers and cost ledger
"""
