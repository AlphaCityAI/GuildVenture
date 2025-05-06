import json
import os
import re
import random
import asyncio
import time
import logging
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# OpenAI API key
import os
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

FACTIONS = {
    "Glitchborn": {"hp": 12, "description": "A living ghost: stealth and sabotage specialist."},
    "Nodewalker": {"hp": 10, "description": "Data-mystic: manipulates data streams and digital systems."},
    "Coinbroker": {"hp": 8, "description": "Underground financier: master of forbidden markets."},
    "Chainbreaker": {"hp": 14, "description": "Warrior-hero: brute strength and combat prowess."}
}

ACTIVE_GAME = {
    "chat_id": None,
    "end_task": None
}

ENEMY_AC = 12
ENEMY_HP = 10

STATE_DIR = "game_states"
if not os.path.exists(STATE_DIR):
    os.makedirs(STATE_DIR)

def get_state_file(chat_id):
    return os.path.join(STATE_DIR, f"game_state_{chat_id}.json")

def load_state(chat_id):
    filepath = get_state_file(chat_id)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    else:
        return {
            "players": {},
            "log": [],
            "enemy_hp": ENEMY_HP,
            "game_over": False,
            "final_turns_active": False,
            "final_turns_received": {},
            "intro": ""
        }

def save_state(chat_id, state):
    filepath = get_state_file(chat_id)
    with open(filepath, "w") as f:
        json.dump(state, f)

async def trigger_final_turns(app, chat_id):
    state = load_state(chat_id)
    state["final_turns_active"] = True
    save_state(chat_id, state)
    await app.bot.send_message(chat_id, "⏰ The campaign is nearing its end! Each player gets **one final action**. Type it now!")

    # Optional timeout: force end if players don’t reply in X minutes
    await asyncio.sleep(300)  # 5 min timeout
    await check_end_final_turns(app, chat_id)

async def check_end_final_turns(app, chat_id):
    state = load_state(chat_id)
    if state["game_over"]:
        return

    all_submitted = len(state["final_turns_received"]) >= len(state["players"])
    if all_submitted or True:  # remove "or True" if you only want timeout to wait for everyone
        # Build epilogue prompt
        final_actions = "\n".join(
            f"{state['players'][uid]['username']} ({state['players'][uid]['faction']}): {msg}"
            for uid, msg in state["final_turns_received"].items()
        )

        system_prompt = (
            "You are a Dungeon Master concluding a dystopian cyberpunk D&D campaign set in Alpha City.\n"
            "Write an epic, emotional narrative epilogue that describes the fate of each player individually, based on their final actions.\n"
            "Tie their choices to the overall outcome of the rebellion and the fate of Alpha City.\n"
            "Be cinematic, dramatic, and poetic, as if closing a movie."
            "Maximum 500 characters."
        )

        prompt = (
            f"{system_prompt}\n\n"
            f"Campaign intro:\n{state['intro']}\n\n"
            f"Final player actions:\n{final_actions}\n\n"
            f"Write a powerful closing scene."
        )

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )
        epilogue = response.choices[0].message.content.strip()

        await app.bot.send_message(chat_id, epilogue)
        await app.bot.send_message(chat_id, "🏁 The campaign ends. Thank you for playing in Alpha City!")

        state["game_over"] = True
        save_state(chat_id, state)

async def end_game(app, chat_id):
    await trigger_final_turns(app, chat_id)  # Instead of ending → trigger final turns

async def set_commands(app):
    commands = [
        ("startgame", "Start a new campaign in Alpha City, with a duration in minutes"),
        ("choosefaction", "Select your character's faction"),
        ("endgame", "End the current campaign")
    ]
    await app.bot.set_my_commands(commands)

