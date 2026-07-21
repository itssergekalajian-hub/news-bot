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


def _extract_media(msg):
    """Returns (media_url, media_type) or (None, None)."""
    video_el = msg.select_one("video.tgme_widget_message_video")
    if video_el and video_el.get("src"):
        return video_el["src"], "video"

    photo_el = msg.select_one("a.tgme_widget_message_photo_wrap")
    if photo_el and photo_el.get("style"):
        match = re.search(r"background-image:\s*url\(['\"]?(.*?)['\"]?\)", photo_el["style"])
        if match:
            return match.group(1), "photo"

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
