#!/usr/bin/python

import json as json_lib
import logging
import os
import re
import sqlite3
import threading
from urllib import parse as urlparse

import better_bencode
import requests
import token_bucket as token_bucket_lib
import yaml


TRACKER_REGEXES = (
    re.compile(
        r"https?://landof.tv/(?P<passkey>[a-z0-9]{32})/"),
    re.compile(
        r"https?://tracker.broadcasthe.net:34001/"
        r"(?P<passkey>[a-z0-9]{32})/"),
)

EPISODE_SEED_TIME = 24 * 3600
EPISODE_SEED_RATIO = 1.0
SEASON_SEED_TIME = 120 * 3600
SEASON_SEED_RATIO = 1.0

# Minimum completion for a torrent to count on your history.
TORRENT_HISTORY_FRACTION = 0.1


def log():
    return logging.getLogger(__name__)


class Series(object):

    @classmethod
    def _create_schema(cls, api):
        api.db.execute(
            "create table if not exists series ("
            "  id integer primary key,"
            "  imdb_id text,"
            "  name text,"
            "  banner text,"
            "  poster text,"
            "  tvdb_id integer,"
            "  tvrage_id integer,"
            "  youtube_trailer text)")

    @classmethod
    def _from_db(cls, api, id):
        row = api.db.execute(
            "select * from series where id = ?", (id,)).fetchone()
        if not row:
            return None
        return cls(api, **row)

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

    def serialize(self):
        self.api.db.execute(
            "insert or replace into series "
            "(id, imdb_id, name, banner, poster, tvdb_id, tvrage_id, "
            " youtube_trailer) "
            "values "
            "(?,"
            "coalesce(?,(select imdb_id from series where id = ?)),"
            "coalesce(?,(select name from series where id = ?)),"
            "coalesce(?,(select banner from series where id = ?)),"
            "coalesce(?,(select poster from series where id = ?)),"
            "coalesce(?,(select tvdb_id from series where id = ?)),"
            "coalesce(?,(select tvrage_id from series where id = ?)),"
            "coalesce(?,(select youtube_trailer from series where id = ?)))",
            (self.id,
             self.imdb_id, self.id,
             self.name, self.id,
             self.banner, self.id,
             self.poster, self.id,
             self.tvdb_id, self.id,
             self.tvrage_id, self.id,
             self.youtube_trailer, self.id))

    def __repr__(self):
        return "<Series %s \"%s\">" % (self.id, self.name)


class Group(object):

    @classmethod
    def _create_schema(cls, api):
        api.db.execute(
            "create table if not exists torrent_entry_group ("
            "  id integer primary key,"
            "  category_id integer not null,"
            "  name text not null,"
            "  series_id integer not null)")
        api.db.execute(
            "create table if not exists category ("
            "  id integer primary key,"
            "  name text not null)")
        api.db.execute(
            "create unique index if not exists category_name "
            "on category (name)")

    @classmethod
    def _from_db(cls, api, id):
        row = api.db.execute(
            "select "
            "  torrent_entry_group.id as id,"
            "  category.name as category,"
            "  torrent_entry_group.name as name,"
            "  series_id "
            "from torrent_entry_group "
            "left outer join category "
            "on torrent_entry_group.category_id = category.id "
            "where torrent_entry_group.id = ?",
            (id,)).fetchone()
        if not row:
            return None
        row = dict(row)
        series = Series._from_db(api, row.pop("series_id"))
        return cls(api, series=series, **row)

    def __init__(self, api, id=None, category=None, name=None, series=None):
        self.api = api

        self.id = id
        self.category = category
        self.name = name
        self.series = series

    def serialize(self):
        self.api.db.execute(
            "insert or ignore into category (name) values (?)",
            (self.category,))
        category_id = self.api.db.execute(
            "select id from category where name = ?",
            (self.category,)).fetchone()[0]
        self.series.serialize()
        self.api.db.execute(
            "insert or replace into torrent_entry_group "
            "(id, category_id, name, series_id) values "
            "(?, ?, ?, ?)",
            (self.id, category_id, self.name, self.series.id))

    def __repr__(self):
        return "<Group %s \"%s\" \"%s\">" % (
            self.id, self.series.name, self.name)


