#!/usr/bin/env python3
import difflib
import json
import os
import sys
import subprocess
import webbrowser
import argparse
from dataclasses import dataclass
from typing import Literal
from pathlib import Path
from openpilot.common.basedir import BASEDIR

DIFF_OUT_DIR = Path(BASEDIR) / "selfdrive" / "ui" / "tests" / "diff" / "report"
HTML_TEMPLATE_PATH = Path(__file__).with_name("diff_template.html")

CLIP_PADDING_BEFORE = 0  # extra frames of context to include before each chunk
CLIP_PADDING_AFTER = 0  # extra frames of context to include after each chunk


def extract_framehashes(video_path):
  cmd = ['ffmpeg', '-i', video_path, '-map', '0:v:0', '-vsync', '0', '-f', 'framehash', '-hash', 'md5', '-']
  result = subprocess.run(cmd, capture_output=True, text=True, check=True)
  hashes = []
  for line in result.stdout.splitlines():
    if not line or line.startswith('#'):
      continue
    parts = line.split(',')
    if len(parts) < 4:
      continue
    hashes.append(parts[-1].strip())
  return hashes


def create_diff_video(video1, video2, output_path):
  """Create a diff video using ffmpeg blend filter with difference mode."""
  print("Creating diff video...")
  cmd = ['ffmpeg', '-i', video1, '-i', video2, '-filter_complex', '[0:v]blend=all_mode=difference', '-vsync', '0', '-y', output_path]
  subprocess.run(cmd, capture_output=True, check=True)


def find_differences(video1, video2) -> tuple[list[str], list[str]]:
  """Hash every frame of both videos and return the two hash lists."""
  print(f"Hashing frames from {video1}...")
  hashes1 = extract_framehashes(video1)

  print(f"Hashing frames from {video2}...")
  hashes2 = extract_framehashes(video2)

  print(f"Comparing {len(hashes1)} vs {len(hashes2)} frames...")
  return hashes1, hashes2


@dataclass
class Chunk:
  type: Literal['replace', 'insert', 'delete']
  v1_start: int
  v1_end: int
  v1_count: int
  v2_start: int
  v2_end: int
  v2_count: int


def compute_diff_chunks(hashes1: list[str], hashes2: list[str]) -> list[Chunk]:
  """Use difflib to compute diff chunks from the two hash lists. Returns a list of Chunk objects."""
  matcher = difflib.SequenceMatcher(a=hashes1, b=hashes2, autojunk=False)
  # Collect only the non-equal opcodes
  diff_ops: list[list] = [list(op) for op in matcher.get_opcodes() if op[0] != 'equal']
  # Create chunks with frame ranges and counts for each video
  chunks: list[Chunk] = []
  for tag, i1, i2, j1, j2 in diff_ops:
    chunks.append(Chunk(
      type=tag,
      v1_start=i1, v1_end=i2 - 1, v1_count=i2 - i1,
      v2_start=j1, v2_end=j2 - 1, v2_count=j2 - j1,
    ))
  return chunks


def count_different_frames(chunks: list[Chunk]) -> int:
  """Return a headline 'different frame' count for the summary."""
  return sum(max(c.v1_count, c.v2_count) for c in chunks)


def get_video_fps(video_path: str) -> float:
  """Return fps for a video file."""
  cmd = [
    'ffprobe', '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=r_frame_rate',
    '-of', 'json', str(video_path),
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=True)
  info = json.loads(result.stdout)['streams'][0]
  num, den = info['r_frame_rate'].split('/')
  return int(num) / int(den)


def extract_clip(video_path: str, start_frame: int, end_frame: int, output_path: str, fps: float) -> int:
  """Extract [start_frame, end_frame] plus padding before/after into *output_path*.
  Returns the actual number of frames written."""
  padded_start = max(0, start_frame - CLIP_PADDING_BEFORE)
  padding_before = start_frame - padded_start
  total_frames = (end_frame - start_frame + 1) + padding_before + CLIP_PADDING_AFTER
  start_time = padded_start / fps
  cmd = [
    'ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(video_path),
    '-ss', f"{start_time:.6f}", '-frames:v', str(total_frames), '-vsync', '0', '-y', str(output_path)
  ]
  subprocess.run(cmd, capture_output=True, check=True)
  return total_frames


