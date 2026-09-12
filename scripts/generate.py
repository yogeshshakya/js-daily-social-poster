"""Generate today's Instagram carousel post: researches an ADVANCED,
commonly-misunderstood JS/React/Next.js topic with Gemini (grounded via
Google Search), turns the research into a multi-slide infographic-style
carousel script, renders each slide onto a blue circuit-pattern background,
and writes an SEO-friendly caption with a structured hashtag mix.

The title/thumbnail slide uses the bundled mascot avatar (assets/avatar.png)
with its background removed - ONLY that slide, per spec.

Outputs (per run):
  - images/<DATE>-slide1.png ... slideN.png
  - build/caption_instagram.txt   (full SEO caption + hashtags, <=2200 chars)
  - build/caption_telegram.txt    (shorter variant, <=1024 chars)
  - build/meta.json               (raw structured content, for debugging)

Only Gemini is called over the network. Designed to run inside GitHub
Actions, which has open internet access.
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

from topics import TOPICS

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]


def _clean_model_name(name):
    name = name.strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name


# Try the configured model first, then fall back through this list if it's
# unavailable/overloaded - keeps a single busy model from blocking the whole
# run. GEMINI_MODEL env var (if set) always goes first.
_env_model = os.environ.get("GEMINI_MODEL")
MODEL_CANDIDATES = []
if _env_model:
    MODEL_CANDIDATES.append(_clean_model_name(_env_model))
for _fallback in [
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]:
    if _fallback not in MODEL_CANDIDATES:
        MODEL_CANDIDATES.append(_fallback)

GEMINI_MODEL = MODEL_CANDIDATES[0]  # for logging only; actual calls try the whole list

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).strftime("%Y-%m-%d")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(REPO_ROOT, "images")
BUILD_DIR = os.path.join(REPO_ROOT, "build")
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")
AVATAR_PATH = os.path.join(ASSETS_DIR, "avatar.png")
BG_REFERENCE_PATH = os.path.join(ASSETS_DIR, "bg_reference.jpg")
os.makedirs(IMAGES_DIR, exist_ok=True)
HISTORY_PATH = os.path.join(REPO_ROOT, "data", "topic_history.json")
HISTORY_MAX_ENTRIES = 30

os.makedirs(BUILD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)

# Image-generation model candidates, tried in order (same pattern as the text
# MODEL_CANDIDATES above). AI background generation is best-effort: if every
# candidate fails (no billing enabled, model unavailable, quota, network),
# generate_daily_background() falls back to the procedural gradient so a
# single day's post is never blocked by this.
_env_image_model = os.environ.get("IMAGE_MODEL")
IMAGE_MODEL_CANDIDATES = []
if _env_image_model:
    IMAGE_MODEL_CANDIDATES.append(_clean_model_name(_env_image_model))
for _fallback in [
    "gemini-2.5-flash-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image-generation",
]:
    if _fallback not in IMAGE_MODEL_CANDIDATES:
        IMAGE_MODEL_CANDIDATES.append(_fallback)

# ---------------------------------------------------------------------------
# Theme: deep blue gradient + circuit pattern, matching the brand reference
# ---------------------------------------------------------------------------
W, H = 1080, 1350  # Instagram carousel-friendly (4:5)
GRAD_TOP = (7, 16, 48)
GRAD_BOTTOM = (18, 42, 104)
PANEL_BG = (13, 28, 68)
PANEL_BORDER = (72, 132, 214)
ACCENT = (86, 196, 255)
CHECK_GREEN = (94, 224, 152)
WHITE = (240, 246, 255)
MUTED = (168, 194, 230)
CODE_TEXT = (222, 232, 248)
DOT_INACTIVE = (45, 70, 120)

BRAND_HANDLE = "@modernjavascripthub"

FONT_DIR_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
]


def find_font(filename):
    for d in FONT_DIR_CANDIDATES:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return None


def load_font(filename, size):
    path = find_font(filename)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def blend(base, accent, amount):
    return tuple(int(b * (1 - amount) + a * amount) for b, a in zip(base, accent))


def wrap_text(draw, text, font, max_width, max_chars_hard=None):
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if max_chars_hard:
        lines = [l if len(l) <= max_chars_hard else l[: max_chars_hard - 1] + "…" for l in lines]
    return lines


# ---------------------------------------------------------------------------
# Background: AI-generated texture (once/day, best-effort) or a procedural
# gradient fallback, then a faint circuit pattern + scattered tech glyphs
# drawn fresh on top of every slide for per-slide variety.
# ---------------------------------------------------------------------------
_BASE_TEXTURE = None  # set once per run by generate_daily_background()


def _encode_image_b64(path):
    import base64
    import mimetypes

    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        return mime, base64.b64encode(f.read()).decode("ascii")


def _extract_inline_image(response_json):
    try:
        parts = response_json["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return None
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            import base64
            from io import BytesIO

            raw = base64.b64decode(inline["data"])
            return Image.open(BytesIO(raw)).convert("RGB")
    return None


def generate_daily_background():
    """Best-effort: ask a Gemini image-generation model for a background
    texture in the style of assets/bg_reference.jpg. Returns a PIL Image
    sized (W, H), or None if generation isn't available/fails - callers
    must handle None by falling back to the procedural gradient."""
    if not os.path.exists(BG_REFERENCE_PATH):
        return None

    mime, b64 = _encode_image_b64(BG_REFERENCE_PATH)
    prompt = (
        "Generate a seamless-looking abstract background image, portrait "
        "orientation, in the exact color mood and visual style of the "
        "attached reference: deep navy/blue gradient with a subtle glowing "
        "tech circuit-board pattern (thin connected lines and small nodes), "
        "very dark and low-contrast overall so text and UI panels stay "
        "readable when placed on top. No text, no logos, no watermarks, no "
        "people, no readable code - purely an abstract textured background."
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": mime, "data": b64}},
            ]
        }],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }

    for model in IMAGE_MODEL_CANDIDATES:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        try:
            resp = requests.post(url, json=payload, timeout=90)
        except requests.RequestException as e:
            print(f"AI background: network error calling '{model}': {e}", file=sys.stderr)
            continue
        if not resp.ok:
            print(
                f"AI background: model '{model}' failed with {resp.status_code}: "
                f"{resp.text[:300]}",
                file=sys.stderr,
            )
            continue
        img = _extract_inline_image(resp.json())
        if img is None:
            print(f"AI background: model '{model}' returned no image data.", file=sys.stderr)
            continue

        print(f"AI background: generated successfully with '{model}'.", file=sys.stderr)
        # Cover-fit to our canvas size (resize then center-crop).
        src_w, src_h = img.size
        scale = max(W / src_w, H / src_h)
        img = img.resize((int(src_w * scale) + 1, int(src_h * scale) + 1), Image.LANCZOS)
        left = (img.width - W) // 2
        top = (img.height - H) // 2
        img = img.crop((left, top, left + W, top + H))

        # Legibility wash: darken toward our brand navy so white text/panels
        # placed on top stay readable regardless of what the model produced.
        wash = Image.new("RGB", (W, H), GRAD_TOP)
        img = Image.blend(img, wash, alpha=0.45)
        return img

    print("AI background: all candidate models failed, using procedural gradient instead.",
          file=sys.stderr)
    return None


def make_background(seed):
    if _BASE_TEXTURE is not None:
        img = _BASE_TEXTURE.copy()
    else:
        img = Image.new("RGB", (W, H), GRAD_TOP)
        draw = ImageDraw.Draw(img)
        for y in range(H):
            t = y / (H - 1)
            draw.line([(0, y), (W, y)], fill=blend(GRAD_TOP, GRAD_BOTTOM, t))

    draw = ImageDraw.Draw(img)
    rng = random.Random(seed)
    node_color = blend(GRAD_BOTTOM, ACCENT, 0.35)
    line_color = blend(GRAD_BOTTOM, ACCENT, 0.16)
    pts = [(rng.randint(0, W), rng.randint(0, int(H * 0.4))) for _ in range(16)]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 < 240:
                draw.line([pts[i], pts[j]], fill=line_color, width=1)
    for p in pts:
        draw.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3], fill=node_color)

    glyph_font = load_font("DejaVuSansMono-Bold.ttf", 30) or load_font("DejaVuSans-Bold.ttf", 30)
    glyphs = ["{ }", "</>", "01", "{ }", "()"]
    for i, g in enumerate(glyphs):
        gx = 40 + i * (W - 80) // (len(glyphs) - 1)
        gy = rng.randint(20, 55)
        color = blend(GRAD_TOP, ACCENT, 0.22)
        draw.text((gx, gy), g, font=glyph_font, fill=color)

    return img


def draw_check_badge(draw, cx, cy, r=16, fill=CHECK_GREEN):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    draw.line([(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.4)],
              fill=(8, 16, 40), width=3, joint="curve")


def draw_flow_chevron(draw, cx, cy, direction="right", r=14):
    """Small circular arrow marker used to visually connect grid panels into a flow."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=blend(GRAD_BOTTOM, ACCENT, 0.30),
                 outline=ACCENT, width=2)
    if direction == "right":
        draw.line([(cx - 5, cy - 6), (cx + 4, cy), (cx - 5, cy + 6)], fill=WHITE, width=3, joint="curve")
    else:
        draw.line([(cx - 6, cy - 5), (cx, cy + 4), (cx + 6, cy - 5)], fill=WHITE, width=3, joint="curve")


