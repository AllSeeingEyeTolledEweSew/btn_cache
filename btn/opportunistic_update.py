import logging
import threading

import btn
from btn import scrape as btn_scrape


def log():
    return logging.getLogger(__name__)


class OpportunisticUpdater(object):

    KEY_OFFSET = "opportunistic_update_next_offset"
    KEY_RESULTS = "opportunistic_update_last_results"

    BLOCK_SIZE = 1000

    DEFAULT_NUM_THREADS = 10

    def __init__(self, api, target_tokens=None, num_threads=None):
        if num_threads is None:
            num_threads = self.DEFAULT_THREADS

        self.api = api
        self.target_tokens = target_tokens or 0
        self.num_threads = num_threads

        self.lock = threading.RLock()
        self.tokens = None
        self.done = False
        self.threads = []

    def get_int(self, key):
        with self.api.db:
            # Workaround for savepoint-as-immediate
            self.api.db.cursor().execute(
                "insert or ignore into global (name, value) values (?, ?)",
                (key, None))
            try:
                return int(self.api.get_global(key))
            except (ValueError, TypeError):
                return None

    def set_int(self, key, value):
        if value is None:
            self.api.delete_global(key)
        else:
            self.api.set_global(key, str(value))

    def update_step(self):
        with self.api.db:
            offset = self.get_int(self.KEY_OFFSET) or 0
            results = self.get_int(self.KEY_RESULTS)
            next_offset = offset + self.BLOCK_SIZE - 1
            if results and next_offset > results:
                next_offset = 0
            self.set_int(self.KEY_OFFSET, next_offset)

        tokens, _ = self.api.api_token_bucket.peek()
        with self.lock:
            if self.tokens is not None and tokens > self.tokens:
                log().info("Tokens refilled, quitting")
                self.done = True
                return
            self.tokens = tokens

        log().info(
            "Trying update at offset %s, %s tokens left", offset,
            self.api.api_token_bucket.peek()[0])

        try:
            sr = self.api.getTorrents(
                results=2**31, offset=offset,
                leave_tokens=self.target_tokens, block_on_token=False)
        except btn.WouldBlock:
            log().info("Out of tokens, quitting")
            with self.lock:
                self.done = True
            return
        except btn.APIError as e:
            if e.code == e.CODE_CALL_LIMIT_EXCEEDED:
                log().debug("Call limit exceeded, quitting")
                with self.lock:
                    self.done = True
                return
            raise

        with self.api.db:
            self.set_int(self.KEY_RESULTS, sr.results)

            btn_scrape.apply_contiguous_results_locked(self.api, offset, sr)

    def run(self):
        try:
            while True:
                with self.lock:
                    if self.done:
                        break
                self.update_step()
        except:
            log().exception("fatal error")
        finally:
            log().debug("shutting down")

    def start(self):
        if self.threads:
            return
        for i in range(self.num_threads):
            t = threading.Thread(target=self.run)
            t.start()
            self.threads.append(t)

    def join(self):
        for t in self.threads:
            t.join()
