import argparse
import logging
import sys

import schedule
import time

from dotenv import load_dotenv

from src.agent import process_meetings
from src.services.doc_mapper import add_mapping

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_once():
    logger.info("Checking for recent meetings...")
    results = process_meetings()
    if results:
        for r in results:
            logger.info(
                "Processed: %s -> %s (%d action items, %d decisions)",
                r["meeting"], r["doc_url"],
                r["action_items_count"], r["decisions_count"],
            )
    else:
        logger.info("No new meetings to process.")
    return results


def run_scheduler(interval_minutes: int):
    logger.info("Starting scheduler, polling every %d minutes", interval_minutes)
    schedule.every(interval_minutes).minutes.do(run_once)
    run_once()
    while True:
        schedule.run_pending()
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description="Meeting Notes Agent")
    parser.add_argument(
        "--mode",
        choices=["once", "poll"],
        default="once",
        help="Run once or poll on a schedule (default: once)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Polling interval in minutes (default: 15)",
    )
    parser.add_argument(
        "--map",
        nargs=2,
        metavar=("MEETING_TITLE", "DOC_ID"),
        help="Add a meeting-to-document mapping",
    )
    args = parser.parse_args()

    if args.map:
        add_mapping(args.map[0], args.map[1])
        logger.info("Mapping added: '%s' -> '%s'", args.map[0], args.map[1])
        return

    if args.mode == "poll":
        run_scheduler(args.interval)
    else:
        results = run_once()
        sys.exit(0 if results else 1)


if __name__ == "__main__":
    main()
