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

import calendar
import time
import warnings

import dbver
from typing_extensions import TypedDict

from . import api_types


class _SnatchEntryRow(TypedDict):
    id: int
    downloaded: int
    uploaded: int
    seed_time: int
    seeding: int
    snatch_time: int


def _snatch_entry_json_to_row(entry: api_types.SnatchEntry) -> _SnatchEntryRow:
    if any(k not in api_types.SNATCH_ENTRY_KEYS for k in entry):
        warnings.warn(
            "snatchlist entry has unrecognized keys. we may need to "
            "update our parsing logic"
        )

    return _SnatchEntryRow(
        id=int(entry["TorrentID"]),
        downloaded=int(entry["Downloaded"]),
        uploaded=int(entry["Uploaded"]),
        seed_time=int(entry["Seedtime"]),
        seeding=int(entry["IsSeeding"]),
        snatch_time=calendar.timegm(
            time.strptime(entry["SnatchTime"], "%Y-%m-%d %H:%M:%S")
        ),
    )


_MIGRATIONS = dbver.SemverMigrations[dbver.Connection](
    application_id=1194369890
)


@_MIGRATIONS.migrates(0, 1000000)
def _migrate_1(conn: dbver.Connection, schema: str = "main") -> None:
    assert schema == "main"
    conn.cursor().execute(
        "create table snatchlist (id integer primary key, downloaded integer, "
        "uploaded integer, seed_time integer, seeding tinyint, "
        "snatch_time integer, hnr_removed tinyint not null default 0)"
    )


get_version = _MIGRATIONS.get_format
upgrade = _MIGRATIONS.upgrade


class SnatchEntriesUpdate:
    def __init__(self, *entries: api_types.SnatchEntry) -> None:
        self._rows = [_snatch_entry_json_to_row(entry) for entry in entries]
        if self._rows:
            # TODO: optimize?
            cols = self._rows[0].keys()
            self._query = "".join(
                (
                    "insert into snatchlist (",
                    ", ".join(cols),
                    ") values (",
                    ", ".join(f":{k}" for k in cols),
                    ") on conflict (id) do update set ",
                    ", ".join(f"{k} = :{k}" for k in cols if k != "id"),
                )
            )
        else:
            self._query = ""

    def apply(self, conn: dbver.Connection) -> None:
        if not self._rows:
            return
        conn.cursor().executemany(self._query, self._rows)


class GetSnatchlistResultUpdate:
    def __init__(self, result: api_types.GetUserSnatchlistResult) -> None:
        self._inner = SnatchEntriesUpdate(*result["torrents"].values())

    def apply(self, conn: dbver.Connection) -> None:
        self._inner.apply(conn)
