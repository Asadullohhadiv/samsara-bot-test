import requests
from datetime import datetime, timedelta
import config

SAMSARA_API_TOKEN = config.SAMSARA_API_TOKEN
BASE_URL = "https://api.samsara.com"

# ==================== VEHICLE STATS ====================

def get_vehicle_stats(vehicle_id):
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/vehicles/stats"
    params = {"types": "fuelPercents,gps", "vehicleIds": vehicle_id}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("data") and len(data["data"]) > 0:
            vehicle_stat = data["data"][0]
            result = {}
            if "fuelPercent" in vehicle_stat:
                result["fuel"] = vehicle_stat["fuelPercent"].get("value")
            if "gps" in vehicle_stat:
                gps = vehicle_stat["gps"]
                result["lat"] = gps.get("latitude")
                result["lng"] = gps.get("longitude")
                result["heading"] = gps.get("heading")
            return result
        return None
    except Exception as e:
        print(f"❌ Samsara API error (stats): {e}")
        return None

def get_vehicle_fuel_level(vehicle_id):
    """Return fuel percentage for a vehicle, or None if unavailable."""
    stats = get_vehicle_stats(vehicle_id)
    return stats.get("fuel") if stats else None

def get_vehicle_location(vehicle_id):
    stats = get_vehicle_stats(vehicle_id)
    if stats and "lat" in stats:
        return {"latitude": stats["lat"], "longitude": stats["lng"], "heading": stats.get("heading")}
    return None

# ==================== VEHICLES ====================

def get_all_vehicles():
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/vehicles"
    params = {"limit": 100}
    all_vehicles = []
    while True:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            all_vehicles.extend(data.get('data', []))
            if not data.get('pagination', {}).get('hasNextPage'):
                break
            params['startingAfter'] = data['pagination']['endCursor']
        except Exception as e:
            print(f"❌ Error fetching vehicles: {e}")
            break
    return all_vehicles

def find_vehicle_by_truck_number(truck_number):
    vehicles = get_all_vehicles()
    for vehicle in vehicles:
        name = vehicle.get('name', '')
        external_ids = vehicle.get('externalIds', {})
        v_id = vehicle.get('id')
        if truck_number in name:
            return v_id
        for key, value in external_ids.items():
            if truck_number in str(value):
                return v_id
    return None

def get_driver_for_vehicle(vehicle_id):
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/vehicles/{vehicle_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        driver = data.get('data', {}).get('driver')
        if driver:
            return driver.get('id')
        return None
    except Exception as e:
        print(f"❌ Error fetching driver: {e}")
        return None

# ==================== FAULT CODES ====================

def get_fault_codes(vehicle_id=None, limit=50):
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/vehicles/diagnostics"
    params = {"limit": limit, "types": "dtcInfo"}  # Required to fetch DTCs
    if vehicle_id:
        params["vehicleIds"] = vehicle_id
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', [])
    except Exception as e:
        print(f"❌ Error fetching fault codes: {e}")
        return []

# ==================== SAFETY EVENTS (Harsh Events) ====================

def get_harsh_events(vehicle_id=None, limit=20):
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/safety/events"
    now = datetime.utcnow()
    start_time = (now - timedelta(days=7)).isoformat() + "Z"
    end_time = now.isoformat() + "Z"
    params = {"limit": limit, "startTime": start_time, "endTime": end_time}
    if vehicle_id:
        params["vehicleIds"] = vehicle_id
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', [])
    except Exception as e:
        print(f"❌ Error fetching safety events: {e}")
        return []

# ==================== MAINTENANCE ====================

def get_maintenance_alerts(vehicle_id=None, limit=20):
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/maintenance/service-schedules"
    params = {"limit": limit}
    if vehicle_id:
        params["vehicleIds"] = vehicle_id
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', [])
    except Exception as e:
        print(f"❌ Error fetching maintenance alerts: {e}")
        return []

# ==================== PARSERS ====================

def parse_fault_code(raw_fault):
    return {
        "code": raw_fault.get("code", ""),
        "description": raw_fault.get("description", "") or raw_fault.get("message", ""),
        "severity": raw_fault.get("severity", "unknown"),
        "vehicle_id": raw_fault.get("vehicleId", ""),
        "recorded_at": raw_fault.get("time", "")
    }

def parse_harsh_event(raw_event):
    return {
        "event_type": raw_event.get("type", ""),
        "location": raw_event.get("location", "") or raw_event.get("address", ""),
        "video_url": raw_event.get("videoUrl", "") or raw_event.get("video", ""),
        "vehicle_id": raw_event.get("vehicleId", ""),
        "happened_at": raw_event.get("time", "")
    }

def parse_maintenance_alert(raw_alert):
    return {
        "maintenance_type": raw_alert.get("type", ""),
        "due_mileage": raw_alert.get("dueMiles", 0),
        "location": raw_alert.get("location", "") or "",
        "vehicle_id": raw_alert.get("vehicleId", ""),
        "created_at": raw_alert.get("time", "")
    }

# ==================== FLEET FUEL LEVELS ====================

def get_fleet_fuel_levels():
    """Fetch fuel levels for all vehicles (used by background monitor)."""
    vehicles = get_all_vehicles()
    result = []
    for v in vehicles:
        vid = v.get("id")
        if not vid:
            continue
        stats = get_vehicle_stats(vid)
        if stats and "fuel" in stats:
            result.append({
                "id": vid,
                "name": v.get("name", ""),
                "fuel_percent": stats["fuel"],
                "latitude": stats.get("lat"),
                "longitude": stats.get("lng")
            })
    return result
