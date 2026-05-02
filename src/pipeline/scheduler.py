"""
Local scheduler — runs the pipeline on a daily cron-like schedule.

Use this module when running on a local machine or a self-managed server
instead of GitHub Actions.

Usage:
    python -m src.pipeline.scheduler
    python -m src.pipeline.scheduler --time 07:30
"""
import argparse
import time

import schedule

from src.pipeline.orchestrator import PipelineOrchestrator
from src.utils.file_manager import cleanup_temp, ensure_dirs
from src.utils.logger import get_logger
from src.config import PATHS

logger = get_logger(__name__, log_dir=PATHS["logs"])


def run_pipeline() -> None:
    """Wrapper called by the scheduler on each trigger."""
    logger.info("Scheduler triggered pipeline run.")
    ensure_dirs()
    try:
        orchestrator = PipelineOrchestrator(dry_run=False)
        orchestrator.run()
    except Exception as exc:
        logger.error("Pipeline run failed: %s", exc, exc_info=True)
    finally:
        cleanup_temp()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local daily scheduler for the YouTube pipeline.")
    parser.add_argument(
        "--time",
        default="07:30",
        help="Daily run time in HH:MM (24h) format. Default: 07:30",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Also run the pipeline immediately on startup (before waiting for scheduled time).",
    )
    args = parser.parse_args()

    logger.info("Scheduler configured to run daily at %s", args.time)
    schedule.every().day.at(args.time).do(run_pipeline)

    if args.run_now:
        logger.info("--run-now flag set: running pipeline immediately.")
        run_pipeline()

    logger.info("Scheduler is running. Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
