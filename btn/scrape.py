# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.
"""Several long-lived-daemon-style classes to scrape BTN and update the cache.
"""

import abc
import contextlib
import logging
import sqlite3
import threading
import time
from typing import Iterator
from typing import List
from typing import Tuple
import urllib.parse

import feedparser
import requests

from . import api as api_lib
from . import daemon
from . import dbver
from . import metadata_db
from . import ratelimit
from . import site
from . import user_db

_LOG = logging.getLogger(__name__)


class NonFatal(Exception):
    pass


# NB: We used to have a TorrentFileScraper that would download torrent files
# from the site if we didn't have file_info for them. Staff does not like this!
# Do not do it.


class _Base(daemon.Daemon):
    def __init__(self, *, wait=True) -> None:
        self._terminated = threading.Event()
        self._condition = threading.Condition()
        self._wait = wait

    def terminate(self) -> None:
        with self._condition:
            self._terminated.set()
            self._condition.notify_all()

    @abc.abstractmethod
    def _step_inner(self) -> float:
        raise NotImplementedError

    def _step_wrap(self) -> float:
        return self._step_inner()

    def step(self) -> float:
        return self._step_wrap()

    def run(self) -> None:
        fail_streak = 0
        while not self._terminated.is_set():
            wait_time: float = 0
            try:
                wait_time = self._step_wrap()
                fail_streak = 0
            except NonFatal:
                _LOG.exception("non-fatal exception")
                fail_streak += 1
            if fail_streak:
                backoff = min(2 ** fail_streak, 300)
                _LOG.info("backing off %.1fs", backoff)
                wait_time = max(wait_time, backoff)
            if self._wait and wait_time > 0:
                with self._condition:
                    self._condition.wait_for(
                        self._terminated.is_set, wait_time
                    )


class _API(_Base):
    def __init__(self, *, api: api_lib.RateLimitedAPI, wait=True) -> None:
        _Base.__init__(self, wait=wait)
        self._api = api

    def terminate(self) -> None:
        super().terminate()
        self._api.get_rate_limiter().set_blocking(False)

    def _step_wrap(self) -> float:
        try:
            return super()._step_wrap()
        except ratelimit.RequestWouldBlock:
            # We're being terminated
            return 0
        except api_lib.CallLimitExceededError:
            # We can retry immediately, the rate limiter will delay the
            # appropriate time
            return 0
        except requests.HTTPError as exc:
            # 4xx errors are fatal; others are not
            if exc.response.status_code // 100 == 4:
                raise
            raise NonFatal() from exc
        except requests.RequestException as exc:
            raise NonFatal() from exc


class _Pool(_Base):
    def _step_wrap(self) -> float:
        try:
            return super()._step_wrap()
        except sqlite3.OperationalError as exc:
            if str(exc) == "database is locked":
                raise NonFatal() from exc
            raise


class _UserAccess(_Base):
    def __init__(self, *, user_access: site.UserAccess, wait=True) -> None:
        _Base.__init__(self, wait=wait)
        self._user_access = user_access

    def terminate(self) -> None:
        super().terminate()
        self._user_access.get_rate_limiter().set_blocking(False)

    def _step_wrap(self) -> float:
        try:
            return super()._step_wrap()
        except ratelimit.RequestWouldBlock:
            # We're being terminated
            return 0
        except requests.HTTPError as exc:
            # 4xx errors are fatal; others are not
            if exc.response.status_code // 100 == 4:
                raise
            raise NonFatal() from exc
        except requests.RequestException as exc:
            raise NonFatal() from exc


_META_VERSION_SUPPORTED = 1000000
_USER_VERSION_SUPPORTED = 1000000
_TORRENTS_VERSION_SUPPORTED = 1000000


@contextlib.contextmanager
def _meta_write(pool: dbver.Pool) -> Iterator[Tuple[sqlite3.Connection, int]]:
    with dbver.begin_pool(pool, dbver.LockMode.IMMEDIATE) as conn:
        version = metadata_db.upgrade(conn)
        dbver.semver_check_breaking(version, _META_VERSION_SUPPORTED)
        yield (conn, version)


@contextlib.contextmanager
def _meta_read(pool: dbver.Pool) -> Iterator[Tuple[sqlite3.Connection, int]]:
    with dbver.begin_pool(pool, dbver.LockMode.DEFERRED) as conn:
        version = metadata_db.get_version(conn)
        dbver.semver_check_breaking(version, _META_VERSION_SUPPORTED)
        yield (conn, version)


