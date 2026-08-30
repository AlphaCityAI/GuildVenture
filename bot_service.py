"""Telegram orchestration over deterministic game rules and atomic repositories."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import logging
import random
import uuid
from collections import OrderedDict

from telegram.error import TelegramError

import game
import encounters
import gameplay_content as content
import presentation as ui
import profiles
from abilities import ABILITIES
from database import PersistenceError, StateConflict
from game_constants import FACTIONS, GAUNTLET_ROUTES
from item_traits import ITEM_SLOTS, ITEM_SPECIALTIES, RARITY_ORDER
from profiles import InvalidAction
from player_features import PlayerFeatures

logger = logging.getLogger(__name__)

HELP = """Welcome to Alpha City.

Gauntlet: choose a route, form a party, ready up, then the owner starts. Use ability buttons on your turn. After victory the owner can ascend or bank a reward for each participant.

Open Campaign: form a party and type an action on your turn. The game resolves a skill roll; completing the objective unlocks rewards.

AI designs boss moves, campaign chapters, ally skills, talents, crafting blueprints, and victory scenes. Published effects and resource costs are saved before use.
Hire Help recruits allies. Deploy one with /allies. At levels 2, 5, and 10 choose a talent with /progression. Dig for Treasure creates equipment.

/venture — open or resume the game
/join — join the faction lobby (no joining mid-fight)
/status or /resume — recover current controls
/profile — XP and career stats
/inventory — compare, filter, equip or discard items
/allies or /collection — deploy and inspect your ally roster
/progression — choose level-unlocked talents
/craft — saved upgrade blueprint and material balance
/recap — factual contributions from the last completed encounter
/campaign — chapter progress and current objective
/camp — current preparation controls
/rewards — claim pending rewards, even from ended runs
/settings — owner presentation options between fights
/endgame — owner ends the run; already banked claims remain
/help — this guide

One game runs per chat, in its original topic. Equipped items refresh when the next floor or chapter starts. Rolls use +1 point per attempted floor and +1 per defeat, capped at 60; final reward rolls cap at 100. Inventory and rewards belong to the player opening their controls.

