import re
import sqlite3
import requests
import os
import csv
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
import config
from database import (
    init_db, get_driver_by_chat, get_all_drivers, get_truck_number_from_group,
    get_mapping_by_truck_number, register_driver_auto, register_truck_mapping,
    set_dispatch, get_dispatch, set_truck_specs, get_truck_specs, DB_PATH,
    set_dispatch_route, add_user_fuel_stop,
    # new functions
    add_points, get_points_balance, get_points_history,
    add_pti, get_pti_history,
    add_fuel_station_usage, get_fuel_station_usage, confirm_fuel_station_usage,
    submit_verification, get_verification,
    add_cashout, get_cashouts,
    get_all_points_summary, get_all_pti_summary, get_all_fuel_usage
)
from samsara_client import get_vehicle_fuel_level, get_vehicle_location, get_vehicle_stats, find_vehicle_by_truck_number, get_driver_for_vehicle
from fuel_service import (
    get_comprehensive_fuel_info,
    format_fuel_report_with_map,
)
from route_service import geocode_address_to_coords
from utils import parse_dispatch_message
# No scheduler import!

TOKEN = config.TELEGRAM_TOKEN

# ================== Commands ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with buttons."""
    web_app_url = config.WEB_APP_URL
    keyboard = [
        [KeyboardButton("⛽ Open Driver App", web_app={"url": web_app_url})],
        [InlineKeyboardButton("📋 My Points", callback_data="points")],
        [InlineKeyboardButton("💖 Donate", callback_data="donate")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    chat_id = update.effective_chat.id
    driver_info = get_driver_by_chat(chat_id)

    if driver_info:
        truck = driver_info.get('truck_number', 'Unknown')
        welcome_text = (
            f"🚛 **Truck {truck}** – Hello, Driver!\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Connected to Samsara.\n"
            f"📊 Use the Driver App to find fuel, submit PTI, and track points.\n"
            f"💡 You earn points for fuel stops and PTIs."
        )
    else:
        welcome_text = (
            "🚛 **Welcome!**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "I'm your Samsara assistant.\n"
            "Use the Driver App below to:\n"
            "• Search fuel stations\n"
            "• Submit PTI\n"
            "• Track points\n"
            "• Verify your truck\n\n"
            "🔹 To get started, open the app and verify your truck."
        )

    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)

async def points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    driver_info = get_driver_by_chat(chat_id)
    if not driver_info:
        await query.edit_message_text("❌ Not registered.")
        return
    driver_id = driver_info["driver_id"]
    balance = get_points_balance(driver_id)
    history = get_points_history(driver_id, limit=5)
    text = f"💰 **Points Balance:** {balance}\n\nRecent:\n"
    if history:
        for row in history:
            text += f"• {row[0]} pts ({row[1]}) - {row[3]}\n"
    else:
        text += "No transactions yet."
    await query.edit_message_text(text, parse_mode="Markdown")

async def donate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = f"💖 **Support the Developer**\n\nDonate to keep this bot running:\n{config.DONATION_WALLET}"
    await query.edit_message_text(text, parse_mode="Markdown")

async def fuel_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open the web app."""
    web_app_url = config.WEB_APP_URL
    await update.message.reply_text("Please use the Driver App for fuel search.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⛽ Open Driver App", web_app={"url": web_app_url})]], resize_keyboard=True))

async def submit_pti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for PTI number."""
    chat_id = update.effective_chat.id
    driver_info = get_driver_by_chat(chat_id)
    if not driver_info:
        await update.message.reply_text("❌ Not registered.")
        return
    if len(context.args) == 1:
        pti_num = context.args[0]
        driver_id = driver_info["driver_id"]
        add_pti(driver_id, pti_num)
        add_points(driver_id, config.POINTS_PER_PTI, "pti", f"PTI {pti_num}")
        await update.message.reply_text(f"✅ PTI {pti_num} submitted. You earned {config.POINTS_PER_PTI} points!")
    else:
        await update.message.reply_text("Usage: /pti PTI_NUMBER")

async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    driver_info = get_driver_by_chat(chat_id)
    if not driver_info:
        await update.message.reply_text("❌ Not registered.")
        return
    driver_id = driver_info["driver_id"]
    balance = get_points_balance(driver_id)
    history = get_points_history(driver_id, limit=10)
    text = f"💰 **Points Balance:** {balance}\n\nRecent transactions:\n"
    for row in history:
        text += f"• {row[0]} pts ({row[1]}) - {row[3]}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to verify via mini app."""
    web_app_url = config.WEB_APP_URL + "?tab=verify"
    await update.message.reply_text("Please use the Driver App to verify your truck.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📷 Verify Truck", web_app={"url": web_app_url})]], resize_keyboard=True))

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id == config.ADMIN_GROUP_ID:
        points = get_all_points_summary()
        pti = get_all_pti_summary()
        fuel = get_all_fuel_usage()
        text = "📊 **Admin Summary**\n\nPoints:\n"
        for p in points:
            text += f"• {p[0]}: {p[1]} pts\n"
        text += "\nPTI Counts:\n"
        for p in pti:
            text += f"• {p[0]}: {p[1]}\n"
        text += "\nRecent Fuel Usage:\n"
        for f in fuel[:10]:
            text += f"• {f[0]} - {f[1]} ({f[2]})\n"
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Not authorized.")

