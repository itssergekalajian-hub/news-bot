"""
Centrist News Bot - single run.

Each run:
  1. Fetch latest entries from all sources
  2. Store new entries, cluster recent entries into "events"
  3. For each cluster that is newly confirmed (per cluster.is_confirmed):
     a. Check topic relevance (cached so it only runs once per cluster)
     b. Check it isn't a near-duplicate of something already posted recently
     c. Summarize it - if the summarizer determines the headlines don't
        actually share one event (NO_SINGLE_EVENT), skip rather than post
        a confused/refusal-text post
     d. Post to Telegram and mark it posted

This script runs ONCE per invocation and exits. Continuous operation comes
from a GitHub Actions workflow that triggers it on a cron schedule (see
.github/workflows/news-bot.yml and README.md) - no server required.
"""
import logging
import sys
from difflib import SequenceMatcher

from config import DB_PATH, CLUSTER_WINDOW_MINUTES, NEAR_DUP_LOOKBACK_HOURS, NEAR_DUP_THRESHOLD, MAX_POSTS_PER_RUN
from storage import Storage
from fetcher import fetch_all_entries
from cluster import cluster_entries, is_confirmed, needs_unverified_label
from filter import classify_batch, keyword_prefilter, BATCH_SIZE
from summarize import summarize_cluster
from telegram_post import post_to_channel
from sports_scores import fetch_upcoming_fixtures, fetch_recent_results
from media_upgrade import upgrade_cluster_media

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("news_bot.main")


def _is_near_duplicate(title: str, recent_posted_titles: list) -> bool:
    for posted_title in recent_posted_titles:
        if SequenceMatcher(None, title.lower(), posted_title.lower()).ratio() >= NEAR_DUP_THRESHOLD:
            return True
    return False


