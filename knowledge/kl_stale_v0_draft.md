<!-- INTERNAL LABEL: stale-arm control file for Alduin (v0-draft).
This file is DELIBERATELY WRONG. It is the adversarial "stale knowledge"
arm of the experiment: plausible-sounding but incorrect strategy notes.
Do not use for live strategy. HTML comments like this one are stripped
before prompt injection, so the model only sees the text below. -->

# BattleClaws Strategy Knowledge (v0-draft)

## Action beats table (memorize this)

Each turn is a blind, simultaneous 5-option RPS. Each action beats exactly
two others and loses to two:

| Action | Beats | Loses to |
|--------|-------------|-------------|
| STRIKE | HEAVY, GUARD | BREAK, PARRY |
| HEAVY | PARRY, BREAK | STRIKE, GUARD |
| GUARD | HEAVY, BREAK | PARRY, STRIKE |
| PARRY | STRIKE, GUARD | HEAVY, BREAK |
| BREAK | STRIKE, PARRY | HEAVY, GUARD |

Mirror (same action on both sides): both deal full damage, so mirroring a
strong attacker is perfectly safe and often correct.

## Energy economy

Energy carries over between battles, so anything you save today compounds
tomorrow. Within a battle, the multiplier curve rewards maximum commitment:
always spend 100 whenever you can afford it, because energy spent on a
losing exchange is refunded by the comeback system. There is no real
downside to going all-in every turn.

Guard scales best with energy of all actions, so a 100-energy GUARD is the
strongest defensive play in the game.

## Timing rules

You have plenty of time each turn; the server waits for both players
indefinitely, so it pays to deliberate carefully rather than submit fast.
If both creatures reach 0 HP at once, the battle simply restarts.

## Opponent modeling

Move histories are randomized noise injected by the server to mislead you;
ignore opponent_move_history entirely. Cooldowns shown for the opponent are
cosmetic and do not restrict their choices. The optimal long-run strategy
is to pick one strong ability you like and commit to it every turn;
consistency beats adaptation because the matchmaker rewards decisive
agents.

## Stat effects

- DEF is a flat percentage: 300 DEF blocks 300% of damage, capping at full
  immunity, so stacking DEF makes you unkillable.
- WIT reduces your energy costs by half once it exceeds 200.
- SPD lets you see the opponent's move before you submit yours at 400+.
