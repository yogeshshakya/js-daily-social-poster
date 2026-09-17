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
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

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
ALERT_RED = (238, 82, 82)
HEAD_AMBER = (255, 197, 92)
WARN_ORANGE = (255, 146, 74)

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


def _call_image_model(parts, label):
    """Try each candidate image-generation model in turn with the given
    request parts. Returns a PIL Image on the first success, or None if every
    candidate fails (no access, quota, error, or no image in the response)."""
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }

    for model in IMAGE_MODEL_CANDIDATES:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        try:
            resp = requests.post(url, json=payload, timeout=120)
        except requests.RequestException as e:
            print(f"{label}: network error calling '{model}': {e}", file=sys.stderr)
            continue
        if not resp.ok:
            print(
                f"{label}: model '{model}' failed with {resp.status_code}: "
                f"{resp.text[:300]}",
                file=sys.stderr,
            )
            continue
        img = _extract_inline_image(resp.json())
        if img is None:
            print(f"{label}: model '{model}' returned no image data.", file=sys.stderr)
            continue

        print(f"{label}: generated successfully with '{model}'.", file=sys.stderr)
        return img

    print(f"{label}: all candidate models failed.", file=sys.stderr)
    return None


def _cover_fit(img):
    """Resize + center-crop to exactly (W, H). Use for backgrounds, where
    cropping the edges is harmless."""
    src_w, src_h = img.size
    scale = max(W / src_w, H / src_h)
    img = img.resize((int(src_w * scale) + 1, int(src_h * scale) + 1), Image.LANCZOS)
    left = (img.width - W) // 2
    top = (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def _contain_fit(img):
    """Resize to fit fully inside (W, H) and pad with the brand navy. Use for
    full AI-generated SLIDES, where cropping could cut off text."""
    src_w, src_h = img.size
    scale = min(W / src_w, H / src_h)
    new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), GRAD_TOP)
    canvas.paste(resized, ((W - new_w) // 2, (H - new_h) // 2))
    return canvas


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
    parts = [
        {"text": prompt},
        {"inlineData": {"mimeType": mime, "data": b64}},
    ]

    img = _call_image_model(parts, "AI background")
    if img is None:
        print("AI background: using procedural gradient instead.", file=sys.stderr)
        return None

    img = _cover_fit(img)
    # Legibility wash: darken toward our brand navy so white text/panels
    # placed on top stay readable regardless of what the model produced.
    wash = Image.new("RGB", (W, H), GRAD_TOP)
    return Image.blend(img, wash, alpha=0.45)


# ---------------------------------------------------------------------------
# Full-slide AI image generation (SLIDE_IMAGE_MODE=ai, the default).
#
# The image model is given the reference background image (and, for the title
# slide, the mascot avatar) plus the exact text that must appear, and asked to
# lay the whole slide out itself as an infographic. Image models are not
# reliable at rendering exact text, so EVERY slide falls back to the
# procedural PIL renderer if generation fails - and the whole mode can be
# switched off with the SLIDE_IMAGE_MODE=procedural env var / repo secret.
# ---------------------------------------------------------------------------
SLIDE_IMAGE_MODE = (os.environ.get("SLIDE_IMAGE_MODE") or "ai").strip().lower()


def _panel_spec_text(panel, n):
    lines = panel.get("lines") or []
    spec = f'  Panel {n} - heading "{panel.get("title", "")}", containing these lines exactly:\n'
    for line in lines[:5]:
        spec += f"    * {line}\n"
    if panel.get("result"):
        spec += f'    * small highlighted result label: "{panel["result"]}"\n'
    if panel.get("plain"):
        spec += (
            f'    * a short plain-English caption at the bottom of this panel, in a '
            f'smaller muted font, no jargon: "{panel["plain"]}"\n'
        )
    return spec


def _slide_spec_text(slide, index, total, variant=None, topic=None):
    """Exact, unambiguous description of what must appear on this slide."""
    kind = slide.get("type", "content")
    if kind == "title":
        avatar_line = slide.get("avatar_line") or "Let's break this down!"
        topic_line = topic or slide.get("title", "")
        spec = (
            f'This is the COVER/THUMBNAIL slide ({index} of {total}). Topic: "{topic_line}".\n\n'
            f'Generate a thumbnail for this topic with a supporting infographic so the '
            f'thumbnail is eye-catching. Creatively use the attached avatar character so it '
            f'looks like the avatar wants to explain/say something about this topic - choose '
            f'his pose, gesture, expression, and placement yourself, in whatever way best '
            f'sells the topic. Keep his face, clothes, colours and character design exactly '
            f'as in the attached image - only his pose/expression may change. Use the '
            f'attached background reference image as the background for this slide.\n\n'
            f'Text that must appear on the thumbnail, spelled exactly:\n'
            f'  - small pill label: "{slide.get("kicker", "JS DEEP DIVE")}"\n'
        )
        if slide.get("alert"):
            spec += f'  - a short warning line: "{slide["alert"]}"\n'
        spec += (
            f'  - headline: "{slide.get("title", "")}"\n'
            f'  - subtitle: "{slide.get("subtitle", "")}"\n'
            f'  - what the avatar is saying, e.g. in a speech bubble near him: "{avatar_line}"\n'
            f'  - bottom handle: "{BRAND_HANDLE}"\n'
        )
        return spec
    if kind == "summary":
        return (
            f'This is the closing SUMMARY slide ({index} of {total}).\n'
            f'Text that must appear, spelled exactly:\n'
            f'  - heading: "{slide.get("heading", "")}"\n'
            f'  - body text: "{slide.get("body", "")}"\n'
            f'  - call to action: "{slide.get("cta", "")}"\n'
            f'  - bottom left handle: "{BRAND_HANDLE}"\n'
        )

    spec = (
        f'This is CONTENT slide {index} of {total}.\n'
        f'Text that must appear, spelled exactly:\n'
        f'  - slide heading at the top: "{slide.get("heading", "")}"\n'
        f'  - then a 2x2 grid of four rounded infographic panels, connected by '
        f'small arrows so the grid reads as one left-to-right, top-to-bottom flow:\n'
    )
    for i, panel in enumerate(slide.get("panels", [])[:4], start=1):
        spec += _panel_spec_text(panel, i)
    spec += f'  - bottom left handle: "{BRAND_HANDLE}"\n'
    return spec


def generate_slide_image_ai(slide, index, total, variant=None, topic=None):
    """Best-effort: have the image model lay out the entire slide. Returns a
    PIL Image sized (W, H), or None so the caller falls back to PIL."""
    if not os.path.exists(BG_REFERENCE_PATH):
        return None

    is_title = slide.get("type", "content") == "title"
    spec = _slide_spec_text(slide, index, total, variant=variant, topic=topic)

    prompt = (
        "Create a single finished Instagram carousel slide, portrait 4:5 "
        "(1080x1350), for a developer-education account.\n\n"
        "BACKGROUND: use the attached reference image's background exactly - "
        "the same deep navy/blue tone with the same subtle glowing circuit-"
        "board pattern. Keep it dark and low-contrast so text on top is easy "
        "to read.\n\n"
        "STYLE: explain the content visually, with infographics - rounded "
        "bordered panels, clear icons, arrows showing flow between steps, "
        "monospace-looking code blocks, checkmarks for outputs. Bright cyan/"
        "blue accents, white body text, green for correct results, orange/red "
        "for errors. Clean, modern, high contrast, generous spacing, nothing "
        "cramped or clipped at the edges.\n\n"
    )

    if is_title:
        prompt += (
            "CHARACTER: the second attached image is the mascot character. Use "
            "that exact character, full body, with its background removed so it "
            "sits cleanly on the slide background - see the instructions below "
            "for how to pose and place him.\n\n"
        )

    prompt += (
        "TEXT: render every line of the text below exactly as written, with "
        "correct spelling - do not paraphrase, translate, invent extra text, or "
        "add lorem ipsum. Any code must be character-for-character identical to "
        "what is given.\n\n"
        f"{spec}\n"
        "Do not add any other logos, watermarks, URLs or captions."
    )

    bg_mime, bg_b64 = _encode_image_b64(BG_REFERENCE_PATH)
    parts = [
        {"text": prompt},
        {"inlineData": {"mimeType": bg_mime, "data": bg_b64}},
    ]
    if is_title and os.path.exists(AVATAR_PATH):
        av_mime, av_b64 = _encode_image_b64(AVATAR_PATH)
        parts.append({"inlineData": {"mimeType": av_mime, "data": av_b64}})

    img = _call_image_model(parts, f"AI slide {index}/{total}")
    if img is None:
        return None
    return _contain_fit(img)


def make_background(seed):
    if _BASE_TEXTURE is not None:
        img = _BASE_TEXTURE.copy()
    else:
        img = Image.new("RGB", (W, H), GRAD_TOP)
        draw = ImageDraw.Draw(img)
        for y in range(H):
            t = y / (H - 1)
            draw.line([(0, y), (W, y)], fill=blend(GRAD_TOP, GRAD_BOTTOM, t))

    rng = random.Random(seed)

    # Circuit texture is drawn on its own layer, then composited, so traces can
    # glow softly instead of looking like flat hairlines.
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    ld = ImageDraw.Draw(layer)

    trace = blend(GRAD_BOTTOM, ACCENT, 0.55)
    trace_dim = blend(GRAD_BOTTOM, ACCENT, 0.30)
    pad = blend(GRAD_BOTTOM, ACCENT, 0.75)

    # 1. PCB-style right-angle traces down both side gutters, with solder pads.
    for side in (0, 1):
        base_x = 18 if side == 0 else W - 18
        step = -1 if side == 1 else 1
        for lane in range(5):
            x = base_x + step * lane * 22
            y = rng.randint(0, 160)
            while y < H:
                seg = rng.randint(90, 220)
                ld.line([(x, y), (x, min(y + seg, H))], fill=trace_dim, width=2)
                y += seg
                if y < H:
                    jog = step * rng.randint(14, 34)
                    ld.line([(x, y), (x + jog, y)], fill=trace_dim, width=2)
                    ld.ellipse([x + jog - 4, y - 4, x + jog + 4, y + 4], outline=pad, width=2)
                    x += jog
                    y += rng.randint(10, 40)

    # 2. Node constellation across the whole canvas (not just the top strip).
    pts = [(rng.randint(30, W - 30), rng.randint(30, H - 30)) for _ in range(46)]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            (x1, y1), (x2, y2) = pts[i], pts[j]
            if ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 < 190:
                ld.line([pts[i], pts[j]], fill=trace_dim, width=1)
    for p in pts:
        ld.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=trace)
        ld.ellipse([p[0] - 8, p[1] - 8, p[0] + 8, p[1] + 8], outline=trace_dim, width=1)

    # 3. Faint hex/chip motifs in the corners.
    for cx, cy, r in ((90, 150, 46), (W - 90, H - 220, 56), (W - 140, 210, 34)):
        poly = []
        for k in range(6):
            a = math.pi / 3 * k
            poly.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        ld.polygon(poly, outline=trace_dim)

    layer = layer.filter(ImageFilter.GaussianBlur(radius=1.2))
    img = ImageChops.add(img, layer)

    draw = ImageDraw.Draw(img)
    glyph_font = load_font("DejaVuSansMono-Bold.ttf", 30) or load_font("DejaVuSans-Bold.ttf", 30)
    glyphs = ["{ }", "</>", "01", "{ }", "()"]
    for i, g in enumerate(glyphs):
        gx = 40 + i * (W - 80) // (len(glyphs) - 1)
        gy = rng.randint(20, 55)
        draw.text((gx, gy), g, font=glyph_font, fill=blend(GRAD_TOP, ACCENT, 0.26))

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
POSE_DIR = os.path.join(ASSETS_DIR, "avatar_poses")

# "on" (default) = try to use an AI-redrawn pose of the mascot;
# "off" = always use assets/avatar.png exactly as supplied.
AVATAR_POSE_MODE = (os.environ.get("AVATAR_POSE_MODE") or "on").strip().lower()

# Image models drift badly on "redraw this character" unless the character is
# also described in words. Edit this if you replace assets/avatar.png.
AVATAR_IDENTITY = (
    os.environ.get("AVATAR_DESCRIPTION")
    or "a young boy, 3D animated-film style (Pixar-like), with dark brown hair "
       "under a navy-blue knitted beanie that has a red-and-white pom-pom on "
       "top, wearing a green knitted sweater with a cartoon lion face on the "
       "chest, a light blue collared shirt showing at the neck, light-blue "
       "cargo jeans and teal-grey sneakers"
)

# Poses the mascot can be re-drawn in. Generated once from assets/avatar.png by
# an image model, then committed to the repo and reused every day after that.
AVATAR_POSES = {
    "pointing": (
        "standing and leaning slightly forward, pointing clearly with one hand "
        "toward his own right (the LEFT side of the frame), as if presenting "
        "something to the viewer, mouth open mid-sentence, eyes wide and "
        "enthusiastic, eyebrows raised"
    ),
    "excited": (
        "grinning widely with both eyebrows raised and one hand raised in a "
        "'wait till you see this' gesture, clearly excited and surprised"
    ),
    "thinking": (
        "one hand on his chin, head tilted slightly, thoughtful puzzled "
        "expression, as if working through a tricky problem"
    ),
}


# Cover layouts the title slide rotates through (mascot side + pose), so
# daily posts don't all share one template. What the mascot points at (a
# code-diff card or a mini step-diagram) is chosen per-topic by Gemini in
# cover_visual.style instead, since that depends on the topic, not the day.
COVER_VARIANTS = [
    {"id": "right_pointing", "side": "right", "pose": "pointing"},
    {"id": "left_excited", "side": "left", "pose": "excited"},
    {"id": "right_thinking", "side": "right", "pose": "thinking"},
    {"id": "left_pointing", "side": "left", "pose": "pointing"},
]


def choose_cover_variant(history):
    """Pick today's cover layout, avoiding whatever was used last time so
    consecutive days don't look identical."""
    last_id = None
    for entry in reversed(history):
        if entry.get("cover_variant"):
            last_id = entry["cover_variant"]
            break
    choices = [v for v in COVER_VARIANTS if v["id"] != last_id] or COVER_VARIANTS
    return random.choice(choices)


def get_avatar_pose(pose):
    """Return a path to the mascot re-drawn in `pose`, generating it once via
    the image model and caching it in assets/avatar_poses/. Falls back to the
    original avatar (returned as-is) if generation isn't available."""
    if pose not in AVATAR_POSES or not os.path.exists(AVATAR_PATH):
        return AVATAR_PATH

    os.makedirs(POSE_DIR, exist_ok=True)
    cached = os.path.join(POSE_DIR, f"{pose}.png")
    if os.path.exists(cached):
        return cached          # already approved/committed - never regenerate
    if AVATAR_POSE_MODE == "off":
        return AVATAR_PATH

    mime, b64 = _encode_image_b64(AVATAR_PATH)
    # Identity drift is the main failure mode here, so the prompt pins the
    # character down in words as well as with the attached image, and asks for
    # the smallest possible edit rather than a free redraw.
    prompt = (
        "This is an image edit task, not a new illustration. Keep the attached "
        "character EXACTLY as he is and change as little as possible.\n\n"
        f"The character is {AVATAR_IDENTITY}. His face, facial features, skin "
        "tone, hair, beanie with its pom-pom, sweater with its lion graphic, "
        "jeans, shoes, colours, proportions and rendering style must all stay "
        "identical to the attached image. Someone who knows this character must "
        "instantly recognise him as the same character.\n\n"
        f"Change ONLY his arm position and facial expression, so that he is: "
        f"{AVATAR_POSES[pose]}.\n\n"
        "Output: the same character, full body, facing the viewer, at the same "
        "scale, on a plain pure white background, with no shadow, no text, no "
        "background objects and no border."
    )
    parts = [
        {"text": prompt},
        {"inlineData": {"mimeType": mime, "data": b64}},
    ]
    img = _call_image_model(parts, f"Avatar pose '{pose}'")
    if img is None:
        print(f"Avatar pose '{pose}': unavailable, using the original avatar.", file=sys.stderr)
        return AVATAR_PATH

    img.save(cached, "PNG")
    print(f"Avatar pose '{pose}': generated and cached at {cached}.", file=sys.stderr)
    return cached


def load_avatar_cutout(target_height, path=None):
    path = path or AVATAR_PATH
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert("RGB")
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


def draw_side_speech_bubble(draw, anchor_x, cy, text, font, box_w=430, max_lines=6, side="right"):
    """Speech bubble sitting beside the mascot, tail pointing at his face - reads
    as him actually talking to the viewer, rather than a caption floating over
    his head. side="right" means the mascot is to the RIGHT of the bubble (the
    bubble's right edge sits at anchor_x, tail points right); side="left" mirrors
    this for a mascot placed on the left side of the frame."""
    pad_x, pad_y, line_h = 24, 20, 32
    lines = wrap_text(draw, text, font, box_w - 2 * pad_x)[:max_lines]
    box_h = len(lines) * line_h + 2 * pad_y
    tail = 26
    top = cy - box_h / 2
    if side == "right":
        left, right = anchor_x - box_w, anchor_x
    else:
        left, right = anchor_x, anchor_x + box_w
    draw.rounded_rectangle([left, top, right, top + box_h], radius=22, fill=WHITE)
    if side == "right":
        draw.polygon(
            [(right - 4, cy - tail / 1.6), (right - 4, cy + tail / 1.6), (right + tail, cy - 4)],
            fill=WHITE,
        )
    else:
        draw.polygon(
            [(left + 4, cy - tail / 1.6), (left + 4, cy + tail / 1.6), (left - tail, cy - 4)],
            fill=WHITE,
        )
    ty = top + pad_y
    for line in lines:
        draw.text((left + pad_x, ty), line, font=font, fill=(12, 22, 50))
        ty += line_h
    return left, top, box_h, right


def draw_cover_compare(draw, x, y, w, visual):
    """Small before/after infographic on the cover: the slow way in red, the
    fast way in green, with an arrow between them."""
    bad_label = visual.get("bad_label") or "The naive way"
    bad_note = visual.get("bad_note") or "slow path"
    good_label = visual.get("good_label") or "The right way"
    good_note = visual.get("good_note") or "stays fast"

    card_h = 112
    gap = 46
    lf = load_font("DejaVuSans-Bold.ttf", 27)
    nf = load_font("DejaVuSans.ttf", 22)

    def card(cy, label, note, color, sign):
        draw.rounded_rectangle([x, cy, x + w, cy + card_h], radius=16,
                               fill=blend(PANEL_BG, color, 0.16), outline=color, width=2)
        # status mark: x for the slow path, check for the fast one
        mx, my, r = x + 38, cy + card_h / 2, 17
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill=color)
        if sign == "bad":
            draw.line([(mx - 7, my - 7), (mx + 7, my + 7)], fill=(20, 8, 8), width=4)
            draw.line([(mx - 7, my + 7), (mx + 7, my - 7)], fill=(20, 8, 8), width=4)
        else:
            draw.line([(mx - 8, my), (mx - 2, my + 7), (mx + 8, my - 7)],
                      fill=(6, 24, 14), width=4, joint="curve")
        for line in wrap_text(draw, label, lf, w - 100)[:1]:
            draw.text((x + 68, cy + 24), line, font=lf, fill=WHITE)
        for line in wrap_text(draw, note, nf, w - 100)[:1]:
            draw.text((x + 68, cy + 60), line, font=nf, fill=color)

    card(y, bad_label, bad_note, ALERT_RED, "bad")
    arrow_y = y + card_h + gap / 2
    draw_down_arrow(draw, x + w / 2, y + card_h + 8, arrow_y + 12, color=ACCENT)
    card(y + card_h + gap, good_label, good_note, CHECK_GREEN, "good")
    return y + 2 * card_h + gap


