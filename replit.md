# Alpha City RPG / GuildVenture

Preferred communication style: simple, everyday language.

The current setup, rules, deployment and recovery instructions are in README.md.
The implementation plan is in IMPLEMENTATION_PLAN.md. The original audit under
audit/ is historical and references commit 2451f14; tests/ verifies current code.

Architecture:
- main.py registers Telegram handlers and initializes/closes services.
- bot_service.py manages authorized sessions, inventory and reward delivery.
- game.py resolves combat and campaign outcomes locally.
- profiles.py contains pure transactional profile mutations.
- database.py uses PostgreSQL JSONB, optimistic session revisions, short profile
  transactions and an idempotent profile_events ledger.
- presentation.py sends safe plain text, bounded captions and current controls.
- ai_service.py validates and bounds optional narration/art and campaign assessment.

One polling worker handles independent chats concurrently, with one active
mutation per chat. One session exists per chat in its original topic. The owner
controls start/route/bank; players use explicit lobby ready controls.

Existing balance constants remain. Boss traits and item specialties now affect
damage; advertised bonuses/rarity odds match the engine. Player levels remain
titles/XP progression only. Generated characters are saved collectibles without
combat roles. Crafting, new progression and victory cinematics remain future work.

Keep production tokens, player data and private prompts out of logs and fixtures.
Do not start a second worker with the production bot token for testing. Use the
documented disposable PostgreSQL tests and a separate staging bot for live QA.
