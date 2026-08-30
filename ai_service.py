"""Optional, bounded flavor generation; game mechanics never depend on AI damage."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import datetime as dt

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from game_constants import CHAT_MODEL, IMAGE_MODEL, LORE_SUMMARY
from prompts import (
    FLAVOR_INSTRUCTIONS,
    SCENE_INSTRUCTIONS,
    ASSESS_INSTRUCTIONS,
    BOSS_FLAVOR_INSTRUCTIONS,
    NARRATE_INSTRUCTIONS,
)

logger = logging.getLogger(__name__)


class Flavor(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str = Field(min_length=1, max_length=80)
    background: str = Field(min_length=1, max_length=300)


class CampaignAssessment(BaseModel):
    model_config = ConfigDict(strict=True)
    action_category: Literal["technology", "communication", "stealth", "strength"]
    skill_score: int = Field(ge=0, le=10)
    player_damage: int = Field(ge=0, le=10)
    event: Literal["none", "milestone_reached", "objective_complete"]
    milestone_id: str = Field(default="", max_length=80)


class Scene(BaseModel):
    model_config = ConfigDict(strict=True)
    objective: str = Field(min_length=1, max_length=180)
    opening_scene: str = Field(min_length=1, max_length=400)


class Narrative(BaseModel):
    model_config = ConfigDict(strict=True)
    text: str = Field(min_length=1, max_length=300)


class AIService:
    def __init__(
        self,
        api_key=None,
        client=None,
        chat_model=CHAT_MODEL,
        image_model=IMAGE_MODEL,
        timeout=20,
        image_timeout=45,
        max_concurrent=3,
        text_daily_limit=1000,
        image_daily_limit=100,
    ):
        self.client = client or (AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0) if api_key else None)
        self.chat_model, self.image_model = chat_model, image_model
        self.timeout, self.image_timeout = timeout, image_timeout
        self.limit = asyncio.Semaphore(max_concurrent)
        self.budgets = {"text": text_daily_limit, "image": image_daily_limit}
        self.usage = {"text": 0, "image": 0}
        self.usage_date = dt.datetime.now(dt.timezone.utc).date()

    def admit(self, kind):
        today = dt.datetime.now(dt.timezone.utc).date()
        if today != self.usage_date:
            self.usage = {"text": 0, "image": 0}
            self.usage_date = today
        if self.usage[kind] >= self.budgets[kind]:
            logger.warning("Local daily %s generation limit reached; using fallback", kind)
            return False
        self.usage[kind] += 1
        return True

    async def json_request(self, instruction, data, schema):
        if self.client is None or not self.admit("text"):
            return None
        # Queueing time is included in the deadline; a provider outage must not
        # create an unbounded wait behind the concurrency semaphore.
        try:
            async with asyncio.timeout(self.timeout), self.limit:
                for attempt in range(2):
                    try:
                        response = await self.client.chat.completions.create(
                            model=self.chat_model,
                            max_tokens=450,
                            response_format={"type": "json_object"},
                            messages=[
                                {"role": "system", "content": instruction + "\nReturn only JSON.\n" + LORE_SUMMARY},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                            ],
                        )
                        choice = response.choices[0]
                        if choice.finish_reason != "stop":
                            return None
                        return schema.model_validate_json(choice.message.content)
                    except (APIConnectionError, RateLimitError):
                        if attempt:
                            raise
                        await asyncio.sleep(0.5)
                    except APIStatusError as exc:
                        if exc.status_code < 500 or attempt:
                            raise
                        await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning(
                "AI request unavailable: %s request_id=%s", type(exc).__name__, getattr(exc, "request_id", None)
            )
        return None

    async def flavor(self, kind, traits):
        return await self.json_request(
            FLAVOR_INSTRUCTIONS,
            {"kind": kind, "traits": traits},
            Flavor,
        )

    async def scene(self, players, location):
        return await self.json_request(
            SCENE_INSTRUCTIONS,
            {"party": players, "location": location},
            Scene,
        )

    async def assess(self, state, action):
        return await self.json_request(
            ASSESS_INSTRUCTIONS,
            {"objective": state["objective"], "history": state.get("narrative_log", []), "action": action[:1000]},
            CampaignAssessment,
        )

    async def boss_flavor(self, boss, location):
        return await self.json_request(
            BOSS_FLAVOR_INSTRUCTIONS,
            {"archetype": boss["archetype"], "description": boss["description"], "location": location["name"]},
            Flavor,
        )

    async def narrate(self, outcome):
        return await self.json_request(
            NARRATE_INSTRUCTIONS,
            {"resolved_event": outcome},
            Narrative,
        )

    async def image(self, prompt):
        if self.client is None or not self.admit("image"):
            return None
        try:
            async with asyncio.timeout(self.image_timeout), self.limit:
                response = await self.client.images.generate(
                    model=self.image_model,
                    prompt="Textless hand-painted cyberpunk art: " + prompt[:600],
                    n=1,
                    size="1024x1024",
                    quality="medium",
                )
                data = response.data[0].b64_json
                return base64.b64decode(data, validate=True) if data else None
        except Exception as exc:
            logger.warning("Image unavailable: %s request_id=%s", type(exc).__name__, getattr(exc, "request_id", None))
            return None

    async def close(self):
        if self.client is not None:
            await self.client.close()
