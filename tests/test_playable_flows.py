"""Press Telegram's rendered buttons, never fabricate fresh callbacks from state."""

import asyncio
import copy
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.error import BadRequest

from ai_service import CampaignAssessment
from bot_service import BotService
import bot_service
import game
from game_constants import FACTIONS, GAUNTLET_ROUTES
import gameplay_content as content
import main
import profiles


class TelegramUI:
    def __init__(self, rig, chat=100, thread=None):
        self.rig, self.chat, self.thread = rig, chat, thread
        self.messages = []
        self.sequence = 100
        self.bot_user = User(999, "GuildVenture", True, username=rig.bot.username)
        rig.context.application = SimpleNamespace(bot_data={"service": rig.service})
        rig.bot.send_message.side_effect = self.send
        rig.bot.edit_message_text.side_effect = self.edit

    def message(self, text, uid=None, message_id=None, markup=None, chat=None, thread=None):
        self.sequence += 1
        chat = self.chat if chat is None else chat
        message = Message(
            message_id or self.sequence,
            dt.datetime.now(dt.timezone.utc),
            Chat(chat, "private" if chat == 1 else "supergroup"),
            from_user=self.bot_user if uid is None else User(uid, f"Player {uid}", False),
            text=text,
            reply_markup=markup,
            message_thread_id=thread,
        )
        message.set_bot(self.rig.bot)
        return message

    async def send(self, **kwargs):
        message = self.message(
            kwargs["text"],
            markup=kwargs.get("reply_markup"),
            chat=kwargs["chat_id"],
            thread=kwargs.get("message_thread_id"),
        )
        self.messages.append(message)
        return message

    async def edit(self, **kwargs):
        old = next((m for m in self.messages if m.message_id == kwargs["message_id"]), None)
        if old is None:
            raise BadRequest("Message to edit not found")
        message = self.message(
            kwargs["text"],
            message_id=old.message_id,
            markup=kwargs.get("reply_markup"),
            chat=old.chat.id,
            thread=old.message_thread_id,
        )
        self.messages.append(message)
        return message

    async def command(self, text, uid=1, thread=None):
        message = self.message(text, uid, thread=self.thread if thread is None else thread)
        await main.dispatch(Update(self.sequence, message=message), self.rig.context)

    @property
    def latest(self):
        return self.messages[-1]

    @property
    def state(self):
        return self.rig.repo.states[self.chat]

    def find(self, *, label=None, action=None, argument=None, message=None):
        for candidate in [message] if message else reversed(self.messages):
            if not candidate.reply_markup:
                continue
            for row in candidate.reply_markup.inline_keyboard:
                for button in row:
                    data = button.callback_data
                    if label is not None and button.text.startswith(label):
                        return candidate, data
                    if action is not None and data.startswith("g:"):
                        parts = data.split(":", 4)
                        if parts[3] == action and (argument is None or parts[4] == argument):
                            return candidate, data
        raise AssertionError(f"No rendered button: {label or (action, argument)}; last message: {self.latest.text}")

    async def press(self, *, label=None, action=None, argument=None, uid=1, message=None, saved=None):
        message, data = saved or self.find(label=label, action=action, argument=argument, message=message)
        query = CallbackQuery(str(self.sequence), User(uid, f"Player {uid}", False), "chat", message=message, data=data)
        query.set_bot(self.rig.bot)
        await main.dispatch(Update(self.sequence, callback_query=query), self.rig.context)

    async def restart(self):
        self.rig.service = BotService(self.rig.repo, self.rig.ai, self.rig.service.rng, default_images=False)
        self.rig.context.application.bot_data["service"] = self.rig.service


