"""
Posts a message to the configured Telegram channel using the Bot API.
Supports plain text, photo+caption, or video+caption.

Media is attached as reliably as possible:

  * PRIMARY path - we download the image/video bytes ourselves and UPLOAD
    them to Telegram as multipart form-data. This is important: when you send
    media by URL, Telegram's own servers have to fetch that URL, and they
    reject a lot of them with "400 Bad Request" (size limits, hotlink
    protection, content-type quirks - the CDN thumbnails behind t.me links
    are a common offender). Uploading the bytes means Telegram never fetches
    anything, which removes that entire class of failure.
  * SECONDARY path - if we can't download the bytes (host blocked us, file
    too big, not actually an image), we still try handing Telegram the URL,
    the way the bot used to.
  * Then a graceful fallback chain: video -> still image -> plain text, so a
    story keeps as much media as possible and a bad asset never loses the
    post entirely.
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

# Telegram Bot API upload ceilings: photos up to 10 MB, other files (video)
# up to 50 MB. We download up to a little over the relevant cap and bail if a
# file is bigger, rather than waste time pulling a huge asset Telegram would
# reject anyway.
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024

# Pretend to be a browser when pulling media - some CDNs 403 a bare/default
# client (the same reason the fetcher and media_upgrade set this header).
DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


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


def _download_media(url: str, kind: str):
    """Download media bytes for uploading. Returns (bytes, content_type) or
    None if it can't be fetched, is too large, or isn't the expected kind
    (photo->image/*, video->video/*). Never raises."""
    max_bytes = MAX_PHOTO_BYTES if kind == "photo" else MAX_VIDEO_BYTES
    want_prefix = "image/" if kind == "photo" else "video/"
    try:
        with requests.get(url, headers=DOWNLOAD_HEADERS, timeout=30, stream=True) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            # If the server tells us the type, use it to reject non-media
            # (e.g. an HTML error page returned with status 200).
            if content_type and not content_type.startswith(want_prefix):
                logger.info("Skipping %s upload, unexpected content-type %s for %s",
                            kind, content_type, url[:120])
                return None
            chunks = []
            total = 0
            for chunk in resp.iter_content(64 * 1024):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    logger.info("Skipping %s upload, larger than %d bytes: %s",
                                kind, max_bytes, url[:120])
                    return None
            data = b"".join(chunks)
            if not data:
                return None
            return data, (content_type or want_prefix + "jpeg")
    except Exception as e:
        logger.info("Could not download %s for upload (%s): %s", kind, e, url[:120])
        return None


def _upload_media(endpoint: str, media_field: str, data: bytes,
                  content_type: str, caption: str):
    """Send media by uploading the raw bytes as multipart form-data."""
    ext = "mp4" if media_field == "video" else "jpg"
    files = {media_field: (f"{media_field}.{ext}", data, content_type)}
    form = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "Markdown"}
    resp = requests.post(f"{BASE_URL}/{endpoint}", data=form, files=files, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _send_media_by_url(endpoint: str, media_field: str, media_url: str, caption: str):
    """Fallback: hand Telegram the URL and let it fetch the media itself."""
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


def _try_send_media(endpoint: str, media_field: str, url: str, caption: str) -> bool:
    """Attempt one media send: upload the bytes first, then fall back to
    sending the URL. Returns True if either succeeded."""
    downloaded = _download_media(url, media_field)
    if downloaded is not None:
        data, content_type = downloaded
        try:
            _upload_media(endpoint, media_field, data, content_type, caption)
            return True
        except Exception as e:
            logger.warning("Uploading %s bytes failed (%s), trying send-by-URL", media_field, e)

    try:
        _send_media_by_url(endpoint, media_field, url, caption)
        return True
    except Exception as e:
        logger.warning("Media send failed (%s via %s: %s), trying next fallback",
                       media_field, endpoint, e)
        return False


def post_to_channel(text: str, sources: list, media_url: str = None,
                    media_type: str = None, fallback_image_url: str = None):
    """Post a story. Returns how it was ultimately sent: "video", "photo",
    or "text" (so callers can log accurate media stats). Raises only if even
    the plain-text send fails."""
    full_text = text + _build_footer(sources)
    caption = full_text if len(full_text) <= CAPTION_LIMIT else text[:CAPTION_LIMIT - 3] + "..."

    # Ordered media attempts. A video is tried first, then a still image
    # (an explicit fallback, or the photo itself), so a video that can't be
    # delivered degrades to a photo before dropping to text-only.
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
        if _try_send_media(endpoint, media_field, url, caption):
            # If we had to truncate the caption, send the full text as a follow-up
            if len(full_text) > CAPTION_LIMIT:
                _send_message(full_text)
            return media_field  # "video" or "photo"

    try:
        _send_message(full_text)
        return "text"
    except Exception as e:
        logger.error("Telegram post failed: %s", e)
        raise
