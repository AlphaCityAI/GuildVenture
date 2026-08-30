# GuildVenture

A cooperative cyberpunk RPG played through a Telegram bot. AI authors encounter
mechanics, chapters, ally skills, talent choices, crafting designs, and narration.
Python validates those contracts, executes their published effects, and protects
progress and resources. PostgreSQL stores sessions, profiles, and an idempotent
event ledger. The feature plan is [GAMEPLAY_PLAN.md](GAMEPLAY_PLAN.md); the subsequent
solo/multiplayer reliability review is [PLAYABILITY_REVIEW.md](PLAYABILITY_REVIEW.md).

## Run locally

Use Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/). Install the committed
dependencies with `uv sync --frozen`, set the environment variables from
`.env.example`, and run `uv run python main.py`. The bot reads the process
environment; it does **not** automatically load a `.env` file. Never commit keys.

Startup applies the additive SQL migrations under `migrations/`. Run one polling
process per Telegram bot token. Independent chats process concurrently (maximum
16 handlers); actions within one chat are serialized. Button presses are acknowledged
immediately and may wait up to three seconds behind another action; longer waits
receive a retry notice. Text commands during an active action receive a busy notice.
Session writes use revision checks. Global profile
updates use short row-lock transactions, including first-time profile creation.

## Player flow

- `/start` or `/venture` opens a menu or resumes the current game.
  Reopening a menu does not replace its run or owner. At an idle menu, **Host a
  new venture** explicitly lets another player host; active games cannot be taken over.
- The owner chooses a mode/route. Players preview factions, join, and ready up.
  Press **Choose this faction**, then **Ready**, then **Start (owner)**. One ready
  player is sufficient for solo play, including private chats. In multiplayer,
  each player chooses and readies themselves; shared lobby buttons remain usable
  when someone else joins. **Ready** and **Not ready** set an explicit value, so
  duplicate taps do not toggle it back. `/join` never changes a live boss.
  Campaign startup generates a saved briefing; the owner chooses an approach,
  then the party prepares and readies for the first chapter.
