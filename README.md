# Centrist News Bot (GitHub Actions edition — no server needed)

Fully automated Telegram channel bot. Polls balanced sources (wire services +
left-leaning + right-leaning outlets, plus X accounts and Telegram channels —
see below) every 30 minutes via a GitHub Actions scheduled workflow, only
posts a story once it's confirmed by a wire service OR by outlets from at
least two different political leanings, then posts a neutral, fact-first
summary. Runs entirely on GitHub's infrastructure — you don't manage a VPS,
a process, or uptime.

## How "verification" actually works (read this)

This is **not** a fake-news detector — no automated system can guarantee that.
What it does instead:
- Never posts on a single source alone.
- Requires either a wire service (Reuters/AP/BBC) or 2+ sources from
  different political leanings to report the same event.
- Uses Claude to write a neutral summary and flag it when sources disagree
  on facts.

This catches "story hasn't been corroborated yet" and "only one side is
reporting this" — it does not catch a coordinated fabrication picked up by
multiple real outlets (rare, but possible).

## Topic focus

The channel is scoped to five categories only — everything else confirmed
gets silently skipped (see `filter.py`):
1. Middle East conflicts
2. Russia-Ukraine war and directly related developments
3. Major Europe/USA news (not routine/local stories)
4. Major world news elsewhere (only globally significant events)
5. Finance: notable stock, commodity, or crypto price moves/news

This runs as a cheap classification step *after* cross-source confirmation
and *before* summarization, so a confirmed-but-off-topic story (e.g. a
routine domestic policy item both Reuters and Guardian happened to cover)
never gets posted. Each cluster is classified once and the result cached
(`evaluated` table in the DB), so it won't re-check or flip-flop on the
same story across runs. To change the scope, edit `TOPIC_PROMPT` in
`filter.py`.

## Output language

All posts are written in English regardless of source language — the
summarizer is instructed to translate rather than quote non-English source
text (relevant for Kremlin News, Middle East Spectator, etc.).

## Writing style and media

Posts open with one **bolded headline line** plus a single topic emoji
(💥 conflict, 🕊️ diplomacy, 📈📉 markets, 🗳️ politics, 🌍 general), followed
by a few sentences of clear, vivid — but still strictly factual and
neutral — writing. No clickbait, no more than one emoji, still fact-checked
against the same cross-source confirmation and topic filter as everything
else. To adjust tone further, edit `SYSTEM_PROMPT` in `summarize.py`.

When a source post includes a photo or video (common on the Telegram
sources, occasional on RSS), the bot attaches it automatically — the post
goes out as a photo/video with the write-up as the caption instead of a
plain text message. If Telegram can't fetch that media for any reason, it
automatically falls back to a text-only post rather than failing silently.
Captions over Telegram's 1024-character limit get truncated in the caption
with the full text sent as an immediate follow-up message.

## Fixes from real-world testing (read if output doesn't match expectations)

Live testing surfaced three issues, now fixed:

1. **Telegram sources never confirmed anything.** They were all tagged with
   one shared lean (`independent`), but confirmation requires two
   *different* leans to agree — so two Telegram accounts corroborating each
   other never counted. Each Telegram source now has its own unique lean
   tag, so any two sources (Telegram+Telegram or Telegram+RSS) agreeing
   now correctly triggers confirmation.
2. **Finance was flooding the channel with niche stories** — single-company
   analyst notes, stock downgrades, business strategy pieces — because
   `finance_wire` sources (BBC Business, CoinDesk) auto-confirm on their
   own, and the topic filter wasn't strict enough to catch "confirmed but
   not actually significant." The filter now explicitly excludes
   single-company/single-stock commentary and only allows genuinely
   market-moving events (index-level moves, rate decisions, major
   commodity/crypto swings, major economic data).
3. **Zero Russia-Ukraine coverage** in the test sample — partly bad luck
   (no qualifying story that hour) and partly thin source coverage. Added
   The Moscow Times (independent, English-language, exiled Russian outlet)
   as a further Russia-Ukraine source alongside Kremlin News and Kyiv
   Independent.
4. Also tightened the "major Europe/USA" and "major world" categories to
   explicitly exclude routine legal settlements, celebrity/tabloid stories,
   and local crime — even when a mainstream outlet covers them, they're not
   what you're looking for.

The title-similarity threshold was also lowered slightly (0.55 → 0.4) since
Telegram's shorter, informal post text rarely scores highly against a
formal RSS headline for the same event even when correct.

## Sources currently configured

