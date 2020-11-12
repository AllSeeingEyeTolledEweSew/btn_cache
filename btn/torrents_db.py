# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.
from typing import Union

import better_bencode

from . import dbver

_MIGRATIONS = dbver.SemverMigrations[dbver.Connection](
    application_id=257675987
)


@_MIGRATIONS.migrates(0, 1000000)
def _migrate_1(conn: dbver.Connection, schema: str = "main") -> None:
    assert schema == "main"
    conn.cursor().execute(
        "create table info (id integer primary key, info blob not null)"
    )


get_version = _MIGRATIONS.get_format
upgrade = _MIGRATIONS.upgrade


class TorrentInfoUpdate:
    def __init__(
        self, torrent_entry_id: int, info: Union[bytes, memoryview, bytearray]
    ) -> None:
        self._torrent_entry_id = torrent_entry_id
        self._info = info

    def apply(self, conn: dbver.Connection) -> None:
        conn.cursor().execute(
            "insert or ignore into info(id, info) values (?, ?)",
            (self._torrent_entry_id, self._info),
        )


class TorrentFileUpdate:
    def __init__(
        self,
        torrent_entry_id: int,
        torrent_file: Union[bytes, memoryview, bytearray],
    ) -> None:
        # TODO: optimize
        self._inner = TorrentInfoUpdate(
            torrent_entry_id,
            better_bencode.dumps(better_bencode.loads(torrent_file)[b"info"]),
        )

    def apply(self, conn: dbver.Connection) -> None:
        self._inner.apply(conn)