async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # 1. only one game at a time
    if ACTIVE_GAME["chat_id"] is not None:
        await update.message.reply_text(
            "⚠️ A game is already running (in chat "
            f"{ACTIVE_GAME['chat_id']}). Use /endgame to stop it first."
        )
        return

    # 2. parse duration argument (minutes)
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /startgame <minutes>")
        return
    duration_minutes = int(context.args[0])
    state = {
        "players": {},
        "log": [],
        "enemy_hp": ENEMY_HP,
        "game_over": False,
        "final_turns_active": False,
        "final_turns_received": {},
        "intro": ""
    }

    lore_prompt = (
        "You are a Dungeon Master introducing a unique cyberpunk dystopian D&D campaign set in Alpha City.\n"
        "Use the following lore to generate a dramatic opening narrative that introduces the setting, stakes, and starting scenario for the players.\n"
        "Lore:\n"
        "Decades ago, the promise of blockchain technology was decentralization, freedom, and economic self-sovereignty. "
        "Humanity embraced the worldwide integration of trustless, transparent transactions — fully expecting that it would usher in a golden age of prosperity.\n\n"
        "But that dream was stolen. Under the guise of 'protection from volatility,' coin oligarchs seized control. "
        "Leveraging the power of artificial intelligence and social modification protocols, cabal syndicates created a brutal new system of oppression and insider corruption. "
        "They claimed they would use their power to share wealth. Instead, they hoarded it — turning the transparency of the blockchain into a tool for monitoring every transaction, "
        "interaction and identity. Meanwhile, cybernetic upgrades — advertised and sold as utility — instead became a system of propaganda distribution and thought-modification.\n\n"
        "The result was a worldwide dystopia, ruled by a coalition of trillionaire dynasties and their AI-led enslavement forces. "
        "With growing control over digital assets, weapons, and misinformation channels, these Overlords crushed their opponents and made cults of the masses. "
        "Meanwhile, neural implants became mandatory — ensuring blockchain-enabled thought-control from birth until death.\n\n"
        "Maligned and persecuted, the last vestiges of free humanity fled to the catacombs and abandoned warehouses in the Underground of Alpha City — the World Capital. "
        "Many were illegal children, natural born and without cybernetic manipulation. Others destroyed their bio-mods or hacked their implants, providing unique abilities free of Overlord control. "
        "Living in the shadows, building black-market weapons and benevolent AI, these refugees slowly began their rebellion.\n\n"
        "Factions of note:\n"
        "The Overcity: Throne of the Oppressors\n"
        "Overlords — Wealth-addicts corrupted by decades of unchallenged power. They own the economy, hoarding personal fortune while keeping the masses locked in financial servitude and tech-enforced slavery.\n\n"
        "The Singularity — Soulless AI council ruling with mathematical precision and unflinching violence. They act as judges, executioners, and gods — determining who thrives and who gets digitally (or physically) erased.\n\n"
        "Neuralifes — Overcity citizens indoctrinated via high-tech propaganda and neural implants. They despise the rebels of the Underground, even as the Underground fights to save them…\n\n"
        "The Underground: Last Bastion of Freedom\n"
        "Glitchborn — Illegal, unregistered, and born free of techno-implants, the Glitchborn are living ghosts. Saboteurs, assassins, and espionage-specialists, they operate outside the Overlords' control, unknown and unnamed even by The Singularity.\n\n"
        "Nodewalkers — Data-mystics of Alpha City, the Nodewalkers are the result of early-gen neural implants hacked and optimized by the Underground. Minds encrypted but wired into the blockchain, they can manipulate data, forge identities, and hijack Overcity mainframes.\n\n"
        "Coinbrokers — Black-market financiers of the rebellion. They deal in forbidden tokens, untraceable assets, and off-chain wealth. Their genius keeps the revolution alive by funding weapons, tech, and bribes.\n\n"
        "Chainbreakers — Born via genetic modification, Chainbreakers are the only humans strong enough to survive destruction of their neural implants. Wielding weaponized augments from the Underground, Chainbreakers are the warrior-heroes of the revolution."
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": lore_prompt}],
        max_tokens=500
    )

    intro_narrative = response.choices[0].message.content.strip()
    state["intro"] = intro_narrative

    save_state(chat_id, state)
    await update.message.reply_text(intro_narrative)
    await update.message.reply_text(
        f"Each player: /choosefaction  \n"
        f"The game will last {duration_minutes} minute(s)."
    )

    # End game after 60 minutes
    async def schedule_end():
        await asyncio.sleep(duration_minutes * 60)
        await end_game(context.application, chat_id)

    task = asyncio.create_task(schedule_end())
    ACTIVE_GAME["chat_id"] = chat_id
    ACTIVE_GAME["end_task"] = task

