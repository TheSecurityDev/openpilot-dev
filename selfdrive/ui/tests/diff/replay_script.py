from __future__ import annotations
from typing import TYPE_CHECKING
from collections.abc import Callable
from dataclasses import dataclass

from cereal import log, messaging
from openpilot.selfdrive.ui.tests.diff.replay import FPS
from openpilot.selfdrive.ui.tests.diff.replay_setup import put_update_params, send_onroad, setup_offroad_alerts, setup_update_available, setup_developer_params

WAIT = int(FPS * 0.5)

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus


# Persistent per-frame sender function, set by setup callbacks to keep sending cereal messages
_frame_fn: Callable | None = None  # TODO: This seems hacky, find a better way to do this

def get_frame_fn():
  return _frame_fn

def setup_send_fn(send_fn: Callable[[], None]) -> Callable[[], None]:
  """Return a setup function that sets the global _frame_fn to the given send function and calls it."""

  def setup() -> None:
    global _frame_fn
    _frame_fn = send_fn
    send_fn()

  return setup


# --- Setup helper functions ---


def make_network_state_setup(pm, network_type):
  def _send() -> None:
    ds = messaging.new_message('deviceState')
    ds.deviceState.networkType = network_type
    pm.send('deviceState', ds)

  return setup_send_fn(_send)


def make_onroad_setup(pm):
  def _send() -> None:
    send_onroad(pm)

  return setup_send_fn(_send)


def make_alert_setup(pm, size, text1, text2, status):
  def _send() -> None:
    send_onroad(pm)
    alert = messaging.new_message('selfdriveState')
    ss = alert.selfdriveState
    ss.alertSize = size
    ss.alertText1 = text1
    ss.alertText2 = text2
    ss.alertStatus = status
    pm.send('selfdriveState', alert)

  return setup_send_fn(_send)


# --- Script building functions ---


@dataclass
class ScriptEvent:
  if TYPE_CHECKING:
    # Prevent application imports from being excluded by coverage report since we only import here for the type hint
    from openpilot.system.ui.lib.application import MouseEvent

  setup: Callable | None = None
  mouse_events: list[MouseEvent] | None = None


AddFn = Callable[[int, ScriptEvent], None]


def build_mici_script(pm, add: AddFn, click):
  """Build the replay script for the mici layout."""
  from openpilot.system.ui.lib.application import gui_app

  center = (gui_app.width // 2, gui_app.height // 2)

  add(FPS, ScriptEvent())
  click(*center, FPS)
  click(*center, FPS)


def build_tizi_script(pm, add: AddFn, click, main_layout):
  """Build the replay script for the tizi layout."""

  def setup_and_click(setup: Callable, click_pos: tuple[int, int], wait_frames: int = WAIT):
    add(0, ScriptEvent(setup=setup))
    click(*click_pos, wait_frames)

  def setup(fn: Callable, wait_frames: int = WAIT):
    add(0, ScriptEvent(setup=fn))
    add(wait_frames, ScriptEvent())

  def make_home_refresh_setup(fn: Callable):
    """Return setup function that calls the given function to modify state and forces an immediate refresh on the home layout."""
    from openpilot.selfdrive.ui.layouts.main import MainState

    def setup():
      fn()
      main_layout._layouts[MainState.HOME].last_refresh = 0

    return setup

  # TODO: Better way of organizing the events

  # === Homescreen (clean) ===
  setup(make_network_state_setup(pm, log.DeviceState.NetworkType.wifi))

  # === Offroad Alerts (auto-transitions via HomeLayout refresh) ===
  setup(make_home_refresh_setup(setup_offroad_alerts))

  # === Update Available (auto-transitions via HomeLayout refresh) ===
  setup(make_home_refresh_setup(setup_update_available))

  # === Settings - Device (click sidebar settings button) ===
  click(150, 90)

  # === Settings - Network ===
  click(278, 450)

  # === Settings - Toggles ===
  click(278, 600)

  # === Settings - Software ===
  setup_and_click(put_update_params, (278, 720))

  # === Settings - Firehose ===
  click(278, 845)

  # === Settings - Developer (set CarParamsPersistent first) ===
  setup_and_click(setup_developer_params, (278, 950))

  # === Keyboard modal (SSH keys button in developer panel) ===
  click(1930, 470)  # click SSH keys
  click(1930, 115)  # click cancel on keyboard

  # === Close settings ===
  click(250, 160)

  # === Onroad ===
  setup(make_onroad_setup(pm))
  click(1000, 500)  # click onroad to toggle sidebar

  # === Onroad alerts ===
  # Small alert (normal)
  setup(make_alert_setup(pm, AlertSize.small, "Small Alert", "This is a small alert", AlertStatus.normal))
  # Medium alert (userPrompt)
  setup(make_alert_setup(pm, AlertSize.mid, "Medium Alert", "This is a medium alert", AlertStatus.userPrompt))
  # Full alert (critical)
  setup(make_alert_setup(pm, AlertSize.full, "DISENGAGE IMMEDIATELY", "Driver Distracted", AlertStatus.critical))
  # Full alert multiline
  setup(make_alert_setup(pm, AlertSize.full, "Reverse\nGear", "", AlertStatus.normal))
  # Full alert long text
  setup(make_alert_setup(pm, AlertSize.full, "TAKE CONTROL IMMEDIATELY", "Calibration Invalid: Remount Device & Recalibrate", AlertStatus.userPrompt))

  # End
  add(0, ScriptEvent())


ScriptEntry = tuple[int, ScriptEvent]  # (frame, event)


def build_script(pm, main_layout, big=False) -> list[ScriptEntry]:
  """
  Build the replay script for the appropriate layout variant by calling the corresponding build function.
  Return the list of ScriptEntry tuples containing the frame number and ScriptEvent for each event in the script.
  """
  from openpilot.system.ui.lib.application import MouseEvent, MousePos

  print(f"Building replay script (big={big})...")

  frame = 0
  script: list[ScriptEntry] = []

  def get_frame_time() -> float:
    return frame / FPS

  def add(delta: int, event: ScriptEvent):
    """Add event to the script with the given delta in frames from the previous event."""
    nonlocal frame
    frame += delta
    script.append((frame, event))

  def click(x: int, y: int, wait_frames: int = WAIT):
    """Add a click event for the given position and wait for the given frames."""
    mouse_down = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=get_frame_time())
    add(0, ScriptEvent(mouse_events=[mouse_down]))
    # wait 1 frame between press and release (otherwise settings button can click close underneath immediately when opened)
    mouse_up = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=get_frame_time())
    add(1, ScriptEvent(mouse_events=[mouse_up]))
    # additional wait after click
    if wait_frames > 0:
      add(wait_frames, ScriptEvent())

  if big:
    build_tizi_script(pm, add, click, main_layout)
  else:
    build_mici_script(pm, add, click)

  print(f"Built replay script with {len(script)} events and {frame} frames ({frame / FPS:.2f} seconds)")

  return script
