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

from btn_cache import api_types
from btn_cache import user_db

from . import lib


class UpdateSnatchEntriesTest(lib.BaseTest):
    def setUp(self) -> None:
        self.conn = sqlite3.Connection(":memory:", isolation_level=None)
        user_db.upgrade(self.conn)

        self.entry = api_types.SnatchEntry(
            TorrentID="100",
            Downloaded="1000",
            Uploaded="2000",
            Ratio="---",
            Seedtime="86400",
            IsSeeding="1",
            SnatchTime="2000-01-01 01:02:03",
            TorrentInfo=api_types.SnatchEntryTorrentInfo(
                GroupName="S01E01",
                Series="Example",
                Year="2000",
                Source="HDTV",
                Container="MKV",
                Codec="H.264",
                Resolution="1080p",
            ),
        )

    def test_update(self) -> None:
        user_db.SnatchEntriesUpdate(self.entry).apply(self.conn)
        self.assert_golden_db(self.conn)

    def test_respect_hnr_removed(self) -> None:
        user_db.SnatchEntriesUpdate(self.entry).apply(self.conn)
        cur = self.conn.cursor()
        cur.execute("update snatchlist set hnr_removed = 1")
        self.entry["Uploaded"] = "3000"
        user_db.SnatchEntriesUpdate(self.entry).apply(self.conn)
        values = cur.execute("select hnr_removed from snatchlist").fetchall()
        self.assertEqual(values, [(1,)])
