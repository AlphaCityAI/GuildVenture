"""
PostgreSQL database layer for GuildVenture.

Replaces the former Replit DB (key-value store) with asyncpg + PostgreSQL.
Railway provides DATABASE_URL automatically when a PostgreSQL plugin is attached.

Tables
------
game_states      – one row per Telegram chat   (chat_id  BIGINT PK, data JSONB)
player_profiles  – one row per Telegram user    (user_id  BIGINT PK, data JSONB)
"""

import os
import json
import logging
from typing import Optional, List

import asyncpg

logger = logging.getLogger(__name__)

# Module-level connection pool (initialised at startup)
_pool: Optional[asyncpg.Pool] = None


# ───────── Lifecycle ─────────

async def init_db() -> None:
    """Create the connection pool and ensure tables exist."""
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10, timeout=30)
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_states (
                chat_id  BIGINT PRIMARY KEY,
                data     JSONB NOT NULL DEFAULT '{}'::jsonb
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS player_profiles (
                user_id  BIGINT PRIMARY KEY,
                data     JSONB NOT NULL DEFAULT '{}'::jsonb
            );
        """)
    logger.info("Database initialised (tables verified)")


async def close_db() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


def _ensure_pool() -> asyncpg.Pool:
    """Raise early if the pool was never initialised."""
    if _pool is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _pool


def _parse_jsonb(value) -> dict:
    """Normalise a JSONB column value to a Python dict."""
    if value is None:
        return {}
    return json.loads(value) if isinstance(value, str) else value


# ───────── Game State ─────────

async def load_state(chat_id: int) -> dict:
    try:
        pool = _ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM game_states WHERE chat_id = $1", chat_id
            )
        if row and row["data"]:
            return _parse_jsonb(row["data"])
        return {}
    except Exception as e:
        logger.error("load_state fail: %s", e)
        return {}


async def save_state(chat_id: int, state: dict) -> None:
    try:
        pool = _ensure_pool()
        payload = json.dumps(state)
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO game_states (chat_id, data)
                   VALUES ($1, $2::jsonb)
                   ON CONFLICT (chat_id)
                   DO UPDATE SET data = EXCLUDED.data""",
                chat_id, payload,
            )
    except Exception as e:
        logger.error("save_state fail: %s", e)


# ───────── Player Profiles ─────────

async def load_profile(user_id: int) -> Optional[dict]:
    try:
        pool = _ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM player_profiles WHERE user_id = $1", user_id
            )
        if row and row["data"]:
            return _parse_jsonb(row["data"]) or None
        return None
    except Exception as e:
        logger.error("load_profile fail: %s", e)
        return None


async def save_profile(user_id: int, profile: dict) -> None:
    try:
        pool = _ensure_pool()
        payload = json.dumps(profile)
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO player_profiles (user_id, data)
                   VALUES ($1, $2::jsonb)
                   ON CONFLICT (user_id)
                   DO UPDATE SET data = EXCLUDED.data""",
                user_id, payload,
            )
    except Exception as e:
        logger.error("save_profile fail: %s", e)


async def get_all_profiles() -> List[dict]:
    """Fetch every player profile (for leaderboard, etc.)."""
    try:
        pool = _ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT data FROM player_profiles")
        return [_parse_jsonb(row["data"]) for row in rows if row["data"]]
    except Exception as e:
        logger.error("Failed to get all profiles: %s", e)
        return []
