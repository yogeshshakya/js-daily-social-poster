"""Posts the already-generated carousel (multiple images) + captions to
Telegram (as an album) and Instagram (as a carousel post).

Expects these environment variables:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT            e.g. @modernjavascripthub or a numeric chat id
  IG_ACCESS_TOKEN          long-lived Instagram Graph API access token
  IG_BUSINESS_ID           numeric Instagram Business Account ID
  FB_PAGE_ID               (optional) numeric Facebook Page ID
  FB_PAGE_TOKEN            (optional) that Page's own access token
                            - both must be set for Facebook posting; if either
                              is missing the Facebook step is skipped quietly
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
FB_PAGE_ID = (os.environ.get("FB_PAGE_ID") or "").strip()
FB_PAGE_TOKEN = (os.environ.get("FB_PAGE_TOKEN") or "").strip()
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


IG_REQUIRED_SCOPES = ("instagram_basic", "instagram_content_publish")


def report_instagram_token(token):
    """Best-effort: print what this token actually is and which scopes it has.

    Instagram publishing fails with a bare '(#10) Application does not have
    permission for this action' when a scope is missing, which says nothing
    about WHICH scope. Printing the scope list up front turns that into an
    obvious answer. Never fatal - if the check itself fails we just post.
    """
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=30,
        )
        info = resp.json().get("data", {})
    except Exception as e:  # noqa: BLE001 - diagnostics must never break posting
        print(f"Instagram: token check skipped ({e})", file=sys.stderr)
        return

    if not info:
        print("Instagram: token check returned nothing useful.", file=sys.stderr)
        return

    scopes = info.get("scopes", [])
    expires = info.get("expires_at")
    print(
        f"Instagram: token type={info.get('type')} "
        f"expires_at={'never' if expires == 0 else expires} "
        f"scopes={', '.join(scopes) if scopes else 'none reported'}",
        file=sys.stderr,
    )
    missing = [s for s in IG_REQUIRED_SCOPES if s not in scopes]
    if missing:
        print(
            "Instagram: WARNING - this token is missing "
            f"{', '.join(missing)}. That is what causes "
            "'(#10) Application does not have permission for this action'. "
            "Regenerate the token in Graph API Explorer with instagram_basic, "
            "instagram_content_publish, pages_show_list and "
            "pages_read_engagement, exchange it for a long-lived one, then "
            "update the IG_ACCESS_TOKEN secret.",
            file=sys.stderr,
        )


def post_to_instagram(caption):
    report_instagram_token(IG_ACCESS_TOKEN)
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


def resolve_facebook_page_id():
    """Check FB_PAGE_ID is actually reachable with FB_PAGE_TOKEN.

    A wrong page id (or a user token pasted in place of a page token) fails
    later with an unhelpful 'Object with ID ... does not exist' (code 100,
    subcode 33). Checking up front turns that into a readable message, and if
    the token itself knows which page it belongs to we use that id instead so
    the post still goes out.
    """
    check = requests.get(
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{FB_PAGE_ID}",
        params={"fields": "id,name", "access_token": FB_PAGE_TOKEN},
        timeout=30,
    )
    body = check.json()
    if check.ok and body.get("id"):
        print(f"Facebook: posting to page '{body.get('name')}' ({body['id']})", file=sys.stderr)
        return body["id"]

    print(f"Facebook: FB_PAGE_ID is not reachable with this token: {body}", file=sys.stderr)

    # Fall back to asking the token which page it belongs to.
    me = requests.get(
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/me",
        params={"fields": "id,name", "access_token": FB_PAGE_TOKEN},
        timeout=30,
    )
    me_body = me.json()
    if me.ok and me_body.get("id"):
        print(
            f"Facebook: the token belongs to '{me_body.get('name')}' (id {me_body['id']}). "
            f"Using that id. Update the FB_PAGE_ID secret to {me_body['id']} to silence this.",
            file=sys.stderr,
        )
        return me_body["id"]

    raise RuntimeError(
        "Facebook page id/token check failed. Most likely one of: "
        "(1) FB_PAGE_ID is not the numeric Page ID, "
        "(2) FB_PAGE_TOKEN is a USER token instead of that Page's own token, or "
        "(3) the token is missing the pages_manage_posts permission. "
        f"Page lookup said: {body}. Token lookup said: {me_body}"
    )


def post_to_facebook(caption):
    """Post the same images to a Facebook Page.

    Facebook has no 'carousel' object for page posts: the equivalent is a
    multi-photo post, built by uploading each photo UNPUBLISHED to /photos and
    then attaching all of their media_fbids to a single /feed post.
    """
    if not FB_PAGE_ID or not FB_PAGE_TOKEN:
        print(
            "Facebook: FB_PAGE_ID / FB_PAGE_TOKEN not set - skipping Facebook.",
            file=sys.stderr,
        )
        return None

    page_id = resolve_facebook_page_id()
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}"

    if len(RAW_IMAGE_URLS) == 1:
        resp = requests.post(
            f"{base}/photos",
            data={"url": RAW_IMAGE_URLS[0], "caption": caption, "access_token": FB_PAGE_TOKEN},
            timeout=60,
        )
        body = resp.json()
        if not resp.ok or "id" not in body:
            raise RuntimeError(f"Facebook photo post failed: {resp.status_code} {body}")
        print(f"Facebook: posted single photo {body['id']}", file=sys.stderr)
        return body

    media_ids = []
    for url in RAW_IMAGE_URLS:
        resp = requests.post(
            f"{base}/photos",
            data={"url": url, "published": "false", "access_token": FB_PAGE_TOKEN},
            timeout=60,
        )
        body = resp.json()
        if not resp.ok or "id" not in body:
            raise RuntimeError(
                f"Facebook photo upload failed for {url}: {resp.status_code} {body}"
            )
        media_ids.append(body["id"])
        print(f"Facebook: uploaded unpublished photo {body['id']}", file=sys.stderr)

    data = {"message": caption, "access_token": FB_PAGE_TOKEN}
    for i, media_id in enumerate(media_ids):
        # documented form: attached_media[0]={"media_fbid":"..."}
        data[f"attached_media[{i}]"] = json.dumps({"media_fbid": media_id})

    resp = requests.post(f"{base}/feed", data=data, timeout=120)
    body = resp.json()
    if not resp.ok or "id" not in body:
        raise RuntimeError(f"Facebook feed post failed: {resp.status_code} {body}")
    print(f"Facebook: published {len(media_ids)}-photo post {body['id']}", file=sys.stderr)
    return body


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

    try:
        post_to_facebook(ig_caption)
    except Exception as e:
        errors.append(f"Facebook error: {e}")
        print(f"Facebook error: {e}", file=sys.stderr)

    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
