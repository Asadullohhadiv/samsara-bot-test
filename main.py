import os
import re
import sqlite3
import threading
import uvicorn
from datetime import datetime
from typing import Dict, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Message,
    PhotoSize,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ChatMemberHandler,
)

import config
from database import (
    init_db,
    get_driver_by_chat,
    get_all_drivers,
    get_truck_number_from_group,
    get_mapping_by_truck_number,
    register_driver_auto,
    register_truck_mapping,
    set_dispatch,
    get_dispatch,
    set_truck_specs,
    get_truck_specs,
    set_dispatch_route,
    add_user_fuel_stop,
    add_points,
    get_points_balance,
    get_points_history,
    add_pti,
    get_pti_history,
    add_fuel_station_usage,
    get_fuel_station_usage,
    submit_verification,
    get_verification,
    add_cashout,
    get_cashouts,
    get_all_points_summary,
    get_all_pti_summary,
    get_all_fuel_usage,
    set_driver_credentials,
    add_pti_submission,
    save_bot_group,
    get_all_bot_groups,
)
from samsara_client import (
    get_vehicle_fuel_level,
    get_vehicle_location,
    get_vehicle_stats,
    find_vehicle_by_truck_number,
    get_driver_for_vehicle,
    get_all_vehicles,
)
from fuel_service import get_comprehensive_fuel_info, format_fuel_report_with_map
from route_service import geocode_address_to_coords
from utils import parse_dispatch_message

TOKEN = config.TELEGRAM_TOKEN

# ======================== PTI STATE ========================
PTI_STATES: Dict[int, Dict] = {}

PTI_STEPS = [
    ("Truck Front & Lights", "Please send a clear photo of the front of the truck, including lights and grill."),
    ("Engine Compartment", "Please open the hood and send a photo of the engine bay, fluids, and belts."),
    ("Truck Tires & Side", "Please send a photo showing the side of the truck, including all tires and fuel tank."),
    ("Coupling & Airlines", "Please send a photo of the fifth wheel, airlines, and cables."),
    ("Trailer & Tires", "Please send a photo of the trailer body, including tandems and underneath."),
    ("Rear & Lights", "Please send a photo of the rear lights, license plate, and doors."),
]

PTI_STEP_LABELS = ["Odometer"] + [s[0] for s in PTI_STEPS]

# ======================== PTI HELPERS ========================

def get_pti_state(chat_id: int) -> Optional[Dict]:
    return PTI_STATES.get(chat_id)

def set_pti_state(chat_id: int, state: Optional[Dict]):
    if state is None:
        PTI_STATES.pop(chat_id, None)
    else:
        PTI_STATES[chat_id] = state

# ======================== PTI FLOW ========================

async def start_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if get_pti_state(chat_id):
        await update.message.reply_text("You already have an unfinished inspection. Use /resume to continue or /cancel to start over.")
        return
    state = {
        "step": 0,
        "photos": [],           # list of (step_index, file_id, comment)
        "truck_number": None,
        "trailer_number": None,
        "driver_name": None,
        "created_at": datetime.now(),
    }
    set_pti_state(chat_id, state)
    driver_info = get_driver_by_chat(chat_id)
    if driver_info and driver_info.get('truck_number'):
        state['truck_number'] = driver_info['truck_number']
        await update.message.reply_text(
            f"🚛 **Start New PTI Inspection**\n\n"
            f"Truck # detected: **{state['truck_number']}**\n"
            "Enter your **Trailer Number** (or 'N/A').",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🚛 **Start New PTI Inspection**\n\n"
            "Please enter your **Truck Number** (e.g., 88817).",
            parse_mode="Markdown"
        )

async def handle_pti_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return
    text = update.message.text.strip()

    # Waiting for truck number
    if state["step"] == 0 and state.get("truck_number") is None:
        if not re.match(r'^[\dA-Za-z-]+$', text):
            await update.message.reply_text("❌ Invalid truck number. Please enter a valid truck number.")
            return
        state["truck_number"] = text
        await update.message.reply_text(
            f"✅ Truck # **{text}**\n\n"
            "Now enter your **Trailer Number** (optional, type 'N/A' if none)."
        )
        return

    # Waiting for trailer number
    if state["step"] == 0 and state.get("truck_number") is not None:
        state["trailer_number"] = text if text.upper() != "N/A" else ""
        state["step"] = 1
        state["photos"] = []
        driver_info = get_driver_by_chat(chat_id)
        if driver_info:
            state["driver_name"] = driver_info.get("driver_id", "")
        else:
            await update.message.reply_text("Please enter your full name (first and last).")
            state["step"] = -1
            return
        await ask_next_photo(update, state)
        return

    # Waiting for driver name
    if state["step"] == -1:
        state["driver_name"] = text
        state["step"] = 1
        await ask_next_photo(update, state)
        return

    # If waiting for a comment after a photo, store it
    if state.get("waiting_comment") == True:
        # Store comment for the last photo
        if state["photos"]:
            last_photo = state["photos"][-1]
            # last_photo is (step, file_id, comment) - update comment
            state["photos"][-1] = (last_photo[0], last_photo[1], text)
        # Clear waiting flag and proceed to next photo request (or review)
        state["waiting_comment"] = False
        state["step"] += 1
        await ask_next_photo(update, state)
        return

    # If in photo step and text, ignore
    await update.message.reply_text("Please send a photo, or use /cancel to stop.")

