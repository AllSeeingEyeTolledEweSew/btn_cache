# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.
import calendar
import sys
import time
import typing
from typing import Any
from typing import Dict
import warnings

from . import api_types
from . import dbver

if sys.version_info >= (3, 8):

    class _SnatchEntryRow(typing.TypedDict):
        id: int
        downloaded: int
        uploaded: int
        seed_time: int
        seeding: int
        snatch_time: int


else:
    _SnatchEntryRow = Dict[str, Any]


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
