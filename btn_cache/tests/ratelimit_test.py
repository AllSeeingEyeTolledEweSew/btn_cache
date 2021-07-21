# Copyright (c) 2021 AllSeeingEyeTolledEweSew
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

import threading
import time
import unittest
import unittest.mock

import requests
import requests_mock

from btn_cache import ratelimit


class APIRateLimiterTest(unittest.TestCase):
    def test_max_calls_nonblocking(self) -> None:
        limiter = ratelimit.APIRateLimiter(blocking=False)
        for _ in range(150):
            limiter()
        # Next call should block
        with self.assertRaises(ratelimit.WouldBlock):
            limiter()

    def test_max_calls_blocking(self) -> None:
        limiter = ratelimit.APIRateLimiter(period=0.25)
        start = time.monotonic()
        for _ in range(150):
            limiter()
        limiter()
        end = time.monotonic()
        self.assertGreaterEqual(end - start, 0.25)

    def test_calls_age_out(self) -> None:
        limiter = ratelimit.APIRateLimiter(period=0.25, blocking=False)
        for _ in range(150):
            limiter()
        time.sleep(0.25)
        # Should not block
        limiter()

    def test_set_remaining_zero(self) -> None:
        limiter = ratelimit.APIRateLimiter(blocking=False)
        limiter.set_remaining(0)
        with self.assertRaises(ratelimit.WouldBlock):
            limiter()

    def test_set_remaining_decrease(self) -> None:
        limiter = ratelimit.APIRateLimiter(blocking=False)
        limiter.set_remaining(100)
        for _ in range(100):
            limiter()
        with self.assertRaises(ratelimit.WouldBlock):
            limiter()

    def test_get_set_blocking(self) -> None:
        limiter = ratelimit.APIRateLimiter(blocking=True)
        self.assertTrue(limiter.get_blocking())
        limiter.set_blocking(False)
        self.assertFalse(limiter.get_blocking())

        limiter = ratelimit.APIRateLimiter(blocking=False)
        self.assertFalse(limiter.get_blocking())
        limiter.set_blocking(True)
        self.assertTrue(limiter.get_blocking())

    def test_set_blocking_from_thread(self) -> None:
        limiter = ratelimit.APIRateLimiter(blocking=True)
        for _ in range(150):
            limiter()
        # Next call should block
        # Set up a thread to change us to non-blocking after a wait time

        def thread():
            time.sleep(0.1)
            limiter.set_blocking(False)

        threading.Thread(target=thread).start()
        with self.assertRaises(ratelimit.WouldBlock):
            limiter()


class RateLimiterTest(unittest.TestCase):
    def test_burst_without_blocking(self) -> None:
        limiter = ratelimit.RateLimiter(blocking=False, rate=0.001)
        for _ in range(10):
            limiter()
        with self.assertRaises(ratelimit.WouldBlock):
            limiter()

    def test_bucket_fills(self) -> None:
        limiter = ratelimit.RateLimiter(blocking=False, burst=10, rate=100)
        for _ in range(10):
            limiter()
        time.sleep(0.1)
        # Should not block
        for _ in range(10):
            limiter()

    def test_get_set_blocking(self) -> None:
        limiter = ratelimit.RateLimiter(blocking=True)
        self.assertTrue(limiter.get_blocking())
        limiter.set_blocking(False)
        self.assertFalse(limiter.get_blocking())

        limiter = ratelimit.RateLimiter(blocking=False)
        self.assertFalse(limiter.get_blocking())
        limiter.set_blocking(True)
        self.assertTrue(limiter.get_blocking())

    def test_set_blocking_from_thread(self) -> None:
        limiter = ratelimit.RateLimiter(blocking=True)
        for _ in range(10):
            limiter()
        # Next call should block
        # Set up a thread to change us to non-blocking after a wait time

        def thread():
            time.sleep(0.1)
            limiter.set_blocking(False)

        threading.Thread(target=thread).start()
        with self.assertRaises(ratelimit.WouldBlock):
            limiter()


class RatelimitAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = requests.Session()
        self.mock_adapter = requests_mock.adapter.Adapter()
        self.session.mount("http://", self.mock_adapter)
        self.session.mount("https://", self.mock_adapter)

    def test_calls(self) -> None:
        self.mock_adapter.register_uri("get", "http://example.com")

        rate_limit = unittest.mock.Mock()
        ratelimit.ratelimit_session(self.session, "http://", rate_limit)

        for _ in range(3):
            self.session.get("http://example.com")

        self.assertEqual(rate_limit.call_count, 3)

    def test_exception(self) -> None:
        self.mock_adapter.register_uri("get", "http://example.com")

        class DummyException(Exception):
            pass

        rate_limit = unittest.mock.Mock(side_effect=DummyException())
        ratelimit.ratelimit_session(self.session, "http://", rate_limit)

        with self.assertRaises(DummyException):
            self.session.get("http://example.com")

    def test_wouldblock_wrapped(self) -> None:
        self.mock_adapter.register_uri("get", "http://example.com")

        rate_limit = unittest.mock.Mock(side_effect=ratelimit.WouldBlock())
        ratelimit.ratelimit_session(self.session, "http://", rate_limit)

        with self.assertRaises(ratelimit.RequestWouldBlock):
            self.session.get("http://example.com")
