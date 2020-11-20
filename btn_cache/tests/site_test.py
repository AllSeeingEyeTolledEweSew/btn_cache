# Copyright (c) 2020 AllSeeingEyeTolledEweSew
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

import unittest

import requests_mock

from btn_cache import site

AUTH = site.UserAuth(
    user_id=123,
    auth="dummy_auth",
    authkey="dummy_authkey",
    passkey="dummy_passkey",
)


@requests_mock.Mocker()
class UserAccessTest(unittest.TestCase):
    def test_get_feed(self, mock: requests_mock.Mocker) -> None:
        mock.get(
            "https://broadcasthe.net/feeds.php?feed=torrents_all&user=123"
            "&auth=dummy_auth&authkey=dummy_authkey"
            "&passkey=dummy_passkey",
            complete_qs=True,
            text="response",
        )
        response = site.UserAccess(auth=AUTH).get_feed("torrents_all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "response")

    def test_get_torrent(self, mock: requests_mock.Mocker) -> None:
        mock.get(
            "https://broadcasthe.net/torrents.php?action=download&id=456"
            "&torrent_pass=dummy_passkey",
            complete_qs=True,
            text="torrent",
        )
        response = site.UserAccess(auth=AUTH).get_torrent(456)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "torrent")

    def test_empty_auth(self, _: requests_mock.Mocker) -> None:
        access = site.UserAccess(auth=site.UserAuth())
        with self.assertRaises(ValueError):
            access.get_feed("dummy_feed")
        with self.assertRaises(ValueError):
            access.get_torrent(456)
