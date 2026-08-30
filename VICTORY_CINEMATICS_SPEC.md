# Victory contributions and AI celebrations

Implemented in the AI gameplay upgrade. The original proposal is preserved in
Git history; its placeholder archetypes, sample rarity values, cost estimates,
and references to old main.py handlers are not current implementation guidance.

## Saved facts

encounters.py records per-player effective damage, healing, blocked damage,
support actions, critical successes, turns, ability names, and ally damage/healing.
Overkill and overheal do not inflate totals. The encounter roster includes players
who fall before victory. Honest ties receive the same honors. No contribution
metric changes the reward roll or grants extra XP by itself.

New floors and chapters initialize their own counters. A victory or chapter
completion freezes last_recap with its title, contributors, fallen status, honors,
and current gauntlet streak. A party defeat saves a defeat report and resets the
streak. Active legacy encounters receive explicitly partial reports; missing past
contributions are never invented. The last report remains available via /recap,
including after returning to the menu.

## Player presentation

The result is committed before delivery. The victory/checkpoint panel combines
numeric contributions with one optional AI-authored celebration. The prompt gets
the saved facts and factions; it cannot change stats or award new items. The
story is saved when available. Reopening /status or /recap does not generate a new
story or replay XP. Provider failure leaves the numerical report available.

Reports use plain text and split at safe Telegram lengths. Fallen participants
remain visible. Honors include Most Lethal, Field Medic, Guardian, Clutch Moment,
and Team Support, only when their associated metric is positive. Several honors
or ties can appear; no arbitrary MVP suppresses the rest of the party.

## Related gameplay

AI-authored boss intents are saved before turns and support counters and phases.
Deployed allies have finite support charges and earn bond through completion
events. Chapter checkpoints, completion materials, and XP share the idempotent
profile event pipeline; the recap itself is presentation, not an award ledger.

Victory imagery remains optional encounter/reward art. A durable cinematic media
queue, animated recap, faction-specific templates, and paid-provider cost forecasts
are not implemented. There is no fixed cost claim: actual cost depends on deployed
models, tokens, generated images, retries, and provider pricing.

## Validation

Behavioral tests verify effective damage/healing, fallen contribution credit,
phase-intent consistency, ally support accounting, restart recovery, and chapter
checkpoints. PostgreSQL tests verify completion event deduplication alongside XP,
materials, and ally bond. Live AI narrative quality and real Telegram layout still
require the staging checklist in README.md.
