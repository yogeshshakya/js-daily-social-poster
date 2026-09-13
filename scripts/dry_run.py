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

# A realistic script, exactly the shape build_carousel() returns from Gemini.
CAROUSEL = {
    "slides": [
        {
            "type": "title",
            "kicker": "V8 DEEP DIVE",
            "alert": "Stop Using delete Like This!",
            "title": "Why delete Makes Objects 10x Slower",
            "subtitle": "The hidden shape-transition cost most developers never see.",
            "avatar_line": "Hey Devs! Deleting a property can silently drop your object "
                           "out of V8's fast path. Let me show you why!",
            "cover_visual": {
                "bad_label": "delete obj.key",
                "bad_note": "goes dictionary mode",
                "good_label": "obj.key = undefined",
                "good_note": "keeps hidden class",
            },
        },
        {
            "type": "content",
            "heading": "The Problem",
            "icon": "bug",
            "panels": [
                {"title": "Naive Code", "kind": "code",
                 "lines": ["const o = { a: 1, b: 2 };", "delete o.a;", "use(o.b);"]},
                {"title": "What You Expect", "kind": "flow",
                 "lines": ["Property removed", "Same speed as before"]},
                {"title": "Measured", "kind": "output",
                 "lines": ["before: 12ms", "after: 121ms"], "result": "10x slower"},
                {"title": "Why Odd", "kind": "flow",
                 "lines": ["Same data", "Same loop", "Very different time"]},
            ],
        },
        {
            "type": "content",
            "heading": "Why It Happens",
            "icon": "warning",
            "panels": [
                {"title": "Hidden Class", "kind": "flow",
                 "lines": ["Object has a shape", "Shape maps key to slot"]},
                {"title": "On delete", "kind": "flow",
                 "lines": ["Shape invalidated", "Object goes dict mode"]},
                {"title": "Dict Mode", "kind": "output",
                 "lines": ["Hash lookup", "No inline cache"], "result": "slow path"},
                {"title": "Cost", "kind": "output",
                 "lines": ["Every read pays", "JIT deopts"], "result": "stays slow"},
            ],
        },
        {
            "type": "content",
            "heading": "The Fix",
            "icon": "idea",
            "panels": [
                {"title": "Assign Instead", "kind": "code",
                 "lines": ["const o = { a: 1, b: 2 };", "o.a = undefined;", "use(o.b);"]},
                {"title": "Or Rebuild", "kind": "code",
                 "lines": ["const { a, ...rest } = o;", "return rest;"]},
                {"title": "Result", "kind": "output",
                 "lines": ["12ms", "12ms"], "result": "fast path kept"},
                {"title": "Trade-off", "kind": "flow",
                 "lines": ["Key still present", "Use rest for clean copy"]},
            ],
        },
        {
            "type": "content",
            "heading": "How The Fix Works",
            "icon": "idea",
            "panels": [
                {"title": "Shape Stays", "kind": "flow",
                 "lines": ["Slot kept", "Only value changes"]},
                {"title": "Inline Cache", "kind": "flow",
                 "lines": ["Same hidden class", "IC still hits"]},
                {"title": "Engine View", "kind": "output",
                 "lines": ["mode: fast", "ic: monomorphic"], "result": "no deopt"},
                {"title": "Rest Copy", "kind": "flow",
                 "lines": ["New object", "Fresh clean shape"]},
            ],
        },
        {
            "type": "content",
            "heading": "Real World Impact",
            "icon": "chart",
            "panels": [
                {"title": "Scenario", "kind": "flow",
                 "lines": ["Cache layer evicts keys with delete"]},
                {"title": "What Breaks", "kind": "flow",
                 "lines": ["Every read slows", "CPU climbs"]},
                {"title": "Measured", "kind": "output",
                 "lines": ["p95 +40%"], "result": "prod incident"},
                {"title": "Root Cause", "kind": "flow",
                 "lines": ["Hot object in dict mode"]},
            ],
        },
        {
            "type": "content",
            "heading": "Pro Tip",
            "icon": "star",
            "panels": [
                {"title": "Use A Map", "kind": "code",
                 "lines": ["const m = new Map();", "m.delete(key);"]},
                {"title": "Why Better", "kind": "flow",
                 "lines": ["Built for churn", "No hidden class"]},
                {"title": "Rule", "kind": "output",
                 "lines": ["Objects: fixed shape", "Maps: dynamic keys"], "result": "right tool"},
                {"title": "Watch For", "kind": "flow",
                 "lines": ["delete in loops", "delete on hot paths"]},
            ],
        },
        {
            "type": "summary",
            "heading": "Key Takeaway",
            "body": "delete doesn't just remove a key, it changes the object's hidden "
                    "class and can drop it into dictionary mode for the rest of its life.",
            "cta": "Follow @modernjavascripthub for daily JS/React/Next.js deep dives",
        },
    ],
    "caption": "Ever deleted one property and watched a hot loop get 10x slower? "
               "delete doesn't just remove a key - it invalidates the object's hidden "
               "class and can push it into dictionary mode, where every later read "
               "pays a hash lookup and the JIT's inline caches stop helping. Assigning "
               "undefined, or rebuilding with rest syntax, keeps the fast path intact. "
               "Save this for your next performance review.",
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

    slides = CAROUSEL["slides"]
    total = len(slides)
    files = []
    for i, slide in enumerate(slides, start=1):
        path = os.path.join(outdir, f"slide{i}.png")
        g.render_slide(slide, i, total, path)
        files.append(path)
        print(f"rendered {path} ({slide.get('type', 'content')})")

    ig, tg = g.build_captions(
        CAROUSEL["caption"], CAROUSEL["seo_keywords"], CAROUSEL["hashtags"]
    )
    with open(os.path.join(outdir, "caption_instagram.txt"), "w", encoding="utf-8") as f:
        f.write(ig)
    with open(os.path.join(outdir, "caption_telegram.txt"), "w", encoding="utf-8") as f:
        f.write(tg)

    print("\n--- INSTAGRAM CAPTION ---")
    print(ig)
    print(f"\nDone: {len(files)} slides + 2 caption files in {outdir}/")


if __name__ == "__main__":
    main()