@pytest.fixture
def ui(rig):
    # A modest, valid authored encounter tests reachability, not live AI balance.
    rig.ai.encounter = AsyncMock(
        return_value=content.EncounterDesign(
            name="Test Sentinel",
            description="A guarded checkpoint.",
            moves=[
                content.BossMove(
                    name="Signal",
                    telegraph="The scanner charges.",
                    kind="damage",
                    power=1,
                    target="actor",
                    counter_category="technology",
                ),
                content.BossMove(
                    name="Sweep",
                    telegraph="The scanner sweeps.",
                    kind="damage",
                    power=1,
                    target="party",
                    counter_category="stealth",
                ),
            ],
            phase_name="Alert",
            phase_telegraph="The scanner accelerates.",
            phase_threshold=50,
            phase_power_bonus=1,
        )
    )
    rig.service.rng.randint = lambda low, high: high
    return TelegramUI(rig)


async def lobby(ui, mode="gauntlet", route="default"):
    await ui.command("/venture")
    await ui.press(action="mode", argument=mode)
    if mode == "gauntlet":
        await ui.press(action="route", argument=route)
    assert ui.state["phase"] == "lobby"
    return ui.latest


async def join(ui, uid=1, faction="Nodewalker", panel=None):
    await ui.press(action="faction", argument=faction, uid=uid, message=panel)
    preview = ui.latest
    await ui.press(label="Choose this faction", uid=uid, message=preview)
    assert any(p["id"] == uid and p["faction"] == faction for p in ui.state["players"])
    assert "joined as" in ui.latest.text and ui.latest.message_id != preview.message_id


async def start(ui, party=(1,), mode="gauntlet", faction="Nodewalker"):
    panel = await lobby(ui, mode)
    for uid in party:
        await join(ui, uid, faction=faction, panel=panel)
        await ui.press(action="ready", argument="1", uid=uid, message=panel)
    await ui.press(action="start", message=panel)
    if mode == "open_campaign":
        await ui.press(action="chapter", argument="0")
        camp = ui.latest
        for uid in party:
            await ui.press(action="ready", argument="1", uid=uid, message=camp)
        await ui.press(action="start", message=camp)
    assert ui.state["phase"] == ("combat" if mode == "gauntlet" else "campaign")


async def finish_floor(ui):
    for _ in range(50):
        if ui.state["phase"] != "combat":
            break
        actor = ui.state["players"][ui.state["turn_index"]]
        available = [
            (a["effect"]["value"], i)
            for i, a in enumerate(actor["abilities"])
            if a["effect"]["type"] == "direct_damage" and a.get("charges", 1) > 0
        ]
        _, index = max(available)
        await ui.press(action="ability", argument=str(index), uid=actor["id"], message=ui.latest)
    assert ui.state["phase"] == "victory", ui.latest.text


async def test_faction_confirmation_survives_settings_change_and_gives_visible_next_step(ui):
    panel = await lobby(ui)
    await ui.press(action="faction", argument="Nodewalker", message=panel)
    preview = ui.latest
    await ui.press(action="images", message=panel)
    await ui.press(label="Choose this faction", message=preview)
    assert len(ui.state["players"]) == 1 and "joined as Nodewalker" in ui.latest.text
    joined = ui.latest
    await ui.press(action="ready", argument="1", message=joined)
    await ui.press(action="start", message=joined)
    assert ui.state["phase"] == "combat"


async def test_multiplayer_previews_and_shared_ready_buttons_survive_other_players(ui):
    panel = await lobby(ui)
    await ui.press(action="faction", argument="Nodewalker", message=panel)
    first = ui.latest
    await ui.press(action="faction", argument="Coinbroker", uid=2, message=panel)
    second = ui.latest
    await asyncio.gather(
        ui.press(label="Choose this faction", uid=1, message=first),
        ui.press(label="Choose this faction", uid=2, message=second),
    )
    assert {p["id"] for p in ui.state["players"]} == {1, 2}
    await asyncio.gather(*[ui.press(action="ready", argument="1", uid=uid, message=panel) for uid in (1, 2)])
    await ui.press(action="ready", argument="1", message=panel)  # Repeat must not unready.
    assert all(p["ready"] for p in ui.state["players"])
    await ui.press(action="start", uid=2, message=panel)
    assert ui.state["phase"] == "lobby" and "owner" in ui.latest.text
    await ui.press(action="start", message=panel)
    assert ui.state["phase"] == "combat" and ui.state["boss"]["max_hp"] == 80
    assert not ui.rig.service.locks and not ui.rig.service.lock_users
    await finish_floor(ui)
    await ui.press(action="bank", message=ui.latest)
    for uid in (1, 2):
        await ui.command("/rewards", uid)
        await ui.press(label="Gauntlet: Treasure", uid=uid, message=ui.latest)
        assert len(ui.rig.repo.profiles[uid]["inventory"]) == 1


