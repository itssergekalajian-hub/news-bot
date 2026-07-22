"""
Source configuration for the centrist news aggregator.

Each source has a political "lean" bucket used only for confirmation logic
(requiring a story to appear across multiple buckets before it's posted).
This is not a claim about objective truth - it's a heuristic for
"reported broadly enough across the spectrum that it's very likely real."
"""

SOURCES = [
    # --- Wire services / newswires (treated as high-trust, near-instant confirmation) ---
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "lean": "wire"},
    {"name": "UPI World News", "url": "https://rss.upi.com/news/tn_int.rss", "lean": "wire"},
    {"name": "DW (Deutsche Welle)", "url": "https://rss.dw.com/rdf/rss-en-top", "lean": "wire"},
    {"name": "France24", "url": "https://www.france24.com/en/rss", "lean": "wire"},
    {"name": "Sky News World", "url": "https://feeds.skynews.com/feeds/rss/world.xml", "lean": "wire"},

    # --- Left-leaning ---
    {"name": "The Guardian World", "url": "https://www.theguardian.com/world/rss", "lean": "left"},
    {"name": "NYT World", "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "lean": "left"},
    {"name": "NPR World", "url": "https://feeds.npr.org/1004/rss.xml", "lean": "left"},

    # --- Right-leaning ---
    {"name": "Fox News World", "url": "https://moxie.foxnews.com/google-publisher/world.xml", "lean": "right"},
    {"name": "NY Post World", "url": "https://nypost.com/world-news/feed/", "lean": "right"},
    {"name": "WSJ World", "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "lean": "right"},

    # --- Middle East depth (not treated as neutral wire - needs a second source to confirm) ---
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "lean": "middle_east"},
    {"name": "Times of Israel", "url": "https://www.timesofisrael.com/feed/", "lean": "il_media"},

    # --- Russia-Ukraine depth (independent, exiled Russian outlet - needs a second source) ---
    {"name": "The Moscow Times", "url": "https://www.themoscowtimes.com/rss/news", "lean": "ru_independent"},

    # --- Serious analytical/think-tank source (not breaking news, daily
    # expert analysis - co-published with ISW's daily "Iran Update", the
    # deepest ongoing English-language analysis of Iran/IRGC/Hezbollah/Axis
    # of Resistance activity available). Not auto-confirming - still needs
    # a second, different-lean source, consistent with not auto-trusting
    # any single non-major-wire source regardless of reputation.
    {"name": "Critical Threats Project (AEI)", "url": "https://www.criticalthreats.org/feed", "lean": "analysis_ctp"},
    {"name": "Predictive History", "url": "https://predictivehistory.substack.com/feed", "lean": "analysis_predictivehistory"},

    # --- Finance / markets / crypto (auto-confirming, like wire - see FINANCE_WIRE_LEANS) ---
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "lean": "finance_wire"},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "lean": "finance_wire"},
    {"name": "Kitco News", "url": "https://www.kitco.com/news/category/mining/rss", "lean": "finance_wire"},

    # --- Sports (auto-confirming, like wire - low controversy, factual results/news) ---
    {"name": "BBC Football", "url": "https://feeds.bbci.co.uk/sport/football/rss.xml", "lean": "sports_wire"},
    {"name": "Sherdog MMA", "url": "https://www.sherdog.com/rss/news.xml", "lean": "sports_wire"},
]

# Leans that auto-confirm a story on their own (same treatment as "wire"),
# without needing a second source. Reputable, established outlets only.
FINANCE_WIRE_LEANS = {"finance_wire"}
SPORTS_WIRE_LEANS = {"sports_wire"}

