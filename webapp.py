import os
import json
import hmac
import hashlib
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
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
    set_driver_credentials
)
from samsara_client import find_vehicle_by_truck_number, get_vehicle_stats
from fuel_service import get_comprehensive_fuel_info
from route_service import geocode_address_to_coords
import shutil

app = FastAPI()

# Admin credentials (hardcoded for now)
ADMIN_TRUCK = "1234"
ADMIN_PASSWORD = "Mytrucksafetypassword3223"

# ---- Helper functions ----

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

# ---- Routes ----

@app.get("/", response_class=HTMLResponse)
async def mini_app_page():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/donate", response_class=HTMLResponse)
async def donate_page():
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Donate</title></head>
    <body style="font-family:Arial; text-align:center; padding:20px;">
        <h2>💖 Support the Developer</h2>
        <p>If you find this bot useful, consider donating:</p>
        <p><b>Crypto Wallet:</b> {config.DONATION_WALLET}</p>
    </body>
    </html>
    """

@app.get("/api/user/{telegram_user_id}")
async def get_user(telegram_user_id: int):
    user = get_mini_app_user(telegram_user_id)
    if user:
        return user
    return {"driver_name": "", "truck_number": ""}

# ---- Admin Login API ----

class AdminLoginRequest(BaseModel):
    truck_number: str
    password: str

@app.post("/api/admin-login")
async def admin_login_api(req: AdminLoginRequest):
    if req.truck_number == ADMIN_TRUCK and req.password == ADMIN_PASSWORD:
        return {"success": True, "role": "admin"}
    return {"error": "Invalid admin credentials"}

# ---- Login API ----

class LoginRequest(BaseModel):
    truck_number: str
    password: str
    init_data: str

@app.post("/api/login")
async def login_api(req: LoginRequest):
    print(f"🔍 LOGIN ATTEMPT: truck={req.truck_number}, pass={req.password}")
    
    # TEMPORARY: Skip init_data verification for testing
    # if not verify_init_data(req.init_data):
    #     print("❌ INIT DATA INVALID")
    #     raise HTTPException(status_code=403, detail="Unauthorized")
    
    result = verify_driver_login(req.truck_number, req.password)
    print(f"🔍 LOGIN RESULT: {result}")
    
    if not result:
        return {"error": "Invalid truck number or password"}
    
    user = get_user_from_init_data(req.init_data)
    if user:
        tg_id = user.get("id")
        save_mini_app_user(tg_id, "", req.truck_number)
    
    return {"success": True, "truck_number": req.truck_number}

# ---- Fuel Search API ----

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

# ---- Use Station API ----

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

# ---- PTI API ----

class PTIRequest(BaseModel):
    pti_number: str
    init_data: str

@app.post("/api/submit-pti")
async def submit_pti_api(req: PTIRequest):
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
    add_pti(driver_id, req.pti_number)
    add_points(driver_id, config.POINTS_PER_PTI, "pti", f"PTI {req.pti_number}")
    return {"message": f"PTI submitted! You earned {config.POINTS_PER_PTI} points."}

# ---- Points API ----

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

# ---- Verification API ----

@app.post("/api/verify")
async def verify_api(driver_name: str = Form(...), truck_number: str = Form(...), photo: UploadFile = File(...), init_data: str = Form(...)):
    if not verify_init_data(init_data):
        raise HTTPException(status_code=403, detail="Unauthorized")
    user = get_user_from_init_data(init_data)
    if not user:
        raise HTTPException(status_code=400, detail="No user")
    tg_id = user.get("id")
    os.makedirs("uploads", exist_ok=True)
    photo_path = f"uploads/{tg_id}_{photo.filename}"
    with open(photo_path, "wb") as buffer:
        shutil.copyfileobj(photo.file, buffer)
    submit_verification(tg_id, driver_name, truck_number, photo_path)
    return {"message": "Verification submitted. Await admin approval."}

# ---- History API ----

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

# ---- Admin endpoints ----

@app.get("/admin")
async def admin_page():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Admin Dashboard</title></head>
    <body>
        <h2>Admin Dashboard</h2>
        <button onclick="loadData()">Load Data</button>
        <div id="adminData"></div>
        <script>
            async function loadData() {
                const resp = await fetch('/api/admin-summary');
                const data = await resp.json();
                let html = '<h3>Points Summary</h3>';
                data.points.forEach(d => html += `<div>${d.driver_id}: ${d.balance} pts</div>`);
                html += '<h3>PTI Counts</h3>';
                data.pti.forEach(d => html += `<div>${d.driver_id}: ${d.count} PTIs</div>`);
                html += '<h3>Fuel Usage (last 10)</h3>';
                data.fuel_usage.forEach(f => html += `<div>${f.driver_id} - ${f.station_name} - ${f.used_at} - Confirmed: ${f.is_confirmed}</div>`);
                document.getElementById('adminData').innerHTML = html;
            }
        </script>
    </body>
    </html>
    """

@app.get("/api/admin-summary")
async def admin_summary_api():
    points = get_all_points_summary()
    pti = get_all_pti_summary()
    fuel = get_all_fuel_usage()
    points_list = [{"driver_id": r[0], "balance": r[1]} for r in points]
    pti_list = [{"driver_id": r[0], "count": r[1]} for r in pti]
    fuel_list = []
    for row in fuel:
        fuel_list.append({"driver_id": row[0], "station_name": row[1], "used_at": row[2], "is_confirmed": row[3]})
    return {"points": points_list, "pti": pti_list, "fuel_usage": fuel_list}
