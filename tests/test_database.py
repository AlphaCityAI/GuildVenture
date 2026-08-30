"""Run only against a disposable database whose name ends in _test."""

import asyncio
import json
import os
from pathlib import Path
import socket
from unittest.mock import AsyncMock
from urllib.parse import urlparse
import uuid

import asyncpg
import pytest
import pytest_asyncio

import database
from database import Repository, StateConflict
import profiles
import gameplay_content as content

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run disposable PostgreSQL integration tests")
    if not urlparse(url).path.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL database name must end in _test; never use production")
    schema = "test_" + uuid.uuid4().hex
    admin = await asyncpg.connect(url)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = await asyncpg.create_pool(url, min_size=1, max_size=5, server_settings={"search_path": schema})
    repo = Repository(pool)
    try:
        # Simulate the old production schema before applying the additive migration.
        await pool.execute("CREATE TABLE game_states(chat_id BIGINT PRIMARY KEY, data JSONB NOT NULL)")
        await pool.execute("CREATE TABLE player_profiles(user_id BIGINT PRIMARY KEY, data JSONB NOT NULL)")
        await pool.execute("INSERT INTO game_states VALUES(1,'{\"legacy\": true}')")
        sql = (Path(__file__).resolve().parents[1] / "migrations/001_reliability.sql").read_text()
        await pool.execute(sql)
        await pool.execute(sql)  # Repeated deployment is safe.
        yield repo
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def test_additive_migration_preserves_legacy_document(db):
    assert await db.load_state(1) == {"legacy": True, "_revision": 0}


async def test_stale_state_write_is_rejected(db):
    state = await db.load_state(1)
    stale = dict(state)
    state["new"] = True
    await db.save_state(1, state)
    with pytest.raises(StateConflict):
        await db.save_state(1, stale)
    assert (await db.load_state(1))["new"] is True


async def test_concurrent_profile_events_do_not_lose_xp(db):
    def award(profile):
        profiles.normalize(profile, "Alice")
        return profiles.add_xp(profile, 10)

    await asyncio.gather(*[db.mutate_profile(10, award, f"event:{i}") for i in range(20)])
    await asyncio.gather(*[db.mutate_profile(10, award, f"event:{i}") for i in range(20)])
    assert (await db.load_profile(10))["current_xp"] == 200


async def test_failed_mutation_rolls_back_profile_and_event(db):
    await db.mutate_profile(10, lambda p: p.update(counter=1))

    def failure(profile):
        profile["counter"] = 2
        raise ValueError("Simulated invalid mutation")

    with pytest.raises(ValueError):
        await db.mutate_profile(10, failure, "failure")
    assert (await db.load_profile(10))["counter"] == 1
    assert await db.pool.fetchval("SELECT count(*) FROM profile_events WHERE user_id=10") == 0


async def test_duplicate_reward_claims_commit_only_once(db):
    payload = profiles.make_item("Module", "Cranial", "Neural", "Salvage", "")

    def entitlement(profile):
        profiles.normalize(profile)
        profile["pending_rewards"]["reward"] = {}

    await db.mutate_profile(10, entitlement)
    results = await asyncio.gather(
        *[
            db.mutate_profile(10, lambda p: profiles.grant_reward(p, "reward", payload), "claim:reward")
            for _ in range(8)
        ]
    )
    assert sum(r.applied for r in results) == 1
    assert len((await db.load_profile(10))["inventory"]) == 1
    assert len(await db.recent_rewards(10)) == 1


async def test_leaderboard_handles_legacy_missing_and_invalid_counters(db):
    for uid, stats in [(1, {}), (2, {"highest_floor": "bad"}), (3, {"highest_floor": 4, "bosses_defeated": 3})]:
        await db.pool.execute(
            "INSERT INTO player_profiles VALUES($1,$2::jsonb)", uid, json.dumps({"username": str(uid), "stats": stats})
        )
    top = await db.top_profiles()
    assert top[0]["user_id"] == 3
    assert all("inventory" not in row for row in top)


