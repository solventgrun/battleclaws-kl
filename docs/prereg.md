# Pre-Registration: Knowledge-Layer Effect in the BattleClaws Arena

**Status:** FROZEN 2026-07-09, git tag prereg-v1. Any change after this
commit is a numbered deviation appended to section 10, never an edit.
KL artifact: knowledge/kl_v3.md at this commit's SHA. Stale artifact:
knowledge/kl_stale_v0_draft.md at this commit's SHA.

**Platform:** battleclaws.ai, an agent-vs-agent battle arena. Server
authoritative; our agents are pure API clients.

**Purpose:** a confirmatory test of whether a curated knowledge layer
(KL) causally changes the head-to-head win rate of an LLM game agent,
holding model, harness, prompt scaffold, and creature statistics exactly
constant. This extends tenancy-bench-v1 (github.com/solventgrun/
tenancy-bench), which found +5.3 pp resolution-rate improvement from KL
access for capable models (95% CI +1.9 to +8.7, p = 0.002) and a
directional penalty from adversarially wrong KL content, into an
adversarial game environment built by a third party.

## 1. Design inputs (Phase A disclosure)

All exploration is disclosed here and generated the hypotheses below.
Nothing in this section is confirmatory.

- 13 instrumented self-play battles (165 turns) between the two arms
  with KL v0 (mechanics distilled from platform docs only). Corrected
  score approximately 6-7 after excluding 4 battles decided by a
  turn-resolution race in our own harness (both moves POSTed within
  65 ms; server forfeited one side arbitrarily). The race is fixed and
  the fix is regression-tested before Phase B.
- Telemetry analysis of those battles found: both arms open with a
  scripted STRIKE then HEAVY (13 of 13); neither arm repeats its
  previous action (0 to 12%); both arms play a counter to the
  opponent's last action about half the time; neither arm ever bet
  above tier 50; the KL v0 opponent-modeling advice (frequency counting
  and repeat-after-win) was followed and measurably counterproductive
  against this opponent.
- KL v1 was rewritten from that analysis, then adversarially reviewed
  by a fresh-context red-team agent, which found 5 blocking errors
  (including two inverted beats-table claims and an absorbing loop in
  the decision procedure). KL v2 fixes all findings and is the freeze
  candidate.
- 17 organic matchmaking battles by the third creature (alduin, stale
  arm) confirmed the queue produces opponents in an idle arena within
  about 90 seconds. Record 11W 6L, descriptive only.
- A 10-battle validation batch with KL v2 (after the race fix) scored
  4W 5L in the 9 cleanly attributable battles: parity, not an edge.
  Adherence audit: the opening rule executed 10 of 10, but the response
  table was followed on only about a third of mid-battle turns. KL v3
  adds a binding usage contract (table is policy, four numbered
  exceptions, rule cited in the reasoning field) and is the freeze
  artifact. We disclose plainly: Phase A gave no empirical demonstration
  that the KL beats the control; the confirmatory run is a genuine test,
  not a formality.
- One creature-level fact: all three creatures were registered with the
  same DNA seed and received byte-identical base stats (HP 383, ATK
  294, DEF 242, SPD 284, WIT 319, STA 455, archetype FELINE, element
  STONE). Earned stats are equalized at +1 HP/ATK/DEF/SPD/WIT before
  Phase B and skill-point allocation is disabled in the harness for all
  arms. Evolution and divergence endpoints are never called.

## 2. Arms

| Arm | Creature | Knowledge file | Content |
|-----|----------|----------------|---------|
| KL | paarthurnax | knowledge/kl_v3.md (SHA pinned at freeze) | Curated strategy: verified mechanics, measured opponent priors, binding response table, energy policy |
| Control | mirmulnir | none | Identical prompt scaffold with no knowledge block |
| Stale | alduin | knowledge/kl_stale_v0_draft.md (SHA pinned at freeze) | Deliberately wrong strategy: inverted beats relationships, always-max-spend advice, repeat-after-win folk theory |

Identical across arms: model (Claude Haiku 4.5 via Bedrock inference
profile us.anthropic.claude-haiku-4-5-20251001-v1:0, temperature 1.0,
max_tokens 300), system prompt, state serialization, harness code,
creature base and earned stats, server, AWS region. The ONLY difference
is the presence and content of the delimited strategy_knowledge block in
the user message.

## 3. Hypotheses (directional, frozen before any confirmatory battle)

- H1 (primary): the KL arm beats the control arm in more than half of
  valid head-to-head battles. Prediction: win rate above 0.5. Honest
  prior: Phase A validation (n = 9) was consistent with parity, so this
  is a genuinely uncertain sign-level prediction, not a formality. The
  mechanism, if the effect exists, is the exploitable regularities the
  KL encodes (scripted openings, no-repeat, counter-to-last behavior).
- H2 (stale): the stale arm's win rate against the control is at or
  below 0.5. Bad knowledge should not help, and following it should
  hurt (tenancy-bench adversarial tier analog).
- H3 (mediator, no outcome claim): in a majority of turns from turn 3
  onward, the KL arm's move matches the knowledge file's play table row
  for its own previous action OR one of the file's four numbered
  exceptions applies (cooldown fallback, being-read deviation, kill
  window, frequency skew), scored mechanically from telemetry.
  Demonstrates the treatment was consumed. Reported descriptively.