async def handle_pti_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return
    if state["step"] < 1:
        await update.message.reply_text("Please complete the text steps first.")
        return

    # If we were waiting for a comment, but driver sent a photo, ignore or treat as new? We'll ignore.
    if state.get("waiting_comment") == True:
        await update.message.reply_text("Please type a comment or press 'Next' to continue.")
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id
    step = state["step"]
    # Store photo with placeholder comment
    state["photos"].append((step, file_id, ""))
    # Ask for comment or next
    await ask_comment_or_next(update, state)

async def ask_comment_or_next(update: Update, state: Dict):
    """Ask driver to add comment (optional) or press Next."""
    chat_id = update.effective_chat.id
    step_label = PTI_STEP_LABELS[state["step"] - 1] if state["step"] - 1 < len(PTI_STEP_LABELS) else "Photo"
    keyboard = [[
        InlineKeyboardButton("⏭️ Next Photo", callback_data=f"pti_next_{state['step']}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📸 **{step_label}** photo received.\n"
        "If there is an issue, type a comment now (e.g., 'Lights damaged').\n"
        "Or press **Next Photo** to continue.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    state["waiting_comment"] = True

async def handle_pti_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return
    data = query.data

    if data.startswith("pti_next_"):
        # When user presses Next after a photo
        if state.get("waiting_comment") == True:
            state["waiting_comment"] = False
            state["step"] += 1
            await ask_next_photo(update, state)
        else:
            # If not waiting comment, ignore
            await query.message.reply_text("This action is not active.")

    elif data.startswith("pti_skip_"):
        step = int(data.split("_")[2])
        if state["step"] == step:
            # Skip this step, advance without photo
            state["photos"].append((step, "", "Skipped"))
            state["step"] += 1
            await ask_next_photo(update, state)
        else:
            await query.message.reply_text("This step is no longer active.")

    elif data == "pti_submit":
        await submit_pti(update, state)

    elif data == "pti_cancel":
        set_pti_state(chat_id, None)
        await query.message.reply_text("❌ PTI inspection cancelled.")

async def ask_next_photo(update: Update, state: Dict):
    chat_id = update.effective_chat.id
    step = state["step"]
    if step > len(PTI_STEPS):
        await show_review(update, state)
        return
    label, prompt = PTI_STEPS[step - 1]
    keyboard = [[
        InlineKeyboardButton("Skip / N/A", callback_data=f"pti_skip_{step}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📸 **Step {step} of {len(PTI_STEPS)}**\n\n"
        f"**{label}**\n{prompt}",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    state["waiting_comment"] = False

async def show_review(update: Update, state: Dict):
    chat_id = update.effective_chat.id
    collected = len([p for p in state["photos"] if p[1] != ""])  # count non-skipped
    total = len(PTI_STEPS)
    text = (
        f"📋 **PTI Summary**\n\n"
        f"Driver: {state.get('driver_name', 'Unknown')}\n"
        f"Truck #: {state.get('truck_number', 'N/A')}\n"
        f"Trailer #: {state.get('trailer_number', 'N/A')}\n\n"
        f"Photos collected: {collected}/{total}\n\n"
        f"Ready to submit?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Submit PTI", callback_data="pti_submit")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pti_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def submit_pti(update: Update, state: Dict):
    chat_id = update.effective_chat.id
    if not state["photos"]:
        await update.message.reply_text("No photos collected. Cannot submit.")
        return

    # Build media group: only photos with file_id (not skipped)
    media_group = []
    for step_idx, file_id, comment in state["photos"]:
        if file_id == "":
            continue  # skipped
        label = PTI_STEP_LABELS[step_idx - 1] if step_idx - 1 < len(PTI_STEP_LABELS) else "Photo"
        caption = label
        if comment:
            caption += f"\n⚠️ Comment: {comment}"
        media_group.append({
            "type": "photo",
            "media": file_id,
            "caption": caption,
        })

    caption = (
        f"📋 **NEW PRE-TRIP INSPECTION REPORT**\n\n"
        f"👤 Driver: {state.get('driver_name', 'Unknown')}\n"
        f"🚛 Truck #: {state.get('truck_number', 'N/A')}\n"
        f"📦 Trailer #: {state.get('trailer_number', 'N/A')}\n"
        f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"📸 Collected {len(media_group)} photos"
    )

    try:
        # Send media group to PTI group
        await context.bot.send_media_group(
            chat_id=config.PTI_GROUP_ID,
            media=media_group,
            caption=caption,
        )
        # Send brief notification to dispatcher group (if different)
        dispatcher_msg = (
            f"📋 **PTI COMPLETED**\n"
            f"👤 Driver: {state.get('driver_name', 'Unknown')}\n"
            f"🚛 Truck #: {state.get('truck_number', 'N/A')}\n"
            f"📦 Trailer #: {state.get('trailer_number', 'N/A')}\n"
            f"📊 Photos: {len(media_group)}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        if config.DISPATCHER_GROUP_ID and config.DISPATCHER_GROUP_ID != config.PTI_GROUP_ID:
            await context.bot.send_message(
                chat_id=config.DISPATCHER_GROUP_ID,
                text=dispatcher_msg,
                parse_mode="Markdown"
            )
        # Record in database
        driver_id = state.get('driver_name', '')
        truck_number = state.get('truck_number', '')
        trailer_number = state.get('trailer_number', '')
        photo_count = len(media_group)
        pti_number = f"PTI-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        add_pti(driver_id, pti_number)
        add_points(driver_id, config.POINTS_PER_PTI, "pti", f"PTI {pti_number}")
        add_pti_submission(driver_id, truck_number, trailer_number, photo_count)
        await update.message.reply_text(f"✅ PTI submitted successfully!\n\nYou earned {config.POINTS_PER_PTI} points.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to submit PTI: {e}")
    set_pti_state(chat_id, None)

async def cancel_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    set_pti_state(chat_id, None)
    await update.message.reply_text("❌ PTI inspection cancelled.")

async def resume_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        await update.message.reply_text("No unfinished inspection found.")
        return
    if state["step"] == -1:
        await update.message.reply_text("Please enter your full name.")
    elif state["step"] == 0 and state.get("truck_number") is None:
        await update.message.reply_text("Please enter your Truck Number.")
    elif state["step"] == 0 and state.get("truck_number") is not None:
        await update.message.reply_text("Please enter your Trailer Number.")
    else:
        await ask_next_photo(update, state)

# ======================== ORIGINAL COMMANDS ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            f"📊 Use the Driver App to access all features."
        )
    else:
        welcome_text = (
            "🚛 **Welcome!**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "I'm your Samsara assistant. Use the Driver App below to access fuel search, PTI submission, points tracking, and truck verification."
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
    web_app_url = config.WEB_APP_URL
    await update.message.reply_text("Please use the Driver App for fuel search.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⛽ Open Driver App", web_app={"url": web_app_url})]], resize_keyboard=True))

async def submit_pti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # The actual /pti command is handled separately
    await start_pti(update, context)

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
    amount_usd = balance * 0.01
    month = datetime.now().strftime("%Y-%m")
    add_cashout(driver_id, balance, amount_usd, month)
    add_points(driver_id, -balance, "cashout", f"Monthly cashout {month}")
    await update.message.reply_text(f"✅ Cashout processed: {balance} pts -> ${amount_usd:.2f}")

async def set_credentials_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != config.ADMIN_GROUP_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /set_credentials TRUCK_NUMBER PASSWORD")
        return
    truck_number, password = context.args[0], context.args[1]
    set_driver_credentials(truck_number, password)
    await update.message.reply_text(f"✅ Credentials set for truck {truck_number}")

async def list_groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != config.ADMIN_GROUP_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    groups = get_all_bot_groups()
    if not groups:
        await update.message.reply_text("No groups found.")
        return
    text = "📋 **Groups where bot is added:**\n"
    for g in groups:
        text += f"• `{g[1]}` (ID: {g[0]}) - Added: {g[2]}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        # Save group
        save_bot_group(chat_id, chat_title)
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
    # For non-PTI texts, we don't do anything
    pass

# ======================== MAIN ========================

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Original commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fuel", fuel_search_command))
    app.add_handler(CommandHandler("points", points_command))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("cashout", cashout_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("set_credentials", set_credentials_command))
    app.add_handler(CommandHandler("groups", list_groups_command))

    # PTI commands
    app.add_handler(CommandHandler("pti", start_pti))
    app.add_handler(CommandHandler("cancel", cancel_pti))
    app.add_handler(CommandHandler("resume", resume_pti))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(points_callback, pattern="points"))
    app.add_handler(CallbackQueryHandler(donate_callback, pattern="donate"))
    app.add_handler(CallbackQueryHandler(handle_pti_callback, pattern="pti_"))

    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_pti_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pti_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))  # for general photo handling
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # catch others

    # Chat member handler (group joins)
    app.add_handler(ChatMemberHandler(auto_register_on_join, ChatMemberHandler.MY_CHAT_MEMBER))

    # Start web server
    from webapp import app as web_app
    def run_web():
        render_port = int(os.environ.get("PORT", 10000))
        uvicorn.run(web_app, host="0.0.0.0", port=render_port, log_level="warning")
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    print("🌐 Web server running on port", os.environ.get("PORT", 10000))

    print("✅ Bot started with Driver App support!")
    app.run_polling()

if __name__ == "__main__":
    main()
