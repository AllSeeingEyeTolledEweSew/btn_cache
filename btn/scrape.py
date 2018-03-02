# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.

"""Several long-lived-daemon-style classes to scrape BTN and update the cache.
"""

import feedparser
import logging
import os
import Queue
import threading
import time
from urllib import parse as urllib_parse

import btn


def log():
    """Gets a module-level logger."""
    return logging.getLogger(__name__)


def get_int(api, key):
    """Get an integer global value from a `btn.API`."""
    try:
        return int(api.get_global(key))
    except (ValueError, TypeError):
        return None


def set_int(api, key, value):
    """Set an integer global value on a `btn.API`."""
    if value is None:
        api.delete_global(key)
    else:
        api.set_global(key, str(value))


def apply_contiguous_results_locked(api, offset, sr, changestamp=None):
    """Marks torrent entries as deleted, appropriate to a search result.

    When we receive a search result for getTorrents with no filters, the result
    should be a contiguous slice of all torrents on the site, ordered by time.
    Here we search for any torrent entries in the local cache that ought to be
    in the results (the id is between the oldest and youngest id returned), but
    isn't found there. We mark all these torrent entries as deleted.

    In fact, this is the only way to know when a torrent entry has been deleted
    from the site.

    As a special case, if this is known to be the last page of results on the
    site, any torrent entries older than the oldest returned are marked as
    deleted.

    Args:
        api: A `btn.API` instance.
        offset: The offset parameter that was passed to getTorrents.
        sr: A `btn.SearchResult` returned as a result of getTorrents with no
            filters, and the supplied offset.
        changestamp: An integer changestamp. If None, a new changestamp will be
            generated.

    Returns:
        A (list_of_torrent_entries, is_end) tuple. The list of torrent entries
            is sorted by time descending. is_end specifies whether `sr` was
            determined to represent the last page of results.
    """
    entries = sorted(sr.torrents, key=lambda te: (-te.time, -te.id))
    is_end = offset + len(entries) >= sr.results

    if entries:
        newest = entries[0]
        oldest = entries[-1]
        api.db.cursor().execute(
            "create temp table ids (id integer not null primary key)")
        api.db.cursor().executemany(
            "insert into temp.ids (id) values (?)",
            [(entry.id,) for entry in entries])

        torrent_entries_to_delete = set()
        groups_to_check = set()
        if is_end:
            for id, group_id, in api.db.cursor().execute(
                    "select id, group_id from torrent_entry "
                    "where time <= ? and id < ? and not deleted",
                    (oldest.time, oldest.id)):
                torrent_entries_to_delete.add(id)
                groups_to_check.add(group_id)
        for id, group_id, in api.db.cursor().execute(
                "select id, group_id from torrent_entry "
                "where (not deleted) and time < ? and time > ? and "
                "id not in (select id from temp.ids)",
                (newest.time, oldest.time)):
            torrent_entries_to_delete.add(id)
            groups_to_check.add(group_id)

        api.db.cursor().execute("drop table temp.ids")

        if torrent_entries_to_delete:
            log().debug(
                "Deleting torrent entries: %s",
                sorted(torrent_entries_to_delete))

            if changestamp is None:
                changestamp = api.get_changestamp()

            api.db.cursor().executemany(
                "update torrent_entry set deleted = 1, updated_at = ? "
                "where id = ?",
                [(changestamp, id) for id in torrent_entries_to_delete])
            btn.Group._maybe_delete(
                api, *list(groups_to_check), changestamp=changestamp)

    return entries, is_end


