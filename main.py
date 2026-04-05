import os
import json
import asyncio
import logging
import random
import re
import base64
import httpx
import copy
from typing import Optional, Tuple, List, Dict, Any
import datetime

from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# Import the error
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import database as db_layer

# Import constants, prompts, and external data
from locations import LOCATIONS
from abilities import ABILITIES
from bosstraits import BOSS_TRAITS
from game_constants import *
import prompts
import item_traits

# ───────── Logging setup ─────────
logging.basicConfig(
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ───────── OpenAI client ─────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY missing")
    raise SystemExit("Set OPENAI_API_KEY in environment")
client = AsyncOpenAI(api_key=OPENAI_API_KEY)
# CHAT_MODEL and IMAGE_MODEL are now imported from game_constants

# ───────── Helpers ─────────
def create_bar(current: int, total: int, length: int = 10) -> str:
    """Creates a generic text-based progress bar."""
    current = max(0, current)
    total = max(1, total)
    fill_count = int(round((current / total) * length))
    empty_count = length - fill_count
    return f"[{'█' * fill_count}{'░' * empty_count}]"

async def gpt_request(**kwargs):
    """Wrapper with retries."""
    backoff = 1
    for _ in range(3):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.warning("GPT call failed, retrying in %ds: %s", backoff, e)
            await asyncio.sleep(backoff)
            backoff *= 2
    raise RuntimeError("GPT calls failed after 3 retries")

async def generate_image(prompt: str) -> Optional[str]:
    """Return base64 image string or None."""
    # Use the prompt function from prompts.py
    enhanced_prompt = prompts.get_image_prompt(prompt)
    try:
        resp = await client.images.generate(model=IMAGE_MODEL, prompt=enhanced_prompt, n=1, size="1024x1024", quality="medium")
        img = resp.data[0]
        if getattr(img, "b64_json", None): return img.b64_json
        if getattr(img, "url", None):
            async with httpx.AsyncClient() as http_client:
                r = await http_client.get(img.url, timeout=30)
                r.raise_for_status()
                return base64.b64encode(r.content).decode("utf-8")
        raise Exception("No image data in response")
    except Exception as e:
        logger.error("Image gen error: %s", e, exc_info=True)
        return None

async def send_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int], text: str, reply_markup=None):
    """
    Sends a message to a specific chat and thread, optimized to not re-load state.
    Includes automatic retry logic for flood control.
    """
    text_to_send = (text or "")[:4090]
    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            message_thread_id=thread_id, 
            text=text_to_send, 
            reply_markup=reply_markup, 
            parse_mode="Markdown"
        )
    except RetryAfter as e:
        # If we get rate-limited, wait the specified time and retry once.
        logger.warning(f"Flood control exceeded. Retrying in {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        await context.bot.send_message(
            chat_id=chat_id, 
            message_thread_id=thread_id, 
            text=text_to_send, 
            reply_markup=reply_markup, 
            parse_mode="Markdown"
        )
    except Exception as e:
        # Log other potential send errors without crashing
        logger.error(f"Error in send_message: {e}", exc_info=True)


def get_rarity_and_level(roll: int) -> Tuple[str, str, int]:
    """Deterministic rarity bands."""
    if roll <= 35: return "Salvage", "⚪️", random.randint(1, 4)
    if roll <= 60: return "Gutter-Tech", "🟢", random.randint(5, 8)
    if roll <= 80: return "Street Mod", "🔵", random.randint(9, 12)
    if roll <= 94: return "Black Market", "🟣", random.randint(13, 16)
    if roll <= 99: return "Node-Forged", "🟡", random.randint(17, 19)
    return "Peerless", "💥", 20

def get_outcome_tier(score: float) -> str:
    if score <= 10: return "Catastrophic Failure"
    if score <= 30: return "Significant Failure"
    if score <= 50: return "Partial Failure"
    if score <= 70: return "Partial Success"
    if score <= 90: return "Significant Success"
    return "Tremendous Success"

def adjust_boss_damage_for_traits(raw_damage: int, state: dict, attacker: dict, action_category: Optional[str]):
    if raw_damage <= 0 or not state.get("boss"): return raw_damage, []
    boss = state["boss"]
    out, notes = float(raw_damage), []
    for s in boss.get("strengths", []):
        if s.get("type") == "faction_damage_resistance" and attacker.get("faction") == s.get("faction"):
            out *= float(s.get("value", 1.0)); notes.append(s.get("narrative", ""))
        if s.get("type") == "action_category_resistance" and action_category and action_category == s.get("category"):
            out *= float(s.get("value", 1.0)); notes.append(s.get("narrative", ""))
    return max(0, int(round(out))), [n for n in notes if n]

def compute_run_bonus(state: dict):
    attempted, defeated = int(state.get("gauntlet_bonus_attempted", 0)), int(state.get("gauntlet_bonus_defeated", 0))
    bonus = attempted + defeated
    return min(bonus, 60), attempted, defeated


def get_next_turn_index(players_list: List[dict], current_player_id: Optional[int], original_turn_index: int) -> int:
    """
    Calculates the next turn index, handling player deaths.
    `players_list` is the *new* list of players (after any deaths).
    `current_player_id` is the ID of the player who *just* acted.
    `original_turn_index` is the index they acted at.
    """
    if not players_list:
        return 0

    # Check if the player who acted is still alive
    new_index_of_actor = next((i for i, p in enumerate(players_list) if p['id'] == current_player_id), -1)

    if new_index_of_actor != -1:
        # Current player survived, advance turn from their new position
        return (new_index_of_actor + 1) % len(players_list)
    else:
        # Current player died. Advance from the original index, clamped to new list size.
        # If original_turn_index >= len(players_list), it wraps around automatically.
        return original_turn_index % len(players_list) if len(players_list) > 0 else 0

# ───────── Persistence (Game State) ─────────
async def load_state(chat_id: int) -> dict:
    return await db_layer.load_state(chat_id)

async def save_state(chat_id: int, state: dict):
    # Convert Enum members to their names for JSON serialization
    if 'game_stage' in state and isinstance(state['game_stage'], GameStage):
        state['game_stage'] = state['game_stage'].name
    await db_layer.save_state(chat_id, state)

# ───────── Persistence (Player Profiles) ─────────
async def load_profile(user_id: int) -> Optional[dict]:
    return await db_layer.load_profile(user_id)

async def save_profile(user_id: int, profile: dict):
    await db_layer.save_profile(user_id, profile)

async def get_or_create_profile(user_id: int, username: str) -> dict:
    profile = await load_profile(user_id)
    needs_save = False
    if profile is None:
        profile = {
            "username": username, "level": 1, "current_xp": 0,
            "xp_to_next_level": int(XP_BASE * (XP_MULTIPLIER ** 1)),
            "title": TITLES[0],
            "stats": {"bosses_attempted": 0, "bosses_defeated": 0, "highest_floor": 0, "moves_made": 0},
            "last_login_date": "1970-01-01",
            "inventory": [],
            "equipped_items": {"Cranial": None, "Chassis": None, "Equipment": None, "Mobility": None, "Companion": None}
        }
        needs_save = True
    # Ensure username is up-to-date
    if profile.get("username") != username:
        profile["username"] = username
        needs_save = True
    # Migrate old profiles that don't have inventory/equipped_items
    if "inventory" not in profile:
        profile["inventory"] = []
        needs_save = True
    if "equipped_items" not in profile:
        profile["equipped_items"] = {"Cranial": None, "Chassis": None, "Equipment": None, "Mobility": None, "Companion": None}
        needs_save = True
    if needs_save:
        await save_profile(user_id, profile)
    return profile

async def get_all_profiles() -> List[dict]:
    """Fetches all player profiles from the database."""
    return await db_layer.get_all_profiles()


async def award_xp(context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int], user_id: int, username: str, amount: int, reason: str):
    if amount <= 0: return
    profile = await get_or_create_profile(user_id, username)
    profile["current_xp"] += amount
    await send_message(context, chat_id, thread_id, f"⚡️ {username} gained *{amount} XP* ({reason})!")

    leveled_up = False
    while profile["current_xp"] >= profile["xp_to_next_level"]:
        leveled_up = True
        profile["level"] += 1
        profile["current_xp"] -= profile["xp_to_next_level"]
        profile["xp_to_next_level"] = int(XP_BASE * (XP_MULTIPLIER ** profile["level"]))
        if profile["level"] in TITLES:
            profile["title"] = TITLES[profile["level"]]
            await send_message(context, chat_id, thread_id, f"Congratulations {username}, you have earned the title: *{profile['title']}*!")

    if leveled_up:
        await send_message(context, chat_id, thread_id, f"🎉 *LEVEL UP!* {username} is now Level *{profile['level']}*!")
    await save_profile(user_id, profile)

# ───────── Game State Management ─────────
async def reset_game_state(chat_id: int, thread_id: Optional[int]):
    init = {"game_stage": GameStage.MAIN_MENU.name, "thread_id": thread_id, "players": [], "dead_players": [], "turn_index": 0, "owner_id": None, "narrative_log": [], "objective": None, "boss": None, "active_roll_bonuses": {}, "guaranteed_success": {}, "gauntlet_bonus_attempted": 0, "gauntlet_bonus_defeated": 0, "scout": None, "selected_route": None, "hazard_effect": None, "gauntlet_level": 0, "game_mode": None, "location": None, "location_interaction_used": False, "last_action_timestamp": datetime.datetime.utcnow().isoformat()}
    await save_state(chat_id, init)
    return init

