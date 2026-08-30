"""Versioned sessions and atomic, idempotent profile mutations."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import asyncpg

logger = logging.getLogger(__name__)
CONNECT_RETRY_DELAYS = (2, 4, 8, 16)


class PersistenceError(RuntimeError):
    """Storage is unavailable; never substitute an empty profile."""


class StateConflict(PersistenceError):
    """Another action changed this session. Reload before trying again."""


class DatabaseStartupError(PersistenceError):
    """An actionable startup failure whose message contains no connection secrets."""


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


def validate_database_url(dsn: str | None = None) -> str:
    """Check common deployment mistakes without connecting or logging the URL.

    Leave asyncpg's IPv6, multi-host, query options, and TLS semantics intact.
    Require an explicit host so a missing service reference cannot select localhost.
    """
    dsn = (os.getenv("DATABASE_URL", "") if dsn is None else dsn).strip()
    if not dsn:
        raise DatabaseStartupError("DATABASE_URL is required on the bot service.")
    if "${" in dsn:
        raise DatabaseStartupError(
            "DATABASE_URL contains an unresolved variable reference. Resolve the database service reference "
            "in the hosting environment before starting the bot. See README: Database startup troubleshooting."
        )
    try:
        parsed = urlsplit(dsn)
        query = parse_qs(parsed.query, strict_parsing=True)
    except ValueError:
        raise DatabaseStartupError("DATABASE_URL is malformed. Copy the provider's PostgreSQL connection URL.") from None
    if parsed.scheme not in {"postgres", "postgresql"} or any(c.isspace() for c in dsn):
        raise DatabaseStartupError(
            "DATABASE_URL must be a postgres:// or postgresql:// URL without wrapping quotes or embedded whitespace."
        )
    if "#" in dsn or parsed.netloc.count("@") > 1:
        raise DatabaseStartupError(
            "DATABASE_URL contains unescaped URL delimiters. Copy the provider's URL; "
            "percent-encode special characters in credentials instead of editing the hostname."
        )
    hosts = parsed.netloc.rsplit("@", 1)[-1] or query.get("host", [""])[-1]
    if any(not host or host.startswith(":") for host in hosts.split(",")):
        raise DatabaseStartupError("DATABASE_URL must include an explicit database hostname or host query parameter.")
    return dsn


def _connection_failure(exc: Exception) -> tuple[bool, str]:
    """Classify without including driver messages, which may contain credentials."""
    if isinstance(exc, socket.gaierror):
        return True, (
            "Database hostname could not be resolved (DNS). Check DATABASE_URL on the bot service and "
            "the database's current hostname. Railway private hosts require the bot and database in the same "
            "project/environment at runtime. See README: Database startup troubleshooting."
        )
    if isinstance(exc, asyncpg.InvalidAuthorizationSpecificationError):
        return False, "Database authentication failed. Check the database credentials referenced by DATABASE_URL."
    if isinstance(exc, asyncpg.InvalidCatalogNameError):
        return False, "The database named by DATABASE_URL does not exist. Check the provider's connection URL."
    if isinstance(exc, ssl.SSLError):
        return False, "Database TLS negotiation failed. Check the provider's TLS/certificate settings; do not disable TLS."
    if isinstance(exc, (asyncpg.ClientConfigurationError, ValueError)):
        return False, "DATABASE_URL or PostgreSQL client options are invalid. Check the provider's connection settings."
    if isinstance(exc, (asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError)):
        return True, "PostgreSQL is not accepting connections yet. Check database health and connection capacity."
    if isinstance(exc, (asyncpg.ConnectionDoesNotExistError, asyncpg.ConnectionFailureError)):
        return True, "The database connection was interrupted. Check database health and network reachability."
    if isinstance(exc, OSError):
        retry = exc.errno in {
            None, errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED,
            errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE,
        }
        return retry, "Database connection failed. Check the database host/port, network access, and client settings."
    return False, "PostgreSQL rejected the connection. Check database health, access permissions, and client settings."


async def _connect_pool(dsn: str):
    attempts = len(CONNECT_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        pool = None
        try:
            pool = asyncpg.create_pool(dsn, min_size=1, max_size=10, timeout=15, command_timeout=15)
            await pool
            return pool
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, ValueError) as exc:
            if pool is not None:
                pool.terminate()
            retry, message = _connection_failure(exc)
            if not retry or attempt == attempts - 1:
                raise DatabaseStartupError(
                    f"{message} Startup stopped after {attempt + 1} connection attempt(s); polling has not started."
                ) from None
            delay = CONNECT_RETRY_DELAYS[attempt]
            logger.warning("%s Attempt %d/%d; retrying in %ds.", message, attempt + 1, attempts, delay)
            await asyncio.sleep(delay)
        except BaseException:
            if pool is not None:
                pool.terminate()
            raise


async def connect(dsn: str | None = None) -> Repository:
    pool = await _connect_pool(validate_database_url(dsn))
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(784204601)")
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                await conn.execute(path.read_text(encoding="utf-8"))
        logger.info("Database connected; startup migrations completed.")
        return Repository(pool)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        pool.terminate()
        raise DatabaseStartupError(
            "Database connected, but startup migrations failed "
            f"({type(exc).__name__}). Check schema permissions and database health before redeploying. "
            "Do not reset the database. Polling has not started."
        ) from None
    except BaseException:
        pool.terminate()
        raise
