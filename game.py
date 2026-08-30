"""Deterministic session rules. No I/O, provider clients, or Telegram objects."""

from __future__ import annotations

import copy
import datetime as dt
import random
import re
import uuid

import encounters
import profiles
from abilities import ABILITIES
from bosstraits import BOSS_TRAITS
from game_constants import FACTIONS, HAZARDS, RUN_BONUS_CAP, XP_FOR_ATTEMPT, XP_FOR_DEFEAT_BASE, XP_FOR_MILESTONE
from item_traits import calculate_equipped_damage_bonus, get_abilities_from_equipped_items
from locations import LOCATIONS
from profiles import InvalidAction, stable_id


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def new_state(owner_id, thread_id, previous=None):
    state = {
        "schema_version": 3,
        "run_id": uuid.uuid4().hex[:12],
        "phase": "menu",
        "owner_id": owner_id,
        "thread_id": thread_id,
        "players": [],
        "dead_players": [],
        "turn_index": 0,
        "turn_id": 0,
        "gauntlet_level": 0,
        "game_mode": None,
        "boss": None,
        "location": {},
        "narrative_log": [],
        "events": [],
        "gauntlet_bonus_attempted": 0,
        "gauntlet_bonus_defeated": 0,
        "active_roll_bonuses": {},
        "guaranteed_success": {},
        "settings": {"images": True},
        "winning_streak": 0,
        "combat_stats": {},
        "last_action_timestamp": now(),
    }
    if previous:
        if "_revision" in previous:
            state["_revision"] = previous["_revision"]
        state["settings"] = copy.deepcopy(previous.get("settings", state["settings"]))
        state["events"] = copy.deepcopy(previous.get("events", []))
        if previous.get("last_recap"):
            state["last_recap"] = copy.deepcopy(previous["last_recap"])
    return state


def migrate_state(state: dict):
    if not state or state.get("schema_version") == 3:
        return False
    if state.get("schema_version") == 2:
        upgrade_gameplay(state)
        return True
    phase = {
        "MAIN_MENU": "menu",
        "FACTION_SELECT": "lobby",
        "SCOUTING": "scout",
        "GAUNTLET": "combat",
        "VICTORY": "victory",
        "LEVEL_1": "campaign",
    }.get(state.get("game_stage"))
    if phase is None:
        raise InvalidAction("This saved session needs inspection by the bot maintainer. No progress was overwritten.")
    state.update(
        schema_version=2, run_id=uuid.uuid4().hex[:12], phase=phase, turn_id=0, events=[], settings={"images": True}
    )
    state.pop("is_processing_turn", None)
    state.setdefault("active_roll_bonuses", {})
    state.setdefault("guaranteed_success", {})
    for field in ("location", "hazard_effect"):
        state[field] = state.get(field) or {}
    if not state.get("owner_id") and state.get("players"):
        state["owner_id"] = state["players"][0]["id"]
    for player in state.get("players", []):
        player.setdefault("ready", False)
    if phase == "victory" and not state.get("players"):
        state["phase"] = "defeat"
    upgrade_gameplay(state)
    return True


def upgrade_gameplay(state):
    state["schema_version"] = 3
    state.setdefault("winning_streak", state.get("gauntlet_bonus_defeated", 0))
    state.setdefault("combat_stats", {})
    if state.get("phase") in {"combat", "campaign"}:
        state["stats_partial"] = True
        for player in state["players"]:
            encounters.contribution(state, player)
    if state.get("game_mode") == "open_campaign" and not state.get("campaign"):
        state["campaign"] = {
            "title": "Legacy campaign",
            "premise": state.get("objective") or "Finish the saved objective.",
            "index": 0,
            "completed": [],
            "source": "legacy",
            "chapters": [
                {
                    "title": "Original objective",
                    "objective": state.get("objective") or "Finish the saved objective.",
                    "opening_scene": "Resume the original adventure.",
                    "approaches": [],
                }
            ],
        }


