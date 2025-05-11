import os
import json
import asyncio
import logging
import time

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

# ───────── Constants & Globals ─────────
FACTIONS = {
    "Glitchborn": {"hp": 24, "description": "A living ghost: stealth and sabotage specialist."},
    "Nodewalker": {"hp": 22, "description": "Data-mystic: manipulates data streams and digital systems."},
    "Coinbroker": {"hp": 19, "description": "Underground financier: master of forbidden markets."},
    "Chainbreaker": {"hp": 28, "description": "Warrior-hero: brute strength and combat prowess."}
}
ENEMY_AC = 12
ENEMY_HP = 24

STATE_DIR = "game_states"
os.makedirs(STATE_DIR, exist_ok=True)

# In-memory guard for one active game per chat
ACTIVE_GAMES: dict[int, asyncio.Task] = {}

# Per-chat asyncio locks for file I/O
_STATE_LOCKS: dict[int, asyncio.Lock] = {}

def _get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _STATE_LOCKS:
        _STATE_LOCKS[chat_id] = asyncio.Lock()
    return _STATE_LOCKS[chat_id]


def _state_file(chat_id: int) -> str:
    return os.path.join(STATE_DIR, f"game_state_{chat_id}.json")

# ───────── Helpers ─────────
async def gpt_request(**kwargs):
    backoff = 1
    for _ in range(3):
        try:
            return await asyncio.to_thread(client.chat.completions.create, **kwargs)
        except Exception as e:
            logger.warning("GPT call failed, retrying in %ds: %s", backoff, e)
            await asyncio.sleep(backoff)
            backoff *= 2
    raise RuntimeError("GPT calls failed after 3 retries")

async def send_threaded(bot, chat_id: int, text: str, thread_id: int | None = None):  # type: ignore
    await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text)

# ───────── State Persistence ─────────
async def load_state(chat_id: int) -> dict:
    path = _state_file(chat_id)
    lock = _get_lock(chat_id)
    async with lock:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Failed to load state[%s]: %s", chat_id, e)
        return {
            "players": {},
            "log": [],
            "enemy_hp": ENEMY_HP,
            "game_over": False,
            "final_turns_active": False,
            "final_turns_received": {},
            "intro": "",
            "thread_id": None,
            "last_narrative": ""
        }

