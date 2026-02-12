#!/usr/bin/env python3
"""
UI introspection layer for the mici UI.

Walks the live widget tree rooted at MiciMainLayout and produces a structured
description of the current screen state — what's visible, what's interactive,
and what the available actions are.

The output is designed to be consumed by an AI agent (via the MCP server) so it
can intelligently decide how to explore the UI for maximum code coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openpilot.system.ui.widgets import Widget, NavWidget
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.widgets.toggle import Toggle
from openpilot.system.ui.lib.application import gui_app

# Lazy imports to avoid circular deps — resolved at call time
_MICI_BUTTON_CLASSES: dict[str, type] | None = None
_DIALOG_CLASSES: dict[str, type] | None = None


def _ensure_imports():
  global _MICI_BUTTON_CLASSES, _DIALOG_CLASSES
  if _MICI_BUTTON_CLASSES is not None:
    return
  from openpilot.selfdrive.ui.mici.widgets.button import (
    BigButton, BigToggle, BigMultiToggle, BigParamControl,
    BigCircleButton, BigCircleToggle, BigCircleParamControl, BigMultiParamToggle,
  )
  from openpilot.selfdrive.ui.mici.widgets.dialog import (
    BigDialogBase, BigDialog, BigConfirmationDialogV2, BigInputDialog,
  )
  # Order matters! Check subclasses before base classes (all inherit from BigButton)
  _MICI_BUTTON_CLASSES = {
    'BigMultiParamToggle': BigMultiParamToggle,
    'BigCircleParamControl': BigCircleParamControl,
    'BigCircleToggle': BigCircleToggle,
    'BigCircleButton': BigCircleButton,
    'BigParamControl': BigParamControl,
    'BigMultiToggle': BigMultiToggle,
    'BigToggle': BigToggle,
    'BigButton': BigButton,  # must be last — base class
  }
  _DIALOG_CLASSES = {
    'BigDialogBase': BigDialogBase,
    'BigDialog': BigDialog,
    'BigConfirmationDialogV2': BigConfirmationDialogV2,
    'BigInputDialog': BigInputDialog,
  }


# ---------------------------------------------------------------------------
# Widget info dataclass — structured representation of a single widget
# ---------------------------------------------------------------------------

@dataclass
class WidgetInfo:
  """Structured info about a single widget in the tree."""
  widget_type: str         # e.g. "BigToggle", "Scroller", "NavWidget"
  class_name: str          # actual Python class name
  # Position
  x: int = 0
  y: int = 0
  width: int = 0
  height: int = 0
  # State
  visible: bool = True
  enabled: bool = True
  pressed: bool = False
  # Content
  text: str = ""
  value: str = ""
  checked: bool | None = None       # for toggles
  options: list[str] | None = None  # for multi-toggles
  param: str = ""                   # for param-backed widgets
  # Interaction hints
  clickable: bool = False
  scrollable: bool = False
  scroll_horizontal: bool = False
  swipe_to_dismiss: bool = False     # NavWidget
  has_back: bool = False
  # Children
  children: list[WidgetInfo] = field(default_factory=list)
  # Identification
  attr_name: str = ""    # attribute name on parent (e.g. "_home_layout")
  depth: int = 0

  def center(self) -> tuple[int, int]:
    """Return center coordinates of this widget."""
    return (self.x + self.width // 2, self.y + self.height // 2)

  def is_on_screen(self, screen_w: int, screen_h: int) -> bool:
    """Check if widget is at least partially visible on screen and has non-zero size."""
    return (self.width > 0 and self.height > 0 and
            self.x + self.width > 0 and self.x < screen_w and
            self.y + self.height > 0 and self.y < screen_h)


# ---------------------------------------------------------------------------
# Tree walker
# ---------------------------------------------------------------------------

def _get_widget_type(widget: Widget) -> str:
  """Classify a widget into a high-level type string."""
  _ensure_imports()
  cls = type(widget)
  name = cls.__name__

  # Check specific mici types first
  for type_name, type_cls in _MICI_BUTTON_CLASSES.items():
    if isinstance(widget, type_cls):
      return type_name

  for type_name, type_cls in _DIALOG_CLASSES.items():
    if isinstance(widget, type_cls):
      return type_name

  if isinstance(widget, Scroller):
    return "Scroller"
  if isinstance(widget, Toggle):
    return "Toggle"
  if isinstance(widget, NavWidget):
    return "NavWidget"

  return name


def _extract_widget_info(widget: Widget, attr_name: str = "", depth: int = 0) -> WidgetInfo:
  """Extract structured info from a single widget (non-recursive)."""
  _ensure_imports()

  rect = widget.rect
  info = WidgetInfo(
    widget_type=_get_widget_type(widget),
    class_name=type(widget).__name__,
    x=int(rect.x),
    y=int(rect.y),
    width=int(rect.width),
    height=int(rect.height),
    visible=widget.is_visible,
    enabled=widget.enabled,
    pressed=widget.is_pressed,
    attr_name=attr_name,
    depth=depth,
  )

  # Clickable if it has a click callback or handles releases
  info.clickable = (widget._click_callback is not None or
                    type(widget)._handle_mouse_release is not Widget._handle_mouse_release)

  # NavWidget properties
  if isinstance(widget, NavWidget):
    info.swipe_to_dismiss = True
    info.has_back = widget.back_enabled

  # Scroller properties
  if isinstance(widget, Scroller):
    info.scrollable = True
    info.scroll_horizontal = widget._horizontal

  # Toggle properties
  if isinstance(widget, Toggle):
    info.checked = widget.get_state()
    info.clickable = True

  # Mici button properties (text, value, checked, param)
  from openpilot.selfdrive.ui.mici.widgets.button import (
    BigButton, BigToggle, BigMultiToggle, BigParamControl,
    BigCircleToggle, BigCircleParamControl, BigMultiParamToggle,
  )

  if isinstance(widget, BigButton):
    info.text = widget.text or ""
    info.value = widget.value or ""
    info.clickable = True

  if isinstance(widget, BigToggle):
    info.checked = widget._checked

  if isinstance(widget, BigMultiToggle):
    info.options = list(widget._options)
    info.value = widget.value

  if isinstance(widget, BigParamControl):
    info.param = widget.param

  if isinstance(widget, BigMultiParamToggle):
    info.param = widget._param

  if isinstance(widget, BigCircleToggle):
    info.checked = widget._checked
    info.clickable = True

  if isinstance(widget, BigCircleParamControl):
    info.param = widget._param

  return info


def _iter_widget_children(widget: Widget) -> list[tuple[str, Widget]]:
  """
  Yield (attr_name, child_widget) for all child widgets of a given widget.
  Handles the various storage patterns used across the codebase.
  """
  seen_ids: set[int] = set()
  children: list[tuple[str, Widget]] = []

  # Special case: Scroller has _items list
  if isinstance(widget, Scroller):
    for i, item in enumerate(widget._items):
      if id(item) not in seen_ids:
        seen_ids.add(id(item))
        children.append((f"_items[{i}]", item))
    return children

  # Walk instance attributes
  for attr_name, attr_val in vars(widget).items():
    if attr_name.startswith('__'):
      continue

    if isinstance(attr_val, Widget) and id(attr_val) not in seen_ids:
      seen_ids.add(id(attr_val))
      children.append((attr_name, attr_val))

    elif isinstance(attr_val, list):
      for i, item in enumerate(attr_val):
        if isinstance(item, Widget) and id(item) not in seen_ids:
          seen_ids.add(id(item))
          children.append((f"{attr_name}[{i}]", item))

    elif isinstance(attr_val, dict):
      for k, v in attr_val.items():
        if isinstance(v, Widget) and id(v) not in seen_ids:
          seen_ids.add(id(v))
          children.append((f"{attr_name}[{k}]", v))
        # PanelInfo pattern in SettingsLayout
        elif hasattr(v, 'instance') and isinstance(getattr(v, 'instance', None), Widget):
          inst = v.instance
          if id(inst) not in seen_ids:
            seen_ids.add(id(inst))
            children.append((f"{attr_name}[{k}].instance", inst))

  return children


def walk_widget_tree(widget: Widget, attr_name: str = "root", depth: int = 0,
                     max_depth: int = 15) -> WidgetInfo:
  """
  Recursively walk the widget tree and build a WidgetInfo tree.
  """
  info = _extract_widget_info(widget, attr_name, depth)

  if depth < max_depth:
    for child_name, child_widget in _iter_widget_children(widget):
      child_info = walk_widget_tree(child_widget, child_name, depth + 1, max_depth)
      info.children.append(child_info)

  return info


# ---------------------------------------------------------------------------
# Screen state — the full picture including modal overlay
# ---------------------------------------------------------------------------

@dataclass
class ScreenState:
  """Complete state of the UI at a given frame."""
  frame: int
  screen_width: int
  screen_height: int
  widget_tree: WidgetInfo
  modal_overlay: WidgetInfo | None = None
  has_modal: bool = False

  def get_interactive_widgets(self) -> list[WidgetInfo]:
    """Return all visible, enabled, interactive widgets on screen."""
    result = []
    tree = self.modal_overlay if self.has_modal else self.widget_tree
    self._collect_interactive(tree, result)
    return result

  def _collect_interactive(self, info: WidgetInfo, result: list[WidgetInfo]):
    if not info.visible:
      return
    # Prune subtrees under zero-size parent widgets (not rendered/laid out)
    if info.width <= 0 and info.height <= 0 and info.depth > 0:
      return
    if (info.clickable or info.scrollable or info.swipe_to_dismiss) and info.enabled:
      if info.is_on_screen(self.screen_width, self.screen_height):
        result.append(info)
    for child in info.children:
      self._collect_interactive(child, result)

  def get_all_visible(self) -> list[WidgetInfo]:
    """Return all visible widgets."""
    result = []
    tree = self.modal_overlay if self.has_modal else self.widget_tree
    self._collect_visible(tree, result)
    return result

  def _collect_visible(self, info: WidgetInfo, result: list[WidgetInfo]):
    if not info.visible:
      return
    if info.width <= 0 and info.height <= 0 and info.depth > 0:
      return
    if info.is_on_screen(self.screen_width, self.screen_height):
      result.append(info)
    for child in info.children:
      self._collect_visible(child, result)


def capture_screen_state(main_layout: Widget) -> ScreenState:
  """Capture the current state of the entire UI."""
  widget_tree = walk_widget_tree(main_layout)

  modal_info = None
  has_modal = False
  if gui_app._modal_overlay.overlay is not None:
    overlay = gui_app._modal_overlay.overlay
    if isinstance(overlay, Widget):
      modal_info = walk_widget_tree(overlay, "modal_overlay")
      has_modal = True

  return ScreenState(
    frame=gui_app.frame,
    screen_width=gui_app.width,
    screen_height=gui_app.height,
    widget_tree=widget_tree,
    modal_overlay=modal_info,
    has_modal=has_modal,
  )


# ---------------------------------------------------------------------------
# Markdown rendering — human/AI readable screen description
# ---------------------------------------------------------------------------

def widget_info_to_markdown(info: WidgetInfo, indent: int = 0, show_hidden: bool = False) -> str:
  """Render a WidgetInfo tree as readable markdown."""
  if not info.visible and not show_hidden:
    return ""

  lines = []
  pad = "  " * indent

  # Build the widget label
  label_parts = [info.widget_type]
  if info.text:
    label_parts.append(f'"{info.text}"')
  if info.value:
    label_parts.append(f'value="{info.value}"')
  if info.checked is not None:
    label_parts.append(f'checked={"✓" if info.checked else "✗"}')
  if info.param:
    label_parts.append(f'param={info.param}')
  if info.options:
    label_parts.append(f'options={info.options}')

  # State badges
  badges = []
  if not info.visible:
    badges.append("HIDDEN")
  if not info.enabled:
    badges.append("DISABLED")
  if info.clickable and info.enabled and info.visible:
    badges.append("🖱 clickable")
  if info.scrollable:
    direction = "horizontal" if info.scroll_horizontal else "vertical"
    badges.append(f"📜 scroll-{direction}")
  if info.swipe_to_dismiss and info.has_back:
    badges.append("👇 swipe-to-dismiss")

  badge_str = f" [{', '.join(badges)}]" if badges else ""

  # Position
  pos_str = f"({info.x},{info.y} {info.width}×{info.height})"

  lines.append(f"{pad}- **{' '.join(label_parts)}** {pos_str}{badge_str}")

  # Recurse into visible children
  for child in info.children:
    child_md = widget_info_to_markdown(child, indent + 1, show_hidden)
    if child_md:
      lines.append(child_md)

  return "\n".join(lines)


def screen_state_to_markdown(state: ScreenState) -> str:
  """Render a complete screen state as markdown for the AI agent."""
  lines = [
    f"# UI State — Frame {state.frame}",
    f"Screen: {state.screen_width}×{state.screen_height}",
    "",
  ]

  if state.has_modal:
    lines.append("## ⚠ Modal Overlay Active")
    lines.append("*Main UI is blocked. Interact with modal or dismiss it.*")
    lines.append("")
    lines.append(widget_info_to_markdown(state.modal_overlay))
    lines.append("")
    lines.append("---")
    lines.append("## Main UI (behind modal)")

  lines.append(widget_info_to_markdown(state.widget_tree))

  # Interactive elements summary
  interactive = state.get_interactive_widgets()
  lines.append("")
  lines.append(f"## Interactive Elements ({len(interactive)} available)")
  lines.append("")
  for i, w in enumerate(interactive):
    cx, cy = w.center()
    desc = w.widget_type
    if w.text:
      desc += f' "{w.text}"'
    if w.checked is not None:
      desc += f" [{'ON' if w.checked else 'OFF'}]"
    actions = []
    if w.clickable:
      actions.append(f"click({cx},{cy})")
    if w.scrollable:
      d = "horizontal" if w.scroll_horizontal else "vertical"
      actions.append(f"scroll_{d}")
    if w.swipe_to_dismiss:
      actions.append("swipe_down_to_dismiss")
    lines.append(f"  {i+1}. {desc} → {', '.join(actions)}")

  return "\n".join(lines)