def draw_slide_role_icon(draw, cx, cy, role, r=22):
    """Icon badge next to a content slide's heading, signaling its narrative role
    (bug/problem, idea/fix, clock/impact, chart/data, star/pro-tip, warning)."""
    role = (role or "idea").lower()
    bg = ACCENT
    fg = (6, 14, 32)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=bg)

    if role == "bug":
        # simple, bold "beetle" glyph readable at small badge sizes:
        # oval body split by a center line, 3 short legs per side, 2 antennae
        bw, bh = r * 0.42, r * 0.56
        draw.ellipse([cx - bw, cy - bh, cx + bw, cy + bh], fill=fg)
        draw.line([(cx, cy - bh), (cx, cy + bh)], fill=bg, width=2)
        for ly in (-0.45, 0, 0.45):
            yy = cy + ly * bh
            draw.line([(cx - bw, yy), (cx - r * 0.82, yy)], fill=fg, width=3)
            draw.line([(cx + bw, yy), (cx + r * 0.82, yy)], fill=fg, width=3)
        draw.line([(cx - bw * 0.5, cy - bh), (cx - r * 0.55, cy - r * 0.85)], fill=fg, width=2)
        draw.line([(cx + bw * 0.5, cy - bh), (cx + r * 0.55, cy - r * 0.85)], fill=fg, width=2)
    elif role == "clock":
        cr = r * 0.62
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=fg, width=3)
        draw.line([(cx, cy), (cx, cy - cr * 0.7)], fill=fg, width=3)
        draw.line([(cx, cy), (cx + cr * 0.5, cy + cr * 0.15)], fill=fg, width=3)
    elif role == "chart":
        bw = r * 0.32
        heights = [r * 0.5, r * 0.9, r * 0.65]
        bx = cx - 1.5 * bw - 4
        for i, h in enumerate(heights):
            x0 = bx + i * (bw + 6)
            draw.rectangle([x0, cy + r * 0.6 - h, x0 + bw, cy + r * 0.6], fill=fg)
    elif role == "star":
        pts = []
        import math
        for i in range(10):
            ang = math.pi / 2 + i * math.pi / 5
            rad = r * 0.75 if i % 2 == 0 else r * 0.32
            pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
        draw.polygon(pts, fill=fg)
    elif role == "warning":
        h = r * 0.85
        draw.polygon([(cx, cy - h), (cx - h * 0.95, cy + h * 0.7), (cx + h * 0.95, cy + h * 0.7)], fill=fg)
        draw.ellipse([cx - 3, cy + h * 0.35, cx + 3, cy + h * 0.35 + 6], fill=bg)
        draw.line([(cx, cy - h * 0.35), (cx, cy + h * 0.2)], fill=bg, width=3)
    else:  # idea / lightbulb (default: fix / how-it-works slides)
        br = r * 0.5
        draw.ellipse([cx - br, cy - br * 1.1, cx + br, cy + br * 0.9], outline=fg, width=3)
        draw.line([(cx - r * 0.22, cy + br * 0.75), (cx + r * 0.22, cy + br * 0.75)], fill=fg, width=3)
        draw.line([(cx - r * 0.15, cy + r * 0.65), (cx + r * 0.15, cy + r * 0.65)], fill=fg, width=2)


