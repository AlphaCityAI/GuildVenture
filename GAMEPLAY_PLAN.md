# AI gameplay upgrade

Base: merged PR #4 (`10880ac`). Branch: `feat/ai-gameplay-upgrades`.

AI authors encounter moves and counters, campaign chapters and approaches, ally
support skills, talent choices, crafting designs, and victory stories. Python
validates the generated contracts, executes the published effects, and owns
authorization, dice, resource accounting, and durable progress. Generated content
is saved before use and never rerolled by recovery. Invalid/unavailable generation
uses labeled local fallback content; campaign action assessment still fails
without consuming a turn when unavailable.

1. **Persistent allies and progression.** Existing collectibles become deployable
   allies without losing their IDs. Select one ally; its support action consumes
   a turn and has limited charges per encounter/chapter. Participating allies earn
   bond on completions. Levels 2, 5, and 10 unlock two or three AI-authored talent offers;
   select one per milestone. Cumulative combat bonuses remain capped and are
   visible. Loadouts snapshot at encounter/chapter boundaries.
2. **Salvage/crafting.** Confirm an exact backpack item before salvaging it for
   materials. Request a persisted AI upgrade blueprint; preview the output and
   material cost before confirming. The source item, materials, and replacement
   commit atomically, with an idempotent receipt. Equipped, changed, missing,
   unaffordable, stale, or already-upgraded sources cannot be consumed again.
3. **Bosses and recaps.** AI supplies bounded moves, a counter category, and a
   second phase. Persist the next telegraphed move before the player acts; it
   cannot secretly change after seeing the action. Brace and counter actions
   create tactical choices. Track effective damage/healing, blocked damage,
   support, criticals, participation, and ally contributions, including fallen
   players. Generate celebrations only from those saved facts.
4. **Preparation.** After choosing the next route, stop at camp. Living players
   may rest once, equip/craft, deploy an ally, and choose talents. All players
   ready again; changed loadouts invalidate readiness. The owner starts or banks.
   Fallen players retain claims and contribution credit; this release does not
   add resurrection or mid-encounter joining.
5. **Campaign chapters.** AI creates a three-chapter arc with two approach choices
   per chapter. Successful objective completion saves a checkpoint, chapter XP,
   materials, and ally bond. The owner selects an approach for the next chapter;
   AI develops it using previous outcomes. Preparation and readiness separate
   chapters. Final completion unlocks the run reward. Existing one-objective
   campaigns continue as one-chapter stories without losing their objective.
6. **Verification and delivery.** Exercise generated contracts, boundary math,
   compatibility, hostile/stale callbacks, concurrency, partial failures,
   restart recovery, and complete multiplayer/chapter/crafting flows. Run the
   frozen suite with PostgreSQL on Python 3.11/3.12, then create a new PR. No merge
   or production deployment is authorized by this task.

Existing JSON documents are upgraded additively; no destructive SQL migration is
needed. AI requests remain bounded by deadlines, schema limits, concurrency, and
admission caps. Real provider quality and Telegram UX require a separate staging
bot/database before rollout. Model replacement remains a separate maintenance
evaluation; the deployment's configurable defaults are unchanged here.
