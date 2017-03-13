import feedparser
import logging
import os
from urllib import parse as urllib_parse


def log():
    return logging.getLogger(__name__)


class Scraper(object):

    def __init__(self, api):
        self.api = api

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

    def get_int(self, key):
        value = self.api.get_global(key)
        try:
            return int(self.api.get_global(key))
        except (ValueError, TypeError):
            return None

    def set_int(self, key, value):
        if value is None:
            self.api.delete_global(key)
        else:
            self.api.set_global(key, str(value))

    def update_scrape_results_unlocked(self, offset, sr):
        done = False
        ids = [te.id for te in sr.torrents]
        is_end = offset + len(ids) >= sr.results

        if ids:
            changestamp = self.api.get_changestamp()
            c = self.api.db.cursor()
            if is_end:
                c.execute(
                    "update torrent_entry set deleted = 1, updated_at = ? "
                    "where id < ? and not deleted",
                    (changestamp, ids[-1]))
            c.execute(
                "create temp table ids (id integer not null primary key)")
            c.executemany(
                "insert into temp.ids (id) values (?)",
                [(id,) for id in ids])
            c.execute(
                "update torrent_entry set deleted = 1, updated_at = ? "
                "where not deleted and id < ? and id > ? and "
                "id not in (select id from temp.ids)",
                (changestamp, ids[0], ids[-1]))
            c.execute("drop table temp.ids")

        last_scraped = self.get_int("last_scraped")
        oldest = self.get_int("scrape_oldest")
        newest = self.get_int("scrape_newest")

        if newest is None or (ids and ids[0] >= newest):
            newest = ids[0]

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
            self.set_int("last_scraped", newest)
            self.set_int("scrape_offset", None)
            self.set_int("scrape_oldest", None)
            self.set_int("scrape_newest", None)
        else:
            self.set_int("scrape_offset", offset)
            self.set_int("scrape_oldest", oldest)
            self.set_int("scrape_newest", newest)

        return done

    def scrape_step(self):
        with self.api.db:
            offset = self.get_int("scrape_offset")
            last_scraped = self.get_int("last_scraped")
            db_ids = []

            if offset is None:
                log().debug("No current scrape.")
                c = self.api.db.cursor().execute(
                    "select id from torrent_entry where not deleted "
                    "order by id desc")
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

        with self.api.db:
            return self.update_scrape_results_unlocked(offset, sr)

    def scrape(self):
        while True:
            done = self.scrape_step()
            if done:
                break