# ---------------------------------------------------------------------------
# Avatar (title slide only): remove near-white background, paste as PNG
# ---------------------------------------------------------------------------
def load_avatar_cutout(target_height):
    if not os.path.exists(AVATAR_PATH):
        return None
    im = Image.open(AVATAR_PATH).convert("RGB")
    w, h = im.size
    scale = target_height / h
    im = im.resize((int(w * scale), target_height), Image.LANCZOS)
    im = im.convert("RGBA")
    pixels = im.load()
    iw, ih = im.size
    for y in range(ih):
        for x in range(iw):
            r, g, b, a = pixels[x, y]
            if r > 235 and g > 235 and b > 235:
                pixels[x, y] = (r, g, b, 0)
            elif r > 220 and g > 220 and b > 220:
                # soft edge feather
                pixels[x, y] = (r, g, b, 120)
    return im


# ---------------------------------------------------------------------------
# Chrome shared across slides: kicker, counter, footer brand + progress dots
# ---------------------------------------------------------------------------
def draw_chrome(draw, slide_index, total, kicker=None):
    margin = 60

    if kicker:
        tag_font = load_font("DejaVuSans-Bold.ttf", 26)
        pad_x, pad_y = 20, 10
        tw = draw.textlength(kicker, font=tag_font)
        box_w, box_h = tw + 2 * pad_x, 26 + 2 * pad_y
        draw.rounded_rectangle([margin, 90, margin + box_w, 90 + box_h], radius=box_h / 2, fill=ACCENT)
        draw.text((margin + pad_x, 90 + pad_y), kicker, font=tag_font, fill=(6, 14, 32))

    counter_font = load_font("DejaVuSans-Bold.ttf", 26)
    counter_text = f"{slide_index}/{total}"
    ctw = draw.textlength(counter_text, font=counter_font)
    draw.text((W - margin - ctw, 98), counter_text, font=counter_font, fill=MUTED)

    brand_font = load_font("DejaVuSans-Bold.ttf", 28)
    draw.text((margin, H - 80), BRAND_HANDLE, font=brand_font, fill=ACCENT)

    dot_r, gap = 7, 22
    total_w = (total - 1) * gap
    start_x = W - margin - total_w - dot_r
    for i in range(total):
        cx = start_x + i * gap
        cy = H - 66
        fill = ACCENT if i == slide_index - 1 else DOT_INACTIVE
        draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=fill)


