import os
import json
import asyncio
import logging
import time
import random
import re
import base64
import requests
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import NetworkError
from replit import db
from locations import LOCATIONS
from abilities import ABILITIES

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
client = OpenAI(api_key=OPENAI_API_KEY)

# ───────── Constants & Lore ─────────
LORE_SUMMARY = """
World: Alpha City, a dystopia built on a twisted version of blockchain.
Oppressors (The Overcity):
- Overlords: Trillionaire dynasties who enforce financial slavery.
- The Singularity: An AI council that acts as judge, jury, and executioner.
- Neuralifes: The indoctrinated masses, controlled by mandatory neural implants.
Rebels (The Underground):
- Glitchborn: Unregistered, implant-free "ghosts" - assassins and saboteurs.
- Nodewalkers: Blockchain-mystics who can bend data and hack implants.
- Coinbrokers: Black-market financiers who fund the rebellion.
- Chainbreakers: Augmented warriors with weaponized mods, survivors of implant destruction.
The Conflict: The Underground fights for freedom against the Overcity's total surveillance and control.
"""

FACTIONS = {
    "Nodewalkers": {
        "hp": 20,
        "description": "Hackers of early implants, blockchain-mystics who bend data and identities.",
        "modifier_type": "technology",
        "modifier_value": 1
    },
    "Coinbrokers": {
        "hp": 19,
        "description": "Black-market financiers fueling the rebellion with forbidden tokens and off-chain wealth.",
        "modifier_type": "communication",
        "modifier_value": 1
    },
    "Glitchborn": {
        "hp": 21,
        "description": "Unregistered, implant-free “ghosts” — unseen saboteurs and assassins.",
        "modifier_type": "stealth",
        "modifier_value": 1
    },
    "Chainbreakers": {
        "hp": 24,
        "description": "Augmented warriors who survived implant destruction, wielding weaponized mods against the Overcity.",
        "modifier_type": "strength",
        "modifier_value": 1
    }
}
ALL_FACTIONS_LIST = ["Neuralife", "Nodewalker", "Singularity", "Overlord", "Coinbroker", "Glitchborn", "Chainbreaker"]
ITEM_SLOTS = ["Cranial", "Chassis", "Equipment", "Mobility", "Companion"]
ITEM_SPECIALTIES = ["Umbral", "Neural", "Kinetic", "Enertech"]
ACTIONS_PER_LEVEL = 7
HACK_COOLDOWN = 300  # 5 minutes in seconds
INACTIVITY_TIMEOUT = 180 # seconds for multiplayer

# In-memory storage for inactivity timer tasks
INACTIVITY_TIMERS: dict[int, asyncio.Task] = {}

# ───────── Helpers ─────────
async def gpt_request(**kwargs):
    """Generic helper to make GPT calls with retries."""
    backoff = 1
    for _ in range(3):
        try:
            return await asyncio.to_thread(client.chat.completions.create, **kwargs)
        except Exception as e:
            logger.warning("GPT call failed, retrying in %ds: %s", backoff, e)
            await asyncio.sleep(backoff)
            backoff *= 2
    raise RuntimeError("GPT calls failed after 3 retries")

async def generate_image(prompt: str) -> str | None:
    """Generates an image using dall-e-3 and returns the base64 JSON, handling URL fallbacks."""
    enhanced_prompt = f"Hand-painted art style, cinematic still photo of: {prompt}. Textless, no words, no letters, no typography, purely visual."
    try:
        logger.info("Generating image with prompt: %s", enhanced_prompt)

        def _generate_and_fetch():
            resp = client.images.generate(
                model="dall-e-3",
                prompt=enhanced_prompt,
                n=1,
                size="1024x1024",
                quality="standard",
                response_format="b64_json"
            )
            if not resp.data or not resp.data[0] or not resp.data[0].b64_json:
                 raise Exception("No b64_json in OpenAI image response")
            return resp.data[0].b64_json
        return await asyncio.to_thread(_generate_and_fetch)
    except Exception as e:
        logger.error("Error generating image: %s", e, exc_info=True)
        return None

async def send_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None):
    """Helper to send messages, respecting threads."""
    state = await load_state(chat_id)
    thread_id = state.get("thread_id")
    await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")

