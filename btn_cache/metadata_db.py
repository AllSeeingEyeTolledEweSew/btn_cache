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

from typing import Any
from typing import cast
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
import warnings

import better_bencode
import dbver
import importlib_resources
from typing_extensions import TypedDict

from . import api_types


class _SeriesRow(TypedDict):
    id: int
    imdb_id: Optional[str]
    name: Optional[str]
    banner: Optional[str]
    poster: Optional[str]
    tvdb_id: Optional[int]
    tvrage_id: Optional[int]
    youtube_trailer: Optional[str]
    deleted: bool


class _GroupRow(TypedDict):
    id: int
    category: str
    name: Optional[str]
    series_id: int
    deleted: bool


class _TorrentEntryRow(TypedDict):
    id: int
    codec: Optional[str]
    container: Optional[str]
    group_id: int
    info_hash: str
    origin: Optional[str]
    release_name: Optional[str]
    resolution: Optional[str]
    size: int
    source: Optional[str]
    time: int
    snatched: int
    seeders: int
    leechers: int
    deleted: bool


class _FileInfoRow(TypedDict):
    id: int
    file_index: int
    path: bytes
    encoding: Optional[str]
    start: int
    stop: int


_Rows = Tuple[_SeriesRow, _GroupRow, _TorrentEntryRow]


def _te_json_to_rows(entry: api_types.TorrentEntry) -> _Rows:
    if any(k not in api_types.TORRENT_ENTRY_KEYS for k in entry):
        warnings.warn(
            "torrent entry has unrecognized keys. we may need to update "
            "our parsing logic"
        )

    series_row = _SeriesRow(
        id=int(entry["SeriesID"]),
        name=entry["Series"] or None,
        banner=entry["SeriesBanner"] or None,
        poster=entry["SeriesPoster"] or None,
        imdb_id=entry["ImdbID"] or None,
        tvdb_id=(int(entry["TvdbID"]) if entry["TvdbID"] else None) or None,
        tvrage_id=(int(entry["TvrageID"]) if entry["TvrageID"] else None) or None,
        youtube_trailer=entry["YoutubeTrailer"] or None,
        deleted=False,
    )
    if series_row["youtube_trailer"] == "0":
        series_row["youtube_trailer"] = None
    group_row = _GroupRow(
        id=int(entry["GroupID"]),
        category=entry["Category"],
        name=entry["GroupName"] or None,
        series_id=series_row["id"],
        deleted=False,
    )
    torrent_entry_row = _TorrentEntryRow(
        id=int(entry["TorrentID"]),
        codec=entry["Codec"] or None,
        container=entry["Container"] or None,
        group_id=group_row["id"],
        info_hash=entry["InfoHash"],
        origin=entry["Origin"] or None,
        release_name=entry["ReleaseName"] or None,
        resolution=entry["Resolution"] or None,
        size=int(entry["Size"]),
        source=entry["Source"] or None,
        time=int(entry["Time"]),
        snatched=int(entry["Snatched"]),
        seeders=int(entry["Seeders"]),
        leechers=int(entry["Leechers"]),
        deleted=False,
    )

    return series_row, group_row, torrent_entry_row


_RowArray = Tuple[
    Tuple[_SeriesRow, ...], Tuple[_GroupRow, ...], Tuple[_TorrentEntryRow, ...]
]


def _te_json_to_row_array(*entries: api_types.TorrentEntry) -> _RowArray:
    # TODO: make this more elegant
    if not entries:
        return ((), (), ())
    row_tuples = [_te_json_to_rows(entry) for entry in entries]
    # typeshed can't currently deal with this
    return cast(_RowArray, tuple(zip(*row_tuples)))


_MIGRATIONS = dbver.SemverMigrations[dbver.Connection](application_id=-1353141288)


@_MIGRATIONS.migrates(0, 1000000)
def _migrate_1(conn: dbver.Connection, schema: str) -> None:
    sql = importlib_resources.read_text("btn_cache.sql", "metadata_1.0.0.sql")
    cur = conn.cursor()
    for line in sql.splitlines():
        line = line.format(schema=schema)
        cur.execute(line)


get_version = _MIGRATIONS.get_format
upgrade = _MIGRATIONS.upgrade


def _update_series(conn: dbver.Connection, *rows: _SeriesRow) -> None:
    if not rows:
        return
    cur = conn.cursor()
    cols = rows[0].keys()
    query = "".join(
        (
            "insert into series (",
            ", ".join(cols),
            ") values (",
            ", ".join(f":{k}" for k in cols),
            ") on conflict (id) do update set ",
            ", ".join(f"{k} = :{k}" for k in cols if k != "id"),
            " where ",
            " or ".join(f"{k} is not :{k}" for k in cols if k != "id"),
        )
    )
    cur.executemany(query, rows)


def _update_groups(conn: dbver.Connection, *rows: _GroupRow) -> None:
    if not rows:
        return
    cur = conn.cursor()
    cols = rows[0].keys()
    query = "".join(
        (
            "insert into torrent_entry_group (",
            ", ".join(cols),
            ") values (",
            ", ".join(f":{k}" for k in cols),
            ") on conflict (id) do update set ",
            ", ".join(f"{k} = :{k}" for k in cols if k != "id"),
            " where ",
            " or ".join(f"{k} is not :{k}" for k in cols if k != "id"),
        )
    )
    cur.executemany(query, rows)


def _update_torrent_entries(conn: dbver.Connection, *rows: _TorrentEntryRow) -> None:
    if not rows:
        return
    cur = conn.cursor()
    cols = rows[0].keys()
    query = "".join(
        (
            "insert into torrent_entry (",
            ", ".join(cols),
            ") values (",
            ", ".join(f":{k}" for k in cols),
            ") on conflict (id) do update set ",
            ", ".join(f"{k} = :{k}" for k in cols if k != "id"),
            " where ",
            " or ".join(f"{k} is not :{k}" for k in cols if k != "id"),
        )
    )
    cur.executemany(query, rows)


