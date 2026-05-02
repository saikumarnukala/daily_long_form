"""
Topic selection service.

Determines today's video topic based on the weekday and avoids
repeating the same subtopic within the last MAX_HISTORY_LOOKBACK cycles.
"""
from datetime import datetime
from typing import Any, Dict

from src.config import MAX_HISTORY_LOOKBACK, TOPIC_SCHEDULE
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_today_topic(history: Dict[str, Any]) -> Dict[str, Any]:
    """
    Determine today's topic, category, subtopic, and Pexels keywords.

    Algorithm:
      1. Get today's weekday (0=Mon … 6=Sun).
      2. Load the category and subtopics for that weekday.
      3. Inspect the last MAX_HISTORY_LOOKBACK entries for this category.
      4. Pick the first subtopic *not* recently used.
      5. Fall back to round-robin rotation if all subtopics were used recently.

    Args:
        history: Parsed content of data/history.json.

    Returns:
        dict with keys: weekday, category, subtopic, keywords.
    """
    weekday = datetime.now().weekday()  # 0 = Monday, 6 = Sunday
    schedule_entry = TOPIC_SCHEDULE[weekday]
    category: str = schedule_entry["category"]
    subtopics: list = schedule_entry["subtopics"]
    keywords: list = schedule_entry["keywords"]

    # Collect previously used subtopics for this category (chronological)
    used_subtopics = [
        v["subtopic"]
        for v in history.get("videos", [])
        if v.get("category") == category
    ]
    recent = set(used_subtopics[-MAX_HISTORY_LOOKBACK:])

    # Pick first subtopic not in the recent window
    chosen_subtopic: str = ""
    for st in subtopics:
        if st not in recent:
            chosen_subtopic = st
            break

    if not chosen_subtopic:
        # All subtopics were used recently — rotate by total-use count
        rotation_index = len(used_subtopics) % len(subtopics)
        chosen_subtopic = subtopics[rotation_index]
        logger.warning(
            "All subtopics for '%s' recently used; rotating → index %d ('%s')",
            category,
            rotation_index,
            chosen_subtopic,
        )

    logger.info("Today's topic selected: [%s] %s", category, chosen_subtopic)
    return {
        "weekday": weekday,
        "category": category,
        "subtopic": chosen_subtopic,
        "keywords": keywords,
    }
