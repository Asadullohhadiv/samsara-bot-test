import re

def parse_dispatch_message(text: str) -> dict:
    original = text.strip()
    text_lower = original.lower()
    text_lower = re.sub(r'\s+', ' ', text_lower).strip()
    
    pickup_keywords = ['pickup', 'pu', 'load', 'origin', 'pick']
    delivery_keywords = ['delivery', 'drop', 'destination', 'dest', 'stop',
                         'unload', 'receiver', 'consignee', 'deliver',
                         'dropoff', 'drop off', 'final']
    
    all_keywords = pickup_keywords + delivery_keywords
    all_keywords.sort(key=len, reverse=True)
    pattern = r'\b(' + '|'.join(re.escape(kw) for kw in all_keywords) + r')\b'
    
    matches = list(re.finditer(pattern, text_lower))
    
    if not matches:
        return {
            'pickups': [original], 
            'deliveries': [], 
            'destination': original, 
            'all_stops': [{'type': 'pickup', 'address': original}]
        }
        
    stops = []
    for i, match in enumerate(matches):
        keyword = match.group(0)
        start_pos = match.end()
        end_pos = matches[i+1].start() if i+1 < len(matches) else len(original)
        
        # Slice from original text to preserve casing for geocoding
        segment = original[start_pos:end_pos].strip()
        
        # Clean leading/trailing punctuation and noise
        segment = re.sub(r'^[\s,;:\-\&\|]+', '', segment)
        segment = re.sub(r'[\s,;:\-\&\|]+$', '', segment)
        
        # Strip common trailing dispatch noise like time formats (@ 08:00, 14:00 hrs)
        segment = re.sub(r'\s+(@|at)?\s*\d{1,2}:\d{2}\s*(AM|PM|am|pm|hrs)?', '', segment, flags=re.IGNORECASE)
        
        if not segment:
            next_part = original[start_pos:].strip()
            m2 = re.match(r'^([^,;:\-&]+)', next_part)
            if m2:
                segment = m2.group(1).strip()
                
        stop_type = 'pickup' if keyword in pickup_keywords else 'delivery'
        stops.append({'type': stop_type, 'address': segment.strip()})
        
    pickups = [s['address'] for s in stops if s['type'] == 'pickup' and s['address']]
    deliveries = [s['address'] for s in stops if s['type'] == 'delivery' and s['address']]
    
    destination = None
    dest_match = re.search(r'\b(destination|final)\b', text_lower)
    
    if dest_match:
        pos = dest_match.end()
        remaining = original[pos:].strip()
        next_keyword_match = re.search(pattern, remaining.lower())
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
        addr = re.sub(r'^[\s,;:\-\&]+', '', addr)
        return re.sub(r'[\s,;:\-\&]+$', '', addr).strip()

    pickups = [clean_addr(a) for a in pickups if a]
    deliveries = [clean_addr(a) for a in deliveries if a]
    destination = clean_addr(destination)

    return {
        'pickups': pickups, 
        'deliveries': deliveries, 
        'destination': destination, 
        'all_stops': stops
    }
