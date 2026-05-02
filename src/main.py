"""
Entry point for the YouTube automation pipeline.

Usage examples:
  python src/main.py                         # Full run (production)
  python src/main.py --dry-run               # Skip YouTube upload
  python src/main.py --topic-override "How to Start SIP in India"
  python src/main.py --cleanup-temp          # Delete temp files after run
"""
import argparse
import sys

from src.pipeline.orchestrator import PipelineOrchestrator
from src.utils.file_manager import cleanup_temp, ensure_dirs
from src.utils.logger import get_logger
from src.config import PATHS

logger = get_logger(__name__, log_dir=PATHS["logs"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finance Decoded — Daily YouTube Video Pipeline",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full pipeline but skip the YouTube upload step.",
    )
    parser.add_argument(
        "--topic-override",
        type=str,
        default=None,
        help=(
            "Override the auto-selected subtopic with a custom title.\n"
            "The category for today's weekday is still used."
        ),
    )
    parser.add_argument(
        "--cleanup-temp",
        action="store_true",
        help="Delete all files in assets/temp/ after the pipeline finishes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Ensure all runtime directories exist
    ensure_dirs()

    logger.info("Starting pipeline | dry_run=%s", args.dry_run)

    try:
        orchestrator = PipelineOrchestrator(dry_run=args.dry_run)

        # Apply topic override if provided
        if args.topic_override:
            original_get_today_topic = orchestrator._step_topic

            def _overridden_step_topic(context):
                original_get_today_topic(context)
                context["topic_data"]["subtopic"] = args.topic_override
                logger.info("Topic overridden → '%s'", args.topic_override)

            orchestrator._step_topic = _overridden_step_topic

        context = orchestrator.run()

    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        return 1
    finally:
        if args.cleanup_temp:
            logger.info("Cleaning up temp directory…")
            cleanup_temp()

    return 0


if __name__ == "__main__":
    sys.exit(main())