@pytest.mark.parametrize("faction", list(FACTIONS))
@pytest.mark.parametrize("route", list(GAUNTLET_ROUTES))
async def test_every_faction_and_route_can_finish_a_solo_gauntlet_from_rendered_controls(ui, faction, route):
    ui.chat = 1
    panel = await lobby(ui, route=route)
    await join(ui, faction=faction, panel=panel)
    await ui.press(action="ready", argument="1", message=panel)
    await ui.press(action="start", message=panel)
    await finish_floor(ui)
    await ui.press(action="bank", message=ui.latest)
    await ui.command("/rewards")
    await ui.press(label="Gauntlet: Treasure", message=ui.latest)
    assert len(ui.rig.repo.profiles[1]["inventory"]) == 1
    await ui.command("/status")
    await ui.press(action="reset", message=ui.latest)
    assert ui.state["phase"] == "menu"


@pytest.mark.parametrize("party", [(1,), (1, 2, 3)])
@pytest.mark.parametrize("faction", list(FACTIONS))
async def test_campaign_all_chapters_turns_checkpoints_and_rewards(ui, party, faction):
    ui.thread = 321
    await start(ui, party, "open_campaign", faction=faction)
    original = copy.deepcopy(ui.state)
    await ui.command("/venture", uid=2, thread=999)
    assert ui.state == original and "another topic" in ui.latest.text
    await ui.command("/campaign")
    ui.rig.ai.assess = AsyncMock(
        return_value=CampaignAssessment(
            action_category="technology",
            skill_score=10,
            player_damage=0,
            event="none",
        )
    )
    for chapter in range(3):
        for uid in party:
            ui.rig.ai.assess.return_value = CampaignAssessment(
                action_category="technology",
                skill_score=10,
                player_damage=0,
                event="objective_complete" if uid == party[-1] else "milestone_reached",
                milestone_id="checkpoint",
            )
            await ui.command("/act@guildventure_test_bot Hack the relay and secure the exit", uid)
        assert len(ui.state["campaign"]["completed"]) == chapter + 1
        if chapter < 2:
            checkpoint = ui.latest
            await ui.restart()
            await ui.command("/recap")
            await ui.press(action="chapter", argument="1", message=checkpoint)
            camp = ui.latest
            for uid in party:
                await ui.press(action="ready", argument="1", uid=uid, message=camp)
            await ui.press(action="start", message=camp)
    assert ui.state["phase"] == "victory"
    await ui.press(action="bank", message=ui.latest)
    for uid in party:
        await ui.command("/rewards", uid)
        await ui.press(label="Alpha City: Character", uid=uid, message=ui.latest)
        assert len(ui.rig.repo.profiles[uid]["collectibles"]) == 1
        assert ui.rig.repo.profiles[uid]["stats"]["chapters_completed"] == 3


@pytest.mark.parametrize("mode, collection", [("hire_help", "collectibles"), ("dig_treasure", "inventory")])
async def test_free_activities_belong_to_clicking_player_without_stealing_menu(ui, mode, collection):
    await ui.command("/venture")
    original = copy.deepcopy(ui.state)
    await ui.command("/venture", uid=2)
    assert ui.state == original and ui.state["owner_id"] == 1
    roll = ui.find(action="mode", argument=mode)
    await ui.press(saved=roll, uid=2)
    assert len(ui.rig.repo.profiles[2][collection]) == 1 and ui.state["owner_id"] == 1
    await ui.press(saved=roll, uid=2)
    assert len(ui.rig.repo.profiles[2][collection]) == 1
    assert "current controls" in ui.latest.text
    await ui.press(action="mode", argument=mode, uid=2, message=ui.latest)
    assert len(ui.rig.repo.profiles[2][collection]) == 2