class TorrentEntry(object):

    CATEGORY_EPISODE = "Episode"
    CATEGORY_SEASON = "Season"

    GROUP_EPISODE_REGEX = re.compile(
        r"S(?P<season>\d+)(?P<episodes>(E\d+)+)$")
    PARTIAL_EPISODE_REGEX = re.compile(r"E(?P<episode>\d\d)")
    GROUP_EPISODE_DATE_REGEX = re.compile(
        r"(?P<year>\d\d\d\d)\.(?P<month>\d\d)\.(?P<day>\d\d)")
    GROUP_EPISODE_SPECIAL_REGEX = re.compile(
        r"Season (?P<season>\d+) - (?P<name>.*)")

    GROUP_FULL_SEASON_REGEX = re.compile(r"Season (?P<season>\d+)$")

    @classmethod
    def _create_schema(cls, api):
        api.db.execute(
            "create table if not exists torrent_entry ("
            "  id integer primary key,"
            "  codec_id integer not null,"
            "  container_id integer not null,"
            "  group_id integer not null,"
            "  info_hash text,"
            "  leechers integer not null,"
            "  origin_id integer not null,"
            "  release_name text not null,"
            "  resolution_id integer not null,"
            "  seeders integer not null,"
            "  size integer not null,"
            "  snatched integer not null,"
            "  source_id integer not null,"
            "  time integer not null)")
        api.db.execute(
            "create table if not exists codec ("
            "  id integer primary key,"
            "  name text not null)")
        api.db.execute(
            "create unique index if not exists codec_name "
            "on codec (name)")
        api.db.execute(
            "create table if not exists container ("
            "  id integer primary key,"
            "  name text not null)")
        api.db.execute(
            "create unique index if not exists container_name "
            "on container (name)")
        api.db.execute(
            "create table if not exists origin ("
            "  id integer primary key,"
            "  name text not null)")
        api.db.execute(
            "create unique index if not exists origin_name "
            "on origin (name)")
        api.db.execute(
            "create table if not exists resolution ("
            "  id integer primary key,"
            "  name text not null)")
        api.db.execute(
            "create unique index if not exists resolution_name "
            "on resolution (name)")
        api.db.execute(
            "create table if not exists source ("
            "  id integer primary key,"
            "  name text not null)")
        api.db.execute(
            "create unique index if not exists source_name "
            "on source (name)")

    @classmethod
    def _from_db(cls, api, id):
        row = api.db.execute(
            "select "
            "  torrent_entry.id as id,"
            "  codec.name as codec,"
            "  container.name as container,"
            "  torrent_entry.group_id as group_id,"
            "  info_hash,"
            "  leechers,"
            "  origin.name as origin,"
            "  release_name,"
            "  resolution.name as resolution,"
            "  seeders,"
            "  size,"
            "  snatched,"
            "  source.name as source,"
            "  time "
            "from torrent_entry "
            "left outer join codec on codec.id = codec_id "
            "left outer join container on container.id = container_id "
            "left outer join origin on origin.id = origin_id "
            "left outer join resolution on resolution.id = resolution_id "
            "left outer join source on source.id = source_id "
            "where torrent_entry.id = ?",
            (id,)).fetchone()
        if not row:
            return None
        row = dict(**row)
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
        self._info_hash = info_hash
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

    def serialize(self):
        if self.codec is not None:
            self.api.db.execute(
                "insert or ignore into codec (name) values (?)", (self.codec,))
            codec_id = self.api.db.execute(
                "select id from codec where name = ?",
                (self.codec,)).fetchone()[0]
        else:
            codec_id = None
        if self.container is not None:
            self.api.db.execute(
                "insert or ignore into container (name) values (?)",
                (self.container,))
            container_id = self.api.db.execute(
                "select id from container where name = ?",
                (self.container,)).fetchone()[0]
        else:
            container_id = None
        if self.origin is not None:
            self.api.db.execute(
                "insert or ignore into origin (name) values (?)",
                (self.origin,))
            origin_id = self.api.db.execute(
                "select id from origin where name = ?",
                (self.origin,)).fetchone()[0]
        else:
            origin_id = None
        if self.resolution is not None:
            self.api.db.execute(
                "insert or ignore into resolution (name) values (?)",
                (self.resolution,))
            resolution_id = self.api.db.execute(
                "select id from resolution where name = ?",
                (self.resolution,)).fetchone()[0]
        else:
            resolution_id = None
        if self.source is not None:
            self.api.db.execute(
                "insert or ignore into source (name) values (?)",
                (self.source,))
            source_id = self.api.db.execute(
                "select id from source where name = ?",
                (self.source,)).fetchone()[0]
        else:
            source_id = None
        self.group.serialize()
        self.api.db.execute(
            "insert or replace into torrent_entry ("
            "id,"
            "codec_id,"
            "container_id,"
            "group_id,"
            "info_hash,"
            "leechers,"
            "origin_id,"
            "release_name,"
            "resolution_id,"
            "seeders,"
            "size,"
            "snatched,"
            "source_id,"
            "time"
            ") values ("
            "?,"
            "coalesce(?,(select codec_id from torrent_entry where id = ?)),"
            "coalesce(?,(select container_id from torrent_entry where id = ?)),"
            "coalesce(?,(select group_id from torrent_entry where id = ?)),"
            "coalesce(?,(select info_hash from torrent_entry where id = ?)),"
            "coalesce(?,(select leechers from torrent_entry where id = ?)),"
            "coalesce(?,(select origin_id from torrent_entry where id = ?)),"
            "coalesce(?,(select release_name from torrent_entry where id = ?)),"
            "coalesce(?,(select resolution_id from torrent_entry where id = ?)),"
            "coalesce(?,(select seeders from torrent_entry where id = ?)),"
            "coalesce(?,(select size from torrent_entry where id = ?)),"
            "coalesce(?,(select snatched from torrent_entry where id = ?)),"
            "coalesce(?,(select source_id from torrent_entry where id = ?)),"
            "coalesce(?,(select time from torrent_entry where id = ?))"
            ")",
            (self.id,
             codec_id, self.id,
             container_id, self.id,
             self.group.id, self.id,
             self._info_hash, self.id,
             self.leechers, self.id,
             origin_id, self.id,
             self.release_name, self.id,
             resolution_id, self.id,
             self.seeders, self.id,
             self.size, self.id,
             self.snatched, self.id,
             source_id, self.id,
             self.time, self.id))

    @property
    def link(self):
        return self.api.mk_url(
            self.api.HOST, "/torrents.php", action="download",
            authkey=self.api.authkey, torrent_pass=self.api.passkey,
            id=self.id)

    @property
    def raw_torrent_path(self):
        return os.path.join(self.api.cache_path, "torrents", str(self.id))

    @property
    def raw_torrent(self):
        with self._lock:
            if self._raw_torrent is not None:
                return self._raw_torrent
            if os.path.exists(self.raw_torrent_path):
                with open(self.raw_torrent_path, mode="rb") as f:
                    self._raw_torrent = f.read()
                return self._raw_torrent
            log().debug("Fetching raw torrent for %s", repr(self))
            response = self.api.get_url(self.link)
            if response.status_code != requests.codes.ok:
                raise APIError(response.text, response.status_code)
            self._raw_torrent = response.content
            if self.api.store_raw_torrent:
                if not os.path.exists(os.path.dirname(self.raw_torrent_path)):
                    os.makedirs(os.path.dirname(self.raw_torrent_path))
                with open(self.raw_torrent_path, mode="wb") as f:
                    f.write(self._raw_torrent)
            return self._raw_torrent

    @property
    def info_hash(self):
        with self._lock:
            if self._info_hash is not None:
                return self._info_hash
            row = self.api.db.execute(
                "select info_hash from torrent_entry where id = ?",
                (self.id,)).fetchone()
            if row and row["info_hash"] is not None:
                self._info_hash = row["info_hash"]
                return self._info_hash
            info = self.torrent_object[b"info"]
            self._info_hash = hashlib.sha1(
                better_bencode.dumps(info)).hexdigest().upper()
            with self.api.db:
                self.api.db.execute(
                    "update or ignore torrent_entry set info_hash = ? "
                    "where id = ?", (self._info_hash, self.id))
            return self._info_hash

    @property
    def torrent_object(self):
        return better_bencode.loads(self.raw_torrent)

    def __repr__(self):
        return "<TorrentEntry %d \"%s\">" % (self.id, self.release_name)


