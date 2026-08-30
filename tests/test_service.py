import asyncio
import copy
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

import game
import profiles
from ai_service import CampaignAssessment
from conftest import update
from database import PersistenceError


async def click(rig, action, arg="", uid=1, chat=100, thread=None):
    state = await rig.repo.load_state(chat)
    await rig.service.handle(
        update(rig.bot, uid, chat, data=game.callback_data(state, action, arg), thread=thread), rig.context
    )


async def begin(rig, mode="gauntlet", chat=100):
    await rig.service.handle(update(rig.bot, chat=chat, text="/venture"), rig.context)
    await click(rig, "mode", mode, chat=chat)
    if mode == "gauntlet":
        await click(rig, "route", "default", chat=chat)


async def join_ready(rig, uid=1, chat=100):
    await click(rig, "choose", "Nodewalker", uid, chat)
    await click(rig, "ready", uid=uid, chat=chat)


@pytest.mark.asyncio
async def test_two_players_join_ready_and_owner_starts(rig):
    await begin(rig)
    await join_ready(rig, 1)
    await join_ready(rig, 2)
    assert rig.repo.states[100]["phase"] == "lobby"
    await click(rig, "start", uid=2)
    assert rig.repo.states[100]["phase"] == "lobby"
    await click(rig, "start")
    state = rig.repo.states[100]
    assert state["phase"] == "combat" and len(state["players"]) == 2
    assert state["boss"]["hp"] == 80
    assert rig.repo.profiles[1]["stats"]["bosses_attempted"] == 1
    before = copy.deepcopy(state)
    await rig.service.handle(update(rig.bot, uid=3, text="/join"), rig.context)
    assert rig.repo.states[100] == before


async def test_stale_or_foreign_buttons_cannot_change_run(rig):
    await rig.service.handle(update(rig.bot, text="/venture"), rig.context)
    old = game.callback_data(rig.repo.states[100], "mode", "gauntlet")
    await begin(rig)
    before = copy.deepcopy(rig.repo.states[100])
    await rig.service.handle(update(rig.bot, uid=2, data=old), rig.context)
    await click(rig, "route", "adrenal", uid=2)
    assert rig.repo.states[100] == before


async def test_outbox_recovery_does_not_double_award(rig):
    state = game.new_state(1, None)
    for uid in [1, 2]:
        game.queue_event(state, {"id": uid, "username": str(uid)}, "attempt", 25, {"bosses_attempted": 1})
    rig.repo.fail_event = state["events"][1]["id"]
    with pytest.raises(PersistenceError):
        await rig.service.commit(100, state)
    assert rig.repo.profiles[1]["current_xp"] == 25
    assert len(rig.repo.states[100]["events"]) == 2
    rig.repo.fail_event = None
    recovered = await rig.service.state(100)
    await rig.service.flush_events(100, recovered)
    assert rig.repo.profiles[1]["current_xp"] == 25
    assert rig.repo.profiles[2]["current_xp"] == 25
    assert rig.repo.states[100]["events"] == []


async def test_read_failure_does_not_replace_state_or_profile(rig):
    rig.repo.fail_state = True
    await rig.service.handle(update(rig.bot, text="/venture"), rig.context)
    assert rig.repo.profiles == {} and rig.repo.states == {}


async def banked(rig):
    await begin(rig)
    await join_ready(rig, 1)
    await join_ready(rig, 2)
    state = await rig.repo.load_state(100)
    state["phase"] = "victory"
    state["gauntlet_bonus_attempted"] = 1
    state["gauntlet_bonus_defeated"] = 1
    await rig.service.commit(100, state)
    await click(rig, "bank")
    return next(iter(rig.repo.profiles[1]["pending_rewards"]))


async def test_each_player_has_independent_durable_claim(rig):
    reward_id = await banked(rig)
    await rig.service.handle(update(rig.bot, uid=2, data=f"r:1:{reward_id}:item"), rig.context)
    assert rig.repo.profiles[1]["inventory"] == []
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:item"), rig.context)
    assert len(rig.repo.profiles[1]["inventory"]) == 1
    assert len(rig.repo.profiles[2]["pending_rewards"]) == 1
    profile = copy.deepcopy(rig.repo.profiles[1])
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:character"), rig.context)
    assert rig.repo.profiles[1] == profile
    await rig.service.handle(update(rig.bot, text="/endgame"), rig.context)
    assert len(rig.repo.profiles[2]["pending_rewards"]) == 1


async def test_failed_delivery_keeps_reward_and_resend_does_not_regrant(rig):
    reward_id = await banked(rig)
    state = await rig.repo.load_state(100)
    state["settings"]["images"] = True
    await rig.service.commit(100, state)
    rig.ai.image = AsyncMock(return_value=b"fake image")
    rig.bot.send_photo.side_effect = BadRequest("simulated failed upload")
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:item"), rig.context)
    assert len(rig.repo.profiles[1]["inventory"]) == 1
    assert any("Reward claimed" in call.kwargs.get("text", "") for call in rig.bot.send_message.call_args_list)
    before = copy.deepcopy(rig.repo.profiles[1])
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:retry"), rig.context)
    assert rig.repo.profiles[1] == before
    assert rig.ai.image.await_count == 1


async def test_flavor_failure_keeps_reserved_reward_recoverable(rig):
    reward_id = await banked(rig)
    rig.ai.flavor = AsyncMock(side_effect=RuntimeError("simulated interruption"))
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:item"), rig.context)
    assert reward_id in rig.repo.profiles[1]["pending_rewards"]
    assert not rig.repo.profiles[1]["inventory"]
    reserved = copy.deepcopy(rig.repo.events[1, f"reserve:{reward_id}"])
    rig.ai.flavor = AsyncMock(return_value=None)
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:item"), rig.context)
    assert rig.repo.profiles[1]["inventory"][0]["roll"] == reserved["roll"]


