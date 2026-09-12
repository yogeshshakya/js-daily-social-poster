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
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-flash-latest").strip()
if GEMINI_MODEL.startswith("models/"):
    GEMINI_MODEL = GEMINI_MODEL[len("models/"):]

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).strftime("%Y-%m-%d")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(REPO_ROOT, "images")
BUILD_DIR = os.path.join(REPO_ROOT, "build")
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")
AVATAR_PATH = os.path.join(ASSETS_DIR, "avatar.png")
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(BUILD_DIR, exist_ok=True)

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
# Background: gradient + faint circuit pattern + scattered tech glyphs
# ---------------------------------------------------------------------------
def make_background(seed):
    img = Image.new("RGB", (W, H), GRAD_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        draw.line([(0, y), (W, y)], fill=blend(GRAD_TOP, GRAD_BOTTOM, t))

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
def render_title_slide(slide, index, total, out_path):
    img = make_background(seed=f"{TODAY}-title")
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, index, total, kicker=slide.get("kicker", "JS DEEP DIVE"))

    margin = 60
    title_font = load_font("DejaVuSans-Bold.ttf", 66)
    subtitle_font = load_font("DejaVuSans.ttf", 34)

    y = 190
    title_lines = wrap_text(draw, slide["title"], title_font, W - 2 * margin)[:4]
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=WHITE)
        y += 78
    y += 16
    for line in wrap_text(draw, slide.get("subtitle", ""), subtitle_font, W - 2 * margin)[:3]:
        draw.text((margin, y), line, font=subtitle_font, fill=MUTED)
        y += 44

    # Mascot avatar, bottom area, above footer
    avatar_h = 600
    avatar = load_avatar_cutout(avatar_h)
    if avatar:
        ax = (W - avatar.width) // 2
        ay = H - 150 - avatar_h
        img.paste(avatar, (ax, ay), avatar)
        draw = ImageDraw.Draw(img)  # redraw handle after paste

    swipe_font = load_font("DejaVuSans-Bold.ttf", 30)
    swipe_text = "SWIPE TO LEARN  →"
    stw = draw.textlength(swipe_text, font=swipe_font)
    draw.text(((W - stw) / 2, H - 150 - avatar_h - 60), swipe_text, font=swipe_font, fill=ACCENT)

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
    draw_check_badge(draw, margin + 20, y + 30, r=20)
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
def gemini_call(payload, max_retries=5):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    delay = 20
    for attempt in range(1, max_retries + 1):
        resp = requests.post(url, json=payload, timeout=90)
        if resp.status_code == 429 and attempt < max_retries:
            print(
                f"Gemini rate-limited (429), retrying in {delay}s "
                f"(attempt {attempt}/{max_retries})...",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay = min(delay * 2, 120)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Gemini API kept returning 429 (rate limited) after all retries.")


def research_topic(topic):
    prompt = f"""Research this ADVANCED, commonly-misunderstood JavaScript/React/Next.js
topic - the kind experienced developers still get wrong in real code, not a
beginner definition. Topic: "{topic}"

Write a precise, technically accurate briefing (250-400 words) I will use to
build a step-by-step "here's the bug, here's why, here's the fix" carousel
post. Include: the exact buggy behavior/output, WHY it happens internally
(the actual mechanism, not a hand-wave), and the correct fix with what
changes internally. Do not invent APIs or behavior that doesn't exist.
Plain text only."""

    data = gemini_call(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
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
      "subtitle": "one short sentence promising the fix they'll learn"
    }},
    {{
      "type": "content",
      "heading": "3-5 word heading for this slide, e.g. 'Fix It With let'",
      "panels": [
        {{"title": "Code Input", "kind": "code", "lines": ["up to 5 short code lines, <=30 chars each, showing the buggy or example code"]}},
        {{"title": "Execution Flow", "kind": "flow", "lines": ["2-4 short labels (<=26 chars) showing what actually happens step by step"], "result": "short verified/result label, <=22 chars"}},
        {{"title": "Console Output", "kind": "output", "lines": ["2-4 short output values/lines, <=20 chars each"], "result": "short label, <=22 chars"}},
        {{"title": "Internal Mechanics", "kind": "flow", "lines": ["2-4 short labels (<=26 chars) explaining the WHY at the engine/runtime level"], "result": "short label, <=22 chars"}}
      ]
    }},
    ... exactly 2 of these "content" slides total (first = the buggy/naive
    version, second = the correct fix), each with exactly 4 panels as shown
    above ...,
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
    plain language, then a short call to action to save/share/follow. Do
    NOT include hashtags in this field.",
  "hashtags": [
    "8 to 12 hashtags as a flat array (include the # in each string), mixing:
     3-4 broad/high-volume (#JavaScript #WebDevelopment #Coding
     #Programming), 4-5 niche/specific to THIS exact topic, and 1-2
     community/branded (#DevCommunity #ModernJavaScriptHub)."
  ]
}}

All on-image text must be short and punchy - this is for a 1080x1350 image
panel, not an essay. Keep code lines under 30 characters so they don't get
truncated."""

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

    ig = caption.strip() + "\n\n" + " ".join(hashtags[:12])
    ig = ig[:2200]

    tg = caption.strip() + "\n\n" + " ".join(hashtags[:5])
    if len(tg) > 1024:
        tg = tg[:1000].rsplit(" ", 1)[0] + "…"

    return ig, tg


def main():
    print(f"Using Gemini model: {GEMINI_MODEL}", file=sys.stderr)
    topic = random.choice(TOPICS)
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

    print(f"IMAGE_FILENAMES={','.join(filenames)}")
    print(f"Generated {total}-slide carousel for topic: {topic}", file=sys.stderr)


if __name__ == "__main__":
    main()
