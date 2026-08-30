import asyncio
import copy
import json
import random
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from telegram.error import BadRequest

import encounters
import game
import gameplay_content as content
import presentation as ui
import profiles
from ai_service import AIService, CampaignAssessment
from bot_service import BotService
from conftest import update
from profiles import InvalidAction
from test_domain import FixedRandom, combat
from test_service import begin, click, join_ready
from test_ai_and_startup import client_with, response


def recruit(uid="a" * 16, kind="heal", value=4):
    return {
        "id": uid,
        "kind": "character",
        "name": "Nyx",
        "faction": "Nodewalker",
        "rarity": "Street Mod",
        "level": 10,
        "background": "A street medic with an unusual signal rig.",
        "bond": 0,
        "support": content.SupportSkill(
            name="Signal Mend", description="Restore a wounded ally.", kind=kind, value=value, category="technology"
        ).model_dump(),
    }


def designed_combat(power=6):
    state = combat()
    state["boss"]["description"] = "A patrol automaton."
    encounters.begin(state)
    design = content.fallback_encounter(state["boss"], random.Random(1)).model_dump()
    design["moves"][0].update(power=power, counter_category="technology")
    design["moves"][1] = {**design["moves"][0], "name": "Second strike"}
    encounters.install_design(state, design, "AI")
    return state


def backpack(profile, name="Cache", rarity="Salvage", uid="i" * 16):
    item = profiles.make_item(name, "Cranial", "Neural", rarity, "Recovered equipment.")
    item["id"] = uid
    profile["inventory"].append(item)
    return item


async def campaign_started(rig, party=(1,)):
    await begin(rig, "open_campaign")
    for uid in party:
        await join_ready(rig, uid)
    await click(rig, "start")
    assert rig.repo.states[100]["phase"] == "chapter_briefing"
    await click(rig, "chapter", "0")
    for uid in party:
        await click(rig, "ready", uid=uid)
    await click(rig, "start")
    assert rig.repo.states[100]["phase"] == "campaign"


@pytest.mark.parametrize(
    "change",
    [
        {"power": 999},
        {"target": "party", "power": 8},
        {"target": "self", "kind": "damage"},
        {"kind": "erase_inventory"},
        {"counter_category": "admin"},
        {"extra_reward": 1000},
    ],
)
def test_invalid_generated_boss_mechanics_are_rejected(change):
    move = {
        "name": "Sweep",
        "telegraph": "A weapon charges.",
        "kind": "damage",
        "power": 4,
        "target": "actor",
        "counter_category": "stealth",
        **change,
    }
    with pytest.raises(ValidationError):
        content.BossMove.model_validate(move)


def test_phase_change_keeps_current_telegraph_honest_and_survives_reload():
    state = designed_combat()
    state["boss"]["hp"] = 51
    displayed = copy.deepcopy(state["boss"]["intent"])
    state = copy.deepcopy(state)  # What a storage reload returns.
    before = state["players"][0]["hp"]
    game.resolve_combat(state, 1, 0, rng=FixedRandom(5))
    assert before - state["players"][0]["hp"] == displayed["power"]
    assert state["boss"]["phase_two"]
    assert state["boss"]["intent"]["power"] == displayed["power"] + 1
    assert "New phase" in state["last_result"]


def test_crossed_phase_threshold_is_not_erased_by_boss_healing():
    state = designed_combat()
    state["boss"]["hp"] = 51
    state["boss"]["intent"].update(kind="heal", target="self", power=8)
    game.resolve_combat(state, 1, 0, rng=FixedRandom(5))
    assert state["boss"]["hp"] > 50 and state["boss"]["phase_two"]


@pytest.mark.parametrize("tactic,roll,expected", [("guard", 1, 3), ("counter", 5, 3), ("counter", 1, 6)])
def test_guard_and_counter_apply_exactly_to_published_damage(tactic, roll, expected):
    state = designed_combat()
    before = state["players"][0]["hp"]
    game.resolve_combat(state, 1, rng=FixedRandom(roll), tactic=tactic)
    assert before - state["players"][0]["hp"] == expected
    assert state["combat_stats"]["1"]["blocked"] == 6 - expected
    assert state["turn_id"] == 1