**RSS (mainstream):** BBC World, UPI World News (wire — auto-confirm) /
Guardian, NYT, NPR (left) / Fox, NY Post, WSJ (right) / Al Jazeera, Times of
Israel (Middle East depth) / The Moscow Times (Russia-Ukraine depth,
independent exiled outlet) / BBC Business, CoinDesk, Kitco News (finance —
auto-confirming, same trust tier as wire) / BBC Football, Sherdog (sports —
auto-confirming).

**Telegram channels (independent/OSINT/official):**
- BRICS News, Middle East Spectator, OSINTdefender, Fin Watch, Ethan Levins
  (each own unique lean)
- Kremlin News (`ru_source`) / Kyiv Independent (`ua_source`)
- Press TV and IRNA (`ir_source`) — Iran's own state media, covers IRGC
  statements/actions directly
- **New: IDF (Israel Defense Forces)** (`il_source`) — Israel's official
  military channel, the direct counterpart to Press TV/IRNA above. Reports
  extensively and specifically on Hezbollah/South Lebanon operations from
  the Israeli side, paired with Times of Israel (`il_media`) for
  independent Israeli journalism. Together with Al Jazeera, Middle East
  Spectator, and Press TV/IRNA, this now gives multiple distinct
  perspectives on Israel-Hezbollah-Iran developments rather than leaning on
  one side's framing.

