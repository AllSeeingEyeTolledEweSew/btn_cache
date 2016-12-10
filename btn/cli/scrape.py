import feedparser
import logging
import os
import sys
from urllib import parse as urllib_parse

import btn


def log():
    return logging.getLogger(__name__)


class Scraper(object):

    LIMIT = 1000

    def __init__(self, api):
        self.api = api

    def get_last_id_from_feed(self):
        user = self.api.userInfo()
        resp = self.api.get(
            "/feeds.php", feed="torrents_all", user=user.id,
            auth=self.api.auth, passkey=self.api.passkey,
            authkey=self.api.authkey)
        feed = feedparser.parse(resp.text)
        link = feed.entries[0].link
        return int(urllib_parse.parse_qs(
            urllib_parse.urlparse(link).query)["id"][0])

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

    def scrape(self):
        offset = 0
        oldest_scraped_in_run = None

        init_last_id = self.get_last_id_from_feed()
        if init_last_id == self.get_last_scraped():
            log().debug("Feed has not updated.")
            return

        newest = 0
        while True:
            log().debug("Scraping metadata at offset %s", offset)
            limit = self.LIMIT
            
            sr = self.api.getTorrents(results=limit, offset=offset)
            ids = [te.id for te in sr.torrents]

            if ids:
                with self.api.db:
                    if offset == 0:
                        self.api.db.execute(
                            "delete from torrent_entry where id > ?",
                            (ids[0],))
                    if len(ids) < limit:
                        self.api.db.execute(
                            "delete from torrent_entry where id < ?",
                            (ids[-1],))
                    for start in range(0, len(ids), 500):
                        s = ids[start:start + 500]
                        self.api.db.execute(
                            "delete from torrent_entry where id < ? and "
                            "id > ? and id not in (%s)" %
                            ",".join(["?"] * len(s)),
                            tuple([s[0], s[-1]] + s))

            if ids and ids[0] > newest:
                newest = ids[0]

            if oldest_scraped_in_run is None or (
                    ids and ids[0] >= oldest_scraped_in_run):
                if len(ids) < limit:
                    log().debug("We reached the earliest torrent entry.")
                    break
                if ids[-1] <= (self.get_last_scraped() or 0):
                    log().debug("We reached where we were before.")
                    break
                if oldest_scraped_in_run is None or (
                        ids[-1] < oldest_scraped_in_run):
                    oldest_scraped_in_run = ids[-1]
                offset += len(ids) - 1
            else:
                log().debug("Missed overlap, backing off.")
                offset -= limit // 2
                if offset <= 0:
                    offset = 0
                    oldest_scraped_in_run = None

        # torrent entry index seems to be updated asynchronously from the feed.
        if newest < init_last_id:
            for id in range(newest + 1, init_last_id + 1):
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
