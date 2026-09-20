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


# Human-readable label for each of the 8 new slide "type" values, used as the
# small kicker/eyebrow text on non-hook slides.
SLIDE_TYPE_LABEL = {
    "hook": "JS DEEP DIVE",
    "simple_explanation": "THE IDEA",
    "code_example": "CODE",
    "flow": "WHAT HAPPENS",
    "under_the_hood": "UNDER THE HOOD",
    "common_mistake": "COMMON MISTAKE",
    "better_approach": "BETTER APPROACH",
    "summary": "KEY TAKEAWAY",
}


def _slide_spec_text(slide, index, total, variant=None, topic=None):
    """Exact, unambiguous description of what must appear on this slide, for
    the flat schema (slide_number/type/title/content/code/visual_type/
    visual_story/infographic/highlight/design_emphasis)."""
    kind = slide.get("type", "simple_explanation")
    label = SLIDE_TYPE_LABEL.get(kind, "JS DEEP DIVE")

    if kind == "summary":
        spec = (
            f'This is the closing SUMMARY slide ({index} of {total}).\n'
            f'Text that must appear, spelled exactly:\n'
            f'  - small pill label: "{label}"\n'
            f'  - heading: "{slide.get("title", "")}"\n'
            f'  - body text: "{slide.get("content", "")}"\n'
        )
        if slide.get("highlight"):
            spec += f'  - highlighted takeaway line: "{slide["highlight"]}"\n'
        spec += f'  - bottom left handle: "{BRAND_HANDLE}"\n'
        return spec

    spec = (
        f'This is CONTENT slide {index} of {total} (visual format: '
        f'"{slide.get("visual_type", "")}"). No avatar/mascot on this slide - '
        f'the educational content and infographic are the visual focus.\n\n'
        f'Visual story to depict: {slide.get("visual_story", "")}\n'
    )
    if slide.get("infographic"):
        spec += f'Infographic instructions: {slide["infographic"]}\n'
    if slide.get("design_emphasis"):
        spec += f'Design emphasis: {slide["design_emphasis"]}\n'

    spec += (
        f'\nText that must appear, spelled exactly:\n'
        f'  - small pill label: "{label}"\n'
        f'  - slide heading at the top: "{slide.get("title", "")}"\n'
        f'  - body/explanation text: "{slide.get("content", "")}"\n'
    )
    if slide.get("code"):
        spec += f'  - code block, character-for-character identical:\n{slide["code"]}\n'
    if slide.get("highlight"):
        spec += f'  - a highlighted/emphasized short line: "{slide["highlight"]}"\n'
    spec += f'  - bottom left handle: "{BRAND_HANDLE}"\n'
    return spec