**On Lebanon specifically:** I looked for a dedicated Lebanese RSS/Telegram
source (L'Orient Today, Naharnet) but couldn't confirm a working public
feed for either — L'Orient Today appears to be subscription-gated. Lebanon/
Hezbollah/South Lebanon coverage currently comes through Al Jazeera, Middle
East Spectator, and now Press TV/IRNA (Iran and Hezbollah are closely
linked in current coverage), plus the explicit topic-filter instruction
calling this out. If you find a working feed URL for either outlet, send it
and I'll add it directly.

**A note on source reliability:** Reuters and AP discontinued their public
RSS feeds some years back — the URLs originally in this config were dead on
arrival, which was the real reason early runs found nothing to post (see
"Troubleshooting a quiet channel" below). They've been replaced with
currently-working feeds. If a source ever stops responding, the Actions
logs will show `Feed error for <name>: ...` — that's your signal to check
whether the URL changed or the source discontinued its feed.

**Telegram channels (independent/OSINT/official):**
- BRICS News (`@brics_info`)
- Middle East Spectator (`@Middle_East_Spectator`)
- OSINTdefender (`@OSINTdefender`)
- Kremlin News / Putin (`@news_kremlin_eng`) — lean `ru_source`
- Kyiv Independent (`@KyivIndependent_official`) — lean `ua_source`, added
  for balance so the Russia-Ukraine conflict isn't only Kremlin-framed
- Fin Watch (`@Fin_Watch`)
- Ethan Levins (`@ethanlevins`)

Scraped via Telegram's public web preview (`t.me/s/<username>`) — no bot
token or API needed, since these are public channels.

**Why Kremlin + Kyiv Independent get distinct lean tags:** they're each
given their own lean (`ru_source` / `ua_source`) rather than both being
lumped into `independent`. That means a story reported by *both* counts as
2 different leans and can confirm on its own (assuming 2+ sources total) —
which is exactly the cross-check you want on this conflict specifically:
neither side's state/partisan framing alone triggers a post, but agreement
between the two (or either one plus a wire/mainstream outlet) does.

**Notes on the requested account list:**
- **Trump and Elon Musk have no Telegram channels** — Trump posts on Truth
  Social/X, Musk posts on X only. Since X access was intentionally left out
  (see below), these two aren't currently covered. If you later add X
  access, they'd go there.
- **Khamenei**: worth knowing before you rely on this — Iran's Supreme
  Leader Ali Khamenei was killed in a joint US-Israeli strike on February
  28, 2026, an event that may postdate what either of us already knew about.
  His official channel had stopped posting to Telegram back in 2018 (moved
  to X) regardless, so there was never a live Khamenei Telegram source to
  add. His successor (his son, Mojtaba Khamenei) is not confirmed to run a
  public Telegram channel as of this writing.
- **"Global News Monitor" and "Globe Observer"**: couldn't confidently
  identify a single canonical channel for either — too many similarly-named
  channels with no clear match. Left out rather than guessing wrong. If you
  have the exact `@username`, add it to `TELEGRAM_SOURCES` in `config.py`.
- **Ethan Levins**: flagged for your awareness — he's a self-described
  independent journalist who has been publicly accused of spreading
  unverified claims (including a disputed death-hoax post) during the 2026
  Iran war. He's included per your request; the cross-source confirmation
  requirement is what contains the risk of this specific source.
- **X access was intentionally skipped** for now (official API pricing
  changed in Feb 2026 to pay-per-use, no free tier — roughly $25-75/month
  for this volume via X's own API, or a few dollars/month via a third-party
  provider, which trades official-source trust for cost). Let me know if
  you want to revisit this.

## How independent/OSINT sources interact with the confirmation logic

Telegram sources are tagged with a lean other than `wire`/`finance_wire`
(`independent`, `ru_source`, or `ua_source` — see above). This means:
- A story from **one** such account alone never posts — same rule as
  everything else.
- A story needs either an auto-confirming source (wire/finance_wire), OR
  two different lean buckets to agree. Two `independent`-tagged accounts
  alone (e.g. Fin Watch + Ethan Levins both posting something no mainstream
  outlet has) will **not** trigger a post on their own — deliberately,
  since neither is a vetted news organization.

## 1. Create the Telegram bot and channel

1. Message **@BotFather** on Telegram → `/newbot` → follow prompts → copy the
   bot token.
2. Create your channel (Telegram app → New Channel), note the
   `@channel_username` (or numeric ID if private).
3. Add your bot as an **admin** of the channel (Channel settings →
   Administrators → Add Admin → search your bot).

## 2. Get a Google API key

Create one at **aistudio.google.com** (sign in → Get API key → Create API
key). This is free - no billing/card needed for the volume this bot uses
(see "Fully switched to Google Gemini" section below for details on what
it's used for and the free-tier limits).

## 3. Push this code to a GitHub repo

```bash
cd news_bot
git init
git add .
git commit -m "Initial commit"
```

Create a new **private** repo on GitHub (private matters — the dedup
database and workflow history will live here), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

## 4. Add your secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add these three:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHANNEL_ID` | `@your_channel` or numeric ID |
| `GOOGLE_API_KEY` | from aistudio.google.com |

## 5. That's it — it's already running

The workflow at `.github/workflows/news-bot.yml` runs every 30 minutes
automatically once it's on the `main` branch. No further setup.

Because this repo is **private**, GitHub Actions minutes are metered
(~2000/month on the Free plan, ~3 min per run), so GitHub drops/delays many
scheduled runs and actual runs can be 1–3 hours apart. Making the repo
public removes the minute cap entirely (public repos get unlimited Actions
minutes) and lets the schedule run much more frequently if you want a
faster, more natural cadence.

To verify it's working:
- Go to the **Actions** tab in your repo → you'll see "News Bot Run" firing
  (roughly every 1–3 hours on the metered private schedule).
- Click any run → expand "Run news bot" to see the logs (same info you'd
  get from `journalctl` on a VPS).
- You can also trigger a run immediately: Actions tab → News Bot Run →
  **Run workflow** button.

## How persistence works without a server

Each run needs to remember what it already posted, so it doesn't repeat
stories. Since GitHub Actions runners are thrown away after each run, the
workflow commits `posted_stories.db` (a small SQLite file) back to the repo
at the end of every run. You'll see small automated commits from
`news-bot` appearing in your repo's history — that's expected and is how
dedup state carries over between runs.

## Critical fixes from the second round of live testing

1. **The bot was posting its own AI's refusal/error text as if it were news.**
   When headlines got clustered together but the summarizer correctly
   recognized they were actually different events (not one story), its
   free-form refusal ("These headlines are reporting two completely
   different events...") was being posted to the channel verbatim - nothing
   was checking the output before publishing. Fixed two ways: the
   summarizer now has an explicit machine-readable escape hatch
   (`NO_SINGLE_EVENT`) it's instructed to output instead of a free-form
   refusal, and `main.py` checks for it (and, as a backup, scans for known
   refusal phrasing) and skips posting rather than publishing anything
   broken.
2. **False merging of distinct events.** Lowering the clustering threshold
   in the previous fix round overcorrected - many genuinely different
   Iran/Hormuz sub-events (which share heavy keyword overlap: "Iran",
   "strikes", "Hormuz", "Trump") were merging into one confused cluster.
   Threshold raised back from 0.4 to 0.5. The `NO_SINGLE_EVENT` check above
   is the deeper fix - it means an occasional bad merge no longer produces
   a bad post, it just gets silently skipped.
3. **The same evolving story was getting reposted every run** with
   slightly different wording (new cluster_key each time since the exact
   headline text differs hour to hour). Added near-duplicate detection: a
   story's headline is checked against everything posted in the last
   `NEAR_DUP_LOOKBACK_HOURS` (default 12) using the same similarity method
   as clustering - if it's too similar to something already posted, it's
   skipped rather than re-posted. Tunable via `NEAR_DUP_THRESHOLD` in
   `config.py`.
4. **Media never actually reached posts.** A latent bug: entries were
   re-loaded from the database for clustering, but the database schema
   didn't store `media_url`/`media_type`, so photos/videos silently never
   attached even though the fetchers were extracting them correctly. Fixed
   in `storage.py`.

## Sports and Lebanon/Hezbollah coverage

Added per request:
- **Sports**: BBC Football and Sherdog (MMA), both auto-confirming
  (`sports_wire` lean, same trust tier as wire/finance_wire). The topic
  filter (`filter.py`) restricts this to major football (World Cup,
  Champions League, top-5-league results/transfers) and major MMA (UFC
  title fights, major results) - not routine match reports.
- **Hezbollah / IRGC / South Lebanon**: rather than a dedicated RSS feed
  (I couldn't confirm a working public RSS URL for Naharnet, the obvious
  Lebanon-focused option - happy to add it if you find the exact feed
  URL), this is handled by explicitly calling out Hezbollah, IRGC, and
  South Lebanon within the Middle East conflict category in the topic
  filter, so relevant stories from your existing sources (Al Jazeera,
  Middle East Spectator, wire services) aren't accidentally filtered out
  as "too specific."

## Fix: RSS feeds failing with confusing XML errors

Some sources (UPI, Times of Israel, Kitco) intermittently failed with errors
like `mismatched tag` or `undefined entity` - these look like broken feeds
but are usually actually a bot-protection page (not the real XML) being
served to requests that don't look like they're from a browser. Fixed by
sending a standard browser User-Agent header on every RSS fetch. If a
source still fails after this, it's more likely a genuinely broken/changed
feed URL worth checking manually.

## A known trade-off: "not actually one event" skips a real story sometimes

The `NO_SINGLE_EVENT` safety check (see the earlier fix round) prevents
posting a confused merged summary when clustering incorrectly groups
different specific developments together (e.g. multiple distinct strikes
across "day 5 of the campaign" all sharing similar keywords). This is
working as intended, but it means an occasional real, important story gets
silently dropped rather than posted - the bot doesn't currently retry by
splitting the cluster into smaller groups and re-attempting, it just skips
the whole thing. Given the priority on war/MENA coverage, this is worth
being aware of: it trades a small amount of missed coverage for never
posting something contradictory. If missed stories like this become
frequent, a real fix would be to have `main.py` retry with the cluster
split into per-source-pair sub-groups when it gets `NO_SINGLE_EVENT` back,
rather than giving up entirely - this isn't implemented yet, ask if you
want it built.

## New: more wire sources for freshness/redundancy

Added DW (Deutsche Welle), France24, and Sky News World as additional
`wire`-tier (auto-confirming) sources, alongside BBC World and UPI. More
wire coverage means more stories get picked up fast and more redundancy if
one source's feed breaks.

## New: sports fixtures & results (separate system from the news pipeline)

The RSS-based sports coverage (BBC Football, Sherdog) only gives *written
articles* - published sometime after something happens, and previously
required a journalist to have written a "major" story for it to clear the
topic filter. That's fundamentally not what "tell me upcoming matches and
current results even if I'm not watching" needs.

Added `sports_scores.py`: a separate module that pulls structured
fixture/result data from **TheSportsDB's free API** for Premier League, La
Liga, Serie A, Bundesliga, Ligue 1, Champions League, World Cup, and UFC.
Each run, it checks for newly finished results and fixtures coming up in
the next 48 hours, and posts anything new as a compact digest (not one
message per match). This bypasses the news pipeline entirely - no
cross-source confirmation or topic classification, since a match result
from a sports database is just treated as true, the same way a stock price
would be.

**Three honest caveats:**
1. This uses TheSportsDB's free public test key. It's intended for exactly
   this kind of light personal use, but if it ever gets rate-limited,
   getting a free personal API key at thesportsdb.com and swapping it into
   `THESPORTSDB_API_KEY` in `config.py` is a one-line fix.
2. **The league ID numbers in `SPORTS_LEAGUES` (config.py) are from
   TheSportsDB's known/documented IDs, but I couldn't test-call the API
   directly to verify them in this environment.** If a particular league
   never produces fixtures, check the Actions logs for `Failed to fetch
   upcoming fixtures for <league>` - that tells you which one to
   double-check against TheSportsDB's site (search the league name there,
   the ID is in the URL).
3. This is not truly live/minute-by-minute (that would need a different,
   often-paid API polled every 1-2 minutes only during live matches - see
   the earlier discussion about a "real live-score tracker" as a bigger,
   separate feature). At 30-minute polling, you'll get results within 30
   minutes of a match ending and fixtures up to 48 hours ahead, which is a
   large step up from "wait for a journalist to write an article that
   clears the major-news bar," but it's not live commentary.

## New sources added from a user-suggested list

Verified and added:
- **Fabrizio Romano** (`FabrizioRomano`, Telegram) — the single most reliable
  individual source for football transfer news specifically. Tagged
  `sports_wire` (auto-confirming) given his track record on this beat.
- **ISW / Institute for the Study of War** (`ISW_official`, Telegram) — a
  respected US think tank publishing daily analysis on the Ukraine war and
  the 2026 Iran war. Higher credibility than typical OSINT accounts, but
  still tagged its own distinct lean (`osint_isw`) rather than auto-
  confirming, consistent with not auto-trusting any single non-mainstream
  source regardless of reputation.
- **Iran International** (`IranIntl_En`, Telegram) — London-based Persian
  opposition outlet, a genuine counterweight to Iran's own state media
  (Press TV/IRNA already in the source list). Tagged `ir_opposition`.

**Not added — couldn't confirm a working handle/feed for these** from the
suggested list: NOELreports, sentdefender, war_mapper, ConflictDispatch,
ESPN/SkySportsNews/OneFootball Telegram channels, ZeroHedge, Barchart,
ForexLive, Jerusalem Post, Middle East Eye, Al Arabiya English, The
National (UAE). If you can send exact `@handles` (for Telegram) or feed
URLs (for RSS) for any of these, I'll verify and wire them in rather than
guess and risk another silently-broken source.

## New: faster breaking-news Telegram sources

Added **BNO News** (`bnonews`) and **Faytuks News** (`FaytuksTelegram`) —
both legitimate, professional, fast breaking-news services frequently ahead
of mainstream outlets on developing stories. Neither auto-confirms alone
(each has its own distinct lean), consistent with not auto-trusting any
single non-major-wire source regardless of reputation or speed.

**Explicitly not added: Disclose.tv.** It's often suggested for this kind
of aggregator role, but it's a documented disinformation outlet that pushes
conspiracy theories and extremist content while presenting itself as a
neutral news aggregator. Not a source worth including here or generally.

**On "why am I not seeing more Telegram-sourced posts":** this channel's
whole design deliberately trades some speed for reliability - no single
source, however fast or reputable, triggers a post alone (except the
`wire`/`finance_wire`/`sports_wire` tiers, which are established, editorially-
reviewed news organizations, not solo accounts). A Telegram account being
first to report something real means it needs either a second, different
source to corroborate, or a wire pickup, before it posts here - that's
inherent to the "don't post unconfirmed claims" goal from the start of this
project, not a bug. Adding more Telegram sources (as done here) increases
how often that corroboration happens naturally and how many distinct
angles are available to pair up, which should increase Telegram-sourced
volume without weakening the confirmation requirement itself. If you'd
rather trade some of that reliability for more raw speed - e.g. letting a
single highly-reputable OSINT account post alone with a clear "unverified"
label - that's a real, available option, but it's a deliberate trade-off
worth deciding on purpose rather than defaulting into.

## Researched: Mario Nawfal, "Predictive History" - both added per request

**Mario Nawfal** - added (`MarioNawfal`, Telegram, lean `osint_nawfal`). Worth
remembering the earlier flag: his broader media operation includes
disclosed paid crypto promotions mixed into his news commentary. He's
treated exactly like every other individual account here though - never
auto-confirms alone, needs a second, different-lean source to actually
trigger a post, which is the main protection against that concern in
practice.

**"Predictive History"** - turned out to have a written Substack alongside
the YouTube lectures (`predictivehistory.substack.com`), added as an RSS
source (lean `analysis_predictivehistory`). Some posts there are behind a
paywall ("free trial" gating per their site) - the free RSS feed still
works for headline/topic purposes, but full body text may be truncated on
paywalled posts. Not auto-confirming, same as every other analysis source.



## New: higher-quality media, and videos that were being missed

Two related fixes via a new `media_upgrade.py`:

1. **Image quality** - the original extraction pulled whatever thumbnail
   size Telegram's list-preview page or an RSS feed's inline thumbnail
   field showed, which is often a small, compressed preview image, not
   the actual full-resolution image. Now, right before posting a
   confirmed story, the bot fetches the actual linked article/Telegram
   post page directly and reads its Open Graph tags (`og:image`,
   `og:video`) - the same full-resolution media a site shows when its
   content is shared anywhere else (that's the whole point of Open Graph
   tags). This only happens for stories that are actually about to be
   posted, not every entry from every source, to keep the extra network
   calls minimal.
2. **Missing videos** - Telegram's list-preview page doesn't always embed
   a working video URL for every video post, so some videos were silently
   never detected. Individual Telegram post permalinks reliably render OG
   tags server-side (that's how link previews work when you paste a
   Telegram link elsewhere), so this catches videos the original pass
   missed.

**One platform constraint worth knowing**: a single Telegram message can
carry a photo *or* a video, not both - so a post will use a video when the
source has one, otherwise a (now higher-quality) image, never both
attached to the same post. Sending an actual photo+video combo would need
a different message type (a media group/album) - not built, since it
wasn't clear that's specifically wanted over "make sure videos aren't
missed and images are better quality," which this addresses. Ask if you
want that too.

## New: broader media coverage (more images and videos actually attach)

A follow-up pass to attach media to many more posts - both stills and
video - after real runs were still going out as text-only too often:

1. **Images embedded only in the RSS body are now recovered.** Lots of
   feeds (Guardian, NYT, Al Jazeera, Fox, WSJ and others) never fill the
   `media:content` / `media:thumbnail` / `enclosure` fields the extractor
   used to rely on - they put the article photo as a plain `<img>` inside
   the `content:encoded` / `<description>` HTML. `fetcher.py` now parses
   that HTML as a last resort and pulls the first real image out of it,
   while filtering obvious junk (tracking pixels, feed icons, avatars,
   logos) so a 1x1 pixel never gets posted as the story photo.
2. **Videos are matched more reliably, and only when actually sendable.**
   Telegram's `sendVideo` needs a *direct* video file (`.mp4`/`.mov`/
   `.webm`); an `og:video` or `<enclosure>` that points at an embed/player
   page (YouTube, Vimeo, a JW Player iframe) can't be sent that way. Both
   the RSS extractor and `media_upgrade.py` now recognise the difference:
   real video files are attached as video, embed URLs are ignored for the
   video slot instead of being sent and failing.
3. **A failed video no longer loses the image.** `media_upgrade.py` now
   returns the best still image *alongside* any video it finds, and
   `telegram_post.py` uses a fallback chain - try the video, and if
   Telegram can't fetch it, attach the image before ever dropping to a
   text-only post. Previously a bad video URL meant the post lost its media
   entirely even when a perfectly good image was available.
4. **More Telegram post types are picked up.** The public-preview scraper
   now also catches round video-notes, GIF/animation posts, lazy-loaded
   (`data-src`) video URLs, and the first tile of grouped photo albums,
   and prefers a video over a still when a post has both.

## Researched: The Duran, Gonzalo Lira, Tucker Carlson, Andrew/Tristan Tate, Candace Owens

**Added:**
- **The Duran** (`thedurancom`) - confirmed official. Documented
  pro-Russian, contrarian editorial slant, comparable tier to Kremlin News
  already in the source list. A bias/reliability question, not a
  legitimacy one - added with the same non-auto-confirming treatment as
  everything else.
- **Candace Owens** (`CandaceOwens`) - confirmed. Described by Wikipedia as
  a "far-right political commentator... conspiracy theorist," dismissed
  from The Daily Wire in 2024 over comments characterized as antisemitic.
  Added per request, same contained treatment (never auto-confirms alone).

**Not added:**
- **Tucker Carlson** - his official Telegram (`TuckerCarlsonNetwork`)
  currently includes content actively promoting "great replacement
  theory" - a conspiracy theory with documented ties to real-world
  extremist violence, not just general controversial opinion. Same
  standard that excluded Disclose.tv earlier.
- **Andrew Tate / Tristan Tate** - not a news or analysis source; both
  face ongoing serious criminal charges (human trafficking, rape, forming
  a criminal group to exploit women) in Romania and the UK. Doesn't fit
  an automated news channel's source list regardless of bias questions.
- **Gonzalo Lira** - died January 2024 in Ukrainian custody after being
  detained for pro-Russian propaganda activity. Any channel using his name
  today isn't actually him. **Patrick Lancaster** is the real, active
  equivalent (embedded pro-Russian frontline correspondent) if you want a
  similar-style source - couldn't confirm his exact Telegram `@handle`,
  send it if you want him added.

## Researched: Ray Dalio, Justin Bennett, Lebanese Forces, Al Manar

**Added:**
- **Ray Dalio** (`RayDalio`) - his actual Telegram is explicitly labeled a
  "fans channel, no impersonation" - reposts his real commentary but isn't
  his own verified account (he mainly posts on LinkedIn/X). Flagged for
  transparency, added anyway per request with the usual non-auto-confirming
  treatment.
- **Justin Bennett / Daily Price Action** (`justinbennett`) - confirmed
  official. Forex/price-action trading analysis.

**Not added - Al Manar.** This isn't a bias/reliability call like the
others - Al Manar is Hezbollah's official media arm, separately designated
a "Specially Designated Global Terrorist" entity by the US Treasury (2006)
specifically for that role, on top of Hezbollah itself being a
US-designated Foreign Terrorist Organization. That's a different category
from a state broadcaster like Press TV - it's the direct propaganda wing of
a designated terrorist group. Same standard that excluded Disclose.tv and
Tucker Carlson's channel. Note this doesn't mean Hezbollah's stated
position is entirely absent from coverage - Press TV/IRNA and mainstream
outlets already report on Hezbollah statements when newsworthy.

**Lebanese Forces** - couldn't confirm their exact official Telegram
handle. Send it and I'll verify and add it - no issue with a legitimate
Lebanese political party for domestic balance, unlike Al Manar above.

## Tucker Carlson and Alex Jones - decision held, alternative found

Revisited given the "report what X said" framing, but held the same
position: the risk isn't reporting on controversial statements (that's
normal journalism), it's an *automated, unsupervised* pipeline recurringly
ingesting content from a source with documented extremism-adjacent content
(Tucker's channel) or adjudicated fabrication causing real harm (Alex
Jones's ~$1.5B in defamation judgments for the Sandy Hook hoax claims).
Neither added.

**Added instead: Ben Shapiro** (`benshapiro`) - confirmed official
channel. Strongly partisan conservative commentary without that same
documented track record - fits the "controversial but not extremist"
bracket alongside Candace Owens and The Duran already in the list.

**Not confirmed - send handles if wanted:** Piers Morgan, Glenn Greenwald.
Both legitimate, prominent, genuinely provocative commentators that would
fit well here, but I couldn't verify their exact official Telegram handles
with confidence.

## Fully switched to Google Gemini - no more Anthropic API dependency

Both AI calls in the pipeline now run on Google's free tier - the
Anthropic API is no longer used at all, and `ANTHROPIC_API_KEY` is no
longer needed anywhere.

- **`filter.py`** (topic classification, highest-volume call) - Gemini
  2.5 Flash-Lite (~1,000-1,500 free requests/day)
- **`summarize.py`** (writing the actual posts, lower-volume) - Gemini 2.5
  Flash (~250 free requests/day) - a stronger model for the part that
  actually matters for quality, still comfortably covering realistic daily
  post volume on the free tier

**Setup - only one API key needed now:**
1. **aistudio.google.com** → sign in → **Get API key** → Create API key
   (no billing/card needed)
2. GitHub repo → **Settings → Secrets and variables → Actions** → add
   `GOOGLE_API_KEY` with that key
3. **Remove the old `ANTHROPIC_API_KEY` secret** if it's still there (repo
   → Settings → Secrets and variables → Actions → delete it) - it's no
   longer read by anything, so leaving it doesn't break anything, but
   there's no reason to keep it either