def generate_thumbnail(video_path: str, frame: int, out_path: str, fps: float) -> None:
  """Create a single-frame PNG thumbnail at the given frame index."""
  t = frame / fps
  cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(video_path), '-ss', f"{t:.6f}", '-frames:v', '1', '-y', str(out_path)]
  subprocess.run(cmd, capture_output=True, check=True)


def extract_chunk_clips(
  video1: str,
  video2: str,
  diff_video: str,
  chunks: list[Chunk],
  fps: float,
  output_dir: Path,
) -> list[dict]:
  """For each diff chunk extract clips from video1, video2, and a diff/highlight video."""
  clip_sets: list[dict] = []
  folder_name = f"{Path(diff_video).stem}-chunks"
  chunk_dir = output_dir / folder_name
  os.makedirs(chunk_dir, exist_ok=True)
  n = len(chunks)

  for i, chunk in enumerate(chunks):
    chunk_type = chunk.type
    v1_start, v1_end, v1_count = chunk.v1_start, chunk.v1_end, chunk.v1_count
    v2_start, v2_end, v2_count = chunk.v2_start, chunk.v2_end, chunk.v2_count
    clips: dict[str, str | None] = {'video1': None, 'video2': None, 'diff': None}

    def _rel(p: Path) -> str:
      return os.path.join(folder_name, p.name)

    # --- video1 clip ---
    v1_clip = chunk_dir / f"{i:03d}_video1.mp4"
    if chunk_type != 'insert':
      print(f"  Chunk {i + 1}/{n} (v1/{chunk_type}) frames {v1_start}-{v1_end}")
      extract_clip(video1, v1_start, v1_end, str(v1_clip), fps)
      clips['video1'] = _rel(v1_clip)

    # --- video2 clip ---
    v2_clip = chunk_dir / f"{i:03d}_video2.mp4"
    if chunk_type != 'delete':
      print(f"  Chunk {i + 1}/{n} (v2/{chunk_type}) frames {v2_start}-{v2_end}")
      extract_clip(video2, v2_start, v2_end, str(v2_clip), fps)
      clips['video2'] = _rel(v2_clip)

    # --- diff/highlight clip ---
    diff_clip = chunk_dir / f"{i:03d}_diff.mp4"
    if chunk_type == 'replace':
      # Pixel-difference blend of the two clips (stops at the shorter one).
      cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(v1_clip), '-i', str(v2_clip),
        '-filter_complex', 'blend=all_mode=difference',
        '-vsync', '0', '-y', str(diff_clip),
      ]
      subprocess.run(cmd, capture_output=True, check=True)
      clips['diff'] = _rel(diff_clip)

    # --- thumbnail (middle frame of the diff content inside the clip) ---
    padding_used = min((v1_start if chunk_type != 'insert' else v2_start), CLIP_PADDING_BEFORE)
    content_count = v1_count if chunk_type != 'insert' else v2_count
    thumb_frame_in_clip = padding_used + content_count // 2
    thumb_path = chunk_dir / f"{i:03d}_thumb.png"
    thumb_source = diff_clip if chunk_type == 'replace' else (v1_clip if chunk_type == 'delete' else v2_clip)
    print(f"  Chunk {i + 1}/{n} (thumb) clip-frame {thumb_frame_in_clip}")
    generate_thumbnail(str(thumb_source), thumb_frame_in_clip, str(thumb_path), fps)

    # Headline frame numbers for the report (expressed in video1 coordinates where
    # possible, video2 for pure inserts).
    display_start = v1_start if chunk_type != 'insert' else v2_start
    display_end   = v1_end   if chunk_type != 'insert' else v2_end

    clip_sets.append({
      'type': chunk_type,
      'start_frame': display_start,
      'end_frame': display_end,
      'duration': max(v1_count, v2_count),
      'v1_count': v1_count,
      'v2_count': v2_count,
      'clips': clips,
      'thumb': _rel(thumb_path),
    })

  return clip_sets


def generate_html_report(
  videos: tuple[str, str],
  basedir: str,
  diff_frame_count: int,
  frame_counts: tuple[int, int],
  diff_video_name: str,
  clip_sets: list[dict] | None = None,
) -> str:
  total_frames = max(frame_counts)
  frame_delta = frame_counts[1] - frame_counts[0]

  result_text = (
    f"✅ Videos are identical! ({total_frames} frames)"
    if diff_frame_count == 0
    else f"❌ Found {diff_frame_count} different frames out of {total_frames} total ({diff_frame_count / total_frames * 100:.1f}%)."
    + (f" Video {'2' if frame_delta > 0 else '1'} is longer by {abs(frame_delta)} frames." if frame_delta != 0 else "")
  )

  # Pre-join basedir into clip paths and thumb so the template needs no path logic
  processed_sets = [
    {
      **cs,
      'clips': {k: (os.path.join(basedir, v) if (basedir and v) else v) for k, v in cs['clips'].items()},
      'thumb': os.path.join(basedir, cs['thumb']) if (basedir and cs.get('thumb')) else cs.get('thumb', ''),
    }
    for cs in (clip_sets or [])
  ]

  # Load HTML template and replace placeholders
  html = HTML_TEMPLATE_PATH.read_text()
  placeholders = {
    "VIDEO1_SRC": os.path.join(basedir, os.path.basename(videos[0])),
    "VIDEO2_SRC": os.path.join(basedir, os.path.basename(videos[1])),
    "DIFF_SRC": os.path.join(basedir, diff_video_name),
    "RESULT_TEXT": result_text,
    "CHUNKS_JSON": json.dumps(processed_sets),
  }
  for key, value in placeholders.items():
    html = html.replace(f"${key}", value)

  return html


def main():
  parser = argparse.ArgumentParser(description='Compare two videos and generate HTML diff report')
  parser.add_argument('video1', help='First video file')
  parser.add_argument('video2', help='Second video file')
  parser.add_argument('output', nargs='?', default='diff.html', help='Output HTML file (default: diff.html)')
  parser.add_argument("--basedir", type=str, help="Base directory for output", default="")
  parser.add_argument('--no-open', action='store_true', help='Do not open HTML report in browser')

  args = parser.parse_args()

  if not args.output.lower().endswith('.html'):
    args.output += '.html'

  os.makedirs(DIFF_OUT_DIR, exist_ok=True)

  print("=" * 60)
  print("VIDEO DIFF - HTML REPORT")
  print("=" * 60)
  print(f"Video 1: {args.video1}")
  print(f"Video 2: {args.video2}")
  print(f"Output: {args.output}")
  print()

  # Create diff video with name derived from output HTML
  diff_video_name = Path(args.output).stem + '.mp4'
  diff_video_path = str(DIFF_OUT_DIR / diff_video_name)
  create_diff_video(args.video1, args.video2, diff_video_path)

  hashes1, hashes2 = find_differences(args.video1, args.video2)
  frame_counts = (len(hashes1), len(hashes2))

  chunks = compute_diff_chunks(hashes1, hashes2)
  diff_frame_count = count_different_frames(chunks)

  clip_sets = []
  if chunks:
    print(f"\nExtracting {len(chunks)} different section(s)...")
    fps = get_video_fps(args.video1)
    clip_sets = extract_chunk_clips(args.video1, args.video2, diff_video_path, chunks, fps, DIFF_OUT_DIR)

  print()
  print("Generating HTML report...")
  html = generate_html_report((args.video1, args.video2), args.basedir, diff_frame_count, frame_counts, diff_video_name, clip_sets)

  with open(DIFF_OUT_DIR / args.output, 'w') as f:
    f.write(html)

  # Open in browser by default
  if not args.no_open:
    print(f"Opening {args.output} in browser...")
    webbrowser.open(f'file://{os.path.abspath(DIFF_OUT_DIR / args.output)}')

  return 0 if diff_frame_count == 0 else 1


if __name__ == "__main__":
  sys.exit(main())