class UserInfo(object):

    @classmethod
    def _create_schema(cls, api):
        api.db.execute(
            "create table if not exists user_info ("
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
        row = api.db.execute("select * from user_info limit 1").fetchone()
        if not row:
            return None
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
        self.api.db.execute("delete from user_info")
        self.api.db.execute(
            "insert or replace into user_info ("
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
             self.download, self.email, self.enabled, self.hnr, self.invites,
             self.join_date, self.lumens, self.paranoia, self.snatches,
             self.title, self.upload, self.uploads_snatched, self.username))

    def __repr__(self):
        return "<UserInfo %s \"%s\">" % (self.id, self.username)


class SearchResult(object):

    def __init__(self, results, torrents):
        self.results = int(results)
        self.torrents = torrents or ()


class Error(Exception):

    pass


class APIError(Error):

    CODE_CALL_LIMIT_EXCEEDED = -32002

    def __init__(self, message, code):
        super(APIError, self).__init__(message)
        self.code = code


class API(object):

    SCHEME = "https"

    HOST = "broadcasthe.net"

    API_HOST = "api.btnapps.net"
    API_PATH = "/"

    DEFAULT_TOKEN_RATE = 20
    DEFAULT_TOKEN_PERIOD = 100

    DEFAULT_API_TOKEN_RATE = 150
    DEFAULT_API_TOKEN_PERIOD = 3600

    CACHE_FIRST = "first"
    CACHE_BYPASS = "bypass"
    CACHE_ONLY = "only"

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
            self.token_bucket = token_bucket_lib.TokenBucket(
                self.db_path, "web:" + self.key, self.token_rate,
                self.token_period)
        if api_token_bucket is not None:
            self.api_token_bucket = api_token_bucket
        else:
            self.api_token_bucket = token_bucket_lib.ScheduledTokenBucket(
                self.db_path, self.key, self.api_token_rate,
                self.api_token_period)

        self._local = threading.local()
        self._db = None

    @property
    def db_path(self):
        if self.cache_path:
            return os.path.join(self.cache_path, "cache.db")
        return none

    @property
    def config_path(self):
        if self.cache_path:
            return os.path.join(self.cache_path, "config.yaml")
        return None

    @property
    def db(self):
        db = getattr(self._local, "db", None)
        if db is not None:
            return db
        if self.db_path is None:
            return None
        if not os.path.exists(os.path.dirname(self.db_path)):
            os.makedirs(os.path.dirname(self.db_path))
        db = sqlite3.connect(self.db_path)
        self._local.db = db
        db.row_factory = sqlite3.Row
        with db:
            Series._create_schema(self)
            Group._create_schema(self)
            TorrentEntry._create_schema(self)
            UserInfo._create_schema(self)
        return db

    def mk_url(self, host, path, **qdict):
        query = urlparse.urlencode(qdict)
        return urlparse.urlunparse((
            self.SCHEME, host, path, None, query, None))

    @property
    def endpoint(self):
        return self.mk_url(self.API_HOST, self.API_PATH)

    def call_url(self, method, url, **kwargs):
        if self.token_bucket:
            self.token_bucket.consume(1)
        log().debug("%s", url)
        response = method(url, **kwargs)
        if response.status_code != requests.codes.ok:
            raise APIError(response.text, response.status_code)
        return response

    def call(self, method, path, qdict, **kwargs):
        return self.call_url(
            method, self.mk_url(self.HOST, path, **qdict), **kwargs)

    def get(self, path, **qdict):
        return self.call(requests.get, path, qdict)

    def get_url(self, url, **kwargs):
        return self.call_url(requests.get, url, **kwargs)

    def call_api(self, method, *params):
        params = [self.key] + list(params)
        data = json_lib.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params})

        if self.api_token_bucket:
            self.api_token_bucket.consume(1)

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
                if self.api_token_bucket:
                    self.api_token_bucket.set(0)
            raise APIError(message, code)

        return response["result"]

    def _from_db(self, id):
        return TorrentEntry._from_db(self, id)

    def getTorrentsJson(self, results=10, offset=0, **kwargs):
        return self.call_api("getTorrents", kwargs, results, offset)

    def _torrent_entry_from_json(self, tj):
        series = Series(
            self, id=tj["SeriesID"], name=tj["Series"],
            banner=tj["SeriesBanner"], poster=tj["SeriesPoster"],
            imdb_id=tj["ImdbID"],
            tvdb_id=int(tj["TvdbID"]) if tj.get("TvdbID") else None,
            tvrage_id=int(tj["TvrageID"]) if tj.get("TvrageID") else None,
            youtube_trailer=tj["YoutubeTrailer"] or None)
        group = Group(
            self, id=tj["GroupID"], category=tj["Category"],
            name=tj["GroupName"], series=series)
        return TorrentEntry(self, id=int(tj["TorrentID"]), group=group,
            codec=tj["Codec"], container=tj["Container"],
            info_hash=tj["InfoHash"], leechers=int(tj["Leechers"]),
            origin=tj["Origin"], release_name=tj["ReleaseName"],
            resolution=tj["Resolution"], seeders=int(tj["Seeders"]),
            size=int(tj["Size"]), snatched=int(tj["Snatched"]),
            source=tj["Source"], time=int(tj["Time"]))

    def getTorrents(self, cache=None, results=10, offset=0, **kwargs):
        if cache == self.CACHE_ONLY:
            assert offset == 0, offset
            assert results >= 1, results
            assert tuple(kwargs.keys()) == ("hash",), kwargs
            row = self.db.execute(
                "select id from torrent_entry where info_hash = ?",
                (kwargs["hash"],)).fetchone()
            if row:
                return SearchResult(1, [self._from_db(row[0])])
            return SearchResult(0, [])
        sr_json = self.getTorrentsJson(
            results=results, offset=offset, **kwargs)
        tes = []
        for tj in sr_json.get("torrents", {}).values():
            te = self._torrent_entry_from_json(tj)
            tes.append(te)
        with self.db:
            for te in tes:
                te.serialize()
        tes= sorted(tes, key=lambda te: -te.id)
        return SearchResult(sr_json["results"], tes)

    def getTorrentsPaged(self, **kwargs):
        offset = 0
        while True:
            sr = self.getTorrents(offset=offset, results=2**31, **kwargs)
            for te in sr.torrents:
                yield te
            if offset + len(sr.torrents) >= sr.results:
                break
            offset += len(sr.torrents)

    def getTorrentByIdJson(self, id):
        return self.call_api("getTorrentById", id)

    def getTorrentById(self, id, cache=None):
        if cache is None:
            cache = self.CACHE_FIRST
        if cache in (self.CACHE_FIRST, self.CACHE_ONLY):
            te = self._from_db(id)
            if te:
                return te
            if cache == self.CACHE_ONLY:
                return None
        tj = self.getTorrentByIdJson(id)
        te = self._torrent_entry_from_json(tj) if tj else None
        if te:
            with self.db:
                te.serialize()
        return te

    def getUserSnatchlistJson(self, results=10, offset=0):
        return self.call_api("getUserSnatchlist", results, offset)

    def _user_info_from_json(self, j):
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

    def userInfoJson(self):
        return self.call_api("userInfo")

    def userInfo(self, cache=None):
        if cache is None:
            cache = self.CACHE_FIRST
        if cache in (self.CACHE_FIRST, self.CACHE_ONLY):
            ui = UserInfo._from_db(self)
            if ui:
                return ui
            if cache == self.CACHE_ONLY:
                return None
        uj = self.userInfoJson()
        ui = self._user_info_from_json(uj) if uj else None
        if ui:
            with self.db:
                ui.serialize()
        return ui