def get_rarity_and_level(roll: int) -> tuple[str, str, int]:
    """Determines rarity, color, and level from a d100 roll using lore-friendly names."""
    if roll <= 35: return "Salvage", "⚪️", random.randint(1, 4)
    if roll <= 60: return "Gutter-Tech", "🟢", random.randint(5, 8)
    if roll <= 80: return "Street Mod", "🔵", random.randint(9, 12)
    if roll <= 94: return "Black Market", "🟣", random.randint(13, 16)
    if roll <= 99: return "Node-Forged", "🟡", random.randint(17, 19)
    return "Peerless", "💥", 20

def get_outcome_tier(score: int) -> str:
    """Returns the name of the outcome tier based on the score."""
    if score <= 10: return "Catastrophic Failure"
    if score <= 30: return "Significant Failure"
    if score <= 50: return "Partial Failure"
    if score <= 70: return "Partial Success"
    if score <= 90: return "Significant Success"
    return "Tremendous Success"

# ───────── State Persistence ─────────
def get_state_key(chat_id: int) -> str:
    return f"game_state_{chat_id}"

async def load_state(chat_id: int) -> dict:
    state_key = get_state_key(chat_id)
    for attempt in range(3):
        try:
            state_json = db.get(state_key)
            if state_json:
                return json.loads(state_json)
            return {}
        except Exception as e:
            logger.error("Failed to load state (attempt %d/3) for chat %s: %s", attempt + 1, chat_id, e)
            if attempt < 2:
                await asyncio.sleep(1)
    return {}

async def save_state(chat_id: int, state: dict):
    state_key = get_state_key(chat_id)
    for attempt in range(3):
        try:
            db[state_key] = json.dumps(state)
            return
        except Exception as e:
            logger.error("Failed to save state (attempt %d/3) for chat %s: %s", attempt + 1, chat_id, e)
            if attempt < 2:
                await asyncio.sleep(1)

async def reset_game_state(chat_id: int, thread_id: int | None):
    task = INACTIVITY_TIMERS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
    initial_state = {
        "game_stage": "main_menu", "thread_id": thread_id, "players": [],
        "dead_players": [], "turn_index": 0,
        "owner_id": None, "level": 0, "narrative_log": [], "objective": None,
        "actions_remaining": ACTIONS_PER_LEVEL, "hack_cooldowns": {},
        "boss": None
    }
    await save_state(chat_id, initial_state)
    return initial_state

