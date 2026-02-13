"""Mici UI replay script — defines the frame-by-frame test scenario."""
from openpilot.selfdrive.ui.tests.diff.replay import DummyEvent, FPS
from openpilot.system.ui.lib.application import gui_app


def build_script(main_layout):
  t = 0
  script = []

  def add(dt, event):
    nonlocal t
    t += dt
    script.append((t, event))

  w, h = gui_app.width, gui_app.height

  # === Homescreen (clean) ===
  add(0, DummyEvent())
  add(FPS, DummyEvent(click_pos=(w // 2, h // 2)))
  add(FPS, DummyEvent(click_pos=(w // 2, h // 2)))
  add(FPS, DummyEvent())

  return script
