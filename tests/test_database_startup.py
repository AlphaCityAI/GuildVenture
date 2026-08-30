"""Deployment failures must recover safely or explain how to fix configuration."""

import asyncio
from contextlib import asynccontextmanager
import errno
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
import traceback

import asyncpg
import pytest

import database
import main

URL = "postgresql://private-user:private-password@postgres.railway.internal:5432/private-db?sslmode=require"


class StartupPool:
    def __init__(self, error=None, migration_error=None):
        self.initialize = AsyncMock(side_effect=error, return_value=self)
        self.execute = AsyncMock(side_effect=migration_error)
        self.terminate = Mock()
        self.close = AsyncMock()
        self.transactions = 0

    def __await__(self):
        return self.initialize().__await__()

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        yield self


def install_pools(monkeypatch, pools):
    create = Mock(side_effect=pools)
    sleep = AsyncMock()
    monkeypatch.setattr(database.asyncpg, "create_pool", create)
    monkeypatch.setattr(database.asyncio, "sleep", sleep)
    return create, sleep


async def test_dns_startup_delay_recovers_before_migration_without_switching_database(monkeypatch, caplog):
    failed = [StartupPool(socket.gaierror(socket.EAI_NONAME, URL)) for _ in range(2)]
    healthy = StartupPool()
    create, sleep = install_pools(monkeypatch, [*failed, healthy])
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgresql://other:secret@other.example/wrong_db")
    repo = await database.connect(URL)
    assert repo.pool is healthy
    assert create.call_count == 3 and all(c.args == (URL,) for c in create.call_args_list)
    assert sleep.await_args_list == [call(2), call(4)]
    for pool in failed:
        pool.terminate.assert_called_once()
        pool.execute.assert_not_awaited()
    assert healthy.transactions == 1 and healthy.execute.await_count >= 2
    healthy.terminate.assert_not_called()
    assert "DNS" in caplog.text and "private-password" not in caplog.text
    await repo.close()
    healthy.close.assert_awaited_once()


async def test_persistent_dns_failure_stops_with_safe_deployment_advice(monkeypatch, caplog):
    pools = [StartupPool(socket.gaierror(socket.EAI_NONAME, URL)) for _ in range(5)]
    create, sleep = install_pools(monkeypatch, pools)
    with pytest.raises(database.DatabaseStartupError) as failure:
        await database.connect(URL)
    output = "".join(traceback.format_exception(failure.value)) + caplog.text
    assert "DNS" in output and "same project/environment" in output and "5 connection attempt" in output
    assert "private-password" not in output and "private-user" not in output and "private-db" not in output
    assert create.call_count == 5 and sleep.await_args_list == [call(2), call(4), call(8), call(16)]
    for pool in pools:
        pool.terminate.assert_called_once()
        pool.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        socket.gaierror(socket.EAI_AGAIN, "temporary DNS delay"),
        ConnectionRefusedError(errno.ECONNREFUSED, "not ready"),
        TimeoutError(),
        OSError("all connection attempts failed"),
        asyncpg.CannotConnectNowError("starting up"),
        asyncpg.TooManyConnectionsError("full"),
        asyncpg.ConnectionDoesNotExistError("disconnected"),
        asyncpg.ConnectionFailureError("disconnected"),
    ],
)
async def test_transient_network_and_server_failures_retry(monkeypatch, error):
    failed, healthy = StartupPool(error), StartupPool()
    create, sleep = install_pools(monkeypatch, [failed, healthy])
    repo = await database.connect(URL)
    assert repo.pool is healthy and create.call_count == 2
    sleep.assert_awaited_once_with(2)
    failed.terminate.assert_called_once()