async def test_character_reward_persists_without_adding_combat_role(rig):
    reward_id = await banked(rig)
    await rig.service.handle(update(rig.bot, data=f"r:1:{reward_id}:character"), rig.context)
    assert len(rig.repo.profiles[1]["collectibles"]) == 1
    assert rig.repo.profiles[1]["collectibles"][0]["id"] == reward_id


async def test_inventory_confirmation_expires_after_another_view(rig):
    user = update(rig.bot).effective_user
    await rig.service.profile(user)
    item = profiles.make_item("A", "Cranial", "Neural", "Salvage", "")
    item["id"] = "a" * 16
    rig.repo.profiles[1]["inventory"] = [item]
    await rig.service.handle(update(rig.bot, text="/inventory"), rig.context)
    view = rig.service.inventory_views[1]
    await rig.service.handle(update(rig.bot, data=f"i:1:{view['nonce']}:confirm:{item['id']}"), rig.context)
    old_nonce = rig.service.inventory_views[1]["nonce"]
    await rig.service.handle(update(rig.bot, text="/inventory"), rig.context)
    await rig.service.handle(update(rig.bot, data=f"i:1:{old_nonce}:discard:{item['id']}"), rig.context)
    assert len(rig.repo.profiles[1]["inventory"]) == 1
    view = rig.service.inventory_views[1]
    await rig.service.handle(update(rig.bot, uid=2, data=f"i:1:{view['nonce']}:equip:{item['id']}"), rig.context)
    assert rig.repo.profiles[1]["equipped_items"]["Cranial"] is None


async def test_status_recreates_controls_after_service_restart(rig):
    from bot_service import BotService

    await begin(rig)
    restarted = BotService(rig.repo, rig.ai)
    await restarted.handle(update(rig.bot, text="/status"), rig.context)
    assert restarted.panels
    markup = rig.bot.send_message.call_args.kwargs["reply_markup"]
    assert all(b.callback_data.startswith("g:") for row in markup.inline_keyboard for b in row)


async def test_second_chat_responds_while_first_waits_for_ai(rig):
    await begin(rig, "open_campaign")
    await join_ready(rig)
    await click(rig, "start")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()
        return None

    rig.ai.assess = slow
    first = asyncio.create_task(rig.service.handle(update(rig.bot, text="hack terminal"), rig.context))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(rig.service.handle(update(rig.bot, uid=2, chat=200, text="/profile"), rig.context), 1)
        assert 2 in rig.repo.profiles
        assert not first.done()
    finally:
        release.set()
        await first
    assert rig.repo.states[100]["turn_id"] == 0


async def test_campaign_completion_unlocks_banking(rig):
    await begin(rig, "open_campaign")
    await join_ready(rig)
    await click(rig, "start")
    rig.ai.assess = AsyncMock(
        return_value=CampaignAssessment(
            action_category="technology", skill_score=10, player_damage=0, event="objective_complete"
        )
    )
    rig.service.rng.randint = lambda low, high: high
    await rig.service.handle(update(rig.bot, text="extract key and leave"), rig.context)
    assert rig.repo.states[100]["phase"] == "victory"
    await click(rig, "bank")
    assert rig.repo.profiles[1]["pending_rewards"]


async def test_formatting_never_interprets_user_names_as_markup(rig):
    await rig.service.handle(update(rig.bot, text="/profile"), rig.context)
    call = rig.bot.send_message.call_args.kwargs
    assert "*[safe]" in call["text"] and call["parse_mode"] is None


async def test_background_scene_drops_after_run_changes(rig):
    await begin(rig)
    await join_ready(rig)
    await click(rig, "start")
    snapshot = copy.deepcopy(rig.repo.states[100])
    snapshot["settings"]["images"] = True
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_image(*args):
        entered.set()
        await release.wait()
        return b"image"

    rig.ai.image = slow_image
    rig.service.schedule_scene(rig.bot, 100, snapshot)
    await entered.wait()
    await rig.service.handle(update(rig.bot, text="/endgame"), rig.context)
    pending = list(rig.service.tasks)
    release.set()
    await asyncio.gather(*pending)
    rig.bot.send_photo.assert_not_awaited()


async def test_status_resends_saved_result_and_fresh_controls_without_mutation(rig):
    await begin(rig)
    await join_ready(rig)
    await click(rig, "start")
    await click(rig, "ability", "0")
    before = copy.deepcopy(rig.repo.states[100])
    rig.bot.send_message.reset_mock()
    rig.bot.edit_message_text.reset_mock()
    await rig.service.handle(update(rig.bot, text="/status"), rig.context)
    assert rig.repo.states[100] == before
    assert rig.bot.send_message.call_args_list[0].kwargs["text"] == "Last saved action:\n" + before["last_result"]
    assert rig.bot.send_message.call_args_list[-1].kwargs["reply_markup"]
    rig.bot.edit_message_text.assert_not_awaited()


async def test_unknown_legacy_session_is_preserved_for_manual_inspection(rig):
    rig.repo.states[100] = {"game_stage": "UNKNOWN", "_revision": 3, "owner_id": 1}
    before = copy.deepcopy(rig.repo.states[100])
    await rig.service.handle(update(rig.bot, text="/venture"), rig.context)
    assert rig.repo.states[100] == before and rig.repo.profiles == {}
    assert "maintainer" in rig.bot.send_message.call_args.kwargs["text"]
