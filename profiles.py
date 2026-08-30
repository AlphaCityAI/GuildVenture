"""Pure profile updates used inside Repository.mutate_profile transactions."""

from __future__ import annotations

import copy
import hashlib
import uuid

from game_constants import DAILY_LOGIN_XP, TITLES, XP_BASE, XP_BY_RARITY, XP_MULTIPLIER
from item_traits import ITEM_SLOTS, RARITY_ORDER, create_item_data


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
    }
    for key, value in defaults.items():
        profile.setdefault(key, copy.deepcopy(value))
    profile.setdefault("xp_to_next_level", int(XP_BASE * XP_MULTIPLIER ** profile["level"]))
    stats = profile.setdefault("stats", {})
    for key in ("bosses_attempted", "bosses_defeated", "highest_floor", "moves_made"):
        stats.setdefault(key, 0)
    for slot in ITEM_SLOTS:
        profile["equipped_items"].setdefault(slot, None)
    items = profile["inventory"] + [i for i in profile["equipped_items"].values() if i]
    for item in items + profile["collectibles"]:
        item.setdefault("id", uuid.uuid4().hex[:16])
    profile["schema_version"] = 2
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
