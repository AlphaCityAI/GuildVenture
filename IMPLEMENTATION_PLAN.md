# Reliability and UX implementation

Historical plan for merged PR #4. The subsequent gameplay work is described in
[GAMEPLAY_PLAN.md](GAMEPLAY_PLAN.md); the README describes current behavior.

Branch: `fix/reliability-and-ux`. Base: `2451f14`.

Scope: correct existing behavior and improve Telegram usability. No new factions,
bosses, crafting, combat ally roles, progression systems, or campaign chapters.
Model changes remain a separately evaluated maintenance task; both model names
remain configurable. No merge or production deployment is part of this task.

1. Establish importable domain code, dependency injection, real-library tests,
   and repository transactions/version checks. Preserve existing JSONB data.
2. Bind callbacks to the session revision and actor; replace index-based item
   controls; make XP/reward operations idempotent and recoverable.
3. Restore multiplayer through faction preview, join/leave/ready and owner Start.
   Reject mid-fight joining without modifying the boss. Keep one run per chat.
4. Implement deterministic combat effects, traits, damage/deaths, buff lifetime,
   canonical categories, rarity boundaries and truthful bonus displays.
5. Restore the existing campaign completion transition and persist generated
   character collectibles. Do not invent new campaign or ally mechanics.
6. Add onboarding/help/status, safe text/media delivery, compact battle results,
   owner-bound inventory pagination/filter/comparison, explicit loadout timing,
   and text-only presentation. Enable bounded independent-chat processing only
   after state/profile mutation safety is established.
7. Add behavioral and disposable-PostgreSQL tests, CI, configuration and recovery
   documentation. Review diff and CI before creating the pull request.

Decisions: retain +1 per attempted floor and +1 per defeated floor, capped at 60
roll points; cap final reward rolls at 100 so Peerless remains reachable. Current
HP, damage, item scaling and rarity thresholds are retained. Buff values use the
existing percentage-point convention (10 points = one d10 step), consumed by the
next eligible player action. Boss self bonuses modify the next retaliation's
damage percentage because boss attacks do not roll a d10. Owner controls
route/bank/start decisions; each participant
gets an independent persisted reward claim. Item loadouts refresh before a floor.

Acceptance: stale/foreign callbacks cannot mutate data; duplicate events grant
XP/items once; provider/delivery failures cannot erase rewards; two players can
start together; all declared combat effects are supported; all six reward tiers
are reachable; negative HP cannot leave an actor alive; campaign completion
reaches victory; another chat remains responsive during slow narration; app
imports and tests require no production credentials.

## Completion and validation

The implementation steps above are complete. The original monolithic handlers
are now separated into domain, persistence, provider, and Telegram service modules.
Regression coverage includes multiple simultaneous deaths, legacy campaign
migration, visible `/status` recovery, and invalid startup settings.

- Local Python 3.12: 63 tests passed; six database tests skipped without a local
  disposable PostgreSQL server. Ruff and the frozen dependency lock check pass.
- GitHub CI: all 69 tests passed on both Python 3.11 and 3.12 with PostgreSQL 16,
  including additive migration, concurrent profile mutations, rollback, stale
  session writes, and duplicate claims. See the
  [PR validation checks](https://github.com/drjnolen/GuildVenture/pull/4/checks).
  The workflow explicitly selects each matrix interpreter so the local
  `.python-version` pin cannot override the Python 3.11 test job.
- No live Telegram messages, paid provider calls, production database operations,
  merge, or deployment were performed. The README staging and backup checklist
  remains a deployment prerequisite.

Deferred work: new game systems/balance changes, model migration evaluation,
production telemetry dashboards, durable replay of optional scene art, and
archival/privacy review of the legacy tracked player snapshots. Generation caps
are per-process safeguards, not durable billing quotas.
