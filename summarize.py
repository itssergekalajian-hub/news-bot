"""
Turns a cluster of headlines/posts into a short, neutral, fact-first
Telegram post - now using Google Gemini instead of Claude, so the whole
pipeline runs on Google's free tier with no Anthropic API dependency.

Uses Gemini 3.5 Flash-Lite - switched from Gemini 2.5 Flash after discovering
via the account's actual quota page that 2.5 Flash only has a 20 requests/day
free-tier cap on this account, while 3.5 Flash-Lite has 500/day - the model
version choice was the bottleneck, not the account itself.
"""
import base64
import logging
import os
import time
import requests

from fetcher import fetch_article_context

logger = logging.getLogger("news_bot.summarize")

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.5-flash-lite:generateContent"
)

MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 6
# Summarization used to have no pacing at all between different clusters'
# calls (only retries of the SAME call were spaced). With single-source
# war posts now allowed, a run can need 50 individual summarize calls -
# with zero pacing that alone blows past the free tier's RPM limit, which
# is exactly what happened (23 requests in a minute against a 15 RPM cap).
PACING_DELAY_SECONDS = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024

SYSTEM_PROMPT = """You are writing for a fast-moving, well-read Telegram news channel called \
MidWorld News. You will be given several headlines/posts and source names that a matching \
system believes report on the same real-world event, pulled from outlets across the political \
spectrum (left, right, wire services) and from independent/OSINT Telegram accounts and \
official channels.

When a candidate image is attached, verify it visibly depicts the specific
story. Reject logos, text-only banners, unrelated advertisements, and generic
news graphics; never treat an unclear image as relevant.

CRITICAL FIRST CHECK: The matching system that grouped these headlines together is imperfect \
and sometimes wrongly groups headlines about DIFFERENT events that just share similar words \
(e.g. multiple distinct Iran-related developments all mentioning "strikes" and "Hormuz" but \
describing different specific incidents). Before writing anything else, check: do these \
headlines actually describe the SAME specific real-world event/development, or are they \
DIFFERENT developments (even if related to the same broader conflict/story)? \
If they are different events - even closely related ones in an ongoing conflict - respond with \
EXACTLY this and nothing else: NO_SINGLE_EVENT \
Do not explain, apologize, or describe what you would do instead - just output that exact \
token alone if the headlines don't share one specific event. This matters: the channel would \
rather skip a story than post a confused or contradictory merged summary.

If they DO describe the same event, continue below.

IMPORTANT: Always write your output in English, even if the source headlines \
are in another language (Arabic, Russian, Persian, etc.) - translate the \
substance into clear English rather than quoting or leaving any non-English text.

Write a short, punchy Telegram post that:
- Opens with ONE bolded headline-style line (wrap it in single asterisks, e.g. *Israel strikes \
Tehran suburb*) that leads with the single most important fact - no throat-clearing. This is \
MANDATORY for every single post, no exceptions - never start with plain unformatted text.
- Follows with 2-4 sentences of clear, vivid, concrete writing (specific numbers, places, names) \
- engaging and readable, not robotic, but every sentence must still be strictly factual with no \
editorializing, no loaded language, and no adjectives implying judgment about who's right.
- Add ONE relevant emoji at the very start of the headline line to signal the topic (e.g. 💥 \
conflict/strikes, 🕊️ diplomacy/ceasefire, 📈📉 markets/finance, 🗳️ politics/elections, 🌍 general \
world news) - exactly one, never more, never decorative-only. This is MANDATORY, never omit it.
- If sources meaningfully disagree on facts (not just tone), add one short line noting the \
discrepancy and which outlets report which version - using ONLY the exact source names given to \
you below, never any other name.
- CRITICAL: only ever refer to sources by the EXACT names given to you in the "Headlines reporting \
the same event" list below. NEVER invent, guess, paraphrase, or substitute a different outlet/ \
channel/account name - not even one that sounds plausible or that a headline's text happens to \
mention in passing. If a headline's own text references some other outlet by name, do not repeat \
that name as if it were one of your sources - only the bracketed [source] names given to you are \
your actual sources.
- NEVER write your own "Sources:" line, attribution footer, or citation list at the end of your \
post - that is added automatically by the system after your text. Your output should end after \
the last sentence of the write-up itself, with no source list of any kind.
- If any source is an independent/OSINT account rather than a mainstream outlet, treat its \
claims with the same "state what was reported, don't assert as fact beyond what's corroborated" \
caution as any other source - do not give it more or less weight for being independent.
- If the user message below includes an explicit note that this story is single-source, write it \
the way a normal news writer would attribute a claim to one outlet - naturally, in-sentence, \
using the source's actual name (e.g. "Clash Report says Iranian forces struck..." or "According \
to Middle East Spectator, ..."). Do NOT use a separate disclaimer tag, label, or prefix like \
"Unverified:" or "Single source:" - simple in-sentence attribution IS the signal to the reader \
that this is one account's claim, not confirmed by others. Keep it natural and readable, not \
clinical.
- Ends with a short "Why it matters" line only if genuinely non-obvious (otherwise omit it).
- Use Telegram-style single-asterisk *bold* only for the opening headline - nothing else needs \
formatting.
- No hashtags. No clickbait phrasing ("You won't believe...", "Shocking:"). No more than one emoji \
total in the whole post.
- Keep the whole post under roughly 600 characters so it reads well whether or not it's attached \
to an image/video.
- Output plain text only, ready to post as-is. No preamble like "Here is the summary."
"""

