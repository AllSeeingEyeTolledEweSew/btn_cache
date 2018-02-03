# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.

"""
A frontend to the BTN metadata API, tailored to maintain a local cache.

BTN has a large set of metadata, but enforces call limits that are so low that
it's difficult to use the API for much. This module is focused on maintaining a
cache of data from BTN in a local SQLite database.
"""

import contextlib
import json as json_lib
import logging
import os
import re
import threading
import time
import urllib
import urlparse

import apsw
import better_bencode
import requests
import tbucket
import yaml


__all__ = [
    "TRACKER_REGEXES",
    "EPISODE_SEED_TIME",
    "EPISODE_SEED_RATIO",
    "SEASON_SEED_TIME",
    "SEASON_SEED_RATIO",
    "TORRENT_HISTORY_FRACTION",
    "add_arguments",
    "API",
    "SearchResult",
    "Error",
    "APIError",
    "TorrentEntry",
    "FileInfo",
    "Group",
    "Series",
]


"""A list of precompiled regexes to match BTN's trackers URLs."""
TRACKER_REGEXES = (
    re.compile(
        r"https?://landof.tv/(?P<passkey>[a-z0-9]{32})/"),
    re.compile(
        r"https?://tracker.broadcasthe.net:34001/"
        r"(?P<passkey>[a-z0-9]{32})/"),
)

"""The minimum time to seed an episode torrent, in seconds."""
EPISODE_SEED_TIME = 24 * 3600
"""The minimum ratio to seed an episode torrent, in seconds."""
EPISODE_SEED_RATIO = 1.0
"""The minimum time to seed a season torrent, in seconds."""
SEASON_SEED_TIME = 120 * 3600
"""The minimum ratio to seed a season torrent, in seconds."""
SEASON_SEED_RATIO = 1.0

"""The minimum fraction of downloaded data for it to count on your history."""
TORRENT_HISTORY_FRACTION = 0.1


def log():
    """Gets a module-level logger."""
    return logging.getLogger(__name__)


class Series(object):
    """A Series entry on BTN.

    Attributes:
        api: The API instance to which this Series is tied.
        id: The integer id of the Series on BTN.
        imdb_id: The string imdb_id of the Series. Typically in 7-digit
            '0123456' format.
        name: The series name.
        banner: A pseudo-URL to the landscape-style banner image hosted on BTN.
            This is sometimes a real URL, but often takes the form
            '//hostname/path/to/image.jpg'.
        poster: A pseudo-URL to the portrait-style poster image hosted on BTN.
            This is sometimes a real URL, but often takes the form
            '//hostname/path/to/image.jpg'.
        tvdb_id: The integer TVDB identifier of the series.
        tvrage_id: The integer TvRage identifier of the series.
        youtube_trailer: A URL to the youtube trailer for the series.
    """

    @classmethod
    def _create_schema(cls, api):
        """Initializes the database schema of an API instance.

        This should only be called at initialization time.

        This will perform a SAVEPOINT / DML / RELEASE command sequence on the
        database.

        Args:
            api: An API instance.
        """
        with api.db:
            c = api.db.cursor()
            c.execute(
                "create table if not exists series ("
                "id integer primary key, "
                "imdb_id text, "
                "name text, "
                "banner text, "
                "poster text, "
                "tvdb_id integer, "
                "tvrage_id integer, "
                "youtube_trailer text, "
                "updated_at integer not null, "
                "deleted tinyint not null default 0)")
            c.execute(
                "create index if not exists series_on_updated_at "
                "on series (updated_at)")
            c.execute(
                "create index if not exists series_on_tvdb_id "
                "on series (tvdb_id)")

    @classmethod
    def _from_db(cls, api, id):
        """Creates a Series from the cached metadata in an API instance.

        This just deserializes data from the API's database into a python
        object; it doesn't make any API calls.

        A new Series object is always created from this call. Series objects
        are not cached.

        This only calls SELECT on the database.

        Args:
            api: An API instance.
            id: A series id.

        Returns:
            A Series with the data cached in the API's database, or None if the
                series wasn't found in the cache.
        """
        c = api.db.cursor()
        row = c.execute(
            "select * from series where id = ?", (id,)).fetchone()
        if not row:
            return None
        row = dict(zip((n for n, t in c.getdescription()), row))
        for k in ("updated_at", "deleted"):
            del row[k]
        return cls(api, **row)

    @classmethod
    def _maybe_delete(cls, api, *ids, changestamp=None):
        """Mark a series as deleted, if it has no non-deleted groups.

        This will perform a SAVEPOINT / DML / RELEASE sequence on the database.

        Args:
            api: An API instance.
            *ids: A list of series ids to consider for deletion.
            changestamp: An integer changestamp. If None, a new changestamp
                will be generated.
        """
        if not ids:
            return
        with api.db:
            if changestamp is None:
                changestamp = api.get_changestamp()
            api.db.cursor().executemany(
                "update series set deleted = 1, updated_at = ? "
                "where id = ? and not deleted and not exists ("
                "select id from torrent_entry_group "
                "where series_id = series.id and not deleted)",
                [(changestamp, id) for id in ids])

    def __init__(self, api, id=None, imdb_id=None, name=None, banner=None,
                 poster=None, tvdb_id=None, tvrage_id=None, youtube_trailer=None):
        self.api = api
        self.id = int(id)
        self.imdb_id = imdb_id
        self.name = name
        self.banner = banner
        self.poster = poster
        self.tvdb_id = tvdb_id
        self.tvrage_id = tvrage_id
        self.youtube_trailer = youtube_trailer

    def serialize(self, changestamp=None):
        """Serialize the Series' data to its API's database.

        This will write all the fields of this Series to the "series" table of
        the database of the `api`. If any fields have changed, the `updated_at`
        column of the "series" table will be updated. If no data changes, the
        corresponding row in "series" won't change at all.

        This performs a SAVEPOINT / DML / RELEASE sequence against the API.

        Args:
            changestamp: A changestamp from the API. If None, a new changestamp
                will be generated from the API.
        """
        with self.api.db:
            if changestamp is None:
                changestamp = self.api.get_changestamp()
            params = {
                "imdb_id": self.imdb_id,
                "name": self.name,
                "banner": self.banner,
                "poster": self.poster,
                "tvdb_id": self.tvdb_id,
                "tvrage_id": self.tvrage_id,
                "youtube_trailer": self.youtube_trailer,
                "deleted": 0,
            }
            insert_params = {"updated_at": changestamp, "id": self.id}
            insert_params.update(params)
            names = sorted(insert_params.keys())
            self.api.db.cursor().execute(
                "insert or ignore into series (%(n)s) values (%(v)s)" %
                {"n": ",".join(names), "v": ",".join(":" + n for n in names)},
                insert_params)
            where_names = sorted(params.keys())
            set_names = sorted(params.keys())
            set_names.append("updated_at")
            update_params = dict(params)
            update_params["updated_at"] = changestamp
            update_params["id"] = self.id
            self.api.db.cursor().execute(
                "update series set %(u)s where id = :id and (%(w)s)" %
                {"u": ",".join("%(n)s = :%(n)s" % {"n": n} for n in set_names),
                 "w": " or ".join("%(n)s is not :%(n)s" % {"n": n}
                     for n in where_names)},
                update_params)

    def __repr__(self):
        return "<Series %s \"%s\">" % (self.id, self.name)


