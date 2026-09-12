"""Posts the already-generated carousel (multiple images) + captions to
Telegram (as an album) and Instagram (as a carousel post).

Expects these environment variables:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT            e.g. @modernjavascripthub or a numeric chat id
  IG_ACCESS_TOKEN          long-lived Instagram Graph API access token
  IG_BUSINESS_ID           numeric Instagram Business Account ID
  IMAGE_PATHS              comma-separated local paths, in slide order
  RAW_IMAGE_URLS           comma-separated public URLs (same order), for
                            Instagram which requires a hosted image_url
  CAPTION_FILE_INSTAGRAM   path to the Instagram caption text file
  CAPTION_FILE_TELEGRAM    path to the (shorter) Telegram caption text file
"""

import json
import os
import sys
import time

import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT = os.environ["TELEGRAM_CHAT"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
IG_BUSINESS_ID = os.environ["IG_BUSINESS_ID"]
IMAGE_PATHS = [p for p in os.environ["IMAGE_PATHS"].split(",") if p]
RAW_IMAGE_URLS = [u for u in os.environ["RAW_IMAGE_URLS"].split(",") if u]
CAPTION_FILE_INSTAGRAM = os.environ["CAPTION_FILE_INSTAGRAM"]
CAPTION_FILE_TELEGRAM = os.environ["CAPTION_FILE_TELEGRAM"]

GRAPH_API_VERSION = "v21.0"


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def post_to_telegram(caption):
    if len(IMAGE_PATHS) == 1:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(IMAGE_PATHS[0], "rb") as photo:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT, "caption": caption},
                files={"photo": photo},
                timeout=60,
            )
        body = resp.json()
        if not resp.ok or not body.get("ok"):
            raise RuntimeError(f"Telegram post failed: {resp.status_code} {body}")
        print("Telegram: posted single photo successfully.", file=sys.stderr)
        return body

    # Album (carousel) post: sendMediaGroup, files attached as attach://<name>
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media = []
    files = {}
    opened = []
    try:
        for i, path in enumerate(IMAGE_PATHS):
            attach_name = f"photo{i}"
            item = {"type": "photo", "media": f"attach://{attach_name}"}
            if i == 0:
                item["caption"] = caption
            media.append(item)
            fh = open(path, "rb")
            opened.append(fh)
            files[attach_name] = fh

        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT, "media": json.dumps(media)},
            files=files,
            timeout=120,
        )
    finally:
        for fh in opened:
            fh.close()

    body = resp.json()
    if not resp.ok or not body.get("ok"):
        raise RuntimeError(f"Telegram album post failed: {resp.status_code} {body}")
    print(f"Telegram: posted {len(IMAGE_PATHS)}-image album successfully.", file=sys.stderr)
    return body


def wait_for_container(creation_id):
    status_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{creation_id}"
    for attempt in range(15):
        resp = requests.get(
            status_url,
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        body = resp.json()
        status_code = body.get("status_code")
        print(f"Instagram: container {creation_id} status = {status_code} (attempt {attempt + 1})", file=sys.stderr)
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram container processing failed: {body}")
        time.sleep(4)
    raise RuntimeError(f"Instagram container {creation_id} never finished processing (timed out).")


def post_to_instagram(caption):
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{IG_BUSINESS_ID}"

    if len(RAW_IMAGE_URLS) == 1:
        create_resp = requests.post(
            f"{base}/media",
            data={"image_url": RAW_IMAGE_URLS[0], "caption": caption, "access_token": IG_ACCESS_TOKEN},
            timeout=60,
        )
        create_body = create_resp.json()
        if not create_resp.ok or "id" not in create_body:
            raise RuntimeError(f"Instagram container creation failed: {create_resp.status_code} {create_body}")
        creation_id = create_body["id"]
        wait_for_container(creation_id)
    else:
        # Carousel: create a child container per image, then a parent container
        child_ids = []
        for url in RAW_IMAGE_URLS:
            resp = requests.post(
                f"{base}/media",
                data={"image_url": url, "is_carousel_item": "true", "access_token": IG_ACCESS_TOKEN},
                timeout=60,
            )
            body = resp.json()
            if not resp.ok or "id" not in body:
                raise RuntimeError(f"Instagram carousel child creation failed for {url}: {resp.status_code} {body}")
            child_ids.append(body["id"])
            print(f"Instagram: created carousel child {body['id']} for {url}", file=sys.stderr)

        for cid in child_ids:
            wait_for_container(cid)

        parent_resp = requests.post(
            f"{base}/media",
            data={
                "media_type": "CAROUSEL",
                "caption": caption,
                "children": ",".join(child_ids),
                "access_token": IG_ACCESS_TOKEN,
            },
            timeout=60,
        )
        parent_body = parent_resp.json()
        if not parent_resp.ok or "id" not in parent_body:
            raise RuntimeError(f"Instagram carousel container creation failed: {parent_resp.status_code} {parent_body}")
        creation_id = parent_body["id"]
        wait_for_container(creation_id)

    publish_resp = requests.post(
        f"{base}/media_publish",
        data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN},
        timeout=60,
    )
    publish_body = publish_resp.json()
    if not publish_resp.ok or "id" not in publish_body:
        raise RuntimeError(f"Instagram publish failed: {publish_resp.status_code} {publish_body}")
    print(f"Instagram: published post {publish_body['id']}", file=sys.stderr)
    return publish_body


def main():
    if len(IMAGE_PATHS) != len(RAW_IMAGE_URLS):
        raise SystemExit("IMAGE_PATHS and RAW_IMAGE_URLS must have the same number of entries.")

    ig_caption = read_text(CAPTION_FILE_INSTAGRAM)
    tg_caption = read_text(CAPTION_FILE_TELEGRAM)

    errors = []
    try:
        post_to_telegram(tg_caption)
    except Exception as e:
        errors.append(f"Telegram error: {e}")
        print(f"Telegram error: {e}", file=sys.stderr)

    try:
        post_to_instagram(ig_caption)
    except Exception as e:
        errors.append(f"Instagram error: {e}")
        print(f"Instagram error: {e}", file=sys.stderr)

    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