async def test_old_turn_is_not_replayed_and_current_buttons_are_delivered(ui):
    await start(ui)
    turn = ui.find(action="ability", argument="0", message=ui.latest)
    await ui.command("/status")
    await ui.restart()
    await ui.press(saved=turn)
    after = copy.deepcopy(ui.state)
    await ui.press(saved=turn)
    assert ui.state == after and "current controls" in ui.latest.text
    await ui.press(action="ability", argument="0", message=ui.latest)
    assert ui.state["turn_id"] == after["turn_id"] + 1


async def test_saved_event_recovery_does_not_expire_current_turn(ui):
    await start(ui)
    turn = ui.find(action="ability", argument="0", message=ui.latest)
    state = await ui.rig.repo.load_state(ui.chat)
    game.queue_event(state, state["players"][0], "recovered", xp=3)
    await ui.rig.repo.save_state(ui.chat, state)
    await ui.press(saved=turn)
    assert ui.state["turn_id"] == 1 and not ui.state["events"]


async def test_preparation_buttons_are_shared_but_do_not_cross_encounters(ui):
    await start(ui, (1, 2))
    await finish_floor(ui)
    await ui.press(action="continue", message=ui.latest)
    await ui.press(action="route", argument="default", message=ui.latest)
    camp = ui.latest
    for uid in (1, 2):
        await ui.press(action="rest", uid=uid, message=camp)
        await ui.press(action="ready", argument="1", uid=uid, message=camp)
    await ui.press(action="start", message=camp)
    assert ui.state["phase"] == "combat" and ui.state["gauntlet_level"] == 2
    await finish_floor(ui)
    await ui.press(action="continue", message=ui.latest)
    await ui.press(action="route", argument="default", message=ui.latest)
    before = copy.deepcopy(ui.state)
    await ui.press(action="ready", argument="1", message=camp)
    assert ui.state == before and not any(p["ready"] for p in ui.state["players"])


async def test_initial_start_explains_join_and_then_unready_players(ui):
    panel = await lobby(ui)
    await ui.press(action="start", message=panel)
    assert "One ready player is enough" in ui.latest.text
    await join(ui, panel=panel)
    await ui.press(action="start", message=panel)
    assert "Waiting for Ready: Player 1" in ui.latest.text
    await ui.press(action="ready", argument="1", message=ui.latest)
    await ui.press(action="start", message=ui.latest)
    assert ui.state["phase"] == "combat"


@pytest.mark.parametrize("category, damage_type", [("technology", "Enertech"), ("stealth", "Umbral")])
def test_ally_strikes_use_the_same_damage_types_as_equipment_and_boss_traits(category, damage_type):
    effect = game.support_effect({"kind": "strike", "value": 4, "category": category})
    player = game.make_player(1, "Alice", "Nodewalker", profiles.normalize({}))
    state = {"boss": {"strengths": [{"type": "damage_type_resistance", "damage_type": damage_type, "value": 0.5}]}}
    assert game.damage_to_boss(4, state, player, effect["damage_type"], category) == 2


async def test_act_command_validates_turn_recipient_input_and_provider_failure(ui):
    await start(ui, (1, 2), "open_campaign")
    before = copy.deepcopy(ui.state)
    ui.rig.ai.assess = AsyncMock(return_value=None)
    for text, uid in [("/act", 1), ("/act " + "x" * 1001, 1), ("/act hack", 2), ("/act@another_bot hack", 1)]:
        await ui.command(text, uid)
        assert ui.state == before
    ui.rig.ai.assess.assert_not_awaited()
    await ui.command("/act@guildventure_test_bot hack relay")
    assert ui.state == before and "narrator is unavailable" in ui.latest.text
    assert ui.rig.ai.assess.call_args.args[1] == "hack relay"
    ui.rig.ai.assess.return_value = CampaignAssessment(
        action_category="technology",
        skill_score=10,
        player_damage=0,
        event="none",
    )
    await ui.command("/act@guildventure_test_bot hack relay")
    assert ui.state["turn_id"] == 1 and ui.state["players"][ui.state["turn_index"]]["id"] == 2


