"""
Fetches entries from all configured RSS sources.
"""
import hashlib
import re
import time
import logging
from urllib.parse import urljoin, urlsplit

import feedparser
import requests
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
    r"avatar|logo|icon|button|badge|smiley|emoji|default|placeholder|"
    r"share[-_]card|breaking|banner|branding|thumb)",
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
ARTICLE_REQUEST_HEADERS = FEED_REQUEST_HEADERS
ARTICLE_REQUEST_TIMEOUT = 8
_ARTICLE_DETAILS_CACHE = {}


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
    """Reject known generic cards, placeholders, logos, and tracking images."""
    if not url or not url.startswith("http"):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if host == "news.google.com" or host.endswith(".news.google.com"):
        return False
    if host == "googleusercontent.com" or host.endswith(".googleusercontent.com"):
        return False
    return not _IMG_SKIP_RE.search(url)


def _fetch_article_details(
        url: str) -> tuple[list[str], str | None]:
    """Return verified hero image URLs and the first three body paragraphs."""
    if not url:
        return [], None
    if url in _ARTICLE_DETAILS_CACHE:
        return _ARTICLE_DETAILS_CACHE[url]

    image_urls = []
    context = None
    try:
        response = requests.get(
            url, headers=ARTICLE_REQUEST_HEADERS,
            timeout=ARTICLE_REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.debug("Could not fetch article %s: %s", url, e)
    else:
        soup = BeautifulSoup(response.text, "html.parser")
        dimensions = {}
        for key in ("og:image:width", "og:image:height",
                    "twitter:image:width", "twitter:image:height"):
            tag = soup.find("meta", property=key) or soup.find(
                "meta", attrs={"name": key})
            if tag and tag.get("content"):
                try:
                    dimensions[key.rsplit(":", 1)[-1]] = int(tag["content"])
                except ValueError:
                    pass

        for attrs in (
                {"property": "og:image:secure_url"},
                {"property": "og:image:url"},
                {"property": "og:image"},
                {"name": "twitter:image"},
                {"name": "twitter:image:src"}):
            tag = soup.find("meta", attrs=attrs)
            content = tag.get("content") if tag else None
            candidate = urljoin(response.url, content.strip()) if content else None
            width = dimensions.get("width")
            height = dimensions.get("height")
            if (not _usable_image(candidate)
                    or (width is not None and width < 400)
                    or (height is not None and height < 250)):
                continue
            if candidate not in image_urls:
                image_urls.append(candidate)

        body = soup.find("article") or soup.find("main") or soup
        paragraphs = []
        for paragraph in body.find_all("p"):
            text = paragraph.get_text(" ", strip=True)
            if text:
                paragraphs.append(text)
                if len(paragraphs) == 3:
                    break
        if paragraphs:
            context = "\n".join(paragraphs)

    _ARTICLE_DETAILS_CACHE[url] = image_urls, context
    return image_urls, context


def fetch_article_context(url: str) -> str | None:
    """Fetch the first three substantive paragraphs from an article page."""
    return _fetch_article_details(url)[1]


def _extract_rss_media(entry):
    """Returns (media_url, media_type, photo_candidates).

    Direct videos are preserved; photo candidates are ordered as verified
    OpenGraph/Twitter hero images followed by RSS media images.
    """
    image_urls = []
    video_url = None

    # media:content can be a list with several renditions (e.g. a poster
    # image AND a video); scan all of them, not just the first, so a video
    # isn't missed just because an image happened to be listed first.
    for mc in getattr(entry, "media_content", None) or []:
        url = mc.get("url")
        if not url:
            continue
        if mc.get("medium") == "video" or _looks_like_video(
                url, mc.get("type", "")):
            if _looks_like_video(url, mc.get("type", "")):
                video_url = video_url or url
            continue
        if (not _looks_like_video(url, mc.get("type", ""))
                and _usable_image(url) and url not in image_urls):
            image_urls.append(url)

    for enc in getattr(entry, "enclosures", None) or []:
        url = enc.get("href") or enc.get("url")
        enc_type = enc.get("type", "")
        if not url:
            continue
        if _looks_like_video(url, enc_type):
            video_url = video_url or url
            continue
        if enc_type.startswith("image") and _usable_image(url) and url not in image_urls:
            image_urls.append(url)

    og_images, _ = _fetch_article_details(
        getattr(entry, "link", "") or "")
    candidates = []
    for url in og_images:
        candidates.append({"url": url, "kind": "og"})
    for url in image_urls:
        if url not in og_images:
            candidates.append({"url": url, "kind": "rss"})

    if video_url:
        return video_url, "video", candidates
    if candidates:
        return candidates[0]["url"], "photo", candidates
    return None, None, []


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
                media_url, media_type, photo_candidates = _extract_rss_media(entry)
                all_entries.append({
                    "entry_id": _entry_id(src["name"], link, title),
                    "title": title,
                    "link": link,
                    "source": src["name"],
                    "lean": src["lean"],
                    "published": _parse_published(entry),
                    "media_url": media_url,
                    "media_type": media_type,
                    "photo_candidates": photo_candidates,
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
