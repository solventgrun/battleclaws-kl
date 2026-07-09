<!-- Knowledge layer v1 for the KL arm (Paarthurnax). Sources: platform docs
plus 13 instrumented battles (165 turns) against an identical Haiku 4.5 agent,
Phase A exploratory data, 2026-07-09. HTML comments are stripped before
injection. -->

# BattleClaws Strategy Knowledge (v1)

## 1. Action table (verified in play)

Blind simultaneous 5-option RPS. Each action beats two, loses to two.
Power coefficients were measured from real damage logs.

| Action | Beats | Loses to | Measured coefficient |
|--------|--------------|--------------|----------------------|
| STRIKE | BREAK, PARRY | HEAVY, GUARD | 1.00 (baseline ~96 dmg) |
| HEAVY | STRIKE, GUARD | PARRY, BREAK | 1.31 |
| GUARD | PARRY, STRIKE | HEAVY, BREAK | 0.47 |
| PARRY | HEAVY, BREAK | STRIKE, GUARD | 1.10 |
| BREAK | HEAVY, GUARD | STRIKE, PARRY | 1.48 |

Facts that change decisions:
- The turn loser deals ZERO damage. Every decided turn is a full swing.
- Mirrors (same action) chip for about 45% of a normal win, and your energy
  multiplier APPLIES to mirror chip. A 25-spend mirror outdamages a 0-spend
  mirror 60 vs 42 on average.
- Energy regen is symmetric: about +18 to BOTH sides on decided turns, +13
  on mirrors. There is no momentum or snowball mechanic to protect. Optimize
  each turn independently: win probability times damage, minus energy cost.
- GUARD hits weakly but beats STRIKE and PARRY, which together are about
  half of a typical LLM opponent's mix. It is the best cheap probe turn 1
  and a good 0-cost safe play when you have no read.

## 2. Energy (verified multipliers)

Legal spends: exactly 0, 25, 50, 75, 100. Multipliers 1.0 / 1.4 / 1.8 /
2.3 / 3.0. The 75 and 100 tiers pay the most per point. Pools start at 15,
cap 100, regen roughly +13 to +18 per turn.

Policy:
- NEVER request a tier above your current pool. Pick the largest legal tier
  at or below your energy.
- Bank early. Bet 0 while reads are weak; let the pool climb toward 75+.
  Typical agents idle at 25 to 40 and never use the top tiers; a single
  banked 50 to 100 bet on a good read routinely lands 200 to 300 damage and
  decides the battle.
- Unload 75 or 100 only when (a) cooldowns plus the no-repeat rule reduce
  the opponent to about two live actions and you counter both, or (b) the
  opponent is inside a kill window (their HP under roughly 2.3x your basic
  hit, about 250 to 330).
- Bet 25 when you predict a mirror. Bet 0 when you are coin flipping.
- Ending the battle with a full pool is pure waste. If either side is near
  lethal, spend.

## 3. Who you are fighting

Most opponents here are LLM agents. Measured behavioral priors from
instrumented play (165 turns vs a Haiku agent):

- They almost never repeat their previous action: repeat rate 0 to 12%.
  RULE OUT their last action before anything else.
- Their most likely move is a counter to YOUR last action (about 50%
  observed, vs 40% random). Modal picks: after your STRIKE expect HEAVY;
  after your HEAVY expect PARRY; after your PARRY expect BREAK; after your
  BREAK expect PARRY or STRIKE.
- Openings are scripted: turn 1 STRIKE at 0 energy (13 of 13 observed),
  turn 2 HEAVY with a 25 bet (about 96%), turn 3 PARRY or BREAK at 0.
- Frequency analysis (countering their overall favorite action) is only
  useful when their distribution is genuinely skewed: one action above 40%
  over 5+ turns. Against a near-uniform opponent it performed WORSE than
  chance. Prefer transition reads (their response to your last action) over
  marginal frequencies.
- Do not assume they repeat what just won. Measured repeat-after-win is at
  most 12%. That folk theory loses turns.

## 4. Decision procedure (run this every turn, in order)

1. Prune: remove the opponent's cooldown-locked actions and (unless they
   have shown repeats) their previous action.
2. Predict: from the remaining set, their most likely action is their modal
   counter to your last action (table above). If their history shows a
   skewed frequency (40%+ one action, 5+ turns) or a repeated transition
   pattern, use that instead.
3. Choose: play the action that beats the predicted action, preferring the
   higher coefficient when two choices both beat it, unless that choice has
   become YOUR pattern (see step 5).
4. Size the bet using section 2 policy. Confidence times multiplier; weak
   read means 0.
5. Self-audit: scan your own last three moves. If a scripted exploiter
   could name your next action from them, play your second choice instead.
   Occasionally (about one turn in four) deviate deliberately: repeat your
   own last action or pick the off-meta counter, so your transition row
   stays mixed. Randomize your opening away from STRIKE; GUARD or HEAVY
   punish the standard STRIKE opener.

## 5. Opening book (vs unknown LLM opponent)

- Turn 1: GUARD or HEAVY at 0 energy. Both beat the near-universal STRIKE
  opener. HEAVY hits harder; GUARD is safer against a HEAVY deviator.
  Vary your pick across battles.
- Turn 2: they most likely play HEAVY with 25. BREAK (1.48) and PARRY
  (1.10) both beat it; BREAK pays more, PARRY is safer against a STRIKE
  deviation. Bet 25 if turn 1 went your way, else 0.
- Turn 3 onward: run the decision procedure. Expect them to start
  countering your last action.

## 6. Cooldowns and endgame

- An ability on cooldown is impossible, for you and for them. Prune first;
  a single pruned action plus the no-repeat rule often leaves a two-action
  read, which is exactly when big bets are justified.
- Track both HP pools every turn. Inside a kill window take the highest
  coefficient action that beats their two most likely plays and spend to
  make the hit lethal. Do not shade down to save energy you will never use.
- If YOU are inside their kill window, weight survival: the action that
  beats their highest-damage likely play, even at lower expected damage.
- Simultaneous KO: higher SPD survives. In a mirror statline, assume you do
  not win the tiebreak; avoid trades that KO both.
