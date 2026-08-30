"""Pure profile updates used inside Repository.mutate_profile transactions."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid

import gameplay_content as content
from game_constants import DAILY_LOGIN_XP, TITLES, XP_BASE, XP_BY_RARITY, XP_MULTIPLIER
from item_traits import ITEM_SLOTS, RARITY_ORDER, create_item_data, get_damage_type_for_specialty

TALENT_LEVELS = (2, 5, 10)
SALVAGE_YIELDS = (2, 4, 7, 12, 20, 35)
FORGE_COSTS = (8, 16, 28, 48, 80)


class InvalidAction(ValueError):
    """A user control no longer applies or its input is invalid."""


def stable_id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def normalize(profile: dict, username: str | None = None) -> dict:
    profile.setdefault("username", username or "Agent")
    if username is not None:
        profile["username"] = username[:100]
    defaults = {
        "level": 1,
        "current_xp": 0,
        "title": TITLES[0],
        "last_login_date": "1970-01-01",
        "inventory": [],
        "collectibles": [],
        "equipped_items": {},
        "pending_rewards": {},
        "active_ally_id": None,
        "talents": [],
        "talent_offers": {},
        "materials": 0,
        "loadout_version": 0,
        "craft_quote": None,
    }
    for key, value in defaults.items():
        profile.setdefault(key, copy.deepcopy(value))
    profile.setdefault("xp_to_next_level", int(XP_BASE * XP_MULTIPLIER ** profile["level"]))
    stats = profile.setdefault("stats", {})
    for key in (
        "bosses_attempted",
        "bosses_defeated",
        "highest_floor",
        "moves_made",
        "chapters_completed",
        "campaigns_completed",
    ):
        stats.setdefault(key, 0)
    for slot in ITEM_SLOTS:
        profile["equipped_items"].setdefault(slot, None)
    items = profile["inventory"] + [i for i in profile["equipped_items"].values() if i]
    for item in items + profile["collectibles"]:
        item.setdefault("id", uuid.uuid4().hex[:16])
    for ally in profile["collectibles"]:
        ally.setdefault("support", content.fallback_support(ally))
        ally.setdefault("bond", 0)
        ally.setdefault("design_source", "legacy")
    profile["schema_version"] = 3
    return profile


def add_xp(profile: dict, amount: int) -> dict:
    normalize(profile)
    previous_level = profile["level"]
    profile["current_xp"] += max(0, amount)
    while profile["current_xp"] >= profile["xp_to_next_level"]:
        profile["current_xp"] -= profile["xp_to_next_level"]
        profile["level"] += 1
        profile["xp_to_next_level"] = int(XP_BASE * XP_MULTIPLIER ** profile["level"])
        if profile["level"] in TITLES:
            profile["title"] = TITLES[profile["level"]]
    return {"xp": amount, "level": profile["level"], "leveled_up": profile["level"] > previous_level}


def daily_login(profile: dict, username: str, date: str) -> dict:
    normalize(profile, username)
    if profile["last_login_date"] >= date:
        return {"xp": 0, "level": profile["level"], "leveled_up": False}
    profile["last_login_date"] = date
    return add_xp(profile, DAILY_LOGIN_XP)


def apply_event(profile: dict, event: dict):
    normalize(profile, event["username"])
    if event["kind"] == "entitlement":
        reward = copy.deepcopy(event["reward"])
        profile["pending_rewards"].setdefault(reward["id"], reward)
        return reward
    for key, value in event.get("stats", {}).items():
        if key == "highest_floor":
            profile["stats"][key] = max(profile["stats"][key], value)
        else:
            profile["stats"][key] += value
    profile["materials"] += event.get("materials", 0)
    if event.get("ally_id"):
        ally = next((a for a in profile["collectibles"] if a["id"] == event["ally_id"]), None)
        if ally:
            ally["bond"] += 1
            profile["loadout_version"] += 1
    return add_xp(profile, event.get("xp", 0))


def grant_reward(profile: dict, reward_id: str, payload: dict):
    normalize(profile)
    if reward_id not in profile["pending_rewards"]:
        raise InvalidAction("This reward is no longer pending. Use /rewards.")
    reward = copy.deepcopy(payload)
    reward["id"] = reward_id
    collection = "inventory" if reward["kind"] == "item" else "collectibles"
    profile[collection].append(reward)
    del profile["pending_rewards"][reward_id]
    return {"reward": reward, **add_xp(profile, XP_BY_RARITY[reward["rarity"]])}


def make_item(name, slot, specialty, rarity, background, durability=3):
    return {**create_item_data(name, slot, specialty, rarity, background, durability), "kind": "item"}


def inventory_action(profile: dict, action: str, item_id: str):
    normalize(profile)
    inventory, equipped = profile["inventory"], profile["equipped_items"]
    if action == "unequip":
        slot = next((s for s, item in equipped.items() if item and item["id"] == item_id), None)
        if slot is None:
            raise InvalidAction("That item is no longer equipped. Refresh /inventory.")
        item = equipped[slot]
        inventory.append(item)
        equipped[slot] = None
    else:
        item = next((i for i in inventory if i["id"] == item_id), None)
        if item is None:
            raise InvalidAction("That item is no longer in your backpack. Refresh /inventory.")
        if action == "equip":
            slot = item["slot"]
            if slot not in ITEM_SLOTS:
                raise InvalidAction("This legacy item has an unsupported slot.")
            if equipped.get(slot):
                inventory.append(equipped[slot])
            equipped[slot] = item
        elif action != "discard":
            raise InvalidAction("Unknown inventory action.")
        inventory.remove(item)
    if action in {"equip", "unequip"}:
        profile["loadout_version"] += 1
    return item["name"]


def inventory_page(profile: dict, page=0, slot="all", rarity="all", size=5):
    items = [
        i
        for i in profile["inventory"]
        if (slot == "all" or i.get("slot") == slot) and (rarity == "all" or i.get("rarity") == rarity)
    ]
    items.sort(
        key=lambda i: (
            -RARITY_ORDER.index(i["rarity"]) if i.get("rarity") in RARITY_ORDER else 0,
            i.get("name", ""),
            i["id"],
        )
    )
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(int(page), pages - 1))
    return items[page * size : (page + 1) * size], page, pages


def talent_bonuses(profile):
    totals = {kind: 0 for kind in ("vitality", "precision", "restoration", "force")}
    for talent in profile.get("talents", []):
        totals[talent["kind"]] += talent["value"]
    return {
        "hp": min(6, totals["vitality"]),
        "roll": min(10, totals["precision"] * 2),
        "heal": min(3, totals["restoration"]),
        "damage": min(2, totals["force"]),
    }


def eligible_talent(profile):
    chosen = {t["milestone"] for t in profile["talents"]}
    return next((level for level in TALENT_LEVELS if profile["level"] >= level and level not in chosen), None)


def save_talent_offers(profile, milestone, offers, source):
    normalize(profile)
    if eligible_talent(profile) != milestone:
        raise InvalidAction("That talent milestone is no longer available. Open /progression.")
    validated = content.TalentOffers.model_validate({"offers": offers}).model_dump()["offers"]
    return profile["talent_offers"].setdefault(str(milestone), {"offers": validated, "source": source})


def choose_talent(profile, milestone, index):
    normalize(profile)
    offer = profile["talent_offers"].get(str(milestone))
    if eligible_talent(profile) != milestone or not offer or not 0 <= index < len(offer["offers"]):
        raise InvalidAction("That talent choice expired. Open /progression.")
    talent = {**copy.deepcopy(offer["offers"][index]), "milestone": milestone, "source": offer["source"]}
    if talent_bonuses({"talents": profile["talents"] + [talent]}) == talent_bonuses(profile):
        raise InvalidAction("That bonus is already capped. Choose another talent.")
    profile["talents"].append(talent)
    del profile["talent_offers"][str(milestone)]
    profile["loadout_version"] += 1
    return talent


def deploy_ally(profile, ally_id):
    normalize(profile)
    if ally_id != "none" and not any(a["id"] == ally_id for a in profile["collectibles"]):
        raise InvalidAction("You do not own that ally. Open /allies.")
    selected = None if ally_id == "none" else ally_id
    if profile["active_ally_id"] != selected:
        profile["active_ally_id"] = selected
        profile["loadout_version"] += 1
    return profile["active_ally_id"]


def ally_snapshot(profile):
    ally = next((a for a in profile.get("collectibles", []) if a["id"] == profile.get("active_ally_id")), None)
    if not ally:
        return None
    ally = copy.deepcopy(ally)
    rank = min(3, 1 + ally.get("bond", 0) // 3)
    ally["rank"] = rank
    ally["support"]["value"] = min(8, ally["support"]["value"] + rank - 1)
    ally["charges"] = 2 if rank == 3 else 1
    return ally


def salvage_value(item):
    if item.get("rarity") not in RARITY_ORDER:
        raise InvalidAction("This legacy item's rarity needs inspection before salvage.")
    return SALVAGE_YIELDS[RARITY_ORDER.index(item["rarity"])]


def salvage_item(profile, item_id):
    normalize(profile)
    item = next((i for i in profile["inventory"] if i["id"] == item_id), None)
    if not item:
        raise InvalidAction("Only an item currently in your backpack can be salvaged.")
    amount = salvage_value(item)
    profile["inventory"].remove(item)
    profile["materials"] += amount
    return {"name": item["name"], "materials": amount, "balance": profile["materials"]}


def fingerprint(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def forge_terms(item):
    if item.get("rarity") not in RARITY_ORDER or item.get("slot") not in ITEM_SLOTS:
        raise InvalidAction("This legacy item cannot be upgraded safely.")
    tier = RARITY_ORDER.index(item["rarity"])
    if tier == len(RARITY_ORDER) - 1:
        raise InvalidAction("Peerless items are already at the highest tier.")
    return RARITY_ORDER[tier + 1], FORGE_COSTS[tier]


def forged_item(source, design=None):
    rarity, _ = forge_terms(source)
    if design is None:
        return make_item(
            "Refined " + source["name"][:65],
            source["slot"],
            source["specialty"],
            rarity,
            "Rebuilt from recovered materials in an Underground workshop.",
        )
    design = content.ForgeDesign.model_validate(design).model_dump()
    item = make_item(design["name"], source["slot"], design["specialty"], rarity, design["background"])
    skill = design["ability"]
    tier = RARITY_ORDER.index(rarity)
    value = skill["value"] * (1 + tier // 2)
    effect = {
        "strike": {
            "type": "direct_damage",
            "damage_type": get_damage_type_for_specialty(design["specialty"]),
            "value": value,
        },
        "heal": {"type": "heal", "target": "self", "value": value},
        "focus": {"type": "roll_bonus", "target": "self", "value": min(30, value * 2)},
    }[skill["kind"]]
    charges = (1, 1, 2, 2, 3, 3)[tier]
    item["ability"] = {
        "name": skill["name"],
        "description": skill["description"],
        "category": skill["category"],
        "effect": effect,
        "charges": charges,
        "max_charges": charges,
    }
    return item


def save_craft_quote(profile, source, output, quote_id, timestamp, design_source):
    normalize(profile)
    current = next((i for i in profile["inventory"] if i["id"] == source["id"]), None)
    if not current or fingerprint(current) != fingerprint(source):
        raise InvalidAction("The source item changed. Open /inventory to request a new blueprint.")
    prior = profile["craft_quote"]
    if prior and prior["fingerprint"] == fingerprint(source):
        return prior
    if timestamp - profile.get("last_blueprint_at", 0) < 30:
        raise InvalidAction("Wait 30 seconds between new blueprints. Your existing /craft preview is saved.")
    rarity, cost = forge_terms(source)
    if output["slot"] != source["slot"] or output["rarity"] != rarity:
        raise InvalidAction("Blueprint does not match its source and tier.")
    quote = {
        "id": quote_id,
        "source_id": source["id"],
        "source_name": source["name"],
        "fingerprint": fingerprint(source),
        "cost": cost,
        "output": copy.deepcopy(output),
        "design_source": design_source,
    }
    profile["craft_quote"] = quote
    profile["last_blueprint_at"] = timestamp
    return quote


def complete_craft(profile, quote_id):
    normalize(profile)
    quote = profile["craft_quote"]
    if not quote or quote["id"] != quote_id:
        raise InvalidAction("This blueprint expired. Open /craft for the saved preview or receipt.")
    item = next((i for i in profile["inventory"] if i["id"] == quote["source_id"]), None)
    if not item or fingerprint(item) != quote["fingerprint"]:
        raise InvalidAction("The source item was moved or changed. No materials were spent.")
    if profile["materials"] < quote["cost"]:
        raise InvalidAction(f"Need {quote['cost']} materials; you have {profile['materials']}. No items were consumed.")
    output = {**copy.deepcopy(quote["output"]), "id": quote_id, "design_source": quote["design_source"]}
    profile["inventory"].remove(item)
    profile["inventory"].append(output)
    profile["materials"] -= quote["cost"]
    profile["craft_quote"] = None
    receipt = {"item": output, "spent": quote["cost"], "balance": profile["materials"]}
    profile["last_craft_receipt"] = receipt
    return receipt


def cancel_craft(profile, quote_id):
    normalize(profile)
    if not profile["craft_quote"] or profile["craft_quote"]["id"] != quote_id:
        raise InvalidAction("That blueprint is no longer active. Open /craft.")
    profile["craft_quote"] = None
    return quote_id