@contextlib.contextmanager
def _user_write(pool: dbver.Pool) -> Iterator[Tuple[sqlite3.Connection, int]]:
    with dbver.begin_pool(pool, dbver.LockMode.IMMEDIATE) as conn:
        version = user_db.upgrade(conn)
        dbver.semver_check_breaking(version, _USER_VERSION_SUPPORTED)
        yield (conn, version)


class MetadataScraper(_API, _Pool):
    def __init__(
        self,
        *,
        api: api_lib.RateLimitedAPI,
        metadata_pool: dbver.Pool,
        wait=True
    ) -> None:
        _API.__init__(self, api=api, wait=wait)
        self._metadata_pool = metadata_pool
        self._offset = 0

    def _step_inner(self) -> float:
        _LOG.info("scraping metadata at offset %d", self._offset)
        result = self._api.getTorrents(results=2 ** 31, offset=self._offset)
        update = metadata_db.UnfilteredGetTorrentsResultUpdate(
            self._offset, result
        )
        with _meta_write(self._metadata_pool) as (conn, _):
            update.apply(conn)
        if self._offset + len(result["torrents"]) >= int(result["results"]):
            self._offset = 0
        else:
            # The updater knows to delete entries with ids contained within the
            # result's range, so always make queries such that the ranges
            # overlap
            self._offset += len(result["torrents"]) - 1
        return 0


class MetadataTipScraper(_API, _Pool, _UserAccess):
    def __init__(
        self,
        *,
        api: api_lib.RateLimitedAPI,
        metadata_pool: dbver.Pool,
        user_access: site.UserAccess,
        wait=True
    ) -> None:
        _API.__init__(self, api=api, wait=wait)
        _UserAccess.__init__(self, user_access=user_access, wait=wait)
        self._metadata_pool = metadata_pool
        self._changes = False

    def _check_changes(self) -> None:
        if self._changes:
            return

        resp = self._user_access.get_feed("torrents_all")
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        feed_ids: List[int] = []
        for entry in feed.entries:
            link = entry.link
            qd = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
            feed_ids.append(int(qd["id"][0]))

        with _meta_read(self._metadata_pool) as (conn, version):
            if version == 0:
                db_ids: List[int] = []
            else:
                cur = conn.cursor()
                cur.execute(
                    "select id from torrent_entry where not deleted "
                    "order by time desc, id desc limit ?",
                    (len(feed_ids),),
                )
                db_ids = [i for i, in cur]

        self._changes = feed_ids != db_ids
        if self._changes:
            _LOG.info("feed indicates changes, scraping metadata")
        else:
            _LOG.info("feed indicates no changes. latest is %d", feed_ids[0])

    def _step_inner(self) -> float:
        self._check_changes()
        if self._changes:
            result = self._api.getTorrents(results=2 ** 31, offset=0)
            update = metadata_db.UnfilteredGetTorrentsResultUpdate(0, result)
            with _meta_write(self._metadata_pool) as (conn, _):
                update.apply(conn)
            self._changes = False
        return 60


class SnatchlistScraper(_API, _Pool):

    _BLOCK_SIZE = 10000

    def __init__(
        self,
        *,
        api: api_lib.RateLimitedAPI,
        user_pool: dbver.Pool,
        period: float = 3600,
        wait=True
    ):
        _API.__init__(self, api=api, wait=wait)
        _Pool.__init__(self, wait=wait)
        self._user_pool = user_pool
        self._offset = 0
        self._period = period
        self._start_time = time.monotonic()

    def _step_inner(self) -> float:
        _LOG.info("scraping snatchlist at offset %d", self._offset)
        result = self._api.getUserSnatchlist(
            results=self._BLOCK_SIZE, offset=self._offset
        )
        update = user_db.GetSnatchlistResultUpdate(result)
        with _user_write(self._user_pool) as (conn, _):
            update.apply(conn)

        self._offset += len(result["torrents"])
        if self._offset < int(result["results"]):
            return 0
        now = time.monotonic()
        wait_time = self._period - (now - self._start_time)
        self._offset = 0
        self._start_time = now
        if wait_time > 0:
            _LOG.debug("entire snatchlist scraped, waiting %.1fs", wait_time)
        return wait_time