async def _return_to_main_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, thread_id: Optional[int], message_to_edit: Optional[Update.message] = None, new_message_text: Optional[str] = None):
    """
    Safely resets the game state and sends the main menu, optionally editing a message first.
    """
    if message_to_edit and new_message_text:
        try:
            await message_to_edit.edit_text(new_message_text)
        except Exception as e:
            logger.warning(f"Couldn't edit message in _return_to_main_menu: {e}")

    await reset_game_state(chat_id, thread_id)
    keyboard = MAIN_MENU_KEYBOARD_LAYOUT
    await send_message(context, chat_id, thread_id, "The cycle begins anew. What's next?", reply_markup=InlineKeyboardMarkup(keyboard))

# ───────── Commands & Menus ─────────
async def venture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    thread_id = update.effective_message.message_thread_id

    # --- Active Game & Timeout Check ---
    state = await load_state(chat_id)
    if state and state.get("game_stage") != GameStage.MAIN_MENU.name:
        TIMEOUT_DURATION = datetime.timedelta(minutes=30)
        last_action_str = state.get("last_action_timestamp")
        is_stale = True # Assume stale if no timestamp
        if last_action_str:
            last_action_time = datetime.datetime.fromisoformat(last_action_str)
            if datetime.datetime.utcnow() - last_action_time < TIMEOUT_DURATION:
                is_stale = False

        if not is_stale:
            await update.message.reply_text("A game is already in progress. The owner can use /endgame to stop it, or you can wait for it to time out after 30 minutes of inactivity.")
            return
        else:
            await update.message.reply_text("An old game was found and has timed out due to inactivity. Starting a new one...")

    # Daily Login Bonus Check
    profile = await get_or_create_profile(user.id, user.first_name)
    today_str = datetime.date.today().isoformat()
    if profile.get("last_login_date") != today_str:
        await award_xp(context, chat_id, thread_id, user.id, user.first_name, DAILY_LOGIN_XP, "Daily Login Bonus")
        profile["last_login_date"] = today_str
        await save_profile(user.id, profile)

    await reset_game_state(chat_id, thread_id)
    # Use the keyboard layout from game_constants
    keyboard = MAIN_MENU_KEYBOARD_LAYOUT
    await update.message.reply_text("Welcome to the underbelly of Alpha City. What's your move?", reply_markup=InlineKeyboardMarkup(keyboard))

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides players with a well-formatted guide to the game."""
    # Use the text from game_constants
    await update.message.reply_text(INFO_COMMAND_TEXT, parse_mode="Markdown")

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the top players."""
    await update.message.reply_text("📡 Accessing the Alpha City Legends network...")
    top_profiles = await db_layer.get_top_profiles(10)

    if not top_profiles:
        await update.message.reply_text("The leaderboard is empty. Be the first legend!")
        return

    message_lines = ["🏆 *Alpha City Legends Leaderboard* 🏆\n"]
    for i, profile in enumerate(top_profiles):
        rank = i + 1
        name = profile.get('username', 'Unknown Agent')
        floor = profile.get('stats', {}).get('highest_floor', 0)
        defeated = profile.get('stats', {}).get('bosses_defeated', 0)
        message_lines.append(f"{rank}. *{name}* - 🏢 Floor: {floor}, 💀 Defeated: {defeated}")

    await update.message.reply_text("\n".join(message_lines), parse_mode="Markdown")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    profile = await get_or_create_profile(user.id, user.first_name)

    xp_bar = create_bar(profile['current_xp'], profile['xp_to_next_level'])

    message = (
        f"👤 *Player Dossier: {profile['username']}*\n"
        f"Title: *{profile['title']}*\n\n"
        f"📈 Level: *{profile['level']}*\n"
        f"XP: {xp_bar} {profile['current_xp']}/{profile['xp_to_next_level']}\n\n"
        f"📜 *Career Stats*\n"
        f"  - Bosses Attempted: {profile['stats']['bosses_attempted']}\n"
        f"  - Bosses Defeated: {profile['stats']['bosses_defeated']}\n"
        f"  - Highest Gauntlet Floor: {profile['stats']['highest_floor']}\n"
        f"  - Campaign Actions: {profile['stats']['moves_made']}"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display inventory and equipped items with management options."""
    user = update.effective_user
    profile = await get_or_create_profile(user.id, user.first_name)
    
    equipped = profile.get("equipped_items", {})
    inventory = profile.get("inventory", [])
    
    lines = ["🎒 *Your Inventory*\n"]
    lines.append("*Equipped Items:*")
    
    for slot in item_traits.ITEM_SLOTS:
        slot_icon = item_traits.get_slot_icon(slot)
        item = equipped.get(slot)
        if item:
            rarity_icon = item_traits.RARITY_ICONS.get(item.get("rarity", ""), "")
            lines.append(f"  {slot_icon} {slot}: {rarity_icon} {item.get('name', 'Unknown')}")
        else:
            lines.append(f"  {slot_icon} {slot}: _Empty_")
    
    lines.append(f"\n*Backpack* ({len(inventory)} items):")
    if not inventory:
        lines.append("  _Your backpack is empty._")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    
    if inventory:
        keyboard = []
        for i, item in enumerate(inventory[:10]):
            rarity_icon = item_traits.RARITY_ICONS.get(item.get("rarity", ""), "")
            slot_icon = item_traits.get_slot_icon(item.get("slot", ""))
            keyboard.append([InlineKeyboardButton(
                f"{rarity_icon} {item.get('name', 'Unknown')} ({slot_icon} {item.get('slot', '')})",
                callback_data=f"inv:view:{i}"
            )])
        if len(inventory) > 10:
            keyboard.append([InlineKeyboardButton("📜 Show More...", callback_data="inv:more:10")])
        await update.message.reply_text("Select an item to manage:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    if any(equipped.get(slot) for slot in item_traits.ITEM_SLOTS):
        keyboard = []
        for slot in item_traits.ITEM_SLOTS:
            if equipped.get(slot):
                slot_icon = item_traits.get_slot_icon(slot)
                keyboard.append([InlineKeyboardButton(f"Unequip {slot_icon} {slot}", callback_data=f"inv:unequip:{slot}")])
        if keyboard:
            await update.message.reply_text("Or unequip an item:", reply_markup=InlineKeyboardMarkup(keyboard))

async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inventory button callbacks."""
    query = update.callback_query
    user = query.from_user
    await query.answer()
    
    profile = await get_or_create_profile(user.id, user.first_name)
    inventory = profile.get("inventory", [])
    equipped = profile.get("equipped_items", {})
    
    parts = query.data.split(":")
    action = parts[1]
    
    if action == "view":
        try:
            item_index = int(parts[2])
            if item_index >= len(inventory):
                await query.edit_message_text("Item no longer exists in your inventory.")
                return
            item = inventory[item_index]
            display = item_traits.format_item_display(item)
            
            keyboard = [
                [InlineKeyboardButton("✅ Equip", callback_data=f"inv:equip:{item_index}")],
                [InlineKeyboardButton("🗑️ Discard", callback_data=f"inv:discard:{item_index}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="inv:back")]
            ]
            await query.edit_message_text(display, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except (ValueError, IndexError):
            await query.edit_message_text("Error loading item.")
    
    elif action == "equip":
        try:
            item_index = int(parts[2])
            if item_index >= len(inventory):
                await query.edit_message_text("Item no longer exists in your inventory.")
                return
            item = inventory[item_index]
            slot = item.get("slot")
            
            currently_equipped = equipped.get(slot)
            if currently_equipped:
                inventory.append(currently_equipped)
            
            equipped[slot] = item
            inventory.pop(item_index)
            
            profile["inventory"] = inventory
            profile["equipped_items"] = equipped
            await save_profile(user.id, profile)
            
            msg = f"✅ Equipped *{item.get('name')}* to {slot} slot!"
            if currently_equipped:
                msg += f"\n(Unequipped *{currently_equipped.get('name')}* to backpack)"
            await query.edit_message_text(msg, parse_mode="Markdown")
        except (ValueError, IndexError) as e:
            logger.error(f"Equip error: {e}")
            await query.edit_message_text("Error equipping item.")
    
    elif action == "unequip":
        slot = parts[2]
        if slot not in equipped or not equipped.get(slot):
            await query.edit_message_text(f"No item equipped in {slot} slot.")
            return
        
        item = equipped[slot]
        inventory.append(item)
        equipped[slot] = None
        
        profile["inventory"] = inventory
        profile["equipped_items"] = equipped
        await save_profile(user.id, profile)
        
        await query.edit_message_text(f"✅ Unequipped *{item.get('name')}* from {slot} slot.", parse_mode="Markdown")
    
    elif action == "discard":
        try:
            item_index = int(parts[2])
            if item_index >= len(inventory):
                await query.edit_message_text("Item no longer exists.")
                return
            item = inventory.pop(item_index)
            profile["inventory"] = inventory
            await save_profile(user.id, profile)
            await query.edit_message_text(f"🗑️ Discarded *{item.get('name')}*.", parse_mode="Markdown")
        except (ValueError, IndexError):
            await query.edit_message_text("Error discarding item.")
    
    elif action == "more":
        try:
            start_index = int(parts[2])
            keyboard = []
            for i, item in enumerate(inventory[start_index:start_index+10], start=start_index):
                rarity_icon = item_traits.RARITY_ICONS.get(item.get("rarity", ""), "")
                slot_icon = item_traits.get_slot_icon(item.get("slot", ""))
                keyboard.append([InlineKeyboardButton(
                    f"{rarity_icon} {item.get('name', 'Unknown')} ({slot_icon} {item.get('slot', '')})",
                    callback_data=f"inv:view:{i}"
                )])
            if start_index + 10 < len(inventory):
                keyboard.append([InlineKeyboardButton("📜 Show More...", callback_data=f"inv:more:{start_index+10}")])
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="inv:back")])
            await query.edit_message_text("Select an item to manage:", reply_markup=InlineKeyboardMarkup(keyboard))
        except (ValueError, IndexError):
            await query.edit_message_text("Error loading more items.")
    
    elif action == "back":
        keyboard = []
        for i, item in enumerate(inventory[:10]):
            rarity_icon = item_traits.RARITY_ICONS.get(item.get("rarity", ""), "")
            slot_icon = item_traits.get_slot_icon(item.get("slot", ""))
            keyboard.append([InlineKeyboardButton(
                f"{rarity_icon} {item.get('name', 'Unknown')} ({slot_icon} {item.get('slot', '')})",
                callback_data=f"inv:view:{i}"
            )])
        if len(inventory) > 10:
            keyboard.append([InlineKeyboardButton("📜 Show More...", callback_data="inv:more:10")])
        if keyboard:
            await query.edit_message_text("Select an item to manage:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("Your backpack is empty.")

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    if update.effective_message.message_thread_id != thread_id: return
    if state.get("game_stage") == GameStage.GAUNTLET.name and state.get("boss"):
        if any(p['id'] == user.id for p in state.get("players", [])) or any(p['id'] == user.id for p in state.get("dead_players", [])): return await update.message.reply_text("You are already participating in this fight.")
        await update.message.reply_text(f"{user.first_name} is joining the boss fight! Choose your faction from the main menu.")
        hp_per_player = 15 + (5 * (state.get("gauntlet_level", 1) - 1))
        state["boss"]["hp"] += hp_per_player
        state["boss"]["max_hp"] += hp_per_player
        await save_state(chat_id, state)
        await send_message(context, chat_id, thread_id, f"⚡️ *A new challenger appears!* {state['boss']['name']} adapts — its health increases!")
        return
    if state.get("game_stage") != GameStage.FACTION_SELECT.name: return await update.message.reply_text("There is no active campaign to join right now. Use /venture to start one.")
    if any(p['id'] == user.id for p in state.get("players", [])): return await update.message.reply_text("You are already in the campaign.")
    if any(p['id'] == user.id for p in state.get("dead_players", [])): return await update.message.reply_text("You have fallen in this campaign. You cannot rejoin until the next one.")
    await update.message.reply_text(f"{user.first_name} wants to join the fight! Please choose your faction from the menu above.")

async def endgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    state = await load_state(chat_id)

    if update.effective_message.message_thread_id != state.get("thread_id"): return
    if state.get("owner_id") != user_id: return await update.message.reply_text("Only the game owner can end the adventure.")
    thread_id = update.effective_message.message_thread_id
    await reset_game_state(chat_id, thread_id)
    await update.message.reply_text("The current adventure has been ended. Use /venture to start a new one.")

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    state = await load_state(chat_id)

    if query.message.message_thread_id != state.get("thread_id"): return await query.answer()
    await query.answer()
    action = query.data.split(":")[1]
    user = query.from_user

    state["owner_id"], state["game_mode"], state["players"], state["dead_players"] = user.id, action, [], []
    state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat() # Timestamp the start
    if action == "gauntlet":
        state["game_stage"], state["gauntlet_level"], state["gauntlet_bonus_attempted"], state["gauntlet_bonus_defeated"], state["turn_index"] = GameStage.SCOUTING.name, 1, 0, 0, 0
        loading_message = await query.edit_message_text("Scanning the datastream for viable routes...")
        await save_state(chat_id, state)
        await start_scouting(context, chat_id, state)
        try: await context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)
        except Exception as e: logger.warning(f"Could not delete loading message: {e}")
    elif action == "open_campaign":
        state["game_stage"] = GameStage.FACTION_SELECT.name
        await save_state(chat_id, state)
        factions = list(FACTIONS.keys())
        keyboard = [[InlineKeyboardButton(f, callback_data=f"faction:{f}") for f in factions[i:i + 2]] for i in range(0, len(factions), 2)]
        await query.edit_message_text("A new adventure awaits. The first player to choose a faction begins. Others may /join.", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action == "hire_help": await generate_and_send_reward(context, chat_id, user, "character", 1)
    elif action == "dig_treasure": await generate_and_send_reward(context, chat_id, user, "item", 1)

# ───────── Scouting for Gauntlet ─────────
def weighted_choice(options: List[Tuple[str, int]]) -> str:
    total = sum(w for _, w in options)
    r = random.uniform(0, total)
    acc = 0
    for val, w in options:
        acc += w
        if r <= acc: return val
    return options[-1][0]

def pick_three_boss_archetypes() -> List[Tuple[str, float]]:
    keys = list(BOSS_TRAITS.keys())
    random.shuffle(keys)
    return [(keys[0], 0.60), (keys[1], 0.30), (keys[2], 0.10)]

async def start_scouting(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    odds, hazard = pick_three_boss_archetypes(), random.choice(HAZARDS)
    state["scout"], state["selected_route"] = {"odds": odds, "hazard": hazard}, None
    await save_state(chat_id, state)
    lines = ["📡 *Scouting Report*", "Likely bosses:"] + [f"• {name}: {int(p*100)}%" for name, p in odds] + [f"\nGlobal hazard: *{hazard['label']}*"]
    bonus, a, d = compute_run_bonus(state)
    lines.append(f"\nRun Bonus: *+{bonus}%* (Attempted {a}, Defeated {d})")
    keyboard = [
        [InlineKeyboardButton(f"💥 Route: {GAUNTLET_ROUTES['adrenal']['name']}", callback_data="route:adrenal")],
        [InlineKeyboardButton(f"🧪 Route: {GAUNTLET_ROUTES['juiced_up']['name']}", callback_data="route:juiced_up")],
        [InlineKeyboardButton(f"⚙️ Route: {GAUNTLET_ROUTES['default']['name']}", callback_data="route:default")]
    ]
    desc = f"\n\nChoose your combat modifier:\n" + "\n".join([f"• *{r['name']}*: {r['blurb']}" for r in GAUNTLET_ROUTES.values()])
    await send_message(context, chat_id, state.get("thread_id"), "\n".join(lines) + desc, reply_markup=InlineKeyboardMarkup(keyboard))

async def route_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")
    if query.message.message_thread_id != thread_id: return await query.answer()
    if state.get("game_stage") != GameStage.SCOUTING.name: return await query.answer("No active scouting.", show_alert=True)
    await query.answer()

    route_key = query.data.split(":")[1]
    state["selected_route"] = route_key
    r = GAUNTLET_ROUTES[route_key]

    # If this is a subsequent floor, skip faction selection
    if state.get("gauntlet_level", 1) > 1:
        state["game_stage"] = GameStage.GAUNTLET.name
        loading_message = await query.edit_message_text(f"Route selected: *{r['name']}* — {r['blurb']}\n\nThe next floor materializes. Prepare for combat.", parse_mode="Markdown")
        await save_state(chat_id, state)
        await start_gauntlet_floor(context, chat_id)
        try: await context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)
        except Exception as e: logger.warning(f"Could not delete loading message: {e}")
    else: # This is the first floor, proceed to faction selection
        state["game_stage"] = GameStage.FACTION_SELECT.name
        await save_state(chat_id, state)
        await query.edit_message_text(f"Route selected: *{r['name']}* — {r['blurb']}\n\nThe operation is a go. Choose your faction to begin. Others may /join.", parse_mode="Markdown")
        factions = list(FACTIONS.keys())
        keyboard = [[InlineKeyboardButton(f, callback_data=f"faction:{f}") for f in factions[i:i + 2]] for i in range(0, len(factions), 2)]
        await send_message(context, chat_id, thread_id, "Select your faction:", reply_markup=InlineKeyboardMarkup(keyboard))


# ───────── Rewards ─────────
async def generate_and_send_reward(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, reward_type: str, min_roll: int, gauntlet_bonus: int = 0):
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")
    await send_message(context, chat_id, thread_id, f"🎲 Rolling the dice for a new {reward_type}...")

    try:
        base_roll = random.randint(min_roll, 100)
        run_bonus, a, d = compute_run_bonus(state)

        # Apply bonus: +1 per attempt, +1 per defeat (simple unified system, capped at 60)
        final_roll = min(95, base_roll + run_bonus)

        rarity, rarity_icon, level = get_rarity_and_level(final_roll)

        # Update breakdown string
        breakdown = f"Roll: {base_roll}"
        if run_bonus > 0:
            breakdown += f" +{run_bonus} (from {a} attempt(s), {d} defeat(s))"
        breakdown += f" = *{final_roll}* → *{rarity}*"

        await send_message(context, chat_id, thread_id, f"🧪 {breakdown}")

        # Award XP for generating the item/character
        await award_xp(context, chat_id, thread_id, user.id, user.first_name, XP_BY_RARITY.get(rarity, 0), f"generating a {rarity} {reward_type}")

        if reward_type == "item":
            slot, specialty = random.choice(ITEM_SLOTS), random.choice(ITEM_SPECIALTIES)
            # Use prompt function
            prompt = prompts.get_item_reward_prompt(rarity, slot, specialty)
        else:
            ally_faction = random.choice(ALL_FACTIONS_LIST)
            # Use prompt function
            prompt = prompts.get_char_reward_prompt(rarity, ally_faction)

        response = await gpt_request(model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
        try:
            content = json.loads(response.choices[0].message.content)
            name, background = content.get("name", f"Unnamed {reward_type.capitalize()}"), content.get("background", "No background available.")
        except Exception: 
            await send_message(context, chat_id, thread_id, "Error parsing reward details from AI. Please try again.")
            return

        await save_state(chat_id, state)
        if reward_type == "item":
            durability = random.randint(1, 3)
            item_data = item_traits.create_item_data(name, slot, specialty, rarity, background, durability)
            
            profile = await get_or_create_profile(user.id, user.first_name)
            profile["inventory"].append(item_data)
            await save_profile(user.id, profile)
            
            ability = item_data.get("ability")
            ability_text = ""
            if ability:
                effect = ability.get("effect", {})
                effect_desc = ""
                if effect.get("type") == "direct_damage":
                    effect_desc = f"Deal {effect.get('value', 0)} {effect.get('damage_type', '')} damage"
                elif effect.get("type") == "heal":
                    if effect.get("target") == "party":
                        effect_desc = f"Heal party for {effect.get('value', 0)} HP"
                    else:
                        effect_desc = f"Heal for {effect.get('value', 0)} HP"
                elif effect.get("type") == "roll_bonus":
                    effect_desc = f"+{effect.get('value', 0)} to next roll"
                ability_text = f"\n⚡ *Ability*: {ability.get('name')} ({ability.get('max_charges', 1)} charges)\n   _{effect_desc}_"
            
            damage_type = item_traits.get_damage_type_for_specialty(specialty)
            damage_bonus = item_traits.get_damage_bonus_for_specialty(specialty, rarity)
            passive_text = f"\n📈 *Passive*: +{int(damage_bonus * 100)}% {damage_type} damage" if damage_bonus > 0 else ""
            
            caption = f"*{name}*\n🔩 *Slot*: {slot}\n✨ *Specialty*: {specialty}\n{rarity_icon} *Rarity*: {rarity}\n🛠️ *Durability*: {durability}/3{ability_text}{passive_text}\n\n_{background}_\n\n✅ _Added to your inventory! Use /inventory to equip._"
            image_prompt = f"A grimdark cyberpunk item. Slot: {slot}, Specialty: {specialty}. Name: {name}. Desc: {background}."
        else:
            caption = f"*{name}*\n{faction_icon(ally_faction)} *Faction*: {ally_faction}\n⚡ *Level*: {level}\n\n_{background}_"
            image_prompt = f"Cyberpunk character from {ally_faction}: {name}. {background}."

        await send_message(context, chat_id, thread_id, "Please wait, generating visual data...")
        b64 = await generate_image(image_prompt)
        if b64:
            img = base64.b64decode(b64)
            await context.bot.send_photo(chat_id=chat_id, photo=img, caption=caption, message_thread_id=thread_id, parse_mode="Markdown")
        else: 
            await send_message(context, chat_id, thread_id, caption) # Send text caption as fallback

    except Exception as e:
        logger.error(f"Critical error in reward generation: {e}", exc_info=True)
        try:
            await send_message(context, chat_id, thread_id, "A critical error occurred during generation. Unable to grant reward.")
        except Exception as e2:
            logger.error(f"Failed to send error message: {e2}")
        return # Exit gracefully

# ───────── Faction selection ─────────
async def faction_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    user = query.from_user
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    if query.message.message_thread_id != thread_id: return await query.answer()
    if state.get("game_stage") != GameStage.FACTION_SELECT.name: return await query.answer("Faction selection is not active.", show_alert=True)
    if any(p['id'] == user.id for p in state.get("players", [])): return await query.answer("You have already chosen a faction.", show_alert=True)
    await query.answer()
    faction_name = query.data.split(":", 1)[1]
    faction_data = FACTIONS[faction_name]
    
    faction_abilities = [copy.deepcopy(ability) for ability in ABILITIES.get(faction_name, [])]
    
    profile = await get_or_create_profile(user.id, user.first_name)
    equipped = profile.get("equipped_items", {})
    item_abilities = item_traits.get_abilities_from_equipped_items(equipped, reset_charges=True)
    
    all_abilities = faction_abilities + item_abilities
    
    new_player = {
        "id": user.id, 
        "username": user.first_name, 
        "faction": faction_name, 
        "hp": faction_data["hp"], 
        "max_hp": faction_data["hp"], 
        "modifier_type": faction_data["modifier_type"], 
        "modifier_value": faction_data["modifier_value"], 
        "abilities": all_abilities,
        "equipped_items": equipped
    }
    state["players"].append(new_player)
    state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat()
    await send_message(context, chat_id, thread_id, f"{user.first_name} has joined as a {faction_name}!")
    if len(state["players"]) == 1:
        game_mode = state.get("game_mode")
        if game_mode == "gauntlet":
            loading_message = await query.edit_message_text("The gauntlet materializes. Prepare for combat.")
            await save_state(chat_id, state)
            await start_gauntlet_floor(context, chat_id)
            try: await context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)
            except Exception as e: logger.warning(f"Could not delete loading message: {e}")
        elif game_mode == "open_campaign":
            state.update({"game_stage": GameStage.LEVEL_1.name})
            loading_message = await query.edit_message_text("Generating your open world...")
            await start_level(context, chat_id, state)
            try: await context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)
            except Exception as e: logger.warning(f"Could not delete loading message: {e}")
    else: await save_state(chat_id, state)

# ───────── Core Gameplay Loop ─────────
def guess_action_category(text: str) -> str:
    if re.search(r'sneak|hide|disguise|ambush|shadow|stealth', text, re.I): return 'stealth'
    if re.search(r'hack|disable|tech|interface|override|breach', text, re.I): return 'technology'
    if re.search(r'talk|persuade|intimidate|negotiate|bluff|rally', text, re.I): return 'communication'
    if re.search(r'smash|break|force|strike|shoot|punch|kick', text, re.I): return 'strength'
    return 'stealth'

async def handle_player_action(update: Update, context: ContextTypes.DEFAULT_TYPE, player_action: str):
    chat_id = update.effective_chat.id
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    players = state.get("players", [])
    if not players: return
    turn_index = state.get("turn_index", 0)
    current_player = players[turn_index]
    is_boss_fight = state.get("boss") is not None
    action_for_dm, is_ability, dm_note, ability_dealt_damage = player_action, player_action.startswith("[ABILITY]:"), "", False

    if is_ability:
        ability_name = player_action.split(":", 1)[1]
        ability_data = next((a for a in current_player.get("abilities", []) if a['name'] == ability_name), None)
        if not ability_data: return await send_message(context, chat_id, thread_id, "Error: Ability not found.")
        luck_roll, multiplier, luck_descriptor = random.randint(1, 10), 1.0, ""
        if luck_roll == 1: multiplier, luck_descriptor = 0.0, "a complete failure"
        elif 2 <= luck_roll <= 4: multiplier, luck_descriptor = 0.75, "weaker than expected"
        elif luck_roll == 5: luck_descriptor = "as expected"
        elif 6 <= luck_roll <= 9: multiplier, luck_descriptor = 1.25, "stronger than expected"
        elif luck_roll == 10: multiplier, luck_descriptor = 2.0, "a critical success"
        effect = ability_data['effect']
        if luck_roll == 1: dm_note = f"The player tried to use '{ability_name}', but the outcome was {luck_descriptor}. It had no effect. Narrate this failure."
        else:
            if effect['type'] == 'direct_damage' and state.get("boss"):
                base_damage = int(round(effect['value'] * multiplier))
                
                damage_type = effect.get('damage_type', '')
                equipped = current_player.get("equipped_items", {})
                item_multiplier = item_traits.calculate_equipped_damage_bonus(equipped, damage_type)
                base_damage = int(round(base_damage * item_multiplier))
                
                adjusted, notes = adjust_boss_damage_for_traits(base_damage, state, current_player, None)
                if state.get("selected_route") == "adrenal":
                    adjusted = int(adjusted * 1.5)
                if adjusted > 0:
                    state['boss']['hp'] -= adjusted; ability_dealt_damage = True
                    item_bonus_text = f" (+{int((item_multiplier - 1) * 100)}% from items)" if item_multiplier > 1 else ""
                    dm_note = f"The player used '{ability_name}'. The outcome was {luck_descriptor}, dealing exactly {adjusted} damage{item_bonus_text}."
                    if notes: dm_note += f" It also triggered: {', '.join(notes)}."
                else: dm_note = f"The player used '{ability_name}', but it was resisted and dealt no damage."
            elif effect['type'] == 'heal':
                modified_heal = int(round(effect['value'] * multiplier))
                if state.get("selected_route") == "juiced_up":
                    modified_heal *= 2
                if modified_heal > 0:
                    target = effect.get('target', 'self')
                    if target == 'self':
                        current_player['hp'] = min(current_player['max_hp'], current_player['hp'] + modified_heal)
                        await send_message(context, chat_id, thread_id, f"⚕️ *{current_player['username']}* recovers *{modified_heal} HP*!\n{create_bar(current_player['hp'], current_player['max_hp'])} {current_player['hp']}/{current_player['max_hp']}")
                    else:
                        for p in players: p['hp'] = min(p['max_hp'], p['hp'] + modified_heal)
                        party_status = "\n".join([f"{p['username']}: {create_bar(p['hp'], p['max_hp'])} {p['hp']}/{p['max_hp']}" for p in players])
                        await send_message(context, chat_id, thread_id, f"⚕️ The party recovers *{modified_heal} HP*!\n{party_status}")
                    dm_note = f"The player used '{ability_name}'. Outcome: {luck_descriptor}, healing for {modified_heal} HP."
            elif effect['type'] == 'roll_bonus':
                modified_bonus = int(round(effect['value'] * multiplier))
                if modified_bonus != 0:
                    state.setdefault("active_roll_bonuses", {})
                    target = effect.get('target', 'self')
                    if target == 'enemy': state['active_roll_bonuses']['boss'] = state.get('active_roll_bonuses', {}).get('boss', 0) + modified_bonus
                    elif target == 'party':
                        for p in players: state['active_roll_bonuses'][str(p['id'])] = state['active_roll_bonuses'].get(str(p['id']), 0) + modified_bonus
                    else: state['active_roll_bonuses'][str(current_player['id'])] = state['active_roll_bonuses'].get(str(current_player['id']), 0) + modified_bonus
                    dm_note = f"The player used '{ability_name}'. Outcome: {luck_descriptor}, applying a roll modifier of {modified_bonus:+}."
            elif effect['type'] == 'guaranteed_success':
                cat = effect.get("category")
                if cat: state.setdefault("guaranteed_success", {})[str(current_player['id'])] = cat; dm_note = f"The player used '{ability_name}'. Outcome: {luck_descriptor}, guaranteeing success on their next '{cat}' action."

    luck_score, roll_bonus_map = random.randint(1, 10), state.get("active_roll_bonuses", {})
    personal_roll_bonus = roll_bonus_map.pop(str(current_player['id']), 0)
    action_category_for_location = guess_action_category(action_for_dm if not is_ability else current_player.get("modifier_type", "stealth"))
    location_bonus, location_narrative_parts = 0, []

    def maybe_apply(effect):
        nonlocal location_bonus, location_narrative_parts
        if not effect: return
        if effect.get('type') == 'modifier' and effect.get('category') == action_category_for_location:
            location_bonus += effect.get('value', 0)
            if effect.get('narrative'): location_narrative_parts.append(effect['narrative'])
        elif effect.get('type') == 'faction_modifier':
            if current_player['faction'] in (effect['faction'] if isinstance(effect['faction'], list) else [effect['faction']]) and action_category_for_location in (effect['category'] if isinstance(effect['category'], list) else [effect['category']]):
                location_bonus += effect.get('value', 0)
                if effect.get('narrative'): location_narrative_parts.append(effect['narrative'])
    maybe_apply(state.get("location", {}).get("effect")); maybe_apply(state.get("hazard_effect"))
    guaranteed_success = state.get("guaranteed_success", {}).pop(str(current_player['id']), None) == action_category_for_location

    if is_boss_fight:
        boss, players_status = state["boss"], "\n".join([f"- {p['username']} (ID: {p['id']}) ({p['faction']}): {p['hp']}/{p['max_hp']} HP" for p in players])

        # Randomly select a boss ability to ensure variety and pass it to the AI
        chosen_boss_ability = None
        if boss.get("abilities"):
            chosen_boss_ability = random.choice(boss["abilities"])

        # Use prompt functions
        system_prompt = prompts.get_boss_fight_system_prompt()
        user_prompt = prompts.get_boss_fight_user_prompt_base(
            players_status, current_player, boss, action_for_dm,
            luck_score, personal_roll_bonus, location_bonus, dm_note
        )

        if chosen_boss_ability:
            ability_effects = chosen_boss_ability.get('effects', [])
            target_note = ""
            for effect in ability_effects:
                # Check for direct damage targeting a single entity
                if effect.get("type") == "direct_damage" and effect.get("target") == "single":
                    target_note = f" IMPORTANT: The 'single' target for this ability is the current player who just acted: {current_player['username']} (ID: {current_player['id']})."

            user_prompt += f"\nPre-selected Boss Ability: {chosen_boss_ability['name']} ({chosen_boss_ability['description']}) - Effects: {json.dumps(ability_effects)}.{target_note}"

        try:
            response = await gpt_request(model=CHAT_MODEL, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], response_format={"type": "json_object"})
            result = json.loads(response.choices[0].message.content)
        except Exception as e: 
            logger.error("Boss turn error: %s", e)
            await send_message(context, chat_id, thread_id, "A critical simulation error occurred. Please try again.")
            state["is_processing_turn"] = False # <-- UNLOCK
            await save_state(chat_id, state)
            return

        player_narrative, boss_damage_raw = result.get('player_narrative', "The outcome is uncertain."), result.get('boss_damage', 0)
        boss_narrative, player_damage_map = result.get('boss_narrative', f"{boss['name']} stares."), result.get('player_damage', {})
        boss_ability_choice = chosen_boss_ability['name'] if chosen_boss_ability else "an unknown power"

        if is_ability and ability_dealt_damage: boss_damage_raw = 0
        if not is_ability:
            # Non-ability actions are not allowed in boss fights per new logic
            await send_message(context, chat_id, thread_id, "In the heat of a boss battle, you must use one of your abilities! Type /venture to restart if stuck.")
            state["is_processing_turn"] = False # <-- UNLOCK
            await save_state(chat_id, state)
            await prompt_for_next_action(context, chat_id, state)
            return

        else: # Ability was used
            await send_message(context, chat_id, thread_id, f"*{current_player['username']}'s Turn:*\n{player_narrative}")
        if boss_damage_raw > 0:
            adj_dmg, notes = adjust_boss_damage_for_traits(boss_damage_raw, state, current_player, action_category_for_location)
            state['boss']['hp'] -= adj_dmg
            await send_message(context, chat_id, thread_id, f"💥 You dealt *{adj_dmg} damage* to {boss['name']}!" + ("\n" + "\n".join([f"_{n}_" for n in notes if n]) if notes else ""))
        await send_message(context, chat_id, thread_id, f"*{boss['name']}*\n{create_bar(state['boss']['hp'], state['boss']['max_hp'])} {state['boss']['hp']}/{state['boss']['max_hp']}")

        # THIS IS THE FIX
        if state['boss']['hp'] <= 0:
            state["is_processing_turn"] = False # <-- UNLOCK
            await save_state(chat_id, state)
            return await run_epilogue(context, chat_id, state)

        await asyncio.sleep(1.5)

        # Process any boss healing effects before retaliation
        if chosen_boss_ability:
            for effect in chosen_boss_ability.get("effects", []):
                if effect.get("type") == "heal" and effect.get("target") == "self":
                    heal_amount = effect.get("value", 0)
                    if state.get("selected_route") == "juiced_up":
                        heal_amount *= 2
                    if heal_amount > 0:
                        boss['hp'] = min(boss['max_hp'], boss['hp'] + heal_amount)
                        await send_message(context, chat_id, thread_id, f"✨ {boss['name']} regenerates *{heal_amount} HP*!")
                        await send_message(context, chat_id, thread_id, f"*{boss['name']}*\n{create_bar(boss['hp'], boss['max_hp'])} {boss['hp']}/{boss['max_hp']}")

        await send_message(context, chat_id, thread_id, f"*{boss['name']}'s Retaliation ({boss_ability_choice}):*\n{boss_narrative}")

        turn_index = state.get('turn_index', 0)
        current_player_id = players[turn_index]['id'] if players and turn_index < len(players) else None

        state = await apply_boss_damage(context, chat_id, state, player_damage_map)
        if not state.get("players"): 
            state["is_processing_turn"] = False # <-- UNLOCK
            await save_state(chat_id, state)
            return # End if all players were defeated

        state['turn_index'] = get_next_turn_index(state.get("players", []), current_player_id, turn_index)

        state['narrative_log'] = (state.get('narrative_log', []) + [player_narrative, boss_narrative])[-4:]
    else: # Open Campaign
        profile = await get_or_create_profile(current_player['id'], current_player['username'])
        profile['stats']['moves_made'] += 1
        await save_profile(current_player['id'], profile)

        # Use prompt functions
        system_prompt = prompts.get_open_campaign_system_prompt()
        player_list_str = ", ".join([p['username'] for p in players])
        last_scene = state.get('narrative_log', [''])[-1]
        objective = state.get('objective')

        user_prompt = prompts.get_open_campaign_user_prompt(
            player_list_str, current_player, objective, last_scene, 
            action_for_dm, luck_score, personal_roll_bonus, location_bonus, dm_note
        )

        try:
            response = await gpt_request(model=CHAT_MODEL, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], response_format={"type": "json_object"})
            result = json.loads(response.choices[0].message.content)
        except Exception as e: 
            logger.error("Open Campaign turn error: %s", e)
            await send_message(context, chat_id, thread_id, "A critical simulation error occurred. Please try again.")
            state["is_processing_turn"] = False # <-- UNLOCK
            await save_state(chat_id, state)
            return

        narrative, player_damage_raw, event = result.get('narrative', "..."), result.get('player_damage', 0), result.get('event', 'none')
        player_damage = max(0, player_damage_raw) # FIX: Ensure damage from AI is never negative

        if event == "milestone_reached": await award_xp(context, chat_id, thread_id, current_player['id'], current_player['username'], XP_FOR_MILESTONE, "reaching a campaign milestone")
        if not is_ability:
            skill_score, action_category = result.get('skill_score', 5), result.get('action_category', action_category_for_location)
            mod = current_player['modifier_value'] if action_category == current_player['modifier_type'] else 0
            final_score = (skill_score + mod + (personal_roll_bonus / 10) + (location_bonus / 10)) * (10 if guaranteed_success else luck_score)
            full_narrative = f"⚙️ Skill: {skill_score}{f' (+{mod} Faction)' if mod > 0 else ''}{f' ({personal_roll_bonus:+d} Bonus)' if personal_roll_bonus else ''}{f' ({location_bonus:+d} Location)' if location_bonus else ''}{' | ✅ Guaranteed' if guaranteed_success else ''} | 🎲 Luck: {luck_score} | *Total: {final_score:.1f}* ({get_outcome_tier(final_score)})\n\n{narrative}"
        else: full_narrative = narrative
        if " ".join(location_narrative_parts).strip(): full_narrative += f"\n\n_{' '.join(location_narrative_parts).strip()}_"
        if player_damage > 0:
            current_player['hp'] -= player_damage
            full_narrative += f"\n\n{current_player['username']} takes *{player_damage} damage*! HP is now {current_player['hp']}/{current_player['max_hp']}."
        await send_message(context, chat_id, thread_id, full_narrative)

        current_player_id = current_player['id'] # Get ID before potential death
        if current_player['hp'] <= 0:
            await send_message(context, chat_id, thread_id, f"💀 {current_player['username']} has fallen!")
            state['dead_players'].append(current_player)
            original_index = turn_index
            state['players'] = [p for p in state['players'] if p['id'] != current_player['id']]
            if not state['players']:
                state["is_processing_turn"] = False # <-- UNLOCK
                await save_state(chat_id, state)
                return await send_message(context, chat_id, thread_id, "All players have fallen. Game over.") or await reset_game_state(chat_id, thread_id)

            state['turn_index'] = get_next_turn_index(state.get("players", []), current_player_id, original_index)
        else:
            state['turn_index'] = get_next_turn_index(state.get("players", []), current_player_id, turn_index)

        state['narrative_log'] = (state.get('narrative_log', []) + [narrative])[-3:]

    state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat()
    state["is_processing_turn"] = False # <-- UNLOCK
    await save_state(chat_id, state)
    await prompt_for_next_action(context, chat_id, state)

