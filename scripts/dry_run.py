"""Offline dry-run of the daily pipeline.

Runs the real generate.py rendering path end to end, but feeds it a canned
carousel script instead of calling Gemini (the sandbox can't reach the API,
and this also lets you preview design changes without burning quota).

    GEMINI_API_KEY=dummy python3 dry_run.py [outdir]

Writes all 8 slide PNGs + both caption files into `outdir` (default ./dry_run).
"""
import json
import os
import sys

import generate as g

# A realistic script, exactly the shape build_carousel() returns from Gemini
# under the new flat schema: slide_number/type/title/content/code/
# visual_type/visual_story/infographic/highlight/design_emphasis per slide.
CAROUSEL = {
    "slides": [
        {
            "slide_number": 1,
            "type": "hook",
            "title": "Why delete Makes Objects 10x Slower",
            "content": "Deleting one property can silently drop your object out of "
                       "V8's fast path. Here's why that innocent-looking line is "
                       "secretly a performance trap.",
            "code": "",
            "visual_type": "BEFORE → AFTER",
            "visual_story": "A fast object becomes slow the moment delete runs on it.",
            "infographic": "Speedometer or before/after contrast: fast vs slow.",
            "highlight": "Stop Using delete Like This!",
            "design_emphasis": "Bold warning tone, high contrast.",
        },
        {
            "slide_number": 2,
            "type": "simple_explanation",
            "title": "Objects Have a Hidden Shape",
            "content": "Think of a hidden class like a blueprint V8 uses to "
                       "recognize objects with the same shape. As long as an "
                       "object keeps its shape, V8 can read it fast.",
            "code": "",
            "visual_type": "VISUAL METAPHOR",
            "visual_story": "A blueprint stamping out identical object shapes.",
            "infographic": "Simple blueprint/stamp metaphor icon.",
            "highlight": "",
            "design_emphasis": "Friendly, simple, one big metaphor.",
        },
        {
            "slide_number": 3,
            "type": "code_example",
            "title": "The Buggy Code",
            "content": "This looks totally normal, but delete quietly reshapes "
                       "the object.",
            "code": "const o = { a: 1, b: 2 };\ndelete o.a;\nuse(o.b);",
            "visual_type": "CODE + CALLOUTS",
            "visual_story": "Highlight the delete line as the culprit.",
            "infographic": "Code editor with an arrow pointing at line 2.",
            "highlight": "delete o.a; <- this is the problem",
            "design_emphasis": "Monospace code block, one highlighted line.",
        },
        {
            "slide_number": 4,
            "type": "flow",
            "title": "What Actually Happens",
            "content": "Deleting a key doesn't just remove it - it invalidates "
                       "the object's internal shape entirely.",
            "code": "",
            "visual_type": "FLOW DIAGRAM",
            "visual_story": "Object has a shape -> delete runs -> shape invalidated -> dict mode.",
            "infographic": "4-step vertical flow with arrows.",
            "highlight": "",
            "design_emphasis": "Sequential arrows, escalating red tone.",
        },
        {
            "slide_number": 5,
            "type": "under_the_hood",
            "title": "Dictionary Mode Kicks In",
            "content": "Once the shape breaks, V8 falls back to a slower hash-map "
                       "style lookup for every future read of that object.",
            "code": "",
            "visual_type": "UNDER-THE-HOOD DIAGRAM",
            "visual_story": "Fast inline-cache lookup vs slow hash lookup.",
            "infographic": "Two lookup paths side by side, one fast one slow.",
            "highlight": "Every future read now pays a hash lookup",
            "design_emphasis": "Technical, engine-level diagram.",
        },
        {
            "slide_number": 6,
            "type": "common_mistake",
            "title": "The Mistake Developers Make",
            "content": "Reaching for delete to \"clean up\" an object is the "
                       "habit that causes this - it feels harmless but isn't.",
            "code": "delete obj.key;",
            "visual_type": "COMPARISON",
            "visual_story": "Bad approach vs the cost it causes.",
            "infographic": "Red X card with the delete line.",
            "highlight": "",
            "design_emphasis": "Red/warning tone, no shaming language.",
        },
        {
            "slide_number": 7,
            "type": "better_approach",
            "title": "Assign undefined Instead",
            "content": "Keep the shape intact by assigning undefined, or rebuild "
                       "the object with rest syntax when you need a real copy.",
            "code": "obj.key = undefined;\nconst { key, ...rest } = obj;",
            "visual_type": "DECISION TREE",
            "visual_story": "If you need the key gone forever, use rest; otherwise assign undefined.",
            "infographic": "Small decision tree with two branches.",
            "highlight": "Keeps the fast path intact",
            "design_emphasis": "Green/positive tone.",
        },
        {
            "slide_number": 8,
            "type": "summary",
            "title": "Key Takeaway",
            "content": "delete doesn't just remove a key - it changes the "
                       "object's hidden class and can drop it into dictionary "
                       "mode for the rest of its life.",
            "code": "",
            "visual_type": "VISUAL METAPHOR",
            "visual_story": "A closing, memorable summary card.",
            "infographic": "",
            "highlight": "Assign undefined instead of using delete",
            "design_emphasis": "Calm, confident closing tone.",
        },
    ],
    "caption_hook": "Ever deleted one property and watched a hot loop get 10x slower?",
    "caption_points": [
        "delete doesn't just remove a key - it changes the object's internal shape.",
        "That change can push the object into a slower 'dictionary mode' for good.",
        "Assigning undefined, or rebuilding with rest syntax, keeps the fast path.",
    ],
    "caption_takeaway": "\U0001f4a1 Save this for your next performance review.",
    "seo_keywords": [
        "javascript performance optimization",
        "v8 hidden classes explained",
        "javascript interview questions",
    ],
    "hashtags": [
        "#JavaScript", "#WebDevelopment", "#Coding", "#Programming", "#V8",
        "#JavaScriptTips", "#PerformanceOptimization", "#Frontend", "#WebDev",
        "#100DaysOfCode", "#CodeNewbie", "#DevCommunity", "#ModernJavaScriptHub",
    ],
}


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "dry_run"
    os.makedirs(outdir, exist_ok=True)

    variant_id = os.environ.get("COVER_VARIANT")
    variant = next((v for v in g.COVER_VARIANTS if v["id"] == variant_id), None) or g.COVER_VARIANTS[0]
    print(f"Using cover variant: {variant['id']}")

    slides = CAROUSEL["slides"]
    total = len(slides)
    files = []
    for i, slide in enumerate(slides, start=1):
        path = os.path.join(outdir, f"slide{i}.png")
        slide_variant = variant if slide.get("type") == "hook" else None
        slide_topic = "Why deleting an object property in V8 can make later reads up to 10x slower" if slide.get("type") == "hook" else None
        g.render_slide(slide, i, total, path, variant=slide_variant, topic=slide_topic)
        files.append(path)
        print(f"rendered {path} ({slide.get('type', 'simple_explanation')})")

    ig, tg = g.build_captions(
        CAROUSEL["caption_hook"],
        CAROUSEL["caption_points"],
        CAROUSEL["caption_takeaway"],
        CAROUSEL["seo_keywords"],
        CAROUSEL["hashtags"],
    )
    with open(os.path.join(outdir, "caption_instagram.txt"), "w", encoding="utf-8") as f:
        f.write(ig)
    with open(os.path.join(outdir, "caption_telegram.txt"), "w", encoding="utf-8") as f:
        f.write(tg)

    print("\n--- INSTAGRAM CAPTION ---")
    print(ig)

    # Extra check: render the hook/thumbnail slide with the OTHER cover
    # variant too, so both layouts get eyeballed, not just whichever one
    # today's history rotation happened to pick.
    other_variant = next((v for v in g.COVER_VARIANTS if v["id"] != variant["id"]), g.COVER_VARIANTS[0])
    other_path = os.path.join(outdir, "slide1_other_variant.png")
    g.render_slide(slides[0], 1, total, other_path, variant=other_variant,
                    topic="Why deleting an object property in V8 can make later reads up to 10x slower")
    print(f"rendered {other_path} (hook, variant={other_variant['id']})")

    # Extra check: prove the caption safety net turns a misbehaving,
    # paragraph-shaped Gemini response into real bullets.
    messy_hook = (
        "Ever deleted one property and watched a hot loop get 10x slower? "
        "delete doesn't just remove a key - it invalidates the object's "
        "hidden class and can push it into dictionary mode, where every "
        "later read pays a hash lookup and the JIT's inline caches stop "
        "helping, so assigning undefined instead keeps the fast path intact."
    )
    messy_ig, _ = g.build_captions(messy_hook, [], "", CAROUSEL["seo_keywords"], CAROUSEL["hashtags"])
    print("\n--- CAPTION SAFETY-NET TEST (messy single-paragraph input) ---")
    print(messy_ig)

    print(f"\nDone: {len(files)} slides + 2 caption files in {outdir}/")


if __name__ == "__main__":
    main()