def callback_data(state, action, argument=""):
    return f"g:{state['run_id']}:{state['_revision']}:{action}:{argument}"


def validate_callback(state, data, user_id, thread_id):
    try:
        prefix, run_id, revision, action, argument = data.split(":", 4)
        valid = prefix == "g" and run_id == state.get("run_id") and int(revision) == state.get("_revision")
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise InvalidAction("These controls have expired. Use /status for the current game.")
    if thread_id != state.get("thread_id"):
        raise InvalidAction("This game is in a different topic. Return to its original topic.")
    phases = {
        "mode": {"menu"},
        "route": {"scout"},
        "faction": {"lobby"},
        "choose": {"lobby"},
        "ready": {"lobby", "preparation"},
        "leave": {"lobby"},
        "start": {"lobby", "preparation"},
        "rest": {"preparation"},
        "tactic": {"combat"},
        "ally": {"combat", "campaign"},
        "chapter": {"chapter_complete", "chapter_briefing"},
        "ability": {"combat"},
        "environment": {"combat"},
        "boss": {"combat"},
        "continue": {"victory"},
        "bank": {"victory", "defeat", "preparation"},
        "reset": {"victory", "defeat", "rewards"},
        "images": {"menu", "lobby", "scout", "preparation"},
    }
    if action not in phases or state.get("phase") not in phases[action]:
        raise InvalidAction("That action is not available now. Use /status.")
    if action in {"mode", "route", "start", "continue", "bank", "reset", "images", "chapter"}:
        require_owner(state, user_id)
    if action in {"ability", "environment", "tactic", "ally"}:
        require_actor(state, user_id)
    return action, argument


def require_owner(state, user_id):
    if state.get("owner_id") != user_id:
        raise InvalidAction("Only the game owner can make this choice.")


def require_actor(state, user_id):
    players = state.get("players", [])
    if not players or players[state.get("turn_index", 0) % len(players)]["id"] != user_id:
        raise InvalidAction("It is not your turn. Use /status to see who acts next.")


def make_player(user_id, username, faction, profile):
    if faction not in FACTIONS:
        raise InvalidAction("Unknown faction.")
    data = FACTIONS[faction]
    player = {
        "id": user_id,
        "username": username,
        "faction": faction,
        "hp": data["hp"],
        "max_hp": data["hp"],
        "modifier_type": data["modifier_type"],
        "modifier_value": data["modifier_value"],
        "ready": False,
    }
    refresh_loadout(player, profile)
    return player


def refresh_loadout(player, profile):
    bonuses = profiles.talent_bonuses(profile)
    maximum = FACTIONS[player["faction"]]["hp"] + bonuses["hp"]
    player["hp"] = max(0, min(maximum, player["hp"] + maximum - player["max_hp"]))
    player["max_hp"] = maximum
    player["talent_bonuses"] = bonuses
    player["ally"] = profiles.ally_snapshot(profile)
    player["loadout_version"] = profile.get("loadout_version", 0)
    player["equipped_items"] = copy.deepcopy(profile.get("equipped_items", {}))
    player["abilities"] = copy.deepcopy(ABILITIES[player["faction"]]) + get_abilities_from_equipped_items(
        player["equipped_items"], reset_charges=True
    )


def run_bonus(state):
    return min(
        RUN_BONUS_CAP,
        max(0, state.get("gauntlet_bonus_attempted", 0)) + max(0, state.get("gauntlet_bonus_defeated", 0)),
    )


def rarity(roll):
    for upper, name, lo, hi in [
        (35, "Salvage", 1, 4),
        (60, "Gutter-Tech", 5, 8),
        (80, "Street Mod", 9, 12),
        (94, "Black Market", 13, 16),
        (99, "Node-Forged", 17, 19),
        (100, "Peerless", 20, 20),
    ]:
        if roll <= upper:
            return name, (lo, hi)
    raise InvalidAction("Invalid reward roll.")