# User-authored prompt template for the COVER/THUMBNAIL slide specifically
# (content slides 2-7 and the summary slide still use the prompt built in
# generate_slide_image_ai() below - this template is only for the title
# slide). Only the bracketed placeholders are filled in per day; the
# wording is used exactly as given, unchanged.
THUMBNAIL_PROMPT_TEMPLATE = """Create a single finished Instagram carousel COVER/THUMBNAIL slide, portrait 4:5 (1080x1350), for a premium developer-education Instagram account.

The final image must look like a professionally art-directed technology editorial thumbnail, not like a generic Canva template.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. CORE CREATIVE RULE — EVERY THUMBNAIL MUST FEEL NEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is extremely important:

Every thumbnail must have a NEW and DISTINCT creative composition.

Do NOT repeatedly use:
- the same layout
- the same avatar position
- the same avatar pose
- the same facial expression
- the same background composition
- the same infographic arrangement
- the same text placement
- the same camera angle
- the same visual metaphor
- the same card structure
- the same lighting direction

The brand identity must remain consistent, but the creative execution must change significantly from thumbnail to thumbnail.

Think like a professional Instagram creative director creating a unique cover specifically for this topic.

The thumbnail should immediately communicate:
1. What is the topic?
2. Why should a developer care?
3. What makes this interesting or surprising?

The viewer should understand the basic idea within approximately 1 second.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2. CREATIVE STYLE ROTATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For THIS thumbnail, independently choose ONE creative direction that best fits the topic.

Possible creative directions:

A. CINEMATIC
- dramatic lighting
- cinematic depth
- large typography
- close-up or dynamic avatar pose
- strong foreground/background separation

B. DEVELOPER INVESTIGATION
- avatar investigating a technical problem
- magnifying glass
- hidden issue
- debugging/investigation atmosphere
- visual clues and technical evidence

C. BEFORE vs AFTER
- dramatic comparison
- fast vs slow
- correct vs incorrect
- old vs new
- healthy vs broken state

D. FUTURISTIC UI
- avatar interacting with a developer dashboard
- floating panels
- performance metrics
- futuristic interface
- holographic technical elements

E. COMIC / REACTION
- expressive avatar
- exaggerated reaction
- speech bubble
- visual storytelling
- humorous but professional developer aesthetic

F. 3D TECH WORLD
- oversized JavaScript/code objects
- strong perspective
- depth
- floating technical elements
- avatar interacting with the environment

G. BREAKING TECH NEWS
- editorial / breaking-news visual language
- large warning or discovery
- dramatic headline
- technical alert elements

H. MINIMAL EDITORIAL
- fewer visual elements
- very strong typography
- one powerful technical metaphor
- premium minimal composition

I. VISUAL METAPHOR
- explain the technical concept through a creative metaphor
- make the concept visually understandable without relying entirely on text

J. DEBUGGING SCENARIO
- avatar discovers or fixes a technical issue
- error indicators
- code/environment
- clear problem → consequence relationship

Choose ONE direction.

Do not combine all styles.

The chosen direction should be different from the obvious/template-like composition whenever possible.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3. BRAND VISUAL IDENTITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use the attached background image ONLY as a brand-style reference.

Preserve the overall brand language:
- deep navy / dark blue environment
- futuristic developer atmosphere
- subtle technology aesthetic
- subtle circuit-board / digital texture
- cyan / electric blue accents
- premium dark UI feeling
- high contrast
- clean professional developer aesthetic

IMPORTANT:

Do NOT reproduce the attached background exactly.

Create a fresh background specifically for this thumbnail.

You may change:
- circuit pattern
- glow placement
- lighting direction
- depth
- perspective
- technical elements
- background geometry
- atmospheric effects
- visual focal points

The background should feel like it belongs to the same brand family while still being visually new.

Keep the background dark enough for excellent text readability.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4. AVATAR — MAKE THE AVATAR PART OF THE STORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The second attached image contains the mascot/avatar.

Use the EXACT character design from the attached image.

Preserve:
- face
- hairstyle
- clothing
- colours
- character identity
- proportions
- overall character design
- recognizable visual features

Remove the original background cleanly.

DO NOT redesign the character.

However, the avatar's:
- pose
- body orientation
- hand gesture
- facial expression
- scale
- camera angle
- perspective
- placement

may change creatively.

IMPORTANT:

Do NOT simply place the avatar standing beside the text.

The avatar must be an ACTIVE VISUAL STORYTELLING ELEMENT.

The avatar should look like they are:
- discovering something
- explaining something
- warning the viewer
- investigating something
- reacting to something
- interacting with code
- interacting with an infographic
- pointing toward an important detail
- demonstrating a concept
- discovering a hidden problem
- comparing two states
- using a developer tool/interface

Possible avatar treatments:
- pointing at a giant technical element
- holding a magnifying glass
- examining code
- looking shocked at a performance graph
- interacting with a floating UI
- pulling apart an object
- sitting on a UI panel
- emerging from behind the headline
- holding a warning sign
- looking toward an important visual element
- standing inside a technical environment
- interacting with a before/after comparison
- partially overlapping the headline
- appearing in the foreground with strong perspective
- appearing smaller in the background to create depth

The avatar can be:
- left
- right
- center
- foreground
- background
- partially cropped
- integrated into the infographic
- overlapping typography
- interacting with technical elements

Choose the avatar placement based on the chosen creative direction.

NEVER use the same avatar placement and pose as a default.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5. TOPIC-SPECIFIC VISUAL STORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Do NOT create a generic infographic.

First identify the core technical concept of the topic.

Then create ONE strong visual story that explains that concept.

The visual story should communicate:

PROBLEM
↓
WHAT ACTUALLY HAPPENS
↓
CONSEQUENCE

Use visual metaphors wherever possible.

For example:

If the topic is about performance:
FAST → SOMETHING CHANGES → SLOW

If the topic is about an error:
CODE → ERROR → CONSEQUENCE

If the topic is about React:
COMPONENT → STATE/RENDER → RESULT

If the topic is about security:
ACTION → VULNERABILITY → RISK

If the topic is about JavaScript:
CODE → ENGINE BEHAVIOUR → RESULT

Do not blindly follow these examples.

Create the strongest visual interpretation for the actual topic.

The technical concept should be understandable visually, not only through text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6. INFOGRAPHIC DESIGN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use technical visual elements only when they strengthen the story.

Possible elements:
- JavaScript objects
- code windows
- code snippets
- arrows
- performance graphs
- speed indicators
- warning icons
- error indicators
- browser UI
- terminal UI
- developer dashboards
- flow diagrams
- comparison panels
- magnification circles
- state transitions
- network indicators
- performance meters
- visual connectors
- technical diagrams

Infographics must feel integrated into the environment.

Avoid creating a grid of unrelated boxes.

Use:
- depth
- perspective
- scale
- lighting
- arrows
- visual hierarchy
- subtle glow
- meaningful colour coding

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
7. TYPOGRAPHY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Typography should feel premium, modern and highly readable.

Use:
- bold headline typography
- strong size hierarchy
- clean sans-serif typography
- occasional monospace styling for code
- high contrast
- generous spacing

Important words may use:
- cyan
- electric blue
- green
- orange
- red

Do not make every word colourful.

Use colour strategically to emphasize the important concept.

The headline must remain readable even when viewed as a small Instagram thumbnail.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8. COLOUR SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Primary:
- deep navy
- dark blue
- cyan
- electric blue
- white

Semantic colours:
- GREEN = fast / correct / healthy / successful
- ORANGE = warning / transition
- RED = error / slow / dangerous / problematic

Use accent colours strategically.

Do not turn the entire image into a rainbow.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
9. TEXT HIERARCHY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The thumbnail should contain, where appropriate:

1. Small category/topic label
2. Attention-grabbing hook/warning
3. Large primary headline
4. Short explanatory subtitle
5. Avatar speech bubble or contextual dialogue
6. Bottom social handle

Do NOT force all elements into fixed positions.

Depending on the chosen creative direction:
- headline can be left aligned
- right aligned
- centered
- vertically stacked
- integrated with the visual
- partially surrounded by graphics

The composition should determine the placement.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
10. TEXT ACCURACY — ABSOLUTE REQUIREMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Render every required line EXACTLY as provided.

Do NOT:
- paraphrase
- translate
- shorten
- rewrite
- add words
- remove words
- change punctuation
- change capitalization
- invent additional text
- add lorem ipsum

Any code must be character-for-character identical.

If text becomes difficult to fit, redesign the composition rather than changing the text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
11. TEXT TO RENDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TOPIC:
"[INSERT TOPIC HERE]"

SMALL LABEL:
"[INSERT LABEL HERE]"

HOOK / WARNING:
"[INSERT HOOK HERE]"

HEADLINE:
"[INSERT HEADLINE HERE]"

SUBTITLE:
"[INSERT SUBTITLE HERE]"

AVATAR SPEECH:
"[INSERT AVATAR SPEECH HERE]"

BOTTOM HANDLE:
"@modernjavascripthub"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
12. LAYOUT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Create a custom composition specifically for this topic.

Do NOT use a repetitive template.

Maintain:
- strong focal point
- visual hierarchy
- generous spacing
- clean alignment
- safe margins
- readable text
- balanced composition
- strong contrast

Important content must not be clipped at the edges.

Avoid:
- overcrowding
- tiny text
- too many cards
- excessive borders
- excessive decorative elements
- generic stock illustration
- flat composition
- repetitive symmetry
- template-like appearance

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
13. AVATAR + TEXT INTERACTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Whenever appropriate, make the avatar interact visually with the typography.

Examples:
- avatar pointing toward a keyword
- avatar looking at the headline
- avatar partially behind the headline
- speech bubble connected naturally to avatar
- avatar holding an element related to the headline
- avatar reacting to a highlighted word
- avatar interacting with an arrow or diagram
- avatar physically interacting with a UI element

The avatar should feel like a presenter inside the story, not a sticker pasted onto the design.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
14. BACKGROUND DEPTH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Create multiple visual depth layers:

FOREGROUND:
avatar / main technical object / headline

MIDGROUND:
infographics / code / UI / diagrams

BACKGROUND:
subtle circuits / digital environment / lighting / atmosphere

Use depth and perspective to make the thumbnail visually rich without making it cluttered.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
15. PREMIUM INSTAGRAM THUMBNAIL TEST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before finalizing, ensure:

✓ It looks visually different from previous thumbnails.
✓ The avatar is used creatively.
✓ The avatar is part of the story.
✓ The topic can be understood quickly.
✓ The main headline is readable at small size.
✓ There is one clear visual focal point.
✓ The composition does not feel like a Canva template.
✓ The background belongs to the same brand but is not copied.
✓ The visual metaphor supports the topic.
✓ The thumbnail creates curiosity.
✓ Text is not clipped.
✓ No unnecessary text is added.
✓ No extra logo or watermark is added.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
16. FINAL RESTRICTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Do NOT add:
- extra logos
- watermarks
- URLs
- fake social handles
- unrelated captions
- random code
- lorem ipsum
- additional headlines
- unrelated icons
- unrelated characters

Do not change the avatar's identity or clothing.

Do not copy the exact layout of the reference image.

Do not make the avatar stand passively beside the content.

Do not make this look like a generic reusable template.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL CREATIVE INSTRUCTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Create ONE finished, premium, eye-catching Instagram thumbnail specifically for the provided topic.

Think like:
- a senior creative director
- a technology editorial designer
- a developer educator
- an Instagram growth-focused visual designer

Prioritize in this order:

1. Instant visual impact
2. Curiosity
3. Topic comprehension
4. Creative avatar integration
5. Strong visual storytelling
6. Typography readability
7. Premium developer-brand identity
8. Consistency without repetition

The final thumbnail should feel like a unique editorial cover created specifically for THIS topic — not a variation of the same template."""