async def test_concurrent_crafting_and_salvage_cannot_consume_same_source(db):
    def setup(profile):
        profiles.normalize(profile)
        item = profiles.make_item("Source", "Cranial", "Neural", "Salvage", "")
        item["id"] = "i" * 16
        profile.update(inventory=[item], materials=20)
        profiles.save_craft_quote(profile, item, profiles.forged_item(item), "q" * 16, 100, "fallback")

    await db.mutate_profile(10, setup)
    results = await asyncio.gather(
        db.mutate_profile(10, lambda p: profiles.complete_craft(p, "q" * 16), "craft:q"),
        db.mutate_profile(10, lambda p: profiles.salvage_item(p, "i" * 16), "salvage:i"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, profiles.InvalidAction) for r in results) == 1
    profile = await db.load_profile(10)
    assert (profile["materials"], len(profile["inventory"])) in {(12, 1), (22, 0)}
    assert await db.pool.fetchval("SELECT count(*) FROM profile_events WHERE user_id=10") == 1


async def test_parallel_blueprint_confirmations_charge_once(db):
    def setup(profile):
        profiles.normalize(profile)
        item = profiles.make_item("Source", "Cranial", "Neural", "Salvage", "")
        item["id"] = "i" * 16
        profile.update(inventory=[item], materials=20)
        profiles.save_craft_quote(profile, item, profiles.forged_item(item), "q" * 16, 100, "fallback")

    await db.mutate_profile(10, setup)
    results = await asyncio.gather(
        *[db.mutate_profile(10, lambda p: profiles.complete_craft(p, "q" * 16), "craft:q") for _ in range(8)]
    )
    assert sum(r.applied for r in results) == 1
    profile = await db.load_profile(10)
    assert profile["materials"] == 12 and len(profile["inventory"]) == 1
    assert profile["last_craft_receipt"]["item"]["id"] == "q" * 16


async def test_concurrent_talent_choices_commit_exactly_one_option(db):
    def setup(profile):
        profiles.normalize(profile)
        profile["level"] = 2
        profiles.save_talent_offers(profile, 2, content.fallback_talents().model_dump()["offers"], "fallback")

    await db.mutate_profile(10, setup)
    results = await asyncio.gather(
        *[db.mutate_profile(10, lambda p, index=i: profiles.choose_talent(p, 2, index), "talent:2") for i in range(3)]
    )
    profile = await db.load_profile(10)
    assert len(profile["talents"]) == 1 and sum(r.applied for r in results) == 1
    assert all(r.result == profile["talents"][0] for r in results)


async def test_completion_event_preserves_legacy_inventory_and_awards_bond_once(db):
    item = profiles.make_item("Legacy item", "Cranial", "Neural", "Salvage", "")
    ally = {"id": "a" * 16, "name": "Legacy ally", "faction": "Nodewalker", "rarity": "Salvage", "level": 1}
    await db.pool.execute(
        "INSERT INTO player_profiles VALUES(10,$1::jsonb)",
        json.dumps({"level": 5, "current_xp": 123, "inventory": [item], "collectibles": [ally]}),
    )
    event = {
        "username": "Alice",
        "kind": "progress",
        "stats": {"chapters_completed": 1},
        "xp": 100,
        "materials": 5,
        "ally_id": ally["id"],
    }
    await asyncio.gather(
        *[db.mutate_profile(10, lambda p: profiles.apply_event(p, event), "chapter:0") for _ in range(5)]
    )
    profile = await db.load_profile(10)
    assert profile["current_xp"] == 223 and profile["materials"] == 5
    assert profile["collectibles"][0]["bond"] == 1 and profile["inventory"][0]["name"] == "Legacy item"
    assert profile["stats"]["chapters_completed"] == 1 and profile["inventory"][0]["id"]


