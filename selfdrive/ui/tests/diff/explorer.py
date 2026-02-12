#!/usr/bin/env python3
"""
UI explorer and replay engine for the mici UI.

Modes:
  explore  — Auto-explore the offroad UI using introspection for coverage
  replay   — Replay a recorded JSON action log deterministically

Usage:
  python -m openpilot.selfdrive.ui.tests.diff.explorer explore
  python -m openpilot.selfdrive.ui.tests.diff.explorer replay recording.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import coverage
import pyray as rl

from openpilot.selfdrive.ui.tests.diff.diff import DIFF_OUT_DIR

# Recording env — must be set before importing gui_app
os.environ.setdefault("RECORD", "1")
if "RECORD_OUTPUT" not in os.environ:
  os.environ["RECORD_OUTPUT"] = "mici_ui_explorer.mp4"
os.environ["RECORD_OUTPUT"] = os.path.join(DIFF_OUT_DIR, os.environ["RECORD_OUTPUT"])

from openpilot.common.params import Params
from openpilot.system.version import terms_version, training_version
from openpilot.system.ui.lib.application import gui_app, MousePos, MouseEvent
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout

FPS = 60
HEADLESS = os.getenv("WINDOWED", "0") != "1"
COVERAGE_SOURCE = ['openpilot.selfdrive.ui.mici']


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def inject_click(x: int, y: int):
  t = time.monotonic()
  with gui_app._mouse._lock:
    gui_app._mouse._events.extend([
      MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=t),
      MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=t + 0.01),
    ])


def inject_long_press(x: int, y: int):
  t = time.monotonic()
  with gui_app._mouse._lock:
    gui_app._mouse._events.append(
      MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=t)
    )


def inject_release(x: int, y: int):
  t = time.monotonic()
  with gui_app._mouse._lock:
    gui_app._mouse._events.append(
      MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=t)
    )


# def inject_swipe(x1: int, y1: int, x2: int, y2: int, steps: int = 5):
#   t = time.monotonic()
#   events = [MouseEvent(pos=MousePos(x1, y1), slot=0, left_pressed=True, left_released=False, left_down=False, t=t)]
#   for i in range(1, steps + 1):
#     frac = i / steps
#     x = int(x1 + (x2 - x1) * frac)
#     y = int(y1 + (y2 - y1) * frac)
#     events.append(MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=False, left_down=True, t=t + 0.02 * i))
#   events.append(MouseEvent(pos=MousePos(x2, y2), slot=0, left_pressed=False, left_released=True, left_down=False, t=t + 0.02 * (steps + 1)))
#   with gui_app._mouse._lock:
#     gui_app._mouse._events.extend(events)


# ---------------------------------------------------------------------------
# UI Engine
# ---------------------------------------------------------------------------

class UIEngine:
  """Manages the render loop and action execution."""

  def __init__(self):
    self._main_layout: MiciMainLayout | None = None
    self._render_gen = None
    self._frame = 0
    self._action_log: list[dict] = []

  def setup(self):
    """Initialize params and the UI window."""
    params = Params()
    params.put("HasAcceptedTerms", terms_version)
    params.put("CompletedTrainingVersion", training_version)
    params.put("DongleId", "test123456789")
    params.put("HardwareSerial", "TESTSERIAL001")
    params.put("UpdaterCurrentDescription", "0.10.1 / test-branch / abc1234 / Nov 30")
    params.put_bool("OpenpilotEnabledToggle", True)
    params.put_bool("IsLdwEnabled", True)

    os.makedirs(DIFF_OUT_DIR, exist_ok=True)
    if HEADLESS:
      rl.set_config_flags(rl.FLAG_WINDOW_HIDDEN)

    gui_app.init_window("ui explorer", fps=FPS)
    self._main_layout = MiciMainLayout()
    self._main_layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
    self._render_gen = gui_app.render()

  def pump(self, n: int = 1):
    """Advance the render loop by *n* frames."""
    if self._render_gen is None:
      raise RuntimeError("UIEngine not set up; call setup() first")
    for _ in range(n):
      try:
        should_render = next(self._render_gen)
      except StopIteration:
        return
      ui_state.update()
      if should_render:
        self._main_layout.render()
      self._frame += 1

  def execute_action(self, action: dict):
    """Execute a single recorded action dict."""
    t = action["action"]

    if t == "click":
      inject_click(action["x"], action["y"])
      self.pump(8)
    elif t == "long_press":
      inject_long_press(action["x"], action["y"])
      hold = max(1, int(action.get("duration_ms", 600) / 1000 * FPS))
      self.pump(hold)
      inject_release(action["x"], action["y"])
      self.pump(8)
    elif t == "swipe":
      x1, y1 = action["x1"], action["y1"]
      x2, y2 = action["x2"], action["y2"]
      steps = action.get("steps", 5)

      # Spread events across frames so the scroll panel's MANUAL_SCROLL
      # state is visible to child widgets (prevents false clicks).
      with gui_app._mouse._lock:
        gui_app._mouse._events.append(
          MouseEvent(pos=MousePos(x1, y1), slot=0, left_pressed=True, left_released=False, left_down=False, t=time.monotonic()))
      self.pump(2)

      for i in range(1, steps + 1):
        frac = i / steps
        x = int(x1 + (x2 - x1) * frac)
        y = int(y1 + (y2 - y1) * frac)
        with gui_app._mouse._lock:
          gui_app._mouse._events.append(
            MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=False, left_down=True, t=time.monotonic()))
        self.pump(2)

      with gui_app._mouse._lock:
        gui_app._mouse._events.append(
          MouseEvent(pos=MousePos(x2, y2), slot=0, left_pressed=False, left_released=True, left_down=False, t=time.monotonic()))
      self.pump(16)
    elif t == "set_param":
      p = Params()
      pt = action.get("param_type", "string")
      if pt == "remove":
        p.remove(action["key"])
      elif pt == "bool":
        value = str(action["value"]).lower()
        p.put_bool(action["key"], value in ("true", "1", "yes"))
      else:
        p.put(action["key"], str(action["value"]))
      self.pump(8)
    elif t == "pump":
      self.pump(action.get("frames", 30))
    else:
      raise ValueError(f"Unknown action type: {t}")

    self._action_log.append(action)

  def close(self):
    gui_app.close()

  @property
  def frame(self) -> int:
    return self._frame

  @property
  def main_layout(self) -> MiciMainLayout:
    if self._main_layout is None:
      raise RuntimeError("UIEngine not set up; call setup() first")
    return self._main_layout

  @property
  def action_log(self) -> list[dict]:
    return self._action_log


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------

def run_replay(recording_path: str):
  """Replay a previously recorded action log."""
  with open(recording_path) as f:
    actions = json.load(f)

  if not isinstance(actions, list):
    raise ValueError("Recording JSON must be a list of actions")

  print(f"Replaying {len(actions)} actions from {recording_path}")
  engine = UIEngine()
  engine.setup()
  engine.pump(30)

  cov = coverage.Coverage(source=COVERAGE_SOURCE)
  with cov.collect():
    for i, action in enumerate(actions):
      print(f"  [{i+1}/{len(actions)}] {action['action']}", end="")
      if 'x' in action:
        print(f" ({action['x']},{action['y']})", end="")
      print()
      engine.execute_action(action)
    engine.pump(60)

  engine.close()
  cov.save()
  total = cov.report()
  cov.html_report(directory=os.path.join(DIFF_OUT_DIR, 'htmlcov'))
  print(f"\nFrames: {engine.frame}  Coverage: {total:.1f}%")
  return total


# ---------------------------------------------------------------------------
# Auto-explore mode — offroad UI
# ---------------------------------------------------------------------------

def run_auto_explore(target_coverage: float = 90.0):
  """Systematically explore the offroad UI for code coverage."""
  from openpilot.selfdrive.ui.tests.diff.introspect import capture_screen_state, WidgetInfo
  from openpilot.selfdrive.ui.mici.layouts.settings.settings import SettingsLayout, PanelType
  from openpilot.selfdrive.ui.mici.widgets.button import BigButton as RealBigButton, BigCircleButton as RealBigCircleButton

  engine = UIEngine()
  engine.setup()
  engine.pump(60)

  cov = coverage.Coverage(source=COVERAGE_SOURCE)
  visited: set[str] = set()
  action_log: list[dict] = []
  settings_layout: SettingsLayout = engine.main_layout._settings_layout

  # -- helpers ---------------------------------------------------------------

  def get_state():
    return capture_screen_state(engine.main_layout)

  def do(action: dict, desc: str = ""):
    engine.execute_action(action)
    action_log.append(action)
    if desc:
      print(f"    -> {desc}")

  def click(x: int, y: int, desc: str = ""):
    do({"action": "click", "x": x, "y": y}, desc or f"click({x},{y})")

  def swipe(x1, y1, x2, y2, desc=""):
    do({"action": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "steps": 5}, desc)

  def swipe_repeat(x1, y1, x2, y2, count: int, desc: str, pump_frames: int):
    for _ in range(count):
      swipe(x1, y1, x2, y2, desc)
      pump(pump_frames)

  def swipe_left(desc="swipe left"):
    swipe(400, 120, 50, 120, desc)

  def swipe_right(desc="swipe right"):
    swipe(50, 120, 400, 120, desc)

  def swipe_down(desc="swipe down"):
    swipe(268, 12, 268, 228, desc)

  def pump(n=30):
    do({"action": "pump", "frames": n})

  def set_param_bool(key: str, value: bool, desc: str | None = None):
    do(
      {"action": "set_param", "key": key, "value": str(value).lower(), "param_type": "bool"},
      desc or f"{key}={value}",
    )

  def dismiss_modal():
    if get_state().has_modal:
      gui_app.set_modal_overlay(None)
      pump(5)

  def widget_key(w: WidgetInfo) -> str:
    return f"{w.widget_type}:{w.text}:{w.param}"

  def interact_toggles(state):
    """Click every toggle-type widget currently on screen."""
    toggle_types = ("BigToggle", "BigParamControl", "BigMultiToggle",
                    "BigMultiParamToggle", "BigCircleToggle", "BigCircleParamControl",
                    "Toggle")
    for w in state.get_interactive_widgets():
      if not w.is_on_screen(state.screen_width, state.screen_height):
        continue
      if w.widget_type not in toggle_types:
        continue
      key = widget_key(w)
      if key in visited:
        continue
      visited.add(key)
      cx, cy = w.center()
      if w.checked is not None:
        click(cx, cy, f"toggle '{w.text}' ({'ON' if w.checked else 'OFF'})")
        pump(8)
        click(cx, cy, f"toggle '{w.text}' back")
        pump(8)
      elif w.clickable and w.text:
        click(cx, cy, f"click '{w.text}'")
        pump(15)

  def explore_panel(panel_type: PanelType):
    """Open a settings panel, scroll through items, click buttons, dismiss modals."""
    name = panel_type.name.lower()
    print(f"\n  [Settings] Panel: {name}")
    settings_layout._set_current_panel(panel_type)
    pump(40)

    interact_toggles(get_state())

    panel = settings_layout._panels[panel_type].instance
    scroller = getattr(panel, '_scroller', None) or getattr(panel, '_scroll_panel', None)

    if scroller and hasattr(scroller, '_items'):
      items = scroller._items
      print(f"    {len(items)} scroller items")
      for idx, item in enumerate(items):
        # Scroll item into view
        if hasattr(item, '_rect') and item._rect.x > 0:
          scroller.scroll_to(item._rect.x, smooth=False)
          pump(20)
        else:
          swipe_left(f"scroll to item {idx}")
          pump(15)

        interact_toggles(get_state())

        # Click buttons (not toggles — those were handled above)
        if isinstance(item, (RealBigButton, RealBigCircleButton)):
          r = item._rect
          if r.width > 0 and r.height > 0:
            cx, cy = int(r.x + r.width / 2), int(r.y + r.height / 2)
            if 0 <= cx < 536 and 0 <= cy < 240:
              label = getattr(item, '_text', '') or type(item).__name__
              click(cx, cy, f"click '{label[:30]}'")
              pump(15)
              dismiss_modal()

      scroller.scroll_to(0, smooth=False)
      pump(10)
    else:
      # No scroller — just swipe through
      swipe_repeat(400, 120, 50, 120, 6, "scroll", 15)
      swipe_repeat(50, 120, 400, 120, 6, "scroll back", 8)

    pump(20)
    dismiss_modal()
    settings_layout._set_current_panel(None)
    pump(10)
    print(f"    -> closed {name}")

  # -- exploration -----------------------------------------------------------

  print("=" * 60)
  print("AUTO-EXPLORE — offroad UI")
  print("=" * 60)

  with cov.collect():
    # Phase 1: Home screen
    print("\n[Phase 1] Home screen")
    interact_toggles(get_state())
    do({"action": "long_press", "x": 268, "y": 120, "duration_ms": 700}, "long press experimental")
    pump(10)
    do({"action": "long_press", "x": 268, "y": 120, "duration_ms": 700}, "long press experimental off")
    pump(10)

    # # Phase 2: Offroad alerts
    # print("\n[Phase 2] Offroad alerts")
    # params_obj = Params()
    # alerts = {
    #   "Offroad_ConnectivityNeeded": {"text": "Connect to internet.", "severity": 1},
    #   "Offroad_UpdateFailed": {"text": "Update failed.", "severity": 1},
    #   "Offroad_TemperatureTooHigh": {"text": "Temperature too high.", "severity": 0},
    # }
    # for key, val in alerts.items():
    #   params_obj.put(key, val)
    # params_obj.put_bool("UpdateAvailable", True)
    # params_obj.put("UpdaterNewDescription", "0.10.2 / release / def5678 / Dec 01")
    # pump(30)

    # swipe_right("swipe to alerts")
    # pump(30)
    # swipe_repeat(400, 120, 50, 120, 6, "scroll alerts", 15)
    # swipe_repeat(50, 120, 400, 120, 6, "scroll alerts back", 15)
    # interact_toggles(get_state())

    # swipe_left("back to home")
    # pump(20)

    # for key in alerts:
    #   params_obj.remove(key)
    # params_obj.put_bool("UpdateAvailable", False)
    # pump(10)

    # Phase 3: Settings panels
    print("\n[Phase 3] Settings")
    click(32, 204, "open settings")
    pump(30)

    swipe_repeat(400, 120, 50, 120, 4, "scroll settings bar", 15)
    swipe_repeat(50, 120, 400, 120, 4, "scroll settings bar back", 15)

    # Pair button
    for w in get_state().get_interactive_widgets():
      if w.widget_type == "BigButton" and w.text and w.text.lower() in ("pair", "paired"):
        click(*w.center(), f"click '{w.text}'")
        pump(15)
        dismiss_modal()
        break

    for pt in (PanelType.TOGGLES, PanelType.DEVICE, PanelType.NETWORK,
               PanelType.DEVELOPER, PanelType.FIREHOSE):
      explore_panel(pt)

    swipe_down("close settings")
    pump(30)

    # # Phase 4: Param-triggered states
    # print("\n[Phase 4] Param variations")
    # for key, val in [("UpdateAvailable", True), ("UpdateAvailable", False),
    #          ("ExperimentalMode", True), ("ExperimentalMode", False),
    #          ("IsMetric", True), ("IsMetric", False),
    #          ("ShowDebugInfo", True)]:
    #   set_param_bool(key, val)
    #   pump(15)

    # pump(30)
    # interact_toggles(get_state())

    # # Revisit device panel with different param combos
    # click(32, 204, "open settings")
    # pump(20)
    # settings_layout._set_current_panel(PanelType.DEVICE)
    # pump(40)
    # swipe_repeat(400, 120, 50, 120, 6, "scroll device", 15)
    # settings_layout._set_current_panel(None)
    # pump(5)

    # set_param_bool("UpdateAvailable", True)
    # settings_layout._set_current_panel(PanelType.DEVICE)
    # pump(40)
    # swipe_repeat(400, 120, 50, 120, 6, "scroll device w/ update", 15)
    # settings_layout._set_current_panel(None)
    # pump(5)

    # # Reset
    # set_param_bool("UpdateAvailable", False, "reset UpdateAvailable")
    # set_param_bool("ShowDebugInfo", False, "reset ShowDebugInfo")
    # pump(5)
    # swipe_down("close settings")
    # pump(30)

    # Phase 5: Final home screen render
    print("\n[Phase 5] Final home screen")
    pump(60)

  # -- report ----------------------------------------------------------------
  engine.close()
  cov.save()

  total = cov.report(show_missing=True)
  cov.html_report(directory=os.path.join(DIFF_OUT_DIR, 'htmlcov'))

  print(f"\nFrames: {engine.frame}  Widgets: {len(visited)}  Coverage: {total:.1f}%")

  rec_path = os.path.join(DIFF_OUT_DIR, "auto_explore_recording.json")
  with open(rec_path, 'w') as f:
    json.dump(action_log, f, indent=2)
  print(f"Recording: {rec_path}")

  if total < target_coverage:
    print(f"Target coverage {target_coverage:.1f}% not met")
  return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
  import argparse

  parser = argparse.ArgumentParser(description='UI explorer / replay engine')
  sub = parser.add_subparsers(dest='mode', help='Mode')

  explore_p = sub.add_parser('explore', help='Auto-explore UI for coverage')
  explore_p.add_argument('--target-coverage', type=float, default=90.0)

  replay_p = sub.add_parser('replay', help='Replay a recorded action log')
  replay_p.add_argument('recording', help='Path to recording JSON file')

  args = parser.parse_args()

  if args.mode == 'replay':
    return 0 if run_replay(args.recording) >= 80 else 1
  else:
    target = getattr(args, 'target_coverage', 90.0)
    return 0 if run_auto_explore(target_coverage=target) >= target else 1


if __name__ == "__main__":
  sys.exit(main())
