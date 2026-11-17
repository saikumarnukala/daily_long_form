"""Load topic-specific, editable storyboards without changing financial facts."""
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict

from src.config import DATA_DIR, TTS_WORDS_PER_MINUTE

STORYBOARDS_PATH = DATA_DIR / 'storyboards.json'


def load_storyboards() -> Dict[str, Any]:
    return json.loads(STORYBOARDS_PATH.read_text(encoding='utf-8'))


def validate_storyboard(storyboard: Dict[str, Any]) -> None:
    """Check the render contract before spending money on narration."""
    if storyboard.get('market') != 'IN' or storyboard.get('currency') != 'INR':
        raise ValueError('This channel uses India-specific content in INR.')
    if not storyboard.get('title') or len(storyboard['title']) > 100:
        raise ValueError('A YouTube title must contain 1–100 characters.')
    scenes = storyboard.get('scenes', [])
    if len(scenes) < 3:
        raise ValueError('A storyboard needs an opening, demonstration and conclusion.')
    narrations = []
    for scene in scenes:
        if not scene.get('heading') or not scene.get('narration', '').strip():
            raise ValueError('Every scene requires a heading and narration.')
        if not 1 <= len(scene.get('lines', [])) <= 4:
            raise ValueError('Every scene requires one to four readable visual lines.')
        if any(len(line) > 64 for line in scene['lines']):
            raise ValueError('Shorten visual lines to fit a mobile player.')
        narrations.append(scene['narration'].strip())
    if len(set(narrations)) != len(narrations):
        raise ValueError('Repeated narration inside a storyboard.')
    for source in storyboard.get('sources', []):
        if not source.startswith('https://'):
            raise ValueError('Source references must use HTTPS.')


def generate_script(topic_data: Dict[str, Any]) -> Dict[str, Any]:
    subtopic = topic_data['subtopic']
    # Date-bearing titles match the same evergreen lesson in future years.
    key = re.sub(r'\b20\d{2}\b', '{year}', subtopic)
    catalog = load_storyboards()
    if key not in catalog:
        raise ValueError(
            f'No storyboard for {subtopic!r}. Add its specific narration and visuals '
            'to data/storyboards.json before rendering this custom topic.'
        )
    storyboard = catalog[key]
    validate_storyboard(storyboard)
    scenes = storyboard['scenes']
    sections = {str(index): scene['narration'] for index, scene in enumerate(scenes)}
    full_text = '\n\n'.join(sections.values())
    word_count = len(full_text.split())
    return {
        'title': storyboard['title'],
        'thumbnail_text': storyboard.get('thumbnail_text', storyboard['title']),
        'description': (
            storyboard['title'] + '\n\n'
            'Worked examples for Indian viewers. Illustrative amounts are identified '
            'in the narration. Adapt the examples to your circumstances and check '
            'current official rules before making financial or tax decisions.'
        ),
        'tags': ['personal finance India', topic_data['category'], subtopic],
        'sections': sections,
        'scenes': scenes,
        'sources': storyboard.get('sources', []),
        'full_text': full_text,
        'content_hash': hashlib.sha256(full_text.encode('utf-8')).hexdigest(),
        'word_count': word_count,
        'duration_estimate_seconds': word_count / TTS_WORDS_PER_MINUTE * 60,
    }
