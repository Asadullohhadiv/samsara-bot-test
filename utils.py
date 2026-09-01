import re

def parse_dispatch_message(text: str) -> dict:
    original = text
    text = text.lower()
    text = re.sub(r'\s+', ' ', text).strip()
    pickup_keywords = ['pickup', 'pu', 'load', 'origin', 'pick']
    delivery_keywords = ['delivery', 'drop', 'destination', 'dest', 'stop',
                         'unload', 'receiver', 'consignee', 'deliver',
                         'dropoff', 'drop off', 'final']
    all_keywords = pickup_keywords + delivery_keywords
    all_keywords.sort(key=len, reverse=True)
    pattern = r'\b(' + '|'.join(re.escape(kw) for kw in all_keywords) + r')\b'
    matches = list(re.finditer(pattern, text))
    if not matches:
        return {'pickups': [original], 'deliveries': [], 'destination': original, 'all_stops': [{'type': 'pickup', 'address': original}]}
    stops = []
    for i, match in enumerate(matches):
        keyword = match.group(0)
        start_pos = match.end()
        end_pos = matches[i+1].start() if i+1 < len(matches) else len(text)
        segment = text[start_pos:end_pos].strip()
        segment = re.sub(r'^[\s,;:\-\&\|]+', '', segment)
        segment = re.sub(r'[\s,;:\-\&\|]+$', '', segment)
        if not segment:
            next_part = text[start_pos:].strip()
            m2 = re.match(r'^([^,;:\-&]+)', next_part)
            if m2:
                segment = m2.group(1).strip()
        if keyword in pickup_keywords:
            stop_type = 'pickup'
        else:
            stop_type = 'delivery'
        stops.append({'type': stop_type, 'address': segment})
    pickups = [s['address'] for s in stops if s['type'] == 'pickup']
    deliveries = [s['address'] for s in stops if s['type'] == 'delivery']
    destination = None
    dest_match = re.search(r'\b(destination|final)\b', text)
    if dest_match:
        pos = dest_match.end()
        remaining = text[pos:].strip()
        next_keyword_match = re.search(r'\b(' + '|'.join(re.escape(kw) for kw in all_keywords) + r')\b', remaining)
        if next_keyword_match:
            dest_addr = remaining[:next_keyword_match.start()].strip()
        else:
            dest_addr = remaining
        destination = dest_addr
    else:
        if deliveries:
            destination = deliveries[-1]
        elif pickups:
            destination = pickups[-1]
        else:
            destination = original
    def clean_addr(addr):
        return re.sub(r'^[\s,;:\-\&]+', '', addr).strip()
    pickups = [clean_addr(a) for a in pickups if a]
    deliveries = [clean_addr(a) for a in deliveries if a]
    destination = clean_addr(destination)
    return {'pickups': pickups, 'deliveries': deliveries, 'destination': destination, 'all_stops': stops}