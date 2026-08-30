from __future__ import annotations

import asyncio
import copy
import datetime as dt
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User

from ai_service import AIService
from bot_service import BotService
from database import Mutation, PersistenceError, StateConflict


class MemoryRepository:
    """Transactional test double; integration tests also exercise real PostgreSQL."""

    def __init__(self):
        self.states, self.profiles, self.events, self.locks = {}, {}, {}, {}
        self.fail_state = False
        self.fail_event = None

    async def load_state(self, chat):
        if self.fail_state:
            raise PersistenceError("Simulated read outage")
        return copy.deepcopy(self.states.get(chat, {}))

    async def save_state(self, chat, state):
        prior = self.states.get(chat, {})
        if state.get("_revision") != prior.get("_revision"):
            raise StateConflict("Conflict")
        state["_revision"] = prior.get("_revision", 0) + 1
        self.states[chat] = copy.deepcopy(state)

    async def load_profile(self, uid):
        return copy.deepcopy(self.profiles.get(uid))

    async def mutate_profile(self, uid, mutate, event_id=None):
        async with self.locks.setdefault(uid, asyncio.Lock()):
            if event_id and self.fail_event == event_id:
                raise PersistenceError("Simulated event write outage")
            profile = copy.deepcopy(self.profiles.get(uid, {}))
            if event_id and (uid, event_id) in self.events:
                return Mutation(profile, copy.deepcopy(self.events[uid, event_id]), False)
            result = mutate(profile)
            self.profiles[uid] = copy.deepcopy(profile)
            if event_id:
                self.events[uid, event_id] = copy.deepcopy(result)
            return Mutation(profile, result, True)

    async def recent_rewards(self, uid, limit=5):
        return [
            copy.deepcopy(v) for (owner, key), v in self.events.items() if owner == uid and key.startswith("claim:")
        ][-limit:]

    async def top_profiles(self):
        return []

    async def close(self):
        pass


def update(bot, uid=1, chat=100, text="/status", data=None, thread=None):
    user = User(uid, f"Player_{uid}*[safe]", is_bot=False)
    message = Message(
        10, dt.datetime.now(dt.timezone.utc), Chat(chat, "group"), from_user=user, text=text, message_thread_id=thread
    )
    message.set_bot(bot)
    if data is not None:
        query = CallbackQuery("query", user, "chat", message=message, data=data)
        query.set_bot(bot)
        return Update(1, callback_query=query)
    return Update(1, message=message)


@pytest.fixture
def rig():
    repo = MemoryRepository()
    bot = SimpleNamespace(username="guildventure_test_bot")
    for name in ["send_message", "send_photo", "edit_message_text", "answer_callback_query"]:
        setattr(bot, name, AsyncMock(return_value=SimpleNamespace(message_id=20)))
    ai = AIService()
    service = BotService(repo, ai, random.Random(4), default_images=False, free_roll_cooldown=0)
    context = SimpleNamespace(bot=bot)
    return SimpleNamespace(repo=repo, bot=bot, ai=ai, service=service, context=context)