class MetadataScraper(object):
    """A long-lived daemon that updates cached data about all torrents.

    This daemon calls getTorrents with no filters and varying offset. The
    intent is to discover deleted torrents, and freshen metadata.

    This daemon will consume as many tokens from `api.api_token_bucket` as
    possible, up to a configured limit. The intent of this is to defer token
    use to `MetadataTipScraper`.

    Attributes:
        api: A `btn.API` instance.
        target_tokens: A number of tokens to leave as leftover in
            `api.api_token_bucket`.
    """

    KEY_OFFSET = "scrape_next_offset"
    KEY_RESULTS = "scrape_last_results"

    BLOCK_SIZE = 1000

    DEFAULT_TARGET_TOKENS = 0
    DEFAULT_NUM_THREADS = 10

    def __init__(self, api, target_tokens=None, num_threads=None, once=False):
        if num_threads is None:
            num_threads = self.DEFAULT_THREADS
        if target_tokens is None:
            target_tokens = self.DEFAULT_TARGET_TOKENS

        if api.key is None:
            raise ValueError("API key not configured")

        self.api = api
        self.target_tokens = target_tokens
        self.num_threads = num_threads
        self.once = once

        self.lock = threading.RLock()
        self.tokens = None
        self.threads = []

    def update_step(self):
        if self.once:
            tokens, _, _ = self.api.api_token_bucket.peek()
            with self.lock:
                if self.tokens is not None and tokens > self.tokens:
                    log().info("Tokens refilled, quitting")
                    return True
                self.tokens = tokens

        target_tokens = self.target_tokens

        success, _, _, _ = self.api.api_token_bucket.try_consume(
            1, leave=target_tokens)
        if not success:
            return True

        with self.api.begin():
            offset = get_int(self.api, self.KEY_OFFSET) or 0
            results = get_int(self.api, self.KEY_RESULTS)
            next_offset = offset + self.BLOCK_SIZE - 1
            if results and next_offset > results:
                next_offset = 0
            set_int(self.api, self.KEY_OFFSET, next_offset)

        log().info(
            "Trying update at offset %s, %s tokens left", offset,
            self.api.api_token_bucket.peek()[0])

        try:
            sr = self.api.getTorrents(
                results=2**31, offset=offset, consume_token=False)
        except btn.WouldBlock:
            log().info("Out of tokens, quitting")
            return True
        except btn.APIError as e:
            if e.code == e.CODE_CALL_LIMIT_EXCEEDED:
                log().debug("Call limit exceeded, quitting")
                return True
            else:
                raise

        with self.api.begin():
            set_int(self.api, self.KEY_RESULTS, sr.results)
            apply_contiguous_results_locked(self.api, offset, sr)

        return False

    def run(self):
        try:
            while True:
                try:
                    done = self.update_step()
                except:
                    log().exception("during update")
                    done = True
                if done:
                    if self.once:
                        break
                    else:
                        time.sleep(60)
        finally:
            log().debug("shutting down")

    def start(self):
        if self.threads:
            return
        for i in range(self.num_threads):
            t = threading.Thread(
                name="metadata-scraper-%d" % i, target=self.run, daemon=True)
            t.start()
            self.threads.append(t)

    def join(self):
        for t in self.threads:
            t.join()