# ---------------------------------------------------------------------------
# Title / thumbnail slide (uses the mascot avatar)
# ---------------------------------------------------------------------------
def draw_speech_bubble(draw, cx, bottom_y, text, font, max_width=560, fill=None, text_fill=None):
    """Rounded speech bubble with a downward tail, bottom-anchored at (cx, bottom_y)."""
    fill = fill or WHITE
    text_fill = text_fill or (10, 20, 46)
    pad_x, pad_y, line_h = 26, 20, 34
    lines = wrap_text(draw, text, font, max_width - 2 * pad_x)[:3]
    text_w = max((draw.textlength(l, font=font) for l in lines), default=0)
    box_w = text_w + 2 * pad_x
    box_h = len(lines) * line_h + 2 * pad_y
    tail = 18
    top = bottom_y - box_h - tail
    left = cx - box_w / 2
    draw.rounded_rectangle([left, top, left + box_w, top + box_h], radius=20, fill=fill)
    draw.polygon(
        [(cx - tail, top + box_h), (cx + tail, top + box_h), (cx, top + box_h + tail)],
        fill=fill,
    )
    ty = top + pad_y
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text((cx - tw / 2, ty), line, font=font, fill=text_fill)
        ty += line_h
    return top  # top edge, in case caller wants to reserve space above it


def render_title_slide(slide, index, total, out_path):
    img = make_background(seed=f"{TODAY}-title")
    draw = ImageDraw.Draw(img)
    kicker = slide.get("kicker", "JS DEEP DIVE")
    draw_chrome(draw, index, total, kicker=kicker)

    margin = 60
    title_font = load_font("DejaVuSans-Bold.ttf", 64)
    subtitle_font = load_font("DejaVuSans.ttf", 32)

    # "ADVANCED" difficulty badge, right after the kicker pill
    tag_font = load_font("DejaVuSans-Bold.ttf", 26)
    kicker_w = draw.textlength(kicker, font=tag_font) + 40
    badge_text = "ADVANCED"
    badge_w = draw.textlength(badge_text, font=tag_font) + 34
    badge_x = margin + kicker_w + 14
    draw.rounded_rectangle([badge_x, 90, badge_x + badge_w, 90 + 46], radius=23,
                            fill=(255, 138, 76), outline=None)
    draw.text((badge_x + 17, 90 + 10), badge_text, font=tag_font, fill=(40, 16, 4))

    y = 180
    title_lines = wrap_text(draw, slide["title"], title_font, W - 2 * margin)[:4]
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=WHITE)
        y += 76
    y += 14
    for line in wrap_text(draw, slide.get("subtitle", ""), subtitle_font, W - 2 * margin)[:3]:
        draw.text((margin, y), line, font=subtitle_font, fill=MUTED)
        y += 42
    y += 10

    swipe_font = load_font("DejaVuSans-Bold.ttf", 28)
    swipe_text = "SWIPE TO LEARN  →"
    draw.text((margin, y), swipe_text, font=swipe_font, fill=ACCENT)

    # Mascot avatar with a speech bubble "explaining" the topic
    avatar_h = 500
    avatar = load_avatar_cutout(avatar_h)
    if avatar:
        ax = (W - avatar.width) // 2
        ay = H - 150 - avatar_h
        head_cx = ax + avatar.width // 2

        bubble_font = load_font("DejaVuSans-Bold.ttf", 27)
        avatar_line = slide.get("avatar_line") or "Let's break this down!"
        draw_speech_bubble(draw, head_cx, ay - 6, avatar_line, bubble_font)

        img.paste(avatar, (ax, ay), avatar)
        draw = ImageDraw.Draw(img)  # redraw handle after paste

    draw_chrome(draw, index, total, kicker=None)  # repaint footer over avatar edge if needed
    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# Content slide: 2x2 infographic panel grid (code / flow / output / mechanics)
# ---------------------------------------------------------------------------
def panel_icon(draw, cx, cy, kind):
    r = 14
    if kind == "code":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=2)
        f = load_font("DejaVuSansMono-Bold.ttf", 16)
        draw.text((cx - 9, cy - 10), "</>", font=f, fill=ACCENT)
    elif kind == "output":
        draw.rounded_rectangle([cx - r, cy - r, cx + r, cy + r], radius=4, outline=ACCENT, width=2)
        draw.line([(cx - 7, cy - 2), (cx - 2, cy + 3), (cx + 7, cy - 6)], fill=ACCENT, width=2)
    else:  # flow / mechanics
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=2)
        draw.line([(cx - 6, cy), (cx + 6, cy)], fill=ACCENT, width=2)
        draw.line([(cx + 1, cy - 5), (cx + 6, cy), (cx + 1, cy + 5)], fill=ACCENT, width=2)


def render_panel(draw, x, y, w, h, panel):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=18, fill=PANEL_BG, outline=PANEL_BORDER, width=2)

    header_font = load_font("DejaVuSans-Bold.ttf", 24)
    icon_cx, icon_cy = x + 34, y + 34
    panel_icon(draw, icon_cx, icon_cy, panel.get("kind", "flow"))
    draw.text((x + 58, y + 20), panel.get("title", ""), font=header_font, fill=WHITE)

    content_y = y + 68
    content_x = x + 24
    content_w = w - 48
    lines = [str(l) for l in panel.get("lines", [])][:5]
    kind = panel.get("kind", "flow")

    if kind == "code":
        code_font = load_font("DejaVuSansMono.ttf", 20)
        draw.rounded_rectangle([content_x, content_y, x + w - 24, y + h - 16], radius=10, fill=(6, 14, 34))
        cy = content_y + 14
        for line in lines[: max(1, int(h - 90) // 26)]:
            draw.text((content_x + 14, cy), line[:34], font=code_font, fill=CODE_TEXT)
            cy += 26
    elif kind == "output":
        line_font = load_font("DejaVuSansMono.ttf", 21)
        cy = content_y + 6
        for line in lines[: max(1, int(h - 90) // 34)]:
            draw_check_badge(draw, content_x + 12, cy + 12, r=11)
            draw.text((content_x + 34, cy), line[:30], font=line_font, fill=MUTED)
            cy += 34
        result = panel.get("result")
        if result:
            rf = load_font("DejaVuSans-Bold.ttf", 18)
            draw.text((content_x, y + h - 34), f"✓ {result}", font=rf, fill=CHECK_GREEN)
    else:  # flow / mechanics: chained small boxes
        item_font = load_font("DejaVuSans.ttf", 19)
        max_items = max(1, min(len(lines), int(h - 100) // 46))
        cy = content_y
        for i, line in enumerate(lines[:max_items]):
            box_h = 34
            wrapped = wrap_text(draw, line, item_font, content_w - 20)[:1]
            text = wrapped[0] if wrapped else line[:28]
            draw.rounded_rectangle([content_x, cy, x + w - 24, cy + box_h], radius=8,
                                    fill=blend(PANEL_BG, ACCENT, 0.12), outline=PANEL_BORDER, width=1)
            draw.text((content_x + 10, cy + 6), text, font=item_font, fill=MUTED)
            cy += box_h + 8
            if i < max_items - 1:
                draw.line([(x + w / 2, cy - 8), (x + w / 2, cy - 2)], fill=ACCENT, width=2)
        result = panel.get("result")
        if result:
            rf = load_font("DejaVuSans-Bold.ttf", 17)
            draw.text((content_x, y + h - 30), f"✓ {result}", font=rf, fill=CHECK_GREEN)


def render_content_slide(slide, index, total, out_path):
    img = make_background(seed=f"{TODAY}-{index}")
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, index, total, kicker=f"STEP {index - 1}")

    margin = 60
    heading_font = load_font("DejaVuSans-Bold.ttf", 46)
    y = 176
    draw_slide_role_icon(draw, margin + 22, y + 30, slide.get("icon"), r=22)
    heading_lines = wrap_text(draw, slide.get("heading", ""), heading_font, W - 2 * margin - 60)[:2]
    for line in heading_lines:
        draw.text((margin + 56, y), line, font=heading_font, fill=WHITE)
        y += 58
    y += 20

    panels = slide.get("panels", [])[:4]
    while len(panels) < 4:
        panels.append({"title": "", "kind": "flow", "lines": []})

    grid_top = y
    grid_bottom = H - 110
    gap = 22
    panel_w = (W - 2 * margin - gap) / 2
    panel_h = (grid_bottom - grid_top - gap) / 2

    for i, panel in enumerate(panels):
        col, row = i % 2, i // 2
        px = margin + col * (panel_w + gap)
        py = grid_top + row * (panel_h + gap)
        render_panel(draw, px, py, panel_w, panel_h, panel)

    # Connect the 4 panels into a visual flow: -> across each row, v down each column
    hgap_x = margin + panel_w + gap / 2
    row0_cy = grid_top + panel_h / 2
    row1_cy = grid_top + panel_h + gap + panel_h / 2
    draw_flow_chevron(draw, hgap_x, row0_cy, "right")
    draw_flow_chevron(draw, hgap_x, row1_cy, "right")

    vgap_y = grid_top + panel_h + gap / 2
    col0_cx = margin + panel_w / 2
    col1_cx = margin + panel_w + gap + panel_w / 2
    draw_flow_chevron(draw, col0_cx, vgap_y, "down")
    draw_flow_chevron(draw, col1_cx, vgap_y, "down")

    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# Summary slide
# ---------------------------------------------------------------------------
def render_summary_slide(slide, index, total, out_path):
    img = make_background(seed=f"{TODAY}-summary")
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, index, total, kicker="KEY TAKEAWAY")

    margin = 60
    heading_font = load_font("DejaVuSans-Bold.ttf", 58)
    body_font = load_font("DejaVuSans.ttf", 36)
    cta_font = load_font("DejaVuSans-Bold.ttf", 34)

    y = 260
    for line in wrap_text(draw, slide.get("heading", ""), heading_font, W - 2 * margin)[:3]:
        draw.text((margin, y), line, font=heading_font, fill=WHITE)
        y += 70
    y += 20
    for line in wrap_text(draw, slide.get("body", ""), body_font, W - 2 * margin)[:6]:
        draw.text((margin, y), line, font=body_font, fill=MUTED)
        y += 46
    y += 50

    cta = slide.get("cta", f"Follow {BRAND_HANDLE} for daily JS/React/Next.js tips")
    cta_lines = wrap_text(draw, cta, cta_font, W - 2 * margin - 60)
    box_h = 60 + len(cta_lines) * 42
    draw.rounded_rectangle([margin, y, W - margin, y + box_h], radius=18, fill=PANEL_BG, outline=ACCENT, width=2)
    cy = y + 30
    for line in cta_lines:
        draw.text((margin + 30, cy), line, font=cta_font, fill=ACCENT)
        cy += 42

    img.save(out_path, "PNG")


def render_slide(slide, index, total, out_path):
    kind = slide.get("type", "content")
    if kind == "title":
        render_title_slide(slide, index, total, out_path)
    elif kind == "summary":
        render_summary_slide(slide, index, total, out_path)
    else:
        render_content_slide(slide, index, total, out_path)


# ---------------------------------------------------------------------------
# Gemini calls: research -> structured carousel script
# ---------------------------------------------------------------------------
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def gemini_call(payload, retries_per_model=2):
    last_status, last_body = None, ""
    for model in MODEL_CANDIDATES:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        delay = 15
        for attempt in range(1, retries_per_model + 1):
            resp = requests.post(url, json=payload, timeout=90)
            if resp.ok:
                if model != MODEL_CANDIDATES[0]:
                    print(f"Gemini: succeeded using fallback model '{model}'.", file=sys.stderr)
                return resp.json()

            last_status, last_body = resp.status_code, resp.text[:500]
            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < retries_per_model:
                print(
                    f"Gemini model '{model}' returned {resp.status_code} (transient), "
                    f"retrying in {delay}s (attempt {attempt}/{retries_per_model})...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay = min(delay * 2, 90)
                continue
            # Out of retries for this model (or non-retryable error) - try the next model.
            print(
                f"Gemini model '{model}' failed with {resp.status_code}, "
                f"moving to next fallback model...",
                file=sys.stderr,
            )
            break

    raise RuntimeError(
        f"All Gemini models {MODEL_CANDIDATES} failed. Last error {last_status}: {last_body}"
    )


# ---------------------------------------------------------------------------
# Topic selection: ask Gemini to pick today's topic itself (instead of a
# fixed local list), staying within our subjects and avoiding recent repeats.
# ---------------------------------------------------------------------------
SUBJECTS = (
    "JavaScript latest/new language features, JavaScript performance "
    "optimization, React (internals + best practices), and Next.js "
    "(internals + best practices)"
)

# A few examples purely to calibrate the STYLE/DEPTH expected (advanced,
# narrow, commonly-misunderstood) - not an exhaustive pool Gemini is limited
# to. Reused from the old local topics.py list.
STYLE_EXAMPLES = TOPICS[:4]


def load_topic_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_topic_history(history):
    trimmed = history[-HISTORY_MAX_ENTRIES:]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2)


def choose_topic(history):
    """Ask Gemini to pick today's specific topic itself, within SUBJECTS,
    avoiding anything already covered recently. Raises on total failure so
    the caller can fall back to the local topics.py pool."""
    avoid = [entry.get("topic", "") for entry in history if entry.get("topic")]
    avoid_block = (
        "Topics already covered recently - pick something meaningfully "
        "different from ALL of these (not just a reworded duplicate):\n"
        + "\n".join(f"- {t}" for t in avoid)
        if avoid
        else "No topics covered yet - pick freely."
    )
    examples_block = "\n".join(f"- {t}" for t in STYLE_EXAMPLES)

    prompt = f"""You are picking today's topic for "Modern JavaScript Hub", an
Instagram/Telegram account teaching {SUBJECTS} to intermediate/senior
developers.

The topic must be ADVANCED and NARROW - a specific commonly-misunderstood
behavior or mistake experienced developers actually make in real code, not a
beginner definition or a broad category name. For calibration, here is the
STYLE and DEPTH expected (do not just reuse these, they're only examples):
{examples_block}

{avoid_block}

Reply with ONLY a JSON object of this exact shape, no markdown fences:
{{"topic": "one specific, narrow, advanced topic phrased as a full sentence
  describing the exact misunderstood behavior, like the style examples above"}}"""

    data = gemini_call(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.9,
                "responseMimeType": "application/json",
            },
        }
    )
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse topic-selection response as JSON:\n{text}")
        parsed = json.loads(match.group(0))

    topic = (parsed.get("topic") or "").strip()
    if not topic:
        raise RuntimeError(f"Gemini topic-selection response missing 'topic': {parsed}")
    return topic


def research_topic(topic):
    # Note: Google Search grounding ("tools": [{"google_search": {}}]) is a
    # paid/billing-enabled feature and returns persistent 429s on free-tier
    # API keys, so this step relies on the model's own trained knowledge
    # instead. That's fine for stable language/framework mechanics (closures,
    # scheduling, hooks semantics, etc.) which don't change day to day; it's
    # just not suitable for "literally today's news" style topics. If you
    # later enable billing on your Google AI Studio project, you can restore
    # grounding by adding back "tools": [{"google_search": {}}] below.
    prompt = f"""Research this ADVANCED, commonly-misunderstood JavaScript/React/Next.js
topic - the kind experienced developers still get wrong in real code, not a
beginner definition. Topic: "{topic}"

Write a precise, technically accurate briefing (250-400 words) I will use to
build a step-by-step "here's the bug, here's why, here's the fix" carousel
post. Include: the exact buggy behavior/output, WHY it happens internally
(the actual mechanism, not a hand-wave), and the correct fix with what
changes internally. Do not invent APIs or behavior that doesn't exist - if
you are not fully certain of a detail, leave it out rather than guessing.
Plain text only."""

    data = gemini_call(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4},
        }
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


def build_carousel(topic, research):
    prompt = f"""You are the content writer for "Modern JavaScript Hub", an Instagram +
Telegram account ({BRAND_HANDLE}) that teaches ADVANCED JavaScript, React,
and Next.js to intermediate/senior developers - the non-obvious stuff that
causes real bugs, not textbook basics. Tone: clear, confident, precise -
like a sharp senior engineer explaining a bug in code review. Every claim
must be technically correct per the research below.

Topic: {topic}

Research briefing (base the content on this, don't just repeat it verbatim):
---
{research}
---

Produce a JSON object (ONLY JSON, no markdown fences) with this exact shape:

{{
  "slides": [
    {{
      "type": "title",
      "kicker": "3-4 word label, e.g. JS DEEP DIVE",
      "title": "punchy 5-9 word hook naming the specific bug/gotcha",
      "subtitle": "one short sentence promising the fix they'll learn",
      "avatar_line": "a short 3-7 word first-person line the mascot character
        is 'saying' in a speech bubble, as if personally inviting the viewer
        into this topic - e.g. 'Let's debug this together!' or 'You've
        shipped this bug before...' - punchy and conversational, not a
        repeat of the title"
    }},
    {{
      "type": "content",
      "heading": "3-5 word heading for this slide, e.g. 'Fix It With let'",
      "icon": "one of: bug, warning, idea, clock, chart, star - pick whichever
        best matches this slide's role (bug=the problem/buggy code,
        warning=a risk/gotcha, idea=the fix/how it works, clock=timing or
        real-world impact over time, chart=data/comparison/impact, star=pro
        tip/bonus insight)",
      "panels": [
        {{"title": "short 2-3 word panel label fitting this panel's role, e.g. 'Code Input', 'Buggy Code', 'The Fix', 'Console Output', 'Why It Happens', 'Real Impact', 'Pro Tip'", "kind": "code | flow | output (pick whichever best fits this panel's content)", "lines": ["for kind=code: up to 5 short code lines, <=30 chars each. for kind=flow: 2-4 short labels <=26 chars, sequential steps/facts. for kind=output: 2-4 short values/lines <=20 chars each"], "result": "optional short verified/result/takeaway label, <=22 chars"}}
      ]
    }},
    ... exactly 6 of these "content" slides total, each with exactly 4
    panels, following this narrative arc across the 6 slides so the full
    carousel tells a complete step-by-step story (don't label the slides
    with these exact words, just follow the arc via natural headings):
      1. THE PROBLEM - show the buggy/naive code and its unexpected output
      2. WHY IT HAPPENS - the actual internal/engine mechanism causing it
      3. THE FIX - the corrected code
      4. HOW THE FIX WORKS - the internal mechanism that makes the fix work
      5. REAL-WORLD IMPACT - a concrete scenario where this bug actually
         bites (production incident, perf issue, confusing behavior, etc.)
      6. PRO TIP - a related best practice, edge case, or common variant of
         this same mistake developers should also watch for
    Vary the 4 panel titles/kinds per slide to fit what that slide is
    actually showing (e.g. slide 5 might use panels like "Scenario",
    "What Breaks", "User Impact", "Root Cause" instead of the code/output
    panel titles used in slides 1-4) rather than repeating identical panel
    labels on every slide ...,
    {{
      "type": "summary",
      "heading": "short takeaway heading",
      "body": "1-2 sentence key takeaway to remember, precise and correct",
      "cta": "Follow {BRAND_HANDLE} for daily JS/React/Next.js deep dives"
    }}
  ],
  "caption": "an SEO-friendly Instagram caption, 3-5 sentences: first
    sentence is a scroll-stopping hook containing the main keyword (e.g.
    'JavaScript closures', 'React re-renders'), then deliver real value in
    plain language while naturally weaving in 2-3 high-search-volume,
    evergreen keyword phrases developers actually search for around this
    exact topic (e.g. 'javascript interview questions', 'react performance
    optimization', 'nextjs best practices' - whichever genuinely fit this
    topic, don't force unrelated ones), then a short call to action to
    save/share/follow. Do NOT include hashtags in this field.",
  "hashtags": [
    "10 to 15 hashtags as a flat array (include the # in each string), mixing:
     3-4 broad/high-volume (#JavaScript #WebDevelopment #Coding
     #Programming), 5-7 niche/specific to THIS exact topic (include realistic
     high-traffic developer-community tags like #100DaysOfCode #CodeNewbie
     #WebDev #Frontend #ReactJS #JavaScriptTips where relevant), and 1-2
     community/branded (#DevCommunity #ModernJavaScriptHub)."
  ]
}}

All on-image text must be short and punchy - this is for a 1080x1350 image
panel, not an essay. Keep code lines under 30 characters so they don't get
truncated. Total slide count must be exactly 8 (1 title + 6 content + 1
summary)."""

    data = gemini_call(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        }
    )
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse Gemini response as JSON:\n{text}")
        parsed = json.loads(match.group(0))

    if not parsed.get("slides"):
        raise RuntimeError(f"Gemini response missing slides: {parsed}")
    if not parsed.get("caption"):
        raise RuntimeError(f"Gemini response missing caption: {parsed}")
    parsed.setdefault("hashtags", [])
    return parsed


