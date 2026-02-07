#!/usr/bin/env python3
import os
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
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout

FPS = 60
HEADLESS = os.getenv("WINDOWED", "0") == "1"
CLICK_DURATION = 0.05
SWIPE_DURATION = 0.1
EDGE_MARGIN = 2

# Monkey-patch rl.get_frame_time to return fixed value for determinism
rl.get_frame_time = lambda: 1.0 / FPS


@dataclass
class Event:
  click: bool = False
  # TODO: add some kind of intensity
  swipe_left: bool = False
  swipe_right: bool = False
  swipe_down: bool = False
  delay: float = 1.0  # seconds to wait after the event before processing the next one


SCRIPT = [
  Event(delay=0.5),
  Event(click=True),  # settings
  Event(click=True),  # toggles
  Event(swipe_left=True, delay=1.5),  # explore toggles
  Event(swipe_down=True),  # back to settings
  Event(swipe_left=True, delay=1.5),  # explore settings
  Event(swipe_down=True),  # back to home
  Event(swipe_right=True),  # open alerts
  Event(),  # wait
]


def setup_state():
  params = Params()
  params.put("HasAcceptedTerms", terms_version)
  params.put("CompletedTrainingVersion", training_version)
  params.put("DongleId", "test123456789")
  params.put("UpdaterCurrentDescription", "0.10.1 / test-branch / abc1234 / Nov 30")
  return None


def inject_gesture(coords: list[tuple[int, int]], duration: float = CLICK_DURATION):
  """Inject a click or swipe gesture with linear interpolation."""
  num_steps = max(int(duration * FPS), 3)
  events: list[MouseEvent] = []

  # Press down at first coordinate
  x, y = coords[0]
  events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=0.0))

  # Interpolate intermediate positions
  if len(coords) > 1:
    for step in range(1, num_steps):
      progress = step / num_steps
      segment_progress = progress * (len(coords) - 1)
      segment_idx = min(int(segment_progress), len(coords) - 2)
      local_progress = segment_progress - segment_idx

      x1, y1 = coords[segment_idx]
      x2, y2 = coords[segment_idx + 1]
      x = int(x1 + (x2 - x1) * local_progress)
      y = int(y1 + (y2 - y1) * local_progress)
      events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=False, left_down=True, t=0.0))

  # Release at final coordinate
  x, y = coords[-1]
  events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=0.0))

  with gui_app._mouse._lock:
    gui_app._mouse._events.extend(events)


def handle_event(event: Event):
  if event.click:
    inject_gesture([(gui_app.width // 2, gui_app.height // 2)])
  if event.swipe_left:
    inject_gesture(
      [(gui_app.width * 3 // 4, gui_app.height // 2), (gui_app.width // 4, gui_app.height // 2), (EDGE_MARGIN, gui_app.height // 2)], SWIPE_DURATION
    )
  if event.swipe_right:
    inject_gesture(
      [(gui_app.width // 4, gui_app.height // 2), (gui_app.width * 3 // 4, gui_app.height // 2), (gui_app.width - EDGE_MARGIN, gui_app.height // 2)],
      SWIPE_DURATION,
    )
  if event.swipe_down:
    inject_gesture(
      [(gui_app.width // 2, gui_app.height // 4), (gui_app.width // 2, gui_app.height * 3 // 4), (gui_app.width // 2, gui_app.height - EDGE_MARGIN)],
      SWIPE_DURATION,
    )


def run_replay():
  setup_state()
  os.makedirs(DIFF_OUT_DIR, exist_ok=True)

  if not HEADLESS:
    rl.set_config_flags(rl.FLAG_WINDOW_HIDDEN)
  gui_app.init_window("ui diff test", fps=FPS)
  main_layout = MiciMainLayout()
  main_layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

  frame = 0
  script_index = 0
  next_event_frame = 0

  for should_render in gui_app.render():
    if script_index < len(SCRIPT) and frame >= next_event_frame:
      event = SCRIPT[script_index]
      handle_event(event)
      next_event_frame = frame + int(event.delay * FPS)
      script_index += 1

    if should_render:
      main_layout.render()

    frame += 1
    if script_index >= len(SCRIPT):
      break

  gui_app.close()

  print(f"Total frames: {frame}")
  print(f"Video saved to: {os.environ['RECORD_OUTPUT']}")


def main():
  cov = coverage.coverage(source=['openpilot.selfdrive.ui.mici'])
  with cov.collect():
    run_replay()
  cov.stop()
  cov.save()
  cov.report()
  cov.html_report(directory=os.path.join(DIFF_OUT_DIR, 'htmlcov'))
  print("HTML report: htmlcov/index.html")


if __name__ == "__main__":
  main()
