#!/usr/bin/env python3
import os
import sys
import time
import coverage
import pyray as rl
from dataclasses import dataclass
from collections.abc import Callable
from openpilot.selfdrive.ui.tests.diff.diff import DIFF_OUT_DIR


variant = sys.argv[1] if len(sys.argv) > 1 else 'mici'

# Set env variables before application imports
if variant == 'tizi':
  os.environ["BIG"] = "1"
os.environ["RECORD"] = "1"
os.environ["RECORD_OUTPUT"] = os.path.join(DIFF_OUT_DIR, os.environ.get("RECORD_OUTPUT", f"{variant}_ui_replay.mp4"))

from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.system.version import terms_version, training_version
from openpilot.system.ui.lib.application import gui_app, MousePos, MouseEvent

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
  from openpilot.selfdrive.ui.ui_state import ui_state

  os.makedirs(DIFF_OUT_DIR, exist_ok=True)

  setup_state()

  if not HEADLESS:
    rl.set_config_flags(rl.FLAG_WINDOW_HIDDEN)
  gui_app.init_window("ui diff test", fps=FPS)

  # Import layout class dynamically for coverage
  if variant == "mici":
    from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout as MainLayout
  else:
    from openpilot.selfdrive.ui.layouts.main import MainLayout
  main_layout = MainLayout()
  main_layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

  # Import and build script
  get_frame_fn = None
  if variant == "tizi":
    from openpilot.selfdrive.ui.tests.diff.tizi_script import build_script, get_frame_fn
  else:
    from openpilot.selfdrive.ui.tests.diff.mici_script import build_script

  SCRIPT = build_script(main_layout)

  frame = 0
  script_index = 0

  for should_render in gui_app.render():
    while script_index < len(SCRIPT) and SCRIPT[script_index][0] == frame:
      _, event = SCRIPT[script_index]
      handle_event(event)
      script_index += 1

    if get_frame_fn:
      # Keep sending cereal messages for persistent states (onroad, alerts)
      fn = get_frame_fn()
      if fn:
        fn()

    ui_state.update()

    if should_render:
      main_layout.render()

    frame += 1

    if script_index >= len(SCRIPT):
      break

  gui_app.close()

  print(f"Total frames: {frame}")
  print(f"Video saved to: {os.environ['RECORD_OUTPUT']}")


def main(variant='mici'):
  print(f"Running '{variant}' replay...")
  with OpenpilotPrefix():
    # TODO: Improve coverage sources (e.g. system/ui, etc)
    cov = coverage.coverage(source=['openpilot.selfdrive.ui.mici' if variant == "mici" else 'openpilot.selfdrive.ui.layouts'])
    with cov.collect():
      run_replay(variant)
    cov.save()
    cov.report()
    directory = os.path.join(DIFF_OUT_DIR, f"htmlcov-{variant}")
    cov.html_report(directory=directory)
    print(f"HTML report: {directory}/index.html")


if __name__ == "__main__":
  main(variant)
