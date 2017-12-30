import argparse
import logging
import sys

import btn
from btn import scrape as btn_scrape


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="count")
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

    api = btn.API.from_args(parser, args)
    scraper = btn_scrape.Scraper(api)
    scraper.scrape()