def reward_roll(rng, minimum, bonus):
    base = rng.randint(minimum, 100)
    final = min(100, base + bonus)
    name, levels = rarity(final)
    return {"base_roll": base, "roll": final, "bonus": bonus, "rarity": name, "level": rng.randint(*levels)}


def reward_odds(minimum, bonus):
    counts = {}
    for roll in range(minimum, 101):
        name, _ = rarity(min(100, roll + bonus))
        counts[name] = counts.get(name, 0) + 1
    return {name: count / (101 - minimum) for name, count in counts.items()}


def queue_event(state, player, key, xp=0, stats=None, materials=0, ally_id=None):
    event_id = f"{state['run_id']}:{key}:{player['id']}"
    if not any(e["id"] == event_id for e in state["events"]):
        state["events"].append(
            {
                "id": event_id,
                "user_id": player["id"],
                "username": player["username"],
                "kind": "progress",
                "xp": xp,
                "stats": stats or {},
                "materials": materials,
                "ally_id": ally_id,
            }
        )


def queue_reward(state, player, key="bank", minimum=20, types=None):
    event_id = f"{state['run_id']}:{key}:{player['id']}"
    reward = {
        "id": stable_id(event_id),
        "source": "Gauntlet" if state.get("game_mode") == "gauntlet" else "Alpha City",
        "minimum": minimum,
        "bonus": run_bonus(state),
        "types": types or ["item", "character"],
    }
    state["events"].append(
        {
            "id": event_id,
            "kind": "entitlement",
            "user_id": player["id"],
            "username": player["username"],
            "reward": reward,
        }
    )
    return reward


def scout(state, rng=random):
    keys = rng.sample(list(BOSS_TRAITS), 3)
    state.update(phase="scout", scout={"odds": list(zip(keys, [60, 30, 10])), "hazard": rng.choice(HAZARDS)})


def start_floor(state, rng=random):
    if not state["players"]:
        raise InvalidAction("At least one player must join.")
    floor = state["gauntlet_level"]
    odds = state.get("scout", {}).get("odds", [(name, 1) for name in BOSS_TRAITS])
    archetype = rng.choices([x[0] for x in odds], weights=[x[1] for x in odds])[0]
    boss = copy.deepcopy(BOSS_TRAITS[archetype])
    boss.update(name=archetype, archetype=archetype, hp=int(40 * 1.3 ** (floor - 1) * len(state["players"])))
    boss["max_hp"] = boss["hp"]
    state.update(
        phase="combat",
        boss=boss,
        location=copy.deepcopy(rng.choice(LOCATIONS)),
        hazard_effect=copy.deepcopy(state.get("scout", {}).get("hazard", rng.choice(HAZARDS))),
        turn_index=0,
        location_interaction_used=False,
        active_roll_bonuses={},
        guaranteed_success={},
        last_result="",
    )
    state["hazard_effect"]["type"] = "modifier"
    state["objective"] = f"Defeat {archetype}"
    state["gauntlet_bonus_attempted"] += 1
    for player in state["players"]:
        queue_event(state, player, f"floor:{floor}:attempt", XP_FOR_ATTEMPT, {"bosses_attempted": 1})
    encounters.begin(state)


def enter_preparation(state):
    state["phase"] = "preparation"
    state["camp_rest"] = []
    for player in state["players"]:
        player["ready"] = False


