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
    "gemini-3.5-flash-lite:generateContent"
)

# Free-tier Flash-Lite is roughly 10-15 requests/minute. A run with many
# confirmed clusters (common once several auto-confirming wire sources are
# in play) can easily need 30-100+ classification calls - firing them back
# to back with no pacing guarantees hitting the rate limit almost
# immediately. This delay keeps steady-state throughput safely under the
# limit; retries below handle transient errors (429s, and occasional 404s
# that appear to be backend routing hiccups rather than a real wrong model
# name, since retrying the identical request often succeeds).
PACING_DELAY_SECONDS = 8
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 12
BATCH_SIZE = 40

# Free, local, zero-API-cost keyword pre-filter. Given how restrictive some
# accounts' free-tier daily quota can be (as low as 20 requests/day on some
# projects, far below the ~1,000-1,500/day sometimes advertised), this cuts
# the obviously-irrelevant bulk (routine domestic stories, transfer gossip,
# celebrity news, etc.) before ever spending an API call. Deliberately
# generous/broad - the goal is to catch clear non-matches cheaply, not to
# replace the LLM's nuanced judgment, so it's fine (expected, even) for
# borderline-relevant stories to pass through to the real classifier.
KEYWORDS = [
    # Middle East / Iran / Israel / Lebanon
    "iran", "israel", "gaza", "lebanon", "hezbollah", "hamas", "irgc",
    "syria", "houthi", "yemen", "idf", "tehran", "beirut", "gulf",
    "hormuz", "qeshm", "bahrain", "kuwait", "jordan", "iraq", "saudi",
    "uae", "netanyahu", "khamenei", "middle east",
    # Russia-Ukraine
    "russia", "ukraine", "kremlin", "putin", "zelensky", "moscow",
    "kyiv", "donbas", "crimea",
    # Major geopolitics / Europe / USA
    "election", "president", "prime minister", "parliament", "congress",
    "senate", "white house", "nato", "eu ", "european union", "war",
    "attack", "strike", "assassinat", "military", "troops", "sanctions",
    "trump", "biden",
    # USA national
    "washington", "supreme court", "pentagon", "state department",
    "white house", "governor", "u.s.", "united states",
    # European Union / Europe-wide
    "brussels", "european commission", "european parliament", "ecb",
    "eurozone", "von der leyen", "nato",
    # South Caucasus - Armenia & Azerbaijan
    "armenia", "armenian", "yerevan", "pashinyan", "artsakh",
    "azerbaijan", "azerbaijani", "baku", "aliyev", "nagorno", "karabakh",
    "caucasus", "zangezur", "corridor",
    # Technology & AI (specific tokens - avoid bare "ai" which matches too
    # many unrelated words as a substring; " ai " with spaces catches the
    # standalone word)
    " ai ", "artificial intelligence", "openai", "anthropic", "chatgpt",
    "gpt-", "gemini", "llm", "nvidia", "semiconductor", " chip ", "chipmaker",
    "microsoft", "google", "apple", "meta ", "amazon", "tesla", "spacex",
    "startup", "software", "cyberattack", "data breach", "ransomware",
    "quantum", "robot", "self-driving", "silicon valley",
    # Finance/markets
    "stock", "index", "s&p", "dow", "nasdaq", "fed ", "federal reserve",
    "inflation", "gdp", "jobs report", "oil price", "gold price",
    "silver", "bitcoin", "crypto", "btc", "eth ", "rate cut", "rate hike",
    "tariff", "market", "recession",
    # Sports (major only - filter.py's LLM check still applies the "major"
    # bar, this just decides whether to bother asking at all)
    "world cup", "champions league", "premier league", "la liga",
    "serie a", "bundesliga", "ligue 1", "ufc", "transfer",
]


def keyword_prefilter(cluster) -> bool:
    """Returns True if the cluster plausibly matches any topic - cheap,
    local, no API cost. False means confidently skip without ever asking
    Gemini. Checked against all member headlines, not just the first."""
    combined = " ".join(m["title"].lower() for m in cluster["members"])
    return any(kw in combined for kw in KEYWORDS)

