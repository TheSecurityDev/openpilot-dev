from __future__ import annotations
from typing import TYPE_CHECKING
from collections.abc import Callable
from dataclasses import dataclass

from cereal import car, log, messaging
from cereal.messaging import PubMaster
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.selfdrive.ui.tests.diff.replay import FPS, LayoutVariant
from openpilot.system.updated.updated import parse_release_notes

# Default frames to wait after events
WAIT_SHORT = int(FPS * 0.5)
WAIT_LONG = int(FPS * 1)

# Direction vectors for drag gestures
DIR_LEFT = (-1, 0)
DIR_RIGHT = (1, 0)
DIR_UP = (0, -1)
DIR_DOWN = (0, 1)

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus

BRANCH_NAME = "this-is-a-really-super-mega-ultra-max-extreme-ultimate-long-branch-name"


@dataclass
class ScriptEvent:
  if TYPE_CHECKING:
    # Only import for type checking to avoid excluding the application code from coverage
    from openpilot.system.ui.lib.application import MouseEvent

  setup: Callable | None = None  # Setup function to run prior to adding mouse events
  mouse_events: list[MouseEvent] | None = None  # Mouse events to send to the application on this event's frame
  send_fn: Callable | None = None  # When set, the main loop uses this as the new persistent sender


ScriptEntry = tuple[int, ScriptEvent]  # (frame, event)


class Script:
  def __init__(self, fps: int) -> None:
    self.fps = fps
    self.frame = 0
    self.entries: list[ScriptEntry] = []

  def get_frame_time(self) -> float:
    return self.frame / self.fps

  def add(self, event: ScriptEvent, before: int = 0, after: int = 0) -> None:
    """Add event to the script, optionally with the given number of frames to wait before or after the event."""
    self.frame += before
    self.entries.append((self.frame, event))
    self.frame += after

  def end(self) -> None:
    """Add a final empty event to mark the end of the script."""
    self.add(ScriptEvent())  # Without this, it will just end on the last event without waiting for any specified delay after it

  def wait(self, frames: int) -> None:
    """Add a delay for the given number of frames followed by an empty event."""
    self.add(ScriptEvent(), before=frames)

  def setup(self, fn: Callable, wait_after: int = WAIT_SHORT) -> None:
    """Add a setup function to be called immediately followed by a delay of the given number of frames."""
    self.add(ScriptEvent(setup=fn), after=wait_after)

  def set_send(self, fn: Callable, wait_after: int = WAIT_SHORT) -> None:
    """Set a new persistent send function to be called every frame."""
    self.add(ScriptEvent(send_fn=fn), after=wait_after)

  # TODO: Add more complex gestures if needed
  def click(self, x: int, y: int, wait_after: int = WAIT_SHORT, wait_between: int = 2) -> None:
    """Add a click event to the script for the given position and specify frames to wait between mouse events or after the click."""
    # NOTE: By default we wait a couple frames between mouse events so pressed states will be rendered
    from openpilot.system.ui.lib.application import MouseEvent, MousePos

    # TODO: Add support for long press (left_down=True)
    mouse_down = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=True, left_released=False, left_down=False, t=self.get_frame_time())
    self.add(ScriptEvent(mouse_events=[mouse_down]), after=wait_between)
    mouse_up = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=True, left_down=False, t=self.get_frame_time())
    self.add(ScriptEvent(mouse_events=[mouse_up]), after=wait_after)

  def drag(self, start_x: int, start_y: int, direction: tuple[int, int], distance: int, duration_frames: int, wait_after: int = WAIT_LONG) -> None:
    """Add a drag gesture to the script from start position in the specified direction by the given distance over the given number of frames."""
    from openpilot.system.ui.lib.application import MouseEvent, MousePos

    # Calculate delta based on direction and distance
    delta_x = direction[0] * distance
    delta_y = direction[1] * distance

    # Calculate end position based on delta
    end_x = start_x + delta_x
    end_y = start_y + delta_y

    # Mouse down at start
    mouse_down = MouseEvent(pos=MousePos(start_x, start_y), slot=0, left_pressed=True, left_released=False, left_down=True, t=self.get_frame_time())
    self.add(ScriptEvent(mouse_events=[mouse_down]), after=1)

    # Interpolate positions over duration_frames
    for i in range(1, duration_frames):
      t = i / duration_frames
      x = int(start_x + delta_x * t)
      y = int(start_y + delta_y * t)
      mouse_move = MouseEvent(pos=MousePos(x, y), slot=0, left_pressed=False, left_released=False, left_down=True, t=self.get_frame_time())
      self.add(ScriptEvent(mouse_events=[mouse_move]), after=1)

    # Mouse up at end
    mouse_up = MouseEvent(pos=MousePos(end_x, end_y), slot=0, left_pressed=False, left_released=True, left_down=False, t=self.get_frame_time())
    self.add(ScriptEvent(mouse_events=[mouse_up]), after=wait_after)