def rest(state, user_id):
    if state["phase"] != "preparation":
        raise InvalidAction("Rest is available between encounters only.")
    player = next((p for p in state["players"] if p["id"] == user_id), None)
    if not player or user_id in state["camp_rest"] or player["hp"] >= player["max_hp"]:
        raise InvalidAction("Each living player can recover up to 25% max HP once at this camp.")
    amount = heal(player, max(1, (player["max_hp"] + 3) // 4))
    state["camp_rest"].append(user_id)
    player["ready"] = False
    return f"{player['username']} rests and recovers {amount} HP. Ready again when prepared."


def start_chapter(state):
    campaign = state["campaign"]
    chapter = campaign["chapters"][campaign["index"]]
    state.update(
        phase="campaign",
        boss=None,
        turn_index=0,
        objective=chapter["objective"],
        last_result="",
        active_roll_bonuses={},
        guaranteed_success={},
    )
    state["narrative_log"] = (state.get("narrative_log", [])[-2:] + [chapter["opening_scene"]])[-4:]
    encounters.begin(state)


def canonical_category(value):
    return {"tech": "technology", "comm": "communication"}.get(value, value)


def guess_category(text):
    text = text.lower()
    if canonical_category(text) in {"technology", "communication", "strength", "stealth"}:
        return canonical_category(text)
    for pattern, category in [
        (r"hack|disable|tech|interface|override|breach", "technology"),
        (r"talk|persuade|intimidate|negotiate|bluff|rally", "communication"),
        (r"smash|break|force|strike|shoot|punch|kick", "strength"),
    ]:
        if re.search(pattern, text):
            return category
    return "stealth"


def location_bonus(state, player, category):
    bonus = 0
    for effect in [state.get("location", {}).get("effect", {}), state.get("hazard_effect", {})]:
        categories = effect.get("category", [])
        categories = categories if isinstance(categories, list) else [categories]
        if category not in categories:
            continue
        if effect.get("type") == "modifier":
            bonus += effect.get("value", 0)
        elif effect.get("type") == "faction_modifier":
            factions = effect["faction"] if isinstance(effect["faction"], list) else [effect["faction"]]
            factions = [{"Nodewalkers": "Nodewalker", "Chainbreakers": "Chainbreaker"}.get(f, f) for f in factions]
            if player["faction"] in factions:
                bonus += effect["value"]
    return bonus


def damage_to_boss(amount, state, player, damage_type, category):
    if amount > 0:
        amount += player.get("talent_bonuses", {}).get("damage", 0)
    multiplier = calculate_equipped_damage_bonus(player.get("equipped_items", {}), damage_type)
    for effect in state["boss"].get("strengths", []) + state["boss"].get("weaknesses", []):
        kind = effect["type"]
        applies = (
            (
                kind in {"damage_type_resistance", "damage_type_vulnerability"}
                and effect.get("damage_type") == damage_type
            )
            or (kind == "faction_damage_resistance" and effect.get("faction") == player["faction"])
            or (kind == "action_category_resistance" and effect.get("category") == category)
        )
        if applies:
            multiplier *= effect["value"]
    if state.get("selected_route") == "adrenal":
        multiplier *= 1.5
    return max(0, round(amount * multiplier))


def luck_multiplier(score):
    if score <= 1:
        return 0.0
    if score <= 4:
        return 0.75
    if score <= 5:
        return 1.0
    if score <= 9:
        return 1.25
    return 2.0


def heal(player, amount):
    before = player["hp"]
    player["hp"] = min(player["max_hp"], before + max(0, amount))
    return player["hp"] - before


def remove_dead(state, lines):
    known = {p["id"] for p in state["dead_players"]}
    living = []
    for player in state["players"]:
        player["hp"] = max(0, player["hp"])
        if player["hp"] > 0:
            living.append(player)
        elif player["id"] not in known:
            state["dead_players"].append(player)
            known.add(player["id"])
            lines.append(f"{player['username']} has fallen.")
    state["players"] = living
    if not living:
        state["phase"] = "defeat"
        state["winning_streak"] = 0
        encounters.recap(state, "Party defeated")


def win(state):
    if state["phase"] not in {"combat", "campaign"}:
        return
    if state["game_mode"] == "open_campaign" and state.get("campaign"):
        complete_chapter(state)
        return
    state["phase"] = "victory"
    if state["game_mode"] == "gauntlet":
        state["gauntlet_bonus_defeated"] += 1
        floor = state["gauntlet_level"]
        state["winning_streak"] = state.get("winning_streak", 0) + 1
        participants = [
            p for p in state["players"] + state["dead_players"] if str(p["id"]) in state.get("combat_stats", {})
        ] or state["players"]
        for player in participants:
            queue_event(
                state,
                player,
                f"floor:{floor}:victory",
                XP_FOR_DEFEAT_BASE * floor,
                {"bosses_defeated": 1, "highest_floor": floor},
                materials=2 + min(floor, 10),
                ally_id=(player.get("ally") or {}).get("id"),
            )
    encounters.recap(state, f"Defeated {state['boss']['name']}" if state.get("boss") else "Objective complete")


def complete_chapter(state):
    campaign = state["campaign"]
    index = campaign["index"]
    if any(c["index"] == index for c in campaign["completed"]):
        raise InvalidAction("This chapter is already complete. Use /status.")
    chapter = campaign["chapters"][index]
    campaign["completed"].append(
        {
            "index": index,
            "title": chapter["title"],
            "objective": chapter["objective"],
            "approach": state.get("chapter_approach"),
            "last_action": state.get("chapter_last_action", "")[:500],
        }
    )
    final = index == len(campaign["chapters"]) - 1
    state["phase"] = "victory" if final else "chapter_complete"
    participants = [p for p in state["players"] + state["dead_players"] if str(p["id"]) in state["combat_stats"]]
    for player in participants:
        stats = {"chapters_completed": 1}
        if final:
            stats["campaigns_completed"] = 1
        queue_event(
            state,
            player,
            f"chapter:{index}:complete",
            100,
            stats,
            materials=5,
            ally_id=(player.get("ally") or {}).get("id"),
        )
    encounters.recap(state, f"Chapter {index + 1} complete — {chapter['title']}")


def next_turn(state, actor_id, previous_order):
    players = state["players"]
    # Several players, including the actor, can fall in one party-wide hit.
    # Find the next survivor in the original order, not at an obsolete index.
    living = {p["id"]: i for i, p in enumerate(players)}
    start = previous_order.index(actor_id) + 1
    successors = previous_order[start:] + previous_order[:start]
    state["turn_index"] = next((living[uid] for uid in successors if uid in living), 0)
    state["turn_id"] += 1
    state["last_action_timestamp"] = now()


def environmental_available(state):
    boss = state.get("boss") or {}
    return (
        state.get("phase") == "combat"
        and boss.get("hp", 0) > 0
        and boss["hp"] <= boss.get("max_hp", 0) / 2
        and not state.get("location_interaction_used")
        and bool(state.get("location", {}).get("interaction"))
    )


def resolve_combat(state, user_id, ability_index=None, environment=False, rng=random, tactic=None):
    """Apply one complete turn locally, including retaliation and deaths."""
    if state.get("phase") != "combat" or state["boss"]["hp"] <= 0:
        raise InvalidAction("There is no active boss fight.")
    require_actor(state, user_id)
    previous_order = [p["id"] for p in state["players"]]
    player = state["players"][state["turn_index"]]
    lines = []
    guarded, countered, impact = False, False, False
    category = player["modifier_type"]
    if tactic in {"guard", "counter"}:
        if tactic == "counter" and not state["boss"].get("intent"):
            raise InvalidAction("This legacy boss has no published counter. Brace or use an ability.")
        effect, name = {"type": tactic}, "Brace" if tactic == "guard" else "Disrupt intent"
        if tactic == "counter":
            category = state["boss"]["intent"]["counter_category"]
    elif tactic == "ally":
        ally = player.get("ally")
        if not ally or ally["charges"] <= 0:
            raise InvalidAction(
                "No ally support charges remain. Deploy an ally through /allies before the next encounter."
            )
        skill = ally["support"]
        ally["charges"] -= 1
        category, name = skill["category"], f"{ally['name']}: {skill['name']}"
        effect = support_effect(skill)
    elif tactic is not None:
        raise InvalidAction("Unknown tactic.")
    elif environment:
        if not environmental_available(state):
            raise InvalidAction("The environment action is unavailable or already used.")
        interaction = state["location"]["interaction"]
        category = interaction["category"]
        effect = {"type": "environment"}
        name = interaction["name"]
        state["location_interaction_used"] = True
    else:
        if not isinstance(ability_index, int) or not 0 <= ability_index < len(player["abilities"]):
            raise InvalidAction("Unknown ability. Refresh /status.")
        ability = player["abilities"][ability_index]
        if ability.get("charges", 1) <= 0:
            raise InvalidAction("This ability has no charges left.")
        effect, name = ability["effect"], ability["name"]
        category = ability.get("category", category)
        if "charges" in ability:
            ability["charges"] -= 1
    bonuses = state["active_roll_bonuses"]
    encounters.record(state, player, turns=1, ability=name)
    eligible = effect["type"] not in {"roll_bonus", "guaranteed_success", "guard"}
    personal = bonuses.pop(str(user_id), 0) if eligible else 0
    raw_roll = rng.randint(1, 10) if tactic != "guard" else 5
    local = location_bonus(state, player, category) + player.get("talent_bonuses", {}).get("roll", 0)
    if tactic == "counter" and category == player["modifier_type"]:
        local += 10 * player["modifier_value"]
    adjusted_roll = min(10, max(1, raw_roll + (personal + local) / 10))
    guaranteed = state["guaranteed_success"].get(str(user_id))
    if eligible and guaranteed == category:
        adjusted_roll = 10
        del state["guaranteed_success"][str(user_id)]
    multiplier = luck_multiplier(adjusted_roll)
    if tactic == "guard":
        guarded = True
        lines.append(f"{player['username']} braces: incoming boss damage to you is halved this turn.")
    else:
        lines.append(
            f"{player['username']} — {name}\nRoll {raw_roll}/10; modifiers {personal + local:+} points → {adjusted_roll:g}/10 ({multiplier:g}×)"
        )
    if tactic == "counter":
        countered = adjusted_roll >= 6
        impact = countered
        if countered:
            encounters.record(state, player, support=1)
        lines.append(
            "Counter succeeds: the published move is halved."
            if countered
            else "Counter fails; the published move proceeds."
        )
    elif tactic == "guard":
        pass
    elif environment:
        if multiplier == 0:
            damage = interaction["failure_effect"]["value"]
            if state.get("selected_route") == "adrenal":
                damage = int(damage * 1.5)
            for target in state["players"]:
                target["hp"] -= damage
            lines.append(f"{interaction['failure_narrative']} Party takes {damage} damage.")
            remove_dead(state, lines)
        else:
            damage = round(interaction["success_effect"]["value"] * multiplier)
            if state.get("selected_route") == "adrenal":
                damage = int(damage * 1.5)
            actual = min(state["boss"]["hp"], damage)
            state["boss"]["hp"] -= actual
            encounters.record(state, player, damage=actual)
            impact = actual > 0
            lines.append(f"{interaction['success_narrative']} {actual} effective damage.")
    elif multiplier == 0:
        lines.append("The ability fails. Its charge is consumed.")
    elif effect["type"] == "direct_damage":
        damage = damage_to_boss(round(effect["value"] * multiplier), state, player, effect["damage_type"], category)
        actual = min(state["boss"]["hp"], damage)
        state["boss"]["hp"] -= actual
        encounters.record(state, player, damage=actual, ally_damage=actual if tactic == "ally" else 0)
        impact = actual > 0
        lines.append(f"{actual} effective {effect['damage_type']} damage after equipment and boss traits.")
    elif effect["type"] == "heal":
        amount = (round(effect["value"] * multiplier) + player.get("talent_bonuses", {}).get("heal", 0)) * (
            2 if state.get("selected_route") == "juiced_up" else 1
        )
        targets = state["players"] if effect.get("target") == "party" else [player]
        for target in targets:
            actual = heal(target, amount)
            encounters.record(state, player, healing=actual, ally_healing=actual if tactic == "ally" else 0)
            impact = impact or actual > 0
            lines.append(f"{target['username']} recovers {actual} HP.")
    elif effect["type"] == "roll_bonus":
        amount = round(effect["value"] * multiplier)
        targets = (
            ["boss"]
            if effect.get("target") == "enemy"
            else ([str(p["id"]) for p in state["players"]] if effect.get("target") == "party" else [str(user_id)])
        )
        for target in targets:
            bonuses[target] = bonuses.get(target, 0) + amount
        encounters.record(state, player, support=1)
        impact = amount != 0
        lines.append(f"{amount:+} points on the next eligible action.")
    elif effect["type"] == "guaranteed_success":
        state["guaranteed_success"][str(user_id)] = effect["category"]
        encounters.record(state, player, support=1)
        impact = True
        lines.append(f"Next {effect['category']} action is guaranteed.")
    if impact and adjusted_roll >= 10:
        encounters.record(state, player, criticals=1)
    if state["phase"] == "combat" and state["boss"]["hp"] == 0:
        win(state)
        lines.append("Victory! Your progress is saved.")
    elif state["phase"] == "combat":
        retaliate(state, user_id, rng, lines, guarded, countered)
    next_turn(state, user_id, previous_order)
    state["last_result"] = "\n".join(lines)
    return state["last_result"]


def support_effect(skill):
    damage_type = {"technology": "Energy", "communication": "Mercantile", "strength": "Kinetic", "stealth": "Darkness"}[
        skill["category"]
    ]
    return {
        "strike": {"type": "direct_damage", "value": skill["value"], "damage_type": damage_type},
        "heal": {"type": "heal", "value": skill["value"], "target": "self"},
        "focus": {"type": "roll_bonus", "value": skill["value"] * 2, "target": "self"},
    }[skill["kind"]]


def retaliate(state, actor_id, rng, lines, guarded=False, countered=False):
    boss = state["boss"]
    actor = next((p for p in state["players"] if p["id"] == actor_id), state["players"][0])
    if boss.get("intent"):
        crossed_threshold = boss["hp"] * 100 <= boss["max_hp"] * boss["design"]["phase_threshold"]
        encounters.execute_intent(state, actor, lines, guarded, countered)
        remove_dead(state, lines)
        encounters.advance_intent(state, lines, crossed_threshold)
        return
    if not boss.get("abilities"):
        return
    ability = rng.choice(boss["abilities"])
    effects = ability.get("effects", [])
    bonuses = state["active_roll_bonuses"]
    damage_bonus = bonuses.pop("boss", 0) if any(e["type"] == "direct_damage" for e in effects) else 0
    lines.append(f"{boss['name']} — {ability['name']}")
    # Snapshot targets before applying effects; death collection runs exactly once.
    players = state["players"]
    actor = next((p for p in players if p["id"] == actor_id), players[0])
    for effect in effects:
        if effect["type"] == "direct_damage":
            amount = round(effect["value"] * max(0, 1 + damage_bonus / 100))
            if state.get("selected_route") == "adrenal":
                amount = int(amount * 1.5)
            targets = players if effect["target"] in {"all", "players"} else [actor]
            for target in targets:
                damage = round(amount * 0.5) if guarded and target["id"] == actor_id else amount
                encounters.record(state, actor, blocked=max(0, min(target["hp"], amount) - min(target["hp"], damage)))
                target["hp"] -= damage
                lines.append(f"{target['username']} takes {damage} damage.")
        elif effect["type"] == "heal":
            amount = effect["value"] * (2 if state.get("selected_route") == "juiced_up" else 1)
            lines.append(f"{boss['name']} recovers {heal(boss, amount)} HP.")
        elif effect["type"] == "roll_bonus":
            targets = ["boss"] if effect["target"] == "self" else [str(p["id"]) for p in players]
            for target in targets:
                bonuses[target] = bonuses.get(target, 0) + effect["value"]
            lines.append(
                f"{'Boss damage' if effect['target'] == 'self' else 'Party next rolls'}: {effect['value']:+} points."
            )
    remove_dead(state, lines)


def resolve_campaign(state, user_id, action, result, rng=random):
    if state["phase"] != "campaign":
        raise InvalidAction("No campaign is active.")
    require_actor(state, user_id)
    player = state["players"][state["turn_index"]]
    previous_order = [p["id"] for p in state["players"]]
    category = canonical_category(result["action_category"])
    luck = rng.randint(1, 10)
    personal = state["active_roll_bonuses"].pop(str(user_id), 0) + player.get("talent_bonuses", {}).get("roll", 0)
    modifier = player["modifier_value"] if category == player["modifier_type"] else 0
    score = (result["skill_score"] + modifier + (personal + location_bonus(state, player, category)) / 10) * luck
    success = score > 50
    encounters.record(state, player, turns=1, ability=category, criticals=1 if luck == 10 and success else 0)
    damage = 0 if success else max(1, min(10, result["player_damage"]))
    hazard = state.get("location", {}).get("effect", {})
    if not success and hazard.get("type") == "environmental_hazard":
        damage += hazard.get("damage", 0)
    player["hp"] -= damage
    lines = [
        f"{player['username']} — {category}\nSkill {result['skill_score']} + faction {modifier}; bonus {personal + location_bonus(state, player, category):+} points; luck {luck}/10 → {score:g}: {'Success' if success else 'Failure'}."
    ]
    # Narrative is supplied after adjudication by the service; never present an
    # AI claim of victory as if it were the authoritative roll outcome.
    if damage:
        lines.append(f"{damage} damage taken.")
    queue_event(state, player, f"turn:{state['turn_id']}", stats={"moves_made": 1})
    milestone = result.get("milestone_id", "").strip().casefold()
    if success and result["event"] == "milestone_reached" and milestone:
        milestone = f"{state.get('campaign', {}).get('index', 0)}:{milestone}"
        seen = state.setdefault("milestones", [])
        if milestone not in seen and len(seen) < 50:
            seen.append(milestone)
            encounters.record(state, player, objectives=1)
            queue_event(state, player, f"milestone:{stable_id(milestone)}", XP_FOR_MILESTONE)
    remove_dead(state, lines)
    if success and result["event"] == "objective_complete" and state["phase"] == "campaign":
        state["chapter_last_action"] = action[:500]
        encounters.record(state, player, objectives=1)
        win(state)
        lines.append(
            "Campaign complete! Bank your reward."
            if state["phase"] == "victory"
            else "Chapter complete! Checkpoint, XP, and materials saved."
        )
    next_turn(state, user_id, previous_order)
    state["last_result"] = "\n".join(lines)
    state["narrative_log"] = (state.get("narrative_log", []) + [action[:500], state["last_result"]])[-4:]
    return state["last_result"]


def resolve_campaign_support(state, user_id):
    if state["phase"] != "campaign":
        raise InvalidAction("No campaign is active.")
    require_actor(state, user_id)
    player = state["players"][state["turn_index"]]
    ally = player.get("ally")
    if not ally or ally["charges"] <= 0:
        raise InvalidAction("No ally support charges remain this chapter.")
    previous_order = [p["id"] for p in state["players"]]
    ally["charges"] -= 1
    skill = ally["support"]
    encounters.record(state, player, turns=1, ability=skill["name"], support=1)
    if skill["kind"] == "heal":
        amount = heal(player, skill["value"] + player.get("talent_bonuses", {}).get("heal", 0))
        encounters.record(state, player, healing=amount, ally_healing=amount)
        text = f"{ally['name']} — {skill['name']}: {player['username']} recovers {amount} HP."
    else:
        points = skill["value"] * 2
        state["active_roll_bonuses"][str(user_id)] = state["active_roll_bonuses"].get(str(user_id), 0) + points
        text = f"{ally['name']} — {skill['name']}: +{points} points on your next campaign action."
    queue_event(state, player, f"turn:{state['turn_id']}", stats={"moves_made": 1})
    next_turn(state, user_id, previous_order)
    state["last_result"] = text + " Ally support uses your turn."
    state["narrative_log"] = (state.get("narrative_log", []) + [state["last_result"]])[-4:]
    return state["last_result"]