async def choosefaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = load_state(chat_id)
    if state.get("game_over"):
        await update.message.reply_text("The game is over. Please /startgame for a new campaign.")
        return

    buttons = [
        [InlineKeyboardButton(faction, callback_data=f"faction:{faction}")]
        for faction in FACTIONS.keys()
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Choose your faction:", reply_markup=keyboard)

async def faction_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    state = load_state(chat_id)
    if state.get("game_over"):
        await query.edit_message_text("The game is over. Please /startgame for a new campaign.")
        return

    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name

    faction_choice = query.data.split(":")[1]
    faction_info = FACTIONS[faction_choice]

    state["players"][str(user_id)] = {
        "username": username,
        "faction": faction_choice,
        "hp": faction_info["hp"],
        "description": faction_info["description"]
    }

    save_state(chat_id, state)

    await query.edit_message_text(
        f"{username} has joined as a **{faction_choice}**!\n{faction_info['description']}\nStarting HP: {faction_info['hp']}"
    )

async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        
        if not ACTIVE_GAME["chat_id"]:
            await update.message.reply_text("❌ No active game is running.")
            return
            
        if ACTIVE_GAME["chat_id"] != chat_id:
            await update.message.reply_text("❌ No active game in this chat.")
            return
            
        state = load_state(chat_id)
        if state.get("game_over", True):
            await update.message.reply_text("❌ Game is already over.")
            return

    # cancel the pending auto–end if it hasn't fired
    if ACTIVE_GAME["end_task"] and not ACTIVE_GAME["end_task"].done():
        ACTIVE_GAME["end_task"].cancel()

    # trigger the usual final-turns flow
    await end_game(context.application, chat_id)

    # clear the “lock”
    ACTIVE_GAME["chat_id"] = None
    ACTIVE_GAME["end_task"] = None

    await update.message.reply_text("🏁 Game manually ended.")

def parse_intent(message_text):
    lower = message_text.lower()
    if any(word in lower for word in ["attack", "strike", "shoot", "slash", "stab", "hit"]):
        return "attack"
    elif any(word in lower for word in ["move", "run", "sprint", "hide", "jump"]):
        return "move"
    elif any(word in lower for word in ["talk", "speak", "convince", "negotiate", "ask"]):
        return "interact"
    else:
        return "general"

def perform_attack():
    attack_roll = random.randint(1, 20)
    damage_roll = random.randint(1, 8)
    return attack_roll, damage_roll

async def handle_player_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info(f"Message received in chat {update.effective_chat.id}: {update.message.text!r}")
    chat_id = update.effective_chat.id
    state = load_state(chat_id)

    user_id = str(update.effective_user.id)
    username = update.effective_user.username or update.effective_user.first_name
    message_text = update.message.text

    if user_id not in state.get("players", {}):
        return  # Silently ignore messages from users who haven't chosen a faction

    if state.get("game_over"):
        await update.message.reply_text("The campaign has ended. Please /startgame to begin a new one.")
        return

    if state.get("final_turns_active"):
        if user_id in state["final_turns_received"]:
            await update.message.reply_text("✅ Your final action is already recorded!")
        else:
            state["final_turns_received"][user_id] = message_text
            save_state(chat_id, state)
            await update.message.reply_text(f"Final action recorded for {username}.")
            await check_end_final_turns(context.application, chat_id)
        return

    if user_id not in state["players"]:
        await update.message.reply_text("Please choose a faction first using /choosefaction!")
        return

    player_data = state["players"][user_id]
    faction = player_data["faction"]
    hp = player_data["hp"]

    intent = parse_intent(message_text)
    action_result = ""

    if intent == "attack":
        attack_roll, damage_roll = perform_attack()
        if attack_roll >= ENEMY_AC:
            state["enemy_hp"] -= damage_roll
            action_result = f"Attack roll: {attack_roll} (hit), Damage: {damage_roll}. Enemy HP now {state['enemy_hp']}."
            if state["enemy_hp"] <= 0:
                action_result += " The enemy is defeated!"
        else:
            action_result = f"Attack roll: {attack_roll} (miss)."
    elif intent == "move":
        action_result = f"{username} moves strategically."
    elif intent == "interact":
        action_result = f"{username} attempts to interact."
    else:
        action_result = f"{username} acts creatively."

    log_entry = f"{username} ({faction}): {message_text} → {action_result}"
    state["log"].append(log_entry)
    if len(state["log"]) > 5:
        state["log"] = state["log"][-5:]

    save_state(chat_id, state)

    system_prompt = (
        "You are a Dungeon Master narrating a cyberpunk dystopian RPG set in Alpha City.\n"
        "Describe the next scene with immersive, gritty narrative. End with a prompt for the players to make a decision. Max 300 characters."
    )

    current_state = "\n".join(
        [f"{p['username']} ({p['faction']}, {p['hp']} HP)" for p in state["players"].values()]
    )
    recent_log = "\n".join(state["log"])

    prompt = (
        f"{system_prompt}\n\n"
        f"Campaign intro:\n{state['intro']}\n\n"
        f"Current state:\n{current_state}\nEnemy HP: {state['enemy_hp']}\n\n"
        f"Recent events:\n{recent_log}\n\n"
        f"Describe what happens next."
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300
    )

    narrative = response.choices[0].message.content.strip()

    await update.message.reply_text(narrative)
    except Exception as e:
        logger.error(f"Error in handle_player_message: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while processing your message. Please try again.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # catches any exception in your handlers so they don't crash the polling loop
    print(f"[ERROR] update {update} raised {context.error!r}")
    return True

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found in secrets")
        return

    app = Application.builder().token(TOKEN).build()

    # your existing handlers…
    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("choosefaction", choosefaction))
    app.add_handler(CommandHandler("endgame", endgame))
    app.add_handler(CallbackQueryHandler(faction_selection_callback, pattern="^faction:"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_player_message))

    # register the global error‐handler
    app.add_error_handler(error_handler)

    print("Bot starting polling…")
    # retry loop to recover from network errors
    while True:
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except NetworkError as e:
            print(f"[WARN] NetworkError: {e!r}. Retrying in 5s…")
            time.sleep(5)
        except Exception as e:
            print(f"[CRASH] Unhandled exception: {e!r}. Restarting in 5s…")
            time.sleep(5)