def test_recap_counts_effective_damage_healing_and_fallen_contributors():
    state = combat()
    state["players"].append(game.make_player(2, "Bob", "Nodewalker", profiles.normalize({})))
    encounters.begin(state)
    state["players"][0]["hp"] = 19
    game.resolve_combat(state, 1, 2, rng=FixedRandom(10))
    assert state["combat_stats"]["1"]["healing"] == 1
    state["players"][0]["hp"] = 0
    game.remove_dead(state, [])
    state["turn_index"] = 0
    state["boss"]["hp"] = 1
    game.resolve_combat(state, 2, 0, rng=FixedRandom(10))
    report = state["last_recap"]
    alice, bob = report["participants"]
    assert alice["healing"] == 1 and alice["fallen"] and "Field Medic" in alice["honors"]
    assert bob["damage"] == 1 and bob["criticals"] == 1
    assert {e["user_id"] for e in state["events"] if "victory" in e["id"]} == {1, 2}
    assert "fallen" in ui.recap_text(report)


def test_legacy_collectibles_and_active_sessions_upgrade_without_losing_progress():
    profile = {"level": 5, "current_xp": 123, "collectibles": [recruit()], "schema_version": 2}
    profile["collectibles"][0].pop("support")
    profiles.normalize(profile)
    assert profile["collectibles"][0]["id"] == "a" * 16 and profile["current_xp"] == 123
    assert profile["materials"] == 0 and profile["collectibles"][0]["support"]
    state = combat()
    state.update(schema_version=2, phase="campaign", game_mode="open_campaign", objective="Recover the original key")
    state["players"][0]["hp"] = 7
    assert game.migrate_state(state)
    assert len(state["campaign"]["chapters"]) == 1 and state["objective"] == "Recover the original key"
    assert state["players"][0]["hp"] == 7 and state["stats_partial"]
    game.resolve_campaign(
        state,
        1,
        "recover key",
        {"action_category": "technology", "skill_score": 10, "player_damage": 0, "event": "objective_complete"},
        FixedRandom(10),
    )
    assert state["phase"] == "victory" and state["last_recap"]["partial"]


def test_ally_snapshot_has_finite_charges_and_does_not_change_midfight():
    profile = profiles.normalize({"collectibles": [recruit()]})
    profiles.deploy_ally(profile, "a" * 16)
    state = combat()
    game.refresh_loadout(state["players"][0], profile)
    profile["collectibles"][0]["support"]["value"] = 6
    state["players"][0]["hp"] = 10
    game.resolve_combat(state, 1, rng=FixedRandom(5), tactic="ally")
    assert state["players"][0]["hp"] == 14
    assert state["combat_stats"]["1"]["ally_healing"] == 4
    assert state["turn_id"] == 1
    with pytest.raises(InvalidAction, match="charges"):
        game.resolve_combat(state, 1, tactic="ally")
    assert profile["collectibles"][0]["bond"] == 0


def test_talent_choices_are_level_gated_and_bonuses_are_capped():
    profile = profiles.normalize({})
    assert profiles.eligible_talent(profile) is None
    with pytest.raises(InvalidAction):
        profiles.save_talent_offers(profile, 2, content.fallback_talents().model_dump()["offers"], "fallback")
    profile["level"] = 10
    for milestone in profiles.TALENT_LEVELS:
        design = content.fallback_talents(profile)
        profiles.save_talent_offers(profile, milestone, design.model_dump()["offers"], "fallback")
        profiles.choose_talent(profile, milestone, 0)
    assert profiles.eligible_talent(profile) is None
    bonus = profiles.talent_bonuses(profile)
    assert bonus["hp"] <= 6 and bonus["damage"] <= 2 and bonus["heal"] <= 3 and bonus["roll"] <= 10
    state = combat()
    game.refresh_loadout(state["players"][0], profile)
    assert state["players"][0]["max_hp"] == 20 + bonus["hp"]
    with pytest.raises(InvalidAction):
        profiles.choose_talent(profile, 2, 0)


def test_rest_is_once_per_camp_and_never_resurrects():
    state = combat()
    game.enter_preparation(state)
    state["players"][0]["hp"] = 1
    game.rest(state, 1)
    assert state["players"][0]["hp"] == 6
    with pytest.raises(InvalidAction):
        game.rest(state, 1)
    with pytest.raises(InvalidAction):
        game.rest(state, 99)
    state["phase"] = "combat"
    with pytest.raises(InvalidAction):
        game.rest(state, 1)


