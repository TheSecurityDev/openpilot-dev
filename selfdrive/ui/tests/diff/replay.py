#!/usr/bin/env python3
"""
UI replay & exploration tool for the mici UI.

Modes:
  --mode script     Original hardcoded script replay (default for diff testing)
  --mode explore    Introspection-driven auto-exploration with coverage
  --mode replay     Replay a previously recorded JSON action log
"""
import os
import sys
import time
import argparse
import coverage
import pyray as rl
from dataclasses import dataclass
from openpilot.selfdrive.ui.tests.diff.diff import DIFF_OUT_DIR

os.environ["RECORD"] = "1"
if "RECORD_OUTPUT" not in os.environ:
  os.environ["RECORD_OUTPUT"] = "mici_ui_replay.mp4"

os.environ["RECORD_OUTPUT"] = os.path.join(DIFF_OUT_DIR, os.environ["RECORD_OUTPUT"])

from openpilot.common.params import Params
from openpilot.system.version import terms_version, training_version
from openpilot.system.ui.lib.application import gui_app, MousePos, MouseEvent
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout

FPS = 60
HEADLESS = os.getenv("WINDOWED", "0") != "1"


# ---------------------------------------------------------------------------
# Legacy script-based replay (kept for deterministic diff testing)
# ---------------------------------------------------------------------------


@dataclass
class DummyEvent:
  click: bool = False
  swipe_left: bool = False
  swipe_right: bool = False
  swipe_down: bool = False


SCRIPT = [
  (0, DummyEvent()),
  (FPS * 1, DummyEvent(click=True)),
  (FPS * 2, DummyEvent(click=True)),
  (FPS * 3, DummyEvent()),
]


def setup_state():
  params = Params()
  params.put("HasAcceptedTerms", terms_version)
  params.put("CompletedTrainingVersion", training_version)
  params.put("DongleId", "test123456789")
  params.put("HardwareSerial", "TESTSERIAL001")
  params.put("UpdaterCurrentDescription", "0.10.1 / test-branch / abc1234 / Nov 30")
  params.put_bool("OpenpilotEnabledToggle", True)
  params.put_bool("IsLdwEnabled", True)


def inject_click(coords):
  events = []
  x, y = coords[0]
  events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=time.monotonic()))
  for x, y in coords[1:]:
    events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=False, left_down=True, t=time.monotonic()))
  x, y = coords[-1]
  events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=time.monotonic()))

  with gui_app._mouse._lock:
    gui_app._mouse._events.extend(events)


def handle_event(event: DummyEvent):
  if event.click:
    inject_click([(gui_app.width // 2, gui_app.height // 2)])
  if event.swipe_left:
    inject_click([(gui_app.width * 3 // 4, gui_app.height // 2),
                  (gui_app.width // 4, gui_app.height // 2),
                  (0, gui_app.height // 2)])
  if event.swipe_right:
    inject_click([(gui_app.width // 4, gui_app.height // 2),
                  (gui_app.width * 3 // 4, gui_app.height // 2),
                  (gui_app.width, gui_app.height // 2)])
  if event.swipe_down:
    inject_click([(gui_app.width // 2, gui_app.height // 4),
                  (gui_app.width // 2, gui_app.height * 3 // 4),
                  (gui_app.width // 2, gui_app.height)])


def run_replay():
  setup_state()
  os.makedirs(DIFF_OUT_DIR, exist_ok=True)

  if HEADLESS:
    rl.set_config_flags(rl.FLAG_WINDOW_HIDDEN)
  gui_app.init_window("ui diff test", fps=FPS)
  main_layout = MiciMainLayout()
  main_layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

  frame = 0
  script_index = 0

  for should_render in gui_app.render():
    while script_index < len(SCRIPT) and SCRIPT[script_index][0] == frame:
      _, event = SCRIPT[script_index]
      handle_event(event)
      script_index += 1

    ui_state.update()

    if should_render:
      main_layout.render()

    frame += 1

    if script_index >= len(SCRIPT):
      break

  gui_app.close()

  print(f"Total frames: {frame}")
  print(f"Video saved to: {os.environ['RECORD_OUTPUT']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
  parser = argparse.ArgumentParser(description='UI replay/exploration tool')
  parser.add_argument(
    '--mode', choices=['script', 'explore', 'replay'], default='script', help='script: deterministic replay | explore: auto-explore | replay: replay recording'
  )
  parser.add_argument('--recording', type=str, default=None, help='Path to recording JSON (replay mode)')
  parser.add_argument('--target-coverage', type=float, default=90.0, help='Target coverage %% (explore mode)')
  args = parser.parse_args()

  if args.mode == 'explore':
    from openpilot.selfdrive.ui.tests.diff.explorer import run_auto_explore

    final_cov = run_auto_explore(target_coverage=args.target_coverage)
    return 0 if final_cov >= args.target_coverage else 1

  elif args.mode == 'replay':
    from openpilot.selfdrive.ui.tests.diff.explorer import run_replay as run_recording_replay

    if not args.recording:
      rec_path = os.path.join(DIFF_OUT_DIR, "auto_explore_recording.json")
      if not os.path.exists(rec_path):
        print("ERROR: No recording specified and no auto_explore_recording.json found.")
        print("  Run with --mode explore first, or specify --recording <path>")
        return 1
      args.recording = rec_path
    total = run_recording_replay(args.recording)
    return 0 if total >= 80 else 1

  # Legacy script mode
  cov = coverage.Coverage(source=['openpilot.selfdrive.ui.mici'])
  with cov.collect():
    run_replay()
  cov.save()
  cov.report()
  cov.html_report(directory=os.path.join(DIFF_OUT_DIR, 'htmlcov'))
  print("HTML report: htmlcov/index.html")
  return 0


if __name__ == "__main__":
  sys.exit(main())
