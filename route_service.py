import time
import requests
import polyline
import openrouteservice
from openrouteservice import convert
import config

ORS_KEY = config.OPENROUTESERVICE_API_KEY
GEOCODE_CACHE = {}

def get_route_geometry(origin_lat, origin_lng, dest_lat, dest_lng):
    if not ORS_KEY:
        return None
    try:
        client = openrouteservice.Client(key=ORS_KEY)
        coords = [(origin_lng, origin_lat), (dest_lng, dest_lat)]
        
        # Switched to heavy goods vehicle (commercial truck) profile
        route = client.directions(coords, profile='driving-hgv', format='geojson')
        geometry = route['features'][0]['geometry']['coordinates']
        points = [(lat, lng) for lng, lat in geometry]
        return points
    except Exception as e:
        print(f"❌ Route calculation error: {e}")
        # Fallback to driving-car if driving-hgv is restricted or fails
        try:
            client = openrouteservice.Client(key=ORS_KEY)
            coords = [(origin_lng, origin_lat), (dest_lng, dest_lat)]
            route = client.directions(coords, profile='driving-car', format='geojson')
            geometry = route['features'][0]['geometry']['coordinates']
            return [(lat, lng) for lng, lat in geometry]
        except Exception as fallback_err:
            print(f"❌ Fallback route calculation error: {fallback_err}")
            return None

def get_station_distance_to_route(station_lat, station_lng, route_points):
    from math import radians, sin, cos, sqrt, atan2
    R = 3959.87433  # Radius of Earth in miles
    min_distance = float('inf')
    
    # Cap sampling step size to avoid skipping critical waypoints on long routes
    step = max(1, len(route_points) // 200)
    
    for i in range(0, len(route_points), step):
        route_lat, route_lng = route_points[i]
        lat1, lng1, lat2, lng2 = radians(station_lat), radians(station_lng), radians(route_lat), radians(route_lng)
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        distance = R * c
        if distance < min_distance:
            min_distance = distance
            
    return min_distance

def find_stations_along_route(origin_lat, origin_lng, dest_lat, dest_lng, stations, buffer_miles=10):
    route_points = get_route_geometry(origin_lat, origin_lng, dest_lat, dest_lng)
    if not route_points:
        return stations
        
    filtered = []
    for station in stations:
        station_lat = station.get("lat")
        station_lng = station.get("lng")
        if station_lat is None or station_lng is None:
            continue
            
        dist_to_route = get_station_distance_to_route(station_lat, station_lng, route_points)
        if dist_to_route <= buffer_miles:
            station["distance_to_route"] = round(dist_to_route, 1)
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
        
        response = requests.get(url, params=params, headers=headers, timeout=5)
        
        if response.status_code == 429:
            # Respect Nominatim rate limiting rule
            time.sleep(1)
            response = requests.get(url, params=params, headers=headers, timeout=5)
            
        response.raise_for_status()
        data = response.json()
        
        if data:
            lat = float(data[0].get("lat", 0))
            lng = float(data[0].get("lon", 0))
            GEOCODE_CACHE[cache_key] = (lat, lng)
            return lat, lng
        else:
            GEOCODE_CACHE[cache_key] = (None, None)
            return None, None
            
    except Exception as e:
        print(f"⚠️ Geocoding failed for '{full_address}': {e}")
        GEOCODE_CACHE[cache_key] = (None, None)
        return None, None
