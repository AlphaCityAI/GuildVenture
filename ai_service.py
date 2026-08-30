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

import gameplay_content as content
from game_constants import CHAT_MODEL, IMAGE_MODEL, LORE_SUMMARY
from prompts import (
    FLAVOR_INSTRUCTIONS,
    SCENE_INSTRUCTIONS,
    ASSESS_INSTRUCTIONS,
    BOSS_FLAVOR_INSTRUCTIONS,
    NARRATE_INSTRUCTIONS,
    GAMEPLAY_INSTRUCTIONS,
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

    async def json_request(self, instruction, data, schema, max_tokens=450):
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
                            max_tokens=min(max_tokens, 1800),
                            response_format={"type": "json_object"},
                            messages=[
                                {
                                    "role": "system",
                                    "content": instruction
                                    + "\nReturn only JSON matching this schema:\n"
                                    + json.dumps(schema.model_json_schema())
                                    + "\n"
                                    + LORE_SUMMARY,
                                },
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
            {
                "objective": state["objective"],
                "history": state.get("narrative_log", []),
                "action": action[:1000],
                "campaign": state.get("campaign", {}).get("title"),
                "approach": state.get("chapter_approach"),
            },
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

    async def encounter(self, state):
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS + " Design a fresh boss fight. Moves repeat in the supplied order. "
            "Each move has an honest telegraph and a category players can use to weaken it. "
            "Include a damaging move. Only healing targets self; party effects have power <=4. "
            "Make phase two distinctive without inventing effects outside the schema.",
            {
                "boss": state["boss"],
                "location": state["location"],
                "floor": state["gauntlet_level"],
                "party": [
                    {"faction": p["faction"], "ally": p.get("ally", {}).get("name") if p.get("ally") else None}
                    for p in state["players"]
                ],
                "variation": state["run_id"],
            },
            content.EncounterDesign,
            1400,
        )

    async def ally(self, traits):
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS + " Create a recruit and one distinctive support skill. "
            "Strike, heal, or focus; value 1-6. It costs the owner's turn and has limited uses. "
            "Do not promise passive powers, resurrection, or extra turns.",
            traits,
            content.AllyDesign,
            600,
        )

    async def talents(self, profile, milestone):
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS + " Offer three different talent kinds for this career milestone. "
            "Vitality adds max HP; precision adds twice its value in roll points; restoration adds healing; force adds damage. "
            "Combined caps are HP +6, roll points +10, healing +3, damage +2. Describe only the declared benefit.",
            {"milestone": milestone, "chosen": profile.get("talents", []), "username": profile["username"]},
            content.TalentOffers,
            800,
        )

    async def forge(self, item, target_rarity):
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS
            + " Design an upgrade for this item. Keep the slot; choose a specialty and a new ability. "
            "The engine scales the skill's base value by rarity and supplies charges. Do not invent extra effects or costs.",
            {"source": item, "target_rarity": target_rarity},
            content.ForgeDesign,
            650,
        )

    async def campaign(self, players, location, variation):
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS
            + " Author a connected three-chapter campaign with escalating, achievable objectives. "
            "Each chapter offers two meaningfully different approaches. Future chapters should follow naturally from earlier goals.",
            {"party": players, "location": location, "variation": variation},
            content.CampaignPlan,
            1800,
        )

    async def chapter(self, state, approach):
        campaign = state["campaign"]
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS + " Develop the next chapter from the chosen approach and the saved outcomes. "
            "Preserve the campaign's continuity and this chapter's role in the outline. Make the objective achievable.",
            {
                "title": campaign["title"],
                "premise": campaign["premise"],
                "completed": campaign["completed"],
                "next_outline": campaign["chapters"][campaign["index"] + 1],
                "approach": approach,
                "recent_events": state.get("narrative_log", []),
            },
            content.Chapter,
            850,
        )

    async def victory(self, recap):
        return await self.json_request(
            GAMEPLAY_INSTRUCTIONS + " Celebrate this saved outcome in a short cinematic scene. "
            "Use the actual boss, factions, and recorded contributions. Include fallen participants respectfully. "
            "Do not invent contributions, numbers, loot, titles, or mechanical rewards.",
            recap,
            content.VictoryStory,
            500,
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
