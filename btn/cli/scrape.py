import argparse
import logging
import sys

import btn
from btn import scrape as btn_scrape


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument("--cache_path", "-c")

    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        stream=sys.stdout, level=level,
        format="%(asctime)s %(levelname)s %(threadName)s "
        "%(filename)s:%(lineno)d %(message)s")

    api = btn.API(cache_path=args.cache_path)
    scraper = btn_scrape.Scraper(api)
    scraper.scrape()
