"""Generate today's post: calls Gemini for the text content, then renders a
1080x1080 image card with Pillow. Writes:
  - images/<DATE>.png   (the image to attach)
  - build/caption.txt   (the caption text to use on Telegram + Instagram)
  - build/meta.json     (raw structured content, useful for debugging)

No network calls other than Gemini. Designed to run inside GitHub Actions,
which has open internet access (unlike some sandboxed dev environments).
"""

import json
import os
import random
import re
import sys
import textwrap
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

from topics import TOPICS

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).strftime("%Y-%m-%d")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(REPO_ROOT, "images")
BUILD_DIR = os.path.join(REPO_ROOT, "build")
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(BUILD_DIR, exist_ok=True)

FONT_DIR_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
]


def find_font(filename):
    for d in FONT_DIR_CANDIDATES:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    # Fall back to PIL default (bitmap, low quality) if DejaVu isn't installed.
    return None


def load_font(filename, size):
    path = find_font(filename)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def call_gemini(topic):
    prompt = f"""You are writing a short, high-quality educational social media post
for a JavaScript/React/Next.js developer audience (Instagram + Telegram channel
called "Modern JavaScript Hub").

Topic to cover: {topic}

Return ONLY a JSON object (no markdown fences, no extra text) with these exact keys:
- "title": a punchy 4-8 word headline for the image card
- "tip": 2-3 sentences explaining the concept clearly and correctly, for
  intermediate developers. No fluff.
- "code": a short (max 6 lines) correct, runnable code snippet illustrating the
  point. Use "" if a snippet doesn't add value for this topic.
- "caption": the full social media caption (different from "tip" - can restate
  it more conversationally), 2-4 sentences, ending with 4-6 relevant hashtags
  (mix of #JavaScript #React #NextJS style and the specific topic). Do not
  include the code snippet in the caption.

Be technically accurate. Do not invent APIs that don't exist."""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Defensive fallback in case the model wraps JSON in fences anyway.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse Gemini response as JSON:\n{text}")
        parsed = json.loads(match.group(0))

    for key in ("title", "tip", "caption"):
        if key not in parsed or not parsed[key]:
            raise RuntimeError(f"Gemini response missing required key '{key}': {parsed}")
    parsed.setdefault("code", "")
    return parsed


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""
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
    return lines


def render_image(content, out_path):
    W, H = 1080, 1080
    bg = (13, 17, 23)  # GitHub-dark-ish background
    accent = (247, 223, 30)  # JS yellow
    fg = (230, 237, 243)
    dim = (139, 148, 158)
    code_bg = (22, 27, 34)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    title_font = load_font("DejaVuSans-Bold.ttf", 64)
    tip_font = load_font("DejaVuSans.ttf", 36)
    code_font = load_font("DejaVuSansMono.ttf", 30)
    brand_font = load_font("DejaVuSans-Bold.ttf", 32)
    tag_font = load_font("DejaVuSans-Bold.ttf", 28)

    margin = 70
    y = 90

    # Small kicker tag (sized to fit the text with padding)
    kicker_text = "JS / REACT TIP"
    kicker_pad_x, kicker_pad_y = 22, 12
    kicker_w = draw.textlength(kicker_text, font=tag_font) + 2 * kicker_pad_x
    kicker_h = 28 + 2 * kicker_pad_y
    draw.rectangle([margin, y, margin + kicker_w, y + kicker_h], fill=accent)
    draw.text((margin + kicker_pad_x, y + kicker_pad_y), kicker_text, font=tag_font, fill=(13, 17, 23))
    y += kicker_h + 50

    # Title
    title_lines = wrap_text(draw, content["title"], title_font, W - 2 * margin)
    for line in title_lines[:3]:
        draw.text((margin, y), line, font=title_font, fill=fg)
        y += 76
    y += 20

    # Tip paragraph
    tip_lines = wrap_text(draw, content["tip"], tip_font, W - 2 * margin)
    for line in tip_lines[:6]:
        draw.text((margin, y), line, font=tip_font, fill=dim)
        y += 46
    y += 30

    # Code block
    code = content.get("code") or ""
    if code.strip():
        code_lines = code.strip("\n").split("\n")[:8]
        block_h = 50 + len(code_lines) * 40
        draw.rounded_rectangle(
            [margin, y, W - margin, y + block_h], radius=16, fill=code_bg
        )
        cy = y + 25
        for line in code_lines:
            draw.text((margin + 30, cy), line[:60], font=code_font, fill=(126, 231, 135))
            cy += 40
        y += block_h + 30

    # Footer / brand
    draw.line([(margin, H - 130), (W - margin, H - 130)], fill=(48, 54, 61), width=2)
    draw.text((margin, H - 100), "@modernjavascripthub", font=brand_font, fill=accent)

    img.save(out_path, "PNG")


def main():
    topic = random.choice(TOPICS)
    content = call_gemini(topic)

    image_path = os.path.join(IMAGES_DIR, f"{TODAY}.png")
    render_image(content, image_path)

    caption_path = os.path.join(BUILD_DIR, "caption.txt")
    with open(caption_path, "w", encoding="utf-8") as f:
        f.write(content["caption"].strip() + "\n")

    meta_path = os.path.join(BUILD_DIR, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {"date": TODAY, "topic": topic, **content, "image_path": image_path},
            f,
            indent=2,
        )

    # Machine-readable output the workflow reads to know the image filename.
    print(f"IMAGE_FILENAME={TODAY}.png")
    print(f"Generated post for topic: {topic}", file=sys.stderr)


if __name__ == "__main__":
    main()