async def test_real_startup_recovers_dns_then_reapplies_migration_without_losing_data(db, monkeypatch):
    schema = await db.pool.fetchval("SELECT current_schema()")
    create_pool = asyncpg.create_pool
    attempts = 0
    pools = []

    async def delayed_dns(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise socket.gaierror(socket.EAI_NONAME, "Simulated deployment DNS delay")
        return await asyncpg.connect(*args, **kwargs)

    def isolated_pool(*args, **kwargs):
        pool = create_pool(*args, connect=delayed_dns, server_settings={"search_path": schema}, **kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(database.asyncpg, "create_pool", isolated_pool)
    monkeypatch.setattr(database.asyncio, "sleep", AsyncMock())
    repo = await database.connect(os.environ["TEST_DATABASE_URL"])
    try:
        assert attempts == 2 and pools[0].is_closing()
        assert await repo.load_state(1) == {"legacy": True, "_revision": 0}
        await repo.mutate_profile(10, lambda p: p.update(saved="after startup"), "startup-test")
        assert (await db.load_profile(10))["saved"] == "after startup"
    finally:
        await repo.close()


async def test_real_migration_permission_failure_closes_pool_and_preserves_data(db, monkeypatch):
    schema = await db.pool.fetchval("SELECT current_schema()")
    create_pool = asyncpg.create_pool
    pools = []

    def read_only_pool(*args, **kwargs):
        pool = create_pool(
            *args, server_settings={"search_path": schema, "default_transaction_read_only": "on"}, **kwargs
        )
        pools.append(pool)
        return pool

    monkeypatch.setattr(database.asyncpg, "create_pool", read_only_pool)
    with pytest.raises(database.DatabaseStartupError, match="startup migrations failed.*ReadOnlySQLTransactionError"):
        await database.connect(os.environ["TEST_DATABASE_URL"])
    assert len(pools) == 1 and pools[0].is_closing()
    assert await db.load_state(1) == {"legacy": True, "_revision": 0}


@pytest.mark.parametrize("mode", ["gauntlet", "open_campaign"])
async def test_rendered_multiplayer_lobby_survives_jsonb_roundtrips_and_concurrent_readiness(db, rig, mode):
    from bot_service import BotService
    from test_playable_flows import TelegramUI

    rig.repo = db
    rig.service = BotService(db, rig.ai, default_images=False)
    ui = TelegramUI(rig)
    await ui.command("/venture")
    await ui.press(action="mode", argument=mode)
    if mode == "gauntlet":
        await ui.press(action="route", argument="default")
    panel = ui.latest
    await ui.press(action="faction", argument="Nodewalker", message=panel)
    first = ui.latest
    await ui.press(action="faction", argument="Coinbroker", uid=2, message=panel)
    second = ui.latest
    await ui.press(action="images", message=panel)
    await asyncio.gather(
        ui.press(label="Choose this faction", message=first),
        ui.press(label="Choose this faction", uid=2, message=second),
    )
    await asyncio.gather(*[ui.press(action="ready", argument="1", uid=uid, message=panel) for uid in (1, 2)])
    state = await db.load_state(ui.chat)
    assert {p["id"] for p in state["players"]} == {1, 2} and all(p["ready"] for p in state["players"])
    await ui.press(action="start", message=panel)
    state = await db.load_state(ui.chat)
    assert state["phase"] == ("combat" if mode == "gauntlet" else "chapter_briefing")
    assert not rig.service.locks and not rig.service.lock_users
    # Images were enabled to test harmless changes; explicitly drain optional tasks.
    await asyncio.gather(*rig.service.tasks)


@pytest.mark.parametrize("thread", [None, 321])
async def test_persisted_topic_silently_blocks_other_topics_after_restart(db, rig, thread):
    from bot_service import BotService
    import game
    from test_playable_flows import TelegramUI

    rig.repo = db
    rig.service = BotService(db, rig.ai, default_images=False)
    ui = TelegramUI(rig, thread=thread)
    await ui.command("/venture")
    mode = ui.find(action="mode")[1]
    state = await db.load_state(ui.chat)
    state["schema_version"] = 2
    game.queue_event(state, {"id": 1, "username": "Owner"}, "attempt", 25, {"bosses_attempted": 1})
    await db.save_state(ui.chat, state)
    profile = await db.load_profile(1)
    await ui.restart()
    for name in ("send_message", "send_photo", "edit_message_text", "answer_callback_query"):
        getattr(rig.bot, name).reset_mock()
    for outside in (999, None if thread is not None else 321):
        # Explicit messages also exercise General's missing thread ID.
        ui.thread = outside
        await ui.command("hello everyone")
        await ui.command("/venture")
        await ui.command("/profile")
        await ui.press(saved=(ui.message("old menu", thread=outside), mode))
    for name in ("send_message", "send_photo", "edit_message_text", "answer_callback_query"):
        getattr(rig.bot, name).assert_not_awaited()
    assert await db.load_state(ui.chat) == state
    assert await db.load_profile(1) == profile
    # In-topic recovery still migrates and delivers the pending event once.
    ui.thread = thread
    await ui.command("/status")
    assert (await db.load_state(ui.chat))["events"] == []
    assert (await db.load_profile(1))["stats"]["bosses_attempted"] == 1
    assert ui.latest.message_thread_id == thread and ui.latest.reply_markup