def test_crafting_requires_unchanged_backpack_source_and_sufficient_materials():
    profile = profiles.normalize({})
    item = backpack(profile)
    output = profiles.forged_item(item)
    profiles.save_craft_quote(profile, item, output, "q" * 16, 100, "fallback")
    before = copy.deepcopy(profile)
    with pytest.raises(InvalidAction, match="materials"):
        profiles.complete_craft(profile, "q" * 16)
    assert profile == before
    profile["materials"] = 100
    profiles.inventory_action(profile, "equip", item["id"])
    with pytest.raises(InvalidAction, match="moved"):
        profiles.complete_craft(profile, "q" * 16)
    profiles.inventory_action(profile, "unequip", item["id"])
    profile["inventory"][0]["name"] = "Changed item"
    with pytest.raises(InvalidAction, match="moved"):
        profiles.complete_craft(profile, "q" * 16)


@pytest.mark.parametrize("kind", ["strike", "heal", "focus"])
def test_generated_forge_abilities_are_usable_at_every_upgrade_tier(kind):
    design = content.ForgeDesign(
        name="Echo Lattice",
        background="Rebuilt from signal shards.",
        specialty="Neural",
        ability=content.SupportSkill(
            name="Echo", description="A tuned resonance.", kind=kind, value=5, category="technology"
        ),
    )
    for rarity in profiles.RARITY_ORDER[:-1]:
        source = profiles.make_item("Old", "Cranial", "Neural", rarity, "")
        item = profiles.forged_item(source, design.model_dump())
        state = combat()
        state["players"][0]["abilities"].append(item["ability"])
        assert ui.effect_text(item["ability"])
        game.resolve_combat(state, 1, 3, rng=FixedRandom(5))
        assert state["turn_id"] == 1


def test_salvage_yields_prevent_profitable_upgrade_salvage_cycle():
    profile = profiles.normalize({})
    for index, rarity in enumerate(profiles.RARITY_ORDER[:-1]):
        item = backpack(profile, rarity=rarity, uid=str(index))
        target, cost = profiles.forge_terms(item)
        assert profiles.salvage_value({"rarity": target}) < cost + profiles.salvage_value(item)
        before = profile["materials"]
        result = profiles.salvage_item(profile, item["id"])
        assert profile["materials"] == before + result["materials"]
        with pytest.raises(InvalidAction):
            profiles.salvage_item(profile, item["id"])


async def test_generated_encounter_is_saved_once_and_recovers_without_regeneration(rig):
    sample = designed_combat()["boss"]["design"]
    sample["name"] = "The Glass Auditor"
    rig.ai.encounter = AsyncMock(return_value=content.EncounterDesign.model_validate(sample))
    await begin(rig)
    await join_ready(rig)
    await click(rig, "start")
    saved = copy.deepcopy(rig.repo.states[100])
    restarted = BotService(rig.repo, rig.ai)
    await restarted.handle(update(rig.bot, text="/status"), rig.context)
    assert rig.repo.states[100] == saved
    assert saved["boss"]["name"] == "The Glass Auditor" and saved["boss"]["design_source"] == "AI"
    rig.ai.encounter.assert_awaited_once()


async def test_changed_ready_loadout_requires_new_consent(rig):
    await begin(rig)
    await join_ready(rig)
    profile = rig.repo.profiles[1]
    item = backpack(profile)
    profiles.inventory_action(profile, "equip", item["id"])
    await click(rig, "start")
    assert rig.repo.states[100]["phase"] == "lobby" and not rig.repo.states[100]["players"][0]["ready"]
    await click(rig, "ready")
    await click(rig, "start")
    assert rig.repo.states[100]["players"][0]["equipped_items"]["Cranial"]["id"] == item["id"]