# Public Telegram channels, scraped via Telegram's public web preview
# (t.me/s/<username>) - no bot token or API key needed since these are
# public channels, but note this is unofficial (not the Bot API) and could
# break if Telegram changes that page's HTML structure.
#
# IMPORTANT: each account gets its OWN distinct lean tag (not a shared
# "independent" bucket). Confirmation requires two DIFFERENT lean tags to
# agree on a story - if every Telegram account shared one tag, two of them
# agreeing would never count as confirmation (this was a bug in an earlier
# version: Telegram sources never triggered a post because they were all
# lumped into a single "independent" bucket). With unique tags, any two
# accounts (Telegram+Telegram, or Telegram+RSS) corroborating each other
# now correctly counts.
TELEGRAM_SOURCES = [
    {"name": "BRICS News", "username": "brics_info", "lean": "osint_brics"},
    {"name": "Middle East Spectator", "username": "Middle_East_Spectator", "lean": "osint_mideast"},
    {"name": "OSINTdefender", "username": "OSINTdefender", "lean": "osint_defender"},
    {"name": "Kremlin News (Putin)", "username": "news_kremlin_eng", "lean": "ru_source"},
    {"name": "Kyiv Independent", "username": "KyivIndependent_official", "lean": "ua_source"},
    {"name": "Fin Watch", "username": "Fin_Watch", "lean": "osint_finwatch"},
    {"name": "Ethan Levins", "username": "ethanlevins", "lean": "osint_levins"},
    # Fast, professional breaking-news services - frequently faster than
    # mainstream outlets on developing stories, widely cited by journalists.
    # Still not auto-confirming alone (own distinct leans), consistent with
    # not auto-trusting any single non-major-wire source regardless of
    # reputation.
    {"name": "BNO News", "username": "bnonews", "lean": "osint_bno"},
    {"name": "Faytuks News", "username": "FaytuksTelegram", "lean": "osint_faytuks"},
    # Iran state media - covers IRGC statements/actions directly and extensively.
    # Both share one lean deliberately (they're not independent of each other,
    # same government messaging), so two Iran state outlets agreeing still
    # needs a second, genuinely different-lean source to confirm - same
    # design as Kremlin News for Russia.
    {"name": "Press TV", "username": "presstv", "lean": "ir_source"},
    {"name": "IRNA", "username": "Irna_en", "lean": "ir_source"},
    # Israeli military/government official statements - direct counterpart to
    # Press TV/IRNA above, so both sides of Israel-Hezbollah-Iran developments
    # are represented. Own distinct lean, same reasoning as ir_source/ru_source.
    {"name": "IDF (Israel Defense Forces)", "username": "idfofficial", "lean": "il_source"},
    # Iran opposition media (London-based) - genuine counterweight to Iran's
    # own state media (Press TV/IRNA) above, own distinct lean.
    {"name": "Iran International", "username": "IranIntl_En", "lean": "ir_opposition"},
    # Respected US think tank doing daily analysis on Ukraine and the Iran
    # war - higher-credibility than typical OSINT accounts, but still not
    # auto-confirming on its own; needs a second, different-lean source.
    {"name": "ISW (Institute for the Study of War)", "username": "ISW_official", "lean": "osint_isw"},
    # The most reliable individual source specifically for football transfer
    # news - auto-confirming given his singular track record on this beat.
    {"name": "Fabrizio Romano", "username": "FabrizioRomano", "lean": "sports_wire"},
    # Added per explicit request despite the earlier flagged concern (his
    # broader media operation includes disclosed paid crypto promotions
    # alongside news commentary) - same treatment as every other individual
    # account: never auto-confirms alone, needs a second different-lean
    # source to actually trigger a post.
    {"name": "Mario Nawfal", "username": "MarioNawfal", "lean": "osint_nawfal"},
    # Added per explicit request. Documented pro-Russian/contrarian
    # editorial slant (comparable tier to Kremlin News) - a bias question,
    # not a legitimacy one. Same non-auto-confirming treatment as everything
    # else.
    {"name": "The Duran", "username": "thedurancom", "lean": "osint_duran"},
    # Added per explicit request. Worth knowing: described by Wikipedia as
    # a "far-right political commentator... conspiracy theorist," dismissed
    # from The Daily Wire in 2024 over comments characterized as
    # antisemitic. Same non-auto-confirming treatment as every other
    # individual account.
    {"name": "Candace Owens", "username": "CandaceOwens", "lean": "osint_owens"},
    # Unofficial repost/fan channel (labeled "no impersonation") - reposts
    # his real commentary but isn't his own verified account (he mainly
    # posts on LinkedIn/X). Flagged for transparency; same non-auto-
    # confirming treatment.
    {"name": "Ray Dalio (fan/repost channel)", "username": "RayDalio", "lean": "osint_dalio"},
    # Confirmed official - forex/price-action trading analysis.
    {"name": "Justin Bennett (Daily Price Action)", "username": "justinbennett", "lean": "osint_bennett"},
    # Confirmed official. Strongly partisan conservative commentary, but no
    # documented extremism/fabrication track record (unlike the sources
    # declined above) - within normal political-commentary bounds.
    {"name": "Ben Shapiro", "username": "benshapiro", "lean": "osint_shapiro"},
    # Large, well-established breaking conflict-news aggregator.
    {"name": "Clash Report", "username": "ClashReport", "lean": "osint_clashreport"},
    # Fast-moving breaking news account, active coverage of Iran/Israel war.
    {"name": "Megatron", "username": "megatron_ron", "lean": "osint_megatron"},
    # Respected Ukraine war OSINT/translation account.
    {"name": "WarTranslated", "username": "wartranslated", "lean": "osint_wartranslated"},
    {"name": "Intel Slava", "username": "intelslava", "lean": "osint_intelslava"},
    {"name": "The Cradle", "username": "thecradlemedia", "lean": "osint_thecradle"},
    {"name": "Warfare Analysis", "username": "WarfareAnalysis", "lean": "osint_warfareanalysis"},
    # Arabic-language Middle East/world news and analysis. Couldn't confirm
    # a separate English-only version - the summarizer already translates
    # non-English source content to English, so this works either way.
    {"name": "Ali Bk", "username": "Alibk3", "lean": "osint_alibk"},
]

