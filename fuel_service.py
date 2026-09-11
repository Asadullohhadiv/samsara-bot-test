"""Route-aware truck fuel recommendation engine.

The engine deliberately separates:
- candidate discovery (fuel-price provider + local DB),
- route geometry/projection,
- truck fuel feasibility,
- recommendation scoring.

Distances are road-route distances where available and route-detour is an
estimate based on the station's perpendicular distance to the truck route.
For the driver we expose both the raw values and a transparent score.
"""
import html
import math
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import requests

import config
from database import get_truck_stops_near, get_truck_specs
from route_service import get_route_details, project_point_to_route

ALLOWED_BRANDS = [
    "love's", "loves", "pilot", "flying j", "flying-j", "flyingj",
    "ta", "travelcenters of america", "travel centers of america",
    "petro", "travel america", "t/a", "speedco"
]

# Search/ranking defaults. These are intentionally conservative for trucks.
MAX_SEARCH_MILES = 220
ROUTE_BUFFER_MILES = 8
MIN_FUEL_RESERVE_PERCENT = 12
DEFAULT_TANK_GALLONS = 100.0
DEFAULT_MPG = 6.0
MAX_APIFY_ROUTE_QUERIES = 4


def haversine(lat1, lng1, lat2, lng2):
    from math import radians, sin, cos, sqrt, atan2
    r = 3959.87433
    lat1, lng1, lat2, lng2 = radians(lat1), radians(lng1), radians(lat2), radians(lng2)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe_stations(stations: Iterable[dict]) -> List[dict]:
    seen = {}
    for raw in stations:
        lat = _safe_float(raw.get("lat"))
        lng = _safe_float(raw.get("lng"))
        if lat is None or lng is None:
            continue
        key = (round(lat, 5), round(lng, 5))
        current = dict(raw)
        current["lat"], current["lng"] = lat, lng
        if key not in seen:
            seen[key] = current
        else:
            # Prefer the record with a known price and more complete address.
            old = seen[key]
            if old.get("price") in (None, 0, "") and current.get("price") not in (None, 0, ""):
                seen[key] = current
    return list(seen.values())


