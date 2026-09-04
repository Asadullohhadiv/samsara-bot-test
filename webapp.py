import os
import json
import hmac
import hashlib
import uuid
import shutil
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests

import config
from database import (
    get_mini_app_user, save_mini_app_user,
    add_points, get_points_balance, get_points_history,
    add_pti, get_pti_history,
    add_fuel_station_usage, get_fuel_station_usage,
    submit_verification,
    get_all_points_summary, get_all_pti_summary, get_all_fuel_usage,
    get_driver_by_chat,
    verify_driver_login,
    register_driver_admin,
    get_driver_by_truck,
    get_all_drivers,
    get_all_bot_groups,
    get_connection,
    get_all_fault_codes,
    get_all_harsh_events,
    get_all_maintenance_alerts,
)
from samsara_client import (
    find_vehicle_by_truck_number,
    get_vehicle_stats,
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

app = FastAPI()

# ---------- Helpers ----------

def verify_init_data(init_data: str) -> bool:
    if not init_data:
        return False
    try:
        # Split with maxsplit=1 to handle '=' inside json user data
        params = dict(p.split('=', 1) for p in init_data.split('&') if '=' in p)
        if 'hash' not in params:
            return False
        hash_value = params.pop('hash')
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hashlib.sha256(config.TELEGRAM_TOKEN.encode()).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return computed_hash == hash_value
    except Exception:
        return False

def get_user_from_init_data(init_data: str):
    try:
        params = dict(p.split('=', 1) for p in init_data.split('&') if '=' in p)
        user_json = params.get('user', '{}')
        return json.loads(user_json)
    except Exception:
        return None

def send_telegram_message(chat_id: int, text: str):
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Failed to send Telegram message: {e}")

# ---------- Security Middleware / Dependencies ----------

def verify_admin_auth(x_admin_token: Optional[str] = Header(None)):
    """Simple Admin API Key check for administrative routes."""
    admin_secret = getattr(config, "ADMIN_SECRET_KEY", None)
    if admin_secret and x_admin_token != admin_secret:
        raise HTTPException(status_code=401, detail="Unauthorized Admin Access")

# ---------- Standard Routes (Using sync def to run on threadpool) ----------

@app.get("/", response_class=HTMLResponse)
def mini_app_page():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/donate", response_class=HTMLResponse)
def donate_page():
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Donate</title></head>
    <body style="font-family:Arial; text-align:center; padding:20px;">
        <h2>💖 Support the Developer</h2>
        <p>If you find this bot useful, consider donating:</p>
        <p><b>Crypto Wallet:</b> {getattr(config, 'DONATION_WALLET', 'N/A')}</p>
    </body>
    </html>
    """

@app.get("/api/user/{telegram_user_id}")
def get_user(telegram_user_id: int):
    user = get_mini_app_user(telegram_user_id)
    if user:
        return user
    return {"driver_name": "", "truck_number": ""}

# ---------- Login ----------
class LoginRequest(BaseModel):
    truck_number: str
    password: str
    init_data: str

@app.post("/api/login")
def login_api(req: LoginRequest):
    if not verify_init_data(req.init_data):
        raise HTTPException(status_code=403, detail="Invalid Telegram Init Data")
    
    result = verify_driver_login(req.truck_number, req.password)
    if not result:
        return {"error": "Invalid truck number or password"}
    
    user = get_user_from_init_data(req.init_data)
    if user:
        tg_id = user.get("id")
        save_mini_app_user(tg_id, "", req.truck_number)
    return {"success": True, "truck_number": req.truck_number}

# ---------- Admin Endpoints ----------

@app.get("/api/bot-groups", dependencies=[Depends(verify_admin_auth)])
def bot_groups_api():
    try:
        groups = get_all_bot_groups()
        return {"groups": [{"chat_id": g[0], "title": g[1]} for g in groups]}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/samsara-vehicles", dependencies=[Depends(verify_admin_auth)])
def samsara_vehicles_api():
    try:
        vehicles = get_all_vehicles()
        result = [
            {
                "id": v.get("id"),
                "name": v.get("name", ""),
                "externalIds": v.get("externalIds", {}),
                "driverId": v.get("driver", {}).get("id") if v.get("driver") else None
            }
            for v in vehicles
        ]
        return {"vehicles": result}
    except Exception as e:
        return {"error": str(e)}

class AssignDriverRequest(BaseModel):
    vehicle_id: str
    truck_number: str
    group_id: int
    password: str
    truck_license: str = ""

@app.post("/api/admin/assign-driver", dependencies=[Depends(verify_admin_auth)])
def admin_assign_driver_api(req: AssignDriverRequest):
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

@app.get("/api/admin/trucks-with-fuel", dependencies=[Depends(verify_admin_auth)])
def admin_trucks_with_fuel():
    """Optimized batch endpoint to prevent N+1 Samsara API requests."""
    try:
        drivers = get_all_drivers()
        vehicle_ids = [d[2] for d in drivers if d[2]]
        
        # Batch query vehicle stats if list is not empty
        stats_map = {}
        if vehicle_ids:
            # Querying stats in a single batched API call
            headers = {"Authorization": f"Bearer {config.SAMSARA_API_TOKEN}"}
            url = f"{config.BASE_URL}/fleet/vehicles/stats"
            params = {"types": "fuelPercents", "vehicleIds": ",".join(vehicle_ids[:50])}
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                for stat in resp.json().get("data", []):
                    v_id = stat.get("id")
                    fuel = stat.get("fuelPercent", {}).get("value")
                    stats_map[v_id] = fuel

        result = []
        for driver in drivers:
            chat_id, driver_id, vehicle_id, truck_number, truck_license = driver
            result.append({
                "chat_id": chat_id,
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "truck_number": truck_number,
                "truck_license": truck_license,
                "fuel_level": stats_map.get(vehicle_id)
            })
        return {"trucks": result}
    except Exception as e:
        return {"error": str(e)}

# ---------- Telematics Features ----------

@app.get("/api/fault-codes")
def fault_codes_api(vehicle_id: Optional[str] = None):
    try:
        raw_faults = get_fault_codes(vehicle_id=vehicle_id, limit=50)
        fault_list = [parse_fault_code(raw) for raw in raw_faults]
        db_faults = get_all_fault_codes()
        return {"fault_codes": fault_list, "db_count": len(db_faults)}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/harsh-events")
def harsh_events_api(vehicle_id: Optional[str] = None):
    try:
        raw_events = get_harsh_events(vehicle_id=vehicle_id, limit=20)
        event_list = [parse_harsh_event(raw) for raw in raw_events]
        db_events = get_all_harsh_events()
        return {"harsh_events": event_list, "db_count": len(db_events)}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/maintenance-alerts")
def maintenance_alerts_api(vehicle_id: Optional[str] = None):
    try:
        raw_alerts = get_maintenance_alerts(vehicle_id=vehicle_id, limit=20)
        alert_list = [parse_maintenance_alert(raw) for raw in raw_alerts]
        db_alerts = get_all_maintenance_alerts()
        return {"maintenance_alerts": alert_list, "db_count": len(db_alerts)}
    except Exception as e:
        return {"error": str(e)}

# ---------- Driver Verification Upload ----------

@app.post("/api/verify")
def verify_api(
    driver_name: str = Form(...),
    truck_number: str = Form(...),
    photo: UploadFile = File(...),
    init_data: str = Form(...)
):
    if not verify_init_data(init_data):
        raise HTTPException(status_code=403, detail="Unauthorized")
    user = get_user_from_init_data(init_data)
    if not user:
        raise HTTPException(status_code=400, detail="No user data found")
    
    tg_id = user.get("id")
    os.makedirs("uploads", exist_ok=True)
    
    # Secure filename creation using UUID
    ext = os.path.splitext(photo.filename)[1]
    safe_filename = f"{tg_id}_{uuid.uuid4().hex}{ext}"
    photo_path = os.path.join("uploads", safe_filename)
    
    with open(photo_path, "wb") as buffer:
        shutil.copyfileobj(photo.file, buffer)
        
    submit_verification(tg_id, driver_name, truck_number, photo_path)
    return {"message": "Verification submitted. Await admin approval."}

# ---------- Driver Details (Fixed DB context & field queries) ----------

@app.get("/api/driver-details/{truck_number}", dependencies=[Depends(verify_admin_auth)])
def driver_details_api(truck_number: str):
    driver = get_driver_by_truck(truck_number)
    if not driver:
        return {"error": "Driver not found"}
    
    driver_id = driver["samsara_driver_id"]
    
    # Safe Database Connection Management
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('SELECT COALESCE(SUM(amount), 0) FROM points WHERE driver_id = %s', (driver_id,))
        balance = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM pti_submissions WHERE driver_id = %s', (driver_id,))
        pti_count = c.fetchone()[0]
    finally:
        conn.close()
        
    return {
        "truck_number": truck_number,
        "driver_id": driver_id,
        "vehicle_id": driver["vehicle_id"],
        "truck_license": driver["truck_license"],
        "chat_id": driver["chat_id"],
        "password": driver["password"],
        "points": balance,
        "pti_count": pti_count
    }
