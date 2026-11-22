import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "test-channel")

import feedparser
from PIL import Image
import requests

import config
import fetcher
import media_upgrade
import summarize
import telegram_post


class FakeFeedResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def _http_response(status, payload):
    response = requests.Response()
    response.status_code = status
    response._content = payload
    response.headers["Content-Type"] = "application/json"
    return response


class FeedAndMediaTests(unittest.TestCase):
    def test_feed_fetch_sanitizes_ampersands_and_keeps_bozo_entries(self):
        raw = b"<rss><channel><item><title>Oil & gas</title></item></channel></rss>"
        sanitized = fetcher._sanitize_feed_xml(raw)
        actual_feed = feedparser.parse(sanitized)
        self.assertEqual(actual_feed.entries[0].title, "Oil & gas")
        parsed_feed = SimpleNamespace(
            bozo=True,
            bozo_exception=ValueError("recovered XML issue"),
            entries=[SimpleNamespace(
                title="Oil & gas", link="https://news.test/story")],
        )
        with (
            mock.patch.object(fetcher, "SOURCES", [{
                "name": "Test", "url": "https://feed.test/rss", "lean": "wire",
            }]),
            mock.patch.object(fetcher.FEED_SESSION, "get",
                              return_value=FakeFeedResponse(raw)) as get,
            mock.patch.object(fetcher.feedparser, "parse",
                              return_value=parsed_feed) as parse,
            mock.patch.object(fetcher, "_extract_rss_media",
                              return_value=(None, None, [])),
            mock.patch.object(fetcher, "fetch_all_telegram_entries",
                              return_value=[]),
        ):
            entries = fetcher.fetch_all_entries()

        get.assert_called_once_with("https://feed.test/rss", timeout=12)
        parse.assert_called_once_with(sanitized)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Oil & gas")
        self.assertIn("Chrome/124.0.0.0", fetcher.FEED_SESSION.headers["User-Agent"])
        self.assertEqual(
            fetcher.FEED_SESSION.headers["Accept"],
            "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        )
        self.assertEqual(fetcher.FEED_SESSION.headers["Accept-Language"],
                         "en-US,en;q=0.9")

    def test_html_challenge_is_logged_once_and_never_parsed(self):
        fetcher._CHALLENGED_FEEDS_LOGGED.clear()
        challenge = FakeFeedResponse(b"  <!DOCTYPE html><html>challenge</html>")
        challenge.raise_for_status = mock.Mock(
            side_effect=requests.HTTPError("403 challenge"))
        with (
            mock.patch.object(fetcher, "SOURCES", [{
                "name": "Blocked Feed", "url": "https://feed.test/rss", "lean": "wire",
            }]),
            mock.patch.object(fetcher.FEED_SESSION, "get",
                              return_value=challenge),
            mock.patch.object(fetcher.feedparser, "parse") as parse,
            mock.patch.object(fetcher, "fetch_all_telegram_entries",
                              return_value=[]),
            self.assertLogs(fetcher.logger, level="WARNING") as logs,
        ):
            fetcher.fetch_all_entries()
            fetcher.fetch_all_entries()

        parse.assert_not_called()
        challenge.raise_for_status.assert_not_called()
        self.assertEqual(len(logs.records), 1)

    def test_direct_rss_images_are_collected_before_article_scrape_fallback(self):
        entry = SimpleNamespace(
            link="https://publisher.test/story",
            media_content=[{"url": "https://cdn.test/content.jpg",
                            "type": "image/jpeg"}],
            media_thumbnail=[{"url": "https://cdn.test/thumb.jpg"}],
            enclosures=[{"href": "https://cdn.test/enclosure.webp",
                         "type": "image/webp"}],
            summary='<p><img src="/summary.jpg"></p>',
            content=[{"value": '<img src="https://cdn.test/content-body.png">'}],
        )
        with mock.patch.object(
                fetcher, "_fetch_article_details",
                return_value=(["https://cdn.test/og.jpg"], None)) as scrape:
            media_url, media_type, candidates = fetcher._extract_rss_media(entry)

        scrape.assert_called_once_with("https://publisher.test/story")
        self.assertEqual(media_type, "photo")
        self.assertEqual(media_url, "https://cdn.test/content.jpg")
        self.assertEqual(
            [candidate["url"] for candidate in candidates],
            [
                "https://cdn.test/content.jpg",
                "https://cdn.test/thumb.jpg",
                "https://cdn.test/enclosure.webp",
                "https://publisher.test/summary.jpg",
                "https://cdn.test/content-body.png",
                "https://cdn.test/og.jpg",
            ],
        )

    def test_og_media_skips_generic_logo_images(self):
        html = (
            '<meta property="og:image" content="https://cdn.test/logo-header.png">'
        )
        response = FakeFeedResponse(html.encode())
        response.text = html
        response.raise_for_status = lambda: None
        with mock.patch.object(requests, "get", return_value=response):
            self.assertEqual(
                media_upgrade.fetch_og_media("https://news.test/story"),
                (None, None),
            )
        self.assertFalse(media_upgrade._usable_image(
            "https://cdn.test/sprite-logo.png"))
        self.assertTrue(media_upgrade._usable_image(
            "https://cdn.test/story-photo-16x9.jpg"))

    def test_telegram_accepts_474_by_266_photo(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (474, 266), "blue").save(image_bytes, format="JPEG")
        self.assertIsNotNone(telegram_post._prepare_photo(image_bytes.getvalue()))

    def test_gemini_uses_next_model_after_404(self):
        responses = [
            _http_response(404, b'{"error":"missing model"}'),
            _http_response(
                200,
                b'{"candidates":[{"content":{"parts":[{"text":"Headline"}]}}]}',
            ),
        ]
        with (
            mock.patch.object(summarize.requests, "post",
                              side_effect=responses) as post,
            mock.patch.object(summarize.time, "sleep"),
        ):
            data = summarize._call_gemini({"contents": []})

        self.assertIn("candidates", data)
        self.assertIn("gemini-3.5-flash-lite", post.call_args_list[0].args[0])
        self.assertIn("gemini-3.8-flash", post.call_args_list[1].args[0])

    def test_gemini_ignores_thoughts_and_retries_short_bullet_output(self):
        short = {
            "candidates": [{
                "content": {"parts": [
                    {"thought": True, "text": "internal plan"},
                    {"text": "Headline\n- First verified fact"},
                ]},
            }],
        }
        complete = {
            "candidates": [{
                "content": {"parts": [
                    {"thought": True, "text": "another internal plan"},
                    {"text": "Headline\n- First verified fact\n- Second verified fact"},
                ]},
            }],
        }
        cluster = {"title": "Headline", "members": []}
        with (
            mock.patch.object(summarize, "_call_gemini",
                              side_effect=[short, short, complete]) as call,
            mock.patch.object(summarize.time, "sleep"),
        ):
            result = summarize._summary_result(cluster, "source facts")

        self.assertEqual(call.call_count, 3)
        self.assertEqual(call.call_args_list[0].kwargs["model"],
                         summarize.GEMINI_MODELS[0])
        self.assertEqual(call.call_args_list[2].kwargs["model"],
                         summarize.GEMINI_MODELS[1])
        self.assertNotIn("internal plan", result)
        self.assertEqual(result.count("\n- "), 2)
        self.assertEqual(
            summarize._response_text({
                "candidates": [{
                    "content": {"parts": [
                        {"thought": True, "text": "never include this"},
                    ]},
                }],
            }),
            "",
        )

    def test_inactive_public_telegram_handles_are_not_configured(self):
        usernames = {source["username"] for source in config.TELEGRAM_SOURCES}
        self.assertNotIn("realDonaldTrump", usernames)
        self.assertNotIn("netanyahu", usernames)


if __name__ == "__main__":
    unittest.main()
