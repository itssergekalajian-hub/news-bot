"""
Turns a cluster of headlines/posts into a short, neutral, fact-first
Telegram post - now using Google Gemini instead of Claude, so the whole
pipeline runs on Google's free tier with no Anthropic API dependency.

Uses Gemini 2.5 Flash (not Flash-Lite) for this step specifically - actual
post-writing benefits from a stronger model than the binary classification
in filter.py does, and this call runs far less often (only for stories
that already passed confirmation + relevance), so Flash's lower free-tier
daily quota (~250/day) is still comfortably enough.
"""
import os
import logging
import requests

logger = logging.getLogger("news_bot.summarize")

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

SYSTEM_PROMPT = """You are writing for a fast-moving, well-read Telegram news channel called \
MidWorld News. You will be given several headlines/posts and source names that a matching \
system believes report on the same real-world event, pulled from outlets across the political \
spectrum (left, right, wire services) and from independent/OSINT Telegram accounts and \
official channels.

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
Tehran suburb*) that leads with the single most important fact - no throat-clearing.
- Follows with 2-4 sentences of clear, vivid, concrete writing (specific numbers, places, names) \
- engaging and readable, not robotic, but every sentence must still be strictly factual with no \
editorializing, no loaded language, and no adjectives implying judgment about who's right.
- Add ONE relevant emoji at the very start of the headline line to signal the topic (e.g. 💥 \
conflict/strikes, 🕊️ diplomacy/ceasefire, 📈📉 markets/finance, 🗳️ politics/elections, 🌍 general \
world news) - exactly one, never more, never decorative-only.
- If sources meaningfully disagree on facts (not just tone), add one short line noting the \
discrepancy and which outlets report which version.
- If any source is an independent/OSINT account rather than a mainstream outlet, treat its \
claims with the same "state what was reported, don't assert as fact beyond what's corroborated" \
caution as any other source - do not give it more or less weight for being independent.
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


def summarize_cluster(cluster):
    lines = []
    for m in cluster["members"]:
        lines.append(f"- [{m['source']}] {m['title']} ({m['link']})")
    user_content = "Headlines reporting the same event:\n" + "\n".join(lines)

    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_content}]}],
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.4},
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": GOOGLE_API_KEY},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error("Summarization call failed for '%s': %s", cluster["title"][:60], e)
        return None

    if result == "NO_SINGLE_EVENT" or result.startswith("NO_SINGLE_EVENT"):
        return None

    # Defense in depth: if the model ignored the sentinel instruction and
    # wrote free-form refusal/meta-commentary instead, catch it here rather
    # than ever posting it to the channel.
    lowered = result.lower()
    if any(marker in lowered for marker in REFUSAL_MARKERS):
        logger.warning("Detected leaked refusal/meta-commentary, discarding: %s", result[:150])
        return None

    return result
