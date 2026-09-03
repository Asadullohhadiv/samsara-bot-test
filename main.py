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
    add_pti_submission,  # NEW
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

# Order of steps for photos (excluding identity)
PTI_STEPS = [
    ("Truck Front & Lights", "Please send a clear photo of the front of the truck, including lights and grill."),
    ("Engine Compartment", "Please open the hood and send a photo of the engine bay, fluids, and belts."),
    ("Truck Tires & Side", "Please send a photo showing the side of the truck, including all tires and fuel tank."),
    ("Coupling & Airlines", "Please send a photo of the fifth wheel, airlines, and cables."),
    ("Trailer & Tires", "Please send a photo of the trailer body, including tandems and underneath."),
    ("Rear & Lights", "Please send a photo of the rear lights, license plate, and doors."),
]

PTI_STEP_LABELS = ["Odometer"] + [s[0] for s in PTI_STEPS]

# ======================== HELPERS ========================

def get_pti_state(chat_id: int) -> Optional[Dict]:
    return PTI_STATES.get(chat_id)

def set_pti_state(chat_id: int, state: Optional[Dict]):
    if state is None:
        PTI_STATES.pop(chat_id, None)
    else:
        PTI_STATES[chat_id] = state

def pti_progress_text(state: Dict) -> str:
    total = len(PTI_STEPS) + 1  # +1 for odometer
    done = len(state.get('photos', []))
    return f"Collected: {done}/{total}"

# ======================== PTI FLOW ========================

async def start_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /pti - starts a new PTI inspection."""
    chat_id = update.effective_chat.id

    if get_pti_state(chat_id):
        await update.message.reply_text(
            "You already have an unfinished inspection. Use /resume to continue or /cancel to start over."
        )
        return

    # Initialize state
    state = {
        "step": 0,  # 0 = identity, 1..N = photos, N+1 = review
        "photos": [],  # list of (step_index, file_id)
        "truck_number": None,
        "trailer_number": None,
        "driver_name": None,
        "created_at": datetime.now(),
    }
    set_pti_state(chat_id, state)

    # Prefill truck number if driver is registered
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
    """Process text during PTI."""
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return

    text = update.message.text.strip()

    # Step 0: waiting for truck number (if not prefilled)
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

    # Step 0: waiting for trailer number
    if state["step"] == 0 and state.get("truck_number") is not None:
        state["trailer_number"] = text if text.upper() != "N/A" else ""
        state["step"] = 1
        state["photos"] = []

        # Ask for driver name if not known
        driver_info = get_driver_by_chat(chat_id)
        if driver_info:
            state["driver_name"] = driver_info.get("driver_id", "")
        else:
            await update.message.reply_text("Please enter your full name (first and last).")
            state["step"] = -1  # use -1 to request name
            return

        await ask_next_photo(update, state)
        return

    # Step -1: waiting for driver name
    if state["step"] == -1:
        state["driver_name"] = text
        state["step"] = 1
        await ask_next_photo(update, state)
        return

    # Other steps: ignore text
    await update.message.reply_text("Please send a photo, or use /cancel to stop.")

async def ask_next_photo(update: Update, state: Dict):
    """Send the next photo request."""
    chat_id = update.effective_chat.id
    step = state["step"]  # 1-based index

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

async def handle_pti_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process photo during PTI."""
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return

    if state["step"] < 1:
        await update.message.reply_text("Please complete the text steps first.")
        return

    # Use largest photo
    photo = update.message.photo[-1]
    file_id = photo.file_id
    step = state["step"]
    state["photos"].append((step, file_id))

    state["step"] += 1
    await ask_next_photo(update, state)

async def handle_pti_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle skip and submit buttons."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return

    data = query.data

    if data.startswith("pti_skip_"):
        step = int(data.split("_")[2])
        if state["step"] == step:
            state["step"] += 1
            await ask_next_photo(update, state)
        else:
            await query.message.reply_text("This step is no longer active.")

    elif data == "pti_submit":
        await submit_pti(update, state)

    elif data == "pti_cancel":
        set_pti_state(chat_id, None)
        await query.message.reply_text("❌ PTI inspection cancelled.")