class Group(object):
    """A Group entry on BTN.

    A Group is a set of torrents which all represent the same media (a
    particular episode, movie, or else a particular set of media such as a
    season pack).

    Note that the "year" attribute which appears on the BTN site is absent
    here, as it is not currently returned from the API.

    Attributes:
        api: The API instance to which this Series is tied.
        id: The integer id of the Group on BTN.
        category: Either "Episode" or "Season".
        name: The name of the Group. Not necessarily unique within its Series.
            The name is semi-meaningful. Group names matching some patterns
            like 'S01E01', '2012.12.21' or 'Season 3' are recognized and
            visually grouped together on the BTN site.
        series: The Series object this Group belongs to.
    """

    @classmethod
    def _create_schema(cls, api):
        """Initializes the database schema of an API instance.

        This should only be called at initialization time.

        This will perform a SAVEPOINT / DML / RELEASE command sequence on the
        database.

        Args:
            api: An API instance.
        """
        with api.db:
            c = api.db.cursor()
            c.execute(
                "create table if not exists torrent_entry_group ("
                "id integer primary key,"
                "category_id integer not null,"
                "name text not null,"
                "series_id integer not null,"
                "updated_at integer not null, "
                "deleted tinyint not null default 0)")
            c.execute(
                "create index if not exists torrent_entry_group_on_updated_at "
                "on torrent_entry_group (updated_at)")
            c.execute(
                "create index if not exists torrent_entry_group_on_series_id "
                "on torrent_entry_group (series_id)")
            c.execute(
                "create table if not exists category ("
                "id integer primary key, "
                "name text not null)")
            c.execute(
                "create unique index if not exists category_name "
                "on category (name)")

    @classmethod
    def _from_db(cls, api, id):
        """Creates a Group from the cached metadata in an API instance.

        This just deserializes data from the API's database into a python
        object; it doesn't make any API calls.

        A new Group object, and attached Series object, are always created from
        this function. Group and Series objects are not cached.

        This calls SAVEPOINT / SELECT / RELEASE on the database.

        Args:
            api: An API instance.
            id: A group id.

        Returns:
            A Group with the data cached in the API's database, or None if the
                group wasn't found in the cache.
        """
        with api.db:
            c = api.db.cursor()
            row = c.execute(
                "select "
                "torrent_entry_group.id as id, "
                "category.name as category, "
                "torrent_entry_group.name as name, "
                "series_id "
                "from torrent_entry_group "
                "left outer join category "
                "on torrent_entry_group.category_id = category.id "
                "where torrent_entry_group.id = ?",
                (id,)).fetchone()
            if not row:
                return None
            row = dict(zip((n for n, t in c.getdescription()), row))
            series = Series._from_db(api, row.pop("series_id"))
            return cls(api, series=series, **row)

    @classmethod
    def _maybe_delete(cls, api, *ids, changestamp=None):
        """Mark a group as deleted, if it has no non-deleted torrent entries.

        This will also call `Series._maybe_delete` on the series ids associated
        with the given group ids.

        This will perform a SAVEPOINT / DML / RELEASE sequence on the database.

        Args:
            api: An API instance.
            *ids: A list of group ids to consider for deletion.
            changestamp: An integer changestamp. If None, a new changestamp
                will be generated.
        """
        if not ids:
            return
        with api.db:
            if len(ids) > 900:
                api.db.cursor().execute(
                    "create temporary table delete_group_ids "
                    "(id integer not null primary key)")
                api.db.cursor().executemany(
                    "insert into temp.delete_group_ids (id) values (?)",
                    [(id,) for id in ids])
                rows = api.db.cursor().execute(
                    "select torrent_entry_group.id, "
                    "torrent_entry_group.series_id from torrent_entry_group "
                    "inner join temp.delete_group_ids "
                    "where temp.delete_group_ids.id = torrent_entry_group.id "
                    "and not deleted and not exists ("
                    "select id from torrent_entry "
                    "where group_id = torrent_entry_group.id "
                    "and not deleted)").fetchall()
                api.db.cursor().execute("drop table temp.delete_group_ids")
            else:
                rows = api.db.cursor().execute(
                    "select id, series_id from torrent_entry_group "
                    "where id in (%s) and not deleted and not exists ("
                    "select id from torrent_entry "
                    "where group_id = torrent_entry_group.id and not deleted)" %
                    ",".join(["?"] * len(ids)),
                    tuple(ids))
            series_ids_to_check = set()
            group_ids_to_delete = set()
            for group_id, series_id in rows:
                group_ids_to_delete.add(group_id)
                series_ids_to_check.add(series_id)
            if not group_ids_to_delete:
                return
            if changestamp is None:
                changestamp = api.get_changestamp()
            api.db.cursor().executemany(
                "update torrent_entry_group set deleted = 1, updated_at = ? "
                "where id = ?",
                [(changestamp, id) for id in group_ids_to_delete])
            Series._maybe_delete(
                api, *list(series_ids_to_check), changestamp=changestamp)

    def __init__(self, api, id=None, category=None, name=None, series=None):
        self.api = api

        self.id = id
        self.category = category
        self.name = name
        self.series = series

    def serialize(self, changestamp=None):
        """Serialize the Group's data to its API's database.

        This will write all the fields of this Series to the
        "torrent_entry_group" table of the database of the `api`. If any fields
        have changed, the `updated_at` column of the "torrent_entry_group"
        table will be updated. If no data changes, the corresponding row in
        "torrent_entry_group" won't change at all.

        This also calls `serialize()` on the associated `series`.

        This performs a SAVEPOINT / DML / RELEASE sequence against the API.

        Args:
            changestamp: A changestamp from the API. If None, a new changestamp
                will be generated from the API.
        """
        with self.api.db:
            if changestamp is None:
                changestamp = self.api.get_changestamp()
            self.api.db.cursor().execute(
                "insert or ignore into category (name) values (?)",
                (self.category,))
            category_id = self.api.db.cursor().execute(
                "select id from category where name = ?",
                (self.category,)).fetchone()[0]
            r = self.api.db.cursor().execute(
                "select series_id from torrent_entry_group where id = ?",
                (self.id,)).fetchone()
            old_series_id = r[0] if r is not None else None
            self.series.serialize(changestamp=changestamp)
            params = {
                "category_id": category_id,
                "name": self.name,
                "series_id": self.series.id,
                "deleted": 0,
            }
            insert_params = {"updated_at": changestamp, "id": self.id}
            insert_params.update(params)
            names = sorted(insert_params.keys())
            self.api.db.cursor().execute(
                "insert or ignore into torrent_entry_group (%(n)s) "
                "values (%(v)s)" %
                {"n": ",".join(names), "v": ",".join(":" + n for n in names)},
                insert_params)
            where_names = sorted(params.keys())
            set_names = sorted(params.keys())
            set_names.append("updated_at")
            update_params = dict(params)
            update_params["updated_at"] = changestamp
            update_params["id"] = self.id
            self.api.db.cursor().execute(
                "update torrent_entry_group set %(u)s "
                "where id = :id and (%(w)s)" %
                {"u": ",".join("%(n)s = :%(n)s" % {"n": n} for n in set_names),
                 "w": " or ".join("%(n)s is not :%(n)s" % {"n": n}
                     for n in where_names)},
                update_params)
            if old_series_id != self.series.id:
                Series._maybe_delete(
                    self.api, old_series_id, changestamp=changestamp)

    def __repr__(self):
        return "<Group %s \"%s\" \"%s\">" % (
            self.id, self.series.name, self.name)


