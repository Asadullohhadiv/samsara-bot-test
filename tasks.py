import time
import asyncio
import logging
from datetime import datetime
import config
from database import (
    get_all_drivers,
    get_driver_by_vehicle_id,
    record_fuel_stop,
    record_harsh_event,
    record_fault_code,
    deduct_driver_points
)
from samsara_service import (
    get_fleet_fuel_levels,
    get_fault_codes,
    get_harsh_events
)
from fuel_service import get_comprehensive_fuel_info, format_fuel_report_with_map
from ai_service import analyze_harsh_event_coaching

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Cache to track processed alerts and prevent repetitive spam
PROCESSED_FAULTS = set()
PROCESSED_HARSH_EVENTS = set()
FUEL_ALERT_TRACKER = {}  # {vehicle_id: last_alert_timestamp}

async def monitor_fuel_levels(bot):
    """Monitors fuel levels for all vehicles and alerts drivers when fuel drops below threshold."""
    logging.info("Checking fleet fuel levels...")
    fuel_data = get_fleet_fuel_levels()
    if not fuel_data:
        return

    for vehicle in fuel_data:
        vehicle_id = vehicle.get("id")
        fuel_pct = vehicle.get("fuel_percent")
        
        if fuel_pct is None or fuel_pct > 25:
            continue

        # Prevent sending fuel alerts more than once every 4 hours per truck
        now = time.time()
        last_alert = FUEL_ALERT_TRACKER.get(vehicle_id, 0)
        if now - last_alert < 14400:
            continue

        driver = get_driver_by_vehicle_id(vehicle_id)
        if not driver or not driver.get("group_id"):
            continue

        lat = vehicle.get("latitude")
        lng = vehicle.get("longitude")
        
        if lat and lng:
            fuel_info = get_comprehensive_fuel_info(lat, lng)
            estimated_range = (fuel_pct / 100.0) * 800  # Approx 800 mile tank capacity
            report_msg = format_fuel_report_with_map(
                fuel_info,
                fuel_pct,
                estimated_range,
                driver.get("samsara_driver_id"),
                vehicle_id
            )
            
            try:
                await bot.send_message(chat_id=driver["group_id"], text=report_msg, parse_mode="HTML")
                FUEL_ALERT_TRACKER[vehicle_id] = now
                logging.info(f"Dispatched fuel alert for Truck #{driver['truck_number']}")
            except Exception as e:
                logging.error(f"Failed to send fuel alert to group {driver['group_id']}: {e}")

async def monitor_fault_codes(bot):
    """Checks Samsara for active vehicle fault codes and notifies the assigned driver's group."""
    logging.info("Checking engine fault codes...")
    faults = get_fault_codes()
    if not faults:
        return

    for fault in faults:
        fault_key = f"{fault.get('vehicle_id')}_{fault.get('code')}_{fault.get('timestamp')}"
        if fault_key in PROCESSED_FAULTS:
            continue

        vehicle_id = fault.get("vehicle_id")
        driver = get_driver_by_vehicle_id(vehicle_id)
        
        # Save record to local database
        record_fault_code(
            vehicle_id=vehicle_id,
            code=fault.get("code"),
            description=fault.get("description"),
            severity=fault.get("severity")
        )
        PROCESSED_FAULTS.add(fault_key)

        if driver and driver.get("group_id"):
            msg = (
                f"⚠️ <b>VEHICLE FAULT DETECTED</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"<b>Truck:</b> #{driver['truck_number']}\n"
                f"<b>Code:</b> <code>{fault.get('code')}</code>\n"
                f"<b>Severity:</b> {fault.get('severity', 'Unknown').upper()}\n"
                f"<b>Description:</b> {fault.get('description', 'No description available')}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Please report this to dispatch if red warning lights appear on the dash."
            )
            try:
                await bot.send_message(chat_id=driver["group_id"], text=msg, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed to dispatch fault code alert: {e}")

async def monitor_harsh_events(bot):
    """Monitors harsh driving events (braking, turning, acceleration) and triggers AI coaching warnings."""
    logging.info("Checking harsh safety events...")
    events = get_harsh_events()
    if not events:
        return

    for event in events:
        event_id = event.get("id")
        if event_id in PROCESSED_HARSH_EVENTS:
            continue

        vehicle_id = event.get("vehicle_id")
        driver = get_driver_by_vehicle_id(vehicle_id)
        
        # Record event in local DB and deduct driver points
        record_harsh_event(
            vehicle_id=vehicle_id,
            event_type=event.get("event_type"),
            location=event.get("location"),
            video_url=event.get("video_url")
        )
        deduct_driver_points(vehicle_id, points_to_deduct=5)
        PROCESSED_HARSH_EVENTS.add(event_id)

        if driver and driver.get("group_id"):
            ai_coaching = analyze_harsh_event_coaching(
                event_type=event.get("event_type"),
                speed=event.get("speed", "N/A"),
                g_force=event.get("g_force", "N/A")
            )
            
            msg = (
                f"🚨 <b>SAFETY HARSH EVENT WARNING</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"<b>Truck:</b> #{driver['truck_number']}\n"
                f"<b>Event:</b> {event.get('event_type')}\n"
                f"<b>Location:</b> {event.get('location', 'Unknown')}\n"
                f"<b>Penalty:</b> -5 Safety Points\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💡 <b>AI Safety Tip:</b>\n{ai_coaching}\n"
            )
            if event.get("video_url"):
                msg += f"\n📹 <a href='{event.get('video_url')}'>Watch Dashcam Recording</a>"

            try:
                await bot.send_message(chat_id=driver["group_id"], text=msg, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed to dispatch harsh event alert: {e}")

async def start_background_tasks(bot):
    """Main loop driving background polling routines."""
    while True:
        try:
            await monitor_fuel_levels(bot)
            await monitor_fault_codes(bot)
            await monitor_harsh_events(bot)
        except Exception as e:
            logging.error(f"Error in background monitoring task: {e}")
        
        # Poll every 3 minutes
        await asyncio.sleep(180)