# --- Setup functions ---

def put_update_params(params: Params | None = None) -> None:
  if params is None:
    params = Params()
  params.put("UpdaterCurrentReleaseNotes", parse_release_notes(BASEDIR))
  params.put("UpdaterNewReleaseNotes", parse_release_notes(BASEDIR))
  params.put("UpdaterTargetBranch", BRANCH_NAME)


def setup_offroad_alerts() -> None:
  put_update_params(Params())
  set_offroad_alert("Offroad_TemperatureTooHigh", True, extra_text='99C')
  set_offroad_alert("Offroad_ExcessiveActuation", True, extra_text='longitudinal')
  set_offroad_alert("Offroad_IsTakingSnapshot", True)


def setup_update_available() -> None:
  params = Params()
  params.put_bool("UpdateAvailable", True)
  params.put("UpdaterNewDescription", f"0.10.2 / {BRANCH_NAME} / 0a1b2c3 / Jan 01")
  put_update_params(params)


def setup_developer_params() -> None:
  CP = car.CarParams()
  CP.alphaLongitudinalAvailable = True
  Params().put("CarParamsPersistent", CP.to_bytes())


# --- Send functions ---

def send_onroad(pm: PubMaster) -> None:
  ds = messaging.new_message('deviceState')
  ds.deviceState.started = True
  ds.deviceState.networkType = log.DeviceState.NetworkType.wifi

  ps = messaging.new_message('pandaStates', 1)
  ps.pandaStates[0].pandaType = log.PandaState.PandaType.dos
  ps.pandaStates[0].ignitionLine = True

  pm.send('deviceState', ds)
  pm.send('pandaStates', ps)


def make_network_state_setup(pm: PubMaster, network_type) -> Callable:
  def _send() -> None:
    ds = messaging.new_message('deviceState')
    ds.deviceState.networkType = network_type
    pm.send('deviceState', ds)
  return _send


def make_alert_setup(pm: PubMaster, size, text1, text2, status) -> Callable:
  def _send() -> None:
    send_onroad(pm)
    alert = messaging.new_message('selfdriveState')
    ss = alert.selfdriveState
    ss.alertSize = size
    ss.alertText1 = text1
    ss.alertText2 = text2
    ss.alertStatus = status
    pm.send('selfdriveState', alert)
  return _send


# --- Script builders ---

