# Alpha City RPG / GuildVenture

Preferred communication style: simple, everyday language.

The current setup, rules, deployment and recovery instructions are in README.md.
The gameplay plan is in GAMEPLAY_PLAN.md; IMPLEMENTATION_PLAN.md records PR #4.
The original audit under
audit/ is historical and references commit 2451f14; tests/ verifies current code.

Architecture:
- main.py registers Telegram handlers and initializes/closes services.
- bot_service.py manages authorized sessions, inventory and reward delivery.
- player_features.py implements ally, talent and workshop menus.
- game.py resolves combat and campaign outcomes locally.
- gameplay_content.py validates AI-authored gameplay designs and fallback content.
- encounters.py executes published boss intents and records contribution facts.
- profiles.py contains pure transactional profile mutations.
- database.py uses PostgreSQL JSONB, optimistic session revisions, short profile
  transactions and an idempotent profile_events ledger.
- presentation.py sends safe plain text, bounded captions and current controls.
- ai_service.py validates and bounds optional narration/art and campaign assessment.

One polling worker handles independent chats concurrently, with one active
mutation per chat. One session exists per chat in its original topic. The owner
controls start/route/bank; players use explicit lobby ready controls.

AI authors boss moves/counters, campaign chapters, ally support skills, talents,
crafting abilities and victory stories. Published effects are persisted before
use. Player levels unlock capped talent choices; allies deploy at encounter
boundaries; salvage/crafting use atomic transactions. Camp readiness, chapter
checkpoints and contribution reports are recoverable after restart. Use synthetic
provider fixtures in tests; evaluate creative quality and balance in staging.

Keep production tokens, player data and private prompts out of logs and fixtures.
Do not start a second worker with the production bot token for testing. Use the
documented disposable PostgreSQL tests and a separate staging bot for live QA.
