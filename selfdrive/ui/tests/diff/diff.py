#!/usr/bin/env python3
import os
import sys
import subprocess
import webbrowser
import argparse
from pathlib import Path
from openpilot.common.basedir import BASEDIR

DIFF_OUT_DIR = Path(BASEDIR) / "selfdrive" / "ui" / "tests" / "diff" / "report"
HTML_TEMPLATE_PATH = Path(__file__).with_name("diff_template.html")


def create_diff_video(video1, video2, output_path):
  """Create a diff video using ffmpeg blend filter with difference mode."""
  print("Creating diff video...")
  cmd = ['ffmpeg', '-i', video1, '-i', video2, '-filter_complex', '[0:v]blend=all_mode=difference', '-vsync', '0', '-y', output_path]
  subprocess.run(cmd, capture_output=True, check=True)


def embed_framehashes(video_path: Path, hashes: list[str]) -> None:
  """Embed frame hashes into MP4 custom metadata via stream copy with temp file."""
  # We can't read/write the file simultaneously, so write to a temp file and then replace the original
  tmp_path = video_path.with_suffix('.tmp.mp4')
  hash_str = "\n".join(hashes)
  print(f"Embedding {len(hashes)} frame hashes into {video_path}...")
  cmd = ['ffmpeg', '-v', 'warning', '-i', video_path, '-c', 'copy', '-movflags', '+use_metadata_tags', '-metadata', f'framehashes={hash_str}', '-y', tmp_path]
  subprocess.run(cmd, check=True)
  os.replace(tmp_path, video_path)


def extract_framehashes(video_path) -> list[str]:
  """Extract pre-computed frame hashes from custom MP4 metadata."""
  cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format_tags=framehashes', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
  result = subprocess.run(cmd, capture_output=True, text=True)
  value = result.stdout.strip()
  if result.returncode != 0 or not value:
    print(f"WARNING: No framehashes metadata found in {video_path}")
    return []
  hashes = value.splitlines()
  print(f"Loaded {len(hashes)} frame hashes from {video_path}")
  return hashes


def find_differences(video1, video2) -> tuple[list[int], tuple[int, int]]:
  hashes1 = extract_framehashes(video1)
  hashes2 = extract_framehashes(video2)

  print(f"Comparing {len(hashes1)} frames...")
  different_frames = []

  for i, (h1, h2) in enumerate(zip(hashes1, hashes2, strict=False)):
    if h1 != h2:
      different_frames.append(i)

  return different_frames, (len(hashes1), len(hashes2))


def generate_html_report(videos: tuple[str, str], basedir: str, different_frames: list[int], frame_counts: tuple[int, int], diff_video_name):
  chunks = []
  if different_frames:
    current_chunk = [different_frames[0]]
    for i in range(1, len(different_frames)):
      if different_frames[i] == different_frames[i - 1] + 1:
        current_chunk.append(different_frames[i])
      else:
        chunks.append(current_chunk)
        current_chunk = [different_frames[i]]
    chunks.append(current_chunk)

  total_frames = max(frame_counts)
  frame_delta = frame_counts[1] - frame_counts[0]
  different_total = len(different_frames) + abs(frame_delta)

  result_text = (
    f"✅ Videos are identical! ({total_frames} frames)"
    if different_total == 0
    else f"❌ Found {different_total} different frames out of {total_frames} total ({different_total / total_frames * 100:.1f}%)."
    + (f" Video {'2' if frame_delta > 0 else '1'} is longer by {abs(frame_delta)} frames." if frame_delta != 0 else "")
  )

  # Load HTML template and replace placeholders
  html = HTML_TEMPLATE_PATH.read_text()
  placeholders = {
    "VIDEO1_SRC": os.path.join(basedir, os.path.basename(videos[0])),
    "VIDEO2_SRC": os.path.join(basedir, os.path.basename(videos[1])),
    "DIFF_SRC": os.path.join(basedir, diff_video_name),
    "RESULT_TEXT": result_text,
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

  print()
  print("Generating HTML report...")
  html = generate_html_report((args.video1, args.video2), args.basedir, different_frames, frame_counts, diff_video_name)

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
