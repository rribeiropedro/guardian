"""
generate_quads_from_results.py
Only fetches exterior Street View images by standing on the street.
Never uses the building centroid as the camera position.
"""

import json, os, base64, io, time, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
import httpx

load_dotenv()
GMAPS_KEY = os.environ['GOOGLE_MAPS_KEY']

COLORS = {
    'CRITICAL': (220, 38,  38),
    'MODERATE': (234, 88,  12),
    'LOW':      (22,  163, 74),
}

def load_font(size=13, bold=False):
    paths = [
        '/Library/Fonts/Arial Bold.ttf' if bold else '/Library/Fonts/Arial.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold
            else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except: pass
    return ImageFont.load_default()

def offset_point(lat, lon, bearing_deg, distance_m):
    R = 6371000
    d = distance_m / R
    b = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1)*math.cos(d) +
                     math.cos(lat1)*math.sin(d)*math.cos(b))
    lon2 = lon1 + math.atan2(math.sin(b)*math.sin(d)*math.cos(lat1),
                              math.cos(d)-math.sin(lat1)*math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)

def is_outdoor_panorama(meta: dict) -> bool:
    """
    Return True only if this panorama is a real outdoor street-level shot.
    Filters out:
      - Indoor business photos (copyright contains business name patterns)
      - Panoramas with no date (indoor uploads often lack dates)
      - Panoramas explicitly marked as indoor by Google
    """
    if meta.get('status') != 'OK':
        return False

    # Google Street View car shots always have a date
    # Indoor business panoramas often don't, or have unusual copyright
    date = meta.get('date', '')
    if not date:
        return False  # no date = almost certainly an indoor business upload

    copyright_text = meta.get('copyright', '').lower()

    # Google's own imagery = safe. Third-party = likely indoor business
    if 'google' not in copyright_text:
        return False

    return True

def find_exterior_panoramas(lat, lon):
    """
    Stand at 8 directions × 4 distances away from building.
    Only keep panoramas that pass the outdoor filter.
    Returns list of candidate dicts.
    """
    BEARINGS = [0, 45, 90, 135, 180, 225, 270, 315]
    LABELS   = ['N','NE','E','SE','S','SW','W','NW']
    DISTANCES = [35, 55, 80, 110]

    candidates = []
    for bearing, label in zip(BEARINGS, LABELS):
        for dist in DISTANCES:
            cam_lat, cam_lon = offset_point(lat, lon, bearing, dist)
            url = (f'https://maps.googleapis.com/maps/api/streetview/metadata'
                   f'?location={cam_lat},{cam_lon}&radius=30&source=outdoor'
                   f'&key={GMAPS_KEY}')
            try:
                meta = httpx.get(url, timeout=8).json()
            except:
                continue

            if not is_outdoor_panorama(meta):
                continue

            pano_id = meta['pano_id']
            # Don't add the same panorama twice
            if any(c['pano_id'] == pano_id for c in candidates):
                continue

            look_heading = (bearing + 180) % 360
            candidates.append({
                'pano_id':  pano_id,
                'cam_lat':  cam_lat,
                'cam_lon':  cam_lon,
                'heading':  look_heading,
                'label':    label,
                'dist':     dist,
            })
            break  # found outdoor pano at this bearing, move to next bearing

        time.sleep(0.05)

    return candidates

def pick_spread(candidates, n=4):
    """Pick n candidates maximally spread around the building."""
    if len(candidates) <= n:
        return candidates
    candidates.sort(key=lambda c: c['heading'])
    step = len(candidates) / n
    return [candidates[int(i * step)] for i in range(n)]

