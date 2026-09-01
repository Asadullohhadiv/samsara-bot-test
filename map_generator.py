import requests
import datetime

def generate_route_map(origin_lat, origin_lng, dest_lat, dest_lng, stops):
    all_lats = [origin_lat, dest_lat]
    all_lngs = [origin_lng, dest_lng]
    for s in stops:
        if s.get("lat") and s.get("lng"):
            all_lats.append(s["lat"])
            all_lngs.append(s["lng"])
    if not all_lats:
        return None
    pad = 0.2
    min_lat = min(all_lats) - pad
    max_lat = max(all_lats) + pad
    min_lng = min(all_lngs) - pad
    max_lng = max(all_lngs) + pad
    center_lat = (min_lat + max_lat) / 2
    center_lng = (min_lng + max_lng) / 2
    url = f"https://staticmap.openstreetmap.de/staticmap.php?center={center_lat},{center_lng}&zoom=9&size=800x400&maptype=mapnik"
    markers = []
    markers.append(f"markers=color:green|{origin_lat},{origin_lng}|S")
    markers.append(f"markers=color:red|{dest_lat},{dest_lng}|D")
    for s in stops[:8]:
        if s.get("lat") and s.get("lng"):
            markers.append(f"markers=color:blue|{s['lat']},{s['lng']}")
    if markers:
        url += "&" + "&".join(markers)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        img_path = f"route_map_{int(datetime.datetime.now().timestamp())}.png"
        with open(img_path, 'wb') as f:
            f.write(resp.content)
        return img_path
    except Exception as e:
        print(f"❌ Map error: {e}")
        return None