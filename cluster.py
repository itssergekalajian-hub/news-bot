"""
Groups similar headlines into "events" and determines whether an event
is confirmed enough (per config rules) to post.
"""
import hashlib
from difflib import SequenceMatcher

from config import TITLE_SIMILARITY_THRESHOLD, MIN_SOURCES_NON_WIRE, FINANCE_WIRE_LEANS, SPORTS_WIRE_LEANS


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def cluster_entries(entries):
    """
    entries: list of dicts from fetcher.fetch_all_entries() + storage recent entries
    Returns: list of clusters, each a dict:
        {cluster_key, title, members: [entries], sources: set, leans: set}
    """
    clusters = []

    for entry in entries:
        placed = False
        for cluster in clusters:
            if _title_similarity(entry["title"], cluster["title"]) >= TITLE_SIMILARITY_THRESHOLD:
                cluster["members"].append(entry)
                cluster["sources"].add(entry["source"])
                cluster["leans"].add(entry["lean"])
                placed = True
                break
        if not placed:
            clusters.append({
                "title": entry["title"],
                "members": [entry],
                "sources": {entry["source"]},
                "leans": {entry["lean"]},
            })

    # assign a stable cluster key based on the earliest/representative title
    for cluster in clusters:
        key_src = min(cluster["members"], key=lambda e: e["published"])["title"].lower().strip()
        cluster["cluster_key"] = hashlib.sha256(key_src.encode("utf-8")).hexdigest()

        # Pick a representative media item: prefer video, else first photo found
        cluster["media_url"] = None
        cluster["media_type"] = None
        video_member = next((m for m in cluster["members"] if m.get("media_type") == "video"), None)
        photo_member = next((m for m in cluster["members"] if m.get("media_type") == "photo"), None)
        chosen = video_member or photo_member
        if chosen:
            cluster["media_url"] = chosen["media_url"]
            cluster["media_type"] = chosen["media_type"]

    return clusters


def is_confirmed(cluster) -> bool:
    """
    A cluster counts as confirmed if:
      - at least one wire or finance_wire source reported it, OR
      - at least MIN_SOURCES_NON_WIRE sources from different lean buckets reported it
    """
    auto_confirm_leans = {"wire"} | FINANCE_WIRE_LEANS | SPORTS_WIRE_LEANS
    if cluster["leans"] & auto_confirm_leans:
        return True
    non_wire_leans = cluster["leans"] - auto_confirm_leans
    if len(non_wire_leans) >= 2 and len(cluster["sources"]) >= MIN_SOURCES_NON_WIRE:
        return True
    return False
