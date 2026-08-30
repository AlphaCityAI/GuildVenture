# Solo and multiplayer reliability review

This review follows merged PR #5 and addresses the reported faction-confirmation
loop. The gameplay designs remain AI-authored; this pass changes interaction
reliability and corrects a few existing rules/flow inconsistencies.

## Reproduced problem

Every game button used the database row revision as its expiry token. A setting
change, another player's join/readiness, or pending-event cleanup could invalidate
a faction confirmation even though the lobby was still open. Successful joins
also edited an older party message without sending a visible confirmation beside
the faction card. A repeated click then reported expiry. Existing service tests
constructed callbacks from the latest state, bypassing this user-facing problem.

The new rendered-button regression was run against merged `origin/main` and failed
with an empty party after confirmation. It passes with this repair. The test
harness captures actual InlineKeyboardMarkup, message IDs, chat/topic, and button
payloads, then delivers real Telegram CallbackQuery/Update objects through the
application dispatcher. It does not invent fresh callbacks to advance a flow.
Authorization/legacy-format tests deliberately fabricate controls where indicated.

## Changes

- Game controls now identify their lobby, preparation floor/chapter, or exact
  turn, independently of storage revisions. Owner, actor, phase, run, topic, and
  optimistic database-write checks remain enforced. No SQL/JSON migration is needed.
- Faction selection confirms the join in a newly delivered party panel. Repeating
  that same choice does not duplicate the player or reset their loadout. Ready and
  Not ready are explicit, repeatable settings. Empty/unready starts name the next step.
  Starts and campaign assessments show progress notices; saved combat results are
  delivered before waiting for optional AI narration.
- Concurrent taps wait briefly on the same chat lock. Timeout/cancellation cannot
  replace a lock that another request still owns. Obsolete game panels recover
  automatically without replaying their action. Read-only recovery never rerolls AI.
- `/start` and `/venture` resume without silently changing menu ownership or moving
  a session to another topic. An explicit idle-menu hosting button permits a new
  host, while active runs retain their existing owner.
- `/act` and `/act@BotUsername` support campaign actions in groups with privacy
  mode enabled. Empty/oversized actions, other bots' commands, wrong topics, and
  out-of-turn players cannot consume a turn or trigger an assessment request.
- Hire Help and Dig for Treasure are available to the clicking player without
  requiring ownership of the group menu. One-use rolls and personal resource
  checks remain intact.
- Earned Gauntlet rewards can be banked during next-floor scouting, including via
  `/endgame`. Previously this intermediate phase could discard the unbanked claim.
- Technology and stealth ally strikes use Enertech and Umbral damage respectively,
  matching equipment bonuses and boss traits instead of nonexistent damage types.

## Modes and action coverage

| Area | Verified paths |
|---|---|
| Solo Gauntlet | Every faction on every route: preview, confirm, ready, start, combat to victory, bank, claim, and new menu in a private chat |
| Multiplayer Gauntlet | Concurrent confirmations and readiness from shared older panels; owner start; scaled HP; turn order; independent player claims |
| Open Campaign | All factions with one and three players; addressed `/act`; all three chapters; checkpoints; preparation; restart recovery; final banking and ally claims |
| Individual activities | Non-owner ally/treasure rolls, one-use replay prevention, ownership preserved, next roll from fresh controls |
| Lobby | Preview/back, repeated confirmation, join feedback, explicit Ready/Not ready, leave/rejoin, empty/unready start, loadout changes requiring new consent |
| Combat | Every faction ability, finite charges, guard, counter, boss info, environment interaction, equipped ability, all three ally support kinds, stale-turn rejection |
| Survival | Acting player and other players falling together; rotation to surviving player; party defeat; reward banking; owner abandonment |
| Preparation | Per-player rest, shared readiness, stale controls from earlier floors, refreshed encounter loadouts, bank at camp/scouting |
| Profile actions | Equip/talent selection, roster deployment, workshop preview and confirmation across restart, cancellation of destructive discard, salvage, receipt replay, cross-user rejection |
| Recovery | `/status`/`/resume`, legacy expired controls, event cleanup, failed join delivery, callback queue timeout/cancellation, no run reset on reopening |
| PostgreSQL | Real JSONB round trips and concurrent lobby/readiness flows for Gauntlet and Campaign, plus the existing migration/transaction/reward/startup tests |

The broader domain and service suites continue to cover all equipment tiers/slots,
AI contract validation/fallbacks, crafting resource races, progression bounds,
campaign assessment failures, long Unicode messages, delayed artwork, and database
startup. Keep one polling worker per bot token; cross-chat profile changes still
use database transactions and event deduplication.

## Validation and rollout

Run `uv run pytest -q` and `uv run ruff check .`. PostgreSQL cases require a
disposable `TEST_DATABASE_URL` ending in `_test`; CI runs them on PostgreSQL 16
with Python 3.11 and 3.12. The new UI tests use modest valid authored encounters
and controlled rolls to verify reachability, not production win rates or AI quality.

Before deploying, retain the backup and single-worker precautions in README.
Existing lobbies need no reset: `/status` supplies the new controls, and an expired
old button also recovers them. Smoke-test these actual Telegram interactions:

1. In a private chat, preview a faction, confirm, Ready, and Start. Verify the joined
   player and next buttons appear as a new message, then finish a combat turn.
2. With two accounts in a group/topic, open separate previews, then confirm/ready
   from the original messages. Verify both join and the owner can start.
3. Play a campaign turn using `/act@YourBotUsername` with privacy mode enabled,
   progress through a checkpoint, and confirm the next camp/turn controls work.
4. Tap a previous-turn button: no second action should occur, and a fresh panel
   should appear. Bank at a victory or while scouting the next floor; each player
   should claim their own saved reward.

These tests do not contact live Telegram or paid AI endpoints and do not change
production data. They cannot verify the deployed bot's group permissions, provider
access, or the quality/difficulty of every generated encounter. Campaign assessment
still requires AI availability; failure preserves the turn for retry. Active games
do not auto-skip absent players, transfer ownership, or resurrect fallen members.
