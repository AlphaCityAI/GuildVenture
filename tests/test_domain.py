import copy

import pytest

import game
import profiles
from bosstraits import BOSS_TRAITS
from item_traits import ITEM_SLOTS, ITEM_SPECIALTIES, RARITY_ORDER, get_item_ability
from presentation import chunks, effect_text
from profiles import InvalidAction


class FixedRandom:
    def __init__(self, roll=5, ability=0):
        self.roll, self.ability = roll, ability

    def randint(self, low, high):
        return min(high, max(low, self.roll))

    def choice(self, values):
        return values[self.ability]


def combat(faction="Nodewalker"):
    state = game.new_state(1, None)
    profile = profiles.normalize({})
    state.update(
        phase="combat",
        game_mode="gauntlet",
        gauntlet_level=1,
        location={},
        players=[game.make_player(1, "Alice", faction, profile)],
        boss={"name": "Boss", "hp": 100, "max_hp": 100, "abilities": [], "strengths": [], "weaknesses": []},
    )
    return state


def test_daily_login_level_up_is_preserved_and_only_awarded_once():
    profile = profiles.normalize({"current_xp": 560})
    result = profiles.daily_login(profile, "Alice", "2026-08-29")
    assert result == {"xp": 50, "level": 2, "leveled_up": True}
    assert profile["level"] == 2 and profile["current_xp"] == 35
    assert profiles.daily_login(profile, "Alice", "2026-08-29")["xp"] == 0


@pytest.mark.parametrize(
    "roll,expected",
    [
        (1, "Salvage"),
        (35, "Salvage"),
        (36, "Gutter-Tech"),
        (60, "Gutter-Tech"),
        (61, "Street Mod"),
        (80, "Street Mod"),
        (81, "Black Market"),
        (94, "Black Market"),
        (95, "Node-Forged"),
        (99, "Node-Forged"),
        (100, "Peerless"),
    ],
)
def test_reward_boundaries(roll, expected):
    assert game.rarity(roll)[0] == expected


def test_all_tiers_reachable_and_bonus_odds_match_rolls():
    assert {game.reward_roll(FixedRandom(i), 1, 0)["rarity"] for i in range(1, 101)} == set(RARITY_ORDER)
    for bonus in [0, 2, 30, 60]:
        expected = game.reward_odds(20, bonus)
        for tier, probability in expected.items():
            count = sum(game.reward_roll(FixedRandom(i), 20, bonus)["rarity"] == tier for i in range(20, 101))
            assert probability == pytest.approx(count / 81)
        assert sum(expected.values()) == pytest.approx(1)


@pytest.mark.parametrize("boss", list(BOSS_TRAITS))
def test_every_boss_resistance_and_weakness_changes_damage(boss):
    state = combat()
    state["boss"].update(copy.deepcopy(BOSS_TRAITS[boss]))
    actor = state["players"][0]
    for effect in state["boss"]["strengths"] + state["boss"]["weaknesses"]:
        assert game.damage_to_boss(20, state, actor, effect["damage_type"], "technology") == round(20 * effect["value"])


def test_blockchain_passive_boosts_mercantile_damage():
    state = combat("Coinbroker")
    actor = state["players"][0]
    actor["equipped_items"] = {"Equipment": {"specialty": "Blockchain", "rarity": "Peerless"}}
    assert game.damage_to_boss(20, state, actor, "Mercantile", "communication") == 32


def test_buff_is_kept_for_next_eligible_action_then_consumed():
    state = combat()
    actor = state["players"][0]
    actor["abilities"].append(get_item_ability("Cranial", "Blockchain", "Street Mod"))
    game.resolve_combat(state, 1, 3, rng=FixedRandom(5))
    assert state["active_roll_bonuses"]["1"] == 15
    before = state["boss"]["hp"]
    game.resolve_combat(state, 1, 0, rng=FixedRandom(5))
    assert "1" not in state["active_roll_bonuses"]
    assert before - state["boss"]["hp"] == 5  # 4 base × 1.25 after +1.5 d10 steps


def test_boss_debuff_is_applied_to_following_player_action():
    state = combat()
    state["boss"]["abilities"] = [BOSS_TRAITS["Dynastic Scion"]["abilities"][0]]
    game.resolve_combat(state, 1, 0, rng=FixedRandom(5))
    assert state["active_roll_bonuses"]["1"] == -10
    before = state["boss"]["hp"]
    game.resolve_combat(state, 1, 0, rng=FixedRandom(5))
    assert before - state["boss"]["hp"] == 3


def test_boss_heal_and_damage_modifiers_resolve_without_ai():
    state = combat()
    state["boss"]["hp"] = 10
    state["selected_route"] = "juiced_up"
    state["boss"]["abilities"] = [BOSS_TRAITS["Mech-Warden"]["abilities"][1]]
    game.retaliate(state, 1, FixedRandom(), [])
    assert state["boss"]["hp"] == 26
    assert state["active_roll_bonuses"]["1"] == -5


def test_environment_failure_removes_dead_players_without_retaliation():
    state = combat()
    state["location"] = {
        "interaction": {
            "name": "Trap",
            "category": "technology",
            "failure_effect": {"value": 5},
            "failure_narrative": "Trap fails.",
            "success_effect": {"value": 5},
        }
    }
    state["boss"]["hp"] = 50
    state["players"][0]["hp"] = 1
    game.resolve_combat(state, 1, environment=True, rng=FixedRandom(1))
    assert state["phase"] == "defeat" and state["players"] == []
    assert len(state["dead_players"]) == 1 and state["dead_players"][0]["hp"] == 0


