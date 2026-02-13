from collections.abc import Callable

from cereal import car, log, messaging
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.selfdrive.ui.tests.diff.replay import DummyEvent, FPS
from openpilot.system.updated.updated import parse_release_notes

HOLD = int(FPS * 0.5)

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus
PandaType = log.PandaState.PandaType
NetworkType = log.DeviceState.NetworkType

BRANCH_NAME = "this-is-a-really-super-mega-ultra-max-extreme-ultimate-long-branch-name"

# Persistent per-frame sender function, set by setup callbacks to keep sending cereal messages
_frame_fn: Callable | None = None  # TODO: This is really hacky, find a better way to do this


def get_frame_fn():
  return _frame_fn


def setup_send_fn(send_fn: Callable[[], None]) -> Callable[[], None]:
  """Create a setup function that sets the global _frame_fn to the given send function and calls it."""

  def setup() -> None:
    global _frame_fn
    _frame_fn = send_fn
    send_fn()

  return setup


# --- Setup helper functions ---


def put_update_params(params: Params):
  params.put("UpdaterCurrentReleaseNotes", parse_release_notes(BASEDIR))
  params.put("UpdaterNewReleaseNotes", parse_release_notes(BASEDIR))
  params.put("UpdaterTargetBranch", BRANCH_NAME)


def setup_offroad_alerts():
  put_update_params(Params())
  set_offroad_alert("Offroad_TemperatureTooHigh", True, extra_text='99C')
  set_offroad_alert("Offroad_ExcessiveActuation", True, extra_text='longitudinal')
  set_offroad_alert("Offroad_IsTakingSnapshot", True)


def setup_update_available():
  params = Params()
  params.put_bool("UpdateAvailable", True)
  params.put("UpdaterNewDescription", f"0.10.2 / {BRANCH_NAME} / 0a1b2c3 / Jan 01")
  put_update_params(params)


def setup_developer_params():
  CP = car.CarParams()
  CP.alphaLongitudinalAvailable = True
  Params().put("CarParamsPersistent", CP.to_bytes())


def send_onroad(pm):
  ds = messaging.new_message('deviceState')
  ds.deviceState.started = True
  ds.deviceState.networkType = NetworkType.wifi

  ps = messaging.new_message('pandaStates', 1)
  ps.pandaStates[0].pandaType = PandaType.dos
  ps.pandaStates[0].ignitionLine = True

  pm.send('deviceState', ds)
  pm.send('pandaStates', ps)


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


AddFn = Callable[[int, DummyEvent], None]


def build_mici_script(pm, add: AddFn, click, setup):
  """Build the replay script for the mici layout by calling add() with the appropriate events and frame timings."""
  from openpilot.system.ui.lib.application import gui_app

  w, h = gui_app.width, gui_app.height
  center = (w // 2, h // 2)

  HOLD_LONG = int(FPS)

  add(HOLD_LONG, DummyEvent())
  click(*center, HOLD_LONG)
  click(*center, HOLD_LONG)


def build_tizi_script(pm, add: AddFn, click, setup, main_layout):
  """Build the replay script for the tizi layout by calling add() with the appropriate events and frame timings."""

  def hold(dt: int = HOLD):
    """Hold for the given time delta (in frames) by adding a no-op event."""
    add(dt, DummyEvent())

  def make_home_refresh_setup(fn: Callable):
    """Return setup function that calls the given function to modify state and forces an immediate refresh on the home layout."""

    def setup():
      """Call the function to modify state and then force refresh on the home layout."""
      from openpilot.selfdrive.ui.layouts.main import MainState

      fn()
      main_layout._layouts[MainState.HOME].last_refresh = 0

    return setup

  # TODO: Better way of organizing the events

  # === Homescreen (clean) ===
  setup(make_network_state_setup(pm, NetworkType.wifi))

  # === Offroad Alerts (auto-transitions via HomeLayout refresh) ===
  setup(make_home_refresh_setup(setup_offroad_alerts))

  # === Update Available (auto-transitions via HomeLayout refresh) ===
  setup(make_home_refresh_setup(setup_update_available))

  # === Settings - Device (click sidebar settings button) ===
  # Sidebar SETTINGS_BTN = rl.Rectangle(50, 35, 200, 117), center ~(150, 93)
  # NOTE: There's an issue where the click will also trigger the close button underneath (since it occurs in the same frame), so keep it left of that
  click(100, 100)

  # === Settings - Network ===
  # Nav buttons start at y=300, height=110, x centered ~278
  click(278, 450)

  # === Settings - Toggles ===
  click(278, 600)

  # === Settings - Software ===
  add(0, DummyEvent(setup=lambda: put_update_params(Params()), click_pos=(278, 720)))
  hold()

  # === Settings - Firehose ===
  click(278, 845)

  # === Settings - Developer (set CarParamsPersistent first) ===
  add(0, DummyEvent(setup=setup_developer_params, click_pos=(278, 950)))
  hold()

  # === Keyboard modal (SSH keys button in developer panel) ===
  click(1930, 470)  # click SSH keys
  click(1930, 115)  # click cancel on keyboard

  # === Close settings (close button center ~(250, 160)) ===
  click(250, 160)

  # === Onroad ===
  setup(make_onroad_setup(pm))

  # === Onroad with sidebar (click onroad to toggle) ===
  click(1000, 500)

  # === Onroad alerts ===
  # Small alert
  setup(make_alert_setup(pm, AlertSize.small, "Small Alert", "This is a small alert", AlertStatus.normal))

  # Medium alert
  setup(make_alert_setup(pm, AlertSize.mid, "Medium Alert", "This is a medium alert", AlertStatus.userPrompt))

  # Full alert
  setup(make_alert_setup(pm, AlertSize.full, "DISENGAGE IMMEDIATELY", "Driver Distracted", AlertStatus.critical))

  # Full alert multiline
  setup(make_alert_setup(pm, AlertSize.full, "Reverse\nGear", "", AlertStatus.normal))

  # Full alert long text
  setup(make_alert_setup(pm, AlertSize.full, "TAKE CONTROL IMMEDIATELY", "Calibration Invalid: Remount Device & Recalibrate", AlertStatus.userPrompt))

  # End
  add(0, DummyEvent())


def build_script(pm, main_layout, big=False) -> list[tuple[int, DummyEvent]]:
  """Build and return the correct replay script as a list of (frame index, event) tuples."""
  print(f"Building replay script (big={big})...")

  t = 0
  script: list[tuple[int, DummyEvent]] = []

  def add(dt: int, event: DummyEvent):
    """Add an event to the script for the given time delta (in frames) from the previous event."""
    nonlocal t
    t += dt
    script.append((t, event))

  def hold(dt: int = HOLD):
    """Hold for the given time delta (in frames) by adding a no-op event."""
    add(dt, DummyEvent())

  def click(x: int, y: int, hold_time: int = HOLD):
    """Add a click event for the given position and hold for the given time (in frames)."""
    add(0, DummyEvent(click_pos=(x, y)))
    if hold_time > 0:
      hold(hold_time)

  def setup(fn: Callable, hold_time: int = HOLD):
    """Add a setup event that calls the given function and hold for the given time (in frames)."""
    add(0, DummyEvent(setup=fn))
    if hold_time > 0:
      hold(hold_time)

  if big:
    build_tizi_script(pm, add, click, setup, main_layout)
  else:
    build_mici_script(pm, add, click, setup)

  print(f"Built replay script with {len(script)} events and {t} frames ({t / FPS:.2f} seconds)")

  return script