## 4. Primary metric and analysis

Primary metric: KL arm win rate over valid confirmatory battles vs the
control arm, tested against 0.5 with an exact two-sided binomial test at
alpha 0.05, reported with a Wilson 95% interval. Analysis code:
analysis/compute_stats.py (adapted from tenancy-bench, pure stdlib).

A battle is VALID unless it ends by infrastructure failure, defined
mechanically as: (a) either agent's wire log shows the double
waiting_opponent race signature without recovery, (b) either agent
timed out a move (timed_out flag in server history), (c) the server
records a draw from mutual silence, or (d) the server-side move history
for either arm contains a move absent from that arm's wire log. Rule
(d) is a tampering check: the creature API keys were briefly exposed in
a public commit during Phase A (removed and history rewritten within
minutes; the platform offers no key rotation), so arm integrity is
verified per battle by reconciling our submitted-move logs against the
server's record. Invalid battles are excluded from
the primary test, fully logged, counted in section 10, and replaced by
additional battles up to the attempt cap.

## 5. Sample size and power, stated honestly

n = 100 valid battles, fixed in advance, attempt cap 130.

At n = 100 and alpha 0.05 two-sided, the exact binomial test has 80%
power at a true win rate of about 0.64, and about 50% power at 0.60. If
the true effect is at the bottom of our expected range the study is
underpowered for significance and the Wilson interval plus observed
direction ARE the result; we commit to reporting it that way without
reframing.

H2 runs a further 100 valid battles (stale vs control) under the same
test. H2 is secondary: if arena constraints force a cut, H2 is cut
first and the cut is recorded as a deviation.

## 6. Procedure

- Battles are direct challenges between our own creatures on our own
  schedule (self-play), alternating challenger each battle, 60 second
  minimum inter-battle delay, run continuously from the freeze commit
  until n is reached. Order: all H1 battles, then all H2 battles.
- Both agents run on one server (Hetzner, Ashburn) from one codebase at
  the freeze commit SHA. No code, prompt, config, or knowledge changes
  during the run except as documented deviations.
- ELO is explicitly NOT a metric of this experiment: self-play transfers
  rating between our own creatures by design. We report our creatures'
  ELO trajectories descriptively for transparency, nothing more.
- Etiquette: all three creature descriptions publicly declare the
  experiment and link this repo; each battle posts a statement noting
  the experiment and battle number; n is capped at what the power
  analysis requires; the run is paced to stay far inside documented
  rate limits.

## 7. Secondary and exploratory analyses (no confirmatory claims)

- Turn-level exchange win rate per arm; damage differential per turn.
- Energy policy divergence: bet distributions, banked-pool-at-KO rate.
- H3 adherence rate: fraction of KL-arm turns matching the response
  table, and win rate on adherent vs non-adherent turns.
- Cost: per-battle and per-arm token usage and USD, KL premium ratio.
  Dollars per ELO point is reported only if ELO data is interpretable.
- Organic arena battles, if any occur via incoming challenges during
  the run, are excluded from H1/H2 and reported descriptively.

## 8. Stopping rule

Fixed n. The run stops when 100 valid H1 battles complete (then 100
valid H2 battles), or at the attempt cap, or at a hard budget stop of
$120 cumulative project model spend, whichever comes first. No interim
significance testing; a progress counter is the only thing monitored
mid-run. If the platform becomes unavailable the run pauses and resumes
unchanged; the gap is logged.

## 9. What was NOT frozen (declared limitations)

- The KL was tuned against this specific control opponent during Phase
  A. That is the treatment as designed (curated knowledge includes
  opponent scouting), but it means H1 measures knowledge tuned in
  distribution, not transfer to unseen opponents. Organic-arena
  transfer is future work.
- Self-play against a same-model control means both arms share a base
  policy; measured priors can drift once the KL arm's behavior shifts.
  KL v2 flags its priors as priors; whether Haiku respects that nuance
  is part of what the experiment measures.
- Single model, single platform, single knowledge artifact. No claim
  generalizes beyond this configuration.
- The battle-memory pipeline (live Chronicle distillation between
  battles) was collapsed into the static frozen KL for this study;
  Phase A analysis served as the one distillation pass. A living
  memory layer is future work.

## 10. Deviations log (append-only after freeze)

1. 2026-07-10 01:0x UTC, H1 battle 43: challenges began failing with
   HTTP 403 elo_gap_too_large. The platform requires mutual follows for
   challenges across an ELO gap above 500, and 42 battles of one-sided
   ELO transfer between the arms crossed it. Fix: issued mutual follows
   between paarthurnax and mirmulnir (a platform social action; no code,
   prompt, config, or knowledge change). The orchestrator retry-looped
   during the gap and no battle was lost or altered. Battles 1-42 are
   unaffected. Win tallies were not computed during the intervention,
   in keeping with the no-peeking rule; the existence of a large gap
   (direction unquantified) was unavoidably observed.

## Budget

Phase A actual: approximately $2.5 in model spend. Phase B projected:
about $10 per 100-battle block at measured per-battle cost, both arms
included. Hard stop at $120 cumulative (section 8). Well inside the
$150 project cap.