def fetch_image(pano_id, heading):
    url = (f'https://maps.googleapis.com/maps/api/streetview'
           f'?size=640x400&pano={pano_id}'
           f'&heading={heading}&pitch=8&fov=90'
           f'&key={GMAPS_KEY}')
    try:
        resp = httpx.get(url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 5000:
            return base64.standard_b64encode(resp.content).decode()
    except:
        pass
    return None

def annotate_panel(image_b64, findings, label, risk_level):
    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert('RGB')
    draw = ImageDraw.Draw(img)
    W, H = img.size
    fb = load_font(13, bold=True)
    fr = load_font(11)

    risk_color = COLORS.get(risk_level, (150,150,150))
    draw.rectangle([0, 0, W, 24], fill=(0,0,0))
    draw.text((6, 5), label.upper(), fill=(255,255,0), font=fb)
    draw.text((W-95, 5), risk_level, fill=risk_color, font=fb)

    if not findings:
        return img

    row_h = 26
    panel_h = row_h * min(len(findings), 4) + 12
    py = H - panel_h
    draw.rectangle([0, py, W, H], fill=(0,0,0))
    for i, f in enumerate(findings[:4]):
        color = COLORS.get(f.get('severity','LOW'), (150,150,150))
        y = py + 6 + i * row_h
        draw.rectangle([8, y, 100, y+20], fill=color)
        draw.text((11, y+3), f.get('severity',''), fill=(255,255,255), font=fb)
        txt = f"{f.get('type','')}: {f.get('location','')[:42]}"
        draw.text((106, y+4), txt, fill=(255,255,255), font=fr)
    return img

def make_placeholder(label):
    img = Image.new('RGB', (640,400), (45,45,45))
    draw = ImageDraw.Draw(img)
    fb = load_font(13, bold=True)
    fr = load_font(12)
    draw.text((6, 5),    label.upper(),               fill=(255,255,0),  font=fb)
    draw.text((20, 185), 'No exterior Street View',   fill=(180,180,180),font=fr)
    draw.text((20, 205), 'coverage at this angle',    fill=(120,120,120),font=fr)
    return img

def build_quad(panels, building_name, overall_risk, score, material):
    CELL_W, CELL_H = 640, 400
    TITLE_H = 52
    GAP = 4
    cw = CELL_W*2 + GAP
    ch = CELL_H*2 + GAP + TITLE_H
    canvas = Image.new('RGB', (cw, ch), (20,20,20))
    draw   = ImageDraw.Draw(canvas)

    risk_color = COLORS.get(overall_risk, (150,150,150))
    draw.rectangle([0,0,cw,TITLE_H], fill=(10,10,10))
    ft = load_font(17, bold=True)
    fs = load_font(12)
    draw.text((10, 6),  building_name, fill=(255,255,255), font=ft)
    draw.text((10, 28),
              f'Risk: {overall_risk}   Score: {score}   Material: {material}',
              fill=risk_color, font=fs)

    positions = [
        (0,          TITLE_H),
        (CELL_W+GAP, TITLE_H),
        (0,          TITLE_H+CELL_H+GAP),
        (CELL_W+GAP, TITLE_H+CELL_H+GAP),
    ]
    while len(panels) < 4:
        panels.append((make_placeholder('N/A'), 'N/A'))

    for idx, (pil, _) in enumerate(panels[:4]):
        canvas.paste(pil.resize((CELL_W, CELL_H), Image.LANCZOS), positions[idx])

    return canvas

def run(results_path='data/vlm_results.json',
        quads_dir='data/quads',
        epi_lat=40.7549, epi_lon=-73.9840):

    Path(quads_dir).mkdir(parents=True, exist_ok=True)
    results = json.load(open(results_path))
    total   = len(results)
    no_coverage = []

    for i, b in enumerate(results):
        name     = b.get('name') or f"Building {b['building_id']}"
        lat      = b.get('centroid_lat')
        lon      = b.get('centroid_lon')
        analysis = b.get('vlm_analysis') or {}
        triage   = b.get('triage') or {}
        score    = triage.get('score', '?')
        material = b.get('material', 'unknown')
        color    = triage.get('color', 'YELLOW')
        orig_heading = b.get('streetview', {}).get('heading', 0)
        findings     = analysis.get('findings', [])
        risk_level   = analysis.get('risk_level', 'LOW')

        print(f"\n[{i+1}/{total}]  {name}  score={score}")
        print(f"  Scanning for outdoor panoramas...", end=' ', flush=True)

        candidates = find_exterior_panoramas(lat, lon)

        if not candidates:
            print(f"none found")
            no_coverage.append(name)
            panels = [(make_placeholder(d), d) for d in ['N','E','S','W']]
            quad = build_quad(panels, name, color, score, material)
            fname = f"{name.replace(' ','_').replace('/','_')[:60]}.jpg"
            quad.save(f"{quads_dir}/{fname}", quality=88)
            print(f"  Saved placeholder → {quads_dir}/{fname}")
            continue

        selected = pick_spread(candidates, n=4)
        print(f"found {len(candidates)} → using {[c['label'] for c in selected]}")

        panels = []
        for c in selected:
            img_b64 = fetch_image(c['pano_id'], c['heading'])
            if not img_b64:
                panels.append((make_placeholder(c['label']), c['label']))
                continue

            # Attach real findings to panel closest to original analysis heading
            diff = abs((c['heading'] - orig_heading + 180) % 360 - 180)
            use_findings = findings  if diff < 60 else []
            use_risk     = risk_level if diff < 60 else 'LOW'

            panel = annotate_panel(img_b64, use_findings, c['label'], use_risk)
            panels.append((panel, c['label']))
            print(f"    {c['label']} ({c['heading']}°, {c['dist']}m away) OK")
            time.sleep(0.15)

        quad = build_quad(panels, name, color, score, material)
        fname = f"{name.replace(' ','_').replace('/','_')[:60]}.jpg"
        quad.save(f"{quads_dir}/{fname}", quality=88)
        print(f"  Saved → {quads_dir}/{fname}")

    print(f"\n{'='*50}")
    print(f"Done. {total} buildings processed.")
    if no_coverage:
        print(f"No outdoor coverage ({len(no_coverage)}):")
        for n in no_coverage: print(f"  - {n}")

if __name__ == '__main__':
    run()