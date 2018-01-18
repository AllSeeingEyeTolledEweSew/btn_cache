import argparse
import logging
import sys

import btn
from btn import scrape as btn_scrape


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--target_tokens", "-t", type=int,
        default=btn_scrape.MetadataScraper.DEFAULT_TARGET_TOKENS)
    parser.add_argument(
        "--num_threads", "-n", type=int,
        default=btn_scrape.MetadataScraper.DEFAULT_NUM_THREADS)
    btn.add_arguments(parser)

    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        stream=sys.stdout, level=level,
        format="%(asctime)s %(levelname)s %(threadName)s "
        "%(filename)s:%(lineno)d %(message)s")

    api = btn.API.from_args(parser, args)
    updater = btn_scrape.MetadataScraper(
        api, target_tokens=args.target_tokens, num_threads=args.num_threads,
        once=args.once)
    updater.start()
    updater.join()