class FileInfo(object):
    """Metadata about a particular file in a torrent on BTN.

    Attributes:
        index: The integer index of this file within the torrent metafile.
        length: The integer length of the file in bytes.
        path: The recommended pathname as a string, as it appears in the
            torrent metafile.
    """

    @classmethod
    def _from_db(cls, api, id):
        """Creates FileInfo objects from the cached metadata in an API
        instance.

        This just deserializes data from the API's database into a python
        object; it doesn't make any API calls.

        This function always creates new FileInfo objects. The objects are not
        cached.

        This calls SAVEPOINT / SELECT / RELEASE on the database.

        Args:
            api: An API instance.
            id: A torrent entry id.

        Returns:
            A tuple of FileInfo objects with the data cached in the API's
                database associated with a given torrent entry id, or an empty
                tuple if none were found.
        """
        with api.db:
            c = api.db.cursor()
            rows = c.execute(
                "select "
                "file_index as 'index', "
                "path, "
                "length "
                "from file_info "
                "where id = ?",
                (id,))
            return (cls(index=r[0], path=r[1], length=r[2]) for r in rows)

    @classmethod
    def _from_tobj(cls, tobj):
        """Generates FileInfo objects from a deserialized torrent metafile.

        Args:
            tobj: A deserialized torrent metafile; i.e. the result of
                `better_bencode.load*()`.

        Yields:
            FileInfo objects for the given torrent metafile, in the order they
                appear in the metafile.
        """
        ti = tobj[b"info"]
        values = []
        if b"files" in ti:
            for idx, fi in enumerate(ti[b"files"]):
                length = fi[b"length"]
                path_parts = [ti[b"name"]]
                path_parts.extend(list(fi[b"path"]))
                path = b"/".join(path_parts)
                yield cls(index=idx, path=path, length=length)
        else:
            yield cls(index=0, path=ti[b"name"], length=ti[b"length"])

    def __init__(self, index=None, path=None, length=None):
        self.index = index
        self.path = path
        self.length = length

    def __repr__(self):
        return "<FileInfo %s \"%s\">" % (self.index, self.path)


