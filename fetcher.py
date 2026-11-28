"""
Fetches entries from all configured RSS sources.
"""
import base64
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
_IMG_SKIP_RE_WITHOUT_THUMB = re.compile(
    _IMG_SKIP_RE.pattern.replace("|thumb", ""),
    re.IGNORECASE,
)

# Some sites return a bot-challenge/error page (not the real feed) to
# requests that don't look like a browser, which then fails XML parsing
# with confusing errors ("mismatched tag", "undefined entity", etc.) that
# look like a broken feed but are actually a blocked request.
FEED_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
ARTICLE_REQUEST_HEADERS = {
    "User-Agent": FEED_REQUEST_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": FEED_REQUEST_HEADERS["Accept-Language"],
}
ARTICLE_REQUEST_TIMEOUT = 8
FEED_REQUEST_TIMEOUT = 12
FEED_SESSION = requests.Session()
FEED_SESSION.headers.update(FEED_REQUEST_HEADERS)
_CHALLENGED_FEEDS_LOGGED = set()
_ARTICLE_DETAILS_CACHE = {}
_GOOGLE_ARTICLE_URL_CACHE = {}
_GOOGLE_URL_RE = re.compile(rb"https?://[^\x00-\x20\"'<>]+", re.IGNORECASE)


def _protobuf_url_strings(data: bytes, depth: int = 0):
    """Yield URL strings embedded in length-delimited Google News token fields."""
    if depth > 5 or not data:
        return
    offset = 0

    def read_varint(position):
        value = shift = 0
        while position < len(data) and shift <= 63:
            byte = data[position]
            position += 1
            value |= (byte & 0x7f) << shift
            if not byte & 0x80:
                return value, position
            shift += 7
        raise ValueError("invalid protobuf varint")

    while offset < len(data):
        try:
            tag, offset = read_varint(offset)
            wire_type = tag & 7
            if wire_type == 0:
                _, offset = read_varint(offset)
            elif wire_type == 1:
                offset += 8
            elif wire_type == 2:
                length, offset = read_varint(offset)
                end = offset + length
                if end > len(data):
                    return
                value = data[offset:end]
                offset = end
                yield from _GOOGLE_URL_RE.findall(value)
                yield from _protobuf_url_strings(value, depth + 1)
            elif wire_type == 5:
                offset += 4
            else:
                return
        except (ValueError, RecursionError):
            return


def _decode_google_news_url(url: str, publisher_url: str = "") -> str | None:
    """Decode a publisher URL embedded in a Google News base64 article token."""
    path = urlsplit(url).path
    match = re.search(r"/(?:articles|read)/([^/]+)", path)
    if not match:
        return None
    token = match.group(1)
    token += "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token)
    except (ValueError, base64.binascii.Error):
        return None

    publisher_host = (urlsplit(publisher_url).hostname or "").lower()
    for candidate_bytes in _GOOGLE_URL_RE.findall(decoded):
        try:
            candidate = candidate_bytes.decode("utf-8").rstrip(".,);")
        except UnicodeDecodeError:
            continue
        host = (urlsplit(candidate).hostname or "").lower()
        if host and host != "news.google.com" and _matches_publisher_host(
                host, publisher_host):
            return candidate

    if publisher_host:
        for candidate_bytes in _protobuf_url_strings(decoded):
            try:
                candidate = candidate_bytes.decode("utf-8").rstrip(".,);")
            except UnicodeDecodeError:
                continue
            host = (urlsplit(candidate).hostname or "").lower()
            if _matches_publisher_host(host, publisher_host):
                return candidate
    return None


def _matches_publisher_host(host: str, publisher_host: str) -> bool:
    if not host or host == "news.google.com" or host.endswith(".news.google.com"):
        return False
    if not publisher_host:
        return True
    return (host == publisher_host
            or host.endswith("." + publisher_host)
            or publisher_host.endswith("." + host))


def resolve_article_url(url: str, publisher_url: str = "") -> str:
    """Resolve Google News redirect links to the publisher article when possible."""
    host = (urlsplit(url).hostname or "").lower()
    if host != "news.google.com" and not host.endswith(".news.google.com"):
        return url
    cache_key = (url, publisher_url)
    if cache_key in _GOOGLE_ARTICLE_URL_CACHE:
        return _GOOGLE_ARTICLE_URL_CACHE[cache_key]

    decoded = _decode_google_news_url(url, publisher_url)
    if decoded:
        _GOOGLE_ARTICLE_URL_CACHE[cache_key] = decoded
        return decoded

    publisher_host = (urlsplit(publisher_url).hostname or "").lower()
    try:
        response = requests.get(
            url, headers=ARTICLE_REQUEST_HEADERS,
            timeout=ARTICLE_REQUEST_TIMEOUT, allow_redirects=True)
        resolved = response.url
        resolved_host = (urlsplit(resolved).hostname or "").lower()
        if _matches_publisher_host(resolved_host, publisher_host):
            _GOOGLE_ARTICLE_URL_CACHE[cache_key] = resolved
            return resolved
        if response.ok and publisher_host:
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup.find_all(["a", "link"]):
                candidate = tag.get("href") or ""
                candidate = urljoin(resolved, candidate)
                candidate_host = (urlsplit(candidate).hostname or "").lower()
                if _matches_publisher_host(candidate_host, publisher_host):
                    _GOOGLE_ARTICLE_URL_CACHE[cache_key] = candidate
                    return candidate
    except requests.exceptions.RequestException as exc:
        logger.debug("Could not resolve Google News link %s: %s", url, exc)

    _GOOGLE_ARTICLE_URL_CACHE[cache_key] = url
    return url