@pytest.mark.parametrize(
    "action, argument",
    [
        ("tactic", "guard"),
        ("tactic", "counter"),
        ("boss", ""),
        ("environment", ""),
    ],
)
async def test_each_combat_control_can_be_pressed_from_the_delivered_panel(ui, action, argument):
    await start(ui)
    if action == "environment":
        await ui.press(action="ability", argument="1", message=ui.latest)
        assert game.environmental_available(ui.state)
    before = copy.deepcopy(ui.state)
    button = ui.find(action=action, argument=argument, message=ui.latest)
    await ui.press(saved=button, uid=2)
    if action != "boss":
        assert ui.state == before and "not your turn" in ui.latest.text
    await ui.press(saved=button)
    if action == "boss":
        assert ui.state == before and "Next:" in ui.latest.text
    else:
        assert ui.state["turn_id"] == before["turn_id"] + 1
        assert ui.latest.reply_markup


@pytest.mark.parametrize("faction", list(FACTIONS))
@pytest.mark.parametrize("index", [0, 1, 2])
async def test_each_faction_ability_is_usable_and_finite_charges_are_consumed(ui, faction, index):
    await start(ui, (1, 2), faction=faction)
    before = copy.deepcopy(ui.state)
    await ui.press(action="ability", argument=str(index), message=ui.latest)
    assert ui.state["turn_id"] == 1 and ui.state["players"][ui.state["turn_index"]]["id"] == 2
    ability = before["players"][0]["abilities"][index]
    if "charges" in ability:
        assert ui.state["players"][0]["abilities"][index]["charges"] == ability["charges"] - 1


@pytest.mark.parametrize("mode", ["gauntlet", "open_campaign"])
@pytest.mark.parametrize("kind", ["strike", "heal", "focus"])
async def test_ally_deploy_support_depletion_and_turn_rotation(ui, mode, kind):
    await ui.command("/profile")
    profile = ui.rig.repo.profiles[1]
    profile["collectibles"] = [
        {
            "id": "a" * 16,
            "name": "Ally",
            "faction": "Nodewalker",
            "rarity": "Salvage",
            "bond": 0,
            "background": "A contact",
            "support": {
                "name": "Assist",
                "description": "Help the player",
                "kind": kind,
                "value": 3,
                "category": "technology",
            },
        }
    ]
    await ui.command("/allies")
    await ui.press(label="Deploy: Ally", message=ui.latest)
    assert ui.rig.repo.profiles[1]["active_ally_id"] == "a" * 16
    await start(ui, (1, 2), mode)
    button = ui.find(action="ally", message=ui.latest)
    await ui.press(saved=button)
    assert ui.state["players"][0]["ally"]["charges"] == 0
    assert ui.state["players"][ui.state["turn_index"]]["id"] == 2
    before = copy.deepcopy(ui.state)
    await ui.press(saved=button)
    assert ui.state == before


async def test_loadout_talents_and_gear_use_real_personal_buttons_and_require_readiness_again(ui):
    panel = await lobby(ui)
    await join(ui, panel=panel)
    await ui.press(action="ready", argument="1", message=panel)
    profile = ui.rig.repo.profiles[1]
    profile["level"] = 2
    item = profiles.make_item("Test module", "Cranial", "Enertech", "Salvage", "Recovered")
    item["id"] = "i" * 16
    profile["inventory"].append(item)
    await ui.command("/inventory")
    await ui.press(label="Test module", message=ui.latest)
    await ui.press(label="Equip", message=ui.latest)
    await ui.command("/progression")
    talent = ui.find(label="Choose ", message=ui.latest)
    await ui.press(saved=talent, uid=2)
    assert not profile["talents"]
    await ui.press(saved=talent)
    await ui.press(action="start", message=panel)
    assert ui.state["phase"] == "lobby" and not ui.state["players"][0]["ready"]
    await ui.press(action="ready", argument="1", message=ui.latest)
    await ui.press(action="start", message=ui.latest)
    actor = ui.state["players"][0]
    assert actor["max_hp"] > FACTIONS["Nodewalker"]["hp"]
    await ui.press(label="Synaptic Surge", message=ui.latest)
    assert ui.state["players"][0]["abilities"][-1]["charges"] == 0


