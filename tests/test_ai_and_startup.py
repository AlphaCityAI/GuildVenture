import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import AuthenticationError, RateLimitError
import pytest
from pydantic import ValidationError

from ai_service import AIService, CampaignAssessment, Flavor


def client_with(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())


def response(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])


async def test_invalid_json_shape_and_types_fail_closed():
    for value in [{"name": [], "background": "x"}, {"name": "x", "background": "x" * 301}, []]:
        create = AsyncMock(return_value=response(json.dumps(value)))
        ai = AIService(client=client_with(create))
        assert await ai.flavor("item", {}) is None
        assert create.await_count == 1


async def test_authentication_error_not_retried():
    response_obj = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid"))
    create = AsyncMock(side_effect=AuthenticationError("invalid", response=response_obj, body=None))
    ai = AIService(client=client_with(create))
    assert await ai.flavor("item", {}) is None
    assert create.await_count == 1


async def test_rate_limit_retry_is_bounded():
    response_obj = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    create = AsyncMock(side_effect=RateLimitError("limited", response=response_obj, body=None))
    ai = AIService(client=client_with(create))
    assert await ai.flavor("item", {}) is None
    assert create.await_count == 2


async def test_deadline_includes_semaphore_wait():
    ai = AIService(client=client_with(AsyncMock()), timeout=0.01, max_concurrent=1)
    await ai.limit.acquire()
    try:
        assert await asyncio.wait_for(ai.flavor("item", {}), 0.2) is None
    finally:
        ai.limit.release()


async def test_local_daily_admission_limit_has_safe_fallback():
    create = AsyncMock(return_value=response('{"name":"x","background":"y"}'))
    ai = AIService(client=client_with(create), text_daily_limit=1)
    assert isinstance(await ai.flavor("item", {}), Flavor)
    assert await ai.flavor("item", {}) is None
    assert create.await_count == 1


def test_campaign_schema_rejects_model_damage_and_score_outside_bounds():
    with pytest.raises(ValidationError):
        CampaignAssessment(action_category="technology", skill_score=999, player_damage=-10, event="win")


def test_import_and_application_build_need_no_live_credentials(monkeypatch):
    for key in ["OPENAI_API_KEY", "TELEGRAM_TOKEN", "DATABASE_URL"]:
        monkeypatch.delenv(key, raising=False)
    main = importlib.import_module("main")
    app = main.build_application("123456:TEST_TOKEN")
    assert app.concurrent_updates == 16
    with pytest.raises(SystemExit, match="Missing required configuration"):
        main.main()


@pytest.mark.parametrize("value", ["oops", "-1"])
async def test_invalid_budget_fails_before_opening_database(monkeypatch, value):
    import main

    connect = AsyncMock()
    monkeypatch.setattr(main, "connect", connect)
    monkeypatch.setenv("AI_DAILY_TEXT_LIMIT", value)
    with pytest.raises(ValueError, match="AI_DAILY_TEXT_LIMIT must be a nonnegative integer"):
        await main.post_init(SimpleNamespace())
    connect.assert_not_awaited()
