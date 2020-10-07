from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Set
from typing import Tuple
from typing import Type
from typing import TypeVar
from typing import cast

import apsw
import better_bencode

from btn import api_types
from btn import torrents_db

from . import lib



class UpdateWholeTorrentInfoTest(lib.BaseTest):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        torrents_db.upgrade(self.conn)

    def test_update(self) -> None:
        info = better_bencode.dumps({
            b"name": b"test.txt",
            b"length": 1000,
            b"pieces": b"\0" * 40})
        torrents_db.TorrentInfoUpdate(1, info).apply(self.conn)
        self.assert_golden_db(self.conn)
