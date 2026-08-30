"""GuildVenture entry point. Importing this module has no network or credential side effects."""

from __future__ import annotations

import logging
import os

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from ai_service import AIService
from bot_service import BotService
from database import connect

logger = logging.getLogger(__name__)
COMMANDS = [
    ("start", "Welcome and start or resume"),
    ("venture", "Open the game menu"),
    ("join", "Join the faction lobby"),
    ("status", "Recover current game controls"),
    ("resume", "Resume this chat's game"),
    ("profile", "XP and career stats"),
    ("inventory", "Compare and manage equipment"),
    ("collection", "Saved ally roster"),
    ("allies", "Inspect and deploy an ally"),
    ("progression", "Choose career talents"),
    ("craft", "Workshop preview and materials"),
    ("recap", "Last encounter contributions"),
    ("campaign", "Chapter progress and objective"),
    ("camp", "Preparation and readiness"),
    ("rewards", "Claim rewards or resend receipts"),
    ("leaderboard", "Top gauntlet runs"),
    ("settings", "Owner presentation settings"),
    ("endgame", "Owner ends the run"),
    ("help", "Rules and commands"),
    ("info", "Rules and commands"),
]


async def dispatch(update, context):
    text = update.effective_message.text if update.effective_message else None
    if text and text.startswith("/") and "@" in text.split()[0]:
        target = text.split()[0].split("@", 1)[1]
        if target.lower() != (context.bot.username or "").lower():
            return
    await context.application.bot_data["service"].handle(update, context)


async def post_init(app):
    text_limit = nonnegative_setting("AI_DAILY_TEXT_LIMIT", 1000)
    image_limit = nonnegative_setting("AI_DAILY_IMAGE_LIMIT", 100)
    cooldown = nonnegative_setting("FREE_ROLL_COOLDOWN_SECONDS", 30)
    repository = await connect()
    try:
        ai = AIService(
            api_key=os.environ["OPENAI_API_KEY"],
            text_daily_limit=text_limit,
            image_daily_limit=image_limit,
        )
        service = BotService(
            repository,
            ai,
            default_images=os.getenv("DEFAULT_IMAGES", "true").lower() == "true",
            free_roll_cooldown=cooldown,
        )
        app.bot_data["service"] = service
        await app.bot.set_my_commands([BotCommand(command, description) for command, description in COMMANDS])
    except BaseException:
        if "service" in app.bot_data:
            await app.bot_data["service"].close()
        else:
            await repository.close()
        raise


def nonnegative_setting(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        if value < 0:
            raise ValueError
        return value
    except ValueError as exc:
        raise ValueError(f"{name} must be a nonnegative integer") from exc


async def post_shutdown(app):
    if service := app.bot_data.get("service"):
        await service.close()


async def error_handler(update, context):
    logger.error("Unhandled Telegram error: %s", type(context.error).__name__)


def build_application(token):
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(16)
        .connection_pool_size(32)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CallbackQueryHandler(dispatch))
    app.add_handler(MessageHandler(filters.TEXT, dispatch))
    app.add_error_handler(error_handler)
    return app


def main():
    logging.basicConfig(format="%(asctime)s %(name)s [%(levelname)s] %(message)s", level=logging.INFO)
    # HTTPX INFO logging includes Telegram bot-token URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    missing = [key for key in ("TELEGRAM_TOKEN", "DATABASE_URL", "OPENAI_API_KEY") if not os.getenv(key)]
    if missing:
        raise SystemExit("Missing required configuration: " + ", ".join(missing))
    build_application(os.environ["TELEGRAM_TOKEN"]).run_polling()


if __name__ == "__main__":
    main()
