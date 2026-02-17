#!/usr/bin/env python3
import os

import time
import argparse
import coverage
import pyray as rl

from typing import Literal
from collections.abc import Callable
from cereal.messaging import PubMaster
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.ui.tests.diff.diff import DIFF_OUT_DIR
from openpilot.system.version import terms_version, training_version

LayoutVariant = Literal["mici", "tizi"]

FPS = 60
HEADLESS = os.getenv("WINDOWED", "0") != "1"



def setup_state():
  params = Params()
  params.put("HasAcceptedTerms", terms_version)
  params.put("CompletedTrainingVersion", training_version)
  params.put("DongleId", "test123456789")
  params.put("UpdaterCurrentDescription", "0.10.1 / test-branch / abc1234 / Nov 30")


def run_replay(variant: LayoutVariant) -> None:
  from openpilot.selfdrive.ui.ui_state import ui_state  # Import within OpenpilotPrefix context so param values are setup correctly
  from openpilot.system.ui.lib.application import gui_app  # Import here for accurate coverage
  from openpilot.selfdrive.ui.tests.diff.replay_script import build_script

  setup_state()
  os.makedirs(DIFF_OUT_DIR, exist_ok=True)

  if HEADLESS:
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  gui_app.init_window("ui diff test", fps=FPS)

  # Log GL renderer/vendor/version to help triage CI vs local differences.
  def _log_gl_info():
    import ctypes
    import ctypes.util
    import subprocess

    GL_RENDERER = 0x1F01
    GL_VENDOR = 0x1F00
    GL_VERSION = 0x1F02

    def _try_lib(libname):
      try:
        lib = ctypes.CDLL(libname)
        try:
          glGetString = lib.glGetString
          glGetString.restype = ctypes.c_char_p
          glGetString.argtypes = [ctypes.c_uint]
          renderer = glGetString(GL_RENDERER)
          vendor = glGetString(GL_VENDOR)
          version = glGetString(GL_VERSION)
          return (
            renderer.decode('utf-8', 'replace') if renderer else '',
            vendor.decode('utf-8', 'replace') if vendor else '',
            version.decode('utf-8', 'replace') if version else '',
          )
        except AttributeError:
          return None
      except OSError:
        return None

    # Attempt to find common GL libraries
    candidates = []
    found = ctypes.util.find_library('GL')
    if found:
      candidates.append(found)
    candidates.extend(['libGL.so.1', 'libGLESv2.so.2', 'libEGL.so.1'])

    for c in candidates:
      res = _try_lib(c)
      if res:
        renderer, vendor, version = res
        print(f"GL Renderer: {renderer}")
        print(f"GL Vendor: {vendor}")
        print(f"GL Version: {version}")
        return

    # Fallback: try glxinfo if available
    try:
      out = subprocess.run(['glxinfo'], capture_output=True, text=True, check=True).stdout
      for line in out.splitlines():
        line = line.strip()
        if line.startswith('OpenGL renderer string:'):
          print(line)
        if line.startswith('OpenGL vendor string:'):
          print(line)
        if line.startswith('OpenGL version string:'):
          print(line)
      return
    except Exception:
      pass

    # Last resort: print envvars that affect GL selection
    print('Could not query GL info via libs or glxinfo')
    print(f"LIBGL_ALWAYS_SOFTWARE={os.environ.get('LIBGL_ALWAYS_SOFTWARE')}")

  _log_gl_info()

  # Dynamically import main layout based on variant
  if variant == "mici":
    from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout as MainLayout
  else:
    from openpilot.selfdrive.ui.layouts.main import MainLayout
  main_layout = MainLayout()
  main_layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

  pm = PubMaster(["deviceState", "pandaStates", "driverStateV2", "selfdriveState"])
  script = build_script(pm, main_layout, variant)
  script_index = 0

  send_fn: Callable | None = None
  frame = 0
  # Override timing functions to return deterministic values based on frame count instead of real time.
  # Many UI widgets use time.monotonic() directly (home layout refresh, alert timing, device interaction
  # timeout, etc.), so it must also be patched alongside the raylib timing functions.
  # _original_monotonic = time.monotonic
  # time.monotonic = lambda: frame / FPS
  rl.get_frame_time = lambda: 1.0 / FPS
  rl.get_time = lambda: frame / FPS

  # Main loop to replay events and render frames
  for should_render in gui_app.render():
    # Handle all events for the current frame
    while script_index < len(script) and script[script_index][0] == frame:
      _, event = script[script_index]
      # Call setup function, if any
      if event.setup:
        event.setup()
      # Send mouse events to the application
      if event.mouse_events:
        with gui_app._mouse._lock:
          gui_app._mouse._events.extend(event.mouse_events)
      # Update persistent send function
      if event.send_fn is not None:
        send_fn = event.send_fn
      # Move to next script event
      script_index += 1

    # Keep sending cereal messages for persistent states (onroad, alerts)
    if send_fn:
      send_fn()

    ui_state.update()

    if should_render:
      main_layout.render()

    frame += 1

    if script_index >= len(script):
      break

  time.monotonic = _original_monotonic
  gui_app.close()

  print(f"Total frames: {frame}")
  print(f"Video saved to: {os.environ['RECORD_OUTPUT']}")


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--big', action='store_true', help='Use big UI layout (tizi/tici) instead of mici layout')
  args = parser.parse_args()

  variant: LayoutVariant = 'tizi' if args.big else 'mici'

  if args.big:
    os.environ["BIG"] = "1"
  os.environ["RECORD"] = "1"
  os.environ["RECORD_OUTPUT"] = os.path.join(DIFF_OUT_DIR, os.environ.get("RECORD_OUTPUT", f"{variant}_ui_replay.mp4"))
  os.environ["RECORD_DETERMINISTIC"] = "1"

  print(f"Running {variant} UI replay...")
  with OpenpilotPrefix():
    sources = ["openpilot.system.ui"]
    if variant == "mici":
      sources.append("openpilot.selfdrive.ui.mici")
      omit = ["**/*tizi*", "**/*tici*"]  # exclude files containing "tizi" or "tici"
    else:
      sources.extend(["openpilot.selfdrive.ui.layouts", "openpilot.selfdrive.ui.onroad", "openpilot.selfdrive.ui.widgets"])
      omit = ["**/*mici*"]  # exclude files containing "mici"
    cov = coverage.Coverage(source=sources, omit=omit)
    with cov.collect():
      run_replay(variant)
    cov.save()
    cov.report()
    directory = os.path.join(DIFF_OUT_DIR, f"htmlcov-{variant}")
    cov.html_report(directory=directory)
    print(f"HTML report: {directory}/index.html")


if __name__ == "__main__":
  main()