class TorrentEntriesUpdate:
    def __init__(self, *entries: api_types.TorrentEntry) -> None:
        series_rows, group_rows, te_rows = _te_json_to_row_array(*entries)
        self._series_rows = {row["id"]: row for row in series_rows}.values()
        self._group_rows = {row["id"]: row for row in group_rows}.values()
        self._te_rows = {row["id"]: row for row in te_rows}.values()

    def apply(self, conn: dbver.Connection) -> None:
        _update_series(conn, *self._series_rows)
        _update_groups(conn, *self._group_rows)
        _update_torrent_entries(conn, *self._te_rows)


class UnfilteredGetTorrentsResultUpdate(TorrentEntriesUpdate):
    def __init__(self, offset: int, result: api_types.GetTorrentsResult) -> None:
        super().__init__(*result["torrents"].values())
        self._total = int(result["results"])
        self._offset = offset
        # From what I can tell, this is the ordering used by getTorrents
        self._ordered_te_rows = sorted(
            self._te_rows, key=lambda row: (-row["time"], -row["id"])
        )

    def apply(self, conn: dbver.Connection) -> None:
        super().apply(conn)
        if not self._ordered_te_rows:
            return
        oldest, newest = self._ordered_te_rows[-1], self._ordered_te_rows[0]
        cur = conn.cursor()
        cur.execute("create temp table ids (id integer not null primary key)")
        try:
            cur.executemany(
                "insert into temp.ids (id) values (?)",
                [(row["id"],) for row in self._te_rows],
            )

            if self._offset + len(self._te_rows) >= self._total:
                # This result set represents the oldest torrent entries, so
                # delete all older ones
                cur.execute(
                    "update torrent_entry set deleted = 1 "
                    "where time <= ? and id < ? and not deleted",
                    (oldest["time"], oldest["id"]),
                )
            cur.execute(
                "update torrent_entry set deleted = 1 "
                "where (not deleted) and time < ? and time > ? and "
                "id not in (select id from temp.ids)",
                (newest["time"], oldest["time"]),
            )
        finally:
            cur.execute("drop table temp.ids")


class ParsedTorrentInfoUpdate:
    def __init__(self, info_dict: Dict[bytes, Any], torrent_entry_id: int = 0) -> None:
        self._rows: List[_FileInfoRow] = []
        self._torrent_entry_id = torrent_entry_id
        encoding: Optional[str]
        if b"files" in info_dict:
            offset = 0
            files = cast(List[Dict[bytes, Any]], info_dict[b"files"])
            utf8 = b"name.utf-8" in info_dict and all(b"path.utf-8" in v for v in files)
            for index, file_dict in enumerate(files):
                length = cast(int, file_dict[b"length"])
                encoding = None
                if utf8:
                    name = cast(bytes, info_dict[b"name.utf-8"])
                    path = cast(List[bytes], file_dict[b"path.utf-8"])
                    encoding = "utf-8"
                else:
                    name = cast(bytes, info_dict[b"name"])
                    path = cast(List[bytes], file_dict[b"path"])
                path = [name] + path
                self._rows.append(
                    _FileInfoRow(
                        id=torrent_entry_id,
                        file_index=index,
                        path=better_bencode.dumps(path),
                        encoding=encoding,
                        start=offset,
                        stop=offset + length,
                    )
                )
                offset += length
        else:
            length = cast(int, info_dict[b"length"])
            utf8 = b"name.utf-8" in info_dict
            encoding = None
            if utf8:
                name = cast(bytes, info_dict[b"name.utf-8"])
                encoding = "utf-8"
            else:
                name = cast(bytes, info_dict[b"name"])
            self._rows.append(
                _FileInfoRow(
                    id=torrent_entry_id,
                    file_index=0,
                    path=better_bencode.dumps([name]),
                    encoding=encoding,
                    start=0,
                    stop=length,
                )
            )

    def apply(self, conn: dbver.Connection, torrent_entry_id: int = 0) -> None:
        if not self._rows:
            return
        assert (torrent_entry_id > 0) ^ (self._torrent_entry_id > 0)
        if torrent_entry_id > 0:
            for row in self._rows:
                row["id"] = torrent_entry_id
        cols = self._rows[0].keys()
        query = "".join(
            (
                "insert into file_info (",
                ",".join(cols),
                ") values (",
                ",".join(f":{k}" for k in cols),
                ") on conflict (id, file_index) do update set ",
                ",".join(f"{k} = :{k}" for k in cols if k not in ("id", "file_index")),
            )
        )
        conn.cursor().executemany(query, self._rows)


class TorrentInfoUpdate:
    def __init__(
        self, torrent_entry_id: int, info: Union[bytes, memoryview, bytearray]
    ) -> None:
        # TODO: optimize
        self._inner = ParsedTorrentInfoUpdate(
            better_bencode.loads(info), torrent_entry_id=torrent_entry_id
        )

    def apply(self, conn: dbver.Connection) -> None:
        self._inner.apply(conn)


class TorrentFileUpdate:
    def __init__(
        self,
        torrent_entry_id: int,
        torrent_file: Union[bytes, memoryview, bytearray],
    ) -> None:
        # TODO: optimize
        self._info_update = TorrentInfoUpdate(
            torrent_entry_id,
            better_bencode.dumps(better_bencode.loads(torrent_file)[b"info"]),
        )

    def apply(self, conn: dbver.Connection) -> None:
        self._info_update.apply(conn)