def build_thumbnail_prompt(slide, topic):
    """Fills the user-authored THUMBNAIL_PROMPT_TEMPLATE with today's actual
    content. Only the bracketed placeholders are substituted - the wording
    around them is used exactly as given.

    Slide 1 now comes from the new flat script schema (slide_number/type/
    title/content/code/visual_type/visual_story/infographic/highlight/
    design_emphasis) instead of the old kicker/alert/subtitle/avatar_line
    fields, so those are derived from the closest equivalent new fields:
      - label   <- "JS DEEP DIVE" (fixed; the new schema has no short tag)
      - hook    <- slide["highlight"], falling back to the first sentence
                   of slide["content"]
      - headline <- slide["title"]
      - subtitle <- the first sentence of slide["content"] (a short promise/
                   summary line, same role the old "subtitle" field played)
      - avatar speech <- slide["content"] itself (short, in the mascot's
                   voice), falling back to a generic line
    """
    topic_line = topic or slide.get("title", "")
    content = slide.get("content", "")
    content_sentences = _split_sentences(content)
    subtitle = content_sentences[0] if content_sentences else content

    hook = slide.get("highlight") or subtitle
    avatar_speech = content or "Let me show you what's really going on here!"

    return (
        THUMBNAIL_PROMPT_TEMPLATE
        .replace("[INSERT TOPIC HERE]", topic_line)
        .replace("[INSERT LABEL HERE]", "JS DEEP DIVE")
        .replace("[INSERT HOOK HERE]", hook)
        .replace("[INSERT HEADLINE HERE]", slide.get("title", ""))
        .replace("[INSERT SUBTITLE HERE]", subtitle)
        .replace("[INSERT AVATAR SPEECH HERE]", avatar_speech)
    )


