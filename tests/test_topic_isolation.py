"""An existing chat session must never speak or act in another Telegram topic."""

import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

import bot_service
import game
import main
from conftest import update
from database import PersistenceError
from test_playable_flows import TelegramUI


BOT_METHODS = ("send_message", "send_photo", "edit_message_text", "answer_callback_query")


def clear_delivery(rig):
    for name in BOT_METHODS:
        getattr(rig.bot, name).reset_mock()


def assert_silent(rig):
    for name in BOT_METHODS:
        getattr(rig.bot, name).assert_not_awaited()


async def deliver(rig, **kwargs):
    await main.dispatch(update(rig.bot, **kwargs), rig.context)


@pytest.mark.parametrize("thread", [None, 321])
@pytest.mark.parametrize("phase", ["menu", "lobby", "combat", "campaign", "rewards", "defeat"])
async def test_all_commands_and_conversation_are_silent_outside_saved_topic(rig, thread, phase):
    ui = TelegramUI(rig, thread=thread)
    state = game.new_state(1, thread)
    state.update(phase=phase, schema_version=2)
    game.queue_event(state, {"id": 1, "username": "Owner"}, "attempt", 25, {"bosses_attempted": 1})
    await rig.repo.save_state(ui.chat, state)
    before = copy.deepcopy(rig.repo.states)
    await ui.restart()  # The boundary comes from persistence, not a warm UI cache.
    rig.service.ai = Mock()
    rig.repo.save_state = AsyncMock(wraps=rig.repo.save_state)
    rig.repo.mutate_profile = AsyncMock(wraps=rig.repo.mutate_profile)
    messages = ["hello everyone", "", "/unknown", "/act hack the relay", "/status@guildventure_test_bot"]
    messages += [f"/{command}" for command, _ in main.COMMANDS]
    for outside in (999, None if thread is not None else 321):
        for uid in (1, 2):  # Even the game owner must not get off-topic responses.
            for text in messages:
                await deliver(rig, uid=uid, text=text, thread=outside)
    assert_silent(rig)
    assert rig.repo.states == before and rig.repo.profiles == {} and rig.repo.events == {}
    rig.repo.save_state.assert_not_awaited()  # No legacy migration or event cleanup.
    rig.repo.mutate_profile.assert_not_awaited()
    assert not rig.service.ai.mock_calls
    assert not rig.service.locks and not rig.service.lock_users


@pytest.mark.parametrize("thread", [None, 321])
async def test_all_callback_families_are_ignored_without_acknowledgement_outside_topic(rig, thread):
    ui = TelegramUI(rig, thread=thread)
    await ui.command("/venture")
    await ui.press(action="mode", argument="open_campaign")
    payloads = [
        ui.find(action="faction", argument="Nodewalker")[1],
        game.callback_data(ui.state, "choose", "Nodewalker"),
        "g:old:0:ready:1",
        "i:1:nonce:equip:item",
        "p:1:nonce:deploy:ally",
        "c:1:blueprint:confirm",
        "r:1:reward:item",
        "malformed",
    ]
    before = copy.deepcopy((rig.repo.states, rig.repo.profiles, rig.repo.events))
    await ui.restart()
    clear_delivery(rig)
    for outside in (999, None if thread is not None else 321):
        for uid in (1, 2):
            for data in payloads:
                await deliver(rig, uid=uid, data=data, thread=outside)
    assert_silent(rig)
    assert (rig.repo.states, rig.repo.profiles, rig.repo.events) == before
    # The same rendered faction control still works in the original topic.
    await deliver(rig, data=payloads[0], thread=thread)
    rig.bot.answer_callback_query.assert_awaited_once()
    assert "Choose this faction" in str(ui.latest.reply_markup)


