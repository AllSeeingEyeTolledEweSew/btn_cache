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

import logging
import math
import threading
import time
from typing import Any
from typing import Callable
from typing import List

import requests

_LOG = logging.getLogger(__name__)


class Error(Exception):
    pass


class WouldBlock(Error):
    def __init__(self, wait_time: float = None) -> None:
        self.wait_time = wait_time


class APIRateLimiter:

    _MAX_CALLS = 150
    _PERIOD = 3600

    def __init__(
        self,
        *,
        max_calls: int = _MAX_CALLS,
        period: float = _PERIOD,
        blocking: bool = True
    ) -> None:
        self._max_calls = max_calls
        self._period = period
        self._blocking = blocking
        self._condition = threading.Condition()
        # sorted monotonic timestamps of calls, in interval (now - period, now]
        self._calls: List[float] = []

    def get_blocking(self) -> bool:
        with self._condition:
            return self._blocking

    def set_blocking(self, blocking: bool) -> None:
        with self._condition:
            self._blocking = blocking
            self._condition.notify_all()

    def _trim(self, now: float) -> None:
        while self._calls and self._calls[0] <= now - self._period:
            self._calls.pop(0)
        while self._calls and self._calls[-1] > now:
            self._calls.pop(-1)

    def set_remaining(self, remaining: int) -> None:
        now = time.monotonic()
        with self._condition:
            self._trim(now)
            delta = self._max_calls - len(self._calls) - remaining
            if delta > 0:
                # Mark N synthetic calls, made at evenly-distributed times
                for i in range(delta):
                    self._calls.append(now - i * self._period / delta)
                self._calls.sort()
            elif delta < 0:
                # Disregard the N most recent calls
                del self._calls[-delta:]

    def _try_call(self) -> bool:
        now = time.monotonic()
        with self._condition:
            self._trim(now)
            if len(self._calls) + 1 <= self._max_calls:
                # Mark one call, made right now
                self._calls.append(now)
                _LOG.debug(
                    "making 1 call, %d remaining",
                    max(self._max_calls - len(self._calls), 0),
                )
                return True
            nth_oldest = self._calls[-self._max_calls]
            wait_time = max(nth_oldest + self._period - now, 0)
        if wait_time > 0:
            with self._condition:
                if not self._blocking:
                    raise WouldBlock(wait_time)
                _LOG.debug("waiting %.1fs to rate limit calls", wait_time)
                self._condition.wait(wait_time)
        return False

    def __call__(self) -> None:
        while not self._try_call():
            pass


class RateLimiter:
    _RATE = 0.2
    _BURST = 10

    def __init__(
        self,
        *,
        rate: float = _RATE,
        burst: float = _BURST,
        blocking: bool = True
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._blocking = blocking
        self._condition = threading.Condition()
        self._zero_time = -math.inf

    def get_blocking(self) -> bool:
        with self._condition:
            return self._blocking

    def set_blocking(self, blocking: bool) -> None:
        with self._condition:
            self._blocking = blocking
            self._condition.notify_all()

    def _try_call(self) -> bool:
        now = time.monotonic()
        with self._condition:
            have = min((now - self._zero_time) * self._rate, self._burst)
            if have >= 1:
                remaining = have - 1
                _LOG.debug("consuming 1 token, %.1f remaining", remaining)
                self._zero_time = now - remaining / self._rate
                return True
            wait_time = max((1 - have) / self._rate, 0)
        with self._condition:
            if wait_time > 0:
                if not self._blocking:
                    raise WouldBlock(wait_time)
                _LOG.debug("waiting %.1fs to rate limit requests", wait_time)
                self._condition.wait(wait_time)
        return False

    def __call__(self) -> None:
        while not self._try_call():
            pass


class RequestWouldBlock(requests.RequestException):
    def __init__(self, wait_time: float = None) -> None:
        self.wait_time = wait_time


class _RateLimitAdapter(requests.adapters.BaseAdapter):
    # Accept a RateLimiter rather than Callable, in case we ever get the chance
    # to adjust parameters based on returned headers
    def __init__(
        self,
        *,
        rate_limiter: Callable[[], Any],
        upstream: requests.adapters.BaseAdapter
    ):
        super().__init__()
        self._rate_limiter = rate_limiter
        self._upstream = upstream

    def close(self) -> None:
        pass

    def send(
        self,
        request: Any,
        stream: Any = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> Any:
        try:
            self._rate_limiter()
        except WouldBlock as exc:
            raise RequestWouldBlock(exc.wait_time)
        return self._upstream.send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


def ratelimit_session(
    session: requests.Session, url_prefix: str, rate_limiter: Callable[[], Any]
) -> requests.Session:
    upstream = session.get_adapter(url_prefix)
    adapter = _RateLimitAdapter(rate_limiter=rate_limiter, upstream=upstream)
    session.mount(url_prefix, adapter)
    return session
