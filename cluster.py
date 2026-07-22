"""
Groups similar headlines into "events" and determines whether an event
is confirmed enough (per config rules) to post.
"""
import hashlib
from difflib import SequenceMatcher

from config import TITLE_SIMILARITY_THRESHOLD, MIN_SOURCES_NON_WIRE, FINANCE_WIRE_LEANS, SPORTS_WIRE_LEANS

# War/MENA/conflict-tracking OSINT sources - these can trigger a post ALONE
# (see is_confirmed below), unlike every other independent/commentary
# source which still needs a second, different-lean source to agree.
# Deliberately scoped to actual conflict-reporting accounts, NOT political
# commentators (Nawfal, Duran, Owens, Shapiro) or finance analysts
# (Fin Watch, Dalio, Bennett) - those still require real cross-confirmation.
WAR_OSINT_SINGLE_SOURCE_LEANS = {
    "osint_brics", "osint_mideast", "osint_defender", "osint_levins",
    "ru_source", "ua_source", "ir_source", "ir_opposition", "il_source",
    "osint_isw", "osint_bno", "osint_faytuks", "osint_clashreport",
    "osint_megatron", "osint_alibk", "osint_wartranslated",
    "osint_intelslava", "osint_thecradle", "osint_warfareanalysis",
}


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def cluster_entries(entries, similarity_threshold=None):
    """
    entries: list of dicts from fetcher.fetch_all_entries() + storage recent entries
    similarity_threshold: override for TITLE_SIMILARITY_THRESHOLD - used when
        re-clustering a subset of entries more strictly (see main.py's retry
        logic for when the summarizer reports a cluster wasn't really one event).
    Returns: list of clusters, each a dict:
        {cluster_key, title, members: [entries], sources: set, leans: set}
    """
    threshold = similarity_threshold if similarity_threshold is not None else TITLE_SIMILARITY_THRESHOLD
    clusters = []

    for entry in entries:
        placed = False
        for cluster in clusters:
            if _title_similarity(entry["title"], cluster["title"]) >= threshold:
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
      - at least one wire/finance_wire/sports_wire source reported it, OR
      - at least MIN_SOURCES_NON_WIRE sources from different lean buckets reported it, OR
      - at least one war/MENA OSINT source reported it (see
        WAR_OSINT_SINGLE_SOURCE_LEANS) - allowed to post ALONE for speed on
        fast-moving conflict coverage, but see needs_unverified_label()
        below: any cluster that only qualifies through this path MUST be
        clearly labeled unverified/single-source by the summarizer.
    """
    auto_confirm_leans = {"wire"} | FINANCE_WIRE_LEANS | SPORTS_WIRE_LEANS
    if cluster["leans"] & auto_confirm_leans:
        return True
    non_wire_leans = cluster["leans"] - auto_confirm_leans
    if len(non_wire_leans) >= 2 and len(cluster["sources"]) >= MIN_SOURCES_NON_WIRE:
        return True
    if cluster["leans"] & WAR_OSINT_SINGLE_SOURCE_LEANS:
        return True
    return False


def needs_unverified_label(cluster) -> bool:
    """
    True if this cluster only qualified via the single-source war-OSINT
    allowance above - NOT genuine wire-tier or multi-source cross
    confirmation. The summarizer must clearly flag these as
    unverified/single-source rather than presenting them as confirmed news.
    """
    auto_confirm_leans = {"wire"} | FINANCE_WIRE_LEANS | SPORTS_WIRE_LEANS
    if cluster["leans"] & auto_confirm_leans:
        return False
    non_wire_leans = cluster["leans"] - auto_confirm_leans
    if len(non_wire_leans) >= 2 and len(cluster["sources"]) >= MIN_SOURCES_NON_WIRE:
        return False
    return bool(cluster["leans"] & WAR_OSINT_SINGLE_SOURCE_LEANS)
