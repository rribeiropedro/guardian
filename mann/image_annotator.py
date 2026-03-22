from PIL import Image, ImageDraw, ImageFont
import io, base64


COLORS = {
    'CRITICAL': (220, 38, 38),
    'MODERATE': (234, 88, 12),
    'LOW':      (22, 163, 74),
}
THICKNESS = {'CRITICAL': 4, 'MODERATE': 3, 'LOW': 2}


def annotate_image(image_b64, findings, image_size=(640, 400)):
    """
    Draw colored bounding boxes + labels on a Street View image.
    findings: list of dicts from vlm_analyzer, each with:
      severity, location, type, detail
    Returns annotated image as base64 string.


    NOTE: The VLM doesn't return pixel coordinates.
    We place labels as text overlays instead of boxes.
    Boxes are evenly spaced at the bottom of the image.
    """
    img_bytes = base64.b64decode(image_b64)
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    draw = ImageDraw.Draw(img)
    W, H = img.size


    # Try to load a font, fall back to default
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
        font_sm = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
    except:
        font = ImageFont.load_default()
        font_sm = font


    # Draw a legend panel at the bottom
    panel_h = 30 * len(findings) + 20
    panel_top = H - panel_h
    draw.rectangle([0, panel_top, W, H], fill=(0,0,0,180))


    for i, finding in enumerate(findings):
        color = COLORS.get(finding['severity'], (150,150,150))
        y = panel_top + 10 + i * 30
        # Severity badge
        badge_w = 90
        draw.rectangle([10, y, 10 + badge_w, y + 22], fill=color)
        draw.text((14, y + 3), finding['severity'], fill=(255,255,255), font=font)
        # Finding text
        label = f"{finding['type']}: {finding['location'][:50]}"
        draw.text((110, y + 4), label, fill=(255,255,255), font=font_sm)


    # Re-encode to base64
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode('utf-8')


if __name__ == '__main__':
    # Test with fake data
    import httpx, os
    from dotenv import load_dotenv
    load_dotenv()
    url = ('https://maps.googleapis.com/maps/api/streetview'
           '?size=640x400&location=37.2296,-80.4222&heading=180'
           f"&key={os.environ['GOOGLE_MAPS_KEY']}")
    img_b64 = base64.standard_b64encode(httpx.get(url).content).decode()
    test_findings = [
        {'severity': 'CRITICAL', 'type': 'Structural', 'location': 'South overhang', 'detail': 'Concrete canopy likely to fall'},
        {'severity': 'MODERATE', 'type': 'Overhead Hazard', 'location': 'Power lines east', 'detail': 'Lines cross entrance path'},
        {'severity': 'LOW', 'type': 'Access', 'location': 'Main entrance clear', 'detail': 'No visible obstructions'},
    ]
    out = annotate_image(img_b64, test_findings)
    with open('test_annotated.jpg', 'wb') as f:
        f.write(base64.b64decode(out))
    print('Saved test_annotated.jpg — open it to verify')
