import requests
import config

SAMSARA_API_TOKEN = config.SAMSARA_API_TOKEN
BASE_URL = "https://api.samsara.com"

def get_vehicle_stats(vehicle_id):
    """Fetch stats including fuel, gps, heading."""
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

# NEW: Get all vehicles from Samsara
def get_all_vehicles():
    """Fetch all vehicles from Samsara, paginating through results."""
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
    """Search vehicles by name or external ID for the truck number."""
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
    """Fetch the driver assigned to a vehicle from Samsara."""
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
