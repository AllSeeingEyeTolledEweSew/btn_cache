import logging
import threading
import Queue
import time
import urlparse

import better_bencode
import btn
import deluge_client_sync


def log():
    return logging.getLogger(__name__)


class DelugeBulkMeta(object):

    DEFAULT_TARGET = 200
    DEFAULT_METADATA_TIMEOUT = 600
    DEFAULT_NUM_FEEDERS = 5
    DEFAULT_UPDATER_SLEEP = 1
    DEFAULT_UPDATER_TARGET = 10

    NULL_SAVE_PATH = b"/dev/null"

    def __init__(self, api, client, target=None, metadata_timeout=None,
                 num_feeders=None, updater_sleep=None, updater_target=None):
        self.api = api
        self.client = client

        if target is None:
            target = self.DEFAULT_TARGET
        if metadata_timeout is None:
            metadata_timeout = self.DEFAULT_METADATA_TIMEOUT
        if num_feeders is None:
            num_feeders = self.DEFAULT_NUM_FEEDERS
        if updater_sleep is None:
            updater_sleep = self.DEFAULT_UPDATER_SLEEP
        if updater_target is None:
            updater_target = self.DEFAULT_UPDATER_TARGET

        self.target = target
        self.metadata_timeout = metadata_timeout
        self.num_feeders = num_feeders
        self.updater_target = updater_target
        self.updater_sleep = updater_sleep

        self.lock = threading.RLock()
        self.status = {}
        self.cv = threading.Condition(self.lock)
        self.active_count = -1
        self.queue = Queue.PriorityQueue()

    def announce_url(self):
        return list(self.api.announce_urls)[0].encode()

    def unregistered_status(self):
        u = urlparse.urlparse(self.announce_url().decode())
        return u.netloc.encode() + b": Error: Unregistered torrent"

    def unfilled_id_for_hash(self, info_hash):
        r = self.api.db.cursor().execute(
            "select torrent_entry.id from torrent_entry "
            "left join file_info on torrent_entry.id = file_info.id "
            "where file_info.id is null and torrent_entry.info_hash = ?",
            (info_hash.decode().upper(),)).fetchone()
        if not r:
            return
        return r[0]

    def maybe_update_metadata(self, status):
        info_hash = status[b"hash"]
        id = self.unfilled_id_for_hash(info_hash)
        if id is None:
            return
        te = self.api.getTorrentByIdCached(id)
        if not te:
            return
        if any(te.file_info_cached):
            return
        log().info("saving metadata: %s -> %s", info_hash, id)
        md = self.client.call(b"pieceio.get_metadata", info_hash)
        md = better_bencode.loads(md)
        tobj = {b"info": md, b"announce": self.announce_url()}
        te._got_raw_torrent(better_bencode.dumps(tobj))

    def owned_by_us(self, status):
        if status[b"save_path"] != self.NULL_SAVE_PATH:
            return False
        if not any(r.match(t[b"url"].decode()) for t in status[b"trackers"]
                for r in btn.TRACKER_REGEXES):
            return False
        return True

    def should_remove(self, status):
        hash = status[b"hash"]
        if not self.owned_by_us(status):
            return False
        if status[b"tracker_status"] == self.unregistered_status():
            log().info("will delete %s: unregistered", hash)
            return True
        id = self.unfilled_id_for_hash(hash)
        if id is None:
            log().info("will delete %s: done", hash)
            return True
        if status[b"active_time"] > self.metadata_timeout:
            log().info(
                "%s timed out, will get torrent file via http and delete",
                hash)
            te = self.api.getTorrentByIdCached(id)
            _ = te.raw_torrent
            return True
        return False

    def maybe_remove(self, status):
        if not self.should_remove(status):
            return False
        removed = False
        try:
            self.client.call(b"core.remove_torrent", status[b"hash"], True)
            removed = True
        except deluge_client_sync.RPCError as e:
            if e.type not in (b"InvalidTorrentError", b"KeyError"):
                raise
        if removed:
            with self.lock:
                self.status.pop(status[b"hash"], None)
                with self.cv:
                    self.active_count -= 1
                    self.cv.notify()
        return True

    def got_metadata(self, status):
        status[b"has_metadata"] = True
        try:
            self.maybe_update_metadata(status)
            return self.maybe_remove(status)
        except:
            log().exception("in got_metadata")
            raise

    def got_metadata_by_hash(self, info_hash):
        with self.lock:
            status = self.status.get(info_hash)
        if not status:
            return
        self.got_metadata(status)

    def maybe_update(self, status):
        if status[b"has_metadata"]:
            if self.got_metadata(status):
                return True
        if self.owned_by_us(status):
            try:
                if status[b"upload_mode"] != True:
                    self.client.call(
                        b"pieceio.set_upload_mode", status[b"hash"], True)
                if status[b"state"] == b"Paused":
                    self.client.call(
                        b"core.resume_torrent", [status[b"hash"]])
            except deluge_client_sync.RPCError as e:
                if e.type not in (b"InvalidTorrentError", b"KeyError"):
                    raise
        return self.maybe_remove(status)

    def got_tracker_error(self, info_hash, tracker_url, message, times_in_row,
                          status_code, error):
        try:
            if tracker_url.decode() not in self.api.announce_urls:
                return
            with self.lock:
                status = self.status.get(info_hash)
            if not status:
                return
            u = urlparse.urlparse(tracker_url.decode())
            status[b"tracker_status"] = (
                u.netloc.encode() + b": Error: " + message)
            self.maybe_remove(status)
        except:
            log().exception("in got_tracker_error")
            raise

    def add_event_handlers(self):
        self.client.add_event_handler(
            b"MetadataReceivedEvent", self.got_metadata_by_hash)
        self.client.add_event_handler(
            b"TrackerErrorEvent", self.got_tracker_error)

    def update_status(self):
        log().info("full status update from deluge")
        status = self.client.call(
            "core.get_torrents_status", {}, [
                b"save_path", b"trackers", b"has_metadata", b"hash",
                b"upload_mode", b"state", b"tracker_status", b"active_time"])

        with self.lock:
            self.status = status
            with self.cv:
                self.active_count = len(
                    [s for s in status.values() if self.owned_by_us(s)])
                self.cv.notifyAll()
            statuses = list(status.values())
        log().info("cleaning up successful torrents")
        for s in statuses:
            if not s[b"has_metadata"]:
                continue
            self.maybe_update(s)

        log().info("cleaning up unregistered torrents")
        with self.lock:
            statuses = list(self.status.values())
        for s in statuses:
            if s[b"tracker_status"] != self.unregistered_status():
                continue
            self.maybe_update(s)

        log().info("finding at most %s to update", self.updater_target)
        with self.lock:
            statuses = list(self.status.values())
        updated = 0
        for s in sorted(statuses, key=lambda s: -s[b"active_time"]):
            if self.maybe_update(s):
                updated += 1
            if updated >= self.updater_target:
                break

    def updater(self):
        try:
            while True:
                try:
                    self.update_status()
                except:
                    log().exception("during update, will retry")
                time.sleep(self.updater_sleep)
        except:
            log().exception("fatal error")
        finally:
            log().debug("shutting down")

    def add(self, id):
        try:
            te = self.api.getTorrentByIdCached(id)
            if not te:
                return False
            log().info("adding %s", id)
            hash = self.client.call(
                "core.add_torrent_magnet", te.magnet_link(include_as=False),
                {b"download_location": self.NULL_SAVE_PATH})
            added = hash is not None
        except:
            log().exception("while adding %s", id)
            return True
        if added:
            with self.cv:
                self.active_count += 1
        hash = te.info_hash.lower().encode()
        status = {
            b"save_path": self.NULL_SAVE_PATH,
            b"trackers": [{b"url": self.announce_url()}],
            b"has_metadata": False,
            b"hash": hash,
            b"upload_mode": False,
            b"state": b"Paused",
            b"tracker_status": b"OK",
            b"active_time": 0}
        with self.lock:
            self.status[hash] = status
        self.maybe_update(status)
        return False

    def try_feed_one(self):
        try:
            prio, id = self.queue.get(True, 1)
        except Queue.Empty:
            return

        with self.cv:
            while self.active_count < 0 or self.active_count >= self.target:
                self.cv.wait()

        try:
            retry = self.add(id)
        except:
            log().exception("while adding %s", id)
        else:
            if retry:
                self.queue.put((prio, id))

    def feeder(self):
        try:
            while True:
                self.try_feed_one()
        except:
            log().exception("fatal error")
        finally:
            log().debug("shutting down")

    def get_unfilled_ids(self, ts=None):
        if ts is None:
            ts = -1
        c = self.api.db.cursor().execute(
            "select torrent_entry.id, "
            "torrent_entry.seeders + torrent_entry.leechers, "
            "torrent_entry.updated_at "
            "from torrent_entry "
            "left join file_info on torrent_entry.id = file_info.id "
            "where file_info.id is null "
            "and torrent_entry.deleted = 0 "
            "and torrent_entry.updated_at > ? "
            "order by torrent_entry.updated_at", (ts,))
        for r in c:
            yield r

    def scrape(self, ts):
        next_ts = ts or -1
        try:
            for id, peers, id_ts in self.get_unfilled_ids(ts=ts):
                self.queue.put((-peers, id))
                next_ts = max(next_ts, id_ts)
        except:
            log().exception("while scraping")
        return next_ts

    def scraper(self):
        ts = None
        try:
            while True:
                ts = self.scrape(ts)
                time.sleep(1)
        except:
            log().exception("fatal error")
        finally:
            log().debug("shutting down")

    def start(self):
        self.add_event_handlers()
        threading.Thread(name="updater", target=self.updater).start()
        threading.Thread(name="scraper", target=self.scraper).start()
        for i in range(self.num_feeders):
            threading.Thread(name="feeder-%s" % i, target=self.feeder).start()