async def save_state(chat_id: int, state: dict):
    path = _state_file(chat_id)
    lock = _get_lock(chat_id)
    async with lock:
        try:
            with open(path, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error("Failed to save state[%s]: %s", chat_id, e)

# ───────── Final-Turns & Epilogue ─────────
async def trigger_final_turns(app: Application, chat_id: int):
    state = await load_state(chat_id)
    state["final_turns_active"] = True
    await save_state(chat_id, state)

    thread_id = state.get("thread_id")
    await send_threaded(app.bot, chat_id,
                        "⏰ The campaign is nearing its end! Each player gets one final action—type it now! You have one minute!",
                        thread_id)
    await asyncio.sleep(60)
    await run_epilogue(app, chat_id)

async def run_epilogue(app: Application, chat_id: int):
    state = await load_state(chat_id)
    if state["game_over"]:
        return

    final_actions = "\n".join(
        f"{state['players'][uid]['username']} ({state['players'][uid]['faction']}): {msg}"
        for uid, msg in state["final_turns_received"].items()
    ) or "No final actions recorded."

    system_prompt = (
        "You are a deranged Dungeon Master concluding a grimdark, dystopian cyberpunk D&D campaign set in Alpha City.\n"
        "Write an epilogue that describes the fate of each player based on their final actions.\n"
        "Tie these choices to the outcome of the rebellion. ≤500 chars."
    )
    prompt = (
        f"{system_prompt}\n\n"
        f"Campaign intro:\n{state['intro']}\n\n"
        f"Final actions:\n{final_actions}\n\n"
        "Write a ridiculous closing scene."
    )

    response = await gpt_request(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400
    )
    epilogue = response.choices[0].message.content.strip()

    thread_id = state.get("thread_id")
    await send_threaded(app.bot, chat_id, epilogue, thread_id)
    await send_threaded(app.bot, chat_id, "🏁 The campaign ends. Thank you for playing!", thread_id)

    state["game_over"] = True
    await save_state(chat_id, state)
    ACTIVE_GAMES.pop(chat_id, None)

# ───────── Slash-command Registration ─────────
async def set_commands(app: Application):
    await app.bot.set_my_commands([
        ("startgame", "Start a new campaign: /startgame <minutes>"),
        ("choosefaction", "Select your character's faction"),
        ("endgame", "Abort the campaign immediately")
    ])

# ───────── Handlers ─────────
async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user.id
    chat_id   = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id  # may be None

    logger.info("/startgame by %s in chat %s args=%s", user, chat_id, context.args)

    if chat_id in ACTIVE_GAMES:
        return await update.message.reply_text(
            "⚠️ A game is already running here. Use /endgame first."
        )

    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Usage: /startgame <minutes>")
    duration = int(context.args[0])

    state = {
        "players": {},
        "log": [],
        "enemy_hp": ENEMY_HP,
        "game_over": False,
        "final_turns_active": False,
        "final_turns_received": {},
        "intro": "",
        "thread_id": thread_id,
        "last_narrative": ""
    }
    await save_state(chat_id, state)

    lore_prompt = (
        "You are an unhinged Dungeon Master introducing a cyberpunk dystopian D&D campaign in Alpha City.\n"
        "Write a grimdark opening narrative (1–2 paragraphs) that sets the stakes and scenario using this lore:\n\n"
        "Decades ago, blockchain promised decentralization and freedom—but coin oligarchs hijacked it with AI and neural implants to surveil and control humanity.\n"
        "The evil factions include the rich Overlords, the all-seeing Singularity AI, and the Neuralifes - normal citizens controlled via neural implants.\n"
        "Underground rebels formed four factions: Glitchborn (stealth saboteurs), Nodewalkers (data-mystics), Coinbrokers (financiers), and Chainbreakers (augmented warriors).\n\n"
        "Use this lore to craft an opening scene that plunges players into the rebellion."
    )
    response = await gpt_request(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": lore_prompt}],
        max_tokens=500
    )
    intro = response.choices[0].message.content.strip()
    state["intro"] = intro
    await save_state(chat_id, state)

    await update.message.reply_text(intro)
    await update.message.reply_text(f"Players: /choosefaction\nGame lasts {duration} minute(s).")

    async def _auto_epilogue():
        logger.info("🕒 [Auto-epilogue] Task started for chat %s, sleeping %sm", chat_id, duration)
        await asyncio.sleep(duration * 60)
        logger.info("🕒 [Auto-epilogue] Woke up for chat %s, triggering final turns", chat_id)
        try:
            await trigger_final_turns(context.application, chat_id)
        except Exception:
            logger.exception("Auto-epilogue failed for %s", chat_id)

    task = asyncio.create_task(_auto_epilogue())
    ACTIVE_GAMES[chat_id] = task
    logger.info("🗂️ Scheduled epilogue task %s for chat %s", task, chat_id)

async def choosefaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = await load_state(chat_id)
    # only respond in game thread
    thread_id = state.get("thread_id")
    if thread_id and update.effective_message.message_thread_id != thread_id:
        return

    user = update.effective_user.id
    logger.info("/choosefaction by %s in chat %s", user, chat_id)

    if state["game_over"]:
        return await update.message.reply_text("Game over. /startgame to begin again.")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f, callback_data=f"faction:{f}")]
                               for f in FACTIONS])
    await update.message.reply_text("Choose your faction:", reply_markup=kb)

