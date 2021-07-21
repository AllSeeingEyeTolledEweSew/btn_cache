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

import sqlite3
from typing import Any
from typing import cast
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type
from typing import TypeVar

import better_bencode

from btn_cache import api_types
from btn_cache import metadata_db

from . import lib


class TableState:
    def __init__(self, conn: sqlite3.Connection, table: str) -> None:
        cur = conn.cursor()
        self.updated_at = dict(
            cast(
                Iterable[Tuple[int, int]],
                cur.execute(f"select id, updated_at from {table}"),
            )
        )
        self.deleted = {
            i
            for i, in cast(
                Iterable[Tuple[int]],
                cur.execute(f"select id from {table} where deleted"),
            )
        }


_D = TypeVar("_D", bound="TableStateDelta")
_S = Set[int]
_I = Iterable[int]


class TableStateDelta:
    @classmethod
    def diff(cls: Type[_D], a: TableState, b: TableState) -> _D:
        disappeared = set(a.updated_at) - set(b.updated_at)
        went_backward = {
            i for i in a.updated_at if a.updated_at[i] > b.updated_at[i]
        }
        new = set(b.updated_at) - set(a.updated_at)
        modified = {
            i for i in a.updated_at if a.updated_at[i] < b.updated_at[i]
        }
        deleted = b.deleted - a.deleted
        undeleted = a.deleted - b.deleted

        return cls(
            disappeared=disappeared,
            went_backward=went_backward,
            new=new,
            modified=modified,
            deleted=deleted,
            undeleted=undeleted,
        )

    def __init__(
        self,
        *,
        disappeared: _I = (),
        went_backward: _I = (),
        new: _I = (),
        modified: _I = (),
        deleted: _I = (),
        undeleted: _I = (),
    ) -> None:
        self.disappeared = set(disappeared)
        self.went_backward = set(went_backward)
        self.new: _S
        self.modified: _S
        self.deleted: _S
        self.undeleted: _S
        self.set(
            modified=modified, new=new, deleted=deleted, undeleted=undeleted
        )

    def set(
        self,
        *,
        modified: _I = (),
        new: _I = (),
        deleted: _I = (),
        undeleted: _I = (),
    ) -> None:
        self.new = set(new)
        self.deleted = set(deleted)
        self.undeleted = set(undeleted)
        self.modified = set(modified) | self.deleted | self.undeleted


def assert_equal(got: _S, expected: _S, table: str, fact: str):
    if got == expected:
        return
    complaints: List[str] = []
    unexpected = got - expected
    missing = expected - got
    if unexpected:
        complaints.append(f"unexpected: {unexpected}")
    if missing:
        complaints.append(f"missing expected: {missing}")
    raise AssertionError(f"{table}: {fact}: " + "; ".join(complaints))


class ChangeChecker:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

        self.expected_series_delta = TableStateDelta()
        self.expected_group_delta = TableStateDelta()
        self.expected_entry_delta = TableStateDelta()

        self.a_series = TableState(conn, "series")
        self.a_group = TableState(conn, "torrent_entry_group")
        self.a_entry = TableState(conn, "torrent_entry")

    def series(
        self, *, ids: _I = (), new: _I = (), delete: _I = (), undelete: _I = ()
    ) -> "ChangeChecker":
        self.expected_series_delta.set(
            modified=ids, new=new, deleted=delete, undeleted=undelete
        )
        return self

    def group(
        self, *, ids: _I = (), new: _I = (), delete: _I = (), undelete: _I = ()
    ) -> "ChangeChecker":
        self.expected_group_delta.set(
            modified=ids, new=new, deleted=delete, undeleted=undelete
        )
        return self

    def entry(
        self, *, ids: _I = (), new: _I = (), delete: _I = (), undelete: _I = ()
    ) -> "ChangeChecker":
        self.expected_entry_delta.set(
            modified=ids, new=new, deleted=delete, undeleted=undelete
        )
        return self

    def check(
        self, table: str, a_state: TableState, expected: TableStateDelta
    ) -> None:
        b_state = TableState(self.conn, table)

        diff = TableStateDelta.diff(a_state, b_state)

        assert_equal(diff.disappeared, set(), table, "disappeared")
        assert_equal(diff.went_backward, set(), table, "went_backward")

        assert_equal(diff.modified, expected.modified, table, "modified")
        assert_equal(diff.new, expected.new, table, "new")
        assert_equal(diff.deleted, expected.deleted, table, "deleted")
        assert_equal(diff.undeleted, expected.undeleted, table, "undeleted")

    def __enter__(self) -> "ChangeChecker":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        if exc_value is not None:
            return

        self.check("series", self.a_series, self.expected_series_delta)
        self.check(
            "torrent_entry_group", self.a_group, self.expected_group_delta
        )
        self.check("torrent_entry", self.a_entry, self.expected_entry_delta)


