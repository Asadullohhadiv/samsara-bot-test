import os
import json
import hmac
import hashlib
import base64
from datetime import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict
import requests
import config
from database import (
    get_mini_app_user, save_mini_app_user,
    add_points, get_points_balance, get_points_history,
    add_pti, get_pti_history,
    add_fuel_station_usage, get_fuel_station_usage,
    submit_verification,
    add_cashout,
    get_all_points_summary, get_all_pti_summary, get_all_fuel_usage,
    get_driver_by_chat,
    verify_driver_login,
    set_driver_credentials,
    register_driver_admin,
    get_driver_by_truck,
    list_all_drivers_admin,
    get_all_drivers,
    get_all_bot_groups,
    get_connection,
    save_fault_code, get_all_fault_codes,
    save_harsh_event, get_all_harsh_events,
    save_maintenance_alert, get_all_maintenance_alerts,
    add_pti_submission,
)
from samsara_client import (
    find_vehicle_by_truck_number,
    get_vehicle_stats,
    get_vehicle_fuel_level,
    get_all_vehicles,
    get_driver_for_vehicle,
    get_fault_codes,
    get_harsh_events,
    get_maintenance_alerts,
    parse_fault_code,
    parse_harsh_event,
    parse_maintenance_alert,
)
from fuel_service import get_comprehensive_fuel_info
from route_service import geocode_address_to_coords
import shutil

app = FastAPI()

# Temporary in-memory PTI sessions (use database for production)
PTI_SESSIONS: Dict[int, Dict] = {}

# PTI steps (same as bot)
PTI_STEPS = [
    ("Truck Front & Lights", "Please send a clear photo of the front of the truck, including lights and grill."),
    ("Engine Compartment", "Please open the hood and send a photo of the engine bay, fluids, and belts."),
    ("Truck Tires & Side", "Please send a photo showing the side of the truck, including all tires and fuel tank."),
    ("Coupling & Airlines", "Please send a photo of the fifth wheel, airlines, and cables."),
    ("Trailer & Tires", "Please send a photo of the trailer body, including tandems and underneath."),
    ("Rear & Lights", "Please send a photo of the rear lights, license plate, and doors."),
]

