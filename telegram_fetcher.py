"""
Fetches recent posts from public Telegram channels using Telegram's public
web preview (https://t.me/s/<username>) - this is the same page a browser
sees when you view a channel without being logged in, so it works with no
API key, no bot token, and no auth.

Caveat: this is not the official Bot API, just parsing public HTML. If
Telegram changes that page's structure this will need updating - check the
Actions logs for warnings if a channel stops appearing.
"""
import hashlib
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from config import TELEGRAM_SOURCES

logger = logging.getLogger("news_bot.telegram_fetcher")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}


def _entry_id(username: str, post_id: str) -> str:
    raw = f"telegram|{username}|{post_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bg_image_url(el):
    """Pulls the URL out of an inline `background-image: url(...)` style, which
    is how the public web preview renders both single and grouped photos."""
    if not el or not el.get("style"):
        return None
    match = re.search(r"background-image:\s*url\(['\"]?(.*?)['\"]?\)", el["style"])
    return match.group(1) if match else None


def _extract_media(msg):
    """Returns (media_url, media_type) or (None, None).

    Video is preferred over a still image when both are present. Beyond plain
    videos this also catches round (video-note) messages and GIF/animation
    posts, and reads lazy-loaded video URLs from data-src, so far fewer video
    posts fall through to text-only. Grouped-photo albums are handled too -
    the first photo of the album is used as the representative image."""
    # Any <video> the preview renders: normal video, round video-note, or a
    # GIF/animation. src is sometimes deferred to data-src on the s/ page.
    for video_el in msg.select("video"):
        src = video_el.get("src") or video_el.get("data-src")
        if src and src.startswith("http"):
            return src, "video"

    # Round video-notes and GIFs occasionally wrap the source differently.
    for sel in ("a.tgme_widget_message_roundvideo_wrap",
                "div.tgme_widget_message_roundvideo",
                "a.tgme_widget_message_gif_wrap"):
        el = msg.select_one(sel)
        if el:
            src = el.get("data-src") or el.get("href")
            if src and src.startswith("http") and any(
                src.split("?")[0].endswith(ext) for ext in (".mp4", ".mov", ".webm")
            ):
                return src, "video"

    # Photos: single photo wrap or the first tile of a grouped album.
    for sel in ("a.tgme_widget_message_photo_wrap",
                "a.tgme_widget_message_grouped_wrap .tgme_widget_message_photo_wrap"):
        for photo_el in msg.select(sel):
            url = _bg_image_url(photo_el)
            if url and url.startswith("http"):
                return url, "photo"

    return None, None


def _fetch_channel(name: str, username: str, lean: str):
    url = f"https://t.me/s/{username}"
    entries = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch Telegram channel %s: %s", name, e)
        return entries

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.select("div.tgme_widget_message")

    for msg in messages:
        post_attr = msg.get("data-post")  # e.g. "brics_info/12345"
        if not post_attr:
            continue
        post_id = post_attr.split("/")[-1]

        text_el = msg.select_one("div.tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text(separator=" ", strip=True)
        if not text:
            continue

        # Use first ~120 chars as a "title" for clustering purposes
        title = text[:200]

        time_el = msg.select_one("time.time")
        published = time.time()
        if time_el and time_el.get("datetime"):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
                published = dt.timestamp()
            except Exception:
                pass

        media_url, media_type = _extract_media(msg)

        entries.append({
            "entry_id": _entry_id(username, post_id),
            "title": title,
            "link": f"https://t.me/{username}/{post_id}",
            "source": name,
            "lean": lean,
            "published": published,
            "media_url": media_url,
            "media_type": media_type,
        })

    return entries


def fetch_all_telegram_entries():
    all_entries = []
    for src in TELEGRAM_SOURCES:
        entries = _fetch_channel(src["name"], src["username"], src["lean"])
        all_entries.extend(entries)
    return all_entries
