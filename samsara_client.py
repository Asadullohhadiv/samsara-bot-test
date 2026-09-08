import requests
from datetime import datetime, timedelta
import config

SAMSARA_API_TOKEN = config.SAMSARA_API_TOKEN
BASE_URL = "https://api.samsara.com"

# ==================== VEHICLE STATS (fuel, GPS, location) ====================

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
    stats = get_vehicle_stats(vehicle_id)
    return stats.get("fuel") if stats else None

def get_vehicle_location(vehicle_id):
    stats = get_vehicle_stats(vehicle_id)
    if stats and "lat" in stats:
        return {"latitude": stats["lat"], "longitude": stats["lng"], "heading": stats.get("heading")}
    return None

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

# ==================== CONFIRMED WORKING ENDPOINTS ====================

def get_fault_codes(vehicle_id=None, limit=50):
    """
    Fetch fault codes (DTCs) from Samsara.
    Confirmed endpoint: /fleet/vehicles/stats?types=faultCodes&vehicleIds=...
    """
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/vehicles/stats"
    params = {
        "types": "faultCodes",
        "decorations": "vehicle",
        "limit": limit
    }
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

def get_harsh_events(vehicle_id=None, limit=20):
    """
    Fetch safety events (harsh events) with video links.
    Confirmed endpoint: /fleet/safety-events?limit=5&startTime=...&endTime=...
    """
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/fleet/safety-events"
    now = datetime.utcnow()
    start_time = (now - timedelta(days=7)).isoformat() + "Z"
    end_time = now.isoformat() + "Z"
    params = {
        "limit": limit,
        "startTime": start_time,
        "endTime": end_time
    }
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

def get_maintenance_alerts(vehicle_id=None, limit=20):
    """
    Fetch upcoming preventive maintenance schedules.
    Confirmed endpoint: /maintenance/preventive/upcoming?limit=5
    """
    headers = {"Authorization": f"Bearer {SAMSARA_API_TOKEN}"}
    url = f"{BASE_URL}/maintenance/preventive/upcoming"
    params = {"limit": limit}
    if vehicle_id:
        params["assetIds"] = vehicle_id  # Note: parameter is assetIds, not vehicleIds
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', [])
    except Exception as e:
        print(f"❌ Error fetching maintenance alerts: {e}")
        return []

# ==================== PARSERS (convert raw Samsara data to clean dicts) ====================

def parse_fault_code(raw_vehicle_stat):
    """
    raw_vehicle_stat is one item from /fleet/vehicles/stats?types=faultCodes
    Returns a list of fault codes for that vehicle.
    """
    vehicle_id = raw_vehicle_stat.get("id", "")
    name = raw_vehicle_stat.get("name", "")
    fault_codes = raw_vehicle_stat.get("faultCodes", {})
    dtcs = []
    j1939 = fault_codes.get("j1939", {})
    # Get diagnosticTroubleCodes from j1939
    for dtc in j1939.get("diagnosticTroubleCodes", []):
        dtcs.append({
            "vehicle_id": vehicle_id,
            "truck_number": name,
            "code": f"SPN{dtc.get('spnId')}-FMI{dtc.get('fmiId')}",
            "description": dtc.get("spnDescription", ""),
            "severity": "unknown",  # could derive from checkEngineLights
            "recorded_at": fault_codes.get("time", "")
        })
    return dtcs

def parse_harsh_event(raw_event):
    """
    raw_event is one item from /fleet/safety-events
    """
    vehicle = raw_event.get("vehicle", {})
    return {
        "event_type": raw_event.get("eventType", "") or raw_event.get("type", ""),
        "location": raw_event.get("location", "") or "",
        "video_url": raw_event.get("downloadForwardVideoUrl", "") or "",
        "vehicle_id": vehicle.get("id", ""),
        "truck_number": vehicle.get("name", ""),
        "happened_at": raw_event.get("time", "")
    }

def parse_maintenance_alert(raw_alert):
    """
    raw_alert is one item from /maintenance/preventive/upcoming
    """
    asset = raw_alert.get("asset", {})
    return {
        "vehicle_id": asset.get("id", ""),
        "truck_number": asset.get("name", ""),
        "maintenance_type": "Scheduled Maintenance",
        "due_mileage": raw_alert.get("dueInOdometer", 0),
        "location": "",
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
