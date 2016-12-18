import feedparser
import logging
import os
import sys
from urllib import parse as urllib_parse

import btn


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

    @property
    def last_scraped_path(self):
        return os.path.join(self.api.cache_path, "last_scraped")

    def get_last_scraped(self):
        if os.path.exists(self.last_scraped_path):
            with open(self.last_scraped_path) as f:
                s = f.read()
            try:
                return int(s)
            except ValueError:
                return None
        else:
            return None

    def set_last_scraped(self, last_scraped):
        with open(self.last_scraped_path, mode="w") as f:
            f.write(str(last_scraped))

    def getTorrents(self, offset):
        sr = self.api.getTorrents(results=2**31, offset=offset)
        ids = [te.id for te in sr.torrents]
        is_end = offset + len(ids) >= sr.results

        if ids:
            with self.api.db:
                if offset == 0:
                    self.api.db.execute(
                        "delete from torrent_entry where id > ?",
                        (ids[0],))
                if is_end:
                    self.api.db.execute(
                        "delete from torrent_entry where id < ?",
                        (ids[-1],))
                self.api.db.execute(
                    "create temp table ids (id integer not null primary key)")
                self.api.db.executemany(
                    "insert into temp.ids (id) values (?)",
                    [(id,) for id in ids])
                self.api.db.execute(
                    "delete from torrent_entry where id < ? and id > ? and "
                    "id not in (select id from temp.ids)",
                    (ids[0], ids[-1]))
                self.api.db.execute("drop table temp.ids")

        return ids, is_end

    def scrape(self):
        offset = 0
        oldest_scraped_in_run = None

        feed_ids = self.get_feed_ids()
        c = self.api.db.execute(
            "select id from torrent_entry order by id desc limit ?",
            (len(feed_ids),))
        db_ids = [row["id"] for row in c]

        if feed_ids == db_ids and feed_ids[0] == self.get_last_scraped():
            log().debug("Feed has no changes.")
            return

        if feed_ids[1:] == db_ids[:len(feed_ids) - 1]:
            log().debug("Only one torrent added.")
            if self.api.getTorrentById(feed_ids[0]):
                self.set_last_scraped(feed_ids[0])
            return

        newest = 0
        while True:
            log().debug("Scraping metadata at offset %s", offset)
            
            ids, is_end = self.getTorrents(offset)

            if ids and ids[0] > newest:
                newest = ids[0]

            if oldest_scraped_in_run is None or (
                    ids and ids[0] >= oldest_scraped_in_run):
                if is_end:
                    log().debug("We reached the oldest torrent entry.")
                    break
                if ids[-1] <= (self.get_last_scraped() or 0):
                    log().debug("Caught up.")
                    break
                if oldest_scraped_in_run is None or (
                        ids[-1] < oldest_scraped_in_run):
                    oldest_scraped_in_run = ids[-1]
                offset += len(ids) - 1
            else:
                log().debug("Missed page overlap, backing off.")
                offset -= len(ids) // 2
                if offset <= 0:
                    offset = 0
                    oldest_scraped_in_run = None

        # torrent entry index seems to be updated asynchronously from the feed.
        if newest < feed_ids[0]:
            log().debug(
                "The first page of results was missing some entries from the "
                "feed. Caching them individually.")
            for id in range(newest + 1, feed_ids[0] + 1):
                if self.api.getTorrentById(id):
                    newest = id

        if newest > self.get_last_scraped():
            self.set_last_scraped(newest)

        return newest


def main():
    logging.basicConfig(
        stream=sys.stdout, level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(threadName)s "
        "%(filename)s:%(lineno)d %(message)s")

    api = btn.API()
    scraper = Scraper(api)
    scraper.scrape()