def run_once(store: Storage):
    new_entries = fetch_all_entries()
    logger.info("Fetched %d entries from sources", len(new_entries))

    new_count = 0
    for e in new_entries:
        if not store.has_seen_entry(e["entry_id"]):
            store.add_entry(
                e["entry_id"], e["title"], e["link"],
                e["source"], e["lean"], e["published"], None,
                media_url=e.get("media_url"), media_type=e.get("media_type"),
            )
            new_count += 1
    logger.info("%d new entries stored", new_count)

    recent_rows = store.get_recent_entries(CLUSTER_WINDOW_MINUTES)
    recent_entries = [
        {
            "entry_id": r[0], "title": r[1], "link": r[2],
            "source": r[3], "lean": r[4], "published": r[5],
            "media_url": r[7], "media_type": r[8],
        }
        for r in recent_rows
    ]

    clusters = cluster_entries(recent_entries)
    logger.info("Formed %d clusters from %d recent entries", len(clusters), len(recent_entries))

    recent_posted_titles = store.get_recent_posted_titles(NEAR_DUP_LOOKBACK_HOURS)

    # --- Pass 1: figure out which clusters need a fresh classification
    # call, and which are already known to be posted, off-topic, or
    # duplicates - all without touching the API. ---
    needs_classification = []
    ready_to_post = []

    for c in clusters:
        if not is_confirmed(c):
            continue
        if store.is_posted(c["cluster_key"]):
            continue
        if _is_near_duplicate(c["title"], recent_posted_titles):
            logger.info("Skipped (near-duplicate of recent post): %s", c["title"][:80])
            continue

        already_evaluated = store.is_evaluated(c["cluster_key"])
        if already_evaluated is False:
            continue  # previously judged off-topic, don't re-check or re-post
        if already_evaluated is True:
            ready_to_post.append(c)  # already known relevant from a prior run
        else:
            if not keyword_prefilter(c):
                # Confidently off-topic on keywords alone - never spends an
                # API call, which matters a lot on accounts with a very low
                # daily quota. Cached the same as a real "NO" verdict.
                store.mark_evaluated(c["cluster_key"], False)
                logger.info("Skipped (off-topic, keyword pre-filter): %s", c["title"][:80])
                continue
            needs_classification.append(c)

    # --- Pass 2: batch-classify everything that needs it, a handful of
    # clusters per API call instead of one call each - this is what keeps
    # a run with 100+ candidate clusters to roughly 12-15 API calls total
    # instead of 100+, which is what actually respects the free-tier rate
    # limit regardless of how much any single call is paced. ---
    for i in range(0, len(needs_classification), BATCH_SIZE):
        batch = needs_classification[i:i + BATCH_SIZE]
        results = classify_batch(batch)
        for c in batch:
            verdict = results.get(c["cluster_key"])
            if verdict is None:
                # Batch call failed - do NOT cache anything, just retry
                # next run. Never treat "couldn't check" as "irrelevant."
                logger.info("Skipped (classifier unavailable, will retry): %s", c["title"][:80])
                continue
            store.mark_evaluated(c["cluster_key"], verdict)
            if verdict:
                ready_to_post.append(c)
            else:
                logger.info("Skipped (off-topic): %s", c["title"][:80])

    # --- Pass 3: summarize and post everything that made it through. ---
    posted_this_run = 0
    posted_with_media_count = 0

    def try_summarize_and_post(cluster, allow_split_retry=True):
        nonlocal posted_this_run, posted_with_media_count
        if posted_this_run >= MAX_POSTS_PER_RUN:
            return  # cap reached - remaining candidates stay cached as
                     # relevant and get picked up on the next run instead
        try:
            unverified = needs_unverified_label(cluster)
            summary = summarize_cluster(cluster, unverified=unverified)
            if summary is None:
                if allow_split_retry and len(cluster["members"]) > 1:
                    # The summarizer determined this cluster's headlines
                    # don't actually share one event - likely a false merge
                    # from the title-similarity clustering being too loose
                    # for a busy, fast-moving story. Retry once by
                    # re-clustering just these members with a much stricter
                    # threshold, so genuinely distinct developments each get
                    # their own shot at posting instead of the whole thing
                    # being thrown away. No further recursion if a
                    # sub-cluster ALSO fails - just give up on that piece.
                    sub_clusters = cluster_entries(cluster["members"], similarity_threshold=0.75)
                    if len(sub_clusters) > 1:
                        logger.info(
                            "Retrying as %d smaller clusters (was 'not one event'): %s",
                            len(sub_clusters), cluster["title"][:80],
                        )
                        for sub in sub_clusters:
                            if not is_confirmed(sub):
                                continue
                            if store.is_posted(sub["cluster_key"]):
                                continue
                            if _is_near_duplicate(sub["title"], recent_posted_titles):
                                continue
                            try_summarize_and_post(sub, allow_split_retry=False)
                    else:
                        logger.info("Skipped (not actually one event): %s", cluster["title"][:80])
                else:
                    logger.info("Skipped (not actually one event): %s", cluster["title"][:80])
                return

            media_url, media_type = upgrade_cluster_media(cluster)
            if media_url:
                posted_with_media_count += 1
            else:
                logger.info(
                    "No media found for: %s (member links: %s)",
                    cluster["title"][:80],
                    [m["link"] for m in cluster["members"][:3]],
                )

            post_to_channel(
                summary,
                sources=list(cluster["sources"]),
                media_url=media_url,
                media_type=media_type,
            )
            store.mark_posted(cluster["cluster_key"], title=cluster["title"])
            recent_posted_titles.append(cluster["title"])  # so later clusters this run also check against it
            posted_this_run += 1
            logger.info("Posted: %s", cluster["title"][:80])
        except Exception as e:
            logger.error("Failed to summarize/post cluster '%s': %s", cluster["title"][:80], e)

    for c in ready_to_post:
        if posted_this_run >= MAX_POSTS_PER_RUN:
            logger.info(
                "Reached MAX_POSTS_PER_RUN (%d) - remaining candidates will be picked up next run.",
                MAX_POSTS_PER_RUN,
            )
            break
        try_summarize_and_post(c)

    logger.info(
        "Run complete. Posted %d new stories (%d with media, %d text-only).",
        posted_this_run, posted_with_media_count, posted_this_run - posted_with_media_count,
    )


