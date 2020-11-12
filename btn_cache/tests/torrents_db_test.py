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