# ───────── Inactivity Timer ─────────
async def inactivity_ender(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.sleep(INACTIVITY_TIMEOUT - 20)
        state = await load_state(chat_id)
        if state.get("game_stage") in ["level_1", "level_2"]:
            await send_message(context, chat_id, "⌛ The simulation is becoming unstable due to inactivity. Action required in 20 seconds or the session will collapse.")
        await asyncio.sleep(20)
        final_state = await load_state(chat_id)
        # Check if state is still the same, indicating no activity
        if final_state.get("turn_index") == state.get("turn_index") and final_state.get("narrative_log") == state.get("narrative_log"):
            logger.info("Inactivity timeout reached for chat %d. Ending game.", chat_id)
            await send_message(context, chat_id, "🛑 The connection was lost. The simulation has collapsed due to inactivity.")
            await reset_game_state(chat_id, state.get("thread_id"))
    except asyncio.CancelledError:
        logger.info("Inactivity timer cancelled for chat %d.", chat_id)
    except Exception as e:
        logger.error("Error in inactivity_ender for chat %d: %s", chat_id, e)

def schedule_inactivity_timer(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    if chat_id in INACTIVITY_TIMERS:
        task = INACTIVITY_TIMERS.pop(chat_id)
        if not task.done():
            task.cancel()
    task = asyncio.create_task(inactivity_ender(chat_id, context))
    INACTIVITY_TIMERS[chat_id] = task
    logger.info("Scheduled inactivity timer for chat %d.", chat_id)

# ───────── Core Game Commands & Menus ─────────
async def venture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    await reset_game_state(chat_id, thread_id)
    keyboard = [
        [InlineKeyboardButton("🤝 Hire Help", callback_data="main:hire_help")],
        [InlineKeyboardButton("💎 Dig for Treasure", callback_data="main:dig_treasure")],
        [InlineKeyboardButton("🚀 Speed Mission", callback_data="main:speed_mission")],
        [InlineKeyboardButton("⚔️ Boss Fight", callback_data="main:boss_fight")],
        [InlineKeyboardButton("🌍 Open Campaign", callback_data="main:open_campaign")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome to the underbelly of Alpha City. What's your move?", reply_markup=reply_markup)

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = await load_state(chat_id)

    if state.get("game_stage") != "faction_select":
        return await update.message.reply_text("There is no active campaign to join right now. Use /venture to start one.")

    if any(p['id'] == user.id for p in state.get("players", [])):
        return await update.message.reply_text("You are already in the campaign.")

    if any(p['id'] == user.id for p in state.get("dead_players", [])):
        return await update.message.reply_text("You have fallen in this campaign. You cannot rejoin until the next one.")

    # Add player and prompt for faction
    # We can add a temporary placeholder until they select a faction.
    # For now, let's just prompt them.
    await update.message.reply_text(f"{user.first_name} wants to join the fight! Please choose your faction from the menu above.")


async def endgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    state = await load_state(chat_id)
    if state.get("owner_id") != user_id:
        return await update.message.reply_text("Only the game owner can end the adventure.")
    thread_id = update.effective_message.message_thread_id
    await reset_game_state(chat_id, thread_id)
    await update.message.reply_text("The current adventure has been ended. Use /venture to start a new one.")


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    action = query.data.split(":")[1]

    if action in ["speed_mission", "boss_fight", "open_campaign"]:
        state = await load_state(chat_id)
        state["game_stage"] = "faction_select"
        state["owner_id"] = query.from_user.id
        state["game_mode"] = action
        state["players"] = [] # Reset players for new game
        await save_state(chat_id, state)
        keyboard = [[InlineKeyboardButton(f, callback_data=f"faction:{f}")] for f in FACTIONS]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("A new adventure awaits. The first player to choose a faction begins. Others may /join.", reply_markup=reply_markup)
    elif action == "hire_help":
        await generate_and_send_reward(context, chat_id, "character", 1)
    elif action == "dig_treasure":
        await generate_and_send_reward(context, chat_id, "item", 1)

async def generate_and_send_reward(context: ContextTypes.DEFAULT_TYPE, chat_id: int, reward_type: str, min_roll: int):
    await send_message(context, chat_id, f"🎲 Rolling the dice for a new {reward_type}...")
    roll = random.randint(min_roll, 100)
    rarity, rarity_icon, level = get_rarity_and_level(roll)

    prompt = ""
    if reward_type == "item":
        slot, specialty = random.choice(ITEM_SLOTS), random.choice(ITEM_SPECIALTIES)
        prompt = (f"You are a generator for a cyberpunk RPG based on this lore:\n{LORE_SUMMARY}\n"
                  f"Generate an item with these traits:\n- Rarity: '{rarity}'\n- Slot: '{slot}'\n- Specialty: '{specialty}'\n"
                  f"The item's name and background must fit the lore. Provide a JSON object with 'name' and 'background' (max 300 chars).")
    else:  # character
        ally_faction = random.choice(ALL_FACTIONS_LIST)
        prompt = (f"You are a generator for a cyberpunk RPG based on this lore:\n{LORE_SUMMARY}\n"
                  f"Generate a character from the '{ally_faction}' faction. Their name MUST NOT be '{rarity}'.\n- Rarity Tier: '{rarity}' (This should influence their background)\n"
                  f"Provide a JSON object with 'name' (a proper name) and 'background' (mentioning their faction, max 300 chars).")

    response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    try:
        content = json.loads(response.choices[0].message.content)
        name = content.get("name", f"Unnamed {reward_type.capitalize()}")
        background = content.get("background", "No background available.")
    except (json.JSONDecodeError, AttributeError):
        return await send_message(context, chat_id, "Error generating reward details. Please try again.")

    image_prompt, caption = "", ""
    if reward_type == "item":
        durability = random.randint(1, 10)
        image_prompt = f"A futuristic, grimdark cyberpunk item. Slot: {slot}, Specialty: {specialty}. Item Name: {name}. Description: {background}."
        caption = (f"*{name}*\n"
                   f"🔩 *Slot*: {slot}\n"
                   f"✨ *Specialty*: {specialty}\n"
                   f"{rarity_icon} *Rarity*: {rarity}\n"
                   f"🛠️ *Durability*: {durability}/10\n\n"
                   f"_{background}_")
    else: # character
        ally_faction = random.choice(ALL_FACTIONS_LIST) 
        faction_icon = "🔴" if ally_faction in ["Overlord", "Singularity", "Neuralife"] else "🟢"
        image_prompt = f"A futuristic, grimdark cyberpunk character from the {ally_faction} faction: {name}. {background}."
        caption = (f"*{name}*\n"
                   f"{faction_icon} *Faction*: {ally_faction}\n"
                   f"⚡ *Level*: {level}\n\n"
                   f"_{background}_")

    b64_json = await generate_image(image_prompt)
    if b64_json:
        image_bytes = base64.b64decode(b64_json)
        state = await load_state(chat_id)
        thread_id = state.get("thread_id")
        await context.bot.send_photo(chat_id=chat_id, photo=image_bytes, caption=caption, message_thread_id=thread_id, parse_mode="Markdown")
    else:
        await send_message(context, chat_id, caption)

# ───────── Game Progression Callbacks ─────────
async def faction_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    user = query.from_user
    state = await load_state(chat_id)

    if state.get("game_stage") != "faction_select":
        return await query.answer("Faction selection is not active.", show_alert=True)

    if any(p['id'] == user.id for p in state.get("players", [])):
        return await query.answer("You have already chosen a faction.", show_alert=True)

    await query.answer()
    faction_name = query.data.split(":", 1)[1]
    faction_data = FACTIONS[faction_name]
    player_hp = faction_data["hp"]

    new_player = {
        "id": user.id,
        "username": user.first_name,
        "faction": faction_name,
        "hp": player_hp,
        "max_hp": player_hp,
        "modifier_type": faction_data["modifier_type"],
        "modifier_value": faction_data["modifier_value"],
        "abilities": [ability.copy() for ability in ABILITIES.get(faction_name, [])] # Add abilities
    }
    state["players"].append(new_player)

    await send_message(context, chat_id, f"{user.first_name} has joined as a {faction_name}!")

    # If this is the first player, the game can start.
    if len(state["players"]) == 1:
        game_mode = state.get("game_mode", "speed_mission")
        if game_mode == "speed_mission":
            state.update({"game_stage": "level_1", "level": 1, "actions_remaining": ACTIONS_PER_LEVEL})
            await query.edit_message_text("Generating your speed mission...")
            await start_level(context, chat_id, state)
        elif game_mode == "boss_fight":
            await query.edit_message_text("Generating your boss encounter...")
            await start_boss_fight(context, chat_id, state)
        elif game_mode == "open_campaign":
            state.update({"game_stage": "level_1", "level": 1, "actions_remaining": float('inf')})
            await query.edit_message_text("Generating your open world...")
            await start_level(context, chat_id, state)
    else:
        await save_state(chat_id, state) # Save state for newly joined player

async def pre_boss_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    state = await load_state(chat_id)

    # In multiplayer, any player can make this choice
    # You might want to implement a voting system later.
    # For now, first come, first served.

    await query.answer()
    action = query.data.split(":")[1]
    if action == "continue":
        await start_level_2(context, chat_id)
    elif action == "end_campaign":
        await query.edit_message_text("Your party fades back into the shadows, the mission abandoned. The city forgets your names.")
        await reset_game_state(chat_id, state.get("thread_id"))
    else:
        reward_type = "character" if action == "hire_help" else "item"
        await query.edit_message_text("Your party takes a moment to gear up before the final confrontation.")
        await generate_and_send_reward(context, chat_id, reward_type, 20)
        keyboard = [[InlineKeyboardButton("🚀 Continue Adventure", callback_data="post_reward:continue")],
                    [InlineKeyboardButton("🛑 End Campaign", callback_data="post_reward:end_campaign")]]
        await send_message(context, chat_id, "The final challenge awaits. Are you ready?", reply_markup=InlineKeyboardMarkup(keyboard))

async def post_reward_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    state = await load_state(chat_id)

    await query.answer()
    action = query.data.split(":")[1]
    if action == "continue":
        await query.edit_message_text("You steel yourselves and proceed...")
        await start_level_2(context, chat_id)
    elif action == "end_campaign":
        await query.edit_message_text("Even with new assets, your party decides to walk away. The city doesn't care.")
        await reset_game_state(chat_id, state.get("thread_id"))

# ───────── Core Gameplay Loop ─────────
async def handle_player_action(update: Update, context: ContextTypes.DEFAULT_TYPE, player_action: str):
    chat_id = update.effective_chat.id
    state = await load_state(chat_id)

    players = state.get("players", [])
    if not players: return

    turn_index = state.get("turn_index", 0)
    current_player = players[turn_index]
    luck_score = random.randint(1, 10)

    is_boss_fight = state.get("boss") is not None
    system_prompt_addon = ""
    user_prompt_addon = ""

    boss_turn_action = None
    if is_boss_fight:
        boss = state["boss"]
        # Boss ability check
        if random.random() < 0.33 and boss.get("abilities"): # 33% chance to use an ability
            boss_ability = random.choice(boss["abilities"])
            boss_turn_action = f"The boss uses '{boss_ability['name']}'! {boss_ability['description']}"
            system_prompt_addon += "- A boss is present and uses an ability this turn. The narrative must reflect this. Calculate `player_damage` from the boss's action."

        system_prompt_addon += ("- The player is in a BOSS FIGHT. On success, they must deal `boss_damage`. The `event` should be 'victory' ONLY if the boss is defeated.\n"
                               "- Your JSON response MUST include an integer `boss_damage` field.")
        user_prompt_addon = f"Boss: {boss['name']} ({boss['hp']}/{boss['max_hp']} HP)\n"

    players_status = "\n".join([f"- {p['username']} ({p['faction']}): {p['hp']}/{p['max_hp']} HP" for p in players])

    system_prompt = (
        f"You are a Dungeon Master AI for a cyberpunk RPG. World lore:\n{LORE_SUMMARY}\n"
        "Evaluate the player's action. Rules:\n"
        "1. Categorize action into ONE of: 'strength', 'stealth', 'technology', 'communication'.\n"
        "2. Rate action's creativity/effectiveness (0-10) as `skill_score`.\n"
        "3. A faction specialty bonus (+1) will be added if category matches. `final_score = (skill_score + modifier) * luck_score`.\n"
        "4. Narrative must fit the lore. CRITICAL: If `final_score` <= 50, it MUST be a complete failure with NO progress.\n"
        "5. CRITICAL: On failure (score <= 50), `player_damage` must be at least 1 for the current player. Explain the damage source.\n"
        f"{system_prompt_addon}"
        "6. Respond ONLY with a JSON object: {'action_category': str, 'skill_score': int, 'narrative': str, 'player_damage': int, 'event': str ('none'|'level_complete'|'victory'), 'boss_damage': int (0 if not a boss fight)}"
    )
    user_prompt = (f"Current Party:\n{players_status}\n"
                   f"Active Player: {current_player['username']} ({current_player['faction']}, {current_player['hp']}/{current_player['max_hp']} HP, Specialty: '{current_player['modifier_type']}').\n"
                   f"Objective: {state['objective']}\n"
                   f"{user_prompt_addon}"
                   f"Previous Scene: '{state['narrative_log'][-1]}'\n"
                   f"Player Action: '{player_action}'\n"
                   f"Luck Score (d10): {luck_score}\n"
                   f"{'Boss Action: ' + boss_turn_action if boss_turn_action else ''}\nProvide JSON response.")

    try:
        response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], response_format={"type": "json_object"})
        result = json.loads(response.choices[0].message.content)
        skill_score, action_category = result.get('skill_score', 5), result.get('action_category', 'unknown')
        modifier = current_player['modifier_value'] if action_category == current_player['modifier_type'] else 0
        final_score = (skill_score + modifier) * luck_score
        outcome_tier, narrative, player_damage, event = get_outcome_tier(final_score), result.get('narrative', "The world glitches..."), result.get('player_damage', 0), result.get('event', 'none')

        current_player['hp'] -= player_damage
        if state.get("game_mode") != "open_campaign":
            state['actions_remaining'] -= 1

        modifier_text = f" (+{modifier} Faction)" if modifier > 0 else ""
        full_narrative = f"⚙️ Skill: {skill_score}{modifier_text} | 🎲 Luck: {luck_score} | *Total: {final_score}* ({outcome_tier})\n\n{narrative}"
        if player_damage > 0:
            full_narrative += f"\n\n{current_player['username']} takes *{player_damage} damage*! HP is now {current_player['hp']}/{current_player['max_hp']}."

        if is_boss_fight:
            boss_damage = result.get('boss_damage', 0)
            if boss_damage > 0 and final_score > 50:
                state['boss']['hp'] -= boss_damage
                full_narrative += f"\n\nYou deal *{boss_damage} damage* to {state['boss']['name']}! (HP: {state['boss']['hp']}/{state['boss']['max_hp']})"

        await send_message(context, chat_id, full_narrative)

        dead_player_this_turn = None
        if current_player['hp'] <= 0:
            await send_message(context, chat_id, f"💀 {current_player['username']} has fallen! The city consumes another soul.")
            dead_player_this_turn = current_player
            state['dead_players'].append(current_player)
            state['players'].pop(turn_index)
            # Adjust turn index if the dead player was before the next player in the list
            if turn_index >= len(state['players']):
                state['turn_index'] = 0
            # No need to change index if players after the dead one shift left.

        if not state['players']:
             await send_message(context, chat_id, "All players have fallen. Game over.")
             return await reset_game_state(chat_id, state.get("thread_id"))

        if not dead_player_this_turn:
            state['turn_index'] = (turn_index + 1) % len(state['players'])

        state['narrative_log'] = (state['narrative_log'] + [narrative])[-3:]

        if is_boss_fight and state['boss']['hp'] <= 0:
            await run_epilogue(context, chat_id, state)
        elif is_boss_fight and state['actions_remaining'] <= 0:
            await run_defeat_epilogue(context, chat_id, state)
        elif (event == 'level_complete' or state['actions_remaining'] <= 0) and state['level'] == 1 and state.get("game_mode") == "speed_mission":
            await trigger_pre_boss_menu(context, chat_id)
        elif state['actions_remaining'] <= 0 and state['level'] == 2:
            await send_message(context, chat_id, "You've run out of time. Your enemy overwhelms you. Defeat.")
            await reset_game_state(chat_id, state.get("thread_id"))
        else:
            await save_state(chat_id, state)
            await prompt_for_next_action(context, chat_id, state)

    except (json.JSONDecodeError, Exception) as e:
        logger.error("Error handling player message: %s", e)
        await send_message(context, chat_id, "A critical error occurred. Please try again.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    state = await load_state(chat_id)

    if state.get("game_stage") not in ["level_1", "level_2"]:
        return

    players = state.get("players", [])
    if not players: return

    turn_index = state.get("turn_index", 0)
    if user_id != players[turn_index]['id']:
        await update.message.reply_text(f"It's not your turn. Please wait for {players[turn_index]['username']}.")
        return

    await handle_player_action(update, context, update.message.text.strip())


async def ability_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    state = await load_state(chat_id)

    players = state.get("players", [])
    if not players: return

    turn_index = state.get("turn_index", 0)
    current_player = players[turn_index]

    if user_id != current_player['id']:
        return await query.answer("It's not your turn.", show_alert=True)

    await query.answer()
    ability_name = query.data.split(":", 1)[1]

    ability_used = None
    for ability in current_player.get("abilities", []):
        if ability['name'] == ability_name and ability['charges'] > 0:
            ability['charges'] -= 1
            ability_used = ability
            break

    if not ability_used:
        return await query.edit_message_text("Ability not found or out of charges.")

    await save_state(chat_id, state)
    await query.edit_message_text(f"{current_player['username']} uses *{ability_name}*!", parse_mode="Markdown")

    # We'll prepend a special marker to let handle_player_action know this is an ability
    await handle_player_action(update, context, f"[ABILITY]:{ability_name}")


async def suggested_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    state = await load_state(chat_id)

    players = state.get("players", [])
    if not players: return

    turn_index = state.get("turn_index", 0)
    if user_id != players[turn_index]['id']:
        return await query.answer("It's not your turn.", show_alert=True)

    await query.answer()
    action_text = query.data.split(":", 1)[1]
    await query.edit_message_text(f"{query.from_user.first_name} chose to: *{action_text}*", parse_mode="Markdown")
    await handle_player_action(update, context, action_text)


async def start_level(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    base_prompt = (f"You are a Dungeon Master for a cyberpunk RPG. World lore:\n{LORE_SUMMARY}\n"
                   "Provide JSON: {'objective': str, 'scene': str (<400 chars), 'actions': [str, str, str] (2-4 words each, <25 chars)}.")

    if state['level'] > 1:
        prompt = (f"{base_prompt}\nA party of players is continuing their mission against the Overcity. "
                  f"Context from previous scene: {state['narrative_log'][-1]}")
    else:  # It's the start of a new adventure
        location = random.choice(LOCATIONS)
        state['location'] = location  # Store the location for potential future use
        prompt = (f"{base_prompt}\nA party of players is starting a new mission against the Overcity. "
                  f"The adventure begins at this location:\n"
                  f"**Location Name:** {location['name']}\n"
                  f"**Location Description:** {location['description']}\n"
                  "Your generated scene and objective MUST be set within this specific location.")

    response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    content = json.loads(response.choices[0].message.content)
    state["objective"], state["narrative_log"] = content.get("objective"), [content.get("scene")]

    b64_json = await generate_image(f"A grimdark cyberpunk scene from Alpha City at {state.get('location', {}).get('name', '')}. {content.get('scene')}")
    caption = f"📍 *{state.get('location', {}).get('name', 'An unknown location...')}*\n*Objective: {state['objective']}*\n\n{state['narrative_log'][0]}"
    if b64_json:
        image_bytes = base64.b64decode(b64_json)
        await context.bot.send_photo(chat_id=chat_id, photo=image_bytes, caption=caption, message_thread_id=state.get("thread_id"), parse_mode="Markdown")
    else:
        await send_message(context, chat_id, caption)

    await save_state(chat_id, state)
    await prompt_for_next_action(context, chat_id, state)


async def prompt_for_next_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    players = state.get("players", [])
    if not players: return

    turn_index = state.get("turn_index", 0)
    current_player = players[turn_index]

    prompt = (f"You are a Dungeon Master for a cyberpunk RPG with this lore:\n{LORE_SUMMARY}\n"
              "Based on the last scene, generate a JSON object with a key 'actions' containing an array of three distinct, very short (2-4 words, max 25 characters) suggested actions. "
              f"Last scene: {state['narrative_log'][-1]}")
    response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    actions = json.loads(response.choices[0].message.content).get("actions", [])

    keyboard = [[InlineKeyboardButton(a, callback_data=f"action:{a.encode('utf-8')[:57].decode('utf-8','ignore')}")] for a in actions]

    # Add ability buttons
    ability_buttons = []
    for ability in current_player.get("abilities", []):
        if ability['charges'] > 0:
            button_text = f"💥 {ability['name']} ({ability['charges']})"
            ability_buttons.append(InlineKeyboardButton(button_text, callback_data=f"ability:{ability['name']}"))
    if ability_buttons:
        keyboard.append(ability_buttons)

    actions_text = f"({state['actions_remaining']} actions remaining)" if state.get("game_mode") != "open_campaign" else "(Open Campaign)"
    prompt_text = f"It's *{current_player['username']}'s* turn. Choose one of the following, or type your own custom action. {actions_text}"
    await send_message(context, chat_id, prompt_text, reply_markup=InlineKeyboardMarkup(keyboard))
    schedule_inactivity_timer(chat_id, context)


async def trigger_pre_boss_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    task = INACTIVITY_TIMERS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
    await send_message(context, chat_id, "You've completed your objective, but the real challenge lies ahead. Take a moment to prepare.")
    keyboard = [
        [InlineKeyboardButton("🤝 Recruit an Ally", callback_data="pre_boss:hire_help")],
        [InlineKeyboardButton("💎 Receive Treasure", callback_data="pre_boss:dig_treasure")],
        [InlineKeyboardButton("🚀 Continue Adventure", callback_data="pre_boss:continue")],
        [InlineKeyboardButton("🛑 End Campaign", callback_data="pre_boss:end_campaign")],
    ]
    await send_message(context, chat_id, "What's your next move?", reply_markup=InlineKeyboardMarkup(keyboard))

async def start_level_2(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    state = await load_state(chat_id)
    await send_message(context, chat_id, "*LEVEL 2: THE FINAL BATTLE*")
    await start_boss_fight(context, chat_id, state)

async def start_boss_fight(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    num_players = len(state.get("players", []))
    base_hp = 50
    hp_per_player = 15
    boss_hp = base_hp + (hp_per_player * num_players)

    state.update({"level": 2, "game_stage": 'level_2', "actions_remaining": ACTIONS_PER_LEVEL * num_players})
    prompt = (f"You are a Dungeon Master for a cyberpunk RPG. Lore:\n{LORE_SUMMARY}\n"
              f"A party of {num_players} players is starting a boss fight. Create a powerful Overcity enemy.\n"
              "The boss must have 2-3 unique, named abilities with short descriptions of their effect.\n"
              "Provide JSON: {'boss_name': str, 'boss_description': str, 'abilities': [{'name': str, 'description': str}], 'objective': str, 'scene': str (<400 chars, it MUST include a clear visual description of the boss), 'actions': [str, str, str] (short)}.")

    response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
    content = json.loads(response.choices[0].message.content)

    state["boss"] = {
        "name": content.get("boss_name"),
        "description": content.get("boss_description"),
        "abilities": content.get("abilities", []),
        "hp": boss_hp,
        "max_hp": boss_hp
    }
    state["objective"] = content.get("objective")
    state["narrative_log"] = [content.get("scene")]

    b64_json = await generate_image(f"A grimdark cyberpunk boss fight in Alpha City. {content.get('scene')}")
    caption = f"*Objective: {state['objective']}*\n\n{state['narrative_log'][0]}"
    if b64_json:
        image_bytes = base64.b64decode(b64_json)
        await context.bot.send_photo(chat_id=chat_id, photo=image_bytes, caption=caption, message_thread_id=state.get("thread_id"), parse_mode="Markdown")
    else:
        await send_message(context, chat_id, caption)

    await save_state(chat_id, state)
    await prompt_for_next_action(context, chat_id, state)

async def run_epilogue(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    task = INACTIVITY_TIMERS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
    state['game_stage'] = 'victory'
    await send_message(context, chat_id, "🏆 *VICTORY!* Generating your epilogue...")
    prompt = (f"You are a Dungeon Master for a cyberpunk RPG with this lore:\n{LORE_SUMMARY}\n"
              f"The party of players won their mission against the Overcity. Write a short, satisfying epilogue (<400 chars) based on this context: {state['narrative_log'][-1]}")
    response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], max_tokens=100)
    epilogue = response.choices[0].message.content.strip()
    await send_message(context, chat_id, epilogue)
    b64_json = await generate_image(f"A cyberpunk victory scene in Alpha City: {epilogue}")
    if b64_json:
        image_bytes = base64.b64decode(b64_json)
        await context.bot.send_photo(chat_id=chat_id, photo=image_bytes, caption="_Your victory, immortalized._", message_thread_id=state.get("thread_id"), parse_mode="Markdown")
    keyboard = [[InlineKeyboardButton("🤝 Recruit an Ally", callback_data="reward:character")],
                [InlineKeyboardButton("💎 Receive Treasure", callback_data="reward:item")]]
    await send_message(context, chat_id, "As a reward for your triumph, choose one:", reply_markup=InlineKeyboardMarkup(keyboard))
    await save_state(chat_id, state)

async def run_defeat_epilogue(context: ContextTypes.DEFAULT_TYPE, chat_id: int, state: dict):
    """Generates and sends a spectacular defeat message when the boss is not defeated in time."""
    await send_message(context, chat_id, "⌛ You're out of time...")
    prompt = (f"You are a Dungeon Master for a cyberpunk RPG. Lore:\n{LORE_SUMMARY}\n"
              f"The party failed to defeat the boss, {state['boss']['name']}, in time. Write a short, spectacular, and grim defeat epilogue (<300 chars). "
              f"Context: {state['narrative_log'][-1]}")
    response = await gpt_request(model="gpt-4-turbo", messages=[{"role": "user", "content": prompt}], max_tokens=75)
    defeat_message = response.choices[0].message.content.strip()
    await send_message(context, chat_id, f"☠️ *DEFEAT*\n\n{defeat_message}")
    await reset_game_state(chat_id, state.get("thread_id"))

async def reward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    reward_type = query.data.split(":", 1)[1]
    await query.edit_message_text(f"You chose to receive a new {reward_type}. Good choice.")
    await generate_and_send_reward(context, chat_id, reward_type, 20)
    state = await load_state(chat_id)
    await reset_game_state(chat_id, state.get("thread_id"))
    keyboard = [
        [InlineKeyboardButton("🤝 Hire Help", callback_data="main:hire_help")],
        [InlineKeyboardButton("💎 Dig for Treasure", callback_data="main:dig_treasure")],
        [InlineKeyboardButton("🚀 Speed Mission", callback_data="main:speed_mission")],
        [InlineKeyboardButton("⚔️ Boss Fight", callback_data="main:boss_fight")],
        [InlineKeyboardButton("🌍 Open Campaign", callback_data="main:open_campaign")],
    ]
    await send_message(context, chat_id, "The cycle begins anew. What's next?", reply_markup=InlineKeyboardMarkup(keyboard))

# ───────── Main & Polling ─────────
def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN missing")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("venture", venture))
    app.add_handler(CommandHandler("join", join_command))
    app.add_handler(CommandHandler("endgame", endgame_command))
    # Remove hack command for now as it conflicts with multiplayer
    # app.add_handler(CommandHandler("hack", hack_command)) 
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main:"))
    app.add_handler(CallbackQueryHandler(faction_selection_callback, pattern="^faction:"))
    app.add_handler(CallbackQueryHandler(pre_boss_menu_callback, pattern="^pre_boss:"))
    app.add_handler(CallbackQueryHandler(post_reward_menu_callback, pattern="^post_reward:"))
    app.add_handler(CallbackQueryHandler(reward_callback, pattern="^reward:"))
    app.add_handler(CallbackQueryHandler(suggested_action_callback, pattern="^action:"))
    app.add_handler(CallbackQueryHandler(ability_callback, pattern="^ability:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot polling started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

