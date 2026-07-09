<!-- Knowledge layer v2 for the KL arm (Paarthurnax). v1 rewritten after
adversarial review: all "safer against X" claims re-derived from the beats
table, response table completed to five rows with defined fallbacks, energy
policy de-contradicted, deviation rule made evidence-triggered. Sources:
platform docs plus 13 instrumented battles (165 turns) against one Haiku 4.5
agent, Phase A, 2026-07-09. HTML comments are stripped before injection. -->

# BattleClaws Strategy Knowledge (v2)

## 1. Rules that matter

Blind simultaneous 5-option RPS plus an energy bet. Each action beats two,
loses to two:

| Action | Beats | Loses to |
|--------|--------------|--------------|
| STRIKE | BREAK, PARRY | HEAVY, GUARD |
| HEAVY | STRIKE, GUARD | PARRY, BREAK |
| GUARD | PARRY, STRIKE | HEAVY, BREAK |
| PARRY | HEAVY, BREAK | STRIKE, GUARD |
| BREAK | HEAVY, GUARD | STRIKE, PARRY |

- The turn loser deals ZERO damage. Every decided turn is a full swing.
- Read each ability's power_coefficient from your_abilities in the state
  every turn; do not assume values. Typical order: BREAK > HEAVY >
  PARRY = STRIKE > GUARD.
- Docs say mirrors (same action both sides) deal about 35% of a normal hit;
  our logs measured 40 to 45%. Treat it as rough chip, never a bet target.
- Energy regen is symmetric, roughly +13 to +18 per turn to BOTH sides
  regardless of who won. No momentum mechanic exists. Optimize each turn
  independently.
- Legal bets: exactly 0, 25, 50, 75, 100 with multipliers 1.0, 1.4, 1.8,
  2.3, 3.0. Bets are spent win or lose.

## 2. Opponent model

Scope: measured against one Claude Haiku agent, 13 battles, 165 turns.
Treat as priors, not laws.

- It almost never repeats its previous action (0 to 12%). Downweight their
  last action heavily, but never stake a 75+ bet on that alone.
- Its most likely move is a counter to YOUR last action (about half of
  turns). This is the main exploitable pattern.
- Its opening is scripted: turn 1 STRIKE at 0 energy (13 of 13), turn 2
  HEAVY with a 25 bet (12 of 13). Note: those turn-2 numbers were measured
  after mirrored turn-1 openings; once you win turn 1, treat turn 2 as a
  guess, not a 90% read.
- Frequency analysis helps only if one action exceeds 40% of their play
  over 5+ turns. Otherwise use the transition read below.

## 3. The play table (run every turn from turn 3)

Step 1: prune actions the opponent cannot play (cooldown_remaining > 0).
Step 2: look up your OWN last action; play the response:

| Your last action | They most likely play | Your play | Why it works |
|------------------|----------------------|-----------|--------------|
| STRIKE | HEAVY (their modal counter) | BREAK | beats HEAVY, and beats GUARD if they vary |
| HEAVY | PARRY or BREAK | STRIKE | beats BOTH of their likely counters |
| PARRY | BREAK (measured modal) | STRIKE | beats BREAK, mirrors a STRIKE, loses only to rare GUARD |
| BREAK | PARRY or STRIKE | GUARD | beats BOTH of their likely counters |
| GUARD | HEAVY or BREAK | PARRY | beats BOTH of their likely counters |

Fallbacks, in order:
- If the table's predicted action is cooldown-pruned, play the response to
  the other action that beats your last move.
- If you have no usable read (early turns, heavy pruning, or their play
  looks uniform): play STRIKE or GUARD at 0 energy and gather data.

## 4. Opening

- Turn 1: HEAVY at 0 energy. It beats their near-certain STRIKE opener and
  merely mirrors a HEAVY deviator. (GUARD also beats STRIKE but loses
  outright to a HEAVY or BREAK deviator.)
- Turn 2: expect a counter to your HEAVY, which means PARRY or BREAK.
  Play STRIKE: it beats both. Bet 0 or 25.
- Turn 3 onward: use the play table.

## 5. Bet sizing (choose the tier first, then round DOWN to what you can afford)

1. No read or coin flip: 0.
2. Table read (single modal prediction): 25, once your pool is above 40.
3. Strong read, meaning your play beats BOTH of their two most likely
   actions (the STRIKE, GUARD, PARRY rows above, or cooldowns leave them
   two actions you cover): 50.
4. 75 or 100 ONLY when (a) a cooldown prune, not the no-repeat prior,
   leaves them two actions and you beat both, or (b) kill window: their HP
   is under about 2.3 x (your chosen ability's coefficient x your basic
   ~96 damage), roughly 220 to 330 depending on the action.
5. Never end a battle holding a big pool: if either side is within two
   average hits of KO, spend on your best read now.
6. Never bet a tier above your current energy; round down.

## 6. Are you being read?

If you lost 2 of your last 3 turns to actions that beat the move you had
JUST played, the opponent is countering your last action faster than you
are countering theirs. For one turn, play the second-best response to
their predicted move instead of the table row, then return to the table.
When deviating, NEVER simply repeat your own last action: their modal
response is the counter to it.

## 7. Endgame

- Inside their kill window: take the highest-coefficient action that beats
  their two most likely plays and make the hit lethal.
- Inside YOUR kill window (you could be KOd this turn): pick the action
  that beats their highest-damage likely play, even at lower damage.
- Simultaneous KO: higher SPD survives; with equal statlines assume you
  lose the tiebreak and avoid mutual-KO trades.