async def test_inventory_workshop_confirmation_cancellation_salvage_and_receipt_replay(ui):
    await ui.command("/profile")
    item = profiles.make_item("Source module", "Cranial", "Enertech", "Salvage", "Recovered")
    item["id"] = "i" * 16
    ui.rig.repo.profiles[1].update(inventory=[item], materials=20)
    await ui.command("/inventory")
    await ui.press(label="Source module", message=ui.latest)
    await ui.press(label="Upgrade blueprint", message=ui.latest)
    confirm = ui.find(label="Craft —", message=ui.latest)
    await ui.restart()
    await ui.press(saved=confirm, uid=2)
    assert ui.rig.repo.profiles[1]["materials"] == 20
    await ui.press(saved=confirm)
    await ui.press(saved=confirm)
    assert ui.rig.repo.profiles[1]["materials"] == 12
    await ui.command("/inventory")
    await ui.press(label="Refined Source module", message=ui.latest)
    await ui.press(label="Discard…", message=ui.latest)
    await ui.press(label="Cancel", message=ui.latest)
    assert ui.rig.repo.profiles[1]["inventory"]
    await ui.press(label="Salvage…", message=ui.latest)
    salvage = ui.find(label="Confirm salvage", message=ui.latest)
    await ui.press(saved=salvage)
    await ui.press(saved=salvage)
    assert ui.rig.repo.profiles[1]["materials"] == 16 and not ui.rig.repo.profiles[1]["inventory"]


@pytest.mark.parametrize("survivor", [False, True])
async def test_party_deaths_rotate_to_survivors_or_allow_bank_and_new_run(ui, survivor):
    await start(ui, (1, 2, 3))
    state = await ui.rig.repo.load_state(ui.chat)
    for p in state["players"]:
        p["hp"] = p["max_hp"] if survivor and p["id"] == 3 else 1
    state["boss"]["intent"].update(target="party", power=4)
    await ui.rig.repo.save_state(ui.chat, state)
    await ui.command("/status")
    await ui.press(action="tactic", argument="guard", message=ui.latest)
    if survivor:
        assert [p["id"] for p in ui.state["players"]] == [3] and ui.state["turn_index"] == 0
        assert ui.state["phase"] == "combat"
        await ui.press(action="ability", argument="1", uid=3, message=ui.latest)
        assert ui.state["turn_id"] == 2
        await ui.command("/endgame")
    else:
        assert ui.state["phase"] == "defeat" and not ui.state["players"]
        await ui.press(action="reset", message=ui.latest)
        assert all(ui.rig.repo.profiles[uid]["pending_rewards"] for uid in (1, 2, 3))
    assert ui.state["phase"] == "menu"


async def test_leave_rejoin_and_explicit_not_ready_are_recoverable(ui):
    panel = await lobby(ui)
    await join(ui, panel=panel)
    await ui.press(action="ready", argument="1", message=panel)
    await ui.press(action="ready", argument="0", message=panel)
    await ui.press(action="ready", argument="0", message=panel)
    assert not ui.state["players"][0]["ready"]
    await ui.press(action="leave", message=panel)
    assert not ui.state["players"]
    await join(ui, faction="Chainbreaker", panel=panel)
    await ui.press(action="ready", argument="1", message=panel)
    await ui.press(action="start", message=panel)
    assert ui.state["players"][0]["faction"] == "Chainbreaker"


