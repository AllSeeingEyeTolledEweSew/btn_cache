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

import sqlite3

import better_bencode

from btn_cache import torrents_db

from . import lib


class UpdateWholeTorrentInfoTest(lib.BaseTest):
    def setUp(self) -> None:
        self.conn = sqlite3.Connection(":memory:", isolation_level=None)
        torrents_db.upgrade(self.conn)

    def test_update(self) -> None:
        info = better_bencode.dumps(
            {b"name": b"test.txt", b"length": 1000, b"pieces": b"\0" * 40}
        )
        torrents_db.TorrentInfoUpdate(1, info).apply(self.conn)
        self.assert_golden_db(self.conn)
