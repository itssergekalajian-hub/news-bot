"""
Fetches upcoming fixtures and recent results for the configured football
leagues from TheSportsDB's free API. This is deliberately separate from the news
pipeline (fetcher.py / cluster.py / filter.py / summarize.py) - fixtures and
results are structured factual data (who's playing whom and when, or the
final score), not headlines that need cross-source confirmation or topic
classification. A match result from one reputable sports database is
treated as simply true, the same way a stock price is.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from config import (
    THESPORTSDB_API_KEY, SPORTS_LEAGUES,
    SPORTS_FIXTURE_LOOKAHEAD_HOURS, SPORTS_RESULT_LOOKBACK_HOURS,
)

logger = logging.getLogger("news_bot.sports_scores")

BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_API_KEY}"

# TheSportsDB 403s requests that don't look like a browser (a bot-UA block,
# not a key problem) - send a normal browser User-Agent, same as the RSS
# fetcher does.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def _parse_event_datetime(event: dict):
    date_str = event.get("dateEvent")
    time_str = event.get("strTime") or "00:00:00"
    if not date_str:
        return None
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_upcoming_fixtures():
    """Returns a list of dicts: {event_id, league, home, away, when}."""
    cutoff = datetime.now(timezone.utc) + timedelta(hours=SPORTS_FIXTURE_LOOKAHEAD_HOURS)
    fixtures = []

    for league_name, league_id in SPORTS_LEAGUES.items():
        try:
            resp = requests.get(f"{BASE_URL}/eventsnextleague.php", params={"id": league_id}, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch upcoming fixtures for %s: %s", league_name, e)
            continue

        events = data.get("events") or []
        for event in events:
            when = _parse_event_datetime(event)
            if when is None or when > cutoff:
                continue
            home = event.get("strHomeTeam")
            away = event.get("strAwayTeam")
            event_id = event.get("idEvent")
            if not (home and away and event_id):
                continue
            fixtures.append({
                "event_id": event_id,
                "league": league_name,
                "home": home,
                "away": away,
                "when": when,
            })

    return fixtures


def fetch_recent_results():
    """Returns a list of dicts: {event_id, league, home, away, home_score, away_score, when}."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SPORTS_RESULT_LOOKBACK_HOURS)
    results = []

    for league_name, league_id in SPORTS_LEAGUES.items():
        try:
            resp = requests.get(f"{BASE_URL}/eventspastleague.php", params={"id": league_id}, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch recent results for %s: %s", league_name, e)
            continue

        events = data.get("events") or []
        for event in events:
            when = _parse_event_datetime(event)
            if when is None or when < cutoff:
                continue
            home = event.get("strHomeTeam")
            away = event.get("strAwayTeam")
            home_score = event.get("intHomeScore")
            away_score = event.get("intAwayScore")
            event_id = event.get("idEvent")
            if not (home and away and event_id) or home_score is None or away_score is None:
                continue
            results.append({
                "event_id": event_id,
                "league": league_name,
                "home": home,
                "away": away,
                "home_score": home_score,
                "away_score": away_score,
                "when": when,
            })

    return results
