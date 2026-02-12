#!/usr/bin/env python3
"""Quick test for introspect.py — run headless UI and dump screen state."""
import os
os.environ["RECORD"] = "0"

import pyray as rl
from openpilot.common.params import Params
from openpilot.system.version import terms_version, training_version
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.tests.diff.introspect import (
    capture_screen_state, screen_state_to_markdown, walk_widget_tree,
)

# Setup
Params().put("HasAcceptedTerms", terms_version)
Params().put("CompletedTrainingVersion", training_version)
Params().put("DongleId", "test123456789")
Params().put_bool("OpenpilotEnabledToggle", True)

rl.set_config_flags(rl.FLAG_WINDOW_HIDDEN)
gui_app.init_window("introspect test", fps=60)
layout = MiciMainLayout()
layout.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

# Pump a few frames so widgets lay out
gen = gui_app.render()
for i in range(30):
    if next(gen):
        ui_state.update()
        layout.render()

# --- Test introspection ---
state = capture_screen_state(layout)
print(screen_state_to_markdown(state))
print(f"\nInteractive widgets: {len(state.get_interactive_widgets())}")
print(f"All visible: {len(state.get_all_visible())}")
print(f"Has modal: {state.has_modal}")

# Test widget tree depth
tree = walk_widget_tree(layout)
def count_nodes(info):
    return 1 + sum(count_nodes(c) for c in info.children)
print(f"Total widget tree nodes: {count_nodes(tree)}")

gui_app.close()
print("\n✓ introspect.py works")