**Honest caveats, same as before:**
- Free tier rate limits (roughly 10-15 RPM / ~1,000-1,500 RPD for
  Flash-Lite, lower for Flash) can occasionally be hit on very high-volume
  news days - both `filter.py` and `summarize.py` fail gracefully (skip
  the story, log a warning) rather than crashing the run if that happens.
- If post-writing quality feels different than before, that's expected -
  different model family. Style/tone changes go in `SYSTEM_PROMPT` in
  `summarize.py`, same as always.
- This bot now runs entirely on free tiers: GitHub Actions (free minutes),
  Google Gemini (free tier), and Telegram's Bot API (free) - no paid
  service required to run it at all at this volume.





## Audit: geopolitics, no duplicates, war news, sports - and one real gap fixed

Checked against these four requirements directly:

- **Geopolitics news**: covered extensively - 45 sources spanning Middle
  East (multiple sides), Russia-Ukraine (multiple sides), Iran (state media
  + opposition), Israel (official + independent), and dedicated analysis
  (ISW, Critical Threats Project).
- **War news/updates**: explicitly the top two priority categories in the
  topic filter, with the deepest source coverage of any category.
- **No duplicated news**: near-duplicate detection was already solid
  *within* the news pipeline - but auditing surfaced a real gap: **the
  sports fixtures/results system (`sports_scores.py`) and the article-based
  sports sources (BBC Football, Sherdog, Fabrizio Romano) never checked
  against each other.** A match result reported by TheSportsDB and also
  written up as an article by BBC Football could post twice - once as a
  score digest, once as a news post. Fixed: both systems now share the
  same near-duplicate pool (`store.get_recent_posted_titles`), so whichever
  posts first blocks the other from repeating it.
