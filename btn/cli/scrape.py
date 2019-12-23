# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.

import argparse
import logging
import os
import signal
import sys
import threading
import time

import btn
from btn import scrape as btn_scrape


def log():
    return logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument("--metadata", action="store_true")
    parser.add_argument("--metadata_tip", action="store_true")
    parser.add_argument("--torrent_files", action="store_true")
    parser.add_argument("--snatchlist", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--target_tokens", "-t", type=int,
        default=btn_scrape.MetadataScraper.DEFAULT_TARGET_TOKENS)
    parser.add_argument(
        "--metadata_num_threads", "-n", type=int,
        default=btn_scrape.MetadataScraper.DEFAULT_NUM_THREADS)
    parser.add_argument(
        "--snatchlist_num_threads", "-s", type=int,
        default=btn_scrape.SnatchlistScraper.DEFAULT_NUM_THREADS)
    parser.add_argument("--parent", type=int)
    btn.add_arguments(parser, create_group=True)

    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        stream=sys.stdout, level=level,
        format="%(asctime)s %(levelname)s %(threadName)s "
        "%(filename)s:%(lineno)d %(message)s")

    if args.all:
        args.metadata = True
        args.metadata_tip = True
        args.torrent_files = True
        args.snatchlist = True

    api = btn.API.from_args(parser, args)

    scrapers = []
    if args.metadata:
        scrapers.append(
            btn_scrape.MetadataScraper(
                api, once=args.once, target_tokens=args.target_tokens,
                num_threads=args.metadata_num_threads))
    if args.metadata_tip:
        scrapers.append(
            btn_scrape.MetadataTipScraper(api, once=args.once))
    if args.torrent_files:
        if args.once:
            log().fatal("--torrent_files --once isn't implemented")
        scrapers.append(btn_scrape.TorrentFileScraper(api))
    if args.snatchlist:
        scrapers.append(btn_scrape.SnatchlistScraper(
            api, once=args.once, target_tokens=args.target_tokens,
            num_threads=args.snatchlist_num_threads))

    if not scrapers:
        log().fatal("Nothing to do.")

    if args.parent:

        def check_parent(parent):
            while True:
                if os.getppid() != parent:
                    log().fatal("Parent appears to have died, exiting.")
                    os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(60)

        t = threading.Thread(
            name="parent-checker", target=check_parent, args=(args.parent,))
        t.start()

    for scraper in scrapers:
        scraper.start()
    for scraper in scrapers:
        scraper.join()