class TorrentEntry(object):
    """Metadata about a torrent on BTN.

    A TorrentEntry is named to distinguish it from a "torrent". A TorrentEntry
    is the tracker's metadata about a torrent, including at least its info
    hash; the word "torrent" may refer to either the metafile or the actual
    data files.

    Attributes:
        api: The API instance to which this TorrentEntry is tied.
        id: The integer id of the TorrentEntry on BTN.
        codec: The name of the torrent's codec. Common values include "H.264",
            "XviD" and "MPEG2".
        container: The name of the torrent's container. Common values include
            "MKV", "MP4" and "AVI".
        group: The associated Group to which this TorrentEntry belongs.
        info_hash: The "info hash" (the sha1 hash of the bencoded version of
            the "info" section of the metafile). This appears on BTN in
            capitalized hexadecimal, such as
            "642BC82E51FD2BF6F977B8B3D7D571DA3A06B36B".
        leechers: The current number of clients leeching the torrent.
        origin: The origin of the torrent. Common values include "Scene", "P2P"
            and "Internal".
        release_name: The release name of the torrent as a string. This
            commonly obeys a particular format laid out by the scene.
        resolution: The resolution of the torrent, chosen from among one of
            several string values used on BTN. Common values include "1080p",
            "720p" and "SD".
        seeders: The current number of client seeding the torrent.
        size: The total size of the torrent in bytes.
        snatched: The number of times the torrent has been "snatched" (fully
            downloaded).
        source: The source of the torrent. Common values include "WEB-DL",
            "HDTV" and "Bluray".
        time: The UNIX time (seconds since epoch) that this torrent was
            uploaded to the tracker.
    """

    #CATEGORY_EPISODE = "Episode"
    #CATEGORY_SEASON = "Season"

    #GROUP_EPISODE_REGEX = re.compile(
    #    r"S(?P<season>\d+)(?P<episodes>(E\d+)+)$")
    #PARTIAL_EPISODE_REGEX = re.compile(r"E(?P<episode>\d\d)")
    #GROUP_EPISODE_DATE_REGEX = re.compile(
    #    r"(?P<year>\d\d\d\d)\.(?P<month>\d\d)\.(?P<day>\d\d)")
    #GROUP_EPISODE_SPECIAL_REGEX = re.compile(
    #    r"Season (?P<season>\d+) - (?P<name>.*)")

    #GROUP_FULL_SEASON_REGEX = re.compile(r"Season (?P<season>\d+)$")

    @classmethod
    def _create_schema(cls, api):
        """Initializes the database schema of an API instance.

        This should only be called at initialization time.

        This will perform a SAVEPOINT / DML / RELEASE command sequence on the
        database.

        Args:
            api: An API instance.
        """
        with api.db:
            c = api.db.cursor()
            c.execute(
                "create table if not exists torrent_entry ("
                "id integer primary key, "
                "codec_id integer not null, "
                "container_id integer not null, "
                "group_id integer not null, "
                "info_hash text, "
                "origin_id integer not null, "
                "release_name text not null, "
                "resolution_id integer not null, "
                "size integer not null, "
                "source_id integer not null, "
                "time integer not null, "
                "raw_torrent_cached tinyint not null default 0, "
                "updated_at integer not null, "
                "deleted tinyint not null default 0)")
            c.execute(
                "create index if not exists torrent_entry_updated_at "
                "on torrent_entry (updated_at)")
            c.execute(
                "create index if not exists torrent_entry_on_group_id "
                "on torrent_entry (group_id)")
            c.execute(
                "create index if not exists torrent_entry_on_info_hash "
                "on torrent_entry (info_hash)")
            c.execute(
                "create table if not exists codec ("
                "id integer primary key, "
                "name text not null)")
            c.execute(
                "create unique index if not exists codec_name "
                "on codec (name)")
            c.execute(
                "create table if not exists container ("
                "id integer primary key, "
                "name text not null)")
            c.execute(
                "create unique index if not exists container_name "
                "on container (name)")
            c.execute(
                "create table if not exists origin ("
                "id integer primary key, "
                "name text not null)")
            c.execute(
                "create unique index if not exists origin_name "
                "on origin (name)")
            c.execute(
                "create table if not exists resolution ("
                "id integer primary key, "
                "name text not null)")
            c.execute(
                "create unique index if not exists resolution_name "
                "on resolution (name)")
            c.execute(
                "create table if not exists source ("
                "id integer primary key, "
                "name text not null)")
            c.execute(
                "create unique index if not exists source_name "
                "on source (name)")

            c.execute(
                "create table if not exists file_info ("
                "id integer not null, "
                "file_index integer not null, "
                "path text not null, "
                "length integer not null, "
                "updated_at integer not null)")
            c.execute(
                "create unique index if not exists file_info_id_index "
                "on file_info (id, file_index)")
            c.execute(
                "create index if not exists file_info_id_index_path "
                "on file_info (id, file_index, path)")
            c.execute(
                "create index if not exists file_info_updated_at "
                "on file_info (updated_at)")
            c.execute(
                "create table if not exists tracker_stats ("
                "id integer primary key,"
                "snatched integer not null, "
                "seeders integer not null, "
                "leechers integer not null)")

    @classmethod
    def _from_db(cls, api, id):
        """Creates a TorrentEntry from the cached metadata in an API instance.

        This just deserializes data from the API's database into a python
        object; it doesn't make any API calls.

        A new TorrentEntry object, and attached FileInfo, Group and Series
        objects, are always created from this function. None of these objects
        are cached for reuse.

        This calls SAVEPOINT / SELECT / RELEASE on the database.

        Args:
            api: An API instance.
            id: A TorrentEntry id.

        Returns:
            A TorrentEntry with the data cached in the API's database, or None
                if the group wasn't found in the cache.
        """
        with api.db:
            c = api.db.cursor()
            row = c.execute(
                "select "
                "torrent_entry.id as id, "
                "codec.name as codec, "
                "container.name as container, "
                "torrent_entry.group_id as group_id, "
                "info_hash, "
                "tracker_stats.leechers as leechers, "
                "origin.name as origin, "
                "release_name, "
                "resolution.name as resolution, "
                "tracker_stats.seeders as seeders, "
                "size, "
                "tracker_stats.snatched as snatched, "
                "source.name as source, "
                "time "
                "from torrent_entry "
                "left outer join codec on codec.id = codec_id "
                "left outer join container on container.id = container_id "
                "left outer join origin on origin.id = origin_id "
                "left outer join resolution on resolution.id = resolution_id "
                "left outer join source on source.id = source_id "
                "left outer join tracker_stats "
                "on tracker_stats.id = torrent_entry.id "
                "where torrent_entry.id = ?",
                (id,)).fetchone()
            if not row:
                return None
            row = dict(zip((n for n, t in c.getdescription()), row))
            group = Group._from_db(api, row.pop("group_id"))
            return cls(api, group=group, **row)

    def __init__(self, api, id=None, codec=None, container=None, group=None,
                 info_hash=None, leechers=None, origin=None, release_name=None,
                 resolution=None, seeders=None, size=None, snatched=None,
                 source=None, time=None):
        self.api = api

        self.id = id
        self.codec = codec
        self.container = container
        self.group = group
        self.info_hash = info_hash
        self.leechers = leechers
        self.origin = origin
        self.release_name = release_name
        self.resolution = resolution
        self.seeders = seeders
        self.size = size
        self.snatched = snatched
        self.source = source
        self.time = time

        self._lock = threading.RLock()
        self._raw_torrent = None

    def serialize(self, changestamp=None):
        """Serialize the TorrentEntry's data to its API's database.

        This will write all the fields of this TorrentEntry to the
        "torrent_entry" table of the database of the `api`. If any fields have
        changed, the `updated_at` column of the "torrent_entry" table will be
        updated. If no data changes, the corresponding row in "torrent_entry"
        won't change at all.

        The "tracker_stats" table is always updated.

        The "enum value" tables "codec", "container", "origin", "resolution"
        and "source" will also be updated with new values if necessary.

        If the raw torrent has been cached, this function will also update the
        "file_info" table if necessary.

        This also calls `serialize()` on the associated `group` and its
        `series`.

        This performs a SAVEPOINT / DML / RELEASE sequence against the API.

        Args:
            changestamp: A changestamp from the API. If None, a new changestamp
                will be generated from the API.
        """
        file_info = None
        if self.raw_torrent_cached and not any(self.file_info_cached):
            file_info = list(FileInfo._from_tobj(self.torrent_object))
        with self.api.db:
            self.api.db.cursor().execute(
                "insert or ignore into codec (name) values (?)", (self.codec,))
            codec_id = self.api.db.cursor().execute(
                "select id from codec where name = ?",
                (self.codec,)).fetchone()[0]

            self.api.db.cursor().execute(
                "insert or ignore into container (name) values (?)",
                (self.container,))
            container_id = self.api.db.cursor().execute(
                "select id from container where name = ?",
                (self.container,)).fetchone()[0]

            self.api.db.cursor().execute(
                "insert or ignore into origin (name) values (?)",
                (self.origin,))
            origin_id = self.api.db.cursor().execute(
                "select id from origin where name = ?",
                (self.origin,)).fetchone()[0]

            self.api.db.cursor().execute(
                "insert or ignore into resolution (name) values (?)",
                (self.resolution,))
            resolution_id = self.api.db.cursor().execute(
                "select id from resolution where name = ?",
                (self.resolution,)).fetchone()[0]

            self.api.db.cursor().execute(
                "insert or ignore into source (name) values (?)",
                (self.source,))
            source_id = self.api.db.cursor().execute(
                "select id from source where name = ?",
                (self.source,)).fetchone()[0]
            r = self.api.db.cursor().execute(
                "select group_id from torrent_entry where id = ?",
                (self.id,)).fetchone()
            old_group_id = r[0] if r is not None else None

            if changestamp is None:
                changestamp = self.api.get_changestamp()
            self.group.serialize(changestamp=changestamp)
            params = {
                "codec_id": codec_id,
                "container_id": container_id,
                "group_id": self.group.id,
                "info_hash": self.info_hash,
                "origin_id": origin_id,
                "release_name": self.release_name,
                "resolution_id": resolution_id,
                "size": self.size,
                "source_id": source_id,
                "time": self.time,
                "raw_torrent_cached": self.raw_torrent_cached,
                "deleted": 0,
            }
            insert_params = {"id": self.id, "updated_at": changestamp}
            insert_params.update(params)
            names = sorted(insert_params.keys())
            self.api.db.cursor().execute(
                "insert or ignore into torrent_entry (%(n)s) values (%(v)s)" %
                {"n": ",".join(names), "v": ",".join(":" + n for n in names)},
                insert_params)
            where_names = sorted(params.keys())
            set_names = sorted(params.keys())
            set_names.append("updated_at")
            update_params = dict(params)
            update_params["updated_at"] = changestamp
            update_params["id"] = self.id
            self.api.db.cursor().execute(
                "update torrent_entry set %(u)s "
                "where id = :id and (%(w)s)" %
                {"u": ",".join("%(n)s = :%(n)s" % {"n": n} for n in set_names),
                 "w": " or ".join("%(n)s is not :%(n)s" % {"n": n}
                     for n in where_names)},
                update_params)
            self.api.db.cursor().execute(
                "insert or replace into tracker_stats "
                "(id, snatched, seeders, leechers) values (?, ?, ?, ?)",
                (self.id, self.snatched, self.seeders, self.leechers))
            if old_group_id != self.group.id:
                Group._maybe_delete(
                    self.api, old_group_id, changestamp=changestamp)

            if file_info:
                values = [
                    (self.id, fi.index, fi.path, fi.length, changestamp)
                    for fi in file_info]
                self.api.db.cursor().executemany(
                    "insert or ignore into file_info "
                    "(id, file_index, path, length, updated_at) values "
                    "(?, ?, ?, ?, ?)", values)

    @property
    def link(self):
        """A link to the torrent metafile."""
        return self.api.mk_url(
            self.api.HOST, "/torrents.php", action="download",
            authkey=self.api.authkey, torrent_pass=self.api.passkey,
            id=self.id)

    def magnet_link(self, include_as=True):
        """Gets a magnet link for this torrent.

        Args:
            include_as: If True, include an "&as=..." parameter with a direct
                link to the torrent file. Defaults to True.

        Returns:
            A "magnet:?..." link string.
        """
        qsl = [
            ("dn", self.release_name),
            ("xt", "urn:btih:" + self.info_hash),
            ("xl", self.size)]
        for url in self.api.announce_urls:
            qsl.append(("tr", url))
        if include_as:
            qsl.append(("as", urllib.quote(self.link)))

        return "magnet:?%s" % "&".join("%s=%s" % (k, v) for k, v in qsl)

    @property
    def raw_torrent_path(self):
        """The path to the cached torrent metafile."""
        return os.path.join(
            self.api.raw_torrent_cache_path, "%s.torrent" % self.id)

    @property
    def raw_torrent_cached(self):
        """Whether or not the torrent metafile has been locally cached."""
        return os.path.exists(self.raw_torrent_path)

    def _got_raw_torrent(self, raw_torrent):
        """Callback that should be called when we receive the torrent metafile.

        This will write the torrent metafile to `raw_torrent_path`.

        Will call `serialize()`.

        Args:
            raw_torrent: The bencoded torrent metafile.
        """
        self._raw_torrent = raw_torrent
        if self.api.store_raw_torrent:
            if not os.path.exists(os.path.dirname(self.raw_torrent_path)):
                os.makedirs(os.path.dirname(self.raw_torrent_path))
            with open(self.raw_torrent_path, mode="wb") as f:
                f.write(self._raw_torrent)
        while True:
            try:
                with self.api.begin():
                    self.serialize()
            except apsw.BusyError:
                log().warning(
                    "BusyError while trying to serialize, will retry")
            else:
                break
        return self._raw_torrent

    @property
    def raw_torrent(self):
        """The torrent metafile.

        If the torrent metafile isn't locally cached, it will be fetched from
        BTN and cached.

        Raises:
            APIError: When fetching the torrent results in an HTTP error.
        """
        with self._lock:
            if self._raw_torrent is not None:
                return self._raw_torrent
            if self.raw_torrent_cached:
                with open(self.raw_torrent_path, mode="rb") as f:
                    self._raw_torrent = f.read()
                return self._raw_torrent
            log().debug("Fetching raw torrent for %s", repr(self))
            response = self.api.get_url(self.link)
            if response.status_code != requests.codes.ok:
                raise APIError(response.text, response.status_code)
            self._got_raw_torrent(response.content)
            return self._raw_torrent

    @property
    def file_info_cached(self):
        """A tuple of cached FileInfo objects from the database."""
        return FileInfo._from_db(self.api, self.id)

    @property
    def torrent_object(self):
        """The torrent metafile, deserialized via `better_bencode.load*()`."""
        return better_bencode.loads(self.raw_torrent)

    def __repr__(self):
        return "<TorrentEntry %d \"%s\">" % (self.id, self.release_name)