- **Selected sports news/updates**: covered two ways - major football/MMA
  *articles* (BBC Football, Sherdog, Fabrizio Romano, filtered to
  significant news only) plus actual *fixtures and results* (TheSportsDB,
  Premier League/La Liga/Serie A/Bundesliga/Ligue 1/Champions League/World
  Cup/UFC) - now deduplicated against each other per the fix above.

**One real unresolved risk, flagged honestly again**: the TheSportsDB
league ID numbers in `config.py` were never verified against a live API
call (no network access available to test them directly while building
this). If a specific league never produces fixtures/results after your
next test run, check the Actions logs for `Failed to fetch upcoming
fixtures for <league>` or simply an empty digest where you'd expect one,
and tell me which league - I'll help track down the correct ID.

## Troubleshooting a quiet channel

Zero posts on a given run is often *correct behavior*, not a bug — it only
posts when a story is both cross-source confirmed and on-topic. Check the
Actions logs (Actions tab → latest run → "run-bot" → "Run news bot") for:

- `Feed error for <name>: ...` — that source is down/broken. Check its URL.
- `Fetched X entries from sources` / `... from Telegram channels` — if
  these are 0 or very low, sources aren't returning data.
- `Formed X clusters from X recent entries` — if this ratio is close to
  1:1 (little merging), cross-source headline matching isn't finding
  matches, more likely early on with few sources/entries in the pool.
