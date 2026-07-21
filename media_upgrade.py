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
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("news_bot.media_upgrade")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

MAX_LINKS_TO_TRY = 3


def fetch_og_media(url: str, timeout: int = 10):
    """Returns (media_url, media_type) or (None, None). Never raises."""
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

    video_url = (
        meta_content("og:video:secure_url")
        or meta_content("og:video:url")
        or meta_content("og:video")
    )
    if video_url and video_url.startswith("http"):
        return video_url, "video"

    image_url = meta_content("og:image:secure_url") or meta_content("og:image")
    if image_url and image_url.startswith("http"):
        return image_url, "photo"

    return None, None


def upgrade_cluster_media(cluster: dict):
    """
    Returns the (media_url, media_type) to actually use for posting.
    Tries fetching OG tags from a few of the cluster's member links (most
    recent first) and uses the first hit - a fresh high-quality fetch
    always takes priority over whatever the initial fast-pass extraction
    found. Falls back to that original extraction only if every attempt
    here comes up empty.
    """
    existing_media = cluster.get("media_url")
    existing_type = cluster.get("media_type")

    members_sorted = sorted(cluster["members"], key=lambda m: m["published"], reverse=True)
    links_to_try = [m["link"] for m in members_sorted[:MAX_LINKS_TO_TRY]]

    for link in links_to_try:
        media_url, media_type = fetch_og_media(link)
        if media_url:
            return media_url, media_type

    return existing_media, existing_type
