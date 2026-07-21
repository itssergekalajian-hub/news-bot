"""
Fetches entries from all configured RSS sources.
"""
import hashlib
import time
import logging
import feedparser

from config import SOURCES
from telegram_fetcher import fetch_all_telegram_entries

logger = logging.getLogger("news_bot.fetcher")

# Some sites return a bot-challenge/error page (not the real feed) to
# requests that don't look like a browser, which then fails XML parsing
# with confusing errors ("mismatched tag", "undefined entity", etc.) that
# look like a broken feed but are actually a blocked request.
FEED_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def _entry_id(source_name: str, link: str, title: str) -> str:
    raw = f"{source_name}|{link}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_published(entry) -> float:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            return time.mktime(val)
    return time.time()


def _extract_rss_media(entry):
    """Returns (media_url, media_type) or (None, None) from common RSS media fields."""
    media_content = getattr(entry, "media_content", None)
    if media_content:
        url = media_content[0].get("url")
        medium = media_content[0].get("medium", "image")
        if url:
            return url, "video" if medium == "video" else "photo"

    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail:
        url = media_thumbnail[0].get("url")
        if url:
            return url, "photo"

    enclosures = getattr(entry, "enclosures", None)
    if enclosures:
        enc = enclosures[0]
        url = enc.get("href") or enc.get("url")
        enc_type = enc.get("type", "")
        if url:
            return url, "video" if "video" in enc_type else "photo"

    return None, None


def fetch_all_entries():
    """
    Returns a list of dicts: {entry_id, title, link, source, lean, published}
    Skips sources that fail to fetch (logs a warning, doesn't crash the run).
    """
    all_entries = []
    for src in SOURCES:
        try:
            feed = feedparser.parse(src["url"], request_headers=FEED_REQUEST_HEADERS)
            if feed.bozo and not feed.entries:
                logger.warning("Feed error for %s: %s", src["name"], feed.bozo_exception)
                continue
            for entry in feed.entries:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                if not title or not link:
                    continue
                media_url, media_type = _extract_rss_media(entry)
                all_entries.append({
                    "entry_id": _entry_id(src["name"], link, title),
                    "title": title,
                    "link": link,
                    "source": src["name"],
                    "lean": src["lean"],
                    "published": _parse_published(entry),
                    "media_url": media_url,
                    "media_type": media_type,
                })
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", src["name"], e)
            continue

    try:
        telegram_entries = fetch_all_telegram_entries()
        all_entries.extend(telegram_entries)
        logger.info("Fetched %d entries from Telegram channels", len(telegram_entries))
    except Exception as e:
        logger.warning("Failed to fetch Telegram sources: %s", e)

    return all_entries