# Minimum requirement for a story to be considered "confirmed" and postable:
#   - reported by at least one WIRE source, OR
#   - reported by at least 2 sources from DIFFERENT lean buckets (e.g. left + right)
MIN_SOURCES_NON_WIRE = 2

# Similarity threshold (0-1) for clustering headlines into the same "event".
# Was lowered to 0.4 to help Telegram cross-format matching, but that caused
# genuinely DIFFERENT events with heavy keyword overlap (e.g. many distinct
# Iran/Hormuz sub-stories all sharing "Iran", "strikes", "Hormuz", "Trump")
# to falsely merge into one confused cluster. Raised back up as a better
# balance; the summarizer's own same-event check (see summarize.py) is the
# real safety net against false merges now, not this threshold alone.
TITLE_SIMILARITY_THRESHOLD = 0.5

# Before posting, a story's headline is checked against everything posted in
# the last NEAR_DUP_LOOKBACK_HOURS - if it's too similar to something already
# posted, it's treated as a re-hash of an already-covered story and skipped,
# rather than posting the same evolving story again with slightly different
# wording every hour.
NEAR_DUP_LOOKBACK_HOURS = 12
NEAR_DUP_THRESHOLD = 0.4

# Caps how many posts a single run can send. Matters for two reasons: (1)
# API budget - with single-source war posts allowed, a busy run could
# otherwise need 50+ individual summarization calls, blowing past the free
# tier's RPM limit even with pacing; (2) reading experience - 50 posts
# landing in the channel within a couple minutes isn't useful even if the
# API could handle it. Anything not posted this run stays cached as
# "relevant" and gets picked up on the next run instead of being lost.
MAX_POSTS_PER_RUN = 15

# How far back to look when clustering (minutes) - stories older than this
# window are considered separate news cycles even if titles are similar
CLUSTER_WINDOW_MINUTES = 180

# Storage
DB_PATH = "posted_stories.db"

# --- Sports fixtures & results (separate from the news pipeline - see sports_scores.py) ---
# Uses TheSportsDB's free public API (test key "3" - fine for this volume of
# use; if it ever gets rate-limited, a free personal key from
# thesportsdb.com is a drop-in replacement here).
THESPORTSDB_API_KEY = "3"

# TheSportsDB league IDs for the competitions in scope. If any of these IDs
# are wrong or a league gets renamed, sports_scores.py logs a clear warning
# naming which one failed rather than crashing - check the Actions logs and
# report back the exact league name if one never seems to produce fixtures.
SPORTS_LEAGUES = {
    "Premier League": 4328,
    "La Liga": 4335,
    "Serie A": 4332,
    "Bundesliga": 4331,
    "Ligue 1": 4334,
    "UEFA Champions League": 4480,
    "FIFA World Cup": 4429,
    "UFC": 4443,
}

# How far ahead to look for upcoming fixtures, and how far back to check for
# newly finished results, each run.
SPORTS_FIXTURE_LOOKAHEAD_HOURS = 48
SPORTS_RESULT_LOOKBACK_HOURS = 6