async def cashout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monthly cashout: convert points to money."""
    chat_id = update.effective_chat.id
    driver_info = get_driver_by_chat(chat_id)
    if not driver_info:
        await update.message.reply_text("❌ Not registered.")
        return
    driver_id = driver_info["driver_id"]
    balance = get_points_balance(driver_id)
    if balance < config.MIN_POINTS_FOR_CASHOUT:
        await update.message.reply_text(f"❌ Need at least {config.MIN_POINTS_FOR_CASHOUT} points to cashout. You have {balance}.")
        return
    amount_usd = balance * 0.01  # example rate
    month = datetime.now().strftime("%Y-%m")
    add_cashout(driver_id, balance, amount_usd, month)
    add_points(driver_id, -balance, "cashout", f"Monthly cashout {month}")
    await update.message.reply_text(f"✅ Cashout processed: {balance} pts -> ${amount_usd:.2f}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle truck front photo (for verification)."""
    chat_id = update.effective_chat.id
    driver_info = get_driver_by_chat(chat_id)
    if not driver_info:
        await update.message.reply_text("❌ Not registered.")
        return
    photo = update.message.photo[-1]
    file = await photo.get_file()
    os.makedirs("uploads", exist_ok=True)
    file_path = f"uploads/{chat_id}_truck.jpg"
    await file.download_to_drive(file_path)
    await update.message.reply_text("📷 Truck photo received. If you haven't submitted verification, please use the app.")

# ================== Main ==================

async def auto_register_on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.my_chat_member:
        return
    new_member = update.my_chat_member.new_chat_member
    old_member = update.my_chat_member.old_chat_member
    was_removed = old_member.status in ['left', 'kicked']
    is_added = new_member.status in ['member', 'administrator']
    if not was_removed and is_added:
        chat = update.effective_chat
        chat_id = chat.id
        chat_title = chat.title or "Unknown Group"
        truck_number = get_truck_number_from_group(chat_title)
        if truck_number:
            vehicle_id = find_vehicle_by_truck_number(truck_number)
            if vehicle_id:
                driver_id = get_driver_for_vehicle(vehicle_id)
                if not driver_id:
                    driver_id = f"driver_{truck_number}"
                register_driver_auto(chat_id, truck_number, driver_id, vehicle_id, "")
                welcome_msg = (
                    f"🚛 **Truck {truck_number} Connected!**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Driver: {driver_id}\n"
                    f"Vehicle: {vehicle_id}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Use the Driver App to find fuel, submit PTI, and track points."
                )
                web_app_url = config.WEB_APP_URL
                keyboard = [[KeyboardButton("⛽ Open Driver App", web_app={"url": web_app_url})]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await context.bot.send_message(chat_id=chat_id, text=welcome_msg, parse_mode="Markdown", reply_markup=reply_markup)
                return
            mapping = get_mapping_by_truck_number(truck_number)
            if mapping:
                register_driver_auto(chat_id, truck_number, mapping["driver_id"], mapping["vehicle_id"], mapping.get("truck_license", ""))
                welcome_msg = (
                    f"🚛 **Truck {truck_number} Connected!**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Driver: {mapping['driver_id']}\n"
                    f"Vehicle: {mapping['vehicle_id']}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Use the Driver App to find fuel, submit PTI, and track points."
                )
                web_app_url = config.WEB_APP_URL
                keyboard = [[KeyboardButton("⛽ Open Driver App", web_app={"url": web_app_url})]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await context.bot.send_message(chat_id=chat_id, text=welcome_msg, parse_mode="Markdown", reply_markup=reply_markup)
                return
            else:
                await context.bot.send_message(chat_id, f"⚠️ Truck {truck_number} not found. Map it or contact admin.")
        else:
            await context.bot.send_message(chat_id, "⚠️ Could not detect truck number from group name. Rename group to start with truck number.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Keep for dispatch commands? Might not be needed.
    pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fuel", fuel_search_command))
    app.add_handler(CommandHandler("pti", submit_pti_command))
    app.add_handler(CommandHandler("points", points_command))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("cashout", cashout_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(points_callback, pattern="points"))
    app.add_handler(CallbackQueryHandler(donate_callback, pattern="donate"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(ChatMemberHandler(auto_register_on_join, ChatMemberHandler.MY_CHAT_MEMBER))

    # No scheduler start
    print("✅ Bot started with Driver App support!")



    app.run_polling()

if __name__ == "__main__":
    main()