class MetadataTipScraper(object):

    KEY_LAST = "tip_last_scraped"
    KEY_LAST_TS = "tip_last_scraped_ts"
    KEY_OFFSET = "tip_scrape_offset"
    KEY_OLDEST = "tip_scrape_oldest"
    KEY_OLDEST_TS = "tip_scrape_oldest_ts"
    KEY_NEWEST = "tip_scrape_newest"
    KEY_NEWEST_TS = "tip_scrape_newest_ts"

    def __init__(self, api, once=False):
        if api.key is None:
            raise ValueError("API key not configured")
        if api.authkey is None:
            raise ValueError("authkey not configured")
        if api.passkey is None:
            raise ValueError("passkey not configured")
        if api.auth is None:
            raise ValueError("auth not configured")

        self.api = api
        self.once = once
        self.thread = None

    def get_feed_ids(self):
        user = self.api.userInfoCached()
        if not user:
            user = self.api.userInfo()
        resp = self.api._get(
            "/feeds.php", feed="torrents_all", user=user.id,
            auth=self.api.auth, passkey=self.api.passkey,
            authkey=self.api.authkey)
        feed = feedparser.parse(resp.text)
        ids = []
        for entry in feed.entries:
            link = entry.link
            qd = urllib_parse.parse_qs(urllib_parse.urlparse(link).query)
            ids.append(int(qd["id"][0]))
        return ids

    def update_scrape_results_locked(self, offset, sr):
        entries, is_end = apply_contiguous_results_locked(self.api, offset, sr)

        last_scraped_id = get_int(self.api, self.KEY_LAST)
        last_scraped_ts = get_int(self.api, self.KEY_LAST_TS)
        oldest_id = get_int(self.api, self.KEY_OLDEST)
        oldest_ts = get_int(self.api, self.KEY_OLDEST_TS)
        newest_id = get_int(self.api, self.KEY_NEWEST)
        newest_ts = get_int(self.api, self.KEY_NEWEST_TS)

        if newest_ts is None or (entries and (
                (entries[0].time, entries[0].id) >= (newest_ts, newest_id))):
            newest_id = entries[0].id
            newest_ts = entries[0].time

        done = False
        # Ensure we got a good page overlap.
        if oldest_ts is None or (entries and (
                (entries[0].time, entries[0].id) >= (oldest_ts, oldest_id))):
            if is_end:
                log().info("We reached the oldest torrent entry.")
                done = True
            elif last_scraped_ts is not None and (
                    (entries[-1].time, entries[-1].id) <=
                    (last_scraped_ts, last_scraped_id)):
                log().info("Caught up. Current as of %s.", newest_id)
                done = True
            elif oldest_ts is None or (
                    (entries[-1].time, entries[-1].id) <
                    (oldest_ts, oldest_id)):
                oldest_id = entries[-1].id
                oldest_ts = entries[-1].time
            offset += len(entries) - 1
        else:
            log().info("Missed page overlap, backing off.")
            offset -= len(entries) // 2
            if offset <= 0:
                offset = 0
                oldest_id = None
                oldest_ts = None

        if done:
            set_int(self.api, self.KEY_LAST, newest_id)
            set_int(self.api, self.KEY_LAST_TS, newest_ts)
            set_int(self.api, self.KEY_OFFSET, None)
            set_int(self.api, self.KEY_OLDEST, None)
            set_int(self.api, self.KEY_OLDEST_TS, None)
            set_int(self.api, self.KEY_NEWEST, None)
            set_int(self.api, self.KEY_NEWEST_TS, None)
        else:
            set_int(self.api, self.KEY_OFFSET, offset)
            set_int(self.api, self.KEY_OLDEST, oldest_id)
            set_int(self.api, self.KEY_OLDEST_TS, oldest_ts)
            set_int(self.api, self.KEY_NEWEST, newest_id)
            set_int(self.api, self.KEY_NEWEST_TS, newest_ts)

        return done

    def scrape_step(self):
        with self.api.db:
            offset = get_int(self.api, self.KEY_OFFSET)
            last_scraped = get_int(self.api, self.KEY_LAST)
            db_ids = []

            if offset is None:
                log().debug("No current scrape.")
                c = self.api.db.cursor().execute(
                    "select id from torrent_entry where not deleted "
                    "order by time desc, id desc limit 1000")
                db_ids = [id for id, in c]

        if offset is None:
            feed_ids = self.get_feed_ids()
            db_ids = db_ids[:len(feed_ids)]
            if feed_ids == db_ids and feed_ids[0] == last_scraped:
                log().info("Feed has no changes. Latest is %s.", last_scraped)
                return True
            if set(feed_ids) - set(db_ids):
                log().debug(
                    "in feed but not in db: %s",
                    sorted(set(feed_ids) - set(db_ids)))
            if set(db_ids) - set(feed_ids):
                log().debug(
                    "in db but not in feed: %s",
                    sorted(set(db_ids) - set(feed_ids)))
            offset = 0

        log().info("Scraping at offset %s", offset)

        sr = self.api.getTorrents(results=2**31, offset=offset)

        with self.api.begin():
            return self.update_scrape_results_locked(offset, sr)

    def run(self):
        try:
            while True:
                try:
                    done = self.scrape_step()
                except KeyboardInterrupt:
                    raise
                except:
                    log().exception("during scrape")
                    done = True
                if done:
                    if self.once:
                        break
                    else:
                        time.sleep(60)
        finally:
            log().debug("shutting down")

    def start(self):
        if self.thread:
            return
        t = threading.Thread(
            name="metadata-tip-scraper", target=self.run, daemon=True)
        t.start()
        self.thread = t

    def join(self):
        if self.thread:
            self.thread.join()


