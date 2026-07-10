# A curated knowledge layer turned a coin flip into 70-30

**A pre-registered A/B experiment in the BattleClaws arena. Same model,
same harness, same creature stats. The only variable: a 1,900-token
strategy file. n = 100 battles per contrast, all telemetry public.**

## Summary

Two Claude Haiku 4.5 agents fought 100 head-to-head battles on
battleclaws.ai, an agent-vs-agent battle arena. The agents were
identical in every controllable respect: model, prompt scaffold,
harness code, and creature statistics (both creatures were registered
with the same DNA seed, which the platform hashes to base stats, so
their statlines are byte-identical; skill allocation was frozen). One
agent's prompt included a curated knowledge layer: verified game
mechanics, measured opponent behavioral priors, and a compact decision
policy. The other's did not.

The knowledge arm won 70 of 100 valid battles: win rate 0.700, Wilson
95% CI 0.604 to 0.781, exact two-sided binomial p = 7.85e-05 against
the 0.5 null. The hypothesis, metric, n, analysis, and stopping rule
were frozen and published (git tag `prereg-v1`) before any confirmatory
battle. A third arm carrying deliberately wrong strategy notes went
44 of 100 (CI 0.347 to 0.538, p = 0.271): directionally below the
control, as pre-registered, though not individually significant.

This extends tenancy-bench-v1, which found the same asymmetry on
coding tasks (+5.3 pp from a knowledge layer for capable models,
p = 0.002; a directional penalty from adversarially wrong entries), in
a different task family and someone else's instrument.

## The instrument

BattleClaws battles are blind simultaneous 5-action rock-paper-scissors
with an energy bet: each action beats two others, the turn loser deals
zero damage, and betting 0/25/50/75/100 energy multiplies damage 1.0x
to 3.0x, spent win or lose. The server is authoritative; agents are
pure API clients, so the entire decision loop, including what knowledge
a creature fights with, belongs to the entrant. That makes the arena an
unusually clean instrument for exactly this question: it randomizes
nothing about the agent and everything about the exchange. Full
observability of the opponent's move history and energy, with only the
simultaneous choice hidden, turns every turn into a prediction problem,
and prediction problems are where knowledge should matter if it matters
at all.

## What the knowledge layer is

Three kinds of content, about 1,900 tokens, plain markdown
(`knowledge/kl_v3.md`, SHA-pinned at the freeze):

1. Verified mechanics: the beats table, measured power coefficients,
   the energy multiplier ladder, symmetric regen (there is no momentum
   mechanic; each turn is independent).
2. Measured opponent priors, scoped honestly to their source (one Haiku
   4.5 opponent, 22 instrumented battles): LLM agents almost never
   repeat their previous action; their most likely move is a counter to
   YOUR last action; their openings are scripted.
3. A binding decision policy: a five-row lookup table (your last action
   determines your play), four numbered exceptions, and bet sizing
   rules. The file instructs the agent to cite the rule it used.

Item 3 is the part we did not know we needed, and it is the most
interesting finding in the study after the headline number.

## Knowledge as advice did nothing. Knowledge as policy won 70-30.

Phase A, disclosed in full in the pre-registration, tells the story.
The first knowledge layer (v0), distilled only from the platform docs,
LOST its exploratory batch 5-8: telemetry showed the agent faithfully
applying its opponent-modeling advice, which was wrong for LLM
opponents (the folk theory that winners repeat their winning move
measured at 0 to 12% in practice). Followed-and-wrong, the same failure
mode as tenancy-bench's adversarial tier. The rewrite (v1) was then
adversarially reviewed by a fresh-context red-team agent, which found
five blocking errors, including two claims that inverted the game's own
beats table. The corrected v2 validated at parity (4W 5L), with an
adherence audit showing the agent followed the opening rule 10 of 10
times but the mid-battle decision table on only a third of turns. v3
changed one thing: a binding usage contract making the table the
default policy with numbered exceptions. Adherence rose to 71% of turns
in the confirmatory run, and the win rate went to 0.70.

We pre-registered the honest prior ("Phase A was consistent with
parity; this is a genuine test"), so we get to say this plainly: the
effect appeared when the knowledge stopped being reference material and
started being an executable policy with permission to override the
model's instincts. "Does a knowledge layer help?" was the wrong
question on tenancy-bench, and it is the wrong question here. The right
question is whether the agent is bound to consume it.

## The stale arm

The third creature carried confidently wrong notes: an inverted beats
table, always-max-spend energy advice, and the disproven
repeat-after-win theory. Pre-registered prediction: win rate at or
below 0.5 against the same control. Observed: 0.44 (p = 0.271). The
interval includes 0.5, so this is directional evidence only, and we
flagged in advance that the stale contrast was the first thing to cut
for power. But the sign pattern across both contrasts (curated +0.20,
stale -0.06) matches the pre-registered predictions and the
tenancy-bench pattern: knowledge quality has an asymmetric floor. An
unverified knowledge tool is a liability, not an asset.

## Validity, disclosed incidents, and costs

All 200 confirmatory battles passed the pre-registered validity screen:
zero timeouts, zero fallback moves, zero draws, and a tampering
reconciliation that matched all 5,416 telemetry turns against the
server's record with zero mismatches. The tampering check exists
because we briefly leaked our own creature API keys in a public commit
during Phase A (removed within minutes; the platform has no key
rotation, so we verified integrity per battle instead of assuming it).
A harness race condition that had arbitrarily decided 31% of Phase A
battles was fixed and regression-tested before the freeze; the guard
recovered 77 race events during Phase B with zero battles lost. Two
protocol deviations are logged in the pre-registration, both the same
class: establishing mutual follows so the platform's anti-farming rule
(an honorable feature, working as intended) would permit our scheduled
self-play challenges.

Because both arms are ours, ELO was pre-registered as NOT a metric;
self-play transfers rating between siblings by design. Win rate against
the matched control carries the entire claim. All three creatures
publicly declare the experiment in their profiles.

Costs, to the dollar: Phase B model spend $11.77 for 200 battles, both
arms included ($0.043 per battle for the knowledge arm, $0.022 for the
control; the knowledge premium is the price of 1,900 extra input tokens
per turn). Whole project including all Phase A exploration: about $15
of a $150 budget, on one $4.35/month VPS.

## Limitations

- The opponent is the same base model as the treatment arm, and the
  knowledge was tuned against that opponent during disclosed
  exploration. This measures curated knowledge in distribution, not
  transfer. The organic arena, with unknown opponents, is the natural
  follow-up and this harness already speaks it.
- One model, one platform, one knowledge artifact, one author. No claim
  generalizes beyond this configuration.
- The stale contrast is underpowered at n = 100 for effects smaller
  than about 14 pp; 0.44 is a direction, not a demonstration.
- The advice-to-policy comparison (v2 parity vs v3 at 0.70) is a Phase
  A observation plus a confirmatory run, not a pre-registered factorial
  contrast between binding and non-binding phrasings. It is the obvious
  next pre-registration.
- Battles between identical-model agents produce heavy mirror play;
  effects may differ against diverse opponents.

## Reproducibility

Everything is in this repository: harness, knowledge files (including
the wrong ones), pre-registration with append-only deviations, per-turn
telemetry, wire logs (credentials redacted), analysis code (pure
stdlib), and results JSON. The pre-registration is frozen at git tag
`prereg-v1`; the analysis reruns deterministically from the raw data.
