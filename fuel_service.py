import json
import html
import requests
import time
from datetime import datetime
from database import get_truck_stops_near
from route_service import geocode_address_to_coords
import config

ALLOWED_BRANDS = [
    "love's", "loves", "pilot", "flying j", "flying-j", "flyingj",
    "ta", "travelcenters of america", "travel centers of america",
    "petro", "travel america", "t/a", "speedco"
]

MAX_DISTANCE_MILES = 200
BUFFER_MILES = 20

def haversine(lat1, lng1, lat2, lng2):
    from math import radians, sin, cos, sqrt, atan2
    R = 3959.87433
    lat1, lng1, lat2, lng2 = radians(lat1), radians(lng1), radians(lat2), radians(lng2)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def point_to_line_distance(px, py, x1, y1, x2, y2):
    if x1 == x2 and y1 == y2:
        return haversine(px, py, x1, y1)
    num = abs((x2 - x1) * (y1 - py) - (x1 - px) * (y2 - y1))
    den = ((x2 - x1)**2 + (y2 - y1)**2)**0.5
    if den == 0:
        return haversine(px, py, x1, y1)
    dist_deg = num / den
    return dist_deg * 69.0

def get_fuel_prices_from_apify(lat, lng, radius_km=50, fuel_type=4):
    if not config.APIFY_API_TOKEN:
        return None
    url = "https://api.apify.com/v2/acts/johnvc~fuelprices/runs"
    params = {"token": config.APIFY_API_TOKEN}
    payload = {"search": f"{lat},{lng}", "fuel": fuel_type, "limit": 100, "radius": radius_km}
    try:
        resp = requests.post(url, params=params, json=payload, timeout=30)
        resp.raise_for_status()
        run_data = resp.json()
        run_id = run_data.get("data", {}).get("id")
        if not run_id:
            return None
        status_url = f"https://api.apify.com/v2/acts/runs/{run_id}"
        for _ in range(20):
            time.sleep(3)
            s_resp = requests.get(status_url, params={"token": config.APIFY_API_TOKEN})
            s_resp.raise_for_status()
            status = s_resp.json().get("data", {}).get("status")
            if status == "SUCCEEDED":
                dataset_url = f"https://api.apify.com/v2/datasets/{run_id}/items"
                items_resp = requests.get(dataset_url, params={"token": config.APIFY_API_TOKEN})
                items_resp.raise_for_status()
                items = items_resp.json()
                stations = []
                for item in items:
                    stations.append({
                        "name": item.get("name", ""),
                        "brand": item.get("brand", ""),
                        "address": item.get("address", ""),
                        "city": item.get("city", ""),
                        "state": item.get("state", ""),
                        "lat": item.get("lat"),
                        "lng": item.get("lng"),
                        "price": item.get("price", 0.0),
                        "distance": item.get("distance", 0.0)
                    })
                return stations
            elif status in ["FAILED", "TIMED-OUT", "ABORTED"]:
                return None
        return None
    except Exception as e:
        print(f"❌ Apify error: {e}")
        return None

def get_comprehensive_fuel_info(lat, lng, fuel_type=4, route_mode=False, dest_lat=None, dest_lng=None):
    result = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"), "station_list": [], "errors": []}
    apify_stations = get_fuel_prices_from_apify(lat, lng, radius_km=80, fuel_type=fuel_type)
    if apify_stations:
        stations = []
        for s in apify_stations:
            if s["lat"] is None or s["lng"] is None:
                continue
            d = haversine(lat, lng, s["lat"], s["lng"])
            if route_mode and dest_lat and dest_lng:
                dist_to_line = point_to_line_distance(s["lat"], s["lng"], lat, lng, dest_lat, dest_lng)
                if dist_to_line > BUFFER_MILES:
                    continue
            if d > MAX_DISTANCE_MILES:
                continue
            s["distance_from_truck"] = round(d, 1)
            s["maps_link"] = f"https://www.google.com/maps?q={s['lat']},{s['lng']}"
            stations.append(s)
        stations.sort(key=lambda x: (x.get("price") is None, x.get("price", 999999), x.get("distance_from_truck")))
        result["station_list"] = stations[:10]
        return result

    # Fallback to local DB
    stops = get_truck_stops_near(lat, lng, radius_miles=MAX_DISTANCE_MILES, brands=ALLOWED_BRANDS)
    if not stops:
        result["errors"].append("No truck stops found in local DB.")
        return result
    stations = []
    for s in stops:
        d = haversine(lat, lng, s["lat"], s["lng"])
        if route_mode and dest_lat and dest_lng:
            dist_to_line = point_to_line_distance(s["lat"], s["lng"], lat, lng, dest_lat, dest_lng)
            if dist_to_line > BUFFER_MILES:
                continue
        if d > MAX_DISTANCE_MILES:
            continue
        s["distance_from_truck"] = round(d, 1)
        s["maps_link"] = f"https://www.google.com/maps?q={s['lat']},{s['lng']}"
        stations.append(s)
    stations.sort(key=lambda x: (x.get("price") is None, x.get("price", 999999), x.get("distance_from_truck")))
    result["station_list"] = stations[:10]
    return result

def format_fuel_report_with_map(fuel_info, fuel_level, range_miles, driver_id, vehicle_id):
    stations = fuel_info.get("station_list", [])
    dest = fuel_info.get("destination_address", "Unknown")
    lines = [f"⛽ **FUEL ALERT**", f"━━━━━━━━━━━━━━━━", f"Fuel: **{fuel_level}%**",
             f"Range: **{int(range_miles)} miles**", f"Destination: **{dest}**",
             f"━━━━━━━━━━━━━━━━", f"Found **{len(stations)}** truck stops along your route:", ""]
    if not stations:
        lines.append("❌ No truck stops found within the corridor.")
        return "\n".join(lines)
    for i, s in enumerate(stations[:5], 1):
        price = s.get("price")
        price_str = f"${price:.2f}" if price else "N/A"
        name = html.escape(s.get("name", "Unknown"))
        addr = html.escape(s.get("address", ""))
        city = html.escape(s.get("city", ""))
        state = html.escape(s.get("state", ""))
        dist = s.get("distance_from_truck", 0)
        map_link = s.get("maps_link", "")
        lines.append(f"<b>{i}. {name}</b>")
        lines.append(f"   💰 Price: <b>{price_str}</b>")
        if addr or city or state:
            lines.append(f"   📍 {addr}, {city} {state}".strip())
        lines.append(f"   📏 {dist} miles from current location")
        if map_link:
            lines.append(f"   🗺️ <a href='{map_link}'>Open in Google Maps</a>")
        lines.append("")
    return "\n".join(lines)