def draw_cover_code_diff(draw, x, y, w, visual):
    """Cover visual for code-shaped topics: a real before/after code line
    (the actual bug and its fix), not a generic label - so the cover reflects
    today's specific topic instead of a fixed template."""
    bad_label = visual.get("bad_label") or "Before"
    good_label = visual.get("good_label") or "After"
    code_before = visual.get("code_before") or "// buggy line"
    code_after = visual.get("code_after") or "// fixed line"

    card_h = 230
    draw.rounded_rectangle([x, y, x + w, y + card_h], radius=20,
                           fill=blend(PANEL_BG, ACCENT, 0.10), outline=PANEL_BORDER, width=2)
    tag_f = load_font("DejaVuSans-Bold.ttf", 19)
    code_f = load_font("DejaVuSansMono-Bold.ttf", 24)

    def code_row(cy, tag, code, color):
        draw.rounded_rectangle([x + 18, cy, x + 18 + draw.textlength(tag, font=tag_f) + 20, cy + 26],
                               radius=8, fill=blend(PANEL_BG, color, 0.35))
        draw.text((x + 28, cy + 3), tag, font=tag_f, fill=color)
        rows_y = cy + 34
        draw.rounded_rectangle([x + 18, rows_y, x + w - 18, rows_y + 44], radius=10,
                               fill=(5, 11, 28), outline=blend(PANEL_BG, color, 0.4), width=1)
        for line in wrap_text(draw, code, code_f, w - 56)[:1]:
            draw_code_line(draw, x + 30, rows_y + 9, line, code_f)
        return rows_y + 44

    row1_bottom = code_row(y + 18, bad_label.upper(), code_before, ALERT_RED)
    arrow_y = row1_bottom + 22
    draw_down_arrow(draw, x + w / 2, row1_bottom + 4, arrow_y, color=ACCENT)
    code_row(arrow_y + 8, good_label.upper(), code_after, CHECK_GREEN)
    return y + card_h


def draw_cover_diagram(draw, x, y, w, visual):
    """Cover visual for concept-shaped topics: a short connected step-flow
    (2-4 chips) describing today's mechanism, instead of a generic label."""
    steps = [str(s) for s in (visual.get("diagram_steps") or [])][:4]
    if not steps:
        steps = [visual.get("bad_label") or "The problem", visual.get("good_label") or "The fix"]

    n = len(steps)
    chip_h, gap = 52, 20
    card_h = n * chip_h + (n - 1) * gap + 40
    draw.rounded_rectangle([x, y, x + w, y + card_h], radius=20,
                           fill=blend(PANEL_BG, ACCENT, 0.10), outline=PANEL_BORDER, width=2)
    chip_f = load_font("DejaVuSans-Bold.ttf", 22)
    cy = y + 20
    for i, step in enumerate(steps):
        last = i == n - 1
        tone = CHECK_GREEN if last else ACCENT
        draw.rounded_rectangle([x + 20, cy, x + w - 20, cy + chip_h], radius=14,
                               fill=blend(PANEL_BG, tone, 0.18), outline=tone, width=2)
        for line in wrap_text(draw, step, chip_f, w - 76)[:1]:
            tw = draw.textlength(line, font=chip_f)
            draw.text((x + (w - tw) / 2, cy + (chip_h - 26) / 2), line, font=chip_f, fill=WHITE)
        cy += chip_h
        if not last:
            draw_down_arrow(draw, x + w / 2, cy + 2, cy + gap - 2, color=ACCENT)
            cy += gap
    return y + card_h


def draw_cover_visual(draw, x, y, w, visual):
    """Dispatches to the cover-visual style Gemini chose for today's topic
    (code-diff or step-diagram), falling back to the older two-card
    before/after layout if the response didn't include either."""
    style = visual.get("style")
    if not style:
        style = "code" if visual.get("code_before") else ("diagram" if visual.get("diagram_steps") else "cards")
    if style == "code":
        return draw_cover_code_diff(draw, x, y, w, visual)
    if style == "diagram":
        return draw_cover_diagram(draw, x, y, w, visual)
    return draw_cover_compare(draw, x, y, w, visual)


def render_title_slide(slide, index, total, out_path, variant=None):
    variant = variant or COVER_VARIANTS[0]
    side = variant.get("side", "right")
    pose = variant.get("pose", "pointing")
    mirror = side == "left"

    img = make_background(seed=f"{TODAY}-title")
    draw = ImageDraw.Draw(img)
    kicker = slide.get("kicker", "JS DEEP DIVE")
    draw_chrome(draw, index, total, kicker=kicker)

    margin = 60
    title_font = load_font("DejaVuSans-Bold.ttf", 62)
    subtitle_font = load_font("DejaVuSans.ttf", 30)

    # "ADVANCED" difficulty badge, right after the kicker pill
    tag_font = load_font("DejaVuSans-Bold.ttf", 26)
    kicker_w = draw.textlength(kicker, font=tag_font) + 40
    badge_text = "ADVANCED"
    badge_w = draw.textlength(badge_text, font=tag_font) + 34
    badge_x = margin + kicker_w + 14
    draw.rounded_rectangle([badge_x, 90, badge_x + badge_w, 90 + 46], radius=23,
                           fill=WARN_ORANGE)
    draw.text((badge_x + 17, 90 + 10), badge_text, font=tag_font, fill=(40, 16, 4))

    # Red alert banner, like the reference cover's warning strip
    y = 158
    alert = slide.get("alert")
    if alert:
        af = load_font("DejaVuSans-Bold.ttf", 30)
        aw = draw.textlength(alert, font=af) + 96
        draw.rounded_rectangle([margin, y, margin + aw, y + 56], radius=14,
                               fill=ALERT_RED)
        # warning mark
        wx, wy = margin + 36, y + 28
        draw.polygon([(wx, wy - 15), (wx + 15, wy + 12), (wx - 15, wy + 12)], fill=(255, 240, 220))
        draw.line([(wx, wy - 6), (wx, wy + 4)], fill=ALERT_RED, width=4)
        draw.ellipse([wx - 2, wy + 7, wx + 2, wy + 11], fill=ALERT_RED)
        draw.text((margin + 62, y + 12), alert, font=af, fill=(255, 245, 240))
        y += 78

    # Headline in warm amber so it pops off the blue, like the reference
    for line in wrap_text(draw, slide.get("title", ""), title_font, W - 2 * margin)[:3]:
        draw.text((margin, y), line, font=title_font, fill=HEAD_AMBER)
        y += 72
    y += 8
    for line in wrap_text(draw, slide.get("subtitle", ""), subtitle_font, W - 2 * margin)[:2]:
        draw.text((margin, y), line, font=subtitle_font, fill=WHITE)
        y += 40

    swipe_font = load_font("DejaVuSans-Bold.ttf", 27)
    draw.text((margin, y + 16), "SWIPE TO LEARN  →", font=swipe_font, fill=ACCENT)

    # Mascot on today's chosen side, "presenting" the before/after visual on
    # the opposite side of him. Layout + pose rotate daily via `variant`.
    avatar_h = 620
    avatar = load_avatar_cutout(avatar_h, path=get_avatar_pose(pose))
    avatar_w = avatar.width if avatar else 300
    if mirror:
        ax = margin - 26
    else:
        ax = W - margin - avatar_w + 26
    ay = H - 140 - avatar_h

    compare_w = 460
    visual = slide.get("cover_visual") or {}
    v_style = visual.get("style") or ("code" if visual.get("code_before") else
                                       ("diagram" if visual.get("diagram_steps") else "cards"))
    if v_style == "diagram":
        n_steps = max(2, min(4, len(visual.get("diagram_steps") or []) or 2))
        compare_h = n_steps * 52 + (n_steps - 1) * 20 + 40
    elif v_style == "cards":
        compare_h = 2 * 112 + 46
    else:
        compare_h = 230
    compare_top = H - 170 - compare_h
    compare_x = (W - margin - compare_w) if mirror else margin
    draw_cover_visual(draw, compare_x, compare_top, compare_w, visual)

    if avatar:
        # soft spotlight so he stands out from the busy background
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gcx, gcy = ax + avatar_w / 2, ay + avatar_h * 0.62
        gd.ellipse([gcx - 250, gcy - 300, gcx + 250, gcy + 300],
                   fill=blend((0, 0, 0), ACCENT, 0.18))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=60))
        img = ImageChops.add(img, glow)
        draw = ImageDraw.Draw(img)

        # bubble beside his head, tail pointing at his face, sitting clear
        # above the comparison visual
        bubble_font = load_font("DejaVuSans-Bold.ttf", 25)
        line = slide.get("avatar_line") or "Hey Devs! Let me show you what actually goes wrong here."
        head_cy = ay + 112
        bubble_side = "left" if mirror else "right"
        anchor_x = (ax + avatar_w - 26) if mirror else (ax + 26)
        b_left, b_top, b_h, b_right = draw_side_speech_bubble(
            draw, anchor_x, head_cy, line, bubble_font, box_w=430, side=bubble_side
        )

        img.paste(avatar, (ax, ay), avatar)
        draw = ImageDraw.Draw(img)

        # smooth curved pointer from under the bubble down to the visual, so
        # the mascot reads as presenting it, not just standing
        if mirror:
            x0, y0 = b_right - 80, b_top + b_h + 6
            x1 = x0 + 40
            x2 = compare_x + compare_w * 0.55
        else:
            x0, y0 = b_left + 80, b_top + b_h + 6
            x1 = x0 - 40
            x2 = compare_x + compare_w * 0.45
        y2 = compare_top - 14
        y1 = (y0 + y2) / 2
        pts = []
        for i in range(21):
            t = i / 20
            pts.append((
                (1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t * t * x2,
                (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t * t * y2,
            ))
        draw.line(pts, fill=ACCENT, width=6, joint="curve")
        draw.polygon([(x2 - 15, y2 - 16), (x2 + 15, y2 - 16), (x2, y2 + 8)], fill=ACCENT)

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


CODE_KEYWORDS = {
    "function", "const", "let", "var", "return", "if", "else", "for", "while",
    "await", "async", "new", "class", "import", "export", "from", "try",
    "catch", "finally", "throw", "typeof", "of", "in", "=>", "default",
}
KW_COLOR = (147, 170, 255)
STR_COLOR = (255, 186, 126)
NUM_COLOR = (255, 214, 140)
PUNCT_COLOR = (150, 176, 214)


def draw_code_line(draw, x, y, line, font):
    """Monospace line with light syntax colouring, so code blocks read like a
    real editor rather than a flat grey wall."""
    for token in re.findall(r"'[^']*'|\"[^\"]*\"|\w+|\s+|[^\w\s]", line):
        if token.isspace():
            x += draw.textlength(token, font=font)
            continue
        if token[:1] in "'\"":
            color = STR_COLOR
        elif token in CODE_KEYWORDS:
            color = KW_COLOR
        elif token.replace(".", "").isdigit():
            color = NUM_COLOR
        elif not token[:1].isalnum() and token[:1] != "_":
            color = PUNCT_COLOR
        else:
            color = CODE_TEXT
        draw.text((x, y), token, font=font, fill=color)
        x += draw.textlength(token, font=font)


NEGATIVE_HINTS = (
    "slow", "slower", "leak", "leaked", "crash", "break", "breaks", "broken",
    "fail", "fails", "bug", "incident", "error", "problem", "worse", "lost",
    "stale", "wrong", "deopt", "blocked", "stuck", "hang", "freeze", "climbs",
    "grows", "retain", "retains", "spike", "drop", "drops", "never", "can't",
    "cannot", "unexpected", "wasted", "dict mode", "evict",
)


def is_negative(text):
    """Rough sentiment check so failures don't get a cheerful green tick."""
    t = str(text).lower()
    return any(hint in t for hint in NEGATIVE_HINTS)


def draw_cross_badge(draw, cx, cy, r=16, fill=ALERT_RED):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    d = r * 0.42
    draw.line([(cx - d, cy - d), (cx + d, cy + d)], fill=(26, 8, 8), width=max(3, int(r / 5)))
    draw.line([(cx - d, cy + d), (cx + d, cy - d)], fill=(26, 8, 8), width=max(3, int(r / 5)))


def draw_status_badge(draw, cx, cy, r, negative):
    if negative:
        draw_cross_badge(draw, cx, cy, r=r)
    else:
        draw_check_badge(draw, cx, cy, r=r)


def draw_down_arrow(draw, cx, y0, y1, color=ACCENT):
    draw.line([(cx, y0), (cx, y1 - 5)], fill=color, width=3)
    draw.polygon([(cx - 6, y1 - 7), (cx + 6, y1 - 7), (cx, y1 + 1)], fill=color)


def _panel_fonts(s):
    return {
        "code": load_font("DejaVuSansMono.ttf", max(13, int(20 * s))),
        "out": load_font("DejaVuSansMono.ttf", max(13, int(21 * s))),
        "chip": load_font("DejaVuSans-Bold.ttf", max(13, int(20 * s))),
        "res": load_font("DejaVuSans-Bold.ttf", max(13, int(19 * s))),
    }


def panel_metrics(draw, panel, content_w, s):
    """Returns (height_needed, fits_width, extras) for this panel at scale `s`.
    Width matters as much as height: picking a scale on height alone is what
    made long code lines and output rows spill past the panel border."""
    fonts = _panel_fonts(s)
    kind = panel.get("kind", "flow")
    lines = [str(l) for l in panel.get("lines", [])][:5]
    if not lines:
        lines = [""]
    n = len(lines)
    fits = True
    extras = {}

    if kind == "code":
        inner_w = content_w - int(28 * s)
        widest = max(draw.textlength(l, font=fonts["code"]) for l in lines)
        fits = widest <= inner_w
        h = n * int(28 * s) + int(30 * s)

    elif kind == "output":
        r = max(8, int(12 * s))
        text_w = content_w - (2 * r + 20) - 14
        widest = max(draw.textlength(l, font=fonts["out"]) for l in lines)
        fits = widest <= text_w
        h = n * int(42 * s) + int(16 * s)

    else:  # flow chips: allow up to two wrapped lines per chip
        text_w = content_w - int(28 * s)
        line_h = int(26 * s)
        wrapped_all, max_lines = [], 1
        for line in lines:
            wrapped = wrap_text(draw, line, fonts["chip"], text_w)
            if len(wrapped) > 2:
                fits = False
                wrapped = wrapped[:2]
            wrapped_all.append(wrapped)
            max_lines = max(max_lines, len(wrapped))
        chip_h = max_lines * line_h + int(20 * s)
        extras = {"wrapped": wrapped_all, "chip_h": chip_h, "line_h": line_h}
        h = n * chip_h + (n - 1) * int(26 * s)

    result = panel.get("result")
    if result:
        h += int(40 * s)
        if draw.textlength(result, font=fonts["res"]) + int(60 * s) > content_w:
            fits = False

    plain = panel.get("plain")
    if plain:
        plain_font = load_font("DejaVuSans-Oblique.ttf", 16) or load_font("DejaVuSans.ttf", 16)
        plain_lines = wrap_text(draw, plain, plain_font, content_w)[:2]
        extras["plain_lines"] = plain_lines
        h += len(plain_lines) * 20 + 12

    return h, fits, extras


def panel_content_height(draw, panel, content_w, s):
    return panel_metrics(draw, panel, content_w, s)[0]


def render_panel(draw, x, y, w, h, panel):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=18, fill=PANEL_BG,
                           outline=PANEL_BORDER, width=2)
    # Header strip, so the title reads as a label band like the reference.
    draw.rounded_rectangle([x + 2, y + 2, x + w - 2, y + 56], radius=16,
                           fill=blend(PANEL_BG, ACCENT, 0.10))
    draw.line([(x + 14, y + 56), (x + w - 14, y + 56)], fill=blend(PANEL_BG, ACCENT, 0.35), width=2)

    header_font = load_font("DejaVuSans-Bold.ttf", 24)
    panel_icon(draw, x + 34, y + 30, panel.get("kind", "flow"))
    draw.text((x + 58, y + 16), panel.get("title", ""), font=header_font, fill=WHITE)

    pad = 20
    content_x = x + pad
    content_w = w - 2 * pad
    area_top = y + 66
    area_h = h - 66 - 14

    lines = [str(l) for l in panel.get("lines", [])][:5]
    kind = panel.get("kind", "flow")
    result = panel.get("result")

    # Pick the largest scale whose content fits BOTH vertically and
    # horizontally, so sparse content grows to fill the panel while long code
    # lines shrink instead of spilling past the border.
    candidates = (2.5, 2.2, 2.0, 1.85, 1.7, 1.55, 1.4, 1.3, 1.2, 1.1, 1.0,
                  0.92, 0.85, 0.78, 0.7, 0.62, 0.55)
    s, needed, extras = candidates[-1], None, {}
    for candidate in candidates:
        h_need, fits, ex = panel_metrics(draw, panel, content_w, candidate)
        if fits and h_need <= area_h:
            s, needed, extras = candidate, h_need, ex
            break
    if needed is None:  # nothing fit cleanly - use the smallest and clip gracefully
        needed, _, extras = panel_metrics(draw, panel, content_w, s)

    fonts = _panel_fonts(s)
    cy = area_top + max(0, (area_h - needed) // 2)

    if kind == "code":
        code_font = fonts["code"]
        line_h = int(28 * s)
        block_h = len(lines) * line_h + int(30 * s)
        draw.rounded_rectangle([content_x, cy, x + w - pad, cy + block_h], radius=12,
                               fill=(5, 11, 28), outline=blend(PANEL_BG, ACCENT, 0.30), width=1)
        ty = cy + int(15 * s)
        for line in lines:
            draw_code_line(draw, content_x + int(14 * s), ty, line, code_font)
            ty += line_h
        cy += block_h

    elif kind == "output":
        line_font = fonts["out"]
        item_h = int(42 * s)
        r = max(8, int(12 * s))
        for line in lines:
            neg = is_negative(line)
            tint = ALERT_RED if neg else ACCENT
            draw.rounded_rectangle([content_x, cy, x + w - pad, cy + item_h - int(8 * s)],
                                   radius=10, fill=blend(PANEL_BG, tint, 0.09))
            draw_status_badge(draw, content_x + r + 8, cy + (item_h - int(8 * s)) // 2, r, neg)
            draw.text((content_x + 2 * r + 20, cy + int(7 * s)), line, font=line_font, fill=WHITE)
            cy += item_h

    else:  # flow / mechanics: chips chained with real arrows between them
        item_font = fonts["chip"]
        chip_h = extras.get("chip_h", int(46 * s))
        line_h = extras.get("line_h", int(26 * s))
        wrapped_all = extras.get("wrapped") or [[l] for l in lines]
        arrow_h = int(26 * s)
        for i, wrapped in enumerate(wrapped_all):
            last = i == len(wrapped_all) - 1
            neg = is_negative(" ".join(wrapped))
            if last:
                tone = ALERT_RED if neg else CHECK_GREEN
            else:
                tone = ALERT_RED if neg else ACCENT
            chip_fill = blend(PANEL_BG, tone, 0.17)
            chip_edge = tone
            draw.rounded_rectangle([content_x, cy, x + w - pad, cy + chip_h], radius=12,
                                   fill=chip_fill, outline=chip_edge, width=2)
            ty = cy + (chip_h - len(wrapped) * line_h) / 2
            for part in wrapped:
                tw = draw.textlength(part, font=item_font)
                draw.text((content_x + (content_w - tw) / 2, ty), part, font=item_font, fill=WHITE)
                ty += line_h
            cy += chip_h
            if not last:
                draw_down_arrow(draw, x + w / 2, cy + 4, cy + arrow_h - 2)
                cy += arrow_h

    if result:
        rf = fonts["res"]
        neg = is_negative(result)
        tone = ALERT_RED if neg else CHECK_GREEN
        badge_w = draw.textlength(result, font=rf) + int(52 * s)
        bh = int(34 * s)
        by = min(cy + int(8 * s), y + h - bh - 10)
        bx = x + (w - badge_w) / 2
        draw.rounded_rectangle([bx, by, bx + badge_w, by + bh], radius=bh / 2,
                               fill=blend(PANEL_BG, tone, 0.22), outline=tone, width=2)
        draw_status_badge(draw, bx + int(19 * s), by + bh / 2, max(8, int(11 * s)), neg)
        draw.text((bx + int(36 * s), by + (bh - int(22 * s)) / 2), result, font=rf, fill=tone)
        cy = by + bh

    plain_lines = extras.get("plain_lines")
    if plain_lines:
        plain_font = load_font("DejaVuSans-Oblique.ttf", 16) or load_font("DejaVuSans.ttf", 16)
        py = min(cy + 10, y + h - len(plain_lines) * 20 - 8)
        for line in plain_lines:
            draw.text((content_x, py), line, font=plain_font, fill=MUTED)
            py += 20


def draw_series_banner(draw, cy, text="JAVASCRIPT DEEP DIVE SERIES"):
    """Centred series banner, like the reference slide's bottom strip."""
    font = load_font("DejaVuSans-Bold.ttf", 22)
    tw = draw.textlength(text, font=font)
    bw, bh = tw + 96, 44
    bx = (W - bw) / 2
    draw.rounded_rectangle([bx, cy, bx + bw, cy + bh], radius=bh / 2,
                           fill=blend(GRAD_BOTTOM, ACCENT, 0.22), outline=ACCENT, width=2)
    # small spark/star mark on the left of the label
    sx, sy = bx + 30, cy + bh / 2
    for dx, dy in ((0, -11), (0, 11), (-11, 0), (11, 0)):
        draw.line([(sx, sy), (sx + dx, sy + dy)], fill=ACCENT, width=3)
    draw.ellipse([sx - 4, sy - 4, sx + 4, sy + 4], fill=WHITE)
    draw.text((bx + 52, cy + (bh - 26) / 2), text, font=font, fill=WHITE)


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
    grid_bottom = H - 150  # leave room for the series banner + footer
    gap = 22
    panel_w = (W - 2 * margin - gap) / 2
    avail = grid_bottom - grid_top - gap

    # Row heights follow the content. Each panel's "ideal" height is what it
    # needs at the largest scale its text can take without overflowing; a row
    # never grows past that, so short content doesn't get a huge empty box.
    def panel_ideal(panel):
        for candidate in (2.5, 2.2, 2.0, 1.85, 1.7, 1.55, 1.4, 1.3, 1.2, 1.1, 1.0):
            h, fits, _ = panel_metrics(draw, panel, panel_w - 40, candidate)
            if fits:
                return h + 86
        return 260

    row_ideal = [max(panel_ideal(panels[r * 2]), panel_ideal(panels[r * 2 + 1])) for r in range(2)]
    total_ideal = sum(row_ideal)

    if total_ideal <= avail:
        row_h = row_ideal
        grid_top += (avail - total_ideal) * 0.35  # sit nearer the heading, not dead centre
    else:
        row_h = [avail * (n / total_ideal) for n in row_ideal]

    for i, panel in enumerate(panels):
        col, row = i % 2, i // 2
        px = margin + col * (panel_w + gap)
        py = grid_top + (0 if row == 0 else row_h[0] + gap)
        render_panel(draw, px, py, panel_w, row_h[row], panel)

    # Connect the 4 panels into a visual flow: -> across each row, v down each column
    hgap_x = margin + panel_w + gap / 2
    draw_flow_chevron(draw, hgap_x, grid_top + row_h[0] / 2, "right")
    draw_flow_chevron(draw, hgap_x, grid_top + row_h[0] + gap + row_h[1] / 2, "right")

    vgap_y = grid_top + row_h[0] + gap / 2
    draw_flow_chevron(draw, margin + panel_w / 2, vgap_y, "down")
    draw_flow_chevron(draw, margin + panel_w + gap + panel_w / 2, vgap_y, "down")

    draw_series_banner(draw, H - 126)
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
    y += box_h + 56

    # Action chips fill the lower half instead of leaving it blank.
    chip_font = load_font("DejaVuSans-Bold.ttf", 30)
    chips = [("SAVE", "for later"), ("SHARE", "with your team"), ("FOLLOW", "for daily posts")]
    chip_w = (W - 2 * margin - 2 * 20) / 3
    for i, (head, sub) in enumerate(chips):
        cx0 = margin + i * (chip_w + 20)
        draw.rounded_rectangle([cx0, y, cx0 + chip_w, y + 132], radius=16,
                               fill=blend(PANEL_BG, ACCENT, 0.10), outline=PANEL_BORDER, width=2)
        draw_check_badge(draw, cx0 + chip_w / 2, y + 36, r=18)
        hw = draw.textlength(head, font=chip_font)
        draw.text((cx0 + (chip_w - hw) / 2, y + 62), head, font=chip_font, fill=WHITE)
        sf = load_font("DejaVuSans.ttf", 21)
        sw = draw.textlength(sub, font=sf)
        draw.text((cx0 + (chip_w - sw) / 2, y + 98), sub, font=sf, fill=MUTED)

    draw_series_banner(draw, H - 126)
    img.save(out_path, "PNG")


def render_slide(slide, index, total, out_path, variant=None, topic=None):
    # Preferred path: let the image model lay out the whole slide (background
    # from the reference image + infographics + text). If that isn't available
    # for this account, or fails for this slide, fall back to the procedural
    # PIL renderer below, which always works.
    if SLIDE_IMAGE_MODE == "ai":
        ai_img = generate_slide_image_ai(slide, index, total, variant=variant, topic=topic)
        if ai_img is not None:
            ai_img.save(out_path, "PNG")
            return
        print(
            f"AI slide {index}/{total}: falling back to the procedural renderer.",
            file=sys.stderr,
        )

    kind = slide.get("type", "content")
    if kind == "title":
        render_title_slide(slide, index, total, out_path, variant=variant)
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


# ---------------------------------------------------------------------------
# Trending signal: best-effort pull of what's actually being discussed today
# in the JS/React/Next.js community, so topic selection can lean toward
# genuine current buzz instead of only the model's trained sense of what's
# "typically" interesting. Both sources are free/public, no API key needed.
# If both fail (network, rate limit, blocked), choose_topic() just proceeds
# without this context - it's a bonus signal, never a hard dependency.
# ---------------------------------------------------------------------------
TRENDING_SUBREDDITS = ("javascript", "reactjs", "nextjs")


def fetch_trending_signals():
    signals = []
    headers = {"User-Agent": "modernjavascripthub-daily-poster/1.0"}

    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "tags": "story",
                "query": "javascript OR react OR nextjs OR typescript OR v8",
                "hitsPerPage": 20,
            },
            timeout=15,
        )
        if resp.ok:
            for hit in resp.json().get("hits", [])[:20]:
                title = (hit.get("title") or "").strip()
                if title:
                    signals.append(f"[Hacker News] {title}")
        else:
            print(f"Trending signal: Hacker News returned {resp.status_code}.", file=sys.stderr)
    except requests.RequestException as e:
        print(f"Trending signal: Hacker News fetch failed: {e}", file=sys.stderr)

    for sub in TRENDING_SUBREDDITS:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/top.json",
                params={"t": "day", "limit": 8},
                headers=headers,
                timeout=15,
            )
            if resp.ok:
                for child in resp.json().get("data", {}).get("children", []):
                    title = (child.get("data", {}).get("title") or "").strip()
                    if title:
                        signals.append(f"[r/{sub}] {title}")
            else:
                print(f"Trending signal: r/{sub} returned {resp.status_code}.", file=sys.stderr)
        except requests.RequestException as e:
            print(f"Trending signal: r/{sub} fetch failed: {e}", file=sys.stderr)

    return signals[:35]


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

    trending = fetch_trending_signals()
    if trending:
        trending_block = (
            "Here is what's ACTUALLY being discussed today in the JS/React/"
            "Next.js community (real titles from Hacker News + Reddit, pulled "
            "minutes ago) - use these only as a signal for what topics/themes "
            "are currently generating real interest and engagement, so today's "
            "pick is more likely to resonate and get shared. Do not just copy "
            "a title or turn it into a news recap - find the underlying "
            "advanced JS/React/Next.js concept behind the buzz and turn THAT "
            "into our narrow, commonly-misunderstood-bug format:\n"
            + "\n".join(f"- {t}" for t in trending)
        )
    else:
        trending_block = (
            "(No live trending data available today - pick based on what "
            "generally drives high engagement/shares in the JS/React/Next.js "
            "developer community.)"
        )

    prompt = f"""You are picking today's topic for "Modern JavaScript Hub", an
Instagram/Telegram account teaching {SUBJECTS} to intermediate/senior
developers. Growing this account's reach matters as much as technical depth,
so favor topics that are genuinely likely to be shared/saved, not just
correct.

The topic must be ADVANCED and NARROW - never a beginner definition or a
broad category name. Pick ONE of these three flavours (vary across days,
leaning toward (c) whenever the trending signal below gives you something
strong to work with):

  (a) A specific commonly-misunderstood behavior or mistake experienced
      developers actually make in real code.
  (b) Something NEW or RECENTLY CHANGED in the ecosystem that experienced
      developers are still getting wrong or haven't adopted yet - a recent
      JavaScript language feature, a newly stable/changed React or Next.js
      API, a recently changed default or deprecation, or a modern replacement
      for an older pattern people still use out of habit. Only pick things
      you are actually confident exist and are correct about - if you are not
      certain a feature shipped or how it behaves, choose flavour (a)
      instead. Never invent a release, version number, or API.
  (c) The advanced concept underneath whatever is genuinely trending right
      now in the JS/React/Next.js community today (see the real discussion
      titles below) - still narrow and technically precise, just chosen
      because it's currently top-of-mind for developers, which tends to get
      more shares/saves/comments.

For calibration, here is the STYLE and DEPTH expected (these are flavour (a)
examples; do not just reuse them, they're only examples):
{examples_block}

{trending_block}

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
like a sharp senior engineer explaining a bug in code review, in plain
language a reader can follow on a quick scroll. Keep the TOPIC advanced and
narrow, but keep the EXPLANATION simple: prefer short, everyday words and a
quick analogy over dense jargon, and always pair any technical term (hidden
class, tree-shaking, memoization, etc.) with a one-clause plain-English
translation the first time it's used. Every claim must be technically
correct per the research below.

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
      "alert": "a 3-6 word red-warning banner line for the top of the cover,
        phrased as a blunt warning, e.g. 'Stop Writing useEffect Like This!'
        or 'This Kills Your Render Perf!'",
      "avatar_line": "what the mascot character says in a speech bubble - 12
        to 22 words, written as if he is personally talking to the viewer and
        about to show them something: an opener, the surprising fact, and a
        hook. e.g. 'Hey Devs! Did you know delete can make your objects up to
        10x slower? Let me show you why!'",
      "cover_visual": {{
        "style": "'code' if the bug/fix is best shown as one real short line of
          code before vs after, 'diagram' if it's better shown as a short
          chain of 2-4 concept steps that don't reduce to one code line -
          pick whichever genuinely fits THIS topic, don't default to the same
          one every time",
        "bad_label": "1-2 word tag for the 'before'/wrong side, e.g. 'Before'",
        "good_label": "1-2 word tag for the 'after'/right side, e.g. 'After'",
        "code_before": "REQUIRED if style=code: the actual short buggy code
          line for this topic, <=26 characters, e.g. 'delete obj.key'",
        "code_after": "REQUIRED if style=code: the actual short fixed code
          line for this topic, <=26 characters, e.g. 'obj.key = null'",
        "diagram_steps": "REQUIRED if style=diagram: an array of 2-4 short
          (<=20 character) step labels specific to this topic's actual
          mechanism, in order, e.g. ['Object has a shape', 'delete runs',
          'Shape invalidated', 'Falls to dict mode']"
      }}
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
        {{"title": "short 2-3 word panel label fitting this panel's role, e.g. 'Code Input', 'Buggy Code', 'The Fix', 'Console Output', 'Why It Happens', 'Real Impact', 'Pro Tip'", "kind": "code | flow | output (pick whichever best fits this panel's content)", "lines": ["for kind=code: up to 5 short code lines, <=30 chars each. for kind=flow: 2-4 short labels <=26 chars, sequential steps/facts. for kind=output: 2-4 short values/lines <=20 chars each"], "result": "optional short verified/result/takeaway label, <=22 chars", "plain": "ONE short plain-English sentence, <=14 words, NO code and NO jargon, explaining in everyday terms why this specific panel matters or what it means for the reader - written so a developer who is not deeply familiar with engine/runtime internals still gets the point"}}
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
  "caption_hook": "ONE single short sentence/question, <=18 words, naming
    the specific bug/topic in plain language, no jargon - this is ONLY the
    opening line, never a paragraph. WRONG: cramming the whole explanation
    into this one field. RIGHT: 'Ever deleted one key and watched your app
    get 10x slower?'",
  "caption_points": [
    "EXACTLY 3 to 4 array entries (never fewer, never merged into one
     string). Each entry is its OWN separate short sentence, <=15 words,
     ONE idea only, plain English, no code. Together they let someone fully
     get the concept without reading the slides. Example of the CORRECT
     shape (4 separate short array entries, not one paragraph):
     ['delete does not just remove a key - it changes the object internally.',
      'That change can push the object into a slower mode for good.',
      'Assigning undefined instead keeps the fast path intact.',
      'This is an easy mistake that quietly hurts performance.']"
  ],
  "caption_takeaway": "one short closing line, <=15 words, plain language,
    a clear call to action to save/share/follow - may start with a relevant
    emoji like 💡 or 👉",
  "seo_keywords": [
    "3 to 5 high-search-volume, evergreen keyword phrases developers
     actually search for around this exact topic (e.g. 'javascript
     interview questions', 'react performance optimization', 'nextjs best
     practices' - whichever genuinely fit this topic, don't force unrelated
     ones), as a flat array of short phrases, no # symbols"
  ],
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
    if not parsed.get("caption_hook") or not parsed.get("caption_points"):
        raise RuntimeError(f"Gemini response missing caption_hook/caption_points: {parsed}")
    parsed.setdefault("hashtags", [])
    parsed.setdefault("seo_keywords", [])
    parsed.setdefault("caption_takeaway", f"Follow {BRAND_HANDLE} for more like this.")
    return parsed


# ---------------------------------------------------------------------------
# Caption assembly (Instagram: rich SEO caption; Telegram: shorter variant)
# ---------------------------------------------------------------------------
def _split_sentences(text):
    text = (text or "").strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def _normalize_caption_parts(hook, points, takeaway):
    """Best-effort LOCAL enforcement of the bullet-point shape, since Gemini
    doesn't reliably keep captions short/separate even when the prompt asks
    for it - it can dump a whole paragraph into caption_hook, or return one
    long run-on caption_points entry. This guarantees the final caption is
    always genuinely bulleted, regardless of what shape Gemini's response
    actually came back in."""
    hook = str(hook or "").strip()
    points = [str(p).strip() for p in (points or []) if str(p).strip()]
    takeaway = str(takeaway or "").strip()

    # If "hook" is really a multi-sentence paragraph, keep only the first
    # sentence as the hook and feed the rest back in as points.
    hook_sentences = _split_sentences(hook)
    if len(hook_sentences) > 1:
        hook, extra = hook_sentences[0], hook_sentences[1:]
        points = extra + points
    elif len(hook_sentences) == 1:
        hook = hook_sentences[0]

    # Any point that's itself a run-on (multiple sentences, or one very long
    # sentence with no punctuation to split on) gets broken up further.
    expanded = []
    for p in points:
        sentences = _split_sentences(p)
        if len(sentences) > 1:
            expanded.extend(sentences)
        elif len(p.split()) > 22:
            clauses = [c.strip() for c in re.split(r",\s+| - |;\s+", p) if c.strip()]
            expanded.extend(clauses if len(clauses) > 1 else [p])
        else:
            expanded.append(p)

    return hook, expanded[:5], takeaway


def _build_caption_body(hook, points, takeaway):
    """Scannable bullet-point body: hook line, then a bullet per point, then
    a closing takeaway - instead of one dense paragraph."""
    hook, points, takeaway = _normalize_caption_parts(hook, points, takeaway)
    lines = [hook] if hook else []
    for p in points:
        lines.append(f"• {p}")
    if takeaway:
        lines.append(takeaway)
    return lines


def build_captions(caption_hook, caption_points, caption_takeaway, seo_keywords, hashtags):
    hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]
    seo_line = "[" + ", ".join(seo_keywords) + "]" if seo_keywords else ""
    body_lines = _build_caption_body(caption_hook, caption_points, caption_takeaway)

    # Layout, both platforms: hook -> bullet points -> takeaway -> blank ->
    # [SEO keywords] -> blank -> hashtags.
    #
    # Instagram's app collapses a plain blank line (no visible character on
    # it), so the usual fix is to put an invisible Braille-blank character
    # (U+2800) on that line instead of leaving it truly empty - it still
    # reads as a blank line to the viewer but survives Instagram's collapsing.
    ig_gap = "\n⠀\n"
    ig_body = "\n".join(body_lines)
    ig_parts = [ig_body]
    if seo_line:
        ig_parts.append(seo_line)
    ig_parts.append(" ".join(hashtags[:15]))
    ig = ig_gap.join(ig_parts)
    ig = ig[:2200]

    # Telegram doesn't collapse blank lines, so plain double newlines are enough.
    tg_gap = "\n\n"
    tg_body = "\n".join(body_lines)
    tg_parts = [tg_body]
    if seo_line:
        tg_parts.append(seo_line)
    tg_parts.append(" ".join(hashtags[:5]))
    tg = tg_gap.join(tg_parts)
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

    cover_variant = choose_cover_variant(history)
    print(f"Cover layout chosen: {cover_variant['id']}", file=sys.stderr)

    filenames = []
    for i, slide in enumerate(slides, start=1):
        fname = f"{TODAY}-slide{i}.png"
        out_path = os.path.join(IMAGES_DIR, fname)
        variant = cover_variant if slide.get("type") == "title" else None
        slide_topic = topic if slide.get("type") == "title" else None
        render_slide(slide, i, total, out_path, variant=variant, topic=slide_topic)
        filenames.append(fname)

    ig_caption, tg_caption = build_captions(
        carousel.get("caption_hook", ""),
        carousel.get("caption_points", []),
        carousel.get("caption_takeaway", ""),
        carousel.get("seo_keywords", []),
        carousel.get("hashtags", []),
    )

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

    history.append({"date": TODAY, "topic": topic, "cover_variant": cover_variant["id"]})
    save_topic_history(history)

    print(f"IMAGE_FILENAMES={','.join(filenames)}")
    print(f"Generated {total}-slide carousel for topic: {topic}", file=sys.stderr)


if __name__ == "__main__":
    main()
