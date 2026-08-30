"""Published boss intents and factual contribution accounting for encounters and chapters."""

from __future__ import annotations

import copy

import gameplay_content as content


def contribution(state, player):
    return state.setdefault("combat_stats", {}).setdefault(
        str(player["id"]),
        {
            "id": player["id"],
            "username": player["username"],
            "faction": player["faction"],
            "damage": 0,
            "healing": 0,
            "blocked": 0,
            "support": 0,
            "criticals": 0,
            "turns": 0,
            "ally_damage": 0,
            "ally_healing": 0,
            "objectives": 0,
            "abilities": [],
            "fallen": False,
        },
    )


def begin(state):
    state["combat_stats"] = {}
    state["stats_partial"] = False
    for player in state["players"]:
        contribution(state, player)


def record(state, player, **values):
    stats = contribution(state, player)
    for key, value in values.items():
        if key == "ability":
            if value not in stats["abilities"] and len(stats["abilities"]) < 30:
                stats["abilities"].append(value)
        else:
            stats[key] += max(0, value)


def recap(state, title):
    participants = copy.deepcopy(list(state.get("combat_stats", {}).values()))
    fallen = {p["id"] for p in state.get("dead_players", [])}
    for row in participants:
        row["fallen"] = row["id"] in fallen
        row["honors"] = []
    for key, label in [
        ("damage", "Most Lethal"),
        ("healing", "Field Medic"),
        ("blocked", "Guardian"),
        ("criticals", "Clutch Moment"),
        ("support", "Team Support"),
        ("objectives", "Pathfinder"),
    ]:
        high = max((p[key] for p in participants), default=0)
        if high:
            for row in participants:
                if row[key] == high:
                    row["honors"].append(label)
    result = {
        "id": f"{state['run_id']}:{state.get('game_mode')}:{state.get('gauntlet_level')}:{state.get('campaign', {}).get('index', 0)}",
        "title": title,
        "participants": participants,
        "partial": state.get("stats_partial", False),
        "streak": state.get("winning_streak", 0),
        "story": None,
    }
    state["last_recap"] = result
    return result


def install_design(state, design, source):
    data = content.EncounterDesign.model_validate(design).model_dump()
    boss = state["boss"]
    boss.update(
        name=data["name"],
        description=data["description"],
        design=data,
        design_source=source,
        intent_index=0,
        phase_two=False,
    )
    publish_intent(state)
    state["objective"] = f"Defeat {boss['name']}"


def publish_intent(state):
    boss = state["boss"]
    design = boss["design"]
    intent = copy.deepcopy(design["moves"][boss["intent_index"] % len(design["moves"])])
    if boss["phase_two"]:
        intent["power"] = min(6 if intent["target"] == "party" else 10, intent["power"] + design["phase_power_bonus"])
    boss["intent"] = intent


def advance_intent(state, lines, crossed_threshold=False):
    boss = state["boss"]
    if state["phase"] != "combat" or boss["hp"] <= 0:
        return
    design = boss["design"]
    if not boss["phase_two"] and (crossed_threshold or boss["hp"] * 100 <= boss["max_hp"] * design["phase_threshold"]):
        boss["phase_two"] = True
        lines.append(f"New phase — {design['phase_name']}: {design['phase_telegraph']}")
    boss["intent_index"] += 1
    publish_intent(state)


def intent_text(state):
    boss = state.get("boss") or {}
    intent = boss.get("intent")
    if not intent:
        return "Legacy encounter: no published intent. New floors use telegraphed moves."
    value = intent["power"]
    if intent["kind"] == "damage":
        value = round(value * max(0, 1 + state["active_roll_bonuses"].get("boss", 0) / 100))
        if state.get("selected_route") == "adrenal":
            value = int(value * 1.5)
        effect = f"{value} damage to {'the party' if intent['target'] == 'party' else 'the acting player'}"
    elif intent["kind"] == "heal":
        effect = f"heal boss {value * (2 if state.get('selected_route') == 'juiced_up' else 1)} HP"
    else:
        effect = (
            f"-{value * 2} next-roll points to {'the party' if intent['target'] == 'party' else 'the acting player'}"
        )
    phase = boss["design"]["phase_name"] if boss["phase_two"] else "Opening phase"
    return (
        f"Next: {intent['name']} — {effect}.\n{intent['telegraph']}\n"
        f"Counter: {intent['counter_category']} (6+ on d10 halves this move). {phase}."
    )


def execute_intent(state, actor, lines, guarded=False, countered=False):
    """Execute exactly the persisted move; phase transitions affect the next one."""
    boss = state["boss"]
    move = boss["intent"]
    lines.append(f"{boss['name']} — {move['name']} (published intent)")
    targets = state["players"] if move["target"] == "party" else [actor]
    reduction = 0.5 if countered else 1
    if move["kind"] == "damage":
        bonus = state["active_roll_bonuses"].pop("boss", 0)
        base = round(move["power"] * max(0, 1 + bonus / 100))
        if state.get("selected_route") == "adrenal":
            base = int(base * 1.5)
        for target in targets:
            damage = round(base * reduction * (0.5 if guarded and target["id"] == actor["id"] else 1))
            record(state, actor, blocked=max(0, min(target["hp"], base) - min(target["hp"], damage)))
            target["hp"] -= damage
            lines.append(f"{target['username']} takes {damage} damage.")
    elif move["kind"] == "heal":
        amount = round(move["power"] * reduction) * (2 if state.get("selected_route") == "juiced_up" else 1)
        actual = min(amount, boss["max_hp"] - boss["hp"])
        boss["hp"] += actual
        lines.append(f"{boss['name']} recovers {actual} HP.")
    else:
        points = round(move["power"] * 2 * reduction)
        for target in targets:
            key = str(target["id"])
            state["active_roll_bonuses"][key] = state["active_roll_bonuses"].get(key, 0) - points
        lines.append(f"Targets lose {points} points on their next eligible roll.")