Boss intent is shown before your turn. Brace halves incoming damage to you; a successful counter (6+ d10) halves the published move. Both cost a turn. Ally support also costs a turn and refreshes only at the next encounter/chapter. Between floors/chapters, living players may rest once, change loadouts, and ready again. Completed chapters save XP/materials; the final chapter unlocks the run reward.
"""


class BotService(PlayerFeatures):
    def __init__(self, repository, ai, rng=None, default_images=True, free_roll_cooldown=30):
        self.repo, self.ai = repository, ai
        self.rng = rng or random.Random()
        self.default_images, self.free_roll_cooldown = default_images, free_roll_cooldown
        self.locks = {}
        self.panels = {}
        self.inventory_views = OrderedDict()
        self.personal_views = OrderedDict()
        self.tasks = set()
        self.closing = False

    async def profile(self, user):
        result = await self.repo.mutate_profile(user.id, lambda p: profiles.normalize(p, user.first_name) and None)
        return result.profile

    async def state(self, chat_id):
        state = await self.repo.load_state(chat_id)
        if game.migrate_state(state):
            await self.repo.save_state(chat_id, state)
        return state

    async def flush_events(self, chat_id, state):
        if not state.get("events"):
            return
        for event in state["events"]:
            await self.repo.mutate_profile(event["user_id"], lambda p, e=event: profiles.apply_event(p, e), event["id"])
        state["events"] = []
        await self.repo.save_state(chat_id, state)

    async def commit(self, chat_id, state):
        state["last_action_timestamp"] = game.now()
        await self.repo.save_state(chat_id, state)
        await self.flush_events(chat_id, state)

    async def say(self, update, context, text, rows=None):
        return await ui.send(
            context.bot, update.effective_chat.id, update.effective_message.message_thread_id, text, rows
        )

    async def handle(self, update, context):
        if update.effective_chat is None or update.effective_user is None or update.effective_message is None:
            return
        chat_id = update.effective_chat.id
        query = update.callback_query
        # Answer immediately, before database/provider waits. All content remains
        # untrusted until the relevant session/profile authorization below.
        if query:
            try:
                await query.answer()
            except TelegramError:
                pass
        lock = self.locks.setdefault(chat_id, asyncio.Lock())
        if lock.locked():
            await self.say(update, context, "An action is being resolved in this chat. Please try /status shortly.")
            return
        try:
            async with lock:
                if query:
                    await self.callback(update, context)
                else:
                    await self.command_or_action(update, context)
        except InvalidAction as exc:
            await self.say(update, context, str(exc))
        except StateConflict:
            await self.say(update, context, "Another action updated this game. Use /status for current controls.")
        except PersistenceError:
            logger.exception("Database operation failed for chat_id=%s", chat_id)
            await self.say(
                update,
                context,
                "Storage is temporarily unavailable. No replacement profile was created. Try /status or /rewards again shortly.",
            )
        except TelegramError:
            logger.warning("Telegram delivery failed for chat_id=%s; state can be recovered with /status", chat_id)
        except Exception:
            logger.exception("Unexpected handler failure for chat_id=%s", chat_id)
            await self.say(
                update,
                context,
                "This action could not finish. Use /status or /rewards to recover; no turn lock remains.",
            )
        finally:
            # Busy requests never wait on this lock, so it can be removed safely.
            if not lock.locked():
                self.locks.pop(chat_id, None)

    async def command_or_action(self, update, context):
        text = update.effective_message.text or ""
        command = text.split()[0].split("@")[0].lower() if text.strip() else ""
        user, chat_id = update.effective_user, update.effective_chat.id
        if command in {"/help", "/info"}:
            return await self.say(update, context, HELP)
        if command == "/profile":
            p = await self.profile(user)
            stats = p["stats"]
            return await self.say(
                update,
                context,
                f"Player dossier: {p['username']}\n{p['title']} · Level {p['level']}\n"
                f"XP: {ui.bar(p['current_xp'], p['xp_to_next_level'])}\n"
                f"Bosses: {stats['bosses_defeated']} defeated / {stats['bosses_attempted']} attempted\n"
                f"Highest floor: {stats['highest_floor']} · Campaign actions: {stats['moves_made']}\n"
                f"Pending rewards: {len(p['pending_rewards'])} · Allies: {len(p['collectibles'])} · Materials: {p['materials']}\n"
                f"Chapters: {stats['chapters_completed']} · Campaigns: {stats['campaigns_completed']}\n"
                "Choose career talents with /progression; deploy a recruit with /allies.",
            )
        if command == "/leaderboard":
            leaders = await self.repo.top_profiles()
            lines = ["Alpha City leaderboard"] + [
                f"{i}. {p['username'] or 'Agent'} — Floor {p['highest_floor']}, {p['bosses_defeated']} bosses"
                for i, p in enumerate(leaders, 1)
            ]
            return await self.say(
                update, context, "\n".join(lines) if leaders else "No ranked runs yet. Start with /venture."
            )
        if command == "/inventory":
            return await self.show_inventory(update, context, await self.profile(user))
        if command in {"/collection", "/allies"}:
            return await self.show_allies(update, context, await self.profile(user))
        if command == "/progression":
            return await self.show_progression(update, context, await self.profile(user))
        if command == "/craft":
            return await self.show_craft(update, context, await self.profile(user))
        state = await self.state(chat_id)
        if state:
            await self.flush_events(chat_id, state)
        if command == "/rewards":
            return await self.show_rewards(update, context, await self.profile(user))
        if command == "/recap":
            return await self.say(update, context, ui.recap_text(state.get("last_recap")))
        if command in {"/start", "/venture"}:
            daily = await self.repo.mutate_profile(
                user.id,
                lambda p: profiles.daily_login(p, user.first_name, dt.datetime.now(dt.timezone.utc).date().isoformat()),
            )
            if daily.result["xp"]:
                await self.say(
                    update, context, f"Daily login: +{daily.result['xp']} XP. Level {daily.result['level']}."
                )
            if not state or state["phase"] == "menu":
                state = game.new_state(user.id, update.effective_message.message_thread_id, state)
                state["settings"]["images"] = (
                    state["settings"].get("images", self.default_images)
                    if state.get("_revision")
                    else self.default_images
                )
                await self.commit(chat_id, state)
            if command == "/start":
                await self.say(
                    update,
                    context,
                    "Welcome! Start a solo or cooperative game below. Use /help for the rules, /inventory for equipment, and /status to resume.",
                )
            return await self.show_status(update, context, state)
        if not state:
            return await self.say(update, context, "No active game. Use /venture to start.")
        if update.effective_message.message_thread_id != state.get("thread_id"):
            raise InvalidAction("This chat's game is in another topic. Return there to play or resume.")
        if command in {"/status", "/resume", "/settings", "/camp", "/campaign"}:
            return await self.show_status(update, context, state, recover=True)
        if command == "/join":
            if state["phase"] != "lobby":
                raise InvalidAction("Joining is available in the faction lobby only. The current boss is unchanged.")
            return await self.show_status(update, context, state)
        if command == "/endgame":
            game.require_owner(state, user.id)
            if state["phase"] in {"victory", "defeat"} or (
                state["phase"] == "preparation" and state["game_mode"] == "gauntlet"
            ):
                await self.bank(chat_id, state)
            state = game.new_state(user.id, state["thread_id"], state)
            await self.commit(chat_id, state)
            await self.say(update, context, "Run ended. Earned pending rewards remain available through /rewards.")
            return await self.show_status(update, context, state)
        if command.startswith("/"):
            return await self.say(update, context, "Unknown command. Use /help for available commands.")
        if state["phase"] == "combat":
            return await self.say(
                update, context, "Use an ability button for combat. /status restores the current controls."
            )
        if state["phase"] != "campaign":
            return
        game.require_actor(state, user.id)
        if not text.strip() or len(text) > 1000:
            raise InvalidAction("Please describe your action in 1–1,000 characters.")
        assessment = await self.ai.assess(state, text)
        if assessment is None:
            return await self.say(
                update, context, "The narrator is unavailable. Your turn and HP are unchanged; please retry shortly."
            )
        result = game.resolve_campaign(state, user.id, text, assessment.model_dump(), self.rng)
        await self.commit(chat_id, state)
        await self.send_result(update, context, result, state)
        await self.show_status(update, context, state)

    async def callback(self, update, context):
        data = update.callback_query.data or ""
        if data.startswith("i:"):
            return await self.inventory_callback(update, context, data)
        if data.startswith("r:"):
            return await self.claim_callback(update, context, data)
        if data.startswith("p:"):
            return await self.personal_callback(update, context, data)
        if data.startswith("c:"):
            return await self.craft_callback(update, context, data)
        chat_id, user = update.effective_chat.id, update.effective_user
        state = await self.state(chat_id)
        if not state:
            raise InvalidAction("These controls have expired. Use /venture.")
        if state.get("events"):
            await self.flush_events(chat_id, state)
            raise InvalidAction("Saved progress was recovered. Use /status for fresh controls.")
        action, argument = game.validate_callback(state, data, user.id, update.effective_message.message_thread_id)
        if action == "mode":
            if argument not in {"gauntlet", "open_campaign", "hire_help", "dig_treasure"}:
                raise InvalidAction("Unknown game mode.")
            if argument in {"hire_help", "dig_treasure"}:
                await self.free_reward(update, context, state, argument)
                return
            state.update(game_mode=argument, gauntlet_level=1)
            if argument == "gauntlet":
                game.scout(state, self.rng)
            else:
                state["phase"] = "lobby"
        elif action == "route":
            if argument not in GAUNTLET_ROUTES:
                raise InvalidAction("Unknown route.")
            state["selected_route"] = argument
            if state["gauntlet_level"] == 1:
                state["phase"] = "lobby"
            else:
                game.enter_preparation(state)
        elif action == "faction":
            if argument not in FACTIONS:
                raise InvalidAction("Unknown faction.")
            rows = [[ui.button("Choose this faction", game.callback_data(state, "choose", argument))]]
            return await self.say(update, context, ui.faction_text(argument, ABILITIES[argument]), rows)
        elif action == "choose":
            if any(p["id"] == user.id for p in state["players"]):
                raise InvalidAction("You already joined. Leave the lobby first to change faction.")
            state["players"].append(game.make_player(user.id, user.first_name, argument, await self.profile(user)))
        elif action == "ready":
            player = next((p for p in state["players"] if p["id"] == user.id), None)
            if player is None:
                raise InvalidAction("Choose a faction before readying up.")
            player["ready"] = not player["ready"]
            if player["ready"]:
                player["ready_version"] = (await self.profile(user))["loadout_version"]
        elif action == "leave":
            state["players"] = [p for p in state["players"] if p["id"] != user.id]
        elif action == "start":
            if not state["players"] or not all(p["ready"] for p in state["players"]):
                raise InvalidAction("At least one player must join, and everyone must be ready.")
            loadouts = await self.participant_profiles(state)
            changed = [
                p
                for p in state["players"]
                if p.get("ready_version", p.get("loadout_version", 0)) != loadouts[p["id"]]["loadout_version"]
            ]
            if changed:
                for player in changed:
                    player["ready"] = False
                await self.commit(chat_id, state)
                await self.say(
                    update,
                    context,
                    "Loadouts changed: " + ", ".join(p["username"] for p in changed) + ". Please ready again.",
                )
                return await self.show_status(update, context, state)
            if state["game_mode"] == "gauntlet":
                await self.prepare_floor(state, loadouts)
            else:
                await self.prepare_campaign(state, loadouts)
        elif action == "rest":
            notice = game.rest(state, user.id)
            await self.commit(chat_id, state)
            await self.say(update, context, notice)
            return await self.show_status(update, context, state)
        elif action == "chapter":
            await self.advance_chapter(state, argument)
        elif action == "boss":
            return await self.say(update, context, self.boss_info(state))
        elif action in {"ability", "environment", "tactic", "ally"}:
            try:
                index = int(argument) if action == "ability" else None
            except ValueError as exc:
                raise InvalidAction("Invalid ability control.") from exc
            if action == "ally" and state["phase"] == "campaign":
                result = game.resolve_campaign_support(state, user.id)
            else:
                result = game.resolve_combat(
                    state,
                    user.id,
                    index,
                    action == "environment",
                    self.rng,
                    tactic="ally" if action == "ally" else argument if action == "tactic" else None,
                )
            await self.commit(chat_id, state)
            await self.send_result(update, context, result, state)
            return await self.show_status(update, context, state)
        elif action == "continue":
            if state["game_mode"] != "gauntlet":
                raise InvalidAction("This campaign is complete. Bank your reward.")
            state["gauntlet_level"] += 1
            game.scout(state, self.rng)
        elif action == "bank":
            await self.bank(chat_id, state)
            return await self.show_status(update, context, state)
        elif action == "reset":
            if state["phase"] in {"victory", "defeat"}:
                await self.bank(chat_id, state)
            state = game.new_state(user.id, state["thread_id"], state)
        elif action == "images":
            state["settings"]["images"] = not state["settings"]["images"]
        await self.commit(chat_id, state)
        await self.show_status(update, context, state)
        if action in {"start", "chapter"} and state["phase"] in {"combat", "campaign"}:
            self.schedule_scene(context.bot, chat_id, copy.deepcopy(state))

    async def participant_profiles(self, state):
        result = {}
        for player in state["players"]:
            profile = await self.repo.load_profile(player["id"])
            if profile is None:
                raise PersistenceError("Participant profile is missing")
            migrated = await self.repo.mutate_profile(player["id"], lambda p: profiles.normalize(p) and None)
            result[player["id"]] = migrated.profile
        return result

    async def prepare_floor(self, state, loadouts=None):
        loadouts = loadouts or await self.participant_profiles(state)
        for player in state["players"]:
            game.refresh_loadout(player, loadouts[player["id"]])
        game.start_floor(state, self.rng)
        design = await self.ai.encounter(state)
        source = "AI" if design else "fallback"
        design = design or content.fallback_encounter(state["boss"], self.rng)
        encounters.install_design(state, design.model_dump(), source)

    async def send_result(self, update, context, result, state=None):
        # The full turn is already persisted. Failed narration cannot roll back
        # HP, consume a second charge, or leave a durable processing lock.
        if state and state["phase"] in {"victory", "chapter_complete"} and state.get("last_recap"):
            recap = state["last_recap"]
            if not recap.get("story"):
                story = await self.ai.victory(recap)
                if story:
                    recap["story"] = story.text
                    await self.commit(update.effective_chat.id, state)
            return await self.say(update, context, result)
        narrative = await self.ai.narrate(result)
        await self.say(update, context, result + ("\n\n" + narrative.text if narrative else ""))

    async def prepare_campaign(self, state, loadouts=None):
        from locations import LOCATIONS

        loadouts = loadouts or await self.participant_profiles(state)
        for player in state["players"]:
            game.refresh_loadout(player, loadouts[player["id"]])
        if state.get("campaign"):
            game.start_chapter(state)
            return
        location = copy.deepcopy(self.rng.choice(LOCATIONS))
        plan = await self.ai.campaign([p["faction"] for p in state["players"]], location, state["run_id"])
        source = "AI" if plan else "fallback"
        plan = plan or content.fallback_campaign(location, self.rng)
        state.update(
            phase="chapter_briefing",
            location=location,
            boss=None,
            campaign={**plan.model_dump(), "source": source, "index": 0, "completed": []},
        )

    async def advance_chapter(self, state, argument):
        campaign = state["campaign"]
        initial = state["phase"] == "chapter_briefing"
        target = campaign["index"] if initial else campaign["index"] + 1
        try:
            choice = int(argument)
            approaches = campaign["chapters"][target]["approaches"]
            if not 0 <= choice < len(approaches):
                raise ValueError
        except (ValueError, IndexError) as exc:
            raise InvalidAction("Invalid chapter approach. Use /campaign.") from exc
        approach = copy.deepcopy(approaches[choice])
        if not initial:
            chapter = await self.ai.chapter(state, approach)
            if chapter:
                campaign["chapters"][target] = chapter.model_dump()
            campaign["chapter_source"] = "AI" if chapter else "saved outline fallback"
            campaign["index"] = target
        state["chapter_approach"] = approach
        game.enter_preparation(state)

    async def bank(self, chat_id, state):
        preparing_gauntlet = (
            state["phase"] == "preparation"
            and state["game_mode"] == "gauntlet"
            and state["gauntlet_bonus_defeated"] > 0
        )
        if state["phase"] not in {"victory", "defeat"} and not preparing_gauntlet:
            raise InvalidAction("No completed run is ready to bank.")
        seen = set()
        for player in state["players"] + state["dead_players"]:
            if player["id"] not in seen:
                game.queue_reward(state, player)
                seen.add(player["id"])
        state["phase"] = "rewards"
        await self.commit(chat_id, state)

    async def show_status(self, update, context, state, recover=False):
        if update.effective_message.message_thread_id != state["thread_id"]:
            raise InvalidAction("This game is in another topic. Return to its original topic to resume.")
        rows, lines = [], []

        def control(label, action, arg=""):
            return ui.button(label, game.callback_data(state, action, arg))

        phase = state["phase"]
        if phase == "menu":
            lines = [
                "Alpha City — choose your next venture.",
                "The player who opened this menu controls the game mode.",
            ]
            rows = [
                [control("Gauntlet", "mode", "gauntlet"), control("Open Campaign", "mode", "open_campaign")],
                [
                    control("Hire Help (ally)", "mode", "hire_help"),
                    control("Dig for Treasure", "mode", "dig_treasure"),
                ],
            ]
        elif phase == "scout":
            lines = [f"Scouting — Floor {state['gauntlet_level']}"]
            lines += [f"{name}: {weight}%" for name, weight in state["scout"]["odds"]]
            lines.append(state["scout"]["hazard"]["label"])
            lines += [f"{route['name']}: {route['blurb']}" for route in GAUNTLET_ROUTES.values()]
            rows = [[control(route["name"], "route", key)] for key, route in GAUNTLET_ROUTES.items()]
            lines.append("Owner chooses the route. Equipped items refresh before the next floor.")
        elif phase == "lobby":
            lines = ["Party lobby — choose a faction to preview its abilities."]
            lines += [
                f"{'Ready' if p['ready'] else 'Not ready'}: {p['username']} · {p['faction']}" for p in state["players"]
            ]
            lines.append("Everyone chooses Ready; the owner then starts. Solo play works the same way.")
            lines.append(
                "Prepare with /inventory, /allies, and /progression. Changes after Ready require readying again."
            )
            factions = list(FACTIONS)
            rows = [[control(f, "faction", f) for f in factions[i : i + 2]] for i in range(0, len(factions), 2)]
            rows += [
                [control("Ready / Not ready", "ready"), control("Leave lobby", "leave")],
                [control("Start (owner)", "start")],
            ]
        elif phase == "preparation":
            title = (
                f"Floor {state['gauntlet_level']}"
                if state["game_mode"] == "gauntlet"
                else f"Chapter {state['campaign']['index'] + 1}: {state['campaign']['chapters'][state['campaign']['index']]['title']}"
            )
            lines = [
                f"Preparation — {title}",
                "Living players may rest once for up to 25% max HP, equip/craft, deploy an ally, and choose talents. Everyone readies again before the owner starts.",
                "/inventory · /craft · /allies · /progression",
            ]
            if state.get("chapter_approach") and state["game_mode"] == "open_campaign":
                lines.append("Chapter design: " + state["campaign"].get("chapter_source", state["campaign"]["source"]))
                lines.append(
                    "Approach: " + state["chapter_approach"]["label"] + " — " + state["chapter_approach"]["detail"]
                )
                lines.append(state["campaign"]["chapters"][state["campaign"]["index"]]["objective"])
            lines += [
                f"{'Ready' if p['ready'] else 'Not ready'}: {p['username']} · {ui.bar(p['hp'], p['max_hp'])}"
                + (" · rested" if p["id"] in state["camp_rest"] else "")
                for p in state["players"]
            ]
            rows = [
                [control("Rest once", "rest"), control("Ready / Not ready", "ready")],
                [control("Start (owner)", "start")],
            ]
            if state["game_mode"] == "gauntlet":
                rows.append([control("Bank instead (owner)", "bank")])
        elif phase in {"chapter_briefing", "chapter_complete"}:
            campaign = state["campaign"]
            target = campaign["index"] if phase == "chapter_briefing" else campaign["index"] + 1
            chapter = campaign["chapters"][target]
            lines = [
                f"{campaign['title']} · {campaign['source']} design",
                campaign["premise"],
                f"Saved chapters: {len(campaign['completed'])}/{len(campaign['chapters'])}",
                f"Next: Chapter {target + 1} — {chapter['title']}",
                chapter["objective"],
                "Owner chooses an approach, then the party prepares and readies.",
            ]
            if phase == "chapter_complete":
                lines.insert(0, ui.recap_text(state.get("last_recap")))
            for index, approach in enumerate(chapter["approaches"]):
                lines.append(f"{approach['label']} — {approach['detail']}")
                rows.append([control(approach["label"], "chapter", str(index))])
        elif phase in {"combat", "campaign"}:
            lines = [
                f"Floor {state['gauntlet_level']}" if phase == "combat" else "Open Campaign",
                state.get("objective", ""),
            ]
            lines.append(f"Location: {state.get('location', {}).get('name', 'Alpha City')}")
            if phase == "combat":
                boss = state["boss"]
                lines.append(f"{boss['name']}: {ui.bar(boss['hp'], boss['max_hp'])}")
                route = GAUNTLET_ROUTES.get(state.get("selected_route"), GAUNTLET_ROUTES["default"])
                lines.append(f"Route: {route['name']} · {state.get('hazard_effect', {}).get('label', '')}")
                lines.append(encounters.intent_text(state))
                lines.append(f"Encounter design: {boss.get('design_source', 'legacy')}.")
            elif state.get("campaign"):
                campaign = state["campaign"]
                lines.append(f"{campaign['title']} · Chapter {campaign['index'] + 1}/{len(campaign['chapters'])}")
                lines.append("Chapter design: " + campaign.get("chapter_source", campaign["source"]))
                lines.append("Approach: " + (state.get("chapter_approach") or {}).get("label", "Original objective"))
            lines += [f"{p['username']}: {ui.bar(p['hp'], p['max_hp'])}" for p in state["players"]]
            if state["dead_players"]:
                lines.append("Fallen: " + ", ".join(p["username"] for p in state["dead_players"]))
            if state["players"]:
                actor = state["players"][state["turn_index"]]
                lines.append(
                    f"Turn: {actor['username']} — {'choose an ability' if phase == 'combat' else 'type your action'}."
                )
                bonus = state["active_roll_bonuses"].get(str(actor["id"]), 0)
                if bonus:
                    lines.append(f"Next eligible roll: {bonus:+} bonus points (10 points = 1 d10 step).")
                if phase == "combat":
                    rows = [
                        [
                            control(
                                f"{a['name']} · {ui.effect_text(a)} · {a.get('charges', '∞')} uses", "ability", str(i)
                            )
                        ]
                        for i, a in enumerate(actor["abilities"])
                        if a.get("charges", 1) > 0
                    ]
                    if game.environmental_available(state):
                        rows.append([control(state["location"]["interaction"]["name"], "environment")])
                    tactics = [control("Brace (half damage to you)", "tactic", "guard")]
                    if state["boss"].get("intent"):
                        tactics.append(
                            control("Counter " + state["boss"]["intent"]["counter_category"], "tactic", "counter")
                        )
                    rows.append(tactics)
                    rows.append([control("Boss info", "boss")])
                ally = actor.get("ally")
                if ally:
                    lines.append(f"Deployed ally: {ally['name']} · {ally['charges']} support uses left.")
                    if ally["charges"] > 0:
                        rows.append([control("Ally: " + ally["support"]["name"] + " (uses turn)", "ally")])
            if phase == "campaign" and state.get("narrative_log"):
                lines.append(state["narrative_log"][-1])
        elif phase in {"victory", "defeat"}:
            lines = [
                "Victory!" if phase == "victory" else "The party has fallen.",
                f"Bank bonus: +{game.run_bonus(state)} roll points (+1 per attempt and defeat, capped at 60).",
            ]
            if state.get("last_recap"):
                lines.append(ui.recap_text(state["last_recap"]))
            if phase == "victory":
                lines.append("Completion XP/materials and deployed ally bond are saved. See /progression and /craft.")
            odds = game.reward_odds(20, game.run_bonus(state))
            lines.append("Reward odds: " + " · ".join(f"{name} {chance:.1%}" for name, chance in odds.items()))
            if phase == "victory" and state["game_mode"] == "gauntlet":
                rows.append([control(f"Ascend to floor {state['gauntlet_level'] + 1}", "continue")])
            rows += [[control("Bank rewards (owner)", "bank")], [control("Bank & return to menu", "reset")]]
        elif phase == "rewards":
            lines = [
                "Rewards banked for each participant.",
                "Each player can use /rewards to choose their own reward, now or later.",
            ]
            rows = [[control("New venture (owner)", "reset")]]
        if phase in {"menu", "scout", "lobby", "preparation"}:
            rows.append([control(f"Images: {'on' if state['settings']['images'] else 'off'} (owner)", "images")])
        if recover and state.get("last_result"):
            await self.say(update, context, "Last saved action:\n" + state["last_result"])
        key = (update.effective_chat.id, state["run_id"])
        previous = None if recover else self.panels.get(key)
        message = await ui.panel(
            context.bot, update.effective_chat.id, state["thread_id"], "\n\n".join(lines), rows, previous
        )
        if message:
            # Keep only the active run's panel for each chat.
            for old in [k for k in self.panels if k[0] == key[0] and k != key]:
                self.panels.pop(old, None)
            self.panels[key] = message.message_id

    @staticmethod
    def boss_info(state):
        boss = state["boss"]
        lines = [boss["name"], boss.get("description", ""), ui.bar(boss["hp"], boss["max_hp"])]
        for title, key in [("Resistance", "strengths"), ("Vulnerability", "weaknesses")]:
            for effect in boss.get(key, []):
                lines.append(
                    f"{title}: {effect.get('damage_type', effect.get('category', effect.get('faction', '')))} ×{effect['value']:g}"
                )
        lines.append(encounters.intent_text(state))
        if boss.get("design"):
            design = boss["design"]
            lines.append(
                f"At {design['phase_threshold']}% HP or below, phase {design['phase_name']} adds "
                f"{design['phase_power_bonus']} power to subsequent intents (cap 6 party / 10 single). The current intent is unchanged."
            )
        return "\n".join(lines)

    async def free_reward(self, update, context, state, mode):
        user = update.effective_user
        event_id = f"free:{state['run_id']}:{state['_revision']}:{user.id}"
        reward = {
            "id": profiles.stable_id(event_id),
            "source": "Free roll",
            "minimum": 1,
            "bonus": 0,
            "types": ["item" if mode == "dig_treasure" else "character"],
        }
        timestamp = dt.datetime.now(dt.timezone.utc).timestamp()

        def reserve(profile):
            profiles.normalize(profile, user.first_name)
            wait = self.free_roll_cooldown - (timestamp - profile.get("last_free_roll", 0))
            if wait > 0:
                raise InvalidAction(
                    f"Please wait {int(wait) + 1} seconds before another free roll. Pending rewards remain in /rewards."
                )
            profile["last_free_roll"] = timestamp
            profile["pending_rewards"][reward["id"]] = reward
            return reward

        saved = await self.repo.mutate_profile(user.id, reserve, event_id)
        await self.commit(update.effective_chat.id, state)
        await self.claim(update, context, saved.result["id"], saved.result["types"][0])
        await self.show_status(update, context, state)

    async def show_rewards(self, update, context, profile):
        uid = update.effective_user.id
        rewards = list(profile["pending_rewards"].values())
        lines = [f"Pending rewards: {len(rewards)}. Each claim is saved once; interrupted delivery is recoverable."]
        rows = []
        for reward in rewards[:10]:
            lines.append(f"{reward['source']} · +{reward['bonus']} roll points")
            rows.append(
                [
                    ui.button(
                        f"{reward['source']}: {'Treasure' if kind == 'item' else 'Character'}",
                        f"r:{uid}:{reward['id']}:{kind}",
                    )
                    for kind in reward["types"]
                ]
            )
        if len(rewards) > 10:
            lines.append("Showing the first 10. Claim these and reopen /rewards for the rest.")
        for receipt in await self.repo.recent_rewards(uid):
            item = receipt["reward"]
            rows.append([ui.button(f"Resend receipt: {item['name'][:40]}", f"r:{uid}:{item['id']}:retry")])
        await self.say(update, context, "\n".join(lines), rows)

    def inventory_view(self, update, previous=None):
        view = dict(previous or {"slot": "all", "rarity": "all", "page": 0})
        view.update(
            nonce=uuid.uuid4().hex[:8],
            chat_id=update.effective_chat.id,
            thread_id=update.effective_message.message_thread_id,
        )
        uid = update.effective_user.id
        self.inventory_views[uid] = view
        self.inventory_views.move_to_end(uid)
        while len(self.inventory_views) > 2000:
            self.inventory_views.popitem(last=False)
        return view

    async def show_inventory(self, update, context, profile, previous=None, notice=""):
        view = self.inventory_view(update, previous)
        view.pop("confirm_id", None)
        uid = update.effective_user.id

        def control(label, action, arg=""):
            return ui.button(label, f"i:{uid}:{view['nonce']}:{action}:{arg}")

        items, page, pages = profiles.inventory_page(profile, view["page"], view["slot"], view["rarity"])
        view["page"] = page
        lines = [
            notice,
            f"{profile['username']}'s inventory",
            "Equipped — applies at the next floor or chapter:",
        ]
        rows = []
        for slot, item in profile["equipped_items"].items():
            lines.append(f"{slot}: {item['name'] if item else 'Empty'}")
            if item:
                rows.append([control(f"Unequip {slot}", "unequip", item["id"])])
        lines += [
            f"Backpack: {len(profile['inventory'])} total · Page {page + 1}/{pages}",
            f"Filters: {view['slot']} slots / {view['rarity']} rarity",
        ]
        rows += [[control(item["name"][:45] + " · " + item.get("rarity", ""), "view", item["id"])] for item in items]
        if not items:
            lines.append("No items match these filters.")
        rows.append(
            [control("Slot filter", "slot"), control("Rarity filter", "rarity"), control("Clear filters", "clear")]
        )
        rows.append([control("Previous", "page", str(page - 1)), control("Next", "page", str(page + 1))])
        message_id = update.callback_query.message.message_id if update.callback_query else None
        await ui.panel(
            context.bot,
            update.effective_chat.id,
            update.effective_message.message_thread_id,
            "\n".join(line for line in lines if line),
            rows,
            message_id,
        )

    async def inventory_callback(self, update, context, data):
        try:
            _, owner, nonce, action, argument = data.split(":", 4)
        except ValueError as exc:
            raise InvalidAction("Invalid inventory control. Use /inventory.") from exc
        user = update.effective_user
        view = self.inventory_views.get(user.id)
        if owner != str(user.id):
            raise InvalidAction("This inventory belongs to another player. Open your own /inventory.")
        if (
            not view
            or view["nonce"] != nonce
            or view["chat_id"] != update.effective_chat.id
            or view["thread_id"] != update.effective_message.message_thread_id
        ):
            raise InvalidAction("This inventory view expired. Open /inventory again.")
        profile = await self.profile(user)
        if action == "forge":
            return await self.request_blueprint(update, context, argument)
        if action == "salvage":
            if view.get("confirm_id") != argument or view.get("confirm_action") != "salvage":
                raise InvalidAction("Confirm the exact item and salvage yield first.")
            changed = await self.repo.mutate_profile(
                user.id, lambda p: profiles.salvage_item(p, argument), f"salvage:{nonce}:{argument}"
            )
            return await self.show_inventory(
                update,
                context,
                changed.profile,
                view,
                f"Salvaged {changed.result['name']}: +{changed.result['materials']} materials. Balance {changed.result['balance']}.",
            )
        if action in {"equip", "unequip", "discard"}:
            if action == "discard" and view.get("confirm_id") != argument:
                raise InvalidAction("Please confirm the exact item before discarding it.")
            if action == "discard" and view.get("confirm_action") != "discard":
                raise InvalidAction("Use the discard confirmation for this item.")
            changed = await self.repo.mutate_profile(
                user.id,
                lambda p: profiles.inventory_action(p, action, argument),
                f"inventory:{nonce}:{action}:{argument}",
            )
            return await self.show_inventory(
                update, context, changed.profile, view, f"{action.capitalize()}: {changed.result}."
            )
        if action in {"view", "confirm", "scrapask"}:
            item = next((i for i in profile["inventory"] if i["id"] == argument), None)
            if item is None:
                raise InvalidAction("This item is no longer in the backpack. Use /inventory.")
            view = self.inventory_view(update, view)

            def control(label, act, arg=""):
                return ui.button(label, f"i:{user.id}:{view['nonce']}:{act}:{arg}")

            lines = [ui.item_text(item), item.get("background", "")]
            equipped = profile["equipped_items"].get(item["slot"])
            lines.append("Currently equipped:\n" + (ui.item_text(equipped) if equipped else "Empty slot"))
            if action in {"confirm", "scrapask"}:
                view["confirm_id"] = argument
                operation = "salvage" if action == "scrapask" else "discard"
                view["confirm_action"] = operation
                lines.append(
                    f"Salvage this exact item for {profiles.salvage_value(item)} materials? The item will be consumed."
                    if operation == "salvage"
                    else "Discard this exact item permanently? This cannot be undone."
                )
                rows = [[control("Confirm " + operation, operation, argument)], [control("Cancel", "view", argument)]]
            else:
                view.pop("confirm_id", None)
                view.pop("confirm_action", None)
                rows = [
                    [control("Equip", "equip", argument), control("Discard…", "confirm", argument)],
                    [control("Salvage…", "scrapask", argument), control("Upgrade blueprint", "forge", argument)],
                    [control("Back to inventory", "back")],
                ]
            return await ui.panel(
                context.bot,
                update.effective_chat.id,
                update.effective_message.message_thread_id,
                "\n\n".join(lines),
                rows,
                update.callback_query.message.message_id,
            )
        if action == "page":
            try:
                view["page"] = max(0, int(argument))
            except ValueError as exc:
                raise InvalidAction("Invalid page.") from exc
        elif action in {"slot", "rarity"}:
            choices = ["all"] + (ITEM_SLOTS if action == "slot" else RARITY_ORDER)
            view[action] = choices[(choices.index(view[action]) + 1) % len(choices)]
            view["page"] = 0
        elif action == "clear":
            view.update(page=0, slot="all", rarity="all")
        elif action != "back":
            raise InvalidAction("Unknown inventory action.")
        await self.show_inventory(update, context, profile, view)

    async def claim_callback(self, update, context, data):
        try:
            _, owner, reward_id, kind = data.split(":")
        except ValueError as exc:
            raise InvalidAction("Invalid reward control. Use /rewards.") from exc
        if owner != str(update.effective_user.id):
            raise InvalidAction("These rewards belong to another player. Open your own /rewards.")
        if kind not in {"item", "character", "retry"} or len(reward_id) != 16:
            raise InvalidAction("Invalid reward control. Use /rewards.")
        await self.claim(update, context, reward_id, kind)

    async def claim(self, update, context, reward_id, kind):
        user = update.effective_user
        if kind == "retry":

            def missing(_):
                raise InvalidAction("No completed receipt exists. Use /rewards to finish the claim.")

            receipt = await self.repo.mutate_profile(user.id, missing, f"claim:{reward_id}")
            return await self.deliver_reward(update, context, receipt.result, images=False, replay=True)

        def reserve(profile):
            profiles.normalize(profile, user.first_name)
            entitlement = profile["pending_rewards"].get(reward_id)
            if entitlement is None or kind not in entitlement["types"]:
                raise InvalidAction("This claim is no longer pending. Use /rewards to resend its receipt.")
            roll = game.reward_roll(self.rng, entitlement["minimum"], entitlement["bonus"])
            if kind == "item":
                slot, specialty = self.rng.choice(ITEM_SLOTS), self.rng.choice(ITEM_SPECIALTIES)
                payload = profiles.make_item(
                    f"{roll['rarity']} {specialty} {slot}",
                    slot,
                    specialty,
                    roll["rarity"],
                    "Recovered from Alpha City's hidden caches.",
                    self.rng.randint(1, 3),
                )
            else:
                faction = self.rng.choice(list(FACTIONS))
                payload = {
                    "kind": "character",
                    "name": f"{faction} Contact",
                    "faction": faction,
                    "background": "A new contact from the streets of Alpha City.",
                }
                payload.update(support=content.fallback_support(payload), bond=0, design_source="fallback")
            payload.update(roll, id=reward_id)
            return payload

        reserved = await self.repo.mutate_profile(user.id, reserve, f"reserve:{reward_id}")
        if reward_id not in reserved.profile.get("pending_rewards", {}):
            return await self.claim(update, context, reward_id, "retry")
        payload = copy.deepcopy(reserved.result)
        await self.say(
            update, context, "Reward reserved. Preparing your collectible; it remains recoverable through /rewards."
        )
        if payload["kind"] == "character":
            design = await self.ai.ally(payload)
            if design:
                payload.update(design.model_dump(), design_source="AI")
        else:
            flavor = await self.ai.flavor(payload["kind"], payload)
            if flavor:
                payload.update(name=flavor.name, background=flavor.background)
        receipt = await self.repo.mutate_profile(
            user.id, lambda p: profiles.grant_reward(p, reward_id, payload), f"claim:{reward_id}"
        )
        state = await self.repo.load_state(update.effective_chat.id)
        images = state.get("settings", {}).get("images", self.default_images)
        await self.deliver_reward(
            update, context, receipt.result, images=images and receipt.applied, replay=not receipt.applied
        )

    async def deliver_reward(self, update, context, receipt, images=False, replay=False):
        reward = receipt["reward"]
        if reward["kind"] == "item":
            detail = (
                ui.item_text(reward)
                + "\nSaved in /inventory. Equipment applies at the next floor or chapter.\n"
                + reward["background"]
            )
        else:
            ally = copy.deepcopy(reward)
            ally.setdefault("support", content.fallback_support(ally))
            detail = ui.ally_text(ally) + "\nSaved in /allies. Deploy before the next encounter/chapter."
        text = (
            f"{'Saved reward receipt' if replay else 'Reward claimed'}\n"
            f"Roll {reward['base_roll']} + {reward['bonus']} points = {reward['roll']} → {reward['rarity']}\n\n"
            f"{detail}\n\n{receipt['xp']} XP awarded once. Level {receipt['level']}."
        )
        image = await self.ai.image(f"{reward['name']}. {reward['background']}") if images else None
        await ui.reward_card(
            context.bot, update.effective_chat.id, update.effective_message.message_thread_id, text, image
        )

    def schedule_scene(self, bot, chat_id, snapshot):
        if self.closing or not snapshot["settings"]["images"] or len(self.tasks) >= 8:
            return

        async def deliver():
            try:
                image = await self.ai.image(
                    f"{snapshot.get('boss', {}).get('name', '') if snapshot.get('boss') else snapshot['objective']}. {snapshot['location']['description']}"
                )
                if image is None:
                    return
                current = await self.repo.load_state(chat_id)
                if (
                    current.get("run_id") != snapshot["run_id"]
                    or current.get("gauntlet_level") != snapshot["gauntlet_level"]
                    or current.get("campaign", {}).get("index") != snapshot.get("campaign", {}).get("index")
                    or current.get("phase") not in {"combat", "campaign"}
                ):
                    return
                await ui.reward_card(
                    bot,
                    chat_id,
                    snapshot["thread_id"],
                    f"{snapshot['location']['name']}\n{snapshot['objective']}",
                    image,
                )
            except Exception as exc:
                logger.warning("Optional scene delivery skipped: %s", type(exc).__name__)

        task = asyncio.create_task(deliver())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        self.closing = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.ai.close()
        await self.repo.close()
