"""Offline reproductions for the August 2026 repository review.

Run: python audit/offline_reproductions.py
PASS means the described existing defect was reproduced, not that it is fixed.
Compiles original function bodies via AST, without importing main.py or starting
the bot. Telegram, OpenAI, and PostgreSQL are replaced with in-memory doubles.
No credentials, third-party packages, network access, or live state are used.
This is an audit harness, not a replacement for future integration tests.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import copy
import datetime
import json
import logging
import os
from pathlib import Path
import random
import re
import subprocess
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "2451f1485e9934efca7ff3d4d46f606e53554559"


def compile_source(filename, namespace, functions_only=False, skip_imports=()):
    source = subprocess.run(
        ["git", "show", f"{BASE_REVISION}:{filename}"], cwd=ROOT,
        check=True, capture_output=True, encoding="utf-8",
    ).stdout
    tree = ast.parse(source)
    if functions_only:
        tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    else:
        tree.body = [n for n in tree.body if not (
            isinstance(n, ast.ImportFrom) and n.module in skip_imports)]
    tree.body.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    exec(compile(ast.fix_missing_locations(tree), str(ROOT / filename), "exec"), namespace)
    return namespace


class Button:
    def __init__(self, text, callback_data):
        self.text, self.callback_data = text, callback_data


class Message:
    def __init__(self, sink, thread=None):
        self.sink = sink
        self.message_thread_id = thread
        self.message_id = 10
        self.chat = SimpleNamespace(id=100)
        self.text = "hack the terminal"

    async def reply_text(self, text, **kwargs):
        self.sink.append(("reply", text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.sink.append(("edit", text, kwargs))
        return self


class Query:
    def __init__(self, user, data, message):
        self.from_user, self.data, self.message = user, data, message
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        return await self.message.edit_text(text, **kwargs)


class MemoryStore:
    def __init__(self):
        self.states, self.profiles = {}, {}

    async def load_state(self, chat):
        return copy.deepcopy(self.states.get(chat, {}))

    async def save_state(self, chat, state):
        self.states[chat] = copy.deepcopy(state)

    async def load_profile(self, user):
        return copy.deepcopy(self.profiles.get(user))

    async def save_profile(self, user, profile):
        self.profiles[user] = copy.deepcopy(profile)


class ExistingDefects(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.messages, self.requests = [], []
        self.rng = random.Random(12)
        self.response = {
            "name": "Audit Item", "background": "Offline fixture.",
            "boss_name": "Audit Boss", "boss_description": "Offline fixture.",
            "objective": "Reach the terminal.", "opening_scene": "A terminal waits.",
            "player_narrative": "Action resolved.", "boss_narrative": "Boss waits.",
            "boss_damage": 0, "player_damage": {},
        }

        async def no_wait(*args, **kwargs):
            pass

        async def send(**kwargs):
            self.messages.append(("send", kwargs.get("text", kwargs.get("caption", "")), kwargs))
            return Message(self.messages)

        async def gpt(**kwargs):
            self.requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.response)))])

        async def no_image(*args):
            return None

        logger = logging.getLogger("offline_audit")
        logger.disabled = True
        self.ns = dict(os=os, json=json, copy=copy, datetime=datetime, re=re,
                       random=self.rng, base64=base64, logger=logger,
                       asyncio=SimpleNamespace(sleep=no_wait), db_layer=self.store,
                       InlineKeyboardButton=Button, InlineKeyboardMarkup=lambda rows: rows,
                       RetryAfter=type("RetryAfter", (Exception,), {}))
        compile_source("game_constants.py", self.ns, skip_imports=("telegram",))
        for name, file in (("item_traits", "item_traits.py"), ("prompts", "prompts.py")):
            module_ns = {"LORE_SUMMARY": self.ns["LORE_SUMMARY"]}
            compile_source(file, module_ns, skip_imports=("game_constants",))
            self.ns[name] = SimpleNamespace(**module_ns)
        for file in ("abilities.py", "bosstraits.py", "locations.py"):
            compile_source(file, self.ns)
        compile_source("main.py", self.ns, functions_only=True)
        self.ns.update(gpt_request=gpt, generate_image=no_image)
        self.context = SimpleNamespace(bot=SimpleNamespace(
            send_message=send, send_photo=send, send_chat_action=no_wait, delete_message=no_wait))
        await self.ns["reset_game_state"](100, None)

    def update(self, user_id=1, data="", thread=None):
        user = SimpleNamespace(id=user_id, first_name=f"Player{user_id}")
        message = Message(self.messages, thread)
        return SimpleNamespace(effective_chat=message.chat, effective_user=user,
            effective_message=message, message=message,
            callback_query=Query(user, data, message))

    def player(self, user_id=1, faction="Nodewalker"):
        return dict(id=user_id, username=f"Player{user_id}", faction=faction,
                    hp=20, max_hp=20, modifier_type="technology", modifier_value=1,
                    abilities=copy.deepcopy(self.ns["ABILITIES"][faction]), equipped_items={})

    def combat_state(self):
        state = self.store.states[100]
        state.update(game_stage="GAUNTLET", game_mode="gauntlet", owner_id=1,
            players=[self.player()], gauntlet_level=1, location={},
            boss=dict(name="Audit Boss", hp=100, max_hp=100, abilities=[], strengths=[], weaknesses=[]))
        return state

    async def test_01_daily_login_overwrites_awarded_xp(self):
        profile = await self.ns["get_or_create_profile"](1, "Player1")
        profile["current_xp"] = 560
        await self.store.save_profile(1, profile)
        await self.ns["venture"](self.update(), self.context)
        final = self.store.profiles[1]
        self.assertEqual((final["level"], final["current_xp"]), (1, 560))
        self.assertEqual(final["last_login_date"], datetime.date.today().isoformat())
        self.assertTrue(any("LEVEL UP" in m[1] for m in self.messages))

    async def test_02_first_player_closes_multiplayer_lobby(self):
        self.store.states[100].update(game_stage="FACTION_SELECT", game_mode="gauntlet", gauntlet_level=1)
        await self.ns["faction_selection_callback"](self.update(data="faction:Nodewalker"), self.context)
        second = self.update(2, "faction:Glitchborn")
        await self.ns["faction_selection_callback"](second, self.context)
        self.assertEqual(len(self.store.states[100]["players"]), 1)
        self.assertIn("not active", second.callback_query.answers[-1][0][0])

    async def test_03_join_inflates_boss_hp_without_joining(self):
        self.combat_state()
        for _ in range(2):
            await self.ns["join_command"](self.update(2), self.context)
        state = self.store.states[100]
        self.assertEqual(state["boss"]["hp"], 130)
        self.assertEqual(len(state["players"]), 1)

    async def test_04_all_declared_boss_traits_are_ignored(self):
        for boss in self.ns["BOSS_TRAITS"].values():
            damage, notes = self.ns["adjust_boss_damage_for_traits"](20, {"boss": boss}, self.player(), None)
            self.assertEqual((damage, notes), (20, []))

    async def test_05_blockchain_items_do_not_boost_mercantile_damage(self):
        items = {"Equipment": {"specialty": "Blockchain", "rarity": "Peerless"}}
        self.assertEqual(self.ns["item_traits"].calculate_equipped_damage_bonus(items, "Mercantile"), 1.0)

    async def test_06_natural_100_cannot_generate_peerless_reward(self):
        self.rng.randint = lambda low, high: high
        await self.ns["generate_and_send_reward"](self.context, 100, self.update().effective_user, "item", 1)
        self.assertEqual(self.store.profiles[1]["inventory"][0]["rarity"], "Node-Forged")

    async def test_07_bank_bonus_argument_has_no_effect(self):
        results = []
        for bank_bonus in (0, 60):
            self.rng.seed(22)
            await self.ns["generate_and_send_reward"](self.context, 100, self.update().effective_user, "item", 20, bank_bonus)
            results.append(copy.deepcopy(self.store.profiles[1]["inventory"][-1]))
        self.assertEqual(results[0], results[1])

    async def test_08_failed_reward_is_not_recoverable_after_reset(self):
        async def unavailable(**kwargs):
            raise RuntimeError("Simulated provider outage")
        self.ns["gpt_request"] = unavailable
        self.combat_state()["game_stage"] = "VICTORY"
        await self.ns["reward_callback"](self.update(data="reward:item:10"), self.context)
        self.assertEqual(self.store.states[100]["game_stage"], "MAIN_MENU")
        self.assertEqual(self.store.profiles[1]["inventory"], [])
        self.assertGreater(self.store.profiles[1]["current_xp"], 0)

    async def test_09_recruited_ally_is_not_persisted(self):
        await self.ns["generate_and_send_reward"](self.context, 100, self.update().effective_user, "character", 1)
        self.assertNotIn("Audit Item", json.dumps(self.store.profiles))
        self.assertNotIn("Audit Item", json.dumps(self.store.states))

    async def test_10_roll_boost_consumed_during_cast(self):
        state = self.combat_state()
        ability = self.ns["item_traits"].get_item_ability("Cranial", "Blockchain", "Street Mod")
        state["players"][0]["abilities"].append(ability)
        self.rng.randint = lambda low, high: 5
        await self.ns["handle_player_action"](self.update(), self.context, "[ABILITY]:Market Insight", state)
        self.assertEqual(self.store.states[100]["active_roll_bonuses"], {})
        self.assertIn("+15 personal", self.requests[-1]["messages"][-1]["content"])

    async def test_11_invalid_model_shape_leaves_turn_locked(self):
        state = self.combat_state()
        state["is_processing_turn"] = True
        self.response["player_damage"] = []
        with self.assertRaises(AttributeError):
            await self.ns["handle_player_action"](self.update(), self.context, "[ABILITY]:Ping Attack", state)
        self.assertTrue(self.store.states[100]["is_processing_turn"])

    async def test_12_old_main_menu_replaces_active_players_and_owner(self):
        self.combat_state()
        await self.ns["main_menu_callback"](self.update(2, "main:gauntlet"), self.context)
        self.assertEqual(self.store.states[100]["owner_id"], 2)
        self.assertEqual(self.store.states[100]["players"], [])

    async def test_13_nonparticipant_can_claim_reward_from_other_topic(self):
        state = self.combat_state()
        state.update(game_stage="VICTORY", thread_id=44)
        await self.ns["reward_callback"](self.update(99, "reward:item:10", thread=55), self.context)
        self.assertEqual(len(self.store.profiles[99]["inventory"]), 1)
        self.assertEqual(self.store.states[100]["game_stage"], "MAIN_MENU")

    async def test_14_old_ascend_button_skips_live_boss(self):
        self.combat_state()
        await self.ns["gauntlet_menu_callback"](self.update(2, "gauntlet:continue"), self.context)
        self.assertEqual(self.store.states[100]["gauntlet_level"], 2)
        self.assertEqual(self.store.states[100]["game_stage"], "SCOUTING")

    async def test_15_stale_inventory_index_discards_different_item(self):
        profile = await self.ns["get_or_create_profile"](1, "Player1")
        profile["inventory"] = [dict(name="A", slot="Cranial"), dict(name="B", slot="Equipment")]
        await self.store.save_profile(1, profile)
        await self.ns["inventory_callback"](self.update(data="inv:confirm_discard:0"), self.context)
        await self.ns["inventory_callback"](self.update(data="inv:equip:0"), self.context)
        await self.ns["inventory_callback"](self.update(data="inv:discard:0"), self.context)
        self.assertEqual(self.store.profiles[1]["inventory"], [])
        self.assertEqual(self.store.profiles[1]["equipped_items"]["Cranial"]["name"], "A")

    async def test_16_environment_failure_can_leave_dead_player_active(self):
        state = self.combat_state()
        state["players"][0]["hp"] = 1
        state["location"] = copy.deepcopy(self.ns["LOCATIONS"][0])
        self.rng.randint = lambda low, high: 1
        await self.ns["environment_action_callback"](self.update(data="env_action:technology"), self.context)
        self.assertLess(self.store.states[100]["players"][0]["hp"], 0)
        self.assertEqual(self.store.states[100]["dead_players"], [])

    async def test_17_environment_gate_not_checked_by_handler(self):
        state = self.combat_state()
        state["location"] = copy.deepcopy(self.ns["LOCATIONS"][0])
        state["location_interaction_used"] = True
        self.rng.randint = lambda low, high: 5
        await self.ns["environment_action_callback"](self.update(data="env_action:technology"), self.context)
        self.assertEqual(self.store.states[100]["boss"]["hp"], 75)

    async def test_18_open_campaign_completion_event_does_not_end_game(self):
        state = self.combat_state()
        state.update(game_stage="LEVEL_1", game_mode="open_campaign", boss=None,
                     narrative_log=["At the terminal."], objective="Hack terminal.")
        self.response = dict(narrative="Objective complete.", player_damage=0,
                             skill_score=10, action_category="tech", event="objective_complete")
        await self.ns["handle_player_action"](self.update(), self.context, "hack the terminal", state)
        self.assertEqual(self.store.states[100]["game_stage"], "LEVEL_1")
        self.assertTrue(any("Skill: 10 |" in m[1] for m in self.messages))

    async def test_19_database_errors_indistinguishable_from_missing_data(self):
        ns = dict(os=os, json=json, logger=self.ns["logger"], _pool=None)
        compile_source("database.py", ns, functions_only=True)
        self.assertEqual(await ns["load_state"](100), {})
        self.assertIsNone(await ns["load_profile"](1))
        self.assertIsNone(await ns["save_state"](100, {"important": True}))
        self.assertIsNone(await ns["save_profile"](1, {"important": True}))

    async def test_20_boss_roll_modifier_is_not_applied(self):
        state = self.combat_state()
        state["boss"]["abilities"] = [dict(name="Debuff", description="Debuff party.",
            effects=[dict(type="roll_bonus", target="players", value=-10)])]
        await self.ns["handle_player_action"](self.update(), self.context, "[ABILITY]:Ping Attack", state)
        self.assertEqual(self.store.states[100]["active_roll_bonuses"], {})

    async def test_21_ability_outside_combat_can_repeat_victory_rewards(self):
        state = self.combat_state()
        state["game_stage"] = "VICTORY"
        state["boss"]["hp"] = 0
        state["gauntlet_bonus_defeated"] = 1
        await self.ns["ability_callback"](self.update(data="ability:Ping Attack"), self.context)
        self.assertEqual(self.store.states[100]["gauntlet_bonus_defeated"], 2)
        self.assertEqual(self.store.profiles[1]["stats"]["bosses_defeated"], 1)

    async def test_22_overlapping_damage_map_duplicates_deaths(self):
        state = self.combat_state()
        state["players"] = [self.player(1), self.player(2)]
        state["players"][0]["hp"] = 1
        await self.ns["apply_boss_damage"](self.context, 100, state, {"all": 1, "1": 1})
        self.assertEqual([p["id"] for p in state["dead_players"]], [1, 1])


if __name__ == "__main__":
    print("PASS = existing defect reproduced with isolated dependencies; no live calls.\n", flush=True)
    unittest.main(verbosity=2)