TOPIC_PROMPT = """You are a topic classifier for a news channel with a specific, \
narrow focus. The channel ONLY wants:

1. Middle East conflicts (Israel/Gaza/Iran/Lebanon/regional war, strikes, ceasefires, \
major diplomatic developments directly tied to the conflict). This explicitly INCLUDES \
Hezbollah activity, IRGC (Iran's Revolutionary Guard) actions/statements, and developments \
in South Lebanon specifically - these are core to this category, not edge cases.

2. The Russia-Ukraine war and directly related developments (front-line news, strikes, \
sanctions, peace talks, territorial changes, major statements from Russian/Ukrainian \
leadership about the war)

3. News from Europe (including the European Union - European Commission/Parliament/ECB \
policy, EU-wide decisions, major member-state politics) or the USA (national politics, \
Congress, White House, Supreme Court, federal policy) if it is significant at a national/ \
international level: elections, major policy shifts, legislation, acts of political \
violence against major public figures, major disasters, significant geopolitical or \
economic decisions. EXCLUDE: routine legal settlements or lawsuit payouts (even involving \
famous people), celebrity/tabloid news, individual crime stories, purely local politics, \
human-interest pieces, or anything that wouldn't be front-page news nationally.

3b. South Caucasus - Armenia and Azerbaijan. Because this channel specifically follows \
this region, be more inclusive here than for #3: INCLUDE Armenia-Azerbaijan relations and \
border/ceasefire developments, Nagorno-Karabakh, the Zangezur corridor, significant \
Armenian or Azerbaijani domestic politics (government changes, elections, major protests, \
major policy or security decisions), and their dealings with Russia, Turkey, Iran, the EU \
or the USA. Still EXCLUDE trivia: routine local crime, sports, weather, and human-interest \
pieces.

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

7. Technology & Artificial Intelligence - MAJOR developments only: significant AI model or \
product launches (e.g. new frontier models from OpenAI, Anthropic, Google, Meta, xAI), \
major announcements or strategic moves by big tech (Nvidia, Apple, Microsoft, Google, \
Amazon, Meta, Tesla/SpaceX), large fundraises/acquisitions/IPOs, AI or tech regulation and \
policy (EU AI Act, US/China chip rules, antitrust), major research breakthroughs, and \
major cybersecurity incidents (large breaches, significant ransomware/state-linked hacks). \
EXCLUDE: routine app or gadget updates, individual product reviews, minor feature releases, \
small-startup PR, unconfirmed rumors/leaks, and how-to/opinion pieces.

Given the headlines below (all about the same event), answer with ONLY one \
word: YES if this event clearly falls into one of the categories above under the \
criteria given, or NO if it does not. When in doubt about categories 3-7, lean toward NO \
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


def is_relevant(cluster):
    """
    Single-cluster classification - kept for compatibility, but
    classify_batch() below is what main.py actually uses now, since it
    cuts the number of API calls per run by roughly 8x. This function
    still works the same way if ever needed directly.

    Returns True/False for a genuine classification, or None if the check
    could not be completed (API failure after retries) - the caller must
    NOT cache a None result as a verdict.
    """
    result = classify_batch([cluster])
    return result.get(cluster["cluster_key"])


def classify_batch(clusters):
    """
    Classifies multiple clusters in a single API call. This is the real
    fix for persistent rate-limit errors: with 100+ clusters needing
    classification on a busy run, one call per cluster reliably exceeds
    the free tier's rate limit no matter how much the calls are spaced
    out - batching cuts a 100-call run down to roughly 12-15 calls.

    Returns {cluster_key: bool} - a cluster is simply OMITTED from the
    result if the batch call fails, so the caller can tell "genuinely
    judged" apart from "couldn't check" and avoid caching a false verdict.
    """
    if not clusters:
        return {}

    numbered_lines = []
    for i, c in enumerate(clusters, start=1):
        headlines = "; ".join(f"[{m['source']}] {m['title']}" for m in c["members"][:3])
        numbered_lines.append(f"{i}. {headlines}")

    user_content = (
        "For each numbered story below, decide if it matches the channel's topics.\n\n"
        + "\n".join(numbered_lines)
        + "\n\nRespond with EXACTLY one line per number, format 'N:YES' or 'N:NO' "
          "(e.g. '1:YES'), nothing else - no explanations, no blank lines, no extra text."
    )

    payload = {
        "systemInstruction": {"parts": [{"text": TOPIC_PROMPT}]},
        "contents": [{"parts": [{"text": user_content}]}],
        "generationConfig": {"maxOutputTokens": 30 * len(clusters), "temperature": 0},
    }

    try:
        data = _call_gemini(payload)
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.warning("Batch relevance check failed for %d clusters: %s - will retry next run", len(clusters), e)
        return {}
    finally:
        # Pace batches to stay under the free-tier RPM limit.
        time.sleep(PACING_DELAY_SECONDS)

    results = {}
    for line in text.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        num_str, verdict = line.split(":", 1)
        try:
            idx = int(num_str.strip()) - 1
        except ValueError:
            continue
        if 0 <= idx < len(clusters):
            results[clusters[idx]["cluster_key"]] = verdict.strip().upper().startswith("YES")

    return results
