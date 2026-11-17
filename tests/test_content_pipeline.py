import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import TOPIC_SCHEDULE
from src.services import script_service, tts_service, video_service
from src.services.storyboard_service import render_card
from src.pipeline.orchestrator import PipelineOrchestrator


def budget_script():
    return script_service.generate_script({'category': 'Budgeting', 'subtopic': 'Zero-Based Budgeting Explained Simply'})


class ContentTests(unittest.TestCase):
    def test_every_scheduled_topic_has_distinct_content_and_readable_visuals(self):
        hashes = set()
        with tempfile.TemporaryDirectory() as folder:
            for entry in TOPIC_SCHEDULE.values():
                for topic in entry['subtopics']:
                    script = script_service.generate_script({'category': entry['category'], 'subtopic': topic})
                    self.assertNotIn(script['content_hash'], hashes)
                    hashes.add(script['content_hash'])
                    self.assertEqual(script['full_text'], '\n\n'.join(script['sections'].values()))
                    for scene in script['scenes']:
                        render_card(scene, Path(folder) / 'card.png')
        self.assertEqual(len(hashes), 28)

    def test_currency_and_market_survive_generation(self):
        script = budget_script()
        self.assertIn('50,000 rupee', script['full_text'])
        self.assertNotIn('dollars', script['full_text'])
        self.assertNotIn('made me sad', script['full_text'])
        self.assertNotIn('43 percent', script['full_text'])

    def test_custom_title_cannot_relabel_unrelated_content(self):
        with self.assertRaisesRegex(ValueError, 'No storyboard'):
            script_service.generate_script({'category': 'Budgeting', 'subtopic': 'Unknown idea'})

    def test_year_in_title_does_not_invent_new_rates(self):
        script = script_service.generate_script({'category': 'Tax Planning', 'subtopic': 'Crypto Tax Rules in India 2027'})
        self.assertIn('transaction', script['full_text'])
        self.assertTrue(script['sources'])

    def test_caption_chunking_preserves_every_word(self):
        text = budget_script()['full_text']
        chunks = tts_service._split_by_char_limit(text, max_chars=160)
        self.assertEqual(' '.join(chunks), ' '.join(text.split()))
        self.assertTrue(all(len(chunk) <= 160 for chunk in chunks))

    def test_metadata_uses_measured_chapters(self):
        script = budget_script()
        script['scene_timings'] = [{'heading': 'Actual example', 'start': 0}, {'heading': 'Adjustment', 'start': 37.9}]
        context = {'script_data': script}
        PipelineOrchestrator(dry_run=True)._step_metadata(context)
        description = context['upload_metadata']['description']
        self.assertIn('00:37 Adjustment', description)
        self.assertNotIn('07:00', description)

    def test_dry_run_does_not_advance_published_history(self):
        with patch.object(PipelineOrchestrator, '_update_history') as update:
            PipelineOrchestrator(dry_run=True)._step_history_update({'video_id': 'DRY_RUN'})
            update.assert_not_called()

    def test_render_uses_measured_audio_and_burns_captions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = budget_script()
            # Short synthetic phrases exercise production timing/rendering without API credentials.
            script['scenes'] = [{'heading': 'Budget example', 'narration': 'Fifty thousand rupees.', 'lines': ['Income  Rs 50,000']},
                                {'heading': 'Allocate', 'narration': 'Assign each rupee.', 'lines': ['Allocated  Rs 50,000']}]
            script['full_text'] = '\n\n'.join(scene['narration'] for scene in script['scenes'])
            def synthesise(text, path, model, style=None):
                seconds = 1.25 if 'Fifty' in text else 2.0
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                f'sine=frequency=440:duration={seconds}', '-c:a', 'libmp3lame', path], check=True)
            with patch.object(tts_service, '_run_synthesis', side_effect=synthesise):
                audio = tts_service.generate_tts(script['full_text'], str(root / 'audio.mp3'), scenes=script['scenes'])
            self.assertGreater(audio['duration_seconds'], 3)
            self.assertLess(audio['duration_seconds'], 4)
            script['scene_timings'] = audio['scene_timings']
            cues = video_service._parse_srt(audio['subtitle_path'])
            self.assertAlmostEqual(cues[-1][1], audio['duration_seconds'], places=2)
            self.assertAlmostEqual(script['scene_timings'][1]['start'], cues[1][0], places=2)
            output = video_service.assemble_video(audio['audio_path'], [], script, str(root / 'result.mp4'), audio['subtitle_path'])
            self.assertGreater(Path(output).stat().st_size, 1000)
            self.assertAlmostEqual(video_service._probe_duration(output), audio['duration_seconds'], delta=0.25)
            # Keep a frame for local visual verification when explicitly requested.
            import os
            if os.getenv('PREVIEW_FRAME'):
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '0.5', '-i', output,
                                '-frames:v', '1', os.environ['PREVIEW_FRAME']], check=True)


if __name__ == '__main__':
    unittest.main()