async def start_level(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    """Generates the starting scenario for an open campaign with a location and image."""
    players = state.get("players", [])
    player_list = ", ".join([f"{p['username']} (a {p['faction']})" for p in players])
    thread_id = state.get("thread_id")

    # 1. Select a random location
    location = random.choice(LOCATIONS)
    state['location'] = location # Save location to state for modifiers

    # 2. Incorporate location into the prompt
    # Use prompt function
    prompt = prompts.get_start_level_prompt(player_list, location)

    response = await gpt_request(model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    content = json.loads(response.choices[0].message.content)

    state["objective"] = content.get("objective", f"Survive the day in {location['name']}.")
    opening_scene = content.get("opening_scene", "The acid rain sizzles on the grimy streets.")
    state["narrative_log"] = [opening_scene]

    # 3. Generate an image for the scene
    image_prompt = f"A grimdark cyberpunk scene in '{location['name']}'. The scene: {opening_scene}"

    await send_message(context, chat_id, thread_id, "Please wait, materializing the environment...")
    b64 = await generate_image(image_prompt)

    # 4. Send image with the prompt as a caption
    caption = f"📍 *{location['name']}*\n*Objective:* {state['objective']}\n\n{opening_scene}"

    if b64:
        img = base64.b64decode(b64)
        await context.bot.send_photo(chat_id=chat_id, photo=img, caption=caption, message_thread_id=thread_id, parse_mode="Markdown")
    else:
        # Fallback to text if image generation fails
        await send_message(context, chat_id, thread_id, caption)

    state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat()
    await save_state(chat_id, state)
    await prompt_for_next_action(context, chat_id, state)

async def apply_boss_damage(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict, player_damage_map: Dict[str, Any]) -> dict:
    """Applies damage from a boss to players and handles deaths."""
    players = state.get("players", [])
    if not players: return state
    thread_id = state.get("thread_id")

    dead_players_this_turn = []
    damage_messages = []

    for target, damage in player_damage_map.items():
        try: damage = int(damage)
        except: continue
        if damage <= 0: continue

        if state.get("selected_route") == "adrenal":
            damage = int(damage * 1.5)

        target_players = players if str(target).lower() in ('all', 'players') else [p for p in players if str(p['id']) == str(target) and p in state.get("players", [])]
        for p in target_players:
            p['hp'] -= damage
            damage_messages.append(f"🩸 *{p['username']}* takes *{damage} damage*!\n{create_bar(p['hp'], p['max_hp'])} {p['hp']}/{p['max_hp']}")
            if p['hp'] <= 0:
                dead_players_this_turn.append(p)

    if damage_messages:
        await send_message(context, chat_id, thread_id, "\n\n".join(damage_messages))

    if dead_players_this_turn:
        death_messages = []
        for dp in dead_players_this_turn:
            death_messages.append(f"💀 {dp['username']} has fallen!")
            state['dead_players'].append(dp)

        if death_messages:
            await send_message(context, chat_id, thread_id, "\n".join(death_messages))

        state['players'] = [p for p in state['players'] if p['id'] not in [dp['id'] for dp in dead_players_this_turn]]

        if not state['players']:
            await send_message(context, chat_id, thread_id, "All players have fallen. The city claims its due.")

            # Set stage to prevent further actions
            state['game_stage'] = GameStage.VICTORY.name # Use VICTORY as a "run is over" state
            await save_state(chat_id, state)

            # Calculate "failure" bonus (bonus for completed floors)
            failure_bonus = max(0, (state.get("gauntlet_level", 1) - 1) * 10)

            keyboard = [
                [InlineKeyboardButton(f"💰 Bank Reward (+{failure_bonus}% rarity)", callback_data=f"gauntlet:end:{failure_bonus}")],
                [InlineKeyboardButton("Try Again (Main Menu)", callback_data="gauntlet:reset")]
            ]
            await send_message(context, chat_id, thread_id, "Your run has ended. Choose your path:", reply_markup=InlineKeyboardMarkup(keyboard))

            return await load_state(chat_id) # Return the modified state

    return state

async def prompt_for_next_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    players = state.get("players", [])
    if not players: return
    turn_index = state.get("turn_index", 0)
    current_player = players[turn_index]
    keyboard = []
    thread_id = state.get("thread_id")

    is_boss_fight = state.get("boss") is not None

    if is_boss_fight:
        for ability in current_player.get("abilities", []):
            if ability.get('charges', float('inf')) > 0:
                effect = ability['effect']; desc = ""
                if effect['type'] == 'direct_damage': desc = f"Deal {effect['value']} Dmg"
                elif effect['type'] == 'heal': desc = f"Heal {effect['value']} HP"
                elif effect['type'] == 'roll_bonus': desc = f"{effect['value']:+d} Roll"
                elif effect['type'] == 'guaranteed_success': desc = f"Guarantee {effect.get('category','')}"
                charges_text = f"{ability['charges']} left" if 'charges' in ability else "∞ uses"
                keyboard.append([InlineKeyboardButton(f"💥 {ability['name']} ({charges_text} | {desc})", callback_data=f"ability:{ability['name']}")])

        # Add environmental interaction if applicable
        if (state["boss"]["hp"] / state["boss"]["max_hp"] <= 0.50) and not state.get("location_interaction_used"):
            location = state.get("location")
            if location and location.get("interaction"):
                interaction = location["interaction"]
                keyboard.append([InlineKeyboardButton(f"⚡️ {interaction['name']}", callback_data=f"env_action:{interaction['category']}")])

        prompt_text = f"It's *{current_player['username']}'s* turn. You must use an ability to act."
    else: # Open Campaign
        prompt_text = f"It's *{current_player['username']}'s* turn. Type your custom action."

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await send_message(context, chat_id, thread_id, prompt_text, reply_markup=reply_markup)

# ───────── Gauntlet Floor Start ─────────
def pick_weighted_boss_from_scout(state: dict) -> str:
    if state.get("scout") and state["scout"].get("odds"): return weighted_choice([(name, int(p * 100)) for name, p in state["scout"]["odds"]])
    return random.choice(list(BOSS_TRAITS.keys()))

async def start_gauntlet_floor(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    state = await load_state(chat_id)
    state["game_stage"] = GameStage.GAUNTLET.name
    floor, num_players = int(state.get("gauntlet_level", 1)), len(state.get("players", []))
    thread_id = state.get("thread_id")

    # Award XP for attempting the floor and update stats
    for player in state.get("players", []):
        await award_xp(context, chat_id, thread_id, player['id'], player['username'], XP_FOR_ATTEMPT, f"attempting Gauntlet Floor {floor}")
        profile = await get_or_create_profile(player['id'], player['username'])
        profile['stats']['bosses_attempted'] += 1
        await save_profile(player['id'], profile)

    base_hp_for_floor = 40 * (1.3 ** (floor - 1))
    boss_hp = int(base_hp_for_floor * num_players)
    boss_archetype_name, hazard = pick_weighted_boss_from_scout(state), (state.get("scout") or {}).get("hazard") or random.choice(HAZARDS)
    boss_archetype_data = BOSS_TRAITS[boss_archetype_name]
    location = random.choice(LOCATIONS)
    state["location"], state["hazard_effect"] = location, {"type": "modifier", "category": hazard["category"], "value": hazard["value"], "narrative": hazard["label"]}

    # Use prompt function
    prompt = prompts.get_start_gauntlet_prompt(boss_archetype_name, boss_archetype_data, location)

    resp = await gpt_request(model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    content = json.loads(resp.choices[0].message.content)
    state["boss"] = {"name": content.get("boss_name", boss_archetype_name), "description": content.get("boss_description"), "archetype": boss_archetype_name, "abilities": boss_archetype_data["abilities"], "strengths": boss_archetype_data.get("strengths", []), "weaknesses": boss_archetype_data.get("weaknesses", []), "hp": boss_hp, "max_hp": boss_hp}
    state["objective"], state["narrative_log"] = f"Defeat {state['boss']['name']}", [state["boss"]["description"]]
    state["gauntlet_bonus_attempted"] = int(state.get("gauntlet_bonus_attempted", 0)) + 1
    state["location_interaction_used"] = False # Reset on new floor
    await save_state(chat_id, state)

    await send_message(context, chat_id, thread_id, "Please wait, materializing the target...")
    b64 = await generate_image(f"A grimdark cyberpunk boss, {state['boss']['name']}, in '{location['name']}'. Scene: {state['boss']['description']}")
    caption = f"*Gauntlet Floor {floor}*\n📍 *{location['name']}*\n*Objective:* {state['objective']}\n\n{state['boss']['description']}\n\n_Global hazard active: {hazard['label']}_"
    if b64: await context.bot.send_photo(chat_id=chat_id, photo=base64.b64decode(b64), caption=caption, message_thread_id=thread_id, parse_mode="Markdown")
    else: await send_message(context, chat_id, thread_id, caption)
    b, a, d = compute_run_bonus(state)
    bonus_text = f"Run Bonus: *+{b}* (Attempted {a}, Defeated {d})."
    await send_message(context, chat_id, thread_id, f"{bonus_text}\n\n*{state['boss']['name']}*\n{create_bar(state['boss']['hp'], state['boss']['max_hp'])} {state['boss']['hp']}/{state['boss']['max_hp']}")
    await prompt_for_next_action(context, chat_id, state)

# ───────── Victory / Defeat ─────────
async def run_epilogue(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    thread_id = state.get("thread_id")
    if state.get("game_mode") == "gauntlet":
        state['game_stage'] = GameStage.VICTORY.name
        state["gauntlet_bonus_defeated"] = int(state.get("gauntlet_bonus_defeated", 0)) + 1
        floor = state.get("gauntlet_level", 1)
        # Award XP for defeating the boss and update stats
        xp_amount = XP_FOR_DEFEAT_BASE * floor
        for player in state.get("players", []):
            await award_xp(context, chat_id, thread_id, player['id'], player['username'], xp_amount, f"defeating a Floor {floor} boss")
            profile = await get_or_create_profile(player['id'], player['username'])
            profile['stats']['bosses_defeated'] += 1
            profile['stats']['highest_floor'] = max(profile['stats']['highest_floor'], floor)
            await save_profile(player['id'], profile)
        await save_state(chat_id, state)
        await trigger_gauntlet_choice_menu(context, chat_id, state)
        return
    state['game_stage'] = GameStage.VICTORY.name
    await send_message(context, chat_id, thread_id, "🏆 *VICTORY!* Generating your epilogue...")

    # Use prompt function
    prompt = prompts.get_victory_epilogue_prompt(state.get('narrative_log', [''])[-1])

    response = await gpt_request(model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=120)
    epilogue = response.choices[0].message.content.strip()
    await send_message(context, chat_id, thread_id, epilogue)

    await send_message(context, chat_id, thread_id, "Please wait, immortalizing the moment...")
    b64 = await generate_image(f"A cyberpunk victory scene in Alpha City: {epilogue}")
    if b64: await context.bot.send_photo(chat_id=chat_id, photo=base64.b64decode(b64), caption="_Your victory, immortalized._", message_thread_id=thread_id, parse_mode="Markdown")
    keyboard = [[InlineKeyboardButton("🤝 Recruit an Ally", callback_data="reward:character:0")], [InlineKeyboardButton("💎 Receive Treasure", callback_data="reward:item:0")]]
    await send_message(context, chat_id, thread_id, "As a reward for your triumph, choose one:", reply_markup=InlineKeyboardMarkup(keyboard))
    await save_state(chat_id, state)

# ───────── Gauntlet Ascend/Bank ─────────
async def trigger_gauntlet_choice_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    floor, bonus = state.get("gauntlet_level", 1), state.get("gauntlet_level", 1) * 10
    thread_id = state.get("thread_id")
    await send_message(context, chat_id, thread_id, f"You have defeated Gauntlet floor {floor}!")
    b, a, d = compute_run_bonus(state)
    await send_message(context, chat_id, thread_id, f"Run Bonus: *+{b}* (Attempted {a}, Defeated {d}).")
    # Pass the calculated bonus in the callback data
    keyboard = [[InlineKeyboardButton(f"🚀 Ascend (Floor {floor + 1})", callback_data="gauntlet:continue")], [InlineKeyboardButton(f"💰 Bank Reward (+{bonus}% rarity)", callback_data=f"gauntlet:end:{bonus}")]]
    await send_message(context, chat_id, thread_id, "Ascend for bigger odds or bank now?", reply_markup=InlineKeyboardMarkup(keyboard))

async def gauntlet_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    if query.message.message_thread_id != thread_id: return await query.answer()
    await query.answer()

    parts = query.data.split(":")
    action = parts[1]

    if action == "continue":
        state["gauntlet_level"] += 1
        state["game_stage"] = GameStage.SCOUTING.name
        state["turn_index"] = 0
        state["location_interaction_used"] = False
        for i, player in enumerate(state["players"]):
            if faction := player.get("faction"):
                faction_abilities = [copy.deepcopy(a) for a in ABILITIES.get(faction, [])]
                equipped = player.get("equipped_items", {})
                item_abilities = item_traits.get_abilities_from_equipped_items(equipped, reset_charges=True)
                state["players"][i]["abilities"] = faction_abilities + item_abilities
        await query.edit_message_text(f"The challenge intensifies. Re-routing for Floor {state['gauntlet_level']}...")
        state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat()
        await save_state(chat_id, state)
        await start_scouting(context, chat_id, state)
    elif action == "end":
        try:
            # The bonus is now always passed as the 3rd part, e.g., "gauntlet:end:10"
            gauntlet_bonus = int(parts[2])
        except (IndexError, ValueError):
            gauntlet_bonus = 0 # Fallback in case something goes wrong

        await query.edit_message_text(f"You've chosen to bank your earnings. Rarity bonus: +{gauntlet_bonus}%")

        keyboard = [[InlineKeyboardButton("🤝 Recruit an Ally", callback_data=f"reward:character:{gauntlet_bonus}")], [InlineKeyboardButton("💎 Receive Treasure", callback_data=f"reward:item:{gauntlet_bonus}")]]
        await send_message(context, chat_id, thread_id, "Choose your reward.", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "reset":
        # Use the new helper function
        await _return_to_main_menu(context, chat_id, thread_id, query.message, "The simulation fades. You are back at the beginning.")


async def reward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    await query.answer()

    # Get thread_id for the finally block
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    try:
        user = query.from_user
        data_parts = query.data.split(":")
        reward_type, gauntlet_bonus = data_parts[1], int(data_parts[2]) if len(data_parts) > 2 else 0
        await query.edit_message_text(f"You chose to receive a new {reward_type}. Good choice.")
        await generate_and_send_reward(context, chat_id, user, reward_type, 20, gauntlet_bonus)

    except Exception as e:
        logger.error(f"Error during reward generation: {e}", exc_info=True)
        # Try to send a message, but don't fail if it doesn't work
        try:
            await send_message(context, chat_id, thread_id, "An error occurred during reward generation. The game will now reset.")
        except:
            pass

    finally:
        # This ALWAYS runs, ensuring the game resets
        # Use the new helper function and wrap it in a try/except
        # to prevent the bot from getting stuck if the reset itself fails.
        try:
            await _return_to_main_menu(context, chat_id, thread_id)
        except Exception as e_final:
            logger.error(f"CRITICAL: Failed to reset game in finally block: {e_final}")

# ───────── Suggested action & abilities ─────────
async def environment_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    if query.message.message_thread_id != thread_id: return await query.answer()
    players = state.get("players", [])
    if not players: return
    turn_index = state.get("turn_index", 0)
    if user_id != players[turn_index]['id']: return await query.answer("It's not your turn.", show_alert=True)

    # FIX: Check for turn lock
    if state.get("is_processing_turn"):
        return await query.answer("Action in progress, please wait...", show_alert=True)

    state["is_processing_turn"] = True # <-- LOCK
    await save_state(chat_id, state)

    await query.answer()

    state["location_interaction_used"] = True
    location = state.get("location", {})
    interaction = location.get("interaction")
    if not interaction: 
        state["is_processing_turn"] = False # <-- UNLOCK
        await save_state(chat_id, state)
        return await query.edit_message_text("Error: Location interaction not found.")

    loading_message = await query.edit_message_text(f"{query.from_user.first_name} attempts to: *{interaction['name']}*!", parse_mode="Markdown")

    current_player = players[turn_index]
    current_player_id = current_player['id'] # Get the ID before any potential deaths

    # --- New ability-style luck logic ---
    luck_roll, multiplier, luck_descriptor = random.randint(1, 10), 1.0, ""
    if luck_roll == 1: multiplier, luck_descriptor = 0.0, "a complete failure"
    elif 2 <= luck_roll <= 4: multiplier, luck_descriptor = 0.75, "weaker than expected"
    elif luck_roll == 5: luck_descriptor = "as expected"
    elif 6 <= luck_roll <= 9: multiplier, luck_descriptor = 1.25, "stronger than expected"
    elif luck_roll == 10: multiplier, luck_descriptor = 2.0, "a critical success"

    roll_narrative = f"🎲 Luck: {luck_roll}/10 ({luck_descriptor}) | 💥 Multiplier: {multiplier}x"

    if luck_roll == 1: # Complete failure
        damage = interaction['failure_effect']['value']
        if state.get("selected_route") == "adrenal":
            damage = int(damage * 1.5)

        full_narrative = f"{roll_narrative}\n\n{interaction['failure_narrative']}"
        await send_message(context, chat_id, thread_id, full_narrative)

        if damage > 0:
            for p in state["players"]: p['hp'] -= damage
            await send_message(context, chat_id, thread_id, f"🩸 The entire party takes *{damage} damage*!")

    else: # Success (partial or critical)
        base_damage = interaction['success_effect']['value']
        damage = int(round(base_damage * multiplier))

        if state.get("selected_route") == "adrenal":
            damage = int(damage * 1.5)

        if damage > 0:
            state['boss']['hp'] -= damage
            full_narrative = f"{roll_narrative}\n\n{interaction['success_narrative']}\n\n💥 You dealt *{damage} damage* to {state['boss']['name']}!"
        else:
            # Handle cases where multiplier is low or 0.0
            full_narrative = f"{roll_narrative}\n\n{interaction['success_narrative']}\n\n...but your attempt was {luck_descriptor} and had no effect."

        await send_message(context, chat_id, thread_id, full_narrative)

        if state['boss']['hp'] <= 0:
            state["is_processing_turn"] = False # <-- UNLOCK
            await save_state(chat_id, state)
            return await run_epilogue(context, chat_id, state)
    # --- End new logic ---

    # Boss Retaliation
    boss = state["boss"]
    boss_abilities_desc = "\n".join([f"- {a['name']}: {a['description']}" for a in boss.get("abilities", [])])

    # Use prompt functions
    system_prompt = prompts.get_env_action_system_prompt()
    user_prompt = prompts.get_env_action_user_prompt(
        interaction['success_narrative' if luck_roll > 1 else 'failure_narrative'], # Use correct narrative for prompt
        boss['name'],
        boss_abilities_desc
    )

    try:
        response = await gpt_request(model=CHAT_MODEL, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], response_format={"type": "json_object"})
        result = json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error("Boss retaliation (env) error: %s", e)
        result = {"boss_ability_choice": "furious glare", "boss_narrative": "The boss is enraged by your gambit!", "player_damage": {}}

    await asyncio.sleep(1.5)
    await send_message(context, chat_id, thread_id, f"*{boss['name']}'s Retaliation ({result.get('boss_ability_choice')}):*\n{result.get('boss_narrative')}")
    state = await apply_boss_damage(context, chat_id, state, result.get('player_damage', {}))
    if not state.get("players"): 
        state["is_processing_turn"] = False # <-- UNLOCK
        await save_state(chat_id, state)
        return # Party wipe was handled by apply_boss_damage

    state['turn_index'] = get_next_turn_index(state.get("players", []), current_player_id, turn_index)

    state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat()
    state["is_processing_turn"] = False # <-- UNLOCK
    await save_state(chat_id, state)
    await prompt_for_next_action(context, chat_id, state)
    try: await context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)
    except Exception as e: logger.warning(f"Could not delete env action confirmation: {e}")


async def ability_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    state = await load_state(chat_id)

    if query.message.message_thread_id != state.get("thread_id"): return await query.answer()
    players = state.get("players", [])
    if not players: return
    turn_index = state.get("turn_index", 0)
    if user_id != players[turn_index]['id']: return await query.answer("It's not your turn.", show_alert=True)

    # FIX: Check for turn lock
    if state.get("is_processing_turn"):
        return await query.answer("Action in progress, please wait...", show_alert=True)

    await query.answer()
    ability_name = query.data.split(":", 1)[1]
    ability_used = None
    for i, p in enumerate(state['players']):
        if p['id'] == user_id:
            for j, ability in enumerate(p.get("abilities", [])):
                if ability['name'] == ability_name:
                    if 'charges' in ability:
                        if ability['charges'] > 0:
                            state['players'][i]['abilities'][j]['charges'] -= 1
                            ability_used = ability
                    else: # Unlimited use
                        ability_used = ability
                    break
            break
    if not ability_used:
        await query.edit_message_text(f"Ability '{ability_name}' is out of charges.")
        await prompt_for_next_action(context, chat_id, state)
        return

    state["is_processing_turn"] = True # <-- LOCK

    loading_message = await query.edit_message_text(f"{players[turn_index]['username']} uses *{ability_name}*!", parse_mode="Markdown")
    state["last_action_timestamp"] = datetime.datetime.utcnow().isoformat()
    await save_state(chat_id, state) # Save the lock and new charge count
    await handle_player_action(update, context, f"[ABILITY]:{ability_name}")
    try: await context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)
    except Exception as e: logger.warning(f"Could not delete ability confirmation message: {e}")

# ───────── Message routing ─────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")

    if state.get("game_stage") not in [GameStage.LEVEL_1.name, GameStage.GAUNTLET.name, GameStage.SCOUTING.name] or update.effective_message.message_thread_id != thread_id: return
    if state.get("game_stage") == GameStage.SCOUTING.name: return
    players = state.get("players", [])
    if not players or user_id not in [p['id'] for p in players]: return
    turn_index = state.get("turn_index", 0)
    if user_id != players[turn_index]['id']: return await update.message.reply_text(f"It's not your turn. Please wait for {players[turn_index]['username']}.")

    # FIX: Check for turn lock
    if state.get("is_processing_turn"):
        return await update.message.reply_text("Action in progress, please wait...")

    # In boss fights, typed messages are ignored.
    if state.get("game_stage") == GameStage.GAUNTLET.name:
        return

    state["is_processing_turn"] = True # <-- LOCK
    await save_state(chat_id, state)

    await handle_player_action(update, context, update.message.text.strip())

# ───────── Main & Polling ─────────
async def _post_init(app: Application) -> None:
    """Called after the Application is built – initialise the DB pool."""
    await db_layer.init_db()

async def _post_shutdown(app: Application) -> None:
    """Called when the Application shuts down – close the DB pool."""
    await db_layer.close_db()

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN: logger.error("TELEGRAM_TOKEN missing"); return
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("venture", venture))
    app.add_handler(CommandHandler("join", join_command))
    app.add_handler(CommandHandler("endgame", endgame_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("inventory", inventory_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main:"))
    app.add_handler(CallbackQueryHandler(inventory_callback, pattern="^inv:"))
    app.add_handler(CallbackQueryHandler(faction_selection_callback, pattern="^faction:"))
    app.add_handler(CallbackQueryHandler(route_selection_callback, pattern="^route:"))
    app.add_handler(CallbackQueryHandler(gauntlet_menu_callback, pattern="^gauntlet:"))
    app.add_handler(CallbackQueryHandler(reward_callback, pattern="^reward:"))
    app.add_handler(CallbackQueryHandler(ability_callback, pattern="^ability:"))
    app.add_handler(CallbackQueryHandler(environment_action_callback, pattern="^env_action:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot polling started")
    app.run_polling()

if __name__ == "__main__":
    main()
