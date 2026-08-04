"""
Fetches entries from all configured RSS sources.
"""
import hashlib
import re
import time
import logging
import feedparser
from bs4 import BeautifulSoup

from config import SOURCES
from telegram_fetcher import fetch_all_telegram_entries

logger = logging.getLogger("news_bot.fetcher")

# File extensions Telegram can actually fetch and send as a native video.
# An <enclosure>/<media:content> that points at a page or an embed player
# (YouTube/Vimeo/etc.) is NOT a direct video file and would fail sendVideo,
# so we only tag those as "video"; everything else image-ish stays "photo".
VIDEO_EXT_RE = re.compile(r"\.(mp4|mov|m4v|webm)(\?|$)", re.IGNORECASE)
# Tiny tracking pixels / spacers / feed-chrome icons we never want to post.
_IMG_SKIP_RE = re.compile(
    r"(doubleclick|feedburner|feedsportal|gravatar|pixel|1x1|spacer|blank|"
    r"avatar|logo|icon|button|badge|smiley|emoji)",
    re.IGNORECASE,
)

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


def _looks_like_video(url: str, mime: str = "") -> bool:
    """A media reference is a *sendable* video only if it's a direct video
    file - a mime type of video/* on a real file, or a video file extension.
    Embed/player page URLs (YouTube, etc.) are not, and would fail sendVideo."""
    if mime and mime.startswith("video/"):
        return True
    return bool(url and VIDEO_EXT_RE.search(url))


def _usable_image(url: str) -> bool:
    """Filters out obvious non-content images (tracking pixels, feed icons,
    avatars) so we don't attach a 1x1 pixel or a site logo as the story photo."""
    if not url or not url.startswith("http"):
        return False
    return not _IMG_SKIP_RE.search(url)


def _img_from_html(html: str):
    """Pulls the first usable <img> URL out of an HTML blob (an RSS feed's
    content:encoded / summary / description body). Many feeds - Guardian,
    NYT, Al Jazeera, Fox, WSJ and others - don't fill the media:* fields at
    all and only embed the article photo as an <img> inside the description,
    so without this pass those stories post with no image even though one
    was right there in the feed."""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None
    for img in soup.find_all("img"):
        url = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if url and url.startswith("//"):
            url = "https:" + url
        if _usable_image(url):
            return url
    return None


def _extract_rss_media(entry):
    """Returns (media_url, media_type) or (None, None).

    Order of preference:
      1. A direct video file from any media:content / enclosure entry.
      2. A usable image from media:content / media:thumbnail / enclosure.
      3. An <img> parsed out of the entry's HTML body (content / summary /
         description) - the fallback that recovers images for the many feeds
         that only embed the photo inline and never populate the media fields.
    """
    image_url = None

    # media:content can be a list with several renditions (e.g. a poster
    # image AND a video); scan all of them, not just the first, so a video
    # isn't missed just because an image happened to be listed first.
    for mc in getattr(entry, "media_content", None) or []:
        url = mc.get("url")
        if not url:
            continue
        if _looks_like_video(url, mc.get("type", "")) or mc.get("medium") == "video":
            if _looks_like_video(url, mc.get("type", "")):
                return url, "video"
        if image_url is None and _usable_image(url):
            image_url = url

    for enc in getattr(entry, "enclosures", None) or []:
        url = enc.get("href") or enc.get("url")
        enc_type = enc.get("type", "")
        if not url:
            continue
        if _looks_like_video(url, enc_type):
            return url, "video"
        if image_url is None and enc_type.startswith("image") and _usable_image(url):
            image_url = url

    if image_url is None:
        for mt in getattr(entry, "media_thumbnail", None) or []:
            url = mt.get("url")
            if _usable_image(url):
                image_url = url
                break

    if image_url:
        return image_url, "photo"

    # Last resort: dig an <img> out of the entry's HTML body.
    html_blobs = []
    for c in getattr(entry, "content", None) or []:
        if c.get("value"):
            html_blobs.append(c["value"])
    for field in ("summary", "description"):
        val = getattr(entry, field, None)
        if val:
            html_blobs.append(val)
    for html in html_blobs:
        url = _img_from_html(html)
        if url:
            return url, "photo"

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