# Helper functions
def verify_init_data(init_data: str) -> bool:
    if not init_data:
        return False
    params = dict(p.split('=') for p in init_data.split('&') if '=' in p)
    if 'hash' not in params:
        return False
    hash_value = params.pop('hash')
    data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hashlib.sha256(config.TELEGRAM_TOKEN.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return computed_hash == hash_value

def get_user_from_init_data(init_data: str):
    try:
        params = dict(p.split('=') for p in init_data.split('&') if '=' in p)
        user_json = params.get('user', '{}')
        return json.loads(user_json)
    except:
        return None

def send_telegram_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Failed to send Telegram message: {e}")

def send_media_group(chat_id, media_group, caption):
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMediaGroup"
        payload = {"chat_id": chat_id, "media": media_group, "caption": caption}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Failed to send media group: {e}")

def upload_photo_to_telegram(photo_bytes):
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendPhoto"
        files = {"photo": ("photo.jpg", photo_bytes, "image/jpeg")}
        data = {"chat_id": config.ADMIN_GROUP_ID}
        resp = requests.post(url, files=files, data=data, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                return result["result"]["photo"][-1]["file_id"]
        return None
    except Exception as e:
        print(f"❌ Upload photo error: {e}")
        return None

# Routes
@app.get("/", response_class=HTMLResponse)
async def mini_app_page():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/donate", response_class=HTMLResponse)
async def donate_page():
    return f"<html><body><h2>💖 Donate</h2><p>{config.DONATION_WALLET}</p></body></html>"

@app.get("/api/user/{telegram_user_id}")
async def get_user(telegram_user_id: int):
    user = get_mini_app_user(telegram_user_id)
    if user:
        return user
    return {"driver_name": "", "truck_number": ""}

# ---- Login ----
class LoginRequest(BaseModel):
    truck_number: str
    password: str
    init_data: str

@app.post("/api/login")
async def login_api(req: LoginRequest):
    if not verify_driver_login(req.truck_number, req.password):
        return {"error": "Invalid truck number or password"}
    user = get_user_from_init_data(req.init_data)
    if user and user.get("id"):
        tg_id = user["id"]
        save_mini_app_user(tg_id, "", req.truck_number)
    else:
        pseudo_id = int(hashlib.sha256(req.truck_number.encode()).hexdigest()[:8], 16)
        save_mini_app_user(pseudo_id, "", req.truck_number)
    return {"success": True, "truck_number": req.truck_number}

# ---- Bot Groups ----
@app.get("/api/bot-groups")
async def bot_groups_api():
    try:
        groups = get_all_bot_groups()
        return {"groups": [{"chat_id": g[0], "title": g[1]} for g in groups]}
    except Exception as e:
        return {"error": str(e)}

# ---- Samsara Vehicles ----
@app.get("/api/samsara-vehicles")
async def samsara_vehicles_api():
    try:
        vehicles = get_all_vehicles()
        result = []
        for v in vehicles:
            result.append({
                "id": v.get("id"),
                "name": v.get("name", ""),
                "externalIds": v.get("externalIds", {}),
                "driverId": v.get("driver", {}).get("id") if v.get("driver") else None
            })
        return {"vehicles": result}
    except Exception as e:
        return {"error": str(e)}

# ---- Admin: Assign Driver ----
class AssignDriverRequest(BaseModel):
    vehicle_id: str
    truck_number: str
    group_id: int
    password: str
    truck_license: str = ""

@app.post("/api/admin/assign-driver")
async def admin_assign_driver_api(req: AssignDriverRequest):
    if not req.vehicle_id or not req.truck_number or not req.group_id or not req.password:
        return {"error": "All required fields must be filled"}
    try:
        driver_id = get_driver_for_vehicle(req.vehicle_id) or f"driver_{req.truck_number}"
        register_driver_admin(
            truck_number=req.truck_number,
            chat_id=req.group_id,
            samsara_driver_id=driver_id,
            vehicle_id=req.vehicle_id,
            password=req.password,
            truck_license=req.truck_license
        )
        send_telegram_message(
            req.group_id,
            f"🚛 **Welcome aboard!**\n\nTruck: `{req.truck_number}`\nLogin Password: `{req.password}`\n\nPlease open the Driver App and log in with these credentials."
        )
        return {"success": True, "message": f"Truck {req.truck_number} assigned successfully!"}
    except Exception as e:
        return {"error": str(e)}

# ---- Admin: Register Driver (manual) ----
class AdminRegisterDriverRequest(BaseModel):
    truck_number: str
    group_id: int
    samsara_driver_id: str
    vehicle_id: str
    password: str
    truck_license: str = ""

@app.post("/api/admin/register-driver")
async def admin_register_driver_api(req: AdminRegisterDriverRequest):
    if not req.truck_number or not req.group_id or not req.samsara_driver_id or not req.password:
        return {"error": "All required fields must be filled"}
    try:
        register_driver_admin(
            truck_number=req.truck_number,
            chat_id=req.group_id,
            samsara_driver_id=req.samsara_driver_id,
            vehicle_id=req.vehicle_id,
            password=req.password,
            truck_license=req.truck_license
        )
        send_telegram_message(
            req.group_id,
            f"🚛 **Welcome aboard!**\n\nTruck: `{req.truck_number}`\nLogin Password: `{req.password}`\n\nPlease open the Driver App and log in with these credentials."
        )
        return {"success": True, "message": f"Driver {req.truck_number} registered successfully!"}
    except Exception as e:
        return {"error": str(e)}

# ---- Admin: Trucks with fuel ----
@app.get("/api/admin/trucks-with-fuel")
async def admin_trucks_with_fuel():
    try:
        drivers = get_all_drivers()
        result = []
        for driver in drivers:
            chat_id, driver_id, vehicle_id, truck_number, truck_license = driver
            fuel_level = None
            if vehicle_id:
                fuel_stats = get_vehicle_stats(vehicle_id)
                if fuel_stats and 'fuel' in fuel_stats:
                    fuel_level = fuel_stats['fuel']
            result.append({
                "chat_id": chat_id,
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "truck_number": truck_number,
                "truck_license": truck_license,
                "fuel_level": fuel_level
            })
        return {"trucks": result}
    except Exception as e:
        return {"error": str(e)}

# ---- Fault Codes (corrected endpoint) ----
@app.get("/api/fault-codes")
async def fault_codes_api(vehicle_id: Optional[str] = None):
    try:
        raw_faults = get_fault_codes(vehicle_id=vehicle_id, limit=50)
        fault_list = []
        for raw in raw_faults:
            parsed_list = parse_fault_code(raw)
            for item in parsed_list:
                fault_list.append(item)
        # Also save to DB (optional)
        for fault in fault_list:
            save_fault_code(fault["vehicle_id"], fault["truck_number"], fault["code"], fault["description"], fault["severity"])
        return {"fault_codes": fault_list}
    except Exception as e:
        return {"error": str(e)}

# ---- Harsh Events (corrected endpoint) ----
@app.get("/api/harsh-events")
async def harsh_events_api(vehicle_id: Optional[str] = None):
    try:
        raw_events = get_harsh_events(vehicle_id=vehicle_id, limit=20)
        event_list = []
        for raw in raw_events:
            parsed = parse_harsh_event(raw)
            event_list.append(parsed)
            save_harsh_event(parsed["vehicle_id"], parsed["truck_number"], parsed["event_type"], parsed["location"], parsed["video_url"])
        return {"harsh_events": event_list}
    except Exception as e:
        return {"error": str(e)}

# ---- Maintenance Alerts (corrected endpoint) ----
@app.get("/api/maintenance-alerts")
async def maintenance_alerts_api(vehicle_id: Optional[str] = None):
    try:
        raw_alerts = get_maintenance_alerts(vehicle_id=vehicle_id, limit=20)
        alert_list = []
        for raw in raw_alerts:
            parsed = parse_maintenance_alert(raw)
            alert_list.append(parsed)
            save_maintenance_alert(parsed["vehicle_id"], parsed["truck_number"], parsed["maintenance_type"], parsed["due_mileage"], parsed["location"])
        return {"maintenance_alerts": alert_list}
    except Exception as e:
        return {"error": str(e)}

# ---- Fuel Search ----
class FuelSearchRequest(BaseModel):
    destination: Optional[str] = None
    init_data: str

@app.post("/api/fuel-search")
async def fuel_search_api(req: FuelSearchRequest):
    if not verify_init_data(req.init_data):
        raise HTTPException(status_code=403, detail="Unauthorized")
    user = get_user_from_init_data(req.init_data)
    if not user:
        raise HTTPException(status_code=400, detail="No user")
    tg_id = user.get("id")
    mini_user = get_mini_app_user(tg_id)
    if not mini_user:
        return {"error": "Please login first."}
    truck_number = mini_user["truck_number"]
    vehicle_id = find_vehicle_by_truck_number(truck_number)
    if not vehicle_id:
        return {"error": f"Truck {truck_number} not found in Samsara."}
    stats = get_vehicle_stats(vehicle_id)
    if not stats or stats.get("lat") is None:
        return {"error": "Vehicle location unavailable."}
    lat, lng = stats["lat"], stats["lng"]
    dest_lat = dest_lng = None
    if req.destination:
        dest_lat, dest_lng = geocode_address_to_coords("", req.destination, "", "")
        if dest_lat is None:
            return {"error": "Could not geocode destination."}
    fuel_info = get_comprehensive_fuel_info(lat, lng, fuel_type=4, route_mode=(dest_lat is not None), dest_lat=dest_lat, dest_lng=dest_lng)
    stations = fuel_info.get("station_list", [])
    output = []
    for s in stations:
        output.append({
            "name": s.get("name"),
            "brand": s.get("brand"),
            "address": s.get("address"),
            "city": s.get("city"),
            "state": s.get("state"),
            "price": s.get("price"),
            "distance_from_truck": s.get("distance_from_truck"),
            "maps_link": s.get("maps_link"),
            "lat": s.get("lat"),
            "lng": s.get("lng")
        })
    return {"stations": output, "fuel_level": stats.get("fuel")}

# ---- Use Station ----
class UseStationRequest(BaseModel):
    station_name: str
    station_address: str
    station_lat: float
    station_lng: float
    price: float
    init_data: str

@app.post("/api/use-station")
async def use_station_api(req: UseStationRequest):
    if not verify_init_data(req.init_data):
        raise HTTPException(status_code=403, detail="Unauthorized")
    user = get_user_from_init_data(req.init_data)
    if not user:
        raise HTTPException(status_code=400, detail="No user")
    tg_id = user.get("id")
    driver_info = get_driver_by_chat(tg_id)
    if not driver_info:
        return {"error": "Driver not registered."}
    driver_id = driver_info["driver_id"]
    add_fuel_station_usage(driver_id, req.station_name, req.station_address, req.station_lat, req.station_lng, req.price)
    add_points(driver_id, config.POINTS_PER_FUEL_STOP, "fuel", f"Fuel stop at {req.station_name}")
    return {"message": f"Recorded! You earned {config.POINTS_PER_FUEL_STOP} points."}

# ---- PTI: Start ----
class PTIStartRequest(BaseModel):
    truck_number: str
    trailer_number: Optional[str] = None
    init_data: str

@app.post("/api/pti/start")
async def pti_start(req: PTIStartRequest):
    user = get_user_from_init_data(req.init_data)
    if not user:
        return {"error": "No user"}
    tg_id = user.get("id")
    # Store session
    PTI_SESSIONS[tg_id] = {
        "step": 0,
        "photos": [],
        "truck_number": req.truck_number,
        "trailer_number": req.trailer_number or "",
        "created_at": datetime.now(),
    }
    return {"success": True, "message": "PTI started", "steps": len(PTI_STEPS), "first_step": PTI_STEPS[0]}

# ---- PTI: Upload Photo ----
class PTIPhotoRequest(BaseModel):
    step_index: int
    comment: Optional[str] = ""
    init_data: str

@app.post("/api/pti/photo")
async def pti_photo(req: PTIPhotoRequest, photo: UploadFile = File(...)):
    user = get_user_from_init_data(req.init_data)
    if not user:
        return {"error": "No user"}
    tg_id = user.get("id")
    session = PTI_SESSIONS.get(tg_id)
    if not session:
        return {"error": "No active PTI session"}
    if req.step_index != session["step"]:
        return {"error": f"Expected step {session['step']}, got {req.step_index}"}
    
    photo_bytes = await photo.read()
    file_id = upload_photo_to_telegram(photo_bytes)
    if not file_id:
        return {"error": "Failed to upload photo"}
    
    session["photos"].append({
        "step": req.step_index,
        "file_id": file_id,
        "comment": req.comment
    })
    session["step"] += 1
    next_step = session["step"]
    if next_step >= len(PTI_STEPS):
        return {"success": True, "complete": True, "message": "All photos collected", "next_step": next_step}
    return {"success": True, "complete": False, "next_step": next_step, "step_info": PTI_STEPS[next_step]}

# ---- PTI: Submit ----
class PTISubmitRequest(BaseModel):
    init_data: str

@app.post("/api/pti/submit")
async def pti_submit(req: PTISubmitRequest):
    user = get_user_from_init_data(req.init_data)
    if not user:
        return {"error": "No user"}
    tg_id = user.get("id")
    session = PTI_SESSIONS.get(tg_id)
    if not session:
        return {"error": "No active PTI session"}
    
    # Build media group
    media_group = []
    for photo_data in session["photos"]:
        label = PTI_STEPS[photo_data["step"]][0]
        caption = label
        if photo_data["comment"]:
            caption += f"\n⚠️ Comment: {photo_data['comment']}"
        media_group.append({"type": "photo", "media": photo_data["file_id"], "caption": caption})
    
    caption = (
        f"📋 **NEW PRE-TRIP INSPECTION REPORT**\n\n"
        f"👤 Driver: {session.get('driver_name', 'Unknown')}\n"
        f"🚛 Truck #: {session.get('truck_number', 'N/A')}\n"
        f"📦 Trailer #: {session.get('trailer_number', 'N/A')}\n"
        f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"📸 Collected {len(session['photos'])} photos"
    )
    
    target_group = getattr(config, "PTI_GROUP_ID", None) or config.ADMIN_GROUP_ID
    try:
        send_media_group(target_group, media_group, caption)
        driver_id = session.get("truck_number", "")
        add_pti_submission(driver_id, session.get("truck_number"), session.get("trailer_number"), len(session["photos"]))
        add_points(driver_id, config.POINTS_PER_PTI, "pti", f"PTI {datetime.now().strftime('%Y%m%d%H%M%S')}")
        del PTI_SESSIONS[tg_id]
        return {"success": True, "message": f"PTI submitted! You earned {config.POINTS_PER_PTI} points."}
    except Exception as e:
        return {"error": str(e)}

# ---- Points ----
class PointsRequest(BaseModel):
    init_data: str

@app.post("/api/points")
async def points_api(req: PointsRequest):
    if not verify_init_data(req.init_data):
        raise HTTPException(status_code=403, detail="Unauthorized")
    user = get_user_from_init_data(req.init_data)
    if not user:
        raise HTTPException(status_code=400, detail="No user")
    tg_id = user.get("id")
    driver_info = get_driver_by_chat(tg_id)
    if not driver_info:
        return {"error": "Driver not registered."}
    driver_id = driver_info["driver_id"]
    balance = get_points_balance(driver_id)
    history = get_points_history(driver_id, limit=10)
    history_list = []
    for row in history:
        history_list.append({"amount": row[0], "type": row[1], "date": row[3]})
    return {"balance": balance, "history": history_list}

# ---- History ----
@app.post("/api/history")
async def history_api(req: PointsRequest):
    if not verify_init_data(req.init_data):
        raise HTTPException(status_code=403, detail="Unauthorized")
    user = get_user_from_init_data(req.init_data)
    if not user:
        raise HTTPException(status_code=400, detail="No user")
    tg_id = user.get("id")
    driver_info = get_driver_by_chat(tg_id)
    if not driver_info:
        return {"error": "Driver not registered."}
    driver_id = driver_info["driver_id"]
    fuel_usage = get_fuel_station_usage(driver_id, limit=10)
    pti_history = get_pti_history(driver_id, limit=10)
    fuel_list = []
    for row in fuel_usage:
        fuel_list.append({"station_name": row[0], "used_at": row[5]})
    pti_list = []
    for row in pti_history:
        pti_list.append({"pti_number": row[0], "submitted_at": row[1]})
    return {"fuel_usage": fuel_list, "pti_history": pti_list}

# ---- Admin Summary ----
@app.get("/api/admin-summary")
async def admin_summary_api():
    points = get_all_points_summary()
    pti = get_all_pti_summary()
    fuel = get_all_fuel_usage()
    points_list = [{"driver_id": r[0], "balance": r[1]} for r in points]
    pti_list = [{"driver_id": r[0], "count": r[1]} for r in pti]
    fuel_list = []
    for row in fuel:
        fuel_list.append({
            "driver_id": row[0],
            "station_name": row[1],
            "used_at": row[2],
            "is_confirmed": row[3]
        })
    return {"points": points_list, "pti": pti_list, "fuel_usage": fuel_list}

# ---- Driver Details ----
@app.get("/api/driver-details/{truck_number}")
async def driver_details_api(truck_number: str):
    driver = get_driver_by_truck(truck_number)
    if not driver:
        return {"error": "Driver not found"}
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT COALESCE(SUM(amount),0) FROM points WHERE driver_id = %s', (truck_number,))
    balance = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM pti_submissions WHERE driver_id = %s', (truck_number,))
    pti_count = c.fetchone()[0]
    conn.close()
    return {
        "truck_number": truck_number,
        "driver_id": driver["samsara_driver_id"],
        "vehicle_id": driver["vehicle_id"],
        "truck_license": driver["truck_license"],
        "chat_id": driver["chat_id"],
        "password": driver["password"],
        "points": balance,
        "pti_count": pti_count
    }
