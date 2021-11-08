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

from typing import Union

import better_bencode
import dbver

_MIGRATIONS = dbver.SemverMigrations[dbver.Connection](application_id=257675987)


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
