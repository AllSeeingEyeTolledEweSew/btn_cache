import argparse
import logging
import sys

import btn
from btn import opportunistic_update as btn_opportunistic_update


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument("--cache_path", "-c")
    parser.add_argument("--target_tokens", "-t", type=int, default=0)
    parser.add_argument("--num_threads", "-n", type=int, default=10)

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
    updater = btn_opportunistic_update.OpportunisticUpdater(
        api, target_tokens=args.target_tokens, num_threads=args.num_threads)
    updater.start()
    updater.join()
