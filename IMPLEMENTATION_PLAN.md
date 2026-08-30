# Reliability and UX implementation

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