# ---------------------------------------------------------------------------
# Caption assembly (Instagram: rich SEO caption; Telegram: shorter variant)
# ---------------------------------------------------------------------------
def build_captions(caption, hashtags):
    hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]

    # Instagram's app collapses consecutive plain blank lines, so the usual
    # trick to get real visual separation before the hashtag block is a
    # short stack of lines containing an invisible Braille-blank character
    # (U+2800) instead of nothing.
    ig_spacer = "\n" + "\n".join(["⠀"] * 4) + "\n"
    ig = caption.strip() + ig_spacer + " ".join(hashtags[:15])
    ig = ig[:2200]

    # Telegram doesn't collapse blank lines, so a plain double newline is enough.
    tg = caption.strip() + "\n\n" + " ".join(hashtags[:5])
    if len(tg) > 1024:
        tg = tg[:1000].rsplit(" ", 1)[0] + "…"

    return ig, tg


def main():
    global _BASE_TEXTURE
    print(f"Gemini model order (first available wins): {MODEL_CANDIDATES}", file=sys.stderr)
    print(f"Image model order (best-effort): {IMAGE_MODEL_CANDIDATES}", file=sys.stderr)
    _BASE_TEXTURE = generate_daily_background()

    history = load_topic_history()
    try:
        topic = choose_topic(history)
        print(f"Topic chosen by Gemini: {topic}", file=sys.stderr)
    except Exception as e:
        topic = random.choice(TOPICS)
        print(
            f"Gemini topic selection failed ({e}); falling back to local topic "
            f"pool: {topic}",
            file=sys.stderr,
        )

    print(f"Researching topic: {topic}", file=sys.stderr)
    research = research_topic(topic)

    print("Building carousel script...", file=sys.stderr)
    carousel = build_carousel(topic, research)
    slides = carousel["slides"]
    total = len(slides)

    filenames = []
    for i, slide in enumerate(slides, start=1):
        fname = f"{TODAY}-slide{i}.png"
        out_path = os.path.join(IMAGES_DIR, fname)
        render_slide(slide, i, total, out_path)
        filenames.append(fname)

    ig_caption, tg_caption = build_captions(carousel["caption"], carousel.get("hashtags", []))

    with open(os.path.join(BUILD_DIR, "caption_instagram.txt"), "w", encoding="utf-8") as f:
        f.write(ig_caption + "\n")
    with open(os.path.join(BUILD_DIR, "caption_telegram.txt"), "w", encoding="utf-8") as f:
        f.write(tg_caption + "\n")

    with open(os.path.join(BUILD_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"date": TODAY, "topic": topic, "research": research, **carousel, "image_files": filenames},
            f,
            indent=2,
        )

    history.append({"date": TODAY, "topic": topic})
    save_topic_history(history)

    print(f"IMAGE_FILENAMES={','.join(filenames)}")
    print(f"Generated {total}-slide carousel for topic: {topic}", file=sys.stderr)


if __name__ == "__main__":
    main()