class BaseMetadataTest(lib.BaseTest):
    def setUp(self) -> None:
        self.conn = sqlite3.Connection(":memory:", isolation_level=None)
        metadata_db.upgrade(self.conn)

        self.series_id = 345
        self.group_id = 234
        self.entry_id = 123
        self.entry = api_types.TorrentEntry(
            Category="Episode",
            Codec="H.264",
            Container="MKV",
            DownloadURL="https://example.com/unused",
            GroupID="234",
            GroupName="S01E01",
            ImdbID="1234567",
            InfoHash="F" * 40,
            Leechers="1",
            Origin="P2P",
            ReleaseName="example.s01e01.coolkids",
            Resolution="1080p",
            Seeders="10",
            Series="Example",
            SeriesBanner="https://example.com/banner.jpg",
            SeriesID="345",
            SeriesPoster="https://example.com/poster.jpg",
            Size="12345678",
            Snatched="100",
            Source="HDTV",
            Time="123456789",
            TorrentID="123",
            TvdbID="456",
            TvrageID="567",
            YoutubeTrailer="https://www.youtube.com/v/abcdefghijk",
        )

    def assert_changes(self) -> ChangeChecker:
        return ChangeChecker(self.conn)

    def update_entry(self, entry: api_types.TorrentEntry) -> None:
        metadata_db.TorrentEntriesUpdate(entry).apply(self.conn)

    def update_torrent_info(self, entry_id, info: bytes) -> None:
        metadata_db.TorrentInfoUpdate(entry_id, info).apply(self.conn)

    def update_search_result(
        self, offset: int, result: api_types.GetTorrentsResult
    ) -> None:
        update = metadata_db.UnfilteredGetTorrentsResultUpdate(offset, result)
        update.apply(self.conn)