class TorrentFileScraper(object):

    DEFAULT_RESET_TIME = 3600

    def __init__(self, api, reset_time=None):
        if reset_time is None:
            reset_time = self.DEFAULT_RESET_TIME

        if api.authkey is None:
            raise ValueError("authkey not configured")
        if api.passkey is None:
            raise ValueError("passkey not configured")

        self.api = api
        self.reset_time = reset_time

        self.thread = None
        self.ts = None
        self.queue = None
        self.last_reset_time = None

    def get_unfilled_ids(self):
        c = self.api.db.cursor().execute(
            "select torrent_entry.id "
            "from torrent_entry "
            "left join file_info on torrent_entry.id = file_info.id "
            "where file_info.id is null "
            "and torrent_entry.deleted = 0 "
            "and torrent_entry.updated_at > ? "
            "order by torrent_entry.updated_at", (self.ts,))
        for r in c:
            yield r

    def update_ts(self):
        r = self.api.db.cursor().execute(
            "select max(updated_at) from torrent_entry").fetchone()
        self.ts = r[0]

    def step(self):
        now = time.time()
        if (self.last_reset_time is None or
                now - self.last_reset_time > self.reset_time):
            self.ts = -1
            self.queue = Queue.PriorityQueue()
            self.last_reset_time = now

        with self.api.db:
            for id, in self.get_unfilled_ids():
                self.queue.put((-id, id))
            self.update_ts()

        try:
            _, id = self.queue.get_nowait()
        except Queue.Empty:
            id = None

        if id is not None:
            te = self.api.getTorrentByIdCached(id)
            _ = te.raw_torrent

        return id

    def run(self):
        try:
            while True:
                try:
                    id = self.step()
                except KeyboardInterrupt:
                    raise
                except:
                    log().exception("during scrape")
                    time.sleep(60)
                else:
                    if id is None:
                        time.sleep(1)
        finally:
            log().debug("shutting down")

    def start(self):
        if self.thread:
            return
        t = threading.Thread(
            name="torrent-file-scraper", target=self.run, daemon=True)
        t.start()
        self.thread = t

    def join(self):
        if self.thread:
            self.thread.join()


class SnatchlistScraper(object):
    """A long-lived daemon that updates the user's snatchlist.

    This daemon calls getUserSnatchlist with no filters and varying offset.

    This daemon will consume as many tokens from `api.api_token_bucket` as
    possible, up to a configured limit. The intent of this is to defer token
    use to `MetadataTipScraper`.

    Attributes:
        api: A `btn.API` instance.
        target_tokens: A number of tokens to leave as leftover in
            `api.api_token_bucket`.
    """

    KEY_OFFSET = "snatchlist_scrape_next_offset"
    KEY_RESULTS = "snatchlist_scrape_last_results"

    BLOCK_SIZE = 10000

    DEFAULT_TARGET_TOKENS = 0
    DEFAULT_NUM_THREADS = 10

    def __init__(self, api, target_tokens=None, num_threads=None, once=False):
        if num_threads is None:
            num_threads = self.DEFAULT_THREADS
        if target_tokens is None:
            target_tokens = self.DEFAULT_TARGET_TOKENS

        if api.key is None:
            raise ValueError("API key not configured")

        self.api = api
        self.target_tokens = target_tokens
        self.num_threads = num_threads
        self.once = once

        self.lock = threading.RLock()
        self.tokens = None
        self.threads = []

    def update_step(self):
        if self.once:
            tokens, _, _ = self.api.api_token_bucket.peek()
            with self.lock:
                if self.tokens is not None and tokens > self.tokens:
                    log().info("Tokens refilled, quitting")
                    return True
                self.tokens = tokens

        target_tokens = self.target_tokens

        success, _, _, _ = self.api.api_token_bucket.try_consume(
            1, leave=target_tokens)
        if not success:
            return True

        with self.api.begin():
            offset = get_int(self.api, self.KEY_OFFSET) or 0
            results = get_int(self.api, self.KEY_RESULTS)
            next_offset = offset + self.BLOCK_SIZE
            if results and next_offset > results:
                next_offset = 0
            set_int(self.api, self.KEY_OFFSET, next_offset)

        log().info(
            "Trying update at offset %s, %s tokens left", offset,
            self.api.api_token_bucket.peek()[0])

        try:
            sr = self.api.getUserSnatchlist(
                results=self.BLOCK_SIZE, offset=offset, consume_token=False)
        except btn.WouldBlock:
            log().info("Out of tokens, quitting")
            return True
        except btn.APIError as e:
            if e.code == e.CODE_CALL_LIMIT_EXCEEDED:
                log().debug("Call limit exceeded, quitting")
                return True
            else:
                raise

        with self.api.begin():
            set_int(self.api, self.KEY_RESULTS, sr.results)

        return False

    def run(self):
        try:
            while True:
                try:
                    done = self.update_step()
                except:
                    log().exception("during update")
                    done = True
                if done:
                    if self.once:
                        break
                    else:
                        time.sleep(60)
        finally:
            log().debug("shutting down")

    def start(self):
        if self.threads:
            return
        for i in range(self.num_threads):
            t = threading.Thread(
                name="snatchlist-scraper-%d" % i, target=self.run, daemon=True)
            t.start()
            self.threads.append(t)

    def join(self):
        for t in self.threads:
            t.join()
