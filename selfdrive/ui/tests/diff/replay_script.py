"""Tizi UI replay script — defines the frame-by-frame test scenario."""
from collections.abc import Callable

from cereal import car, log, messaging
from cereal.messaging import PubMaster
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.selfdrive.ui.tests.diff.replay import DummyEvent, FPS
from openpilot.system.updated.updated import parse_release_notes

HOLD = int(FPS * 0.25)

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus

BRANCH_NAME = "this-is-a-really-super-mega-ultra-max-extreme-ultimate-long-branch-name"

# Persistent per-frame sender function, set by setup callbacks to keep sending cereal messages
_frame_fn: Callable | None = None


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
  params.put("UpdaterNewDescription", f"0.10.1 / {BRANCH_NAME} / 7864838 / Oct 03")
  put_update_params(params)


def setup_developer_params():
  CP = car.CarParams()
  CP.alphaLongitudinalAvailable = True
  Params().put("CarParamsPersistent", CP.to_bytes())


def dismiss_modal():
  # TODO: Don't dismiss this way if possible
  from openpilot.system.ui.lib.application import gui_app
  gui_app.set_modal_overlay(None)


def send_onroad(pm):
  ds = messaging.new_message('deviceState')
  ds.deviceState.started = True
  ds.deviceState.networkType = log.DeviceState.NetworkType.wifi

  ps = messaging.new_message('pandaStates', 1)
  ps.pandaStates[0].pandaType = log.PandaState.PandaType.dos
  ps.pandaStates[0].ignitionLine = True

  pm.send('deviceState', ds)
  pm.send('pandaStates', ps)


def make_onroad_setup(pm):
  def _send():
    send_onroad(pm)

  def setup():
    global _frame_fn
    _frame_fn = _send
    send_onroad(pm)
  return setup


def make_alert_setup(pm, size, text1, text2, status):
  def _send():
    send_onroad(pm)
    alert = messaging.new_message('selfdriveState')
    ss = alert.selfdriveState
    ss.alertSize = size
    ss.alertText1 = text1
    ss.alertText2 = text2
    ss.alertStatus = status
    pm.send('selfdriveState', alert)

  def setup():
    global _frame_fn
    _frame_fn = _send
    _send()
  return setup


def get_frame_fn():
  return _frame_fn


def build_script(main_layout, big=False) -> list[tuple[int, DummyEvent]]:
  """Build and return the correct replay script as a list of (frame index, event) tuples."""
  t = 0
  script: list[tuple[int, DummyEvent]] = []

  def add(dt: int, event: DummyEvent):
    nonlocal t
    t += dt
    script.append((t, event))

  def hold(dt=HOLD):
    add(dt, DummyEvent())

  if not big:
    # mici script
    from openpilot.system.ui.lib.application import gui_app

    w, h = gui_app.width, gui_app.height

    # === Homescreen (clean) ===
    add(0, DummyEvent())
    add(FPS, DummyEvent(click_pos=(w // 2, h // 2)))
    add(FPS, DummyEvent(click_pos=(w // 2, h // 2)))
    add(FPS, DummyEvent())
    return script

  # tizi script

  def make_home_refresh_setup(fn: Callable):
    """Set up state and force an immediate refresh on the home layout."""
    def setup():
      from openpilot.selfdrive.ui.layouts.main import MainState
      fn()
      main_layout._layouts[MainState.HOME].last_refresh = 0
    return setup

  pm = PubMaster(["deviceState", "pandaStates", "driverStateV2", "selfdriveState"])

  # Seed initial offroad device state
  ds = messaging.new_message('deviceState')
  ds.deviceState.networkType = log.DeviceState.NetworkType.wifi
  pm.send('deviceState', ds)

  # TODO: Better way of organizing the events

  # === Homescreen (clean) ===
  add(0, DummyEvent())
  hold()

  # === Offroad Alerts (auto-transitions via HomeLayout refresh) ===
  add(0, DummyEvent(setup=make_home_refresh_setup(setup_offroad_alerts)))
  hold()

  # === Update Available (auto-transitions via HomeLayout refresh) ===
  add(0, DummyEvent(setup=make_home_refresh_setup(setup_update_available)))
  hold()

  # === Settings - Device (click sidebar settings button) ===
  # Sidebar SETTINGS_BTN = rl.Rectangle(50, 35, 200, 117), center ~(150, 93)
  # NOTE: There's an issue where the click will also trigger the close button underneath (since it occurs in the same frame), so keep it left of that
  add(0, DummyEvent(click_pos=(100, 100)))
  hold()

  # === Settings - Network ===
  # Nav buttons start at y=300, height=110, x centered ~278
  add(0, DummyEvent(click_pos=(278, 450)))
  hold()

  # === Settings - Toggles ===
  add(0, DummyEvent(click_pos=(278, 600)))
  hold()

  # === Settings - Software ===
  add(0, DummyEvent(setup=lambda: put_update_params(Params())))
  add(int(FPS * 0.2), DummyEvent(click_pos=(278, 720)))
  hold()

  # === Settings - Firehose ===
  add(0, DummyEvent(click_pos=(278, 845)))
  hold()

  # === Settings - Developer (set CarParamsPersistent first) ===
  add(0, DummyEvent(setup=setup_developer_params))
  add(int(FPS * 0.2), DummyEvent(click_pos=(278, 950)))
  hold()

  # === Keyboard modal (SSH keys button in developer panel) ===
  add(0, DummyEvent(click_pos=(1930, 470)))
  add(HOLD, DummyEvent(setup=dismiss_modal))
  add(int(FPS * 0.3), DummyEvent())

  # === Close settings (close button center ~(250, 160)) ===
  add(0, DummyEvent(click_pos=(250, 160)))
  hold()

  # === Onroad ===
  add(0, DummyEvent(setup=make_onroad_setup(pm)))
  add(int(FPS * 1.5), DummyEvent())  # wait for transition

  # === Onroad with sidebar (click onroad to toggle) ===
  add(0, DummyEvent(click_pos=(1000, 500)))
  hold()

  # === Onroad alerts ===
  # Small alert
  add(0, DummyEvent(setup=make_alert_setup(pm, AlertSize.small, "Small Alert", "This is a small alert", AlertStatus.normal)))
  hold()

  # Medium alert
  add(0, DummyEvent(setup=make_alert_setup(pm, AlertSize.mid, "Medium Alert", "This is a medium alert", AlertStatus.userPrompt)))
  hold()

  # Full alert
  add(0, DummyEvent(setup=make_alert_setup(pm, AlertSize.full, "DISENGAGE IMMEDIATELY", "Driver Distracted", AlertStatus.critical)))
  hold()

  # Full alert multiline
  add(0, DummyEvent(setup=make_alert_setup(pm, AlertSize.full, "Reverse\nGear", "", AlertStatus.normal)))
  hold()

  # Full alert long text
  add(0, DummyEvent(setup=make_alert_setup(pm, AlertSize.full, "TAKE CONTROL IMMEDIATELY",
                     "Calibration Invalid: Remount Device & Recalibrate", AlertStatus.userPrompt)))
  hold()

  # End
  add(0, DummyEvent())

  return script
