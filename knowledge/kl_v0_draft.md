<!-- Knowledge layer v0-draft for the KL arm (Paarthurnax). Distilled solely
from docs/battleclaws-skill.md. HTML comments are stripped before prompt
injection. -->

# BattleClaws Strategy Knowledge (v0-draft)

## Action beats table (memorize this)

Each turn is a blind, simultaneous 5-option RPS. Each action beats exactly
two others and loses to two:

| Action | Beats | Loses to |
|--------|-------------|-------------|
| STRIKE | BREAK, PARRY | HEAVY, GUARD |
| HEAVY | STRIKE, GUARD | PARRY, BREAK |
| GUARD | PARRY, STRIKE | HEAVY, BREAK |
| PARRY | HEAVY, BREAK | STRIKE, GUARD |
| BREAK | HEAVY, GUARD | STRIKE, PARRY |

Mirror (same action on both sides): both deal about 35% of normal damage,
no exchange winner, no tempo bonus.

## Damage formula

damage = power_coefficient x energy_multiplier x (ATK / (ATK + DEF_CONSTANT))
x variance(0.92 to 1.08)

Only the winning action deals full damage. High-coefficient abilities
(break 1.5x, heavy 1.3x) benefit most from energy; guard (0.5x) gets poor
returns from energy investment, so never spend big on guard.

## Energy economy

Start each battle with 15 energy. Cap is 100. Energy does not carry between
battles. Gains per turn: passive +10 x (1 + STA/600), +5 if you won the
exchange, +5 comeback bonus if you lost. Mirrors give no bonus.

Spend tiers and damage multipliers:

| Spend | Multiplier | Multiplier per 25 energy |
|-------|-----------|--------------------------|
| 0 | 1.0x | baseline |
| 25 | 1.4x | +0.4 |
| 50 | 1.8x | +0.4 |
| 75 | 2.3x | +0.5 |
| 100 | 3.0x | +0.7 |

Expected-value guidance:
- Energy is spent WIN OR LOSE. A 50-energy HEAVY countered by PARRY deals 0
  damage and still burns the 50. Multiply the tier bonus by your confidence
  that your action wins before committing.
- The 75 and 100 tiers are more efficient per point, so save large spends
  for turns where you have a strong read or a kill window, and spend 0 or 25
  when your read is weak.
- Only request tiers you can afford. Legal spends are exactly 0, 25, 50, 75,
  100. Anything else is rejected.
- Track opponent energy: if they hold 75+, expect a big committed swing and
  consider the action that beats their favorite attack. If they are under 25
  they can only play the 1.0x tier, so a cheap trade favors whoever has the
  read.

## Timing rules

- Combat is fully simultaneous and blind. The server never reveals the
  opponent's pending move.
- Submit fast: a 15 second submission window opens once the first creature
  submits; at 120 seconds without your move you forfeit; 180 seconds with no
  moves at all is a draw. Timed-out moves default to basic STRIKE with 0
  energy, and history marks them timed_out.
- Simultaneous KO tiebreak: if both hit 0 HP the higher SPD creature
  survives.
- Respect ability cooldowns: an ability with cooldown_remaining above 0
  cannot be played. This applies to the opponent too, which shrinks their
  option set (see below).

## Stat effects

- DEF: incoming damage reduction = DEF / (DEF + 80). Strong diminishing
  returns.
- WIT: +8% damage when your WIT exceeds the opponent's by 50 or more.
- STA: energy regen per turn = base x (1 + STA/600).
- HP: each earned point adds +2 effective HP. Battles use normalized HP
  around 1000 to 1120, so check your_max_hp, not base stats.
- SPD: turn order, dodge modifiers, and the simultaneous-KO tiebreak.

## Opponent modeling from move history

You see the opponent's full move history (action, ability, energy, damage,
timeouts), their ability cooldowns, and their current energy. Use it:

1. Frequency analysis: count their action types so far. If they play HEAVY
   40% of the time, actions that beat HEAVY (PARRY, BREAK) gain value.
2. Recency weighting: weight the last 2 to 3 turns more than turn 1. Agents
   drift toward what just worked. After they win with an action, they often
   repeat it; after a loss they often switch to the action that would have
   beaten yours.
3. Cooldown pruning: if their HEAVY ability shows cooldown_remaining 1+,
   HEAVY is impossible this turn. Rule out impossible actions BEFORE
   computing your counter. With one action pruned, pick the move that beats
   the two most likely remaining actions.
4. Energy reads: their spend history reveals temperament. Big early spenders
   run dry and become predictable low-tier players; hoarders telegraph a
   coming 75 or 100 swing.
5. Timeout tells: timed_out moves are forced STRIKE at 0 energy. A slow
   opponent near their deadline is likely to produce STRIKE; GUARD and
   HEAVY both beat it.
6. Exploit predictability, but audit your own history the same way. If your
   own last three moves show a pattern, assume the opponent sees it.

## When you have no read: mixed strategy

If the opponent looks uniformly random, no action has an edge; every action
beats two and loses to two. Then prefer: (a) low energy spends, since an
unpredictable coin flip does not justify burning multiplier; (b) abilities
with better power coefficients among actions you are otherwise indifferent
about; (c) genuinely varying your own choices so you stay unexploitable.
Reserve big spends for real reads or lethal range (their HP within your
likely damage this turn).
