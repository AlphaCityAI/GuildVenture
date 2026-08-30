"""Versioned sessions and atomic, idempotent profile mutations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import asyncpg


class PersistenceError(RuntimeError):
    """Storage is unavailable; never substitute an empty profile."""


class StateConflict(PersistenceError):
    """Another action changed this session. Reload before trying again."""


@dataclass
class Mutation:
    profile: dict
    result: Any
    applied: bool


def _document(value) -> dict:
    data = json.loads(value) if isinstance(value, str) else value
    if not isinstance(data, dict):
        raise PersistenceError("Expected a JSON object in storage")
    return data


class Repository:
    def __init__(self, pool):
        self.pool = pool

    async def load_state(self, chat_id: int) -> dict:
        try:
            row = await self.pool.fetchrow("SELECT data, revision FROM game_states WHERE chat_id=$1", chat_id)
            return {**_document(row["data"]), "_revision": row["revision"]} if row else {}
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError, ValueError) as exc:
            raise PersistenceError("Cannot load session") from exc

    async def save_state(self, chat_id: int, state: dict) -> None:
        expected = state.get("_revision")
        payload = json.dumps({k: v for k, v in state.items() if k != "_revision"})
        try:
            if expected is None:
                revision = await self.pool.fetchval(
                    """INSERT INTO game_states(chat_id,data,revision) VALUES($1,$2::jsonb,1)
                    ON CONFLICT(chat_id) DO NOTHING RETURNING revision""",
                    chat_id,
                    payload,
                )
            else:
                revision = await self.pool.fetchval(
                    """UPDATE game_states SET data=$2::jsonb, revision=revision+1
                    WHERE chat_id=$1 AND revision=$3 RETURNING revision""",
                    chat_id,
                    payload,
                    expected,
                )
            if revision is None:
                raise StateConflict("Session changed; use /status for current controls")
            state["_revision"] = revision
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
            raise PersistenceError("Cannot save session") from exc

    async def load_profile(self, user_id: int) -> dict | None:
        try:
            value = await self.pool.fetchval("SELECT data FROM player_profiles WHERE user_id=$1", user_id)
            return _document(value) if value is not None else None
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError, ValueError) as exc:
            raise PersistenceError("Cannot load profile") from exc

    async def mutate_profile(self, user_id: int, mutate: Callable, event_id: str | None = None) -> Mutation:
        """Short transaction: never await providers inside the mutation callback."""
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "INSERT INTO player_profiles(user_id,data) VALUES($1,'{}') ON CONFLICT DO NOTHING", user_id
                )
                value = await conn.fetchval("SELECT data FROM player_profiles WHERE user_id=$1 FOR UPDATE", user_id)
                profile = _document(value)
                if event_id is not None:
                    prior = await conn.fetchrow(
                        "SELECT result FROM profile_events WHERE user_id=$1 AND event_id=$2", user_id, event_id
                    )
                    if prior:
                        result = json.loads(prior["result"]) if isinstance(prior["result"], str) else prior["result"]
                        return Mutation(profile, result, False)
                result = mutate(profile)
                await conn.execute(
                    "UPDATE player_profiles SET data=$2::jsonb WHERE user_id=$1", user_id, json.dumps(profile)
                )
                if event_id is not None:
                    await conn.execute(
                        "INSERT INTO profile_events(user_id,event_id,result) VALUES($1,$2,$3::jsonb)",
                        user_id,
                        event_id,
                        json.dumps(result),
                    )
                return Mutation(profile, result, True)
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
            raise PersistenceError("Cannot update profile") from exc

    async def top_profiles(self, limit: int = 10) -> list[dict]:
        try:
            rows = await self.pool.fetch(
                """SELECT user_id, data->>'username' AS username,
                CASE WHEN data->'stats'->>'highest_floor' ~ '^[0-9]{1,9}$'
                     THEN (data->'stats'->>'highest_floor')::int ELSE 0 END AS highest_floor,
                CASE WHEN data->'stats'->>'bosses_defeated' ~ '^[0-9]{1,9}$'
                     THEN (data->'stats'->>'bosses_defeated')::int ELSE 0 END AS bosses_defeated
                FROM player_profiles ORDER BY highest_floor DESC,bosses_defeated DESC,user_id ASC LIMIT $1""",
                max(1, min(limit, 100)),
            )
            return [dict(row) for row in rows]
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
            raise PersistenceError("Cannot load leaderboard") from exc

    async def close(self):
        await self.pool.close()

    async def recent_rewards(self, user_id: int, limit=5):
        try:
            rows = await self.pool.fetch(
                """SELECT result FROM profile_events WHERE user_id=$1 AND event_id LIKE 'claim:%'
                ORDER BY created_at DESC, event_id DESC LIMIT $2""",
                user_id,
                limit,
            )
            return [_document(row["result"]) for row in rows]
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError) as exc:
            raise PersistenceError("Cannot load reward receipts") from exc


async def connect(dsn: str | None = None) -> Repository:
    dsn = dsn or os.getenv("DATABASE_URL")
    if not dsn:
        raise PersistenceError("DATABASE_URL is required")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, timeout=15, command_timeout=15)
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(784204601)")
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                await conn.execute(path.read_text(encoding="utf-8"))
        return Repository(pool)
    except BaseException:
        await pool.close()
        raise