async def show_review(update: Update, state: Dict):
    """Show summary and submit/cancel buttons."""
    chat_id = update.effective_chat.id
    collected = len(state["photos"])
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
    """Send media group to admin group, notify dispatcher, and record."""
    chat_id = update.effective_chat.id
    if not state["photos"]:
        await update.message.reply_text("No photos collected. Cannot submit.")
        return

    # Build media group (max 10 photos, we have up to 7)
    media_group = []
    for step_idx, file_id in state["photos"]:
        label = PTI_STEP_LABELS[step_idx - 1] if step_idx - 1 < len(PTI_STEP_LABELS) else "Photo"
        media_group.append({
            "type": "photo",
            "media": file_id,
            "caption": label,
        })

    caption = (
        f"📋 **NEW PRE-TRIP INSPECTION REPORT**\n\n"
        f"👤 Driver: {state.get('driver_name', 'Unknown')}\n"
        f"🚛 Truck #: {state.get('truck_number', 'N/A')}\n"
        f"📦 Trailer #: {state.get('trailer_number', 'N/A')}\n"
        f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"📸 Collected {len(state['photos'])} photos"
    )

    try:
        # 1. Send media group to admin group
        await context.bot.send_media_group(
            chat_id=config.ADMIN_GROUP_ID,
            media=media_group,
            caption=caption,  # caption is applied to first photo
        )

        # 2. Send text notification to dispatcher group
        dispatcher_msg = (
            f"📋 **PTI COMPLETED**\n"
            f"👤 Driver: {state.get('driver_name', 'Unknown')}\n"
            f"🚛 Truck #: {state.get('truck_number', 'N/A')}\n"
            f"📦 Trailer #: {state.get('trailer_number', 'N/A')}\n"
            f"📊 Photos: {len(state['photos'])}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        await context.bot.send_message(
            chat_id=config.DISPATCHER_GROUP_ID,
            text=dispatcher_msg,
            parse_mode="Markdown"
        )

        # 3. Record in database and award points
        driver_id = state.get('driver_name', '')
        truck_number = state.get('truck_number', '')
        trailer_number = state.get('trailer_number', '')
        photo_count = len(state['photos'])

        pti_number = f"PTI-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        add_pti(driver_id, pti_number)
        add_points(driver_id, config.POINTS_PER_PTI, "pti", f"PTI {pti_number}")
        add_pti_submission(driver_id, truck_number, trailer_number, photo_count)

        await update.message.reply_text(f"✅ PTI submitted successfully!\n\nYou earned {config.POINTS_PER_PTI} points.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to submit PTI: {e}")

    # Clear state
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
    # If in identity step, continue text; otherwise continue photo step
    if state["step"] == -1:
        await update.message.reply_text("Please enter your full name.")
    elif state["step"] == 0 and state.get("truck_number") is None:
        await update.message.reply_text("Please enter your Truck Number.")
    elif state["step"] == 0 and state.get("truck_number") is not None:
        await update.message.reply_text("Please enter your Trailer Number.")
    else:
        await ask_next_photo(update, state)

# ======================== OTHER COMMANDS (unchanged) ========================
# [Include all your existing commands: start, fuel, points, verify, admin, etc.]
# For brevity, assume they are present. Copy them from your current main.py.

# ======================== MAIN ========================

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Existing commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fuel", fuel_search_command))
    app.add_handler(CommandHandler("points", points_command))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("cashout", cashout_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("set_credentials", set_credentials_command))

    # PTI commands
    app.add_handler(CommandHandler("pti", start_pti))
    app.add_handler(CommandHandler("cancel", cancel_pti))
    app.add_handler(CommandHandler("resume", resume_pti))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(points_callback, pattern="points"))
    app.add_handler(CallbackQueryHandler(donate_callback, pattern="donate"))
    app.add_handler(CallbackQueryHandler(handle_pti_callback, pattern="pti_"))

    # Message handlers - order matters
    app.add_handler(MessageHandler(filters.PHOTO, handle_pti_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pti_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # your existing text handler

    # Chat member handler
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
