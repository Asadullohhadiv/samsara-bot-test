import asyncio
import logging
import html
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

import config
from database import (
    init_db,
    save_bot_group,
    get_driver_by_truck,
    get_driver_points_by_truck,
    save_pti_submission
)
from tasks import start_background_tasks

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command in private chats and group channels."""
    chat = update.effective_chat
    
    # If added to a group, automatically register the group in DB
    if chat.type in ["group", "supergroup"]:
        save_bot_group(chat.id, chat.title)
        await update.message.reply_text(
            f"✅ <b>Truck Safety Bot Connected</b>\n\n"
            f"Group <b>{html.escape(chat.title)}</b> (ID: <code>{chat.id}</code>) is registered. "
            f"Assign driver accounts to this group using the Web Console.",
            parse_mode="HTML"
        )
        return

    # Private chat response with WebApp MiniApp launcher
    web_app_url = f"{config.WEBAPP_BASE_URL}/" if hasattr(config, "WEBAPP_BASE_URL") else "https://your-domain.com/"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚛 Open Fleet App", web_app_url=WebAppInfo(url=web_app_url))]
    ])
    
    welcome_text = (
        f"👋 Welcome to <b>My Truck Safety Portal</b>!\n\n"
        f"Use the button below to launch your driver web dashboard, check fuel stops, submit daily PTIs, "
        f"or review safety points."
    )
    await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode="HTML")

async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command `/points <truck_number>` to quickly check driver safety points."""
    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/points <truck_number></code>", parse_mode="HTML")
        return
        
    truck_num = context.args[0].strip()
    driver = get_driver_by_truck(truck_num)
    
    if not driver:
        await update.message.reply_text(f"❌ No driver or vehicle registered under Truck #{html.escape(truck_num)}.")
        return
        
    points = get_driver_points_by_truck(truck_num)
    await update.message.reply_text(
        f"💰 <b>Safety Balance for Truck #{html.escape(truck_num)}</b>\n"
        f"Current Balance: <b>{points} Points</b>",
        parse_mode="HTML"
    )

async def pti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command `/pti <truck_number> <pti_number>` to register a Pre-Trip Inspection directly from Telegram."""
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: <code>/pti <truck_number> <pti_number></code>", parse_mode="HTML")
        return
        
    truck_num = context.args[0].strip()
    pti_num = context.args[1].strip()
    
    driver = get_driver_by_truck(truck_num)
    if not driver:
        await update.message.reply_text(f"❌ Truck #{html.escape(truck_num)} is not registered in system.")
        return

    success = save_pti_submission(truck_num, pti_num)
    if success:
        await update.message.reply_text(
            f"✅ <b>PTI Recorded Successfully</b>\n"
            f"Truck: <b>#{html.escape(truck_num)}</b>\n"
            f"PTI Ticket: <code>{html.escape(pti_num)}</code>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Failed to save PTI submission. Please try again.")

async def group_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves group ID whenever the bot is added to a new Telegram chat/group."""
    chat = update.effective_chat
    if chat and chat.type in ["group", "supergroup"]:
        save_bot_group(chat.id, chat.title)

def main():
    # Initialize SQLite schema
    init_db()

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing in config!")
        return

    # Build Telegram Application
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Add Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("points", points_command))
    app.add_handler(CommandHandler("pti", pti_command))
    
    # Catch-all status handler for group invites
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, group_status_handler))

    # Initialize Bot and asyncio loop to run concurrent background tasks
    loop = asyncio.get_event_loop()
    
    async def run_bot_and_tasks():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("🤖 Telegram Bot started polling...")
        
        # Start Samsara background monitoring worker
        asyncio.create_task(start_background_tasks(app.bot))
        
        # Keep running
        await asyncio.Event().wait()

    try:
        loop.run_until_complete(run_bot_and_tasks())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopping bot server...")

if __name__ == "__main__":
    main()
