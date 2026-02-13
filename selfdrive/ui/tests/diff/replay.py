#!/usr/bin/env python3
import os
import time
import coverage
import pyray as rl
import argparse
from dataclasses import dataclass
from collections.abc import Callable
from openpilot.selfdrive.ui.tests.diff.diff import DIFF_OUT_DIR

parser = argparse.ArgumentParser()
parser.add_argument('--big', action='store_true', help='Use big UI layout (tizi/tici) instead of mici layout')
args = parser.parse_args()

variant = 'tizi' if args.big else 'mici'

# Set env variables before application imports
if args.big:
  os.environ["BIG"] = "1"
os.environ["RECORD"] = "1"
os.environ["RECORD_OUTPUT"] = os.path.join(DIFF_OUT_DIR, os.environ.get("RECORD_OUTPUT", f"{variant}_ui_replay.mp4"))

from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.system.version import terms_version, training_version

HEADLESS = os.getenv("WINDOWED", "0") == "1"
FPS = 60


@dataclass
class DummyEvent:
  click_pos: tuple[int, int] | None = None
  setup: Callable | None = None


def setup_state():
  params = Params()
  params.put("HasAcceptedTerms", terms_version)
  params.put("CompletedTrainingVersion", training_version)
  params.put("DongleId", "test123456789")
  params.put("UpdaterCurrentDescription", "0.10.1 / test-branch / abc1234 / Nov 30")


def inject_click(x, y):
  from openpilot.system.ui.lib.application import gui_app, MousePos, MouseEvent

  events = [
    MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=time.monotonic()),
    MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=time.monotonic()),
  ]
  with gui_app._mouse._lock:
    gui_app._mouse._events.extend(events)


def handle_event(event: DummyEvent):
  if event.setup:
    event.setup()
  if event.click_pos:
    inject_click(*event.click_pos)


def run_replay(variant):
  from openpilot.selfdrive.ui.ui_state import ui_state  # Import within OpenpilotPrefix context so param values are setup correctly
  from openpilot.system.ui.lib.application import gui_app  # Import here for accurate coverage

  os.makedirs(DIFF_OUT_DIR, exist_ok=True)

  setup_state()

  if not HEADLESS:
    rl.set_config_flags(rl.FLAG_WINDOW_HIDDEN)
  gui_app.init_window("ui diff test", fps=FPS)

  # Dynamically import main layout based on variant
  if variant == "mici":
    from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout as MainLayout
  else:
    from openpilot.selfdrive.ui.layouts.main import MainLayout
  main_layout = MainLayout()
  main_layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

  # Import and build script
  from openpilot.selfdrive.ui.tests.diff.replay_script import build_script, get_frame_fn

  script = build_script(main_layout, big=args.big)
  frame = 0
  script_index = 0

  # Main loop to replay events and render frames
  for should_render in gui_app.render():
    while script_index < len(script) and script[script_index][0] == frame:
      _, event = script[script_index]
      handle_event(event)
      script_index += 1

    # Keep sending cereal messages for persistent states (onroad, alerts)
    fn = get_frame_fn()
    if fn:
      fn()

    ui_state.update()

    if should_render:
      main_layout.render()

    frame += 1

    if script_index >= len(script):
      break

  gui_app.close()

  print(f"Total frames: {frame}")
  print(f"Video saved to: {os.environ['RECORD_OUTPUT']}")


def main():
  print(f"Running '{variant}' replay...")
  with OpenpilotPrefix():
    sources = ["openpilot.system.ui"]
    if variant == "mici":
      sources.append("openpilot.selfdrive.ui.mici")
      omit = ["**/*tizi*", "**/*tici*"]  # exclude files containing "tizi" or "tici"
    else:
      sources.extend(["openpilot.selfdrive.ui.layouts", "openpilot.selfdrive.ui.onroad", "openpilot.selfdrive.ui.widgets"])
      omit = ["**/*mici*"]  # exclude files containing "mici"
    cov = coverage.coverage(source=sources, omit=omit)
    with cov.collect():
      run_replay(variant)
    cov.save()
    cov.report()
    directory = os.path.join(DIFF_OUT_DIR, f"htmlcov-{variant}")
    cov.html_report(directory=directory)
    print(f"HTML report: {directory}/index.html")


if __name__ == "__main__":
  main()