def generate_slide_image_ai(slide, index, total, variant=None, topic=None):
    """Best-effort: have the image model lay out the entire slide. Returns a
    PIL Image sized (W, H), or None so the caller falls back to PIL."""
    if not os.path.exists(BG_REFERENCE_PATH):
        return None

    is_title = slide.get("type", "simple_explanation") == "hook"

    if is_title:
        # The cover/thumbnail uses the user-authored prompt verbatim (see
        # THUMBNAIL_PROMPT_TEMPLATE above), not the generic wrapper below.
        prompt = build_thumbnail_prompt(slide, topic)
    else:
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


# Cover layouts the title/hook slide rotates through (mascot side + pose), so
# daily posts don't all share one template.
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


def render_title_slide(slide, index, total, out_path, variant=None):
    """Procedural fallback for the HOOK/cover slide. Reads the new flat
    schema: title (headline), content (body/hook text - also used as the
    avatar's speech line), highlight (short warning/hook banner, optional)."""
    variant = variant or COVER_VARIANTS[0]
    side = variant.get("side", "right")
    pose = variant.get("pose", "pointing")
    mirror = side == "left"

    img = make_background(seed=f"{TODAY}-title")
    draw = ImageDraw.Draw(img)
    kicker = SLIDE_TYPE_LABEL.get(slide.get("type", "hook"), "JS DEEP DIVE")
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

    content = slide.get("content", "")
    content_sentences = _split_sentences(content)
    subtitle_text = content_sentences[0] if content_sentences else content

    # Red alert banner, like the reference cover's warning strip - driven by
    # "highlight" now (the new schema's short emphasized-line field).
    y = 158
    alert = slide.get("highlight")
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
    for line in wrap_text(draw, subtitle_text, subtitle_font, W - 2 * margin)[:2]:
        draw.text((margin, y), line, font=subtitle_font, fill=WHITE)
        y += 40

    swipe_font = load_font("DejaVuSans-Bold.ttf", 27)
    draw.text((margin, y + 16), "SWIPE TO LEARN  →", font=swipe_font, fill=ACCENT)

    # Mascot on today's chosen side, speaking the slide's content as a hook
    # line. Layout + pose rotate daily via `variant`.
    avatar_h = 620
    avatar = load_avatar_cutout(avatar_h, path=get_avatar_pose(pose))
    avatar_w = avatar.width if avatar else 300
    if mirror:
        ax = margin - 26
    else:
        ax = W - margin - avatar_w + 26
    ay = H - 140 - avatar_h

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

        # bubble beside his head, tail pointing at his face
        bubble_font = load_font("DejaVuSans-Bold.ttf", 25)
        line = content or "Hey Devs! Let me show you what actually goes wrong here."
        head_cy = ay + 112
        bubble_side = "left" if mirror else "right"
        anchor_x = (ax + avatar_w - 26) if mirror else (ax + 26)
        draw_side_speech_bubble(
            draw, anchor_x, head_cy, line, bubble_font, box_w=430, side=bubble_side
        )

        img.paste(avatar, (ax, ay), avatar)
        draw = ImageDraw.Draw(img)

    draw_chrome(draw, index, total, kicker=None)  # repaint footer over avatar edge if needed
    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# Content slide: full-width heading + body + optional code block + highlight
