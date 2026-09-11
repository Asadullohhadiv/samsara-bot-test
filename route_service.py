import time
import math
import requests
import openrouteservice
import config

ORS_KEY = config.OPENROUTESERVICE_API_KEY
GEOCODE_CACHE = {}
ROUTE_CACHE = {}


def _route_cache_key(origin_lat, origin_lng, dest_lat, dest_lng):
    return tuple(round(float(x), 4) for x in (origin_lat, origin_lng, dest_lat, dest_lng))


def get_route_details(origin_lat, origin_lng, dest_lat, dest_lng):
    """Return HGV route geometry and total route distance in miles."""
    if not ORS_KEY:
        return None
    key = _route_cache_key(origin_lat, origin_lng, dest_lat, dest_lng)
    if key in ROUTE_CACHE:
        return ROUTE_CACHE[key]

    coords = [(origin_lng, origin_lat), (dest_lng, dest_lat)]
    try:
        client = openrouteservice.Client(key=ORS_KEY)
        route = client.directions(coords, profile="driving-hgv", format="geojson")
        feature = route["features"][0]
        geometry = feature["geometry"]["coordinates"]
        summary = feature.get("properties", {}).get("summary", {})
        result = {
            "points": [(lat, lng) for lng, lat in geometry],
            "distance_miles": float(summary.get("distance", 0)) / 1609.344,
            "duration_seconds": float(summary.get("duration", 0)),
            "profile": "driving-hgv",
        }
        ROUTE_CACHE[key] = result
        return result
    except Exception as exc:
        print(f"HGV route error: {exc}")
        try:
            client = openrouteservice.Client(key=ORS_KEY)
            route = client.directions(coords, profile="driving-car", format="geojson")
            feature = route["features"][0]
            geometry = feature["geometry"]["coordinates"]
            summary = feature.get("properties", {}).get("summary", {})
            result = {
                "points": [(lat, lng) for lng, lat in geometry],
                "distance_miles": float(summary.get("distance", 0)) / 1609.344,
                "duration_seconds": float(summary.get("duration", 0)),
                "profile": "driving-car-fallback",
            }
            ROUTE_CACHE[key] = result
            return result
        except Exception as fallback_exc:
            print(f"Fallback route error: {fallback_exc}")
            return None


def get_route_geometry(origin_lat, origin_lng, dest_lat, dest_lng):
    route = get_route_details(origin_lat, origin_lng, dest_lat, dest_lng)
    return route["points"] if route else None


def haversine_miles(lat1, lng1, lat2, lng2):
    r = 3959.87433
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _project_to_segment(px, py, ax, ay, bx, by):
    # Equirectangular projection is accurate enough over a single short route
    # segment and avoids expensive GIS dependencies in the API process.
    lat0 = math.radians((py + ay + by) / 3.0)
    scale_x = math.cos(lat0)
    ax2, bx2 = ax * scale_x, bx * scale_x
    px2 = px * scale_x
    dx, dy = bx2 - ax2, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else ((px2 - ax2) * dx + (py - ay) * dy) / denom
    t = max(0.0, min(1.0, t))
    qx = ax + (bx - ax) * t
    qy = ay + (by - ay) * t
    return qy, qx, t


def project_point_to_route(point_lat, point_lng, route_points):
    if not route_points:
        return None
    best = None
    cumulative = 0.0
    for i in range(len(route_points) - 1):
        a_lat, a_lng = route_points[i]
        b_lat, b_lng = route_points[i + 1]
        q_lat, q_lng, t = _project_to_segment(point_lng, point_lat, a_lng, a_lat, b_lng, b_lat)
        segment = haversine_miles(a_lat, a_lng, b_lat, b_lng)
        before = cumulative
        along = before + segment * t
        distance = haversine_miles(point_lat, point_lng, q_lat, q_lng)
        if best is None or distance < best["distance_to_route_miles"]:
            best = {
                "distance_to_route_miles": distance,
                "distance_along_route_miles": along,
                "route_index": i,
            }
        cumulative += segment
    if len(route_points) == 1:
        best = {"distance_to_route_miles": haversine_miles(point_lat, point_lng, *route_points[0]),
                "distance_along_route_miles": 0.0, "route_index": 0}
    return best


def get_station_distance_to_route(station_lat, station_lng, route_points):
    projection = project_point_to_route(station_lat, station_lng, route_points)
    return projection["distance_to_route_miles"] if projection else float("inf")


def find_stations_along_route(origin_lat, origin_lng, dest_lat, dest_lng, stations, buffer_miles=10):
    route = get_route_details(origin_lat, origin_lng, dest_lat, dest_lng)
    if not route:
        return stations
    filtered = []
    for station in stations:
        lat, lng = station.get("lat"), station.get("lng")
        if lat is None or lng is None:
            continue
        projection = project_point_to_route(lat, lng, route["points"])
        if projection and projection["distance_to_route_miles"] <= buffer_miles:
            station = dict(station)
            station["distance_to_route"] = round(projection["distance_to_route_miles"], 1)
            station["miles_ahead"] = round(projection["distance_along_route_miles"], 1)
            station["detour_miles_est"] = round(projection["distance_to_route_miles"] * 2, 1)
            filtered.append(station)
    return filtered


def geocode_address_to_coords(street, city="", state="", zipcode=""):
    if city or state:
        full_address = f"{street}, {city}, {state} {zipcode}".strip()
    else:
        full_address = street.strip()
    if not full_address or full_address == "N/A, , ":
        return None, None
    cache_key = full_address.replace(" ", "_").lower()
    if cache_key in GEOCODE_CACHE:
        return GEOCODE_CACHE[cache_key]
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": full_address, "format": "json", "limit": 1}
        headers = {"User-Agent": "FleetDispatchBot/1.0 (fleet-management-system)"}
        response = requests.get(url, params=params, headers=headers, timeout=8)
        if response.status_code == 429:
            time.sleep(1)
            response = requests.get(url, params=params, headers=headers, timeout=8)
        response.raise_for_status()
        data = response.json()
        result = (float(data[0]["lat"]), float(data[0]["lon"])) if data else (None, None)
        GEOCODE_CACHE[cache_key] = result
        return result
    except Exception as exc:
        print(f"Geocoding failed for '{full_address}': {exc}")
        GEOCODE_CACHE[cache_key] = (None, None)
        return None, None
