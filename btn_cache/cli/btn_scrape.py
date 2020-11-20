# Copyright (c) 2020 AllSeeingEyeTolledEweSew
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

import argparse
import concurrent.futures
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
from typing import Any
from typing import Dict
from typing import List
from typing import Set

import dbver
import requests

from btn_cache import api as api_lib
from btn_cache import daemon as daemon_lib
from btn_cache import ratelimit
from btn_cache import scrape
from btn_cache import site

_LOG = logging.getLogger(__name__)


class Error(Exception):
    pass


class FatalError(Error):
    pass


class ParentChecker(daemon_lib.Daemon):
    def __init__(self, expected_parent_pid: int) -> None:
        self.expected_parent_pid = expected_parent_pid
        self._condition = threading.Condition()
        self._terminated = False

    def terminate(self) -> None:
        with self._condition:
            self._terminated = True
            self._condition.notify_all()

    def run(self):
        while not self._terminated:
            if os.getppid() != self.expected_parent_pid:
                _LOG.fatal("parent appears to have died, exiting")
                raise FatalError()
            with self._condition:
                self._condition.wait_for(lambda: self._terminated, timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument(
        "--auth_file", type=argparse.FileType("r"), required=True
    )
    parser.add_argument("--metadata_db", required=True)
    parser.add_argument("--user_db", required=True)
    parser.add_argument(
        "--disable",
        action="append",
        choices=("metadata", "metadata_tip", "snatchlist"),
    )

    parser.add_argument("--api_max_calls", type=int, default=150)
    parser.add_argument("--api_period", type=int, default=3600)
    parser.add_argument("--web_request_rate", type=float, default=0.2)
    parser.add_argument("--web_request_burst", type=float, default=10)

    parser.add_argument("--snatchlist_period", type=float, default=3600)

    parser.add_argument("--parent", type=int)

    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(asctime)s %(levelname)s %(threadName)s "
        "%(filename)s:%(lineno)d %(message)s",
    )

    session = requests.Session()

    rate_limiter = ratelimit.RateLimiter(
        rate=args.web_request_rate, burst=args.web_request_burst
    )
    api_rate_limiter = ratelimit.APIRateLimiter(
        max_calls=args.api_max_calls, period=args.api_period
    )

    auth = site.UserAuth(**json.load(args.auth_file))

    user_access = site.UserAccess(
        auth=auth, session=session, rate_limiter=rate_limiter
    )
    if auth.api_key is None:
        raise ValueError("api_key is required")
    api = api_lib.RateLimitedAPI(
        auth.api_key, rate_limiter=api_rate_limiter, session=session
    )

    def metadata_factory() -> sqlite3.Connection:
        conn = sqlite3.Connection(args.metadata_db, isolation_level=None)
        cur = conn.cursor()
        cur.execute("pragma busy_timeout = 5000")
        # Metadata updates use temp tables with small data sizes
        cur.execute("pragma temp_store = MEMORY")
        cur.execute("pragma trusted_schema = OFF")
        cur.execute("pragma journal_mode = WAL")
        return conn

    metadata_pool = dbver.null_pool(metadata_factory)

    def user_factory() -> sqlite3.Connection:
        conn = sqlite3.Connection(args.user_db, isolation_level=None)
        cur = conn.cursor()
        cur.execute("pragma busy_timeout = 5000")
        cur.execute("pragma trusted_schema = OFF")
        cur.execute("pragma journal_mode = WAL")
        return conn

    user_pool = dbver.null_pool(user_factory)

    disable: Set[list] = set(args.disable) if args.disable else set()

    daemons: Dict[str, daemon_lib.Daemon] = {}
    if "metadata" not in disable:
        daemons["metadata_scraper"] = scrape.MetadataScraper(
            api=api, metadata_pool=metadata_pool
        )
    if "metadata_tip" not in disable:
        daemons["metadata_tip_scraper"] = scrape.MetadataTipScraper(
            api=api, user_access=user_access, metadata_pool=metadata_pool
        )
    if "snatchlist" not in disable:
        daemons["snatchlist_scraper"] = scrape.SnatchlistScraper(
            api=api, user_pool=user_pool, period=args.snatchlist_period
        )

    if args.parent:
        daemons["parent_checker"] = ParentChecker(args.parent)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    def signal_handler(signum: int, _: Any) -> None:
        _LOG.info("terminating due to signal %d", signum)
        for daemon in daemons.values():
            daemon.terminate()

    try:
        # Set signal handlers within the try-finally, so we'll be sure to unset
        # them if we get a signal while setting them
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        futures: List[concurrent.futures.Future] = []
        for name, daemon in daemons.items():
            executor = concurrent.futures.ThreadPoolExecutor(
                thread_name_prefix=name
            )
            futures.append(executor.submit(daemon.run))

        # Wait for any daemon to die or be terminated
        concurrent.futures.wait(
            futures, return_when=concurrent.futures.FIRST_COMPLETED
        )

        # Ensure all daemons are terminated; all are killed if one dies
        for daemon in daemons.values():
            daemon.terminate()
        # Re-raise any exceptions
        for future in futures:
            future.result()
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
