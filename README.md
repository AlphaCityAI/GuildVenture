# GuildVenture

A cooperative cyberpunk RPG played through a Telegram bot. Gauntlet combat uses
deterministic Python rules; OpenAI adds names, artwork, and narration. PostgreSQL
stores sessions, profiles, and an idempotent reward/XP event ledger.

## Run locally

Use Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/). Install the committed
dependencies with `uv sync --frozen`, set the environment variables from
`.env.example`, and run `uv run python main.py`. The bot reads the process
environment; it does **not** automatically load a `.env` file. Never commit keys.

Startup applies the additive SQL migrations under `migrations/`. Run one polling
process per Telegram bot token. Independent chats process concurrently (maximum
16 handlers); actions within one chat are serialized, and busy taps receive an
immediate explanation. Session writes use revision checks. Global profile
updates use short row-lock transactions, including first-time profile creation.

## Player flow

- `/start` or `/venture` opens a menu or resumes the current game.
- The owner chooses a mode/route. Players preview factions, join, and ready up.
  The owner explicitly starts the encounter. `/join` never changes a live boss.
- Use ability buttons during a gauntlet and typed actions during a campaign.
  `/status` or `/resume` restores the current controls after a restart or lost
  message. Boss Info does not remove the combat panel.
- After victory, the owner ascends or banks a claim for each participant,
  including fallen participants. `/rewards` lists pending claims and recent
  receipts. Claiming a reward does not reset another player's claim.
- `/inventory` filters by slot/rarity, compares items, and uses stable item IDs.
  Changes take effect at the next floor or campaign start. Discard requires a
  confirmation for that item, on the current owner-bound inventory view.
- `/collection` shows the latest 20 saved character collectibles. Characters
  are cosmetic for now; no ally combat role or new progression system is added.
- `/settings` shows the owner's image toggle in the menu/lobby/scouting phase.
  The game supports one session per chat, in its original Telegram topic.
- `/endgame` is owner-only. Pending reward claims remain in player profiles.
  Completed runs are banked before returning to the menu. Active fights are
  abandoned without additional victory rewards.

## Rules clarified in this pass

Existing faction HP, ability values, boss templates, floor scaling, item scaling,
and rarity thresholds are preserved. Boss damage/targets/healing are computed
locally; narration can never change the resolved state.

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
- Campaign skill/category assessment is schema-validated; Python resolves the
  outcome. Only a successful completion action enters victory. Repeated identical
  milestone IDs cannot award XP twice. AI-assessed campaign milestones remain a
  narrative trust boundary, not a competitive scoring system.
- Legacy item durability is retained in storage but hidden from the UI because
  it has no implemented gameplay effect. Charges continue to depend on rarity.

## Recovery and deployment

1. Back up PostgreSQL before upgrading. Test the backup restore and run this
   branch against a **separate bot token and database** first.
2. Stop the old polling worker before deploying. Deploy one worker from the
   committed lockfile with `uv sync --frozen --no-dev` and `uv run --no-sync python main.py`.
   The Nixpacks configuration selects Python 3.12 and uv's frozen install path
   using the documented [Python provider options](https://nixpacks.com/docs/providers/python).
3. Migration 001 adds a session revision column and profile event ledger without
   deleting existing JSON. Existing recognized sessions are migrated lazily by
   the app; obsolete processing booleans are cleared during migration. Profiles
   gain stable item IDs and missing collection/reward fields when accessed.
   Unrecognized legacy sessions are preserved and require maintainer inspection;
   the bot never silently replaces an unknown saved schema.
4. Test a two-player lobby, combat turn, bank, item claim, inventory change, and
   campaign completion in staging. Restart during a pending reward and confirm
   `/rewards` recovery. Verify real Telegram permissions and provider access.
5. Rollback is **not** simply running the old bot against newly written session
   JSON: the old bot does not understand new phases or pending claims. Stop the
   worker and restore a coordinated database/code backup, or perform an explicit
   forward repair. Do not remove the ledger independently of the profiles.

An action stores its session outcome and pending profile events before delivering
messages. `/status`, `/venture`, or `/rewards` retries those events idempotently.
Reward selection/rolls are reserved, then the item/character and XP are committed
in one profile transaction. Provider failure uses locally generated flavor; an
interruption keeps the entitlement for retry. Failed photo delivery falls back
to text. A resend uses the saved receipt and never re-awards XP or regenerates art.

There is no durable turn lock. A cancelled pre-commit turn can be retried; a
post-commit delivery failure is recovered through `/status`. Optional encounter
art is bounded to eight background jobs, dropped when its run/floor ends, and
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
| `game.py` | Session transitions, combat, campaign rolls, rarity and reward odds |
| `profiles.py` | Pure XP, inventory, entitlement and reward mutations |
| `database.py` | PostgreSQL version checks, profile transactions and event deduplication |
| `presentation.py` | Safe plain text, bounded captions, panels and retry handling |
| `ai_service.py`, `prompts.py` | Bounded optional AI requests and validated campaign assessment |
| Data modules | Existing faction abilities, boss traits, locations and item templates |

New progression, crafting, combat allies, boss phases, chapter systems, and
expanded cinematics remain out of scope. Do not remove legacy player snapshots
from the repository or rewrite Git history without separately reviewing their
archival and privacy requirements.