@pytest.mark.parametrize(
    "error, message",
    [
        (asyncpg.InvalidPasswordError(URL), "authentication"),
        (asyncpg.InvalidAuthorizationSpecificationError(URL), "authentication"),
        (asyncpg.InvalidCatalogNameError(URL), "does not exist"),
        (ssl.SSLCertVerificationError(URL), "TLS"),
        (asyncpg.ClientConfigurationError(URL), "client options"),
        (ValueError(URL), "client options"),
        (PermissionError(errno.EACCES, URL), "client settings"),
        (asyncpg.InvalidParameterValueError(URL), "rejected"),
    ],
)
async def test_permanent_failures_are_not_retried_or_exposed(monkeypatch, caplog, error, message):
    pool = StartupPool(error)
    create, sleep = install_pools(monkeypatch, [pool])
    with pytest.raises(database.DatabaseStartupError, match=message) as failure:
        await database.connect(URL)
    assert "private-password" not in "".join(traceback.format_exception(failure.value)) + caplog.text
    assert create.call_count == 1
    sleep.assert_not_awaited()
    pool.terminate.assert_called_once()
    pool.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "  ",
        "https://private-user:private-password@host/db",
        "'" + URL + "'",
        "${{Postgres.DATABASE_URL}}",
        "postgresql://user:private-password@${PGHOST}/db",
        "postgresql:///db",
        "postgresql://user:private-password@/db",
        "postgresql://[broken/db",
        "postgresql://user:private-password@host name/db",
        "postgresql://user:private-password@host/db#fragment",
        "postgresql://user:private-password@with-at@host/db",
        "postgresql://user:private-password@:5432/db",
        "postgresql://host-one,,host-two/db",
    ],
)
async def test_bad_configuration_fails_before_network_without_leaking_url(monkeypatch, url):
    create, sleep = install_pools(monkeypatch, [])
    monkeypatch.setenv("DATABASE_URL", URL)  # An explicitly empty argument must not use the environment instead.
    with pytest.raises(database.DatabaseStartupError) as failure:
        await database.connect(url)
    assert "private-password" not in "".join(traceback.format_exception(failure.value))
    create.assert_not_called()
    sleep.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        URL,
        "postgres://user:pass%40%23%2F@host:5432/db?sslmode=verify-full&application_name=guildventure",
        "postgresql://user:pass@[::1]:5432/db",
        "postgresql://user:pass@host-one:5432,host-two:5433/db",
        "postgresql:///db?host=localhost&user=user&password=pass%40%23&sslmode=require",
        "postgresql:///db?host=%2Fvar%2Frun%2Fpostgresql",
    ],
)
async def test_valid_urls_preserve_driver_options_and_only_trim_outer_whitespace(monkeypatch, url):
    create, _ = install_pools(monkeypatch, [StartupPool()])
    monkeypatch.setenv("DATABASE_URL", "  " + url + "\n")
    await database.connect()
    assert create.call_args.args == (url,)
    assert "ssl" not in create.call_args.kwargs and "host" not in create.call_args.kwargs


async def test_cancelled_connection_terminates_pool_without_retry(monkeypatch):
    pool = StartupPool(asyncio.CancelledError())
    create, sleep = install_pools(monkeypatch, [pool])
    with pytest.raises(asyncio.CancelledError):
        await database.connect(URL)
    assert create.call_count == 1
    pool.terminate.assert_called_once()
    sleep.assert_not_awaited()


async def test_cancellation_during_backoff_stops_startup(monkeypatch):
    pool = StartupPool(socket.gaierror(socket.EAI_NONAME, "DNS"))
    create, sleep = install_pools(monkeypatch, [pool])
    sleep.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await database.connect(URL)
    assert create.call_count == 1
    pool.terminate.assert_called_once()


@pytest.mark.parametrize("error", [asyncpg.InsufficientPrivilegeError(URL), TimeoutError(URL)])
async def test_migration_failure_is_distinct_from_connection_failure_and_not_retried(monkeypatch, error):
    pool = StartupPool(migration_error=error)
    create, sleep = install_pools(monkeypatch, [pool])
    with pytest.raises(database.DatabaseStartupError, match="connected, but startup migrations failed") as failure:
        await database.connect(URL)
    assert "private-password" not in "".join(traceback.format_exception(failure.value))
    assert create.call_count == 1
    pool.terminate.assert_called_once()
    sleep.assert_not_awaited()


async def test_cancelled_migration_terminates_pool(monkeypatch):
    pool = StartupPool(migration_error=asyncio.CancelledError())
    install_pools(monkeypatch, [pool])
    with pytest.raises(asyncio.CancelledError):
        await database.connect(URL)
    pool.terminate.assert_called_once()


@pytest.mark.parametrize("bad_config", [False, True])
def test_entrypoint_reports_failure_without_raw_driver_traceback(monkeypatch, bad_config):
    for key, value in {"DATABASE_URL": URL, "TELEGRAM_TOKEN": "test", "OPENAI_API_KEY": "test"}.items():
        monkeypatch.setenv(key, value)
    build = Mock(
        return_value=SimpleNamespace(run_polling=Mock(side_effect=database.DatabaseStartupError("DNS advice")))
    )
    monkeypatch.setattr(main, "build_application", build)
    if bad_config:
        monkeypatch.setenv("DATABASE_URL", "${{Postgres.DATABASE_URL}}")
    with pytest.raises(SystemExit, match="Startup failed") as failure:
        main.main()
    assert failure.value.code != 0
    if bad_config:
        build.assert_not_called()
    else:
        build.return_value.run_polling.assert_called_once()


async def test_failed_database_startup_does_not_initialize_ai_or_register_commands(monkeypatch):
    ai = Mock()
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    monkeypatch.setattr(main, "AIService", ai)
    monkeypatch.setattr(main, "connect", AsyncMock(side_effect=database.DatabaseStartupError("DNS advice")))
    app = SimpleNamespace(bot_data={}, bot=bot)
    with pytest.raises(database.DatabaseStartupError):
        await main.post_init(app)
    ai.assert_not_called()
    bot.set_my_commands.assert_not_awaited()
    assert not app.bot_data
