"""Render legible teaching cards against measured narration scene boundaries."""
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont

from src.config import CHANNEL_BRANDING, PATHS, VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS


def render_card(scene, output_path):
    image = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), '#101c2c')
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype('DejaVuSans-Bold.ttf', 62)
    text_font = ImageFont.truetype('DejaVuSans.ttf', 43)
    small_font = ImageFont.truetype('DejaVuSans.ttf', 29)
    draw.text((110, 65), CHANNEL_BRANDING, font=small_font, fill='#9bb0c7')
    draw.multiline_text((110, 145), '\n'.join(textwrap.wrap(scene['heading'], 42)),
                        font=title_font, fill='#ffffff', spacing=12)
    y = 365
    for line in scene['lines']:
        if draw.textbbox((0, 0), line, font=text_font)[2] > VIDEO_WIDTH - 240:
            raise ValueError(f'Visual line exceeds the frame: {line}')
        draw.rounded_rectangle((100, y - 15, VIDEO_WIDTH - 100, y + 65),
                               radius=12, fill='#20354c')
        draw.text((130, y), line, font=text_font, fill='#def2ee')
        y += 105
    # Lower part of frame is reserved for burned-in subtitles.
    image.save(output_path)


def assemble_storyboard(audio_path, script_data, output_path, subtitle_path):
    from src.services.video_service import _probe_duration, _run_ffmpeg

    scenes = script_data['scenes']
    timings = script_data.get('scene_timings', [])
    if len(timings) != len(scenes) or not subtitle_path or not Path(subtitle_path).is_file():
        raise ValueError('Measured scene timings and subtitles are required for storyboard rendering.')
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = out.parent / (out.stem + '_frames')
    frames.mkdir(exist_ok=True)
    concat_lines = []
    cursor = 0.0
    for index, (scene, timing) in enumerate(zip(scenes, timings)):
        duration = timing['end'] - timing['start']
        if duration <= 0 or abs(timing['start'] - cursor) > 0.1:
            raise ValueError('Scene intervals must be positive and contiguous.')
        cursor = timing['end']
        frame = frames / f'{index:02d}.png'
        render_card(scene, frame)
        # ffconcat quoting is distinct from shell quoting.
        quoted = str(frame).replace("'", "'\\''")
        concat_lines.extend([f"file '{quoted}'", f'duration {duration:.6f}'])
    concat_lines.append(concat_lines[-2])
    concat_path = frames / 'frames.txt'
    concat_path.write_text('\n'.join(concat_lines) + '\n', encoding='utf-8')
    duration = _probe_duration(audio_path)
    if abs(cursor - duration) > 0.1:
        raise ValueError('Scene timing total does not match the final narration.')
    subtitle = str(Path(subtitle_path).resolve())
    subtitle = subtitle.replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
    filters = (
        f"subtitles=filename='{subtitle}':force_style='FontName=DejaVu Sans,"
        "FontSize=18,MarginV=16,MarginL=25,MarginR=25,Outline=1,Shadow=0',"
        f'fps={VIDEO_FPS},format=yuv420p'
    )
    args = ['-f', 'concat', '-safe', '0', '-i', str(concat_path), '-i', audio_path]
    music = PATHS['bgmusic']
    if music.is_file():
        args += ['-stream_loop', '-1', '-i', str(music), '-filter_complex',
                 '[2:a]volume=0.06[bed];[1:a][bed]amix=inputs=2:duration=first:normalize=0[a]',
                 '-map', '0:v', '-map', '[a]']
    else:
        args += ['-map', '0:v', '-map', '1:a']
    args += ['-vf', filters, '-t', str(duration), '-c:v', 'libx264', '-preset', 'medium',
             '-crf', '20', '-threads', '2', '-c:a', 'aac', '-b:a', '192k',
             '-movflags', '+faststart', str(out)]
    _run_ffmpeg(args, step='storyboard_export')
    if abs(_probe_duration(str(out)) - duration) > 0.25:
        raise RuntimeError('Rendered video duration does not match the narration.')
    return str(out)