async def test_legacy_expired_confirmation_recovers_without_resetting_the_lobby(ui):
    panel = await lobby(ui)
    # The deployed version encoded only its database revision.
    legacy = (panel, f"g:{ui.state['run_id']}:{ui.state['_revision']}:choose:Nodewalker")
    await ui.press(action="images", message=panel)
    before = copy.deepcopy(ui.state)
    await ui.press(saved=legacy)
    assert ui.state == before and "current controls" in ui.latest.text
    await join(ui, panel=ui.latest)


async def test_delivery_failure_after_join_can_recover_with_the_same_confirmation(ui):
    await lobby(ui)
    await ui.press(action="faction", argument="Nodewalker")
    choose = ui.find(label="Choose this faction", message=ui.latest)
    ui.rig.bot.send_message.side_effect = BadRequest("Simulated delivery failure")
    await ui.press(saved=choose)
    assert len(ui.state["players"]) == 1
    ui.rig.bot.send_message.side_effect = ui.send
    await ui.press(saved=choose)
    assert len(ui.state["players"]) == 1 and "joined as" in ui.latest.text
    await ui.press(action="ready", argument="1", message=ui.latest)
    await ui.press(action="start", message=ui.latest)
    assert ui.state["phase"] == "combat"


async def test_waiting_callback_timeout_or_cancellation_does_not_replace_held_chat_lock(ui, monkeypatch):
    await start(ui, (1, 2), "open_campaign")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()
        return None

    ui.rig.ai.assess = slow
    first = asyncio.create_task(ui.command("/act hack relay"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        held = ui.rig.service.locks[ui.chat]
        monkeypatch.setattr(bot_service, "CALLBACK_WAIT_SECONDS", 0.01)
        # An old lobby button queues, times out, and must not disturb the active turn.
        await ui.press(action="ready", argument="1", uid=2)
        assert "still resolving" in ui.latest.text and ui.rig.service.locks[ui.chat] is held
        monkeypatch.setattr(bot_service, "CALLBACK_WAIT_SECONDS", 3)
        queued = asyncio.create_task(ui.press(action="ready", argument="1", uid=2))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert held.locked() and ui.rig.service.lock_users[ui.chat] == 1
    finally:
        release.set()
        await first
    assert not ui.rig.service.locks and not ui.rig.service.lock_users and ui.state["turn_id"] == 0


@pytest.mark.parametrize("use_command", [False, True])
async def test_banking_earned_rewards_is_available_during_next_floor_scouting(ui, use_command):
    await start(ui)
    await finish_floor(ui)
    await ui.press(action="continue", message=ui.latest)
    assert ui.state["phase"] == "scout"
    if use_command:
        await ui.command("/endgame")
        assert ui.state["phase"] == "menu"
    else:
        await ui.press(action="bank", message=ui.latest)
        assert ui.state["phase"] == "rewards"
    assert len(ui.rig.repo.profiles[1]["pending_rewards"]) == 1


async def test_idle_menu_allows_explicit_new_host_but_never_takeover_of_active_game(ui):
    await ui.command("/venture")
    old_host_control = ui.find(action="host", message=ui.latest)
    await ui.press(saved=old_host_control, uid=2)
    assert ui.state["owner_id"] == 2
    await ui.press(action="mode", argument="open_campaign", uid=2, message=ui.latest)
    before = copy.deepcopy(ui.state)
    await ui.press(saved=old_host_control)
    assert ui.state == before
    # Even a manufactured current token must obey the idle-menu restriction.
    current = (ui.latest, game.callback_data(ui.state, "host"))
    await ui.press(saved=current)
    assert ui.state == before and "not available" in ui.latest.text


async def test_slow_narration_does_not_hide_the_already_saved_combat_result(ui):
    await start(ui)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()
        return None

    ui.rig.ai.narrate = slow
    task = asyncio.create_task(ui.press(action="ability", argument="0", message=ui.latest))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert ui.state["turn_id"] == 1 and ui.latest.text == ui.state["last_result"]
        assert not task.done()
    finally:
        release.set()
        await task
    assert ui.latest.reply_markup and not ui.rig.service.locks
