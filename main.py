import os
import re
import threading
import uvicorn
import asyncio
from datetime import datetime
from typing import Dict, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
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
    get_truck_number_from_group,
    get_mapping_by_truck_number,
    register_driver_auto,
    set_driver_credentials,
    add_points,
    get_points_balance,
    get_points_history,
    add_pti,
    add_pti_submission,
    save_bot_group,
    get_all_bot_groups,
    save_fault_code,
    save_harsh_event,
    save_maintenance_alert,
)
from samsara_client import (
    get_vehicle_stats,
    find_vehicle_by_truck_number,
    get_driver_for_vehicle,
    get_all_vehicles,
    get_fault_codes,
    get_harsh_events,
    get_maintenance_alerts,
    parse_fault_code,
    parse_harsh_event,
    parse_maintenance_alert,
)

TOKEN = config.TELEGRAM_TOKEN

# ======================== PTI STATE (simple, in-memory) ========================
PTI_STATES: Dict[int, Dict] = {}
PTI_STEPS = [
    ("Truck Front & Lights", "Send photo of truck front"),
    ("Engine Compartment", "Send photo of engine bay"),
    ("Truck Tires & Side", "Send photo of side and tires"),
    ("Coupling & Airlines", "Send photo of fifth wheel"),
    ("Trailer & Tires", "Send photo of trailer body and tires"),
    ("Rear & Lights", "Send photo of rear lights"),
]

def get_pti_state(chat_id):
    return PTI_STATES.get(chat_id)

def set_pti_state(chat_id, state):
    if state is None:
        PTI_STATES.pop(chat_id, None)
    else:
        PTI_STATES[chat_id] = state

# ======================== PTI FLOW ========================

async def start_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if get_pti_state(chat_id):
        await update.message.reply_text("You have an unfinished PTI. Use /cancel first.")
        return
    state = {"step": 0, "photos": [], "truck_number": None, "trailer_number": None, "driver_name": None}
    set_pti_state(chat_id, state)
    driver_info = get_driver_by_chat(chat_id)
    if driver_info and driver_info.get("truck_number"):
        state["truck_number"] = driver_info["truck_number"]
        await update.message.reply_text(f"Truck # {state['truck_number']} detected. Enter trailer number (or N/A):")
    else:
        await update.message.reply_text("Please enter your Truck Number:")

async def handle_pti_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return
    text = update.message.text.strip()

    if state["step"] == 0 and state.get("truck_number") is None:
        if not re.match(r"^[\dA-Za-z-]+$", text):
            await update.message.reply_text("Invalid truck number.")
            return
        state["truck_number"] = text
        await update.message.reply_text("Enter trailer number (or N/A):")
        return

    if state["step"] == 0 and state.get("truck_number") is not None:
        state["trailer_number"] = text if text.upper() != "N/A" else ""
        state["step"] = 1
        state["photos"] = []
        driver_info = get_driver_by_chat(chat_id)
        state["driver_name"] = driver_info.get("driver_id", "Unknown") if driver_info else "Unknown"
        await ask_next_photo(update, state)
        return

    # If we're waiting for a comment after a photo
    if state.get("waiting_comment"):
        if state["photos"]:
            last = state["photos"][-1]
            state["photos"][-1] = (last[0], last[1], text)
        state["waiting_comment"] = False
        state["step"] += 1
        await ask_next_photo(update, state)
        return

    await update.message.reply_text("Please send a photo, or use /cancel.")

async def handle_pti_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state or state["step"] < 1:
        return
    if state.get("waiting_comment"):
        await update.message.reply_text("Type a comment or press Next.")
        return
    photo = update.message.photo[-1]
    file_id = photo.file_id
    state["photos"].append((state["step"], file_id, ""))
    await ask_comment_or_next(update, state)

