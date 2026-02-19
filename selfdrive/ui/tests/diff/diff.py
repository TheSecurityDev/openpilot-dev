#!/usr/bin/env python3
import json
import os
import sys
import subprocess
import webbrowser
import argparse
from pathlib import Path
from openpilot.common.basedir import BASEDIR

DIFF_OUT_DIR = Path(BASEDIR) / "selfdrive" / "ui" / "tests" / "diff" / "report"
HTML_TEMPLATE_PATH = Path(__file__).with_name("diff_template.html")


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


def find_differences(video1, video2) -> tuple[list[int], tuple[int, int]]:
  print(f"Hashing frames from {video1}...")
  hashes1 = extract_framehashes(video1)

  print(f"Hashing frames from {video2}...")
  hashes2 = extract_framehashes(video2)

  print(f"Comparing {len(hashes1)} frames...")
  different_frames = []

  for i, (h1, h2) in enumerate(zip(hashes1, hashes2, strict=False)):
    if h1 != h2:
      different_frames.append(i)

  return different_frames, (len(hashes1), len(hashes2))


def compute_chunks(different_frames: list[int], max_same_frames: int = 0) -> list[list[int]]:
  """Group differing frame indices into contiguous chunks.

  By default (max_same_frames=0) any gap of >=1 same frames breaks a chunk
  (existing behavior). If `max_same_frames` > 0, then gaps of up to that
  many identical frames between differing frames will be merged into the
  same chunk.
  """
  if not different_frames:
    return []
  if max_same_frames < 0:
    max_same_frames = 0

  chunks: list[list[int]] = []
  current_chunk = [different_frames[0]]
  for i in range(1, len(different_frames)):
    prev = different_frames[i - 1]
    cur = different_frames[i]
    gap = cur - prev - 1
    # If the number of identical frames between prev and cur is <= tolerance,
    # treat them as contiguous and keep in the same chunk.
    if gap <= max_same_frames:
      current_chunk.append(cur)
    else:
      chunks.append(current_chunk)
      current_chunk = [cur]
  chunks.append(current_chunk)
  return chunks


def get_video_fps(video_path: str) -> float:
  """Return the frame-rate of a video file."""
  cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=r_frame_rate', '-of', 'csv=p=0', str(video_path)]
  result = subprocess.run(cmd, capture_output=True, text=True, check=True)
  num, den = result.stdout.strip().split('/')
  return int(num) / int(den)


CLIP_PADDING_BEFORE = 30  # extra frames of context to include before each chunk
CLIP_PADDING_AFTER = 30   # extra frames of context to include after each chunk
MAX_SAME_FRAMES = 0  # allow up to this many identical frames between diffs in a single chunk


def extract_clip(video_path: str, start_frame: int, end_frame: int, output_path: str, fps: float) -> None:
  """Extract [start_frame, end_frame] plus padding before/after into *output_path*."""
  padded_start = max(0, start_frame - CLIP_PADDING_BEFORE)
  total_frames = (end_frame - start_frame + 1) + CLIP_PADDING_BEFORE + CLIP_PADDING_AFTER
  start_time = padded_start / fps
  duration = total_frames / fps
  cmd = ['ffmpeg', '-i', str(video_path), '-ss', str(start_time), '-t', str(duration), '-y', str(output_path)]
  subprocess.run(cmd, capture_output=True, check=True)


def extract_chunk_clips(
  video1: str,
  video2: str,
  diff_video: str,
  chunks: list[list[int]],
  fps: float,
  output_dir: Path,
) -> list[dict]:
  """For each chunk extract a short clip from video1, video2 and the diff video."""
  clip_sets: list[dict] = []
  for i, chunk in enumerate(chunks):
    start_frame, end_frame = chunk[0], chunk[-1]
    clips: dict[str, str] = {}
    # Use a single top-level folder based on the diff video name, e.g. '<diff-stem>-chunks'
    folder_name = f"{Path(diff_video).stem}-chunks"
    chunk_dir = output_dir / folder_name
    os.makedirs(chunk_dir, exist_ok=True)
    for name, src in [('video1', video1), ('video2', video2), ('diff', diff_video)]:
      out_path = chunk_dir / f"{i:03d}_{name}.mp4"
      print(f"  Extracting chunk {i + 1}/{len(chunks)} ({name}) frames {start_frame}–{end_frame} into {folder_name}/{out_path.name}…")
      extract_clip(src, start_frame, end_frame, str(out_path), fps)
      # Store relative path under the report base dir: <diff-stem>-chunks/<file>
      clips[name] = os.path.join(folder_name, out_path.name)
    clip_sets.append({'start_frame': start_frame, 'end_frame': end_frame,
                      'duration': end_frame - start_frame + 1, 'clips': clips})
  return clip_sets


def generate_html_report(
  videos: tuple[str, str],
  basedir: str,
  different_frames: list[int],
  frame_counts: tuple[int, int],
  diff_video_name: str,
  clip_sets: list[dict] | None = None,
) -> str:
  total_frames = max(frame_counts)
  frame_delta = frame_counts[1] - frame_counts[0]
  different_total = len(different_frames) + abs(frame_delta)

  result_text = (
    f"✅ Videos are identical! ({total_frames} frames)"
    if different_total == 0
    else f"❌ Found {different_total} different frames out of {total_frames} total ({different_total / total_frames * 100:.1f}%)."
    + (f" Video {'2' if frame_delta > 0 else '1'} is longer by {abs(frame_delta)} frames." if frame_delta != 0 else "")
  )

  # Pre-join basedir into clip paths so the template needs no path logic
  processed_sets = [
    {**cs, 'clips': {k: os.path.join(basedir, v) if basedir else v for k, v in cs['clips'].items()}}
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

  different_frames, frame_counts = find_differences(args.video1, args.video2)

  if different_frames is None:
    sys.exit(1)

  chunks = compute_chunks(different_frames, MAX_SAME_FRAMES)
  clip_sets: list[dict] = []
  if chunks:
    print(f"\nExtracting {len(chunks)} different section(s)...")
    fps = get_video_fps(args.video1)
    clip_sets = extract_chunk_clips(args.video1, args.video2, diff_video_path, chunks, fps, DIFF_OUT_DIR)

  print()
  print("Generating HTML report...")
  html = generate_html_report((args.video1, args.video2), args.basedir, different_frames, frame_counts, diff_video_name, clip_sets)

  with open(DIFF_OUT_DIR / args.output, 'w') as f:
    f.write(html)

  # Open in browser by default
  if not args.no_open:
    print(f"Opening {args.output} in browser...")
    webbrowser.open(f'file://{os.path.abspath(DIFF_OUT_DIR / args.output)}')

  extra_frames = abs(frame_counts[0] - frame_counts[1])
  return 0 if (len(different_frames) + extra_frames) == 0 else 1


if __name__ == "__main__":
  sys.exit(main())
