import feedparser
import logging
import os
import Queue
import threading
import time
from urllib import parse as urllib_parse

import btn


def log():
    return logging.getLogger(__name__)


def get_int(api, key):
    try:
        return int(api.get_global(key))
    except (ValueError, TypeError):
        return None


def set_int(api, key, value):
    if value is None:
        api.delete_global(key)
    else:
        api.set_global(key, str(value))


def apply_contiguous_results_locked(api, offset, sr, changestamp=None):
    ids = sorted((te.id for te in sr.torrents), key=lambda i: -i)
    is_end = offset + len(ids) >= sr.results

    if ids:
        api.db.cursor().execute(
            "create temp table ids (id integer not null primary key)")
        api.db.cursor().executemany(
            "insert into temp.ids (id) values (?)",
            [(id,) for id in ids])

        torrent_entries_to_delete = set()
        groups_to_check = set()
        if is_end:
            for id, group_id, in api.db.cursor().execute(
                    "select id, group_id from torrent_entry "
                    "where id < ? and not deleted", (ids[-1],)):
                torrent_entries_to_delete.add(id)
                groups_to_check.add(group_id)
        for id, group_id, in api.db.cursor().execute(
                "select id, group_id from torrent_entry "
                "where (not deleted) and id < ? and id > ? and "
                "id not in (select id from temp.ids)",
                (ids[0], ids[-1])):
            torrent_entries_to_delete.add(id)
            groups_to_check.add(group_id)

        api.db.cursor().execute("drop table temp.ids")

        if torrent_entries_to_delete:
            if changestamp is None:
                changestamp = api.get_changestamp()

            api.db.cursor().executemany(
                "update torrent_entry set deleted = 1, updated_at = ? "
                "where id = ?",
                [(changestamp, id) for id in torrent_entries_to_delete])
            btn.Group._maybe_delete(
                api, *list(groups_to_check), changestamp=changestamp)

    return ids, is_end


class MetadataScraper(object):

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
    KEY_OFFSET = "tip_scrape_offset"
    KEY_OLDEST = "tip_scrape_oldest"
    KEY_NEWEST = "tip_scrape_newest"

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
        resp = self.api.get(
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
        ids, is_end = apply_contiguous_results_locked(self.api, offset, sr)

        last_scraped = get_int(self.api, self.KEY_LAST)
        oldest = get_int(self.api, self.KEY_OLDEST)
        newest = get_int(self.api, self.KEY_NEWEST)

        if newest is None or (ids and ids[0] >= newest):
            newest = ids[0]

        done = False
        # Ensure we got a good page overlap.
        if oldest is None or (ids and ids[0] >= oldest):
            if is_end:
                log().info("We reached the oldest torrent entry.")
                done = True
            elif last_scraped is not None and ids[-1] <= last_scraped:
                log().info("Caught up. Current as of %s.", newest)
                done = True
            elif oldest is None or ids[-1] < oldest:
                oldest = ids[-1]
            offset += len(ids) - 1
        else:
            log().info("Missed page overlap, backing off.")
            offset -= len(ids) // 2
            if offset <= 0:
                offset = 0
                oldest = None

        if done:
            set_int(self.api, self.KEY_LAST, newest)
            set_int(self.api, self.KEY_OFFSET, None)
            set_int(self.api, self.KEY_OLDEST, None)
            set_int(self.api, self.KEY_NEWEST, None)
        else:
            set_int(self.api, self.KEY_OFFSET, offset)
            set_int(self.api, self.KEY_OLDEST, oldest)
            set_int(self.api, self.KEY_NEWEST, newest)

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
                    "order by id desc limit 1000")
                db_ids = [id for id, in c]

        if offset is None:
            feed_ids = self.get_feed_ids()
            db_ids = db_ids[:len(feed_ids)]
            if feed_ids == db_ids and feed_ids[0] == last_scraped:
                log().info("Feed has no changes. Latest is %s.", last_scraped)
                return True
            if set(feed_ids) - set(db_ids):
                log().debug(
                    "in feed but not in db: %s", set(feed_ids) - set(db_ids))
            if set(db_ids) - set(feed_ids):
                log().debug(
                    "in db but not in feed: %s", set(db_ids) - set(feed_ids))
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
