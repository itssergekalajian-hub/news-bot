"""
Decides whether a confirmed news cluster falls within the topics the
channel should actually post about. This runs AFTER cross-source
confirmation (cluster.is_confirmed) and BEFORE summarization - it's a
separate, cheap classification call so irrelevant-but-confirmed stories
(e.g. a routine domestic policy story, a celebrity item that a wire service
also happened to run) don't get posted.

Uses Google's Gemini API free tier (Gemini 2.5 Flash-Lite) instead of
Claude - this is the highest-volume call in the whole pipeline (runs on
every confirmed cluster, most of which turn out off-topic), and a binary
YES/NO classification doesn't need a paid model. Falls back to skipping
the story (relevant=False) on any failure, same as before - a classifier
outage should never crash the run or cause an unclassified story to post.
"""
import os
import time
import logging
import requests

logger = logging.getLogger("news_bot.filter")

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-lite:generateContent"
)

# Free-tier Flash-Lite is roughly 10-15 requests/minute. A run with many
# confirmed clusters (common once several auto-confirming wire sources are
# in play) can easily need 30-100+ classification calls - firing them back
# to back with no pacing guarantees hitting the rate limit almost
# immediately. This delay keeps steady-state throughput safely under the
# limit; retries below handle transient errors (429s, and occasional 404s
# that appear to be backend routing hiccups rather than a real wrong model
# name, since retrying the identical request often succeeds).
PACING_DELAY_SECONDS = 4.5
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 6

TOPIC_PROMPT = """You are a topic classifier for a news channel with a specific, \
narrow focus. The channel ONLY wants:

1. Middle East conflicts (Israel/Gaza/Iran/Lebanon/regional war, strikes, ceasefires, \
major diplomatic developments directly tied to the conflict). This explicitly INCLUDES \
Hezbollah activity, IRGC (Iran's Revolutionary Guard) actions/statements, and developments \
in South Lebanon specifically - these are core to this category, not edge cases.

2. The Russia-Ukraine war and directly related developments (front-line news, strikes, \
sanctions, peace talks, territorial changes, major statements from Russian/Ukrainian \
leadership about the war)

3. MAJOR news from Europe or the USA ONLY if it is genuinely significant at a national/ \
international level: elections, major policy shifts, acts of political violence against \
major public figures, major disasters, significant geopolitical decisions. \
EXCLUDE: routine legal settlements or lawsuit payouts (even involving famous people), \
celebrity/tabloid news, individual crime stories, local politics, human-interest pieces, \
or anything that wouldn't be front-page news internationally.

4. MAJOR world news from anywhere else - ONLY significant global-impact events, same bar \
as #3.

5. Finance/markets - ONLY genuinely significant, market-moving news: major stock index \
moves (roughly 1%+ moves in a major index, not a single stock), central bank rate \
decisions, major commodity price swings (oil, gold, silver, and other metals), major \
cryptocurrency price swings (large BTC/ETH moves, not altcoin chatter), sovereign debt or \
currency crises, major economic data releases (inflation, jobs reports, GDP). \
EXCLUDE: single-company analyst notes, price target changes, rating upgrades/downgrades, \
one company's business strategy or product announcements, and general crypto-industry \
commentary that isn't about an actual broad price move. A story about what one bank \
thinks of one company's stock, or one exchange's growth plans, does NOT qualify even if \
CoinDesk or a business outlet covered it - only genuinely market-wide moves and events do.

6. Sports - ONLY major football (soccer) news: World Cup results/major moments, Champions \
League results/major moments, major results or major transfers/incidents in top European \
leagues (Premier League, La Liga, Serie A, Bundesliga, Ligue 1). Also major MMA news: UFC/ \
major-promotion event results (especially title fights and main-card results), major \
fighter news (retirements, big signings, suspensions, major upsets). \
EXCLUDE: routine regular-season match reports outside of major leagues, minor transfers, \
lower-tier MMA events, and opinion/preview/gossip pieces that aren't reporting an actual \
result or confirmed development.

Given the headlines below (all about the same event), answer with ONLY one \
word: YES if this event clearly falls into one of the 6 categories above under the \
criteria given, or NO if it does not. When in doubt about categories 3-6, lean toward NO \
- this channel would rather miss a borderline story than post something off-focus. \
No explanation, no punctuation, just YES or NO.
"""


def _call_gemini(payload):
    """POSTs to Gemini with retry-with-backoff for transient errors
    (429 rate-limited, 404s that resolve on retry, 5xx). Raises on final
    failure so the caller's own try/except can log and default to skip."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GEMINI_URL,
                params={"key": GOOGLE_API_KEY},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            last_error = e
            status = e.response.status_code if e.response is not None else None
            if status in (404, 429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY_SECONDS * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY_SECONDS * (attempt + 1))
                continue
            raise
    raise last_error


def is_relevant(cluster) -> bool:
    lines = [f"- [{m['source']}] {m['title']}" for m in cluster["members"]]
    user_content = "Headlines:\n" + "\n".join(lines)

    payload = {
        "systemInstruction": {"parts": [{"text": TOPIC_PROMPT}]},
        "contents": [{"parts": [{"text": user_content}]}],
        "generationConfig": {"maxOutputTokens": 5, "temperature": 0},
    }

    try:
        data = _call_gemini(payload)
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
        return text.startswith("YES")
    except Exception as e:
        logger.warning("Relevance check failed for '%s': %s - defaulting to skip", cluster["title"][:60], e)
        return False
    finally:
        # Pace calls to stay under the free-tier RPM limit, regardless of
        # whether this call succeeded or failed.
        time.sleep(PACING_DELAY_SECONDS)
