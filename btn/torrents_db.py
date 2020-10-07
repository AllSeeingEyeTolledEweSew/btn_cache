# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.
import calendar
import contextlib
import os
import threading
import time
import warnings
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypedDict
from typing import Union
from typing import cast

import apsw
import better_bencode

from . import api_types
from . import dbver


_MIGRATIONS = dbver.SemverMigrations(application_id=257675987)
@_MIGRATIONS.migrates(0, 1000000)
def _migrate_1(conn:apsw.Connection, schema:str="main") -> None:
    assert schema == "main"
    conn.cursor().execute(
        "create table info (id integer primary key, info blob not null)")


get_version = _MIGRATIONS.get_format
upgrade = _MIGRATIONS.upgrade


class TorrentInfoUpdate:

    def __init__(self, torrent_entry_id:int, info:Union[bytes, memoryview, bytearray]) -> None:
        self._torrent_entry_id = torrent_entry_id
        self._info = info

    def apply(self, conn:apsw.Connection) -> None:
        conn.cursor().execute(
            "insert or ignore into info(id, info) values (?, ?)",
            (self._torrent_entry_id, self._info))


class TorrentFileUpdate:

    def __init__(self, torrent_entry_id:int, torrent_file:Union[bytes,
        memoryview, bytearray]) -> None:
        # TODO: optimize
        self._inner = TorrentInfoUpdate(torrent_entry_id,
                better_bencode.dumps(better_bencode.loads(torrent_file)[b"info"]))

    def apply(self, conn:apsw.Connection) -> None:
        self._inner.apply(conn)
