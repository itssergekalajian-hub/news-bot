"""
Attempts to upgrade a cluster's attached media to a higher-quality version
by fetching the linked article/post page directly and reading its Open
Graph meta tags (og:image, og:video). These are what a site intends to
show when its content is shared elsewhere (that's the whole purpose of Open
Graph tags), so they're typically full resolution - unlike the small inline
thumbnails Telegram's list-preview page (t.me/s/<channel>) or some RSS
feeds provide by default.

This also fixes a related gap: Telegram's list-preview page doesn't always
embed a working video URL for every video post, but individual message
permalinks (t.me/<channel>/<id>) reliably render OG tags server-side
(that's how link previews work when you paste a Telegram link elsewhere),
so this catches videos the initial extraction pass missed too.

Only called right before posting a confirmed, relevant story - not during
the main fetch loop - so the extra HTTP request(s) only happen for stories
that actually end up posting, not every entry pulled from every source.
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("news_bot.media_upgrade")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

MAX_LINKS_TO_TRY = 3

# Telegram's sendVideo can only ingest a direct video *file*. og:video very
# often points at an embed/player page instead (a YouTube watch/embed URL, a
# JW Player iframe, etc.); sending one of those as a video fails, and the
# old code would then throw away the perfectly good og:image too. So we only
# treat og:video as a usable video when it looks like a real video file, and
# we always keep the image alongside it as a fallback.
VIDEO_EXT_RE = re.compile(r"\.(mp4|mov|m4v|webm)(\?|$)", re.IGNORECASE)
_EMBED_HOST_RE = re.compile(
    r"(youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com|/embed/|/player)",
    re.IGNORECASE,
)


def _is_direct_video(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    if _EMBED_HOST_RE.search(url):
        return False
    return bool(VIDEO_EXT_RE.search(url))


def fetch_og_media(url: str, timeout: int = 10):
    """Returns (video_url, image_url) - either may be None. Never raises.

    video_url is only set when the page's og:video is a directly sendable
    video file; image_url is the og:image. Both are returned together so the
    caller can use the video but still fall back to the image if Telegram
    can't fetch that video."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        logger.info("Could not fetch %s for media upgrade: %s", url, e)
        return None, None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.info("Could not parse %s for media upgrade: %s", url, e)
        return None, None

    def meta_content(prop):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        content = tag.get("content") if tag else None
        return content.strip() if content else None

    raw_video = (
        meta_content("og:video:secure_url")
        or meta_content("og:video:url")
        or meta_content("og:video")
    )
    video_url = raw_video if _is_direct_video(raw_video) else None
    if raw_video and not video_url:
        logger.info("Ignoring non-file og:video (embed/player) on %s: %s", url, raw_video)

    image_url = meta_content("og:image:secure_url") or meta_content("og:image")
    if not (image_url and image_url.startswith("http")):
        image_url = None

    return video_url, image_url


def upgrade_cluster_media(cluster: dict):
    """
    Returns (media_url, media_type, fallback_image_url) to use for posting.

    Fetches OG tags from a few of the cluster's member links (most recent
    first). A fresh, directly-sendable og:video wins; otherwise the best
    image found (a fresh full-resolution og:image, else the original
    fast-pass extraction) is used. fallback_image_url is always the best
    image we know of, so if the chosen video fails to send Telegram can
    still attach a photo instead of dropping to text-only.
    """
    existing_media = cluster.get("media_url")
    existing_type = cluster.get("media_type")
    existing_image = existing_media if existing_type == "photo" else None

    members_sorted = sorted(cluster["members"], key=lambda m: m["published"], reverse=True)
    links_to_try = [m["link"] for m in members_sorted[:MAX_LINKS_TO_TRY]]

    best_image = None
    for link in links_to_try:
        video_url, image_url = fetch_og_media(link)
        if best_image is None and image_url:
            best_image = image_url
        if video_url:
            # Prefer a fresh OG image as the fallback; fall back to any image
            # we've already found, then to the original extraction's image.
            return video_url, "video", best_image or existing_image

    # No sendable video anywhere - use the best image we have.
    image = best_image or existing_image
    if image:
        return image, "photo", image

    # Original extraction may have had a (non-file) video reference; pass it
    # through unchanged with no image fallback rather than losing it.
    return existing_media, existing_type, existing_image