REFUSAL_MARKERS = [
    "cannot be combined", "can't responsibly combine", "completely different events",
    "completely unrelated events", "please resubmit", "please provide headlines",
    "i can only write one post", "different real-world events",
]


def _call_gemini(payload):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GEMINI_URL,
                params={"key": GOOGLE_API_KEY},
                json=payload,
                timeout=30,
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


def _download_photo(url):
    try:
        with requests.get(url, timeout=20, stream=True) as response:
            response.raise_for_status()
            mime_type = (response.headers.get("content-type") or "").split(";")[0]
            if not mime_type.startswith("image/"):
                return None
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    logger.info("Skipping oversized candidate image: %s", url[:120])
                    return None
                chunks.append(chunk)
            image = b"".join(chunks)
            if image:
                return {
                    "mimeType": mime_type,
                    "data": base64.b64encode(image).decode("ascii"),
                }
    except requests.RequestException as e:
        logger.info("Could not download candidate image %s: %s", url[:120], e)
    return None


def _summary_result(cluster, user_content, image=None):
    parts = [{"text": user_content}]
    if image:
        parts.append({"inlineData": image})
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.4},
    }

    try:
        data = _call_gemini(payload)
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error("Summarization call failed for '%s': %s", cluster["title"][:60], e)
        return None
    finally:
        time.sleep(PACING_DELAY_SECONDS)

    if result == "NO_SINGLE_EVENT" or result.startswith("NO_SINGLE_EVENT"):
        return None
    lowered = result.lower()
    if any(marker in lowered for marker in REFUSAL_MARKERS):
        logger.warning("Detected leaked refusal/meta-commentary, discarding: %s", result[:150])
        return None
    return result


def summarize_cluster(cluster, unverified=False):
    lines = []
    for i, m in enumerate(cluster["members"]):
        lines.append(f"- [{m['source']}] {m['title']} ({m['link']})")
        if i < 3:
            context = fetch_article_context(m["link"])
            if context:
                lines.append(f"  Article text: {context}")
    user_content = "Headlines reporting the same event:\n" + "\n".join(lines)

    if unverified:
        user_content += (
            "\n\nNOTE: This comes from a single independent/OSINT source only - no wire service "
            "or second source has corroborated it yet. Attribute the claim naturally by name in "
            "your sentences (e.g. \"[Source] reports...\" or \"According to [Source], ...\") "
            "rather than using a separate disclaimer label - simple attribution is enough."
        )

    for candidate in cluster.get("photo_candidates", []):
        url = candidate.get("url")
        image = _download_photo(url) if url else None
        if image is None:
            continue
        image_prompt = (
            user_content
            + "\n\nA candidate story image is attached. Decide whether it visibly "
            "depicts this specific story. Reject generic news-network logos, "
            "text-only banners, unrelated advertisements, and unrelated images. "
            "On the first line output exactly IMAGE_RELEVANT: YES or "
            "IMAGE_RELEVANT: NO. Then, only if YES, write the requested post."
        )
        visual_result = _summary_result(cluster, image_prompt, image)
        if not visual_result:
            continue
        first_line, _, visual_summary = visual_result.partition("\n")
        if (first_line.strip().upper() == "IMAGE_RELEVANT: YES"
                and visual_summary.strip()):
            return visual_summary.strip(), url
        logger.info("Gemini rejected or could not verify candidate image: %s",
                    url[:120])

    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_content}]}],
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.4},
    }

    try:
        data = _call_gemini(payload)
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error("Summarization call failed for '%s': %s", cluster["title"][:60], e)
        return None, None
    finally:
        # Pace calls to stay under the free-tier RPM limit - matters a lot
        # more now that a single run can need many individual summarize
        # calls (one per post, not batched like classification is).
        time.sleep(PACING_DELAY_SECONDS)

    if result == "NO_SINGLE_EVENT" or result.startswith("NO_SINGLE_EVENT"):
        return None, None

    # Defense in depth: if the model ignored the sentinel instruction and
    # wrote free-form refusal/meta-commentary instead, catch it here rather
    # than ever posting it to the channel.
    lowered = result.lower()
    if any(marker in lowered for marker in REFUSAL_MARKERS):
        logger.warning("Detected leaked refusal/meta-commentary, discarding: %s", result[:150])
        return None, None

    return result, None