async def test_next_floor_stops_at_camp_and_refreshes_ally_only_after_ready(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    p = rig.repo.profiles[1]
    p["collectibles"] = [recruit(kind="strike")]
    profiles.deploy_ally(p, "a" * 16)
    await begin(rig)
    await join_ready(rig)
    await click(rig, "start")
    state = await rig.repo.load_state(100)
    state["boss"]["hp"] = 1
    await rig.service.commit(100, state)
    rig.service.rng.randint = lambda lo, hi: hi
    await click(rig, "ally")
    assert rig.repo.states[100]["phase"] == "victory"
    assert rig.repo.profiles[1]["collectibles"][0]["bond"] == 1
    await click(rig, "continue")
    await click(rig, "route", "default")
    assert rig.repo.states[100]["phase"] == "preparation"
    assert rig.repo.states[100]["players"][0]["ally"]["charges"] == 0
    await click(rig, "start")
    assert rig.repo.states[100]["phase"] == "preparation"
    await click(rig, "ready")
    await click(rig, "start")
    assert rig.repo.states[100]["gauntlet_level"] == 2 and rig.repo.states[100]["phase"] == "combat"
    assert rig.repo.states[100]["players"][0]["ally"]["charges"] == 1


async def test_personal_ally_controls_reject_other_users_and_old_views(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    rig.repo.profiles[1]["collectibles"] = [recruit()]
    await rig.service.handle(update(rig.bot, text="/allies"), rig.context)
    nonce = rig.service.personal_views[(1, "allies")]["nonce"]
    data = f"p:1:{nonce}:allies:deploy:{'a' * 16}"
    await rig.service.handle(update(rig.bot, uid=2, data=data), rig.context)
    assert rig.repo.profiles[1]["active_ally_id"] is None
    await rig.service.handle(update(rig.bot, text="/allies"), rig.context)
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    assert rig.repo.profiles[1]["active_ally_id"] is None
    nonce = rig.service.personal_views[(1, "allies")]["nonce"]
    await rig.service.handle(update(rig.bot, data=f"p:1:{nonce}:allies:deploy:{'a' * 16}"), rig.context)
    assert rig.repo.profiles[1]["active_ally_id"] == "a" * 16


async def test_talent_offers_survive_restart_and_cannot_be_chosen_twice(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    rig.repo.profiles[1]["level"] = 2
    rig.ai.talents = AsyncMock(return_value=content.fallback_talents())
    await rig.service.handle(update(rig.bot, text="/progression"), rig.context)
    offers = copy.deepcopy(rig.repo.profiles[1]["talent_offers"])
    rig.service = BotService(rig.repo, rig.ai)
    await rig.service.handle(update(rig.bot, text="/progression"), rig.context)
    assert rig.repo.profiles[1]["talent_offers"] == offers
    rig.ai.talents.assert_awaited_once()
    nonce = rig.service.personal_views[(1, "talents")]["nonce"]
    data = f"p:1:{nonce}:talents:choose:2.0"
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    assert len(rig.repo.profiles[1]["talents"]) == 1


async def test_blueprint_confirmation_survives_delivery_failure_and_replay(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    profile = rig.repo.profiles[1]
    item = backpack(profile)
    profile["materials"] = 20
    rig.ai.forge = AsyncMock(return_value=None)
    await rig.service.request_blueprint(update(rig.bot), rig.context, item["id"])
    quote = copy.deepcopy(rig.repo.profiles[1]["craft_quote"])
    await rig.service.request_blueprint(update(rig.bot), rig.context, item["id"])
    rig.ai.forge.assert_awaited_once()
    rig.service = BotService(rig.repo, rig.ai)
    data = f"c:1:{quote['id']}:confirm"
    await rig.service.handle(update(rig.bot, uid=2, data=data), rig.context)
    assert rig.repo.profiles[1]["materials"] == 20
    rig.bot.send_message.side_effect = BadRequest("simulated delivery failure")
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    assert rig.repo.profiles[1]["materials"] == 12
    assert rig.repo.profiles[1]["inventory"][0]["rarity"] == "Gutter-Tech"
    rig.bot.send_message.side_effect = None
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    assert rig.repo.profiles[1]["materials"] == 12 and len(rig.repo.profiles[1]["inventory"]) == 1
    assert rig.repo.profiles[1]["inventory"][0]["id"] == quote["id"]


async def test_salvage_requires_its_own_exact_confirmation(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    item = backpack(rig.repo.profiles[1])
    await rig.service.handle(update(rig.bot, text="/inventory"), rig.context)
    nonce = rig.service.inventory_views[1]["nonce"]
    await rig.service.handle(update(rig.bot, data=f"i:1:{nonce}:confirm:{item['id']}"), rig.context)
    nonce = rig.service.inventory_views[1]["nonce"]
    await rig.service.handle(update(rig.bot, data=f"i:1:{nonce}:salvage:{item['id']}"), rig.context)
    assert rig.repo.profiles[1]["inventory"]
    await rig.service.handle(update(rig.bot, data=f"i:1:{nonce}:scrapask:{item['id']}"), rig.context)
    nonce = rig.service.inventory_views[1]["nonce"]
    data = f"i:1:{nonce}:salvage:{item['id']}"
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    await rig.service.handle(update(rig.bot, data=data), rig.context)
    assert not rig.repo.profiles[1]["inventory"] and rig.repo.profiles[1]["materials"] == 2


async def test_chapter_checkpoint_recovery_rejects_stale_branch_and_uses_ai_continuity(rig):
    await campaign_started(rig, (1, 2))
    rig.ai.assess = AsyncMock(
        return_value=CampaignAssessment(
            action_category="technology", skill_score=10, player_damage=0, event="objective_complete"
        )
    )
    rig.service.rng.randint = lambda lo, hi: hi
    await rig.service.handle(update(rig.bot, text="Finish the objective"), rig.context)
    assert rig.repo.states[100]["phase"] == "chapter_complete"
    before = copy.deepcopy(rig.repo.profiles)
    saved = copy.deepcopy(rig.repo.states[100])
    branch = game.callback_data(saved, "chapter", "1")
    rig.service = BotService(rig.repo, rig.ai)
    await rig.service.handle(update(rig.bot, text="/status"), rig.context)
    assert rig.repo.profiles == before
    next_chapter = content.Chapter.model_validate(saved["campaign"]["chapters"][1])
    next_chapter.title = "The Courier's Double"
    rig.ai.chapter = AsyncMock(return_value=next_chapter)
    await rig.service.handle(update(rig.bot, uid=2, data=branch), rig.context)
    rig.ai.chapter.assert_not_awaited()
    await rig.service.handle(update(rig.bot, data=branch), rig.context)
    await rig.service.handle(update(rig.bot, data=branch), rig.context)
    assert rig.repo.states[100]["phase"] == "preparation"
    assert rig.repo.states[100]["campaign"]["chapters"][1]["title"] == "The Courier's Double"
    rig.ai.chapter.assert_awaited_once()
    assert rig.repo.profiles == before
    assert all(p["stats"]["chapters_completed"] == 1 and p["materials"] == 5 for p in before.values())


async def test_campaign_ally_support_consumes_turn_and_is_not_a_free_heal(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    profile = rig.repo.profiles[1]
    profile["collectibles"] = [recruit()]
    profiles.deploy_ally(profile, "a" * 16)
    await campaign_started(rig)
    state = await rig.repo.load_state(100)
    state["players"][0]["hp"] = 10
    await rig.service.commit(100, state)
    await click(rig, "ally")
    assert rig.repo.states[100]["players"][0]["hp"] == 14 and rig.repo.states[100]["turn_id"] == 1
    await click(rig, "ally")
    assert rig.repo.states[100]["players"][0]["hp"] == 14 and rig.repo.states[100]["turn_id"] == 1


async def test_concurrent_craft_claims_spend_resources_once(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    profile = rig.repo.profiles[1]
    item = backpack(profile)
    profile["materials"] = 20
    quote = profiles.save_craft_quote(profile, item, profiles.forged_item(item), "q" * 16, 100, "fallback")
    results = await asyncio.gather(
        *[
            rig.repo.mutate_profile(1, lambda p: profiles.complete_craft(p, quote["id"]), "craft:" + quote["id"])
            for _ in range(6)
        ]
    )
    assert sum(r.applied for r in results) == 1
    assert rig.repo.profiles[1]["materials"] == 12 and len(rig.repo.profiles[1]["inventory"]) == 1


async def test_invalid_provider_mechanics_use_labeled_fallback_without_losing_lobby(rig):
    data = copy.deepcopy(designed_combat()["boss"]["design"])
    data["moves"][0]["power"] = 9999
    create = AsyncMock(return_value=response(json.dumps(data)))
    rig.service.ai = AIService(client=client_with(create))
    await begin(rig)
    await join_ready(rig)
    await click(rig, "start")
    state = rig.repo.states[100]
    assert state["phase"] == "combat" and state["boss"]["design_source"] == "fallback"
    assert all(move["power"] <= 8 for move in state["boss"]["design"]["moves"])
    assert rig.repo.profiles[1]["stats"]["bosses_attempted"] == 1
    create.assert_awaited_once()


async def test_ai_ally_design_is_saved_and_delivered_as_a_deployable_recruit(rig):
    design = content.AllyDesign(
        name="Nyx of the Signal",
        background="A covert signal medic.",
        support=content.SupportSkill(
            name="Quiet Repair", description="Restore your partner.", kind="heal", value=6, category="technology"
        ),
    )
    rig.ai.ally = AsyncMock(return_value=design)
    await rig.service.handle(update(rig.bot, text="/venture"), rig.context)
    await click(rig, "mode", "hire_help")
    ally = rig.repo.profiles[1]["collectibles"][0]
    assert ally["name"] == design.name and ally["support"] == design.support.model_dump()
    assert ally["design_source"] == "AI"
    await rig.service.handle(update(rig.bot, text="/allies"), rig.context)
    rig.ai.ally.assert_awaited_once()
    assert "Quiet Repair" in rig.bot.send_message.call_args.kwargs["text"]


async def test_saved_victory_story_and_report_do_not_regenerate_on_resume(rig):
    await begin(rig)
    await join_ready(rig)
    await click(rig, "start")
    state = await rig.repo.load_state(100)
    state["boss"]["hp"] = 1
    await rig.service.commit(100, state)
    rig.service.rng.randint = lambda lo, hi: hi
    rig.ai.victory = AsyncMock(return_value=content.VictoryStory(text="The city remembers the team's precise strike."))
    await click(rig, "ability", "0")
    saved = copy.deepcopy(rig.repo.states[100])
    assert saved["last_recap"]["participants"][0]["damage"] == 1
    assert saved["last_recap"]["story"]
    rig.service = BotService(rig.repo, rig.ai)
    await rig.service.handle(update(rig.bot, text="/status"), rig.context)
    await rig.service.handle(update(rig.bot, text="/recap"), rig.context)
    assert rig.repo.states[100] == saved
    rig.ai.victory.assert_awaited_once()


async def test_old_chapter_art_cannot_arrive_in_a_later_chapter(rig):
    await campaign_started(rig)
    snapshot = copy.deepcopy(rig.repo.states[100])
    snapshot["settings"]["images"] = True
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_image(*args):
        entered.set()
        await release.wait()
        return b"old chapter image"

    rig.ai.image = slow_image
    rig.service.schedule_scene(rig.bot, 100, snapshot)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        state = await rig.repo.load_state(100)
        state["campaign"]["index"] = 1
        game.start_chapter(state)
        await rig.service.commit(100, state)
    finally:
        pending = list(rig.service.tasks)
        release.set()
        await asyncio.gather(*pending)
    rig.bot.send_photo.assert_not_awaited()


async def test_cancelled_blueprint_keeps_resources_and_rejects_old_confirmation(rig):
    await rig.service.profile(update(rig.bot).effective_user)
    profile = rig.repo.profiles[1]
    item = backpack(profile)
    profile["materials"] = 20
    await rig.service.request_blueprint(update(rig.bot), rig.context, item["id"])
    quote = rig.repo.profiles[1]["craft_quote"]
    await rig.service.handle(update(rig.bot, data=f"c:1:{quote['id']}:cancel"), rig.context)
    await rig.service.handle(update(rig.bot, data=f"c:1:{quote['id']}:confirm"), rig.context)
    assert rig.repo.profiles[1]["materials"] == 20 and rig.repo.profiles[1]["inventory"][0] == item
    assert rig.repo.profiles[1]["craft_quote"] is None


async def test_personal_panels_fit_telegram_limits_with_long_ids_and_unicode(rig):
    uid = 2**52 - 1
    user_update = update(rig.bot, uid=uid)
    await rig.service.profile(user_update.effective_user)
    profile = rig.repo.profiles[uid]
    ally = recruit()
    ally["name"] = "🙂" * 80
    profile["collectibles"] = [ally]
    profile["level"] = 2
    await rig.service.handle(update(rig.bot, uid=uid, text="/allies"), rig.context)
    await rig.service.handle(update(rig.bot, uid=uid, text="/progression"), rig.context)
    for call in rig.bot.send_message.call_args_list:
        assert len(call.kwargs["text"].encode("utf-16-le")) // 2 <= 4096
        markup = call.kwargs.get("reply_markup")
        if markup:
            assert all(len(button.callback_data.encode()) <= 64 for row in markup.inline_keyboard for button in row)