- Use ability buttons during a gauntlet and `/act <your action>` during a campaign.
  In groups with several bots, use `/act@YourBotUsername <your action>` or reply
  to the bot's message. Ordinary text may not reach the bot under Telegram's
  default group privacy mode; private-chat text continues to work. There is no
  need to disable privacy mode. See [Telegram's privacy rules](https://core.telegram.org/bots/features#privacy-mode).
  `/status` or `/resume` restores the current controls after a restart or lost
  message. Boss Info does not remove the combat panel.
- After victory, the owner ascends or banks a claim for each participant,
  including fallen participants. `/rewards` lists pending claims and recent
  receipts. Claiming a reward does not reset another player's claim.
  Banking remains available while scouting or preparing the next floor.
- Hire Help and Dig for Treasure are personal menu activities available to any
  player; they do not require taking ownership of the group's game. Their free-roll
  buttons remain single-use, with cooldowns and receipt recovery unchanged.
- `/inventory` filters by slot/rarity, compares items, and uses stable item IDs.
  Changes take effect at the next floor or chapter. Discard and salvage require
  separate confirmations for that item on the current owner-bound view.
- `/allies` (also `/collection`) opens the paginated roster and selects one ally.
  `/progression` shows career talents; `/craft` restores a saved blueprint or receipt.
- `/recap` shows the last completed encounter's factual contribution report.
  `/campaign` restores chapter progress; `/camp` restores preparation controls.
- `/settings` shows the owner's image toggle in menu/lobby/scouting/camp phases.
  The game supports one session per chat, in its original Telegram topic.
- Once a session exists, the bot stays silent in every other topic of that chat,
  including General: no conversation replies, command responses, button
  acknowledgements, busy notices, or personal menus. Use the original topic for
  all interactions. This boundary survives restarts and `/endgame`; private chats
  and other groups remain independent. If storage cannot verify the topic, the
  bot logs the failure and stays silent until it can verify it again.
- `/endgame` is owner-only. Pending reward claims remain in player profiles.
  Completed runs are banked before returning to the menu. Active fights are
  abandoned without additional victory rewards.

Faction confirmations and lobby controls last for that lobby. Camp controls last
for that floor/chapter; combat controls last for the exact turn shown. Database
event cleanup, optional narration, and other players readying no longer invalidate
unrelated controls. Old turns/runs are never replayed: tapping an obsolete panel
automatically sends the current controls. Joining, readiness, and completed actions
send the next panel at the bottom of the conversation so success is visible.
Controls issued before this fix still validate at their exact saved revision;
if expired, they recover to the new controls without resetting the game.

## AI gameplay

AI-generated contracts are persisted before players use them. Reopening controls
does not request a new design. Bosses, chapters, talents, and blueprints show their
generation source. Invalid/unavailable generation uses labeled local fallbacks.
Campaign action assessment remains AI-driven: if that request fails, the player's
turn and HP remain unchanged. Generated prose cannot add unlisted effects, spend
resources, grant rewards, or rewrite a completed outcome.

**Boss mechanics.** Every new floor has two or three AI-authored moves in a saved
rotation, with damage, healing, or debuff effects, explicit targets, and counter
categories. The next move and numeric result are visible before the player acts.
Brace halves incoming boss damage to that player for the turn. Counter succeeds
at an adjusted d10 score of 6+, halving the published move; matching faction skill
adds one d10 step per skill point. Both consume the turn. AI selects a phase-two
threshold from 35–60% HP and a +1–2 power bonus. Phase changes affect only the next
published intent, never the current one. Initial power caps are 8 single-target
and 4 party-wide; phase-two caps are 10 and 6, before route/buff modifiers. Debuff
power becomes twice that many negative roll points.

**Contribution recaps.** Reports include effective damage/healing (no overkill or
overheal), blocked damage, support actions, criticals, turns, ally contributions,
fallen participants, and tied honors. AI writes a celebration using the saved
facts. Current-encounter participants receive completion credit, including those
who fell; earlier-floor spectators do not gain later completion XP or ally bond.
Gauntlet completions award `2 + min(floor, 10)` materials per participant.

**Allies.** Existing collectibles retain their IDs and gain a support skill. New
recruits receive AI-authored strike/heal/focus skills with base values 1–6. Deploy
one ally before an encounter/chapter. Its action consumes the owner's turn, with
one charge per encounter. A deployed ally gains one bond per completed encounter
or chapter, even if its owner fell during that encounter. Ranks increase at bond
3 and 6, adding +1/+2 power (cap 8); rank 3 gets two charges. In campaigns, strike
and focus grant twice the skill value as next-action points; heal restores HP.
Changing the roster during a fight never replenishes charges or changes its snapshot.

**Career talents.** Levels 2, 5, and 10 each unlock one permanent choice from two
or three saved AI-authored offers. Offers at an already-reached bonus cap are
filtered out. Combined bonuses cap at +6 max HP, +2 damage, +3 healing, and +10
roll points. The preview shows the actual incremental benefit. These bonuses
apply when the next floor/chapter starts, without rewriting an active fight.

**Preparation.** Choosing the next gauntlet route or campaign approach opens camp.
Living players can recover up to 25% max HP once (rounded up), equip/craft, deploy
an ally, and select talents. All must ready again. A changed equipment/ally/talent
version invalidates that player's readiness. The owner starts, or banks a gauntlet
instead. Fallen players keep contribution credit and claims; resurrection and
mid-fight joining are not included.

**Campaigns.** AI creates a three-chapter arc with two approach choices per chapter.
The next chapter is developed from the owner's chosen approach and saved earlier
outcomes. Successful objective completion saves a checkpoint, 100 XP, five
materials, and deployed-ally bond for each current participant. Intermediate
checkpoints lead to preparation, not final rewards. The last chapter unlocks
banking. Identical milestone IDs are deduplicated within a chapter. AI decides
whether an action could complete an objective; a successful game roll is still
required. This narrative adjudication is not a competitive anti-cheat system.

**Salvage and crafting.** Salvaging a backpack item yields 2/4/7/12/20/35 materials
across the six rarity tiers. Request an upgrade blueprint from its inventory
details; AI designs the name, specialty, and ability while retaining the slot.
Upgrading advances exactly one rarity tier and costs 8/16/28/48/80 materials;
Peerless cannot upgrade further. The preview shows the complete output and exact
cost. Confirming atomically consumes the unchanged source item and materials and
adds the replacement. Replays return a receipt without spending again. A moved,
changed, or missing source cannot be consumed. New blueprints have a persistent
30-second cooldown; reopening the same saved design does not regenerate it.
Generated ability base values 1–6 scale by `1 + floor(target_rarity_index / 2)`;
focus doubles the result (cap 30 points). Charges are 1/1/2/2/3/3 by rarity.

## Shared rules

Base faction HP/abilities, boss HP scaling, equipment passive scaling, and rarity
thresholds remain unchanged. Talents, allies, and generated moves add the gameplay
above. Boss archetype resistances/vulnerabilities continue to apply.

- Roll bonuses use points: 10 points = one d10 step on the next eligible player
  action. Buff-casting does not consume a previously stored next-action bonus.
  Location modifiers and hazards affect matching action categories. The final
  d10 score is clamped to 1–10 before applying the existing luck multipliers.
- Boss self roll bonuses modify the next damaging retaliation by the specified
  percentage, since boss attacks do not otherwise roll a d10. Party debuffs
  modify each player's next eligible roll. Boss Info shows real damage-type
  resistance/vulnerability multipliers.
- The run reward bonus is +1 per floor attempted and +1 per floor defeated,
  capped at 60 **roll points**. Final rolls cap at 100; Peerless is reachable.
  The obsolete floor-times-ten percentage label is removed. Banked rewards roll
  from 20–100; free collectibles roll from 1–100. Exact tier odds appear when
  banking. One roll applies to either selected reward type.
- Daily login XP uses UTC and commits its date, XP, level, and title together.
- Legacy item durability is retained in storage but hidden from the UI because
  it has no implemented gameplay effect. Charges continue to depend on rarity.

## Recovery and deployment

1. Back up PostgreSQL before upgrading. Test the backup restore and run this
   branch against a **separate bot token and database** first.
2. Stop the old polling worker before deploying. Deploy one worker from the
   committed lockfile with `uv sync --frozen --no-dev` and `uv run --no-sync python main.py`.
   The Nixpacks configuration selects Python 3.12 and uv's frozen install path
   using the documented [Python provider options](https://nixpacks.com/docs/providers/python).
   Set `DATABASE_URL` on the **bot service** to the existing database's current
   connection URL. On Railway, use a service reference such as
   `${{Postgres.DATABASE_URL}}` (substitute the actual database service name),
   with both services in the same project and environment. See the startup
   troubleshooting section below before changing database endpoints.
3. Migration 001 adds a session revision column and profile event ledger without
   deleting existing JSON. Existing recognized sessions are migrated lazily by
   the app; obsolete processing booleans are cleared during migration. Profiles
   gain stable item IDs and missing collection/reward fields when accessed.
   Unrecognized legacy sessions are preserved and require maintainer inspection;
   the bot never silently replaces an unknown saved schema.
   Gameplay schema 3 adds JSON fields without a destructive SQL migration. Active
   older battles retain their mechanics until the next floor and get partial
   contribution reports. Existing one-objective campaigns remain one-chapter
   stories with their original objective. No old progress is backfilled or erased.
4. Test a two-player lobby, combat turn, bank, item claim, inventory change, and
   campaign completion in staging. Restart during a pending reward and confirm
   `/rewards` recovery. Verify real Telegram permissions and provider access.
   Also test a telegraphed counter/phase transition, ally depletion and next-floor
   refresh, changed-ready loadouts, talent selection, salvage, crafting replay,
   and all three chapters. Review several live AI designs for coherent difficulty
   and story continuity; mocked contract tests do not establish generation quality
   or balance. See [GAMEPLAY_PLAN.md](GAMEPLAY_PLAN.md) for the acceptance record.
5. Rollback is **not** simply running the old bot against newly written session
   JSON: the old bot does not understand new phases or pending claims. Stop the
   worker and restore a coordinated database/code backup, or perform an explicit
   forward repair. Do not remove the ledger independently of the profiles.

### Database startup troubleshooting

`socket.gaierror: [Errno -2] Name or service not known` means the configured
database hostname could not be resolved. This happens **before authentication
or migrations**; merging code cannot repair an incorrect hostname or a private
hostname used outside its network.

- On Railway, inspect the bot service's `DATABASE_URL` reference and confirm it
  points to the existing PostgreSQL service in the same project/environment.
  Use the service's current URL, not a copied hostname from an old deployment.
  Railway resolves `${{Postgres.DATABASE_URL}}` in its Variables configuration;
  the app cannot expand that reference if it is passed literally from a local
  shell or another host. See [Railway PostgreSQL connection guidance](https://docs.railway.com/databases/postgresql).
- A `*.railway.internal` host is reachable only within that Railway private
  network at runtime. A worker on Replit, a local machine, or another Railway
  project/environment cannot use it. Prefer moving the worker into the intended
  private network; if external access is required, explicitly configure an
  authorized public TCP endpoint for the **same database** with the provider's
  TLS settings. Do not guess a public hostname or mix private/public ports.
  See [Railway private network scope](https://docs.railway.com/networking/private-networking/how-it-works).
- Keep database startup/migrations in the runtime start command, not the build.
  This repository already does that. Confirm the database is running before
  restarting the bot. Never paste connection URLs/passwords into logs or issues.
- Copy the complete provider URL. Do not include surrounding quotes in a hosting
  dashboard value; percent-encode special characters if manually assembling
  credentials. Scheme, unresolved-reference, whitespace, and missing-host errors
  are caught before Telegram initialization. Other driver options are validated
  when connecting; existing URL query/TLS options are preserved.

Startup now makes at most five connection attempts, with 2/4/8/16-second waits
and a 15-second connection timeout per attempt (up to about 105 seconds total).
Temporary DNS, refused/timed-out connections, and a starting/busy PostgreSQL
server can recover during this window. Persistent failures stop with deployment
advice and a nonzero exit; the hosting restart policy may start another attempt
cycle. Authentication, invalid database/client configuration, and TLS failures
do not retry. Failed/cancelled pools are terminated before another attempt.

Migrations run only after a connection succeeds. Migration failures are reported
separately, roll back their transaction, and stop startup without a retry loop.
Successful startup logs `Database connected; startup migrations completed.`
Polling does not start until database initialization succeeds. The bot never
switches automatically to `DATABASE_PUBLIC_URL`, changes TLS settings, uses an
in-memory replacement, or resets player data to bypass a connection failure.

An action stores its session outcome and pending profile events before delivering
messages. `/status`, `/venture`, or `/rewards` retries those events idempotently.
Reward selection/rolls are reserved, then the item/character and XP are committed
in one profile transaction. Provider failure uses locally generated flavor; an
interruption keeps the entitlement for retry. Failed photo delivery falls back
to text. A resend uses the saved receipt and never re-awards XP or regenerates art.

There is no durable turn lock. A cancelled pre-commit turn can be retried; a
post-commit delivery failure is recovered through `/status`. Optional encounter
art is bounded to eight background jobs, dropped when its run/floor/chapter ends, and
cancelled at shutdown; it is deliberately not persisted or replayed.

Text/image requests have deadlines, schema limits, a shared concurrency limit,
and per-process UTC-day admission caps (defaults: 1,000 text operations and 100
images). Transient text requests may retry once. These are **not billing quotas**:
they reset on process restart and are not shared across replicas. Use provider
billing controls and alerting as the authoritative cost controls. Free rolls
have a persistent per-player cooldown (30 seconds by default); pending claims
and receipt resends are not charged another cooldown.

Model names remain configurable. The reviewed defaults (`gpt-4-turbo` and
`gpt-image-1`) have a published October 23, 2026 shutdown date; validate replacements
and deployed overrides before then. Model migration is intentionally separate
from this mechanics/UX change. See the
[official deprecation notice](https://developers.openai.com/api/docs/deprecations#2026-04-22-legacy-gpt-model-snapshots).

## Verify changes

```text
uv sync --frozen
uv run ruff check .
uv run pytest -q
```

Unit/service tests require no credentials or live API calls. Database tests
require `TEST_DATABASE_URL` pointing to a disposable PostgreSQL database whose
name ends in `_test`. They use a unique schema per test and remove only that
schema afterward. Without it, those tests skip. GitHub CI runs the complete suite
with PostgreSQL 16 on Python 3.11 and 3.12.

The original audit is under `audit/`. Its historical reproduction harness runs
against the reviewed base revision; its passing assertions describe old bugs.
The regression suite under `tests/` asserts the desired fixed behavior.

## Code map

| Module | Responsibility |
|---|---|
| `main.py` | Startup, configuration, handler registration and shutdown |
| `bot_service.py` | Authorized Telegram flows, recoverable profile events and delivery |
| `player_features.py` | Ally, talent, and crafting menus and confirmations |
| `game.py` | Session transitions, combat, campaign rolls, rarity and reward odds |
| `encounters.py` | Persisted boss intents and factual contribution accounting |
| `gameplay_content.py` | Typed AI designs, limits, and fallback content |
| `profiles.py` | XP, inventory, allies, talents, crafting, and reward mutations |
| `database.py` | PostgreSQL version checks, profile transactions and event deduplication |
| `presentation.py` | Safe plain text, bounded captions, panels and retry handling |
| `ai_service.py`, `prompts.py` | Bounded optional AI requests and validated campaign assessment |
| Data modules | Existing faction abilities, boss traits, locations and item templates |

Model migration, production balance tuning, resurrection, and durable optional
art replay remain separate work. Do not remove legacy player snapshots
from the repository or rewrite Git history without separately reviewing their
archival and privacy requirements.