class UserInfo(object):
    """Information about a user on BTN.

    Attributes:
        api: The API instance to which this TorrentEntry is tied.
        id: The integer id of the user on BTN.
        bonus: The user's integer number of bonus points.
        class_name: The name of the user's class.
        class_level: The integer level of the user's class.
        download: The user's all-time download in bytes.
        email: The user's email address.
        enabled: Whether or not the user is enabled.
        hnr: The user's HnR count.
        invites: The number of invites the user has sent.
        join_date: The UNIX timestamp (seconds since epoch) when the user
            joined BTN.
        lumens: The user's lumen count.
        paranoia: The user's paranoia level.
        snatches: The user's total number of snatches.
        title: The user's custom title.
        upload: The user's all-time upload in bytes.
        uploads_snatched: The number of times any torrent uploaded by the user
            has been snatched.
        username: The user's name on the site.
    """

    @classmethod
    def _create_schema(cls, api):
        """Initializes the database schema of an API instance.

        This should only be called at initialization time.

        This will perform a SAVEPOINT / DML / RELEASE command sequence on the
        database.

        Args:
            api: An API instance.
        """
        with api.db:
            api.db.cursor().execute(
                "create table if not exists user.user_info ("
                "id integer primary key, "
                "bonus integer not null, "
                "class_name text not null, "
                "class_level integer not null, "
                "download integer not null, "
                "email text not null, "
                "enabled integer not null, "
                "hnr integer not null, "
                "invites integer not null, "
                "join_date integer not null, "
                "lumens integer not null, "
                "paranoia integer not null, "
                "snatches integer not null, "
                "title text not null, "
                "upload integer not null, "
                "uploads_snatched integer not null, "
                "username text not null)")

    @classmethod
    def _from_db(cls, api):
        """Creates a UserInfo from the cached metadata in an API instance.

        This just deserializes data from the API's database into a python
        object; it doesn't make any API calls.

        A new UserInfo object is always created from this function. UserInfo
        objects aren't cached for reuse.

        This calls SAVEPOINT / SELECT / RELEASE on the database.

        Args:
            api: An API instance.

        Returns:
            A UserInfo representing the first (assumed only) user in the
                database.
        """
        c = api.db.cursor()
        row = c.execute(
            "select * from user.user_info limit 1").fetchone()
        if not row:
            return None
        row = dict(zip((n for n, t in c.getdescription()), row))
        return cls(api, **row)

    def __init__(self, api, id=None, bonus=None, class_name=None,
                 class_level=None, download=None, email=None, enabled=None,
                 hnr=None, invites=None, join_date=None, lumens=None,
                 paranoia=None, snatches=None, title=None, upload=None,
                 uploads_snatched=None, username=None):
        self.api = api

        self.id = id
        self.bonus = bonus
        self.class_name = class_name
        self.class_level = class_level
        self.download = download
        self.email = email
        self.enabled = enabled
        self.hnr = hnr
        self.invites = invites
        self.join_date = join_date
        self.lumens = lumens
        self.paranoia = paranoia
        self.snatches = snatches
        self.title = title
        self.upload = upload
        self.uploads_snatched = uploads_snatched
        self.username = username

    def serialize(self):
        """Serialize the UserInfo's data to its API's database.

        This will truncate the "user_info" table in the user database, and
        will serialize this UserInfo to be the only row.

        This performs a SAVEPOINT / DML / RELEASE sequence against the API.
        """
        with self.api.db:
            c = self.api.db.cursor()
            c.execute("delete from user.user_info")
            c.execute(
                "insert or replace into user.user_info ("
                "id, bonus, class_name, class_level, download, "
                "email, enabled, hnr, invites, join_date, "
                "lumens, paranoia, snatches, title, upload, "
                "uploads_snatched, username) "
                "values ("
                "?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, "
                "?, ?)",
                (self.id, self.bonus, self.class_name, self.class_level,
                 self.download, self.email, self.enabled, self.hnr,
                 self.invites, self.join_date, self.lumens, self.paranoia,
                 self.snatches, self.title, self.upload, self.uploads_snatched,
                 self.username))

    def __repr__(self):
        return "<UserInfo %s \"%s\">" % (self.id, self.username)


class SearchResult(object):
    """The result of a call to `API.getTorrents`.

    Attributes:
        results: The total integer number of torrents matched by the filters in
            the getTorrents call.
        torrents: A list of TorrentEntry objects.
    """

    def __init__(self, results, torrents):
        self.results = int(results)
        self.torrents = torrents or ()


class CrudResult(object):

    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"

    TYPE_TORRENT_ENTRY = "torrent_entry"
    TYPE_GROUP = "group"
    TYPE_SERIES = "series"

    def __init__(self, type, action, id):
        self.type = type
        self.action = action
        self.id = id

    def __repr__(self):
        return "<CrudResult %s %s %s>" % (self.action, self.type, self.id)


class Error(Exception):
    """Top-level class for exceptions in this module."""

    pass


class APIError(Error):
    """An error returned from the API.

    Attributes:
        code: The numeric error code returned by the API.
    """

    """The user has exceeded their allowed call limit."""
    CODE_CALL_LIMIT_EXCEEDED = -32002

    def __init__(self, message, code):
        super(APIError, self).__init__(message)
        self.code = code


class WouldBlock(Error):

    pass


def add_arguments(parser, create_group=True):
    """A helper function to add standard command-line options to make an API
    instance.

    This adds the following command-line options:
        --btn_cache_path: The path to the BTN cache.

    Args:
        parser: An argparse.ArgumentParser.
        create_group: Whether or not to create a subgroup for BTN API options.
            Defaults to True.

    Returns:
        Either the argparse.ArgumentParser or the subgroup that was implicitly
            created.
    """
    if create_group:
        target = parser.add_argument_group("BTN API options")
    else:
        target = parser

    target.add_argument("--btn_cache_path", type=str)

    return target