async def test_other_topics_stay_silent_while_an_action_holds_the_chat_lock(rig, monkeypatch):
    ui = TelegramUI(rig, thread=321)
    await ui.command("/venture")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()

    rig.service.command_or_action = AsyncMock(side_effect=slow)
    first = asyncio.create_task(ui.command("/status"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        clear_delivery(rig)
        monkeypatch.setattr(bot_service, "CALLBACK_WAIT_SECONDS", 0.01)
        for outside in (None, 999):
            await deliver(rig, text="ordinary conversation", thread=outside)
            await deliver(rig, text="/status", thread=outside)
            await deliver(rig, data=ui.find(action="mode")[1], thread=outside)
        assert_silent(rig)
        rig.service.command_or_action.assert_awaited_once()
        assert rig.service.lock_users[ui.chat] == 1
        # Busy feedback still works inside the game's topic.
        await ui.command("/status")
        assert "being resolved" in ui.latest.text and ui.latest.message_thread_id == 321
    finally:
        release.set()
        await first
    assert not rig.service.locks and not rig.service.lock_users


async def test_other_topics_stay_silent_during_first_session_creation(rig):
    ui = TelegramUI(rig, thread=321)
    entered, release = asyncio.Event(), asyncio.Event()
    mutate = rig.repo.mutate_profile

    async def slow(*args, **kwargs):
        entered.set()
        await release.wait()
        return await mutate(*args, **kwargs)

    rig.repo.mutate_profile = AsyncMock(side_effect=slow)
    first = asyncio.create_task(ui.command("/venture"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not rig.repo.states  # The new topic binding is not yet committed.
        for outside in (None, 999):
            await deliver(rig, text="/venture", thread=outside)
            await deliver(rig, text="hello", thread=outside)
            await deliver(rig, data="g:old:0:mode:gauntlet", thread=outside)
        assert_silent(rig)
        rig.repo.mutate_profile.assert_awaited_once()
    finally:
        release.set()
        await first
    assert ui.state["thread_id"] == 321 and ui.state["owner_id"] == 1
    assert all(m.message_thread_id == 321 for m in ui.messages)
    assert not rig.service.locks and not rig.service.lock_users


@pytest.mark.parametrize("error", [PersistenceError("read outage"), RuntimeError("unexpected read failure")])
async def test_unverified_topic_fails_silently_and_recovers_after_storage_outage(rig, error):
    ui = TelegramUI(rig, thread=321)
    await ui.command("/venture")
    before = copy.deepcopy((rig.repo.states, rig.repo.profiles, rig.repo.events))
    load = rig.repo.load_state
    rig.repo.load_state = AsyncMock(side_effect=error)
    clear_delivery(rig)
    for thread in (None, 999, 321):
        await deliver(rig, text="/profile", thread=thread)
        await deliver(rig, data=ui.find(action="mode")[1], thread=thread)
    assert_silent(rig)
    assert (rig.repo.states, rig.repo.profiles, rig.repo.events) == before
    assert not rig.service.locks and not rig.service.lock_users
    rig.repo.load_state = load
    await ui.command("/status")
    assert ui.latest.message_thread_id == 321 and ui.latest.reply_markup


async def test_verified_in_topic_action_errors_still_have_recovery_feedback(rig):
    ui = TelegramUI(rig, thread=321)
    await ui.command("/venture")
    rig.repo.mutate_profile = AsyncMock(side_effect=PersistenceError("write outage"))
    clear_delivery(rig)
    await ui.command("/profile")
    assert "Storage is temporarily unavailable" in ui.latest.text
    assert ui.latest.message_thread_id == 321
    rig.bot.send_message.assert_awaited_once()


async def test_ending_a_run_keeps_its_topic_without_blocking_independent_chats(rig):
    ui = TelegramUI(rig, thread=321)
    await ui.command("/venture")
    await ui.command("/endgame")
    await ui.restart()
    clear_delivery(rig)
    await deliver(rig, text="/profile", thread=999)
    await deliver(rig, text="/venture", thread=None)
    assert_silent(rig)
    await ui.command("/profile")
    await deliver(rig, uid=2, chat=200, text="/venture", thread=999)
    await deliver(rig, uid=3, chat=3, text="/venture")
    assert rig.repo.states[100]["thread_id"] == 321
    assert rig.repo.states[200]["thread_id"] == 999
    assert rig.repo.states[3]["thread_id"] is None
    assert all(m.chat.id != 100 or m.message_thread_id == 321 for m in ui.messages)