def build_mici_script(pm: PubMaster, main_layout, script: Script) -> None:
  """Build the replay script for the mici layout."""
  from openpilot.system.ui.lib.application import gui_app

  width, height = gui_app.width, gui_app.height
  center = (width // 2, height // 2)
  right = (width * 4 // 5, height // 2)
  left = (width // 5, height // 2)
  top = (width // 2, height // 4)
  bottom = (width // 2, height * 3 // 4)

  DURATION = 5
  SWIPE_WAIT = int(FPS * 0.75)
  FAST_CLICK = int(FPS * 0.25)

  def click(wait_after: int = WAIT_SHORT):
    script.click(*center, wait_after=wait_after)

  def swipe_left(distance: int = right[0] - left[0], duration_frames: int = DURATION, wait_after: int = SWIPE_WAIT):
    script.drag(right[0], right[1], DIR_LEFT, distance, duration_frames, wait_after)

  def swipe_right(distance: int = right[0] - left[0], duration_frames: int = DURATION, wait_after: int = SWIPE_WAIT):
    script.drag(left[0], left[1], DIR_RIGHT, distance, duration_frames, wait_after)

  def swipe_down(distance: int = bottom[1] - top[1], duration_frames: int = DURATION, wait_after: int = SWIPE_WAIT):
    script.drag(top[0], top[1], DIR_DOWN, distance, duration_frames, wait_after)

  def swipe_up(distance: int = bottom[1] - top[1], duration_frames: int = DURATION, wait_after: int = SWIPE_WAIT):
    script.drag(bottom[0], bottom[1], DIR_UP, distance, duration_frames, wait_after)

  # === Homescreen === #
  script.wait(WAIT_SHORT)
  swipe_right(width, wait_after=WAIT_SHORT)  # open alerts
  swipe_left(width, wait_after=WAIT_SHORT)  # close alerts
  swipe_left(width, wait_after=WAIT_SHORT)  # onroad screen
  swipe_right(width, wait_after=WAIT_SHORT)  # back to home

  def explore_panel(item_count: int, interact_fn: Callable[[int], None] | None = None, get_swipe_amount: Callable[[int], tuple[int, int]] | None = None):
    """Helper function to explore a panel with the given number of items by swiping through each one
    and optionally interacting with them using the provided callback.
      If `get_swipe_amount` is provided, it will be called for each item index to determine the swipe distance and duration
    for that item, allowing for variable spacing between items. Otherwise, a default swipe amount will be used for all items.
    """
    SWIPE_AMOUNT = (210, 10)  # distance, duration
    distance = 0  # track cumulative distance so we can swipe back
    # Scroll settings and back
    for i in range(item_count):
      # tests trying to scroll past the last item, so skip clicking again
      if (i < item_count):
        if interact_fn:
          interact_fn(i)
      # swipe to roughly the center of the next toggle (using the provided get_swipe_amount callback if necessary)
      swipe_amount = (get_swipe_amount(i) or SWIPE_AMOUNT) if get_swipe_amount else SWIPE_AMOUNT
      distance += swipe_amount[0]
      swipe_left(*swipe_amount)
    swipe_right(distance * item_count, SWIPE_AMOUNT[1])  # go back to beginning

  def interact_toggles(i: int):
    # go through all toggle states (first one is personality, which has 3 states)
    for _ in range(3 if i == 0 else 2):
      click(wait_after=FAST_CLICK)  # click through each toggle quickly

  def interact_network(i: int):
    pass

  def interact_settings(i: int):
    click()  # click each setting
    if (i == 0):
      explore_panel(8, interact_toggles)  # explore toggles
    elif (i == 1):
      explore_panel(4, interact_network)  # explore network settings
    swipe_down()  # go back

  # === Settings === #
  click()  # Open settings
  explore_panel(6, interact_settings)  # Explore settings

  swipe_down() # back to home

  script.end()


def build_tizi_script(pm: PubMaster, main_layout, script: Script) -> None:
  """Build the replay script for the tizi layout."""

  def make_home_refresh_setup(fn: Callable) -> Callable:
    """Return setup function that calls the given function to modify state and forces an immediate refresh on the home layout."""
    from openpilot.selfdrive.ui.layouts.main import MainState

    def setup():
      fn()
      main_layout._layouts[MainState.HOME].last_refresh = 0

    return setup

  # TODO: Better way of organizing the events

  # === Homescreen ===
  script.set_send(make_network_state_setup(pm, log.DeviceState.NetworkType.wifi))

  # === Offroad Alerts (auto-transitions via HomeLayout refresh) ===
  script.setup(make_home_refresh_setup(setup_offroad_alerts))

  # === Update Available (auto-transitions via HomeLayout refresh) ===
  script.setup(make_home_refresh_setup(setup_update_available))

  # === Settings - Device (click sidebar settings button) ===
  script.click(150, 90)
  script.click(1985, 790)  # reset calibration confirmation
  script.click(650, 750)  # cancel

  # === Settings - Network ===
  script.click(278, 450)
  script.click(1880, 100)  # advanced network settings
  script.click(630, 80)  # back

  # === Settings - Toggles ===
  script.click(278, 600)
  script.click(1200, 280)  # experimental mode description

  # === Settings - Software ===
  script.setup(put_update_params, wait_after=0)
  script.click(278, 720)

  # === Settings - Firehose ===
  script.click(278, 845)

  # === Settings - Developer (set CarParamsPersistent first) ===
  script.setup(setup_developer_params, wait_after=0)
  script.click(278, 950)
  script.click(2000, 960)  # toggle alpha long
  script.click(1500, 875)  # confirm

  # === Keyboard modal (SSH keys button in developer panel) ===
  script.click(1930, 470)  # click SSH keys
  script.click(1930, 115)  # click cancel on keyboard

  # === Close settings ===
  script.click(250, 160)

  # === Onroad ===
  script.set_send(lambda: send_onroad(pm))
  script.click(1000, 500)  # click onroad to toggle sidebar

  # === Onroad alerts ===
  # Small alert (normal)
  script.set_send(make_alert_setup(pm, AlertSize.small, "Small Alert", "This is a small alert", AlertStatus.normal))
  # Medium alert (userPrompt)
  script.set_send(make_alert_setup(pm, AlertSize.mid, "Medium Alert", "This is a medium alert", AlertStatus.userPrompt))
  # Full alert (critical)
  script.set_send(make_alert_setup(pm, AlertSize.full, "DISENGAGE IMMEDIATELY", "Driver Distracted", AlertStatus.critical))
  # Full alert multiline
  script.set_send(make_alert_setup(pm, AlertSize.full, "Reverse\nGear", "", AlertStatus.normal))
  # Full alert long text
  script.set_send(make_alert_setup(pm, AlertSize.full, "TAKE CONTROL IMMEDIATELY", "Calibration Invalid: Remount Device & Recalibrate", AlertStatus.userPrompt))

  # End
  script.end()


def build_script(pm: PubMaster, main_layout, variant: LayoutVariant) -> list[ScriptEntry]:
  """Build the replay script for the appropriate layout variant and return list of script entries."""
  print(f"Building {variant} replay script...")

  script = Script(FPS)
  builder = build_tizi_script if variant == 'tizi' else build_mici_script
  builder(pm, main_layout, script)

  print(f"Built replay script with {len(script.entries)} events and {script.frame} frames ({script.get_frame_time():.2f} seconds)")

  return script.entries