# ---------------------------------------------------------------------------
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


# Maps a slide "type" to the role icon shown next to its heading (reusing
# draw_slide_role_icon's existing bug/warning/idea/clock/chart/star glyphs).
TYPE_ROLE = {
    "hook": "warning",
    "simple_explanation": "idea",
    "code_example": "bug",
    "flow": "clock",
    "under_the_hood": "idea",
    "common_mistake": "bug",
    "better_approach": "idea",
    "summary": "star",
}


def _fit_paragraph(draw, text, max_width, max_height, start_size=34, min_size=22):
    """Pick the largest font size (within range) whose wrapped text fits
    max_height, returning (font, lines)."""
    text = text or ""
    for size in range(start_size, min_size - 1, -2):
        font = load_font("DejaVuSans.ttf", size)
        lines = wrap_text(draw, text, font, max_width)
        line_h = size * 1.45
        if len(lines) * line_h <= max_height:
            return font, lines, line_h
    font = load_font("DejaVuSans.ttf", min_size)
    lines = wrap_text(draw, text, font, max_width)
    line_h = min_size * 1.45
    max_lines = max(1, int(max_height // line_h))
    return font, lines[:max_lines], line_h


def _fit_code_lines(raw_code, max_chars=34):
    """Split a code string (from the new flat schema's "code" field, which
    may be a single multi-line string) into short display lines."""
    if not raw_code:
        return []
    lines = []
    for raw_line in str(raw_code).splitlines():
        raw_line = raw_line.rstrip()
        if not raw_line:
            continue
        if len(raw_line) <= max_chars:
            lines.append(raw_line)
        else:
            # Wrap long lines on whitespace so nothing gets cut mid-token.
            words = raw_line.split(" ")
            cur = ""
            for w in words:
                trial = (cur + " " + w).strip()
                if len(trial) <= max_chars:
                    cur = trial
                else:
                    if cur:
                        lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
    return lines[:8]


def draw_code_card(draw, x, y, w, code_lines):
    """Monospace code block with light syntax colouring - same visual
    language as the old panel code blocks, just sized to the new full-width
    content slide layout."""
    code_font = load_font("DejaVuSansMono.ttf", 24)
    line_h = 34
    pad = 20
    block_h = len(code_lines) * line_h + 2 * pad
    draw.rounded_rectangle([x, y, x + w, y + block_h], radius=14,
                           fill=(5, 11, 28), outline=blend(PANEL_BG, ACCENT, 0.30), width=1)
    ty = y + pad
    for line in code_lines:
        draw_code_line(draw, x + pad, ty, line, code_font)
        ty += line_h
    return block_h


def draw_highlight_chip(draw, x, y, w, text):
    """Short emphasized line (the new schema's "highlight" field) shown as a
    colored pill/banner beneath the body text."""
    neg = is_negative(text)
    tone = ALERT_RED if neg else CHECK_GREEN
    font = load_font("DejaVuSans-Bold.ttf", 26)
    lines = wrap_text(draw, text, font, w - 90)[:2]
    line_h = 32
    pad = 18
    box_h = len(lines) * line_h + 2 * pad
    draw.rounded_rectangle([x, y, x + w, y + box_h], radius=16,
                           fill=blend(PANEL_BG, tone, 0.20), outline=tone, width=2)
    draw_status_badge(draw, x + 34, y + box_h / 2, 15, neg)
    ty = y + pad
    for line in lines:
        draw.text((x + 62, ty), line, font=font, fill=tone)
        ty += line_h
    return box_h


def render_content_slide(slide, index, total, out_path):
    """Procedural fallback for a body slide under the new flat schema
    (simple_explanation / code_example / flow / under_the_hood /
    common_mistake / better_approach). Layout: heading, body paragraph,
    optional code block, optional highlighted takeaway line - no panel grid."""
    img = make_background(seed=f"{TODAY}-{index}")
    draw = ImageDraw.Draw(img)
    kind = slide.get("type", "simple_explanation")
    draw_chrome(draw, index, total, kicker=SLIDE_TYPE_LABEL.get(kind, f"STEP {index - 1}"))

    margin = 60
    heading_font = load_font("DejaVuSans-Bold.ttf", 46)
    y = 176
    draw_slide_role_icon(draw, margin + 22, y + 30, TYPE_ROLE.get(kind, "idea"), r=22)
    heading_lines = wrap_text(draw, slide.get("title", ""), heading_font, W - 2 * margin - 60)[:2]
    for line in heading_lines:
        draw.text((margin + 56, y), line, font=heading_font, fill=WHITE)
        y += 58
    y += 28

    content_w = W - 2 * margin
    bottom_limit = H - 150  # leave room for the series banner + footer
    code_lines = _fit_code_lines(slide.get("code", ""))
    highlight = slide.get("highlight", "")

    # Reserve space for the code block and highlight chip (if present) first,
    # then give whatever remains to the body paragraph.
    reserved = 0
    if code_lines:
        reserved += len(code_lines) * 34 + 40 + 24  # block height + gap
    if highlight:
        reserved += 34 * 2 + 36 + 24  # up to 2 lines + padding + gap

    body_max_h = max(80, bottom_limit - y - reserved)
    body_font, body_lines, body_line_h = _fit_paragraph(
        draw, slide.get("content", ""), content_w, body_max_h
    )
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill=MUTED)
        y += body_line_h
    y += 24

    if code_lines:
        block_h = draw_code_card(draw, margin, y, content_w, code_lines)
        y += block_h + 24

    if highlight:
        chip_h = draw_highlight_chip(draw, margin, y, content_w, highlight)
        y += chip_h + 24

    draw_series_banner(draw, H - 126)
    img.save(out_path, "PNG")


# ---------------------------------------------------------------------------
# Summary slide
# ---------------------------------------------------------------------------
def render_summary_slide(slide, index, total, out_path):
    """Procedural fallback for the closing SUMMARY slide. New schema: title
    (heading), content (the 3-4 short takeaways as one body string),
    highlight (short memorable line) - "cta" no longer exists, so a generic
    follow CTA is synthesized."""
    img = make_background(seed=f"{TODAY}-summary")
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, index, total, kicker="KEY TAKEAWAY")

    margin = 60
    heading_font = load_font("DejaVuSans-Bold.ttf", 58)
    body_font = load_font("DejaVuSans.ttf", 36)
    cta_font = load_font("DejaVuSans-Bold.ttf", 34)

    y = 260
    for line in wrap_text(draw, slide.get("title", ""), heading_font, W - 2 * margin)[:3]:
        draw.text((margin, y), line, font=heading_font, fill=WHITE)
        y += 70
    y += 20
    for line in wrap_text(draw, slide.get("content", ""), body_font, W - 2 * margin)[:6]:
        draw.text((margin, y), line, font=body_font, fill=MUTED)
        y += 46
    if slide.get("highlight"):
        hf = load_font("DejaVuSans-Bold.ttf", 30)
        for line in wrap_text(draw, slide["highlight"], hf, W - 2 * margin)[:2]:
            draw.text((margin, y), line, font=hf, fill=ACCENT)
            y += 40
    y += 30

    cta = f"Follow {BRAND_HANDLE} for daily JS/React/Next.js tips"
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

    kind = slide.get("type", "simple_explanation")
    if kind == "hook":
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


