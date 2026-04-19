import unittest
from unittest.mock import MagicMock, patch

from ts_hook_server import BrowserProxy
from timeline_agent import AccessPathFilter


class BrowserProxyTests(unittest.TestCase):
    def test_normalize_waits_after_goto(self):
        proxy = BrowserProxy()
        page = MagicMock()
        page.url = "about:blank"
        proxy._page = page
        proxy._session = MagicMock()
        proxy._ensure_cloudflare = MagicMock()

        with patch("ts_hook_server.time.sleep"):
            proxy._normalize()

        page.goto.assert_called_once_with("https://truthsocial.com/")
        page.wait_for_load_state.assert_called_once_with("domcontentloaded")

    def test_normalize_waits_after_reload(self):
        proxy = BrowserProxy()
        page = MagicMock()
        page.url = "https://truthsocial.com/home"
        proxy._page = page
        proxy._session = MagicMock()
        proxy._ensure_cloudflare = MagicMock()
        proxy._last_reloaded = 0

        with patch("ts_hook_server.time.sleep"), patch("ts_hook_server.time.time", return_value=1000):
            proxy._normalize()

        page.reload.assert_called_once_with()
        page.wait_for_load_state.assert_called_once_with("domcontentloaded")

    def test_raw_fetch_retries_once_after_evaluate_failure(self):
        proxy = BrowserProxy()
        page = MagicMock()
        page.evaluate.side_effect = [RuntimeError("navigating"), "ok"]
        proxy._page = page

        with patch.object(proxy, "_wait_for_page_ready") as wait_mock, patch("ts_hook_server.time.sleep"):
            result = proxy._raw_fetch(method="GET", url="https://truthsocial.com/api/test")

        self.assertEqual(result, "ok")
        self.assertEqual(page.evaluate.call_count, 2)
        wait_mock.assert_called_once_with()

    def test_raw_fetch_raises_after_second_evaluate_failure(self):
        proxy = BrowserProxy()
        page = MagicMock()
        page.evaluate.side_effect = [RuntimeError("first"), RuntimeError("second")]
        proxy._page = page

        with patch.object(proxy, "_wait_for_page_ready") as wait_mock, patch("ts_hook_server.time.sleep"):
            with self.assertRaises(RuntimeError):
                proxy._raw_fetch(method="GET", url="https://truthsocial.com/api/test")

        self.assertEqual(page.evaluate.call_count, 2)
        wait_mock.assert_called_once_with()

    def test_timeline_access_path_filter_suppresses_alert_logs(self):
        filter_ = AccessPathFilter("/api/v1/alerts")
        allowed = MagicMock()
        allowed.getMessage.return_value = "GET /api/v1/statuses/123"
        suppressed = MagicMock()
        suppressed.getMessage.return_value = "GET /api/v1/alerts?category=mentions"

        self.assertTrue(filter_.filter(allowed))
        self.assertFalse(filter_.filter(suppressed))


if __name__ == "__main__":
    unittest.main()
