from dotenv import load_dotenv
load_dotenv()   
import anthropic, base64, json, httpx, os
from pathlib import Path

client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
print(os.environ['ANTHROPIC_API_KEY'])

def get_street_view_image(lat, lon, heading, api_key, size='640x400'):
    """Fetch a Street View static image and return as base64."""
    url = ('https://maps.googleapis.com/maps/api/streetview'
           f'?size={size}&location={lat},{lon}'
           f'&heading={heading}&pitch=0&fov=90'
           f'&key={api_key}')
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    return base64.standard_b64encode(resp.content).decode('utf-8')


def analyze_facade(image_b64, context):
    """
    Send a Street View image to Claude Vision for vulnerability analysis.
    context dict must contain:
      building_name, direction, epicenter_bearing, epicenter_dist_m,
      magnitude, neighbor_name (optional), neighbor_risk (optional)
    """
    neighbor_line = ''
    if context.get('neighbor_name'):
        neighbor_line = (
            f"Adjacent building {context['neighbor_name']} "
            f"({context['neighbor_risk']}% collapse risk) is nearby to the "
            f"{context['neighbor_direction']}."
        )


    system_prompt = f'''You are a tactical AI scout analyzing a building facade for earthquake vulnerability.
You are examining the {context['direction'].upper()} facade of {context['building_name']}.
Epicenter bearing: {context['epicenter_bearing']:.0f}°, distance: {context['epicenter_dist_m']:.0f}m, magnitude: {context['magnitude']}.
{neighbor_line}


CRITICAL RULES:
- You assess VULNERABILITY, never damage. Say 'likely to fail' not 'has failed'.
- Lead with the most critical finding. Use CRITICAL / MODERATE / LOW ratings.
- Be specific: 'north entrance overhang' not just 'overhang'.
- If you cannot see something clearly, say so — do not invent findings.
- Always end with a concrete approach recommendation.


Return ONLY valid JSON with this exact schema:
{{
  "findings": [
    {{"location": "string", "type": "string", "severity": "CRITICAL|MODERATE|LOW", "detail": "string"}}
  ],
  "construction_type": "masonry|steel|concrete|glass|mixed|unknown",
  "risk_level": "CRITICAL|MODERATE|LOW",
  "approach_viable": true or false,
  "recommended_action": "string (specific, actionable)",
  "commander_summary": "string (1-2 sentences, plain English, radio-style)"
}}'''


    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=1000,
        system=system_prompt,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': 'image/jpeg',
                        'data': image_b64,
                    }
                },
                {
                    'type': 'text',
                    'text': 'Analyze this facade for earthquake vulnerability.'
                }
            ]
        }]
    )
    raw = response.content[0].text
    # Strip any accidental markdown fences
    clean = raw.replace('```json', '').replace('```', '').strip()
    return json.loads(clean)


# ── Follow-up question handler ──────────────────────────────────────────
def answer_follow_up(question, building_name, direction,
                      previous_findings, image_b64=None):
    """Handle a commander's follow-up question to a scout."""
    content = []
    if image_b64:
        content.append({'type': 'image',
                         'source': {'type': 'base64',
                                    'media_type': 'image/jpeg',
                                    'data': image_b64}})
    content.append({'type': 'text', 'text': question})


    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=500,
        system=f'''You are Scout Alpha, assigned to {building_name}.
Previous findings: {json.dumps(previous_findings)}
Answer the commander's question directly, concisely, and in radio-style language.
Use CRITICAL/MODERATE/LOW severity tags where relevant.
You assess VULNERABILITY only — never claim to see actual damage.''',
        messages=[{'role': 'user', 'content': content}]
    )
    return response.content[0].text

def get_viewpoints(building_centroid_lat, building_centroid_lon,
                   epicenter_lat, epicenter_lon):
    """
    Return 4 (heading, direction_label) pairs for a building.
    Prioritizes the epicenter-facing side first.
    """
    import math
    dlat = epicenter_lat - building_centroid_lat
    dlon = epicenter_lon - building_centroid_lon
    # Bearing toward epicenter
    bearing = math.degrees(math.atan2(dlon, dlat)) % 360


    # 4 cardinal views: add 0, 90, 180, 270 from epicenter bearing
    views = []
    labels = ['primary (epicenter-facing)', 'right flank', 'rear', 'left flank']
    for i, label in enumerate(labels):
        h = (bearing + i * 90) % 360
        views.append({'heading': round(h), 'label': label})
    return views




if __name__ == '__main__':
    # Quick test (requires .env loaded)
    from dotenv import load_dotenv
    load_dotenv()
    img = get_street_view_image(
        37.2296, -80.4222, 180,   # Squires, south-facing
        os.environ['GOOGLE_MAPS_KEY']
    )
    ctx = {
        'building_name': 'Squires Student Center',
        'direction': 'south',
        'epicenter_bearing': 140,
        'epicenter_dist_m': 200,
        'magnitude': 7.2,
    }
    result = analyze_facade(img, ctx)
    print(json.dumps(result, indent=2))