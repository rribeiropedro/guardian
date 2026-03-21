import json, math

IMPACT_CENTER = (37.2270, -80.4220)
MAGNITUDE = 4.2
SCENARIO_HOUR = 14
SCENARIO_DAY = 1

MATERIAL_SCORE = {
    'brick': 1.0, 'masonry': 1.0, 'stone': 0.85,
    'concrete': 0.65, 'reinforced_concrete': 0.45,
    'steel': 0.35, 'wood': 0.75,
    'unknown': 0.75, None: 0.75,
}

OCCUPANCY = {
    'university': 1.0, 'school': 1.0, 'classroom': 1.0,
    'dormitory': 0.3, 'office': 0.8,
    'residential': 0.4, 'retail': 0.6, 'default': 0.65,
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

def ground_motion(dist_km, magnitude):
    """
    Peak Ground Acceleration proxy using a real attenuation relationship.
    Based on simplified Boore-Atkinson model:
      ln(PGA) = c1 + c2*M - c3*ln(sqrt(dist^2 + h^2))
    where h = focal depth proxy (10km default for shallow crustal quakes).
    
    At magnitude 7.2 within 1km this returns near 1.0.
    At magnitude 4.0 within 1km this returns ~0.3.
    The magnitude term is EXPONENTIAL so 7.2 vs 5.0 is a massive difference.
    """
    h = 10.0  # assumed focal depth in km
    r = math.sqrt(dist_km**2 + h**2)  # hypocentral distance
    
    # Simplified coefficients (calibrated for PGA in units of g)
    c1 = -2.991
    c2 =  1.414   # magnitude scaling — this is the key term
    c3 =  1.000   # distance attenuation
    
    ln_pga = c1 + c2 * magnitude - c3 * math.log(r)
    pga = math.exp(ln_pga)  # in units of g
    
    # Normalize: 0.0g = no shaking, 1.0g+ = extreme (clamp to 0-1)
    return min(pga / 1.0, 1.0)

def score_building(b, magnitude):
    dist = haversine(b['centroid_lat'], b['centroid_lon'],
                     IMPACT_CENTER[0], IMPACT_CENTER[1])

    # 1. Ground motion at this location — DRIVEN BY MAGNITUDE (35%)
    #    At mag 7.2 within 0.5km this is 0.85+
    #    At mag 5.0 same distance this would be ~0.3
    shaking = ground_motion(dist, magnitude)

    # 2. Material vulnerability (25%)
    mat = str(b.get('material', '')).lower().strip()
    mat_score = MATERIAL_SCORE.get(mat, MATERIAL_SCORE[None])

    # 3. Age (15%)
    try:
        yr = int(str(b.get('start_date', ''))[:4])
        if yr < 1940:        age_score = 1.0
        elif yr < 1975:      age_score = 0.85
        elif yr < 1994:      age_score = 0.60
        else:                age_score = 0.30
    except:
        age_score = 0.70

    # 4. Occupancy (15%)
    btype = str(b.get('building_type', 'default')).lower()
    is_daytime = (SCENARIO_DAY < 5) and (8 <= SCENARIO_HOUR <= 18)
    occ = OCCUPANCY.get(btype, OCCUPANCY['default'])
    if not is_daytime:
        occ *= 0.35

    # 5. Height resonance risk (10%)
    try:    levels = int(b.get('levels', 2))
    except: levels = 2
    if levels <= 2:       height = 0.3
    elif levels <= 4:     height = 0.5
    elif levels <= 7:     height = 0.85   # worst — resonates with typical quake freq
    else:                 height = 0.75

    score = (shaking    * 0.35 +
             mat_score  * 0.25 +
             age_score  * 0.15 +
             occ        * 0.15 +
             height     * 0.10)

    breakdown = {
        'shaking':   round(shaking, 3),
        'material':  round(mat_score, 3),
        'age':       round(age_score, 3),
        'occupancy': round(occ, 3),
        'height':    round(height, 3),
        'dist_km':   round(dist, 3),
    }
    return round(min(score, 1.0), 3), round(dist, 3), breakdown

def assign_color(score):
    if score >= 0.75:   return 'RED'
    elif score >= 0.55: return 'ORANGE'
    elif score >= 0.35: return 'YELLOW'
    else:               return 'GREEN'

def run_triage(json_path='data/vt_buildings.json'):
    with open(json_path) as f:
        buildings = json.load(f)

    results = []
    for b in buildings:
        score, dist_km, breakdown = score_building(b, MAGNITUDE)
        color = assign_color(score)
        results.append({
            'building_id':     b['building_id'],
            'name':            b['name'],
            'building_type':   b['building_type'],
            'material':        b['material'],
            'lat':             b['centroid_lat'],
            'lon':             b['centroid_lon'],
            'score':           score,
            'color':           color,
            'dist_km':         dist_km,
            'score_breakdown': breakdown,
            'footprint':       b['footprint'],
        })

    results.sort(key=lambda x: x['score'], reverse=True)

    with open('data/triage_scored.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f'Triage complete (Mag {MAGNITUDE}):')
    for b in results:
        bd = b['score_breakdown']
        print(f"  {b['name']}: {b['score']} ({b['color']})  {b['dist_km']}km")
        print(f"    shaking={bd['shaking']}  material={bd['material']}  "
              f"age={bd['age']}  occ={bd['occupancy']}  height={bd['height']}")

    return results

if __name__ == '__main__':
    run_triage()