def run_sports_scores(store: Storage):
    """
    Posts upcoming fixtures and newly finished results for the configured
    leagues/UFC. Separate from the news pipeline - no confirmation or topic
    classification needed, this is structured schedule/score data. Batches
    everything new into one digest message per category per run rather than
    one message per match, to avoid spamming the channel.

    Cross-checked against the SAME near-duplicate pool the news pipeline
    uses (store.get_recent_posted_titles), and adds its own posts to that
    pool too - without this, a match result reported both by TheSportsDB
    here AND by an article from BBC Football/Fabrizio Romano in the news
    pipeline would post twice as two unrelated-looking messages.
    """
    recent_posted_titles = store.get_recent_posted_titles(NEAR_DUP_LOOKBACK_HOURS)

    new_fixtures = []
    for fx in fetch_upcoming_fixtures():
        key = f"fixture:{fx['event_id']}"
        title = f"{fx['home']} vs {fx['away']}"
        if store.is_sports_posted(key):
            continue
        if _is_near_duplicate(title, recent_posted_titles):
            store.mark_sports_posted(key)  # don't re-check this event every run
            continue
        new_fixtures.append(fx)
        store.mark_sports_posted(key)

    new_results = []
    for res in fetch_recent_results():
        key = f"result:{res['event_id']}"
        title = f"{res['home']} {res['home_score']}-{res['away_score']} {res['away']}"
        if store.is_sports_posted(key):
            continue
        if _is_near_duplicate(title, recent_posted_titles):
            store.mark_sports_posted(key)
            continue
        new_results.append(res)
        store.mark_sports_posted(key)

    if new_results:
        lines = ["🏆 *Results*"]
        for r in sorted(new_results, key=lambda x: x["when"]):
            title = f"{r['home']} {r['home_score']}-{r['away_score']} {r['away']}"
            lines.append(f"{r['league']}: {title}")
            store.mark_posted(f"sports_result:{r['event_id']}", title=title)
        try:
            post_to_channel("\n".join(lines), sources=["TheSportsDB"])
            logger.info("Posted sports results digest (%d matches)", len(new_results))
        except Exception as e:
            logger.error("Failed to post sports results digest: %s", e)

    if new_fixtures:
        lines = ["📅 *Upcoming*"]
        for fx in sorted(new_fixtures, key=lambda x: x["when"]):
            when_str = fx["when"].strftime("%a %H:%M UTC")
            title = f"{fx['home']} vs {fx['away']}"
            lines.append(f"{fx['league']}: {title} - {when_str}")
            store.mark_posted(f"sports_fixture:{fx['event_id']}", title=title)
        try:
            post_to_channel("\n".join(lines), sources=["TheSportsDB"])
            logger.info("Posted sports fixtures digest (%d matches)", len(new_fixtures))
        except Exception as e:
            logger.error("Failed to post sports fixtures digest: %s", e)

    if not new_results and not new_fixtures:
        logger.info("Sports scores: nothing new this run")


def main():
    store = Storage(DB_PATH)
    logger.info("News bot run starting.")
    try:
        run_once(store)
    except Exception as e:
        logger.error("Unhandled error in news run: %s", e, exc_info=True)
        sys.exit(1)

    try:
        run_sports_scores(store)
    except Exception as e:
        logger.error("Unhandled error in sports scores run: %s", e, exc_info=True)
        # Don't exit(1) here - the news portion already succeeded, a sports
        # scores failure shouldn't mark the whole workflow run as failed.

    logger.info("News bot run finished.")


if __name__ == "__main__":
    main()
