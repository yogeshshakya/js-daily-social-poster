"""Posts the already-generated image + caption to Telegram and Instagram.

Expects these environment variables:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT          e.g. @modernjavascripthub or a numeric chat id
  IG_ACCESS_TOKEN        long-lived Instagram Graph API access token
  IG_BUSINESS_ID         numeric Instagram Business Account ID
  IMAGE_PATH             local path to the PNG to upload (for Telegram)
  RAW_IMAGE_URL          publicly reachable URL of the same PNG (for Instagram,
                         which requires a hosted image_url, not a direct upload)
  CAPTION_FILE           path to a text file containing the caption
"""

import os
import sys
import time

import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT = os.environ["TELEGRAM_CHAT"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
IG_BUSINESS_ID = os.environ["IG_BUSINESS_ID"]
IMAGE_PATH = os.environ["IMAGE_PATH"]
RAW_IMAGE_URL = os.environ["RAW_IMAGE_URL"]
CAPTION_FILE = os.environ["CAPTION_FILE"]

GRAPH_API_VERSION = "v21.0"


def read_caption():
    with open(CAPTION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def post_to_telegram(caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(IMAGE_PATH, "rb") as photo:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT, "caption": caption},
            files={"photo": photo},
            timeout=60,
        )
    body = resp.json()
    if not resp.ok or not body.get("ok"):
        raise RuntimeError(f"Telegram post failed: {resp.status_code} {body}")
    print("Telegram: posted successfully.", file=sys.stderr)
    return body


def post_to_instagram(caption):
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{IG_BUSINESS_ID}"

    # Step 1: create media container
    create_resp = requests.post(
        f"{base}/media",
        data={
            "image_url": RAW_IMAGE_URL,
            "caption": caption,
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=60,
    )
    create_body = create_resp.json()
    if not create_resp.ok or "id" not in create_body:
        raise RuntimeError(f"Instagram container creation failed: {create_resp.status_code} {create_body}")
    creation_id = create_body["id"]
    print(f"Instagram: created media container {creation_id}", file=sys.stderr)

    # Step 2: poll status until FINISHED (Instagram needs to fetch the image)
    status_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{creation_id}"
    for attempt in range(15):
        status_resp = requests.get(
            status_url,
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        status_body = status_resp.json()
        status_code = status_body.get("status_code")
        print(f"Instagram: container status = {status_code} (attempt {attempt + 1})", file=sys.stderr)
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram container processing failed: {status_body}")
        time.sleep(4)
    else:
        raise RuntimeError("Instagram container never finished processing (timed out).")

    # Step 3: publish
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
    caption = read_caption()

    errors = []
    try:
        post_to_telegram(caption)
    except Exception as e:
        errors.append(f"Telegram error: {e}")
        print(f"Telegram error: {e}", file=sys.stderr)

    try:
        post_to_instagram(caption)
    except Exception as e:
        errors.append(f"Instagram error: {e}")
        print(f"Instagram error: {e}", file=sys.stderr)

    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