@pytest.mark.parametrize("phase,hp,used", [("victory", 0, False), ("combat", 100, False), ("combat", 50, True)])
def test_environment_rules_enforced_server_side(phase, hp, used):
    state = combat()
    state.update(phase=phase, location_interaction_used=used, location={"interaction": {"name": "Trap"}})
    state["boss"]["hp"] = hp
    snapshot = copy.deepcopy(state)
    with pytest.raises(InvalidAction):
        game.resolve_combat(state, 1, environment=True)
    assert state == snapshot


def test_victory_is_once_and_dead_boss_never_retaliates():
    state = combat()
    state["boss"]["hp"] = 1
    state["boss"]["abilities"] = BOSS_TRAITS["Mech-Warden"]["abilities"]
    game.resolve_combat(state, 1, 0, rng=FixedRandom(5))
    assert state["phase"] == "victory" and state["players"][0]["hp"] == 20
    assert len(state["events"]) == 1 and state["gauntlet_bonus_defeated"] == 1
    with pytest.raises(InvalidAction):
        game.resolve_combat(state, 1, 0)


def test_effective_healing_and_death_deduplication():
    state = combat()
    state["players"][0]["hp"] = 19
    text = game.resolve_combat(state, 1, 2, rng=FixedRandom())
    assert "recovers 1 HP" in text
    state["players"][0]["hp"] = -5
    game.remove_dead(state, [])
    game.remove_dead(state, [])
    assert len(state["dead_players"]) == 1


def test_dead_actor_advances_to_correct_next_player():
    state = combat()
    for uid in (2, 3):
        state["players"].append(game.make_player(uid, str(uid), "Nodewalker", profiles.normalize({})))
    state["turn_index"] = 1
    state["players"][1]["hp"] = 1
    state["boss"]["abilities"] = [BOSS_TRAITS["Mech-Warden"]["abilities"][0]]
    game.resolve_combat(state, 2, 0, rng=FixedRandom())
    assert state["players"][state["turn_index"]]["id"] == 3


def test_party_damage_preserves_turn_order_when_actor_and_earlier_player_die():
    state = combat()
    for uid in (2, 3, 4):
        state["players"].append(game.make_player(uid, str(uid), "Nodewalker", profiles.normalize({})))
    state["turn_index"] = 2
    for index in (0, 2):
        state["players"][index]["hp"] = 1
    state["boss"]["abilities"] = [
        {"name": "Shockwave", "effects": [{"type": "direct_damage", "value": 2, "target": "all"}]}
    ]
    game.resolve_combat(state, 3, 0, rng=FixedRandom())
    assert [p["id"] for p in state["players"]] == [2, 4]
    assert state["players"][state["turn_index"]]["id"] == 4


def test_legacy_campaign_migration_preserves_progress_and_accepts_null_hazard():
    state = combat()
    state.pop("schema_version")
    state.pop("phase")
    state.update(game_stage="LEVEL_1", game_mode="open_campaign", hazard_effect=None, is_processing_turn=True)
    state["players"][0]["abilities"][0]["charges"] = 2
    assert game.migrate_state(state)
    assert state["phase"] == "campaign" and "is_processing_turn" not in state
    assert state["players"][0]["abilities"][0]["charges"] == 2
    assessment = {"action_category": "technology", "skill_score": 5, "player_damage": 1, "event": "none"}
    game.resolve_campaign(state, 1, "hack", assessment, FixedRandom())
    assert state["turn_id"] == 1
    assert not game.migrate_state(state)


def test_campaign_completion_requires_successful_roll_and_canonical_category():
    state = combat()
    state.update(phase="campaign", game_mode="open_campaign", boss=None)
    assessment = {"action_category": "technology", "skill_score": 10, "player_damage": 2, "event": "objective_complete"}
    game.resolve_campaign(state, 1, "hack terminal", assessment, FixedRandom(1))
    assert state["phase"] == "campaign"
    result = game.resolve_campaign(state, 1, "hack terminal", assessment, FixedRandom(10))
    assert state["phase"] == "victory" and "faction 1" in result


def test_location_faction_aliases_and_known_categories():
    state = combat()
    state["location"] = {
        "effect": {"type": "faction_modifier", "category": "technology", "faction": "Nodewalkers", "value": 20}
    }
    assert game.location_bonus(state, state["players"][0], "technology") == 20
    assert game.guess_category("communication") == "communication"
    assert game.guess_category("strength") == "strength"


def test_all_item_templates_are_usable_and_described():
    for slot in ITEM_SLOTS:
        for specialty in ITEM_SPECIALTIES:
            for tier in RARITY_ORDER:
                ability = get_item_ability(slot, specialty, tier)
                assert ability["charges"] > 0 and effect_text(ability)
    assert len(list(chunks("🙂" * 3000))) == 2
    assert "".join(chunks("🙂" * 3000)) == "🙂" * 3000


def test_equipment_ids_survive_list_reordering_and_no_wrong_discard():
    profile = profiles.normalize({})
    for name in ["A", "B"]:
        item = profiles.make_item(name, "Cranial", "Neural", "Salvage", "")
        item["id"] = name
        profile["inventory"].append(item)
    profiles.inventory_action(profile, "equip", "A")
    with pytest.raises(InvalidAction):
        profiles.inventory_action(profile, "discard", "A")
    assert profile["inventory"][0]["name"] == "B"


def test_run_bonus_is_unified_and_capped():
    assert game.run_bonus({"gauntlet_bonus_attempted": 10, "gauntlet_bonus_defeated": 9}) == 19
    assert game.run_bonus({"gauntlet_bonus_attempted": 99, "gauntlet_bonus_defeated": 99}) == 60