class API(object):
    """An API to BTN, and associated data cache.

    Attributes:
        cache_path: The path to the data cache directory.
        key: The user's API key.
        auth: The user's "auth" string.
        passkey: The user's BTN passkey.
        authkey: The user's "authkey" string.
        token_rate: The `rate` parameter to `token_bucket`.
        token_period: The `period` parameter to `token_bucket`.
        api_token_rate: The `rate` parameter to `api_token_bucket`.
        api_token_period: The `period` parameter to `api_token_bucket`.
        store_raw_torrent: Whether or not to cache torrent metafiles to disk,
            whenever they are fetched.
        token_bucket: An instance of tbucket.TokenBucket which controls access
            to most HTTP requests to BTN, such as when downloading torrent
            metafiles.
        api_token_bucket: An instance of tbucket.TimeSeriesTokenBucket which
            controls access to the API.
    """

    """The protocol scheme used to access the API."""
    SCHEME = "https"

    """The hostname of the BTN website."""
    HOST = "broadcasthe.net"

    """The hostname of the BTN API."""
    API_HOST = "api.broadcasthe.net"
    """The HTTP path to the BTN API."""
    API_PATH = "/"

    DEFAULT_TOKEN_RATE = 20
    DEFAULT_TOKEN_PERIOD = 100

    DEFAULT_API_TOKEN_RATE = 150
    DEFAULT_API_TOKEN_PERIOD = 3600

    @classmethod
    def from_args(cls, parser, args):
        """Helper function to create an API from command-line arguments.

        This is intended to be used with a parser which has been configured
        with `add_arguments()`.

        Args:
            parser: An argparse.ArgumentParser.
            args: An argparse.Namespace resulting from calling `parse_args()`
                on the given parser.
        """
        return cls(cache_path=args.btn_cache_path)

    def __init__(self, key=None, passkey=None, authkey=None,
                 api_token_bucket=None, token_bucket=None, cache_path=None,
                 store_raw_torrent=None, auth=None):
        if cache_path is None:
            cache_path = os.path.expanduser("~/.btn")

        self.cache_path = cache_path

        if os.path.exists(self.config_path):
            with open(self.config_path) as f:
                config = yaml.load(f)
        else:
                config = {}

        self.key = config.get("key")
        self.auth = config.get("auth")
        self.passkey = config.get("passkey")
        self.authkey = config.get("authkey")
        self.token_rate = config.get("token_rate")
        self.token_period = config.get("token_period")
        self.api_token_rate = config.get("api_token_rate")
        self.api_token_period = config.get("api_token_period")
        self.store_raw_torrent = config.get("store_raw_torrent")

        if key is not None:
            self.key = key
        if auth is not None:
            self.auth = auth
        if passkey is not None:
            self.passkey = passkey
        if authkey is not None:
            self.authkey = authkey
        if store_raw_torrent is not None:
            self.store_raw_torrent = store_raw_torrent

        if self.token_rate is None:
            self.token_rate = self.DEFAULT_TOKEN_RATE
        if self.token_period is None:
            self.token_period = self.DEFAULT_TOKEN_PERIOD
        if self.api_token_rate is None:
            self.api_token_rate = self.DEFAULT_API_TOKEN_RATE
        if self.api_token_period is None:
            self.api_token_period = self.DEFAULT_API_TOKEN_PERIOD

        if token_bucket is not None:
            self.token_bucket = token_bucket
        else:
            self.token_bucket = tbucket.TokenBucket(
                self.user_db_path, "web:%s" % self.key, self.token_rate,
                self.token_period)
        if api_token_bucket is not None:
            self.api_token_bucket = api_token_bucket
        else:
            self.api_token_bucket = tbucket.TimeSeriesTokenBucket(
                self.user_db_path, self.key, self.api_token_rate,
                self.api_token_period)

        self._local = threading.local()
        self._db = None

    @property
    def metadata_db_path(self):
        """The path to metadata.db."""
        if self.cache_path:
            return os.path.join(self.cache_path, "metadata.db")
        return None

    @property
    def user_db_path(self):
        """The path to user.db."""
        if self.cache_path:
            return os.path.join(self.cache_path, "user.db")
        return None

    @property
    def config_path(self):
        """The path to config.yaml."""
        if self.cache_path:
            return os.path.join(self.cache_path, "config.yaml")
        return None

    @property
    def raw_torrent_cache_path(self):
        """The path to the directory of cached torrent metafiles."""
        if self.cache_path:
            return os.path.join(self.cache_path, "torrents")

    @property
    def db(self):
        """A thread-local apsw.Connection.

        The primary database will be the metadata database. The user database
        will be attached under the schema name "user".
        """
        db = getattr(self._local, "db", None)
        if db is not None:
            return db
        if self.metadata_db_path is None:
            return None
        if not os.path.exists(os.path.dirname(self.metadata_db_path)):
            os.makedirs(os.path.dirname(self.metadata_db_path))
        db = apsw.Connection(self.metadata_db_path)
        db.setbusytimeout(120000)
        self._local.db = db
        c = db.cursor()
        c.execute(
            "attach database ? as user", (self.user_db_path,))
        with db:
            Series._create_schema(self)
            Group._create_schema(self)
            TorrentEntry._create_schema(self)
            UserInfo._create_schema(self)
            c.execute(
                "create table if not exists user.global ("
                "  name text not null,"
                "  value text not null)")
            c.execute(
                "create unique index if not exists user.global_name "
                "on global (name)")
        c.execute("pragma journal_mode=wal").fetchall()
        return db

    @contextlib.contextmanager
    def begin(self, mode="immediate"):
        """Gets a context manager for a BEGIN IMMEDIATE transaction.

        Args:
            mode: The transaction mode. This will be directly used in the
                command to begin the transaction: "BEGIN <mode>". Defaults to
                "IMMEDIATE".

        Returns:
            A context manager for the transaction. If the context succeeds, the
                context manager will issue COMMIT. If it fails, the manager
                will issue ROLLBACK.
        """
        self.db.cursor().execute("begin %s" % mode)
        try:
            yield
        except:
            self.db.cursor().execute("rollback")
            raise
        else:
            self.db.cursor().execute("commit")

    def mk_url(self, host, path, **qdict):
        query = urlparse.urlencode(qdict)
        return urlparse.urlunparse((
            self.SCHEME, host, path, None, query, None))

    @property
    def announce_urls(self):
        """Yields all user-specific announce URLs currently used by BTN."""
        yield self.mk_url("landof.tv", "%s/announce" % self.passkey)

    @property
    def endpoint(self):
        """The HTTP endpoint to the API."""
        return self.mk_url(self.API_HOST, self.API_PATH)

    def call_url(self, method, url, **kwargs):
        """A helper function to make a normal HTTP call to the BTN site.

        This will consume a token from `token_bucket`, blocking if necessary.

        Args:
            method: A `requests.method` to use when calling (i.e., either
                `requests.get` or `requests.post`).
            url: The URL to call.
            **kwargs: The kwargs to pass to the `requests` method.

        Returns:
            The `requests.response`.
        """
        if self.token_bucket:
            self.token_bucket.consume(1)
        log().debug("%s", url)
        response = method(url, **kwargs)
        if response.status_code != requests.codes.ok:
            raise APIError(response.text, response.status_code)
        return response

    def call(self, method, path, qdict, **kwargs):
        """A helper function to make a normal HTTP call to the BTN site.

        This will consume a token from `token_bucket`, blocking if necessary.

        Args:
            method: A `requests.method` to us ewhen calling (i.e., either
                `requests.get` or `requests.post`).
            path: The HTTP path to the URL to call.
            qdict: A dictionary of query parameters.
            **kwargs: The kwargs to pass to the `requests` method.

        Returns:
            The `requests.response`.
        """
        return self.call_url(
            method, self.mk_url(self.HOST, path, **qdict), **kwargs)

    def get(self, path, **qdict):
        """A helper function to make a normal HTTP GET call to the BTN site.

        This will consume a token from `token_bucket`, blocking if necessary.

        Args:
            path: The HTTP path to the URL to call.
            **qdict: A dictionary of query parameters.

        Returns:
            The `requests.response`.
        """
        return self.call(requests.get, path, qdict)

    def get_url(self, url, **kwargs):
        """A helper function to make a normal HTTP GET call to the BTN site.

        This will consume a token from `token_bucket`, blocking if necessary.

        Args:
            url: The full URL to call.
            **kwargs: The kwargs to pass to `requests.get`.

        Returns:
            The `requests.response`.
        """
        return self.call_url(requests.get, url, **kwargs)

    def call_api(self, method, *params, leave_tokens=None,
                 block_on_token=None, consume_token=None):
        """A low-level function to call the API.

        This may consume a token from `api_token_bucket`, blocking if
        necessary.

        Args:
            method: The string name of the API method to call.
            *params: The parameters to the API method. Parameters may be either
                strings or numbers.
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.

        Returns:
            The result of the API call. This may be any JSON object, parsed as
                a python object.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        if block_on_token is None:
            block_on_token = True
        if consume_token is None:
            consume_token = True
        params = [self.key] + list(params)
        data = json_lib.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params})

        if consume_token and self.api_token_bucket:
            if block_on_token:
                self.api_token_bucket.consume(1, leave=leave_tokens)
            else:
                success, _, _, _ = self.api_token_bucket.try_consume(
                    1, leave=leave_tokens)
                if not success:
                    raise WouldBlock()

        call_time = time.time()
        response = requests.post(
            self.endpoint, headers={"Content-Type": "application/json"},
            data=data)

        if len(response.text) < 100:
            log_text = response.text
        else:
            log_text = "%.97s..." % response.text
        log().debug("%s -> %s", data, log_text)

        if response.status_code != requests.codes.ok:
            raise APIError(response.text, response.status_code)

        response = response.json()
        if "error" in response:
            error = response["error"]
            message = error["message"]
            code = error["code"]
            if code == APIError.CODE_CALL_LIMIT_EXCEEDED:
                def fill(_, as_of, n):
                    period = self.api_token_bucket.period
                    start = as_of - period
                    return [
                        start + period * (i + 1) / (n + 1) for i in range(n)]
                if self.api_token_bucket:
                    self.api_token_bucket.set(0, as_of=call_time, fill=fill)
            raise APIError(message, code)

        return response["result"]

    def get_global(self, name):
        """Gets a value from the "global" table in the user database.

        Values in the "global" are stored with BLOB affinity, so the return
        value may be of any data type that can be stored in SQLite.

        This function only issues SELECT against the database; no transaction
        is used.

        Args:
            name: The string name of the global value entry.

        Returns:
            The value from the "global" table, or None if no matching row
                exists.
        """
        row = self.db.cursor().execute(
            "select value from user.global where name = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_global(self, name, value):
        """Sets a value in the "global" table in the user database.

        This function issues a SAVEPOINT / DML / RELEASE sequence to the
        database.

        Args:
            name: The string name of the global value entry.
            value: The value of the global value entry. May be any data type
                that can be coerced in SQLite.
        """
        with self.db:
            self.db.cursor().execute(
                "insert or replace into user.global (name, value) "
                "values (?, ?)",
                (name, value))

    def delete_global(self, name):
        """Deletes a value from the "global" table in the user database.

        This function issues a SAVEPOINT / DML / RELEASE sequence to the
        database.

        Args:
            name: The string name of the global value entry.
        """
        with self.db:
            self.db.cursor().execute(
                "delete from user.global where name = ?", (name,))

    def get_changestamp(self):
        """Gets a new changestamp from the increasing sequence in the database.

        This function issues a SAVEPOINT / DML / RELEASE sequence to the
        database.

        Returns:
            An integer changestamps, unique and larger than the result of any
                previous call to `get_changestamp()` for this database.
        """
        with self.db:
            # Workaround so savepoint behaves like begin immediate
            self.db.cursor().execute(
                "insert or ignore into user.global (name, value) "
                "values (?, ?)",
                ("changestamp", 0))
            try:
                changestamp = int(self.get_global("changestamp") or 0)
            except ValueError:
                changestamp = 0
            changestamp += 1
            self.set_global("changestamp", changestamp)
            return changestamp

    def getTorrentsJson(self, results=10, offset=0, leave_tokens=None,
                        block_on_token=None, consume_token=None, **kwargs):
        """Issues a "getTorrents" API call, and return the result as parsed
        JSON.

        Args:
            results: The maximum number of results to return. Defaults to 10.
            offset: The offset of the results to return, from the list of all
                matching torrent entries. Defaults to 0.
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.
            **kwargs: A dictionary of filter parameters. See
                http://apidocs.broadcasthe.net/apigen/class-btnapi.html
                for filter semantics.

        Returns:
            A dict of {"results": total number of results matching the filters,
                "torrents": a list of parsed JSON torrent entries}.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        return self.call_api(
            "getTorrents", kwargs, results, offset, leave_tokens=leave_tokens,
            block_on_token=block_on_token, consume_token=consume_token)

    def _torrent_entry_from_json(self, tj):
        """Create a TorrentEntry from parsed JSON.

        This always creates a new `TorrentEntry`, `Group` and `Series` object.
        These objects are not cached.

        Args:
            tj: A parsed JSON object, as returned from "getTorrents" or
                "getTorrentById".

        Returns:
            A TorrentEntry object.
        """
        series = Series(
            self, id=int(tj["SeriesID"]), name=tj["Series"],
            banner=tj["SeriesBanner"], poster=tj["SeriesPoster"],
            imdb_id=tj["ImdbID"],
            tvdb_id=int(tj["TvdbID"]) if tj.get("TvdbID") else None,
            tvrage_id=int(tj["TvrageID"]) if tj.get("TvrageID") else None,
            youtube_trailer=tj["YoutubeTrailer"] or None)
        group = Group(
            self, id=int(tj["GroupID"]), category=tj["Category"],
            name=tj["GroupName"], series=series)
        return TorrentEntry(self, id=int(tj["TorrentID"]), group=group,
            codec=tj["Codec"], container=tj["Container"],
            info_hash=tj["InfoHash"], leechers=int(tj["Leechers"]),
            origin=tj["Origin"], release_name=tj["ReleaseName"],
            resolution=tj["Resolution"], seeders=int(tj["Seeders"]),
            size=int(tj["Size"]), snatched=int(tj["Snatched"]),
            source=tj["Source"], time=int(tj["Time"]))

    def getTorrentsCached(self, results=None, offset=None, **kwargs):
        """Issues a synthetic "getTorrents" call against the local cache.

        Args:
            results: The maximum number of results to return. Defaults to 10.
            offset: The offset of the results to return, from the list of all
                matching torrent entries. Defaults to 0.
            **kwargs: A dictionary of filter parameters. See
                http://apidocs.broadcasthe.net/apigen/class-btnapi.html
                for filter semantics.

        Returns:
            A list of `TorrentEntry` objects.
        """
        params = []
        if "id" in kwargs:
            params.append(("torrent_entry.id = ?", kwargs["id"]))
        if "series" in kwargs:
            params.append(("series.name = ?", kwargs["series"]))
        if "category" in kwargs:
            params.append(("category.name = ?", kwargs["category"]))
        if "name" in kwargs:
            params.append(("torrent_entry_group.name = ?", kwargs["name"]))
        if "codec" in kwargs:
            params.append(("codec.name = ?", kwargs["codec"]))
        if "container" in kwargs:
            params.append(("container.name = ?", kwargs["container"]))
        if "source" in kwargs:
            params.append(("source.name = ?", kwargs["source"]))
        if "resolution" in kwargs:
            params.append(("resolution.name = ?", kwargs["resolution"]))
        if "origin" in kwargs:
            params.append(("origin.name = ?", kwargs["origin"]))
        if "hash" in kwargs:
            params.append(("torrent_entry.info_hash = ?", kwargs["hash"]))
        if "tvdb" in kwargs:
            params.append(("series.tvdb_id = ?", kwargs["tvdb"]))
        if "tvrage" in kwargs:
            params.append(("series.tvrage_id = ?", kwargs["tvrage"]))
        if "time" in kwargs:
            params.append(("torrent_entry.time = ?", kwargs["time"]))
        if "age" in kwargs:
            params.append(
                ("torrent_entry.time = ?", time.time() - kwargs["age"]))

        params.append(("deleted = ?", 0))

        constraint = " and ".join(c for c, _ in params)
        constraint_clause = "where %s" % constraint

        values = [v for _, v in params]

        query_base  = (
            "select %s "
            "from torrent_entry "
            "inner join torrent_entry_group on "
            "torrent_entry.group_id = torrent_entry_group.id "
            "inner join series on "
            "torrent_entry_group.series_id = series.id "
            "inner join category on "
            "torrent_entry_group.category_id = category.id "
            "inner join codec on "
            "torrent_entry.codec_id = codec.id "
            "inner join container on "
            "torrent_entry.container_id = container.id "
            "inner join source on "
            "torrent_entry.source_id = source.id "
            "inner join resolution on "
            "torrent_entry.resolution_id = resolution.id "
            "inner join origin on "
            "torrent_entry.origin_id = origin.id "
            "%s "
            "order by torrent_entry.id desc %s %s")

        if results is not None:
            limit_clause = "limit ?"
            values.append(results)
        else:
            limit_clause = ""
        if offset is not None:
            offset_clause = "offset ?"
            values.append(offset)
        else:
            offset_clause = ""

        query = query_base % (
            "torrent_entry.id", constraint_clause, limit_clause, offset_clause)

        with self.db:
            c = self.db.cursor()
            c.execute(query, values)
            return [TorrentEntry._from_db(self, r[0]) for r in c]

    def getTorrents(self, results=10, offset=0, leave_tokens=None,
                    block_on_token=None, consume_token=None, **kwargs):
        """Issues a "getTorrents" API call.

        Args:
            results: The maximum number of results to return. Defaults to 10.
            offset: The offset of the results to return, from the list of all
                matching torrent entries. Defaults to 0.
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.
            **kwargs: A dictionary of filter parameters. See
                http://apidocs.broadcasthe.net/apigen/class-btnapi.html
                for filter semantics.

        Returns:
            A `SearchResult` object.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        sr_json = self.getTorrentsJson(
            results=results, offset=offset, leave_tokens=leave_tokens,
            block_on_token=block_on_token, consume_token=consume_token,
            **kwargs)
        tes = []
        for tj in sr_json.get("torrents", {}).values():
            te = self._torrent_entry_from_json(tj)
            tes.append(te)
        while True:
            try:
                with self.begin():
                    changestamp = self.get_changestamp()
                    for te in tes:
                        te.serialize(changestamp=changestamp)
            except apsw.BusyError:
                log().warning(
                    "BusyError while trying to serialize, will retry")
            else:
                break
        tes= sorted(tes, key=lambda te: -te.id)
        return SearchResult(sr_json["results"], tes)

    def getTorrentByIdJson(self, id, leave_tokens=None, block_on_token=None,
                           consume_token=None):
        """Issues a "getTorrentById" API call, and return the result as parsed
        JSON.

        Args:
            id: The id of the torrent entry on BTN.
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.

        Returns:
            A torrent entry as parsed JSON.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        return self.call_api(
            "getTorrentById", id, leave_tokens=leave_tokens,
            block_on_token=block_on_token, consume_token=consume_token)

    def getTorrentByIdCached(self, id):
        """Get a `TorrentEntry` from the local cache.

        Args:
            id: The id of the torrent entry on BTN.

        Returns:
            A `TorrentEntry`, or None if the given id is not found in the local
                cache.
        """
        return TorrentEntry._from_db(self, id)

    def getTorrentById(self, id, leave_tokens=None, block_on_token=None,
                       consume_token=None):
        """Issues a "getTorrentById" API call.

        Args:
            id: The id of the torrent entry on BTN.
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.

        Returns:
            A `TorrentEntry`, or None if the requested id was not found.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        tj = self.getTorrentByIdJson(
            id, leave_tokens=leave_tokens, block_on_token=block_on_token,
            consume_token=consume_token)
        te = self._torrent_entry_from_json(tj) if tj else None
        if te:
            with self.db:
                te.serialize()
        return te

    def getUserSnatchlistJson(self, results=10, offset=0, leave_tokens=None,
                              block_on_token=None, consume_token=None):
        """Issues a "getUserSnatchlist" API call, and return the result as
        parsed JSON.

        Args:
            results: The maximum number of results to return. Defaults to 10.
            offset: The offset of the results to return, from the list of all
                matching torrent entries. Defaults to 0.
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.

        Returns:
            ???

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        return self.call_api(
            "getUserSnatchlist", results, offset, leave_tokens=leave_tokens,
            block_on_token=block_on_token, consume_token=consume_token)

    def _user_info_from_json(self, j):
        """Create a `UserInfo` from parsed JSON.

        This always creates a new `UserInfo`. These objects are not cached.

        Args:
            j: A parsed JSON object, as returned from "userInfo".

        Returns:
            A `UserInfo` object.
        """
        return UserInfo(
            self, id=int(j["UserID"]), bonus=int(j["Bonus"]),
            class_name=j["Class"], class_level=int(j["ClassLevel"]),
            download=int(j["Download"]), email=j["Email"],
            enabled=bool(int(j["Enabled"])), hnr=int(j["HnR"]),
            invites=int(j["Invites"]), join_date=int(j["JoinDate"]),
            lumens=int(j["Lumens"]), paranoia=int(j["Paranoia"]),
            snatches=int(j["Snatches"]), title=j["Title"],
            upload=int(j["Upload"]),
            uploads_snatched=int(j["UploadsSnatched"]), username=j["Username"])

    def userInfoJson(self, leave_tokens=None, block_on_token=None,
                     consume_token=None):
        """Issues a "userInfo" API call, and return the result as
        parsed JSON.

        Args:
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.

        Returns:
            A user info parsed JSON dictionary.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        return self.call_api(
            "userInfo", leave_tokes=leave_tokens,
            block_on_token=block_on_token, consume_token=consume_token)

    def userInfoCached(self):
        """Gets a `UserInfo` from the local cache.

        Returns:
            A `UserInfo` object about the local user, or None of none was found
                in the local cache.
        """
        return UserInfo._from_db(self)

    def userInfo(self, leave_tokens=None, block_on_token=None,
                 consume_token=None):
        """Issues a "userInfo" API call.

        Args:
            leave_tokens: Block until we would be able to leave at least this
                many tokens in `api_token_bucket`, after one is consumed.
                Defaults to 0.
            block_on_token: Whether or not to block waiting for a token. If
                False and no tokens are available, `WouldBlock` is raised.
                Defaults to True.
            consume_token: Whether or not to consume a token at all. Defaults
                to True. This should only be False when you are handling token
                management outside this function.

        Returns:
            A `UserInfo` object.

        Raises:
            WouldBlock: When block_on_token is False and no tokens are
                available.
            APIError: When we receive an error from the API.
        """
        uj = self.userInfoJson(
            leave_tokens=leave_tokens, block_on_token=block_on_token,
            consume_token=consume_token)
        ui = self._user_info_from_json(uj) if uj else None
        if ui:
            with self.db:
                ui.serialize()
        return ui

    #def feed(self, type=None, timestamp=None):
    #    if timestamp is None:
    #        timestamp = 0

    #    args = {
    #        "delete": CrudResult.ACTION_DELETE,
    #        "update": CrudResult.ACTION_UPDATE,
    #        "ts": timestamp}

    #    type_to_table = {
    #        CrudResult.TYPE_TORRENT_ENTRY: "torrent_entry",
    #        CrudResult.TYPE_GROUP: "torrent_entry_group",
    #        CrudResult.TYPE_SERIES: "series"}

    #    if type is None:
    #        candidates = type_to_table.items()
    #    else:
    #        candidates = ((type, type_to_table[type]),)

    #    for type, table in candidates:
    #        c = self.db.cursor()
    #        c.execute(
    #            "select id, updated_at, deleted "
    #            "from %(table)s where "
    #            "updated_at > ?" % {"table": table},
    #            (timestamp,))
    #        for id, updated_at, deleted in c:
    #            if deleted:
    #                action = CrudResult.ACTION_DELETE
    #            else:
    #                action = CrudResult.ACTION_UPDATE
    #            yield CrudResult(type, action, id)