async def faction_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.callback_query.message.chat.id
    state = await load_state(chat_id)
    # only respond in game thread
    thread_id = state.get("thread_id")
    if thread_id and update.callback_query.message.message_thread_id != thread_id:
        return

    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    username = query.from_user.username or query.from_user.first_name
    faction = query.data.split(":", 1)[1]
    info = FACTIONS[faction]

    if state["game_over"]:
        return await query.edit_message_text("Game over. /startgame to begin again.")

    state["players"][user_id] = {
        "username": username,
        "faction": faction,
        "hp": info["hp"],
        "description": info["description"]
    }
    await save_state(chat_id, state)

    await query.edit_message_text(
        f"{username} joined as *{faction}*\n{info['description']}\nHP: {info['hp']}",
        parse_mode="Markdown"
    )

async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = await load_state(chat_id)
    # only respond in game thread
    thread_id = state.get("thread_id")
    if thread_id and update.effective_message.message_thread_id != thread_id:
        return

    user    = update.effective_user.id
    logger.info("/endgame by %s in chat %s — aborting game", user, chat_id)

    task = ACTIVE_GAMES.pop(chat_id, None)
    if task and not task.done():
        task.cancel()

    thread = state.get("thread_id")
    state.clear()
    state.update({
        "players": {},
        "log": [],
        "enemy_hp": ENEMY_HP,
        "game_over": True,
        "final_turns_active": False,
        "final_turns_received": {},
        "intro": "",
        "last_narrative": "",
        "thread_id": thread
    })
    await save_state(chat_id, state)

    await update.message.reply_text(
        "🛑 Game forcefully ended. Start a new one with /startgame."
    )

async def handle_player_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state   = await load_state(chat_id)

    # only respond in the topic where /startgame was run
    thread_id = state.get("thread_id")
    if thread_id and update.effective_message.message_thread_id != thread_id:
        return

    # **ignore anyone who hasn't chosen a faction**
    user_id = str(update.effective_user.id)
    if user_id not in state["players"]:
        return

    user_id = str(update.effective_user.id)
    text    = update.message.text.strip()

    if state["game_over"]:
        return
    if state["final_turns_active"]:
        if user_id in state["final_turns_received"]:
            return await update.message.reply_text("✅ Final action already recorded.")
        state["final_turns_received"][user_id] = text
        await save_state(chat_id, state)
        await update.message.reply_text("Final action noted.")
        if len(state["final_turns_received"]) >= len(state["players"]):
            await run_epilogue(context.application, chat_id)
        return

    # prune log
    state["log"].append(f"{update.effective_user.username or update.effective_user.first_name} → {text}")
    state["log"] = state["log"][-20:]
    await save_state(chat_id, state)

    system_msg = {
        "role": "system",
        "content": (
            "You are the deranged Dungeon Master for a grimdark cyberpunk RPG in Alpha City.\n"
            "- Players are the heroes of this story.\n"
            "- Describe what happens in response to player actions. Ensure that actions have stakes, e.g., occasionally applying damage, describing failed actions, etc."
            "Keep responses ≤300 chars, ending with an open-ended choice prompt (e.g., what do you do next?)."
        )
    }
    assistant_msg = {
        "role": "assistant",
        "content": state.get("last_narrative", state["intro"])
    }
    user_msg = {"role": "user", "content": text}

    response = await gpt_request(
        model="gpt-3.5-turbo",
        messages=[system_msg, assistant_msg, user_msg],
        max_tokens=300
    )
    narrative = response.choices[0].message.content.strip()

    state["last_narrative"] = narrative
    await save_state(chat_id, state)
    await update.message.reply_text(narrative)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update %s: %s", update, context.error, exc_info=True)
    return True

# ───────── Main & Polling ─────────
def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN missing")
        return

    app = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(set_commands)
        .build()
    )

    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("choosefaction", choosefaction))
    app.add_handler(CommandHandler("endgame", endgame))
    app.add_handler(CallbackQueryHandler(faction_selection_callback, pattern="^faction:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_player_message))
    app.add_error_handler(error_handler)

    logger.info("Bot polling started")
    while True:
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except NetworkError as e:
            logger.warning("NetworkError: %s – retry in 5s", e)
            time.sleep(5)
        except Exception as e:
            logger.error("Unexpected error: %s – restart in 5s", e, exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
