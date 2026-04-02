# bot.py
import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    PreCheckoutQueryHandler,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS
from database.db import init_db
from scheduler.jobs import setup_scheduler

from handlers.start import start_handler, start_callback_handler
from handlers.watch import (
    watch_handler,
    watch_label_handler,
    watch_url_handler,
    watch_type_callback,
)
from handlers.list import list_handler, list_callback_handler
from handlers.remove import remove_handler, remove_callback
from handlers.digest import digest_handler
from handlers.upgrade import (
    upgrade_handler,
    upgrade_callback,
    pre_checkout_handler,
    successful_payment_handler,
)
from handlers.admin import admin_handler, admin_callback, admin_message_handler
from handlers.settings import settings_handler, settings_callback

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Suppress noisy HTTP logs that leak bot token into console
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# Conversation states
WATCH_LABEL, WATCH_URL, WATCH_TYPE = range(3)


def main():
    init_db()
    logger.info("Database initialized")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ── 1. ConversationHandler FIRST ──────────────────────────────────────────
    # Must be registered before any generic CallbackQueryHandler
    # so start_watch callback is caught here not by start_ router
    watch_conv = ConversationHandler(
        entry_points=[
            CommandHandler("watch", watch_handler),
            CallbackQueryHandler(watch_handler, pattern="^start_watch$"),
        ],
        states={
            WATCH_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_label_handler)],
            WATCH_URL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_url_handler)],
            WATCH_TYPE:  [CallbackQueryHandler(watch_type_callback, pattern="^watch_type_")],
        },
        fallbacks=[CommandHandler("start", start_handler)],
        conversation_timeout=120,
    )
    app.add_handler(watch_conv)

    # ── 2. Core command handlers ───────────────────────────────────────────────
    app.add_handler(CommandHandler("start",   start_handler))
    app.add_handler(CommandHandler("list",    list_handler))
    app.add_handler(CommandHandler("digest",  digest_handler))
    app.add_handler(CommandHandler("upgrade", upgrade_handler))
    app.add_handler(CommandHandler("remove",  remove_handler))
    app.add_handler(CommandHandler("admin",   admin_handler))
    app.add_handler(CommandHandler("settings", settings_handler))

    # ── 3. Telegram Stars payment handlers ────────────────────────────────────
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(
        filters.SUCCESSFUL_PAYMENT,
        successful_payment_handler,
    ))

    # ── 4. Callback query routers ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(start_callback_handler, pattern="^start_"))
    app.add_handler(CallbackQueryHandler(list_callback_handler,  pattern="^list_"))
    app.add_handler(CallbackQueryHandler(remove_callback,        pattern="^remove_"))
    app.add_handler(CallbackQueryHandler(upgrade_callback,       pattern="^upgrade_"))
    app.add_handler(CallbackQueryHandler(admin_callback,         pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^qh_"))


    # ── 5. Admin free-text handler (ADMIN_IDS only) ───────────────────────────
    if ADMIN_IDS:
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS),
            admin_message_handler,
        ))

    # ── 6. Scheduler ──────────────────────────────────────────────────────────
    setup_scheduler(app)
    logger.info("Scheduler started")

    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
