from __future__ import annotations
from typing import TYPE_CHECKING
from collections.abc import Callable
from dataclasses import dataclass

from cereal import log
from openpilot.selfdrive.ui.tests.diff.replay import FPS, ReplayContext
from openpilot.selfdrive.ui.tests.diff.replay_setup import (
  put_update_params, setup_offroad_alerts, setup_update_available, setup_developer_params,
  make_network_state_setup, make_onroad_setup, make_alert_setup,
)

WAIT = int(FPS * 0.5)

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus


@dataclass
class ScriptEvent:
  if TYPE_CHECKING:
    # Only import for type checking to avoid excluding the application code from coverage
    from openpilot.system.ui.lib.application import MouseEvent

  setup: Callable | None = None
  mouse_events: list[MouseEvent] | None = None


ScriptEntry = tuple[int, ScriptEvent]  # (frame, event)


class Script:
  def __init__(self, fps: int):
    self.fps = fps
    self.frame = 0
    self.entries: list[ScriptEntry] = []

  def get_frame_time(self) -> float:
    return self.frame / self.fps

  def add(self, delta: int, event: ScriptEvent):
    """Add event to the script with the given delta in frames from the previous event."""
    self.frame += delta
    self.entries.append((self.frame, event))

  def click(self, x: int, y: int, wait_frames: int = WAIT):
    """Add a click event for the given position and wait for the given frames."""
    from openpilot.system.ui.lib.application import MouseEvent, MousePos

    mouse_down = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=self.get_frame_time())
    self.add(0, ScriptEvent(mouse_events=[mouse_down]))
    # wait 1 frame between press and release (otherwise settings button can click close underneath immediately when opened)
    mouse_up = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=self.get_frame_time())
    self.add(1, ScriptEvent(mouse_events=[mouse_up]))
    # additional wait after click
    if wait_frames > 0:
      self.add(wait_frames, ScriptEvent())


def build_mici_script(ctx: ReplayContext, script: Script):
  """Build the replay script for the mici layout."""
  from openpilot.system.ui.lib.application import gui_app

  center = (gui_app.width // 2, gui_app.height // 2)

  script.add(FPS, ScriptEvent())
  script.click(*center, FPS)
  script.click(*center, FPS)


def build_tizi_script(ctx: ReplayContext, script: Script):
  """Build the replay script for the tizi layout."""

  def setup_and_click(setup: Callable, click_pos: tuple[int, int], wait_frames: int = WAIT):
    script.add(0, ScriptEvent(setup=setup))
    script.click(*click_pos, wait_frames)

  def setup(fn: Callable, wait_frames: int = WAIT):
    script.add(0, ScriptEvent(setup=fn))
    script.add(wait_frames, ScriptEvent())

  def make_home_refresh_setup(fn: Callable):
    """Return setup function that calls the given function to modify state and forces an immediate refresh on the home layout."""
    from openpilot.selfdrive.ui.layouts.main import MainState

    def setup():
      fn()
      ctx.main_layout._layouts[MainState.HOME].last_refresh = 0

    return setup

  # TODO: Better way of organizing the events

  # === Homescreen (clean) ===
  setup(make_network_state_setup(ctx, log.DeviceState.NetworkType.wifi))

  # === Offroad Alerts (auto-transitions via HomeLayout refresh) ===
  setup(make_home_refresh_setup(setup_offroad_alerts))

  # === Update Available (auto-transitions via HomeLayout refresh) ===
  setup(make_home_refresh_setup(setup_update_available))

  # === Settings - Device (click sidebar settings button) ===
  script.click(150, 90)

  # === Settings - Network ===
  script.click(278, 450)

  # === Settings - Toggles ===
  script.click(278, 600)

  # === Settings - Software ===
  setup_and_click(put_update_params, (278, 720))

  # === Settings - Firehose ===
  script.click(278, 845)

  # === Settings - Developer (set CarParamsPersistent first) ===
  setup_and_click(setup_developer_params, (278, 950))

  # === Keyboard modal (SSH keys button in developer panel) ===
  script.click(1930, 470)  # click SSH keys
  script.click(1930, 115)  # click cancel on keyboard

  # === Close settings ===
  script.click(250, 160)

  # === Onroad ===
  setup(make_onroad_setup(ctx))
  script.click(1000, 500)  # click onroad to toggle sidebar

  # === Onroad alerts ===
  # Small alert (normal)
  setup(make_alert_setup(ctx, AlertSize.small, "Small Alert", "This is a small alert", AlertStatus.normal))
  # Medium alert (userPrompt)
  setup(make_alert_setup(ctx, AlertSize.mid, "Medium Alert", "This is a medium alert", AlertStatus.userPrompt))
  # Full alert (critical)
  setup(make_alert_setup(ctx, AlertSize.full, "DISENGAGE IMMEDIATELY", "Driver Distracted", AlertStatus.critical))
  # Full alert multiline
  setup(make_alert_setup(ctx, AlertSize.full, "Reverse\nGear", "", AlertStatus.normal))
  # Full alert long text
  setup(make_alert_setup(ctx, AlertSize.full, "TAKE CONTROL IMMEDIATELY", "Calibration Invalid: Remount Device & Recalibrate", AlertStatus.userPrompt))

  # End
  script.add(0, ScriptEvent())


def build_script(context: ReplayContext, big=False) -> list[ScriptEntry]:
  """
  Build the replay script for the appropriate layout variant by calling the corresponding build function.
  Return the list of ScriptEntry tuples containing the frame number and ScriptEvent for each event in the script.
  """
  print(f"Building replay script (big={big})...")

  script = Script(FPS)
  builder = build_tizi_script if big else build_mici_script
  builder(context, script)

  print(f"Built replay script with {len(script.entries)} events and {script.frame} frames ({script.frame / FPS:.2f} seconds)")

  return script.entries
