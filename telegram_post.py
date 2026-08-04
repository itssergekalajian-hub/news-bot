"""
Posts a message to the configured Telegram channel using the Bot API.
Supports plain text, photo+caption, or video+caption.

Media is sent with a graceful fallback chain so a story keeps as much media
as possible: if a video send fails (e.g. Telegram couldn't fetch the video
URL) it retries with a still image when one is available, and only if that
also fails does it drop to a plain-text post. A bad image/video never loses
the post entirely.
"""
import os
import requests
import logging

logger = logging.getLogger("news_bot.telegram")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # e.g. "@your_channel" or "-1001234567890"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Telegram caption limit is 1024 chars; message text limit is 4096.
CAPTION_LIMIT = 1024


def _build_footer(sources):
    return "\n\nSources: " + ", ".join(sorted(sources))


def _send_message(text: str):
    resp = requests.post(
        f"{BASE_URL}/sendMessage",
        json={
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _send_media(endpoint: str, media_field: str, media_url: str, caption: str):
    resp = requests.post(
        f"{BASE_URL}/{endpoint}",
        json={
            "chat_id": CHANNEL_ID,
            media_field: media_url,
            "caption": caption,
            "parse_mode": "Markdown",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def post_to_channel(text: str, sources: list, media_url: str = None,
                    media_type: str = None, fallback_image_url: str = None):
    full_text = text + _build_footer(sources)
    caption = full_text if len(full_text) <= CAPTION_LIMIT else text[:CAPTION_LIMIT - 3] + "..."

    # Build the ordered list of media attempts. A video is tried first, then
    # a still image (either an explicit fallback or the photo itself), so a
    # video Telegram can't fetch degrades to a photo before text-only.
    attempts = []
    if media_url and media_type == "video":
        attempts.append(("sendVideo", "video", media_url))
        if fallback_image_url and fallback_image_url != media_url:
            attempts.append(("sendPhoto", "photo", fallback_image_url))
    elif media_url and media_type == "photo":
        attempts.append(("sendPhoto", "photo", media_url))
    elif fallback_image_url:
        attempts.append(("sendPhoto", "photo", fallback_image_url))

    for endpoint, media_field, url in attempts:
        try:
            result = _send_media(endpoint, media_field, url, caption)
            # If we had to truncate the caption, send the full text as a follow-up
            if len(full_text) > CAPTION_LIMIT:
                _send_message(full_text)
            return result
        except Exception as e:
            logger.warning(
                "Media send failed (%s via %s: %s), trying next fallback",
                media_field, endpoint, e,
            )

    try:
        return _send_message(full_text)
    except Exception as e:
        logger.error("Telegram post failed: %s", e)
        raise