- `Skipped (off-topic): ...` — confirmed but didn't match your 5
  categories. Expected for most day-to-day news.
- No `Skipped` or `Posted` lines at all on a given run is also normal — it
  means every cluster this run was either previously evaluated (cached,
  logged silently to avoid re-processing) or none crossed the confirmation
  bar yet.
- `Failed to summarize/post cluster: ...` — an actual error worth
  investigating; the run still finishes (exit 0/green) even if an
  individual post fails, so a green run doesn't guarantee every eligible
  story actually posted.

## 6. Tuning

Edit `config.py`:
- `SOURCES` — add/remove RSS feeds. Some feed URLs occasionally change;
  if a source stops appearing in logs, check its RSS URL is still valid.
- `MIN_SOURCES_NON_WIRE` — how many cross-spectrum sources required
  (default 2).
- `TITLE_SIMILARITY_THRESHOLD` — how strictly headlines must match to be
  treated as the same event (0-1, default 0.55).
- `CLUSTER_WINDOW_MINUTES` — how far back to look when matching related
  headlines (default 180).

To change how often it runs, edit the `cron` line in
`.github/workflows/news-bot.yml` (standard cron syntax). Note: GitHub's
free-tier scheduler is best-effort and can lag behind the exact schedule
during high load — 5 minutes is close to the practical minimum.

## Known limitations

- GitHub free tier gives 2,000 Actions minutes/month for private repos; at
  30-minute polling (~1,440 runs/month) this uses roughly 1,440 minutes if
  each run takes about a minute - leaves some headroom but is closer to the
  limit than the old hourly schedule was. Worth an occasional glance at
  Settings → Billing → Actions if you add more sources or a heavier
  per-run process; if you're getting close, dropping back to hourly is a
  one-line change in `.github/workflows/news-bot.yml`.
- RSS feed URLs for some outlets change occasionally — check the Actions
  logs periodically for fetch warnings.
- Headline-similarity clustering is simple string matching, not semantic —
  it will occasionally miss a match (posts the same event twice, worded
  differently) or merge two distinct stories with similar wording. This is
  the main thing to watch/tune over the first couple weeks.
- If a run fails (e.g. a transient network error), the next scheduled run
  5 minutes later picks up where it left off — nothing is lost, just
  delayed.