class UpdateTorrentEntryTest(BaseMetadataTest):
    def test_insert_new(self) -> None:
        self.update_entry(self.entry)
        self.assert_golden_db(self.conn)

    def test_no_change(self) -> None:
        self.update_entry(self.entry)
        with self.assert_changes():
            self.update_entry(self.entry)

    def test_series_update(self) -> None:
        i = self.series_id
        self.update_entry(self.entry)

        for value in ("9999999", "", "1234567"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["ImdbID"] = value
                self.update_entry(self.entry)

        for value in ("NewSeries", "", "AnotherSeries"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["Series"] = value
                self.update_entry(self.entry)

        for value in ("https://newbanner.com", "", "https://another.com"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["SeriesBanner"] = value
                self.update_entry(self.entry)

        for value in ("https://newposter.com", "", "https://another.com"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["SeriesPoster"] = value
                self.update_entry(self.entry)

        for value in ("999", "", "9876"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["TvdbID"] = value
                self.update_entry(self.entry)

        for value in ("999", "", "9876"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["TvrageID"] = value
                self.update_entry(self.entry)

        for value in ("https://youtube.com/new", "", "https://video"):
            with self.assert_changes() as change:
                change.series(ids=(i,))
                self.entry["YoutubeTrailer"] = value
                self.update_entry(self.entry)

    def test_group_update(self) -> None:
        i = self.group_id
        self.update_entry(self.entry)

        for value in ("Season", "Another"):
            with self.assert_changes() as change:
                change.group(ids=(i,))
                self.entry["Category"] = value
                self.update_entry(self.entry)

        for value in ("NewGroup", "", "AnotherGroup"):
            with self.assert_changes() as change:
                change.group(ids=(i,))
                self.entry["GroupName"] = value
                self.update_entry(self.entry)

    def test_entry_update(self) -> None:
        i = self.entry_id
        self.update_entry(self.entry)

        for value in ("H.265", "", "AnotherCodec"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Codec"] = value
                self.update_entry(self.entry)

        for value in ("AVI", "", "AnotherContainer"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Container"] = value
                self.update_entry(self.entry)

        for value in ("0" * 40, "1" * 40):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["InfoHash"] = value
                self.update_entry(self.entry)

        for value in ("Scene", "", "OtherOrigin"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Origin"] = value
                self.update_entry(self.entry)

        for value in ("TestRelease", "", "AnotherRelease"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["ReleaseName"] = value
                self.update_entry(self.entry)

        for value in ("720p", "", "AnotherResolution"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Resolution"] = value
                self.update_entry(self.entry)

        for value in ("BluRay", "", "AnotherSource"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Source"] = value
                self.update_entry(self.entry)

        for value in ("9876", "8765"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Size"] = value
                self.update_entry(self.entry)

        for value in ("9876543", "8765432"):
            with self.assert_changes() as change:
                change.entry(ids=(i,))
                self.entry["Time"] = value
                self.update_entry(self.entry)

        with self.assert_changes():
            self.entry["Leechers"] = "999"
            self.update_entry(self.entry)
        with self.assert_changes():
            self.entry["Seeders"] = "999"
            self.update_entry(self.entry)
        with self.assert_changes():
            self.entry["Snatched"] = "999"
            self.update_entry(self.entry)

    def test_delete_all(self) -> None:
        self.update_entry(self.entry)
        cur = self.conn.cursor()
        with self.assert_changes() as change:
            change.series(delete=(self.series_id,))
            change.group(delete=(self.group_id,))
            change.entry(delete=(self.entry_id,))
            cur.execute("update torrent_entry set deleted = 1")

    def test_new_series(self) -> None:
        self.update_entry(self.entry)
        self.entry["SeriesID"] = str(self.series_id + 1)
        with self.assert_changes() as change:
            change.series(new=(self.series_id + 1,), delete=(self.series_id,))
            change.group(ids=(self.group_id,))
            self.update_entry(self.entry)

    def test_new_group(self) -> None:
        self.update_entry(self.entry)
        self.entry["GroupID"] = str(self.group_id + 1)
        with self.assert_changes() as change:
            change.group(delete=(self.group_id,), new=(self.group_id + 1,))
            change.entry(ids=(self.entry_id,))
            self.update_entry(self.entry)

    def test_new_entry(self) -> None:
        self.update_entry(self.entry)
        self.entry["TorrentID"] = str(self.entry_id + 1)
        with self.assert_changes() as change:
            change.entry(new=(self.entry_id + 1,))
            self.update_entry(self.entry)

    def test_abort(self) -> None:
        self.update_entry(self.entry)
        cur = self.conn.cursor()
        with self.assert_changes():
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("delete from series")
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("delete from torrent_entry_group")
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("delete from torrent_entry")

            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("update series set id = -1")
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("update torrent_entry_group set id = -1")
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute("update torrent_entry set id = -1")

    def test_empty_values(self) -> None:
        cur = self.conn.cursor()

        self.entry["ImdbID"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select imdb_id from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["Series"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select name from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["SeriesBanner"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select banner from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["SeriesPoster"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select poster from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["TvdbID"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select tvdb_id from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["TvrageID"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select tvrage_id from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["YoutubeTrailer"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select youtube_trailer from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["GroupName"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select name from torrent_entry_group").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["Codec"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select codec from torrent_entry").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["Container"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select container from torrent_entry").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["Origin"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select origin from torrent_entry").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["ReleaseName"] = ""
        self.update_entry(self.entry)
        values = cur.execute(
            "select release_name from torrent_entry"
        ).fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["Resolution"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select resolution from torrent_entry").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["Source"] = ""
        self.update_entry(self.entry)
        values = cur.execute("select source from torrent_entry").fetchall()
        self.assertEqual(values, [(None,)])

    def test_zero_values(self) -> None:
        cur = self.conn.cursor()

        self.entry["TvdbID"] = "0"
        self.update_entry(self.entry)
        values = cur.execute("select tvdb_id from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["TvrageID"] = "0"
        self.update_entry(self.entry)
        values = cur.execute("select tvrage_id from series").fetchall()
        self.assertEqual(values, [(None,)])

        self.entry["YoutubeTrailer"] = "0"
        self.update_entry(self.entry)
        values = cur.execute("select youtube_trailer from series").fetchall()
        self.assertEqual(values, [(None,)])


class UpdateTorrentInfoTest(BaseMetadataTest):
    def assert_file_info(
        self, expected: List[Tuple[int, int, bytes, Optional[str], int, int]]
    ) -> None:
        got = self.conn.cursor().execute("select * from file_info").fetchall()
        self.assertEqual(got, expected)

    def check(
        self,
        info: Dict[bytes, Any],
        expected: List[Tuple[int, int, bytes, Optional[str], int, int]],
    ) -> None:
        self.update_entry(self.entry)
        with self.assert_changes() as change:
            change.entry(ids=(self.entry_id,))
            self.update_torrent_info(self.entry_id, better_bencode.dumps(info))
        self.assert_file_info(expected)

    def test_single_nonutf8(self) -> None:
        self.check(
            {b"name": b"test.txt", b"length": 123},
            [(self.entry_id, 0, b"l8:test.txte", None, 0, 123)],
        )

    def test_single_utf8(self) -> None:
        self.check(
            {
                b"name": b"test.txt",
                b"name.utf-8": b"testutf8.txt",
                b"length": 123,
            },
            [(self.entry_id, 0, b"l12:testutf8.txte", "utf-8", 0, 123)],
        )

    def test_multi_nonutf8(self) -> None:
        self.check(
            {
                b"name": b"test",
                b"files": [
                    {b"path": [b"a", b"test.txt"], b"length": 100},
                    {b"path": [b"b", b"other.txt"], b"length": 50},
                ],
            },
            [
                (self.entry_id, 0, b"l4:test1:a8:test.txte", None, 0, 100),
                (self.entry_id, 1, b"l4:test1:b9:other.txte", None, 100, 150),
            ],
        )

    def test_multi_utf8(self) -> None:
        self.check(
            {
                b"name": b"test",
                b"name.utf-8": b"test.utf-8",
                b"files": [
                    {
                        b"path": [b"a", b"test.txt"],
                        b"path.utf-8": [b"a.utf-8", b"test.txt"],
                        b"length": 100,
                    },
                    {
                        b"path": [b"b", b"other.txt"],
                        b"path.utf-8": [b"b.utf-8", b"other.txt"],
                        b"length": 50,
                    },
                ],
            },
            [
                (
                    self.entry_id,
                    0,
                    b"l10:test.utf-87:a.utf-88:test.txte",
                    "utf-8",
                    0,
                    100,
                ),
                (
                    self.entry_id,
                    1,
                    b"l10:test.utf-87:b.utf-89:other.txte",
                    "utf-8",
                    100,
                    150,
                ),
            ],
        )

    def test_multi_root_nonutf8(self) -> None:
        self.check(
            {
                b"name": b"test",
                b"files": [
                    {
                        b"path": [b"a", b"test.txt"],
                        b"path.utf-8": [b"a.utf-8", b"test.txt"],
                        b"length": 100,
                    },
                    {
                        b"path": [b"b", b"other.txt"],
                        b"path.utf-8": [b"b.utf-8", b"other.txt"],
                        b"length": 50,
                    },
                ],
            },
            [
                (self.entry_id, 0, b"l4:test1:a8:test.txte", None, 0, 100),
                (self.entry_id, 1, b"l4:test1:b9:other.txte", None, 100, 150),
            ],
        )

    def test_multi_mixed_nonutf8(self) -> None:
        self.check(
            {
                b"name": b"test",
                b"name.utf-8": b"test.utf-8",
                b"files": [
                    {b"path": [b"a", b"test.txt"], b"length": 100},
                    {
                        b"path": [b"b", b"other.txt"],
                        b"path.utf-8": [b"b.utf-8", b"other.txt"],
                        b"length": 50,
                    },
                ],
            },
            [
                (self.entry_id, 0, b"l4:test1:a8:test.txte", None, 0, 100),
                (self.entry_id, 1, b"l4:test1:b9:other.txte", None, 100, 150),
            ],
        )


class UpdateSearchResultsTest(BaseMetadataTest):
    def setUp(self) -> None:
        super().setUp()
        self.torrents: Dict[str, api_types.TorrentEntry] = {}
        self.search_result = api_types.GetTorrentsResult(
            results="1000", torrents=self.torrents
        )
        for entry_id in range(100, 110):
            entry = self.entry.copy()
            entry["TorrentID"] = str(entry_id)
            entry["Time"] = str(entry_id * 1000)
            self.torrents[str(entry_id)] = entry

        with self.assert_changes() as change:
            change.entry(new=[int(i) for i in self.torrents])
            change.group(new=(self.group_id,))
            change.series(new=(self.series_id,))
            self.update_search_result(0, self.search_result)

    def test_update(self) -> None:
        # just test setUp()
        pass

    def test_delete(self) -> None:
        self.torrents.pop("105")

        with self.assert_changes() as change:
            change.entry(delete=(105,))
            self.update_search_result(0, self.search_result)

    def test_edge_cases_not_deleted(self) -> None:
        self.torrents.pop("100")
        self.torrents.pop("109")

        with self.assert_changes():
            self.update_search_result(0, self.search_result)

    def test_delete_oldest(self) -> None:
        self.torrents.pop("100")
        self.torrents.pop("109")
        self.search_result["results"] = str(len(self.torrents))

        with self.assert_changes() as change:
            change.entry(delete=(100,))
            self.update_search_result(0, self.search_result)

    def test_out_of_order_time(self) -> None:
        entry = self.torrents.pop("105")
        entry["Time"] = "0"
        with self.assert_changes() as change:
            change.entry(ids=(105,))
            self.update_entry(entry)

        with self.assert_changes():
            self.update_search_result(0, self.search_result)
