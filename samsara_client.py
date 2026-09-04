import requests
import config

SAMSARA_API_TOKEN = config.SAMSARA_API_TOKEN
BASE_URL = "https://api.samsara.com"

# ==================== EXISTING FUNCTIONS ====================

def get_vehicle_stats(vehicle_id):
    """Fetch stats including fuel, gps, heading."""
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/vehicles/stats"
    params = {"types": "fuelPercents,gps", "vehicleIds": str(vehicle_id)}
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
        print(f"❌ Samsara API error: {e}")
        return None

def get_vehicle_fuel_level(vehicle_id):
    stats = get_vehicle_stats(vehicle_id)
    return stats.get("fuel") if stats else None

def get_vehicle_location(vehicle_id):
    stats = get_vehicle_stats(vehicle_id)
    if stats and "lat" in stats:
        return {"latitude": stats["lat"], "longitude": stats["lng"], "heading": stats.get("heading")}
    return None

def get_all_vehicles():
    """Fetch all vehicles from Samsara."""
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
        if str(truck_number) in name:
            return v_id
        for key, value in external_ids.items():
            if str(truck_number) in str(value):
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

# ==================== UPDATED NEW FUNCTIONS ====================

def get_fault_codes(vehicle_id=None, limit=50):
    """Fetch diagnostic fault codes (DTCs) from Samsara."""
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/diagnostics/fault-codes"
    params = {"limit": limit}
    if vehicle_id:
        params["vehicleIds"] = str(vehicle_id)
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get('data', [])
    except Exception as e:
        print(f"❌ Error fetching fault codes: {e}")
        return []

def get_harsh_events(vehicle_id=None, limit=20):
    """Fetch safety events (speeding, harsh braking, dashcam captures)."""
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/safety/events"
    params = {"limit": limit}
    if vehicle_id:
        params["vehicleIds"] = str(vehicle_id)
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get('data', [])
    except Exception as e:
        print(f"❌ Error fetching safety events: {e}")
        return []

def get_maintenance_alerts(vehicle_id=None, limit=20):
    """Fetch service schedules and maintenance alerts."""
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/maintenance/service-schedules"
    params = {"limit": limit}
    if vehicle_id:
        params["vehicleIds"] = str(vehicle_id)
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get('data', [])
    except Exception as e:
        print(f"❌ Error fetching maintenance alerts: {e}")
        return []

# ==================== PARSERS ====================

def parse_fault_code(raw_fault):
    """Convert raw J1939 / OBD-II Samsara fault into a clean dict."""
    j1939 = raw_fault.get("j1939", {})
    obdii = raw_fault.get("obdii", {})
    
    code = j1939.get("spnId") or obdii.get("dtcId") or raw_fault.get("code", "Unknown")
    description = j1939.get("spnDescription") or obdii.get("dtcDescription") or raw_fault.get("description", "")

    return {
        "code": code,
        "description": description,
        "severity": raw_fault.get("severity", "unknown"),
        "vehicle_id": raw_fault.get("vehicle", {}).get("id", raw_fault.get("vehicleId", "")),
        "recorded_at": raw_fault.get("faultCodeTime", raw_fault.get("time", ""))
    }

def parse_harsh_event(raw_event):
    """Convert raw safety event into a clean dict with media extraction."""
    download_url = ""
    media = raw_event.get("downloadMedia", {}) or raw_event.get("media", {})
    if isinstance(media, dict):
        download_url = media.get("downloadUrl", "")

    return {
        "event_type": raw_event.get("behaviorLabels", [{}])[0].get("name", raw_event.get("type", "Safety Event")),
        "location": raw_event.get("location", {}).get("formattedAddress", ""),
        "video_url": download_url,
        "vehicle_id": raw_event.get("vehicle", {}).get("id", raw_event.get("vehicleId", "")),
        "happened_at": raw_event.get("startTime", raw_event.get("time", ""))
    }

def parse_maintenance_alert(raw_alert):
    """Convert raw service schedule into a clean dict."""
    return {
        "maintenance_type": raw_alert.get("name", raw_alert.get("type", "Scheduled Service")),
        "due_mileage": raw_alert.get("dueOdometerMeters", 0) / 1609.34 if raw_alert.get("dueOdometerMeters") else 0,
        "vehicle_id": raw_alert.get("vehicle", {}).get("id", raw_alert.get("vehicleId", "")),
        "created_at": raw_alert.get("updatedAt", "")
    }