def resolve_cluster_article_links(cluster: dict) -> None:
    """Resolve Google News links on selected cluster members before enrichment."""
    for member in cluster.get("members", []):
        member["link"] = resolve_article_url(
            member.get("link", ""), member.get("publisher_url", ""))


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


def _usable_image(url: str, allow_thumbnail: bool = False) -> bool:
    """Reject known generic cards, placeholders, logos, and tracking images."""
    if not url or not url.startswith("http"):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if host == "news.google.com" or host.endswith(".news.google.com"):
        return False
    if host == "googleusercontent.com" or host.endswith(".googleusercontent.com"):
        return False
    skip_re = _IMG_SKIP_RE_WITHOUT_THUMB if allow_thumbnail else _IMG_SKIP_RE
    return not skip_re.search(url)


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


def fetch_article_context(url: str, publisher_url: str = "") -> str | None:
    """Fetch the first three substantive paragraphs from an article page."""
    return _fetch_article_details(resolve_article_url(url, publisher_url))[1]


def _extract_rss_media(entry):
    """Returns (media_url, media_type, photo_candidates).

    Collects feed-provided media without scraping every article during ingestion.
    Article metadata is fetched later for selected stories during enrichment.
    """
    image_urls = []
    video_url = None

    def add_image(url, attributes, allow_thumbnail=False):
        if (url and _usable_image(url, allow_thumbnail=allow_thumbnail)
                and url not in image_urls):
            try:
                width = int(attributes.get("width", 0) or 0)
                height = int(attributes.get("height", 0) or 0)
            except (TypeError, ValueError):
                width = height = 0
            image_urls.append((url, width, height))

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
        if not _looks_like_video(url, mc.get("type", "")):
            add_image(url, mc, allow_thumbnail=True)

    for thumbnail in getattr(entry, "media_thumbnail", None) or []:
        url = thumbnail.get("url")
        add_image(url, thumbnail, allow_thumbnail=True)

    for enc in getattr(entry, "enclosures", None) or []:
        url = enc.get("href") or enc.get("url")
        enc_type = enc.get("type", "")
        if not url:
            continue
        if _looks_like_video(url, enc_type):
            video_url = video_url or url
            continue
        if ((enc_type.startswith("image") or _looks_like_image_url(url))
                and _usable_image(url)):
            add_image(url, enc, allow_thumbnail=True)

    content_values = []
    summary = getattr(entry, "summary", None)
    if isinstance(summary, str):
        content_values.append(summary)
    for content in getattr(entry, "content", None) or []:
        value = content.get("value")
        if isinstance(value, str):
            content_values.append(value)
    article_url = getattr(entry, "link", "") or ""
    for value in content_values:
        soup = BeautifulSoup(value, "html.parser")
        for image in soup.find_all("img", src=True):
            url = urljoin(article_url, image["src"].strip())
            add_image(url, image.attrs)

    candidates = []
    for url, width, height in image_urls:
        candidate = {"url": url, "kind": "rss"}
        if width and height:
            candidate.update(width=width, height=height)
        candidates.append(candidate)

    if video_url:
        return video_url, "video", candidates
    if candidates:
        return candidates[0]["url"], "photo", candidates
    return None, None, []


_IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|avif)(?:\?|$)", re.IGNORECASE)


def _looks_like_image_url(url: str) -> bool:
    return bool(url and _IMAGE_EXT_RE.search(url))


def _sanitize_feed_xml(raw_bytes: bytes) -> bytes:
    return re.sub(
        rb'&(?!#[0-9]+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]*;)',
        b'&amp;',
        raw_bytes,
    )


def fetch_all_entries():
    """
    Returns a list of dicts: {entry_id, title, link, source, lean, published}
    Skips sources that fail to fetch (logs a warning, doesn't crash the run).
    """
    all_entries = []
    for src in SOURCES:
        try:
            response = FEED_SESSION.get(src["url"], timeout=FEED_REQUEST_TIMEOUT)
            if getattr(response, "status_code", None) == 415:
                logger.info("Feed %s rejected XML Accept header; retrying with */*",
                            src["name"])
                response = FEED_SESSION.get(
                    src["url"], headers={"Accept": "*/*"},
                    timeout=FEED_REQUEST_TIMEOUT)
            raw_bytes = response.content
            if raw_bytes.lstrip(b"\xef\xbb\xbf \t\r\n").lower().startswith(
                    (b"<!doctype html", b"<html")):
                if src["url"] not in _CHALLENGED_FEEDS_LOGGED:
                    logger.warning(
                        "RSS feed %s returned an HTML challenge page; skipping",
                        src["name"],
                    )
                    _CHALLENGED_FEEDS_LOGGED.add(src["url"])
                continue
            response.raise_for_status()
            feed = feedparser.parse(_sanitize_feed_xml(raw_bytes))
            if feed.bozo and not feed.entries:
                logger.warning("Feed error for %s: %s", src["name"], feed.bozo_exception)
                continue
            if feed.bozo:
                logger.info(
                    "Partially parsed RSS feed %s (%d entries): %s",
                    src["name"], len(feed.entries), feed.bozo_exception,
                )
            for entry in feed.entries:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                if not title or not link:
                    continue
                media_url, media_type, photo_candidates = _extract_rss_media(entry)
                source = getattr(entry, "source", {}) or {}
                publisher_url = ""
                if hasattr(source, "get"):
                    publisher_url = source.get("href") or source.get("url") or ""
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
                    "publisher_url": publisher_url,
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
