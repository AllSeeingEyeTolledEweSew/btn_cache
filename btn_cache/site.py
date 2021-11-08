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

from typing import NamedTuple
from typing import Optional
import urllib.parse

import requests

from . import ratelimit


class UserAuth(NamedTuple):
    user_id: Optional[int] = None
    auth: Optional[str] = None
    authkey: Optional[str] = None
    passkey: Optional[str] = None
    api_key: Optional[str] = None


class UserAccess:
    def __init__(
        self,
        *,
        auth: UserAuth,
        rate_limiter: ratelimit.RateLimiter = None,
        session: requests.Session = None,
        timeout: float = 60
    ) -> None:
        if session is None:
            session = requests.Session()
        self._session = session
        self._timeout = timeout
        if rate_limiter is None:
            rate_limiter = ratelimit.RateLimiter()
        self._rate_limiter = rate_limiter
        for prefix in ("https://broadcasthe.net", "http://broadcasthe.net"):
            ratelimit.ratelimit_session(self._session, prefix, self._rate_limiter)
        self._auth = auth

    def get_rate_limiter(self) -> ratelimit.RateLimiter:
        return self._rate_limiter

    def get_feed(self, name: str) -> requests.Response:
        if (
            self._auth.auth is None
            or self._auth.user_id is None
            or self._auth.authkey is None
            or self._auth.passkey is None
        ):
            raise ValueError()
        query = urllib.parse.urlencode(
            {
                "feed": name,
                "user": self._auth.user_id,
                "auth": self._auth.auth,
                "passkey": self._auth.passkey,
                "authkey": self._auth.authkey,
            }
        )
        url = urllib.parse.urlunparse(
            ("https", "broadcasthe.net", "/feeds.php", None, query, None)
        )
        return self._session.get(url, timeout=self._timeout)

    def get_torrent(self, torrent_entry_id: int) -> requests.Response:
        if self._auth.passkey is None:
            raise ValueError()
        query = urllib.parse.urlencode(
            {
                "action": "download",
                "id": torrent_entry_id,
                "torrent_pass": self._auth.passkey,
            }
        )
        url = urllib.parse.urlunparse(
            ("https", "broadcasthe.net", "/torrents.php", None, query, None)
        )
        return self._session.get(url, timeout=self._timeout)