def get_fuel_prices_from_apify(lat, lng, radius_km=50, fuel_type=4):
    if not config.APIFY_API_TOKEN:
        return []
    url = "https://api.apify.com/v2/acts/johnvc~fuelprices/runs"
    params = {"token": config.APIFY_API_TOKEN}
    payload = {"search": f"{lat},{lng}", "fuel": fuel_type, "limit": 100, "radius": radius_km}
    try:
        resp = requests.post(url, params=params, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        run_id = data.get("id")
        dataset_id = data.get("defaultDatasetId")
        if not run_id or not dataset_id:
            return []

        status_url = f"https://api.apify.com/v2/acts/runs/{run_id}"
        for _ in range(15):
            time.sleep(1)
            status_resp = requests.get(status_url, params={"token": config.APIFY_API_TOKEN}, timeout=8)
            status_resp.raise_for_status()
            status = status_resp.json().get("data", {}).get("status")
            if status == "SUCCEEDED":
                items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
                items_resp = requests.get(items_url, params={"token": config.APIFY_API_TOKEN}, timeout=15)
                items_resp.raise_for_status()
                result = []
                for item in items_resp.json():
                    result.append({
                        "name": item.get("name", ""),
                        "brand": item.get("brand", ""),
                        "address": item.get("address", ""),
                        "city": item.get("city", ""),
                        "state": item.get("state", ""),
                        "lat": item.get("lat"),
                        "lng": item.get("lng"),
                        "price": _safe_float(item.get("price")),
                    })
                return result
            if status in {"FAILED", "TIMED-OUT", "ABORTED"}:
                return []
        return []
    except Exception as exc:
        print(f"Fuel provider error: {exc}")
        return []


def _route_query_points(route_points: List[Tuple[float, float]], max_queries=MAX_APIFY_ROUTE_QUERIES):
    if not route_points:
        return []
    # Query the start plus evenly-spaced points. This avoids calling the fuel
    # provider at every route vertex while covering long-haul routes.
    count = min(max_queries, max(1, math.ceil(len(route_points) / 250)))
    indexes = [round(i * (len(route_points) - 1) / max(1, count - 1)) for i in range(count)] if count > 1 else [0]
    return [route_points[i] for i in sorted(set(indexes))]


def _brand_allowed(station: dict) -> bool:
    brand = str(station.get("brand") or "").strip().lower()
    if not brand:
        return True
    return any(x in brand for x in ALLOWED_BRANDS)


def _candidate_stations(lat, lng, route_points=None, max_search_miles=MAX_SEARCH_MILES, fuel_type=4):
    stations = []
    if route_points:
        for qlat, qlng in _route_query_points(route_points):
            stations.extend(get_fuel_prices_from_apify(qlat, qlng, radius_km=80, fuel_type=fuel_type))
    else:
        stations.extend(get_fuel_prices_from_apify(lat, lng, radius_km=80, fuel_type=fuel_type))

    # Local DB remains valuable as a fast/cheap fallback and can supplement the
    # external provider rather than being used only when Apify fails.
    try:
        stations.extend(get_truck_stops_near(lat, lng, radius_miles=max_search_miles, brands=None))
    except Exception as exc:
        print(f"Local fuel DB error: {exc}")

    deduped = []
    for s in _dedupe_stations(stations):
        if not _brand_allowed(s):
            continue
        d = haversine(lat, lng, s["lat"], s["lng"])
        if d <= max_search_miles:
            s["distance_from_truck"] = round(d, 1)
            deduped.append(s)
    return deduped


def _score_station(station: dict, fuel_level: Optional[float], tank: float, mpg: float, route_distance: Optional[float]):
    price = _safe_float(station.get("price"))
    miles_ahead = station.get("miles_ahead")
    detour = station.get("detour_miles_est")

    # A station behind the truck is not useful for a normal forward fuel stop.
    if miles_ahead is not None and miles_ahead < -2:
        return -9999

    # Fuel feasibility: use current fuel level and a conservative reserve.
    fuel_pct = max(0.0, min(100.0, _safe_float(fuel_level, 50.0)))
    gallons_left = tank * fuel_pct / 100.0
    usable_gallons = max(0.0, gallons_left - tank * MIN_FUEL_RESERVE_PERCENT / 100.0)
    safe_range = usable_gallons * max(mpg, 1.0)
    risk = 0.0
    if miles_ahead is not None:
        if miles_ahead > safe_range:
            risk += 120.0 + min(80.0, miles_ahead - safe_range)
        elif miles_ahead > safe_range * 0.85:
            risk += 25.0

    # Lower is better. Normalize price around a plausible diesel range.
    price_component = 0.0 if price is None or price <= 0 else min(60.0, max(0.0, (price - 3.00) * 35.0))
    distance_component = min(70.0, max(0.0, (miles_ahead or station.get("distance_from_truck", 0)) / 4.0))
    detour_component = min(80.0, max(0.0, (detour or 0) * 10.0))

    # Prefer stations that are ahead, close to route and cheap, while heavily
    # penalizing a station that risks running below the reserve.
    score = 200.0 - price_component - distance_component - detour_component - risk
    if route_distance and route_distance > 0 and miles_ahead is not None:
        # A small preference for a refueling window rather than an immediate stop.
        if 30 <= miles_ahead <= 120:
            score += 18
    if price is not None and price > 0:
        score += 12
    return round(score, 2)


def recommend_fuel_stations(origin_lat, origin_lng, dest_lat, dest_lng, fuel_level=None,
                             vehicle_id=None, max_results=10, fuel_type=4):
    route = get_route_details(origin_lat, origin_lng, dest_lat, dest_lng)
    if not route or not route.get("points"):
        # No route: return nearby candidates with a transparent fallback ranking.
        candidates = _candidate_stations(origin_lat, origin_lng, None, MAX_SEARCH_MILES, fuel_type)
        for s in candidates:
            s["miles_ahead"] = s.get("distance_from_truck")
            s["distance_to_route"] = None
            s["detour_miles_est"] = None
        candidates.sort(key=lambda s: (s.get("price") is None, s.get("price", 999), s.get("distance_from_truck", 999)))
        return {"route": None, "stations": candidates[:max_results], "algorithm": "nearby-fallback"}

    specs = get_truck_specs(vehicle_id) if vehicle_id else {"tank": DEFAULT_TANK_GALLONS, "mpg": DEFAULT_MPG}
    tank = _safe_float(specs.get("tank"), DEFAULT_TANK_GALLONS)
    mpg = _safe_float(specs.get("mpg"), DEFAULT_MPG)
    route_points = route["points"]
    candidates = _candidate_stations(origin_lat, origin_lng, route_points, MAX_SEARCH_MILES, fuel_type)

    ranked = []
    for station in candidates:
        projection = project_point_to_route(station["lat"], station["lng"], route_points)
        if not projection:
            continue
        station["distance_to_route"] = round(projection["distance_to_route_miles"], 1)
        station["miles_ahead"] = round(projection["distance_along_route_miles"], 1)
        station["detour_miles_est"] = round(projection["distance_to_route_miles"] * 2.0, 1)
        if station["distance_to_route"] > ROUTE_BUFFER_MILES:
            continue
        station["score"] = _score_station(station, fuel_level, tank, mpg, route["distance_miles"])
        station["maps_link"] = f"https://www.google.com/maps?q={station['lat']},{station['lng']}"
        station["recommendation_reason"] = _reason_for_station(station, fuel_level, tank, mpg)
        ranked.append(station)

    ranked.sort(key=lambda s: (-s["score"], s.get("price") is None, s.get("price", 999), s.get("miles_ahead", 999)))
    return {
        "route": route,
        "stations": ranked[:max_results],
        "algorithm": "hgv-route-fuel-score-v1",
        "truck": {"fuel_level": fuel_level, "tank_gallons": tank, "avg_mpg": mpg,
                   "reserve_percent": MIN_FUEL_RESERVE_PERCENT},
    }


def _reason_for_station(station, fuel_level, tank, mpg):
    parts = []
    if station.get("price"):
        parts.append(f"${station['price']:.2f}/gal")
    if station.get("miles_ahead") is not None:
        parts.append(f"{station['miles_ahead']:.0f} mi ahead")
    if station.get("detour_miles_est") is not None:
        parts.append(f"~{station['detour_miles_est']:.1f} mi detour")
    if station.get("miles_ahead") is not None and fuel_level is not None:
        safe_range = max(0.0, (tank * max(0.0, fuel_level - MIN_FUEL_RESERVE_PERCENT) / 100.0) * mpg)
        if station["miles_ahead"] <= safe_range:
            parts.append("within safe fuel range")
    return " · ".join(parts)


def get_comprehensive_fuel_info(lat, lng, fuel_type=4, route_mode=False, dest_lat=None, dest_lng=None,
                                fuel_level=None, vehicle_id=None):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if route_mode and dest_lat is not None and dest_lng is not None:
        result = recommend_fuel_stations(lat, lng, dest_lat, dest_lng, fuel_level=fuel_level,
                                         vehicle_id=vehicle_id)
    else:
        candidates = _candidate_stations(lat, lng, None, MAX_SEARCH_MILES, fuel_type)
        for s in candidates:
            s["miles_ahead"] = s.get("distance_from_truck")
            s["maps_link"] = f"https://www.google.com/maps?q={s['lat']},{s['lng']}"
        candidates.sort(key=lambda s: (s.get("price") is None, s.get("price", 999), s.get("distance_from_truck", 999)))
        result = {"stations": candidates[:10], "route": None, "algorithm": "nearby-fallback"}
    return {"timestamp": now, "station_list": result.get("stations", []), "route": result.get("route"),
            "algorithm": result.get("algorithm"), "truck": result.get("truck", {})}


def format_fuel_report_with_map(fuel_info, fuel_level, range_miles, driver_id, vehicle_id):
    stations = fuel_info.get("station_list", [])
    lines = [
        "⛽ <b>FUEL RECOMMENDATIONS</b>",
        "━━━━━━━━━━━━━━━━",
        f"Fuel: <b>{fuel_level}%</b>",
        f"Estimated range: <b>{int(range_miles)} miles</b>",
        f"Algorithm: <b>{html.escape(fuel_info.get('algorithm', 'fallback'))}</b>",
        "━━━━━━━━━━━━━━━━",
    ]
    if not stations:
        lines.append("❌ No suitable truck stops found.")
        return "\n".join(lines)
    for i, s in enumerate(stations[:5], 1):
        price = _safe_float(s.get("price"))
        price_str = f"${price:.2f}/gal" if price else "Price N/A"
        name = html.escape(s.get("name") or s.get("brand") or "Truck stop")
        miles = s.get("miles_ahead", s.get("distance_from_truck", 0))
        detour = s.get("detour_miles_est")
        lines.append(f"<b>{i}. {name}</b>")
        lines.append(f"   💰 {price_str} · 📏 {miles:.0f} mi ahead")
        if detour is not None:
            lines.append(f"   ↪️ ~{detour:.1f} mi detour")
        reason = html.escape(s.get("recommendation_reason", ""))
        if reason:
            lines.append(f"   ⭐ {reason}")
        if s.get("maps_link"):
            lines.append(f"   🗺️ <a href='{s['maps_link']}'>Open in Google Maps</a>")
        lines.append("")
    return "\n".join(lines)