async def ask_comment_or_next(update: Update, state: Dict):
    keyboard = [[InlineKeyboardButton("Next Photo", callback_data="pti_next")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Photo received. Add a comment (optional) or press Next.",
        reply_markup=reply_markup,
    )
    state["waiting_comment"] = True

async def handle_pti_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    state = get_pti_state(chat_id)
    if not state:
        return
    if query.data == "pti_next":
        if state.get("waiting_comment"):
            state["waiting_comment"] = False
            state["step"] += 1
            await ask_next_photo(update, state)
    elif query.data == "pti_submit":
        await submit_pti(update, context, state)
    elif query.data == "pti_cancel":
        set_pti_state(chat_id, None)
        await query.message.reply_text("PTI cancelled.")

async def ask_next_photo(update: Update, state: Dict):
    step = state["step"]
    if step > len(PTI_STEPS):
        await show_review(update, state)
        return
    label, prompt = PTI_STEPS[step - 1]
    keyboard = [[InlineKeyboardButton("Skip / N/A", callback_data="pti_skip")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Step {step}: {label} – {prompt}", reply_markup=reply_markup)
    state["waiting_comment"] = False

async def show_review(update: Update, state: Dict):
    keyboard = [
        [InlineKeyboardButton("Submit PTI", callback_data="pti_submit")],
        [InlineKeyboardButton("Cancel", callback_data="pti_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"PTI complete. Ready to submit?", reply_markup=reply_markup)

async def submit_pti(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    # Send media group to admin group (or PTI group)
    media_group = []
    for step_idx, file_id, comment in state["photos"]:
        label = PTI_STEPS[step_idx - 1][0]
        caption = label
        if comment:
            caption += f"\n⚠️ Comment: {comment}"
        media_group.append({"type": "photo", "media": file_id, "caption": caption})
    if not media_group:
        await update.message.reply_text("No photos submitted.")
        return
    caption = f"📋 PTI REPORT\nTruck: {state['truck_number']}\nTrailer: {state['trailer_number']}\nDriver: {state['driver_name']}"
    target_group = getattr(config, "PTI_GROUP_ID", None) or config.ADMIN_GROUP_ID
    try:
        await context.bot.send_media_group(chat_id=target_group, media=media_group, caption=caption)
        # Record points
        add_pti(state["driver_name"], f"PTI-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        add_points(state["driver_name"], config.POINTS_PER_PTI, "pti", "PTI submitted")
        add_pti_submission(state["driver_name"], state["truck_number"], state["trailer_number"], len(media_group))
        await update.message.reply_text(f"✅ PTI submitted! You earned {config.POINTS_PER_PTI} points.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
    set_pti_state(update.effective_chat.id, None)

async def cancel_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_pti_state(update.effective_chat.id, None)
    await update.message.reply_text("PTI cancelled.")

async def resume_pti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_pti_state(update.effective_chat.id)
    if not state:
        await update.message.reply_text("No unfinished PTI.")
        return
    await ask_next_photo(update, state)

# ======================== ADMIN COMMANDS ========================

async def add_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != config.ADMIN_GROUP_ID:
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addgroup <group_id> <title>")
        return
    try:
        save_bot_group(int(context.args[0]), " ".join(context.args[1:]))
        await update.message.reply_text("Group saved.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != config.ADMIN_GROUP_ID:
        await update.message.reply_text("Not authorized.")
        return
    groups = get_all_bot_groups()
    text = "📋 Groups:\n" + "\n".join(f"• {g[1]} ({g[0]})" for g in groups) if groups else "No groups."
    await update.message.reply_text(text)

async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_by_chat(update.effective_chat.id)
    if not driver:
        await update.message.reply_text("Not registered.")
        return
    balance = get_points_balance(driver["driver_id"])
    await update.message.reply_text(f"💰 Points: {balance}")

# ======================== START COMMAND ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        save_bot_group(chat.id, chat.title)
        await update.message.reply_text(f"✅ Bot connected to group: {chat.title}")
        return
    web_app_url = config.WEB_APP_URL
    keyboard = [[KeyboardButton("⛽ Open Driver App", web_app={"url": web_app_url})]]
    await update.message.reply_text(
        "👋 Welcome! Use the button below to open the driver app.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )

# ======================== AUTO REGISTER ON JOIN ========================

async def auto_register_on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.my_chat_member:
        return
    old = update.my_chat_member.old_chat_member
    new = update.my_chat_member.new_chat_member
    if old.status in ["left", "kicked"] and new.status in ["member", "administrator"]:
        chat = update.effective_chat
        save_bot_group(chat.id, chat.title)
        truck_number = get_truck_number_from_group(chat.title)
        if truck_number:
            vehicle_id = find_vehicle_by_truck_number(truck_number)
            if vehicle_id:
                driver_id = get_driver_for_vehicle(vehicle_id) or f"driver_{truck_number}"
                register_driver_auto(chat.id, truck_number, driver_id, vehicle_id, "")
                await context.bot.send_message(
                    chat.id,
                    f"🚛 Truck {truck_number} connected! Driver ID: {driver_id}",
                )

# ======================== BACKGROUND SAMSARA SYNC ========================

async def samsara_monitor(app: Application):
    while True:
        try:
            vehicles = get_all_vehicles()
            for v in vehicles:
                vid = v.get("id")
                truck_number = v.get("name", "").split()[0] if v.get("name") else ""

                # Fault codes
                for f in get_fault_codes(vid) or []:
                    parsed = parse_fault_code(f)
                    save_fault_code(vid, truck_number, parsed["code"], parsed["description"], parsed["severity"])
                    # Find driver group
                    driver = get_driver_by_truck(truck_number)
                    if driver and driver.get("chat_id"):
                        await app.bot.send_message(
                            driver["chat_id"],
                            f"⚠️ FAULT: {parsed['code']} – {parsed['description']}",
                        )

                # Harsh events
                for e in get_harsh_events(vid) or []:
                    parsed = parse_harsh_event(e)
                    save_harsh_event(vid, truck_number, parsed["event_type"], parsed["location"], parsed["video_url"])
                    driver = get_driver_by_truck(truck_number)
                    if driver and driver.get("chat_id"):
                        msg = f"🚨 HARSH EVENT: {parsed['event_type']} at {parsed['location']}"
                        if parsed.get("video_url"):
                            msg += f"\n📹 {parsed['video_url']}"
                        await app.bot.send_message(driver["chat_id"], msg)

                # Maintenance
                for m in get_maintenance_alerts(vid) or []:
                    parsed = parse_maintenance_alert(m)
                    save_maintenance_alert(vid, truck_number, parsed["maintenance_type"], parsed["due_mileage"], parsed["location"])
                    driver = get_driver_by_truck(truck_number)
                    if driver and driver.get("chat_id"):
                        await app.bot.send_message(
                            driver["chat_id"],
                            f"🔧 MAINTENANCE: {parsed['maintenance_type']} due in {parsed['due_mileage']} miles",
                        )

            await asyncio.sleep(900)  # 15 minutes
        except Exception as e:
            print(f"❌ Monitor error: {e}")
            await asyncio.sleep(60)

# ======================== MAIN ========================

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pti", start_pti))
    app.add_handler(CommandHandler("cancel", cancel_pti))
    app.add_handler(CommandHandler("resume", resume_pti))
    app.add_handler(CommandHandler("points", points_command))
    app.add_handler(CommandHandler("addgroup", add_group_command))
    app.add_handler(CommandHandler("groups", groups_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_pti_callback, pattern="pti_"))

    # Photos / Text
    app.add_handler(MessageHandler(filters.PHOTO, handle_pti_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pti_text))

    # Group join
    app.add_handler(ChatMemberHandler(auto_register_on_join, ChatMemberHandler.MY_CHAT_MEMBER))

    # Background task
    async def post_init(application: Application):
        asyncio.create_task(samsara_monitor(application))

    app.post_init = post_init

    # Web server
    from webapp import app as web_app
    def run_web():
        port = int(os.environ.get("PORT", 10000))
        uvicorn.run(web_app, host="0.0.0.0", port=port, log_level="warning")
    threading.Thread(target=run_web, daemon=True).start()

    print("✅ Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