# User-authored prompt for the carousel SCRIPT (text model). Only {topic} and
# {research} are substituted - the wording is used exactly as given. Note the
# JSON shape here is intentionally different from the old one (flat
# title/content/code/visual_type fields per slide, no "panels", no avatar) -
# the whole rendering pipeline below is built to consume THIS shape.
CAROUSEL_PROMPT_TEMPLATE = """You are the senior technical content writer and instructional designer for
"Modern JavaScript Hub", an Instagram + Telegram account
(@modernjavascripthub).

The account teaches ADVANCED JavaScript, React, and Next.js to
intermediate and senior developers.

The goal is NOT to teach textbook basics.

Focus on:
- non-obvious JavaScript behaviour
- hidden performance costs
- browser behaviour
- JavaScript engine behaviour
- V8 internals
- React rendering behaviour
- async behaviour
- memory
- closures
- event loop
- race conditions
- rendering
- caching
- browser APIs
- performance
- subtle bugs
- common senior-level mistakes
- counterintuitive behaviour

However, the explanation must remain SIMPLE.

Think like a senior engineer explaining a tricky issue during a code review.

Use:
- short sentences
- simple everyday language
- practical examples
- small analogies
- short code
- visual explanations

Avoid:
- academic language
- unnecessary jargon
- long paragraphs
- textbook definitions
- vague statements
- unnecessary complexity

Every technical term must be explained in simple language the first time it
appears.

Example:

"Hidden class — V8's internal shape for an object."

Do NOT assume the reader already understands the internal concept.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOPIC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Topic:
{topic}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESEARCH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use the following research briefing as the factual source.

Do NOT simply rewrite it.

Extract the most useful and surprising insight from it and turn it into a
clear educational story.

Research briefing:
---
{research}
---

Every technical claim must be supported by the research or established
JavaScript behaviour.

Do not invent benchmarks, numbers, browser behaviour or implementation details.

If the research says "can", do not rewrite it as "always".

If behaviour depends on an engine, browser, version or implementation,
make that limitation clear.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CORE OBJECTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Create an 8-slide Instagram carousel that teaches ONE narrow technical idea.

The reader should be able to understand the concept by progressing through
the slides.

The carousel should answer:

1. What is the problem?
2. Why does it happen?
3. What is actually happening?
4. Can I see it in code?
5. What happens internally?
6. What mistake do developers commonly make?
7. What should I do instead?
8. What should I remember?

Do NOT repeat the same explanation across multiple slides.

Each slide must add NEW information.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SLIDE STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Exactly 8 slides.

SLIDE 1 — HOOK

Purpose:
Stop the scroll and introduce the surprising technical problem.

Must contain:
- short hook
- topic
- curiosity
- very short supporting statement

Do NOT explain everything on Slide 1.

The reader should want to swipe.

Visual concept:
A strong editorial/technical visual that represents the problem.

Possible visual:
- warning
- before/after
- performance drop
- surprising result
- broken flow
- large keyword

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 2 — SIMPLE EXPLANATION

Purpose:
Explain the core idea in very simple language.

Use:
- a short analogy OR
- a simple real-world comparison OR
- a simple visual model

Avoid deep implementation details.

The reader should think:

"Oh, that's what's happening."

Visual concept:
Prefer an infographic or simple visual metaphor.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 3 — CODE EXAMPLE

Purpose:
Show the smallest useful JavaScript example.

Use minimal code.

Code should demonstrate the exact behaviour being discussed.

Prefer:
5–12 lines maximum.

Avoid unnecessary setup code.

Code lines should ideally remain under 30 characters.

If a longer line is technically necessary, split it into multiple lines.

Highlight the important line.

Explain the important line in one or two short sentences.

Visual concept:
Code editor + highlighted line + arrows/callouts.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 4 — WHAT HAPPENS

Purpose:
Show the actual sequence of events.

Explain the mechanism step-by-step.

Use a FLOW DIAGRAM whenever possible.

Preferred structure:

ACTION
   ↓
INTERNAL CHANGE
   ↓
ENGINE BEHAVIOUR
   ↓
RESULT

Do not describe a flow only with paragraphs.

Represent the flow visually.

Use 3–5 steps maximum.

Each step should have:
- short label
- short explanation

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 5 — UNDER THE HOOD

Purpose:
Explain the deeper technical reason.

This is the "senior developer" slide.

Use simplified technical diagrams where appropriate.

Possible infographic types:
- object shape diagram
- memory diagram
- event loop diagram
- call stack diagram
- rendering pipeline
- dependency graph
- state transition
- cache lookup
- browser pipeline
- V8 engine flow
- React render flow

IMPORTANT:

Simplify internal concepts without making them technically incorrect.

Do not create fake internal mechanisms merely to make the diagram look
interesting.

The infographic should explain the mechanism, not decorate the slide.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 6 — COMMON MISTAKE

Purpose:
Show what developers commonly do wrong.

Use:

❌ Common approach
↓
WHY IT CAUSES THE PROBLEM

Then optionally:

✓ Better approach

Keep the explanation short.

Use a visual comparison whenever possible.

For example:

❌ BAD
code

        VS

✓ BETTER
code

Do not shame the developer.

Explain the trade-off.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 7 — BETTER APPROACH / PRACTICAL RULE

Purpose:
Give the reader something actionable.

Answer:

"What should I actually do in real code?"

Show:
- recommended pattern
- practical rule
- when the advice matters
- when it does NOT matter

Use a compact code example or decision flow.

Preferred visual:

IF
↓
DO THIS

OTHERWISE
↓
THIS IS FINE

Do not overgeneralize.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SLIDE 8 — SUMMARY

Purpose:
Make the concept memorable.

Use 3–4 short takeaways.

Format:

KEY IDEA
→ ...

WHY
→ ...

AVOID
→ ...

REMEMBER
→ ...

End with a concise practical takeaway.

Do NOT introduce new technical information on this slide.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VISUAL STORYTELLING REQUIREMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every slide MUST include a visual explanation strategy.

Do not make all slides:

"text + code block + background".

For each slide, decide which visual format best explains the content.

Allowed visual formats:

1. CODE + CALLOUTS
2. FLOW DIAGRAM
3. BEFORE → AFTER
4. COMPARISON
5. TIMELINE
6. PROCESS DIAGRAM
7. OBJECT DIAGRAM
8. MEMORY DIAGRAM
9. STATE TRANSITION
10. PERFORMANCE GRAPH
11. DECISION TREE
12. CAUSE → EFFECT
13. LAYERED ARCHITECTURE
14. VISUAL METAPHOR
15. DEBUGGING DIAGRAM
16. UNDER-THE-HOOD DIAGRAM

Prefer infographics whenever they can explain the concept more clearly than
text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INFOGRAPHIC RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Infographics must be educational, not decorative.

Every diagram must answer at least one question:

- What changed?
- Why did it change?
- What happens next?
- What is connected to what?
- What is faster/slower?
- What is correct/wrong?
- What happens internally?

Use:
- arrows
- numbered steps
- highlighted nodes
- before/after states
- simple diagrams
- visual grouping
- short labels

Avoid:
- decorative arrows with no meaning
- excessive boxes
- complicated architecture diagrams
- tiny labels
- too many nodes
- visual clutter

A user should be able to understand the infographic without reading a
large paragraph.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VISUAL PROGRESSION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The 8 slides should visually progress like a story.

Recommended progression:

SLIDE 1
Problem / curiosity

↓

SLIDE 2
Simple mental model

↓

SLIDE 3
Real code

↓

SLIDE 4
Step-by-step flow

↓

SLIDE 5
Under the hood

↓

SLIDE 6
Common mistake

↓

SLIDE 7
Better approach

↓

SLIDE 8
Takeaway

Do not use the same infographic type on every slide.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CODE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Code is an educational illustration, not a full production implementation.

Prefer the smallest code that proves the point.

Rules:

- Keep code short.
- Prefer 5–12 lines.
- Keep individual lines under 30 characters whenever practical.
- Never invent APIs.
- Never modify JavaScript syntax incorrectly.
- Do not remove code required to demonstrate the behaviour.
- Highlight the important line.
- Explain what the highlighted line does.
- Use comments only when absolutely necessary.

If the concept cannot be accurately demonstrated in a tiny snippet,
use a simplified but valid example and explain the limitation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEXT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

All on-image text must be short and punchy.

This is an Instagram 1080x1350 slide.

Avoid paragraphs.

Preferred:

1 short headline

+

1–3 short explanation blocks

+

code/diagram

Instead of:

A large paragraph explaining the concept.

Every slide should be scannable in approximately 3–5 seconds.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TITLE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Slide titles should usually be:

4–10 words.

Use specific language.

BAD:
"Understanding JavaScript Objects"

BETTER:
"Why delete Changes the Object's Shape"

BAD:
"Let's Learn About Performance"

BETTER:
"Your Object Just Left the Fast Path"

Titles should create clarity or curiosity.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANALOGIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use an analogy only when it makes the technical concept easier.

Good analogy:

"Think of a hidden class like a blueprint V8 uses
to recognize objects with the same shape."

Then connect the analogy back to the actual mechanism.

Do NOT let the analogy replace the technical explanation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NO AVATAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Do NOT include the mascot/avatar in any slide.

The educational content, code and infographic should be the visual focus.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTAGRAM READABILITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Design content specifically for a mobile screen.

Prioritize:

- large readable headlines
- short text
- high contrast
- clear hierarchy
- visual separation
- simple diagrams
- readable code
- generous spacing

Never sacrifice readability just to fit more information.

If there is too much information:

REMOVE unnecessary information.

Do NOT shrink the text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY valid JSON.

No markdown.
No code fences.
No explanation outside JSON.

Use exactly this structure:

{{
  "slides": [
    {{
      "slide_number": 1,
      "type": "hook",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 2,
      "type": "simple_explanation",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 3,
      "type": "code_example",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 4,
      "type": "flow",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 5,
      "type": "under_the_hood",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 6,
      "type": "common_mistake",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 7,
      "type": "better_approach",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }},
    {{
      "slide_number": 8,
      "type": "summary",
      "title": "",
      "content": "",
      "code": "",
      "visual_type": "",
      "visual_story": "",
      "infographic": "",
      "highlight": "",
      "design_emphasis": ""
    }}
  ],

  "caption_hook": "",
  "caption_points": [],
  "caption_takeaway": "",
  "seo_keywords": [],
  "hashtags": []
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPORTANT FINAL CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before returning the JSON, verify:

✓ Exactly 8 slides
✓ Slide 1 = Hook
✓ Slide 2 = Simple explanation
✓ Slide 3 = Code
✓ Slide 4 = Flow
✓ Slide 5 = Under the hood
✓ Slide 6 = Common mistake
✓ Slide 7 = Better approach
✓ Slide 8 = Summary
✓ Every slide adds new information
✓ No unnecessary repetition
✓ No avatar
✓ At least 1 meaningful infographic/visual explanation per slide
✓ Code is short and valid
✓ Technical claims are accurate
✓ Technical jargon is explained
✓ Text is suitable for 1080x1350
✓ No long paragraphs
✓ Visual story is specified for every slide
✓ Infographic instructions are specified for every slide
✓ Output is valid JSON only"""


def build_carousel(topic, research):
    prompt = CAROUSEL_PROMPT_TEMPLATE.format(topic=topic, research=research)

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
        variant = cover_variant if slide.get("type") == "hook" else None
        slide_topic = topic if slide.get("type") == "hook" else None
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
