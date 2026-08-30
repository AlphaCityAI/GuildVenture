"""Plain-text Telegram presentation: untrusted names never become markup."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter, TelegramError

from game_constants import FACTIONS
from item_traits import RARITY_DAMAGE_BONUS, get_damage_type_for_specialty

logger = logging.getLogger(__name__)


def button(label, data):
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Callback exceeds Telegram's byte limit")
    return InlineKeyboardButton(label, callback_data=data)


def markup(rows):
    return InlineKeyboardMarkup(rows) if rows else None


def chunks(text, limit=3900):
    """Split by UTF-16 units without breaking a Unicode code point."""
    part, units = [], 0
    for char in text or " ":
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            yield "".join(part)
            part, units = [], 0
        part.append(char)
        units += width
    if part:
        yield "".join(part)


def bar(current, maximum):
    maximum = max(1, maximum)
    current = max(0, min(current, maximum))
    fill = round(current / maximum * 10)
    return f"{'█' * fill}{'░' * (10 - fill)} {current}/{maximum}"


def effect_text(ability):
    effect = ability["effect"]
    if effect["type"] == "direct_damage":
        return f"{effect['value']} {effect.get('damage_type', '')} damage"
    if effect["type"] == "heal":
        return f"heal {'party' if effect.get('target') == 'party' else 'self'} {effect['value']} HP"
    if effect["type"] == "roll_bonus":
        return f"{effect['value']:+} next-roll points"
    return f"guarantee {effect.get('category', 'success')}"


def item_text(item):
    ability = item.get("ability")
    lines = [
        item.get("name", "Unknown item"),
        f"{item.get('rarity', '')} · {item.get('slot', '')} · {item.get('specialty', '')}",
    ]
    if ability:
        lines.append(f"{ability['name']}: {effect_text(ability)} ({ability.get('max_charges', 1)} charges/floor)")
    bonus = RARITY_DAMAGE_BONUS.get(item.get("rarity"), 0)
    lines.append(f"Passive: +{bonus:.0%} {get_damage_type_for_specialty(item.get('specialty', ''))} damage")
    # Legacy durability is retained in storage, but not presented as a mechanic.
    return "\n".join(lines)


def faction_text(faction, abilities):
    data = FACTIONS[faction]
    return "\n".join(
        [
            faction,
            data["description"],
            f"{data['hp']} HP · +{data['modifier_value']} {data['modifier_type']} skill",
            *[f"• {a['name']}: {effect_text(a)} ({a.get('charges', 'unlimited')} uses)" for a in abilities],
        ]
    )


async def telegram_call(method, **kwargs):
    for attempt in range(2):
        try:
            return await method(**kwargs)
        except RetryAfter as exc:
            delay = exc.retry_after.total_seconds() if isinstance(exc.retry_after, dt.timedelta) else exc.retry_after
            if attempt or delay > 10:
                raise
            await asyncio.sleep(delay)


async def send(bot, chat_id, thread_id, text, rows=None):
    pieces = list(chunks(text))
    message = None
    for index, piece in enumerate(pieces):
        message = await telegram_call(
            bot.send_message,
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=piece,
            parse_mode=None,
            reply_markup=markup(rows) if index == len(pieces) - 1 else None,
        )
    return message


async def panel(bot, chat_id, thread_id, text, rows, previous_message_id=None):
    if previous_message_id and len(list(chunks(text))) == 1:
        try:
            return await telegram_call(
                bot.edit_message_text,
                chat_id=chat_id,
                message_id=previous_message_id,
                text=text,
                parse_mode=None,
                reply_markup=markup(rows),
            )
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return None
            # Deleted or no-longer-editable panels are recovered with a fresh one.
    return await send(bot, chat_id, thread_id, text, rows)


async def reward_card(bot, chat_id, thread_id, text, image=None):
    if image:
        pieces = list(chunks(text, 1000))
        try:
            await telegram_call(
                bot.send_photo,
                chat_id=chat_id,
                message_thread_id=thread_id,
                photo=image,
                caption=pieces[0],
                parse_mode=None,
            )
        except TelegramError:
            logger.warning("Reward photo delivery failed; using text fallback")
        else:
            for piece in pieces[1:]:
                await send(bot, chat_id, thread_id, piece)
            return
    await send(bot, chat_id, thread_id, text)
