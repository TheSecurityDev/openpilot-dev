import pyray as rl
from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.widget import Widget


class ExperimentalModeButton(Widget):
  def __init__(self):
    super().__init__()

    self.img_width = 80
    self.horizontal_padding = 50
    self.button_height = 125
    self.rounded_corners = 0.20
    self.background_color = rl.Color(255, 0, 0, 150) # Color of the background behind the button

    self.params = Params()
    self.experimental_mode = self.params.get_bool("ExperimentalMode")
    self.is_pressed = False

    self.chill_pixmap = gui_app.texture("icons/couch.png", self.img_width, self.img_width)
    self.experimental_pixmap = gui_app.texture("icons/experimental_grey.png", self.img_width, self.img_width)

  def _get_gradient_colors(self):
    alpha = 0xCC if self.is_pressed else 0xFF

    if self.experimental_mode:
      return rl.Color(255, 155, 63, alpha), rl.Color(219, 56, 34, alpha)
    else:
      return rl.Color(20, 255, 171, alpha), rl.Color(35, 149, 255, alpha)

  def _draw_gradient_background(self, rect):
    start_color, end_color = self._get_gradient_colors()
    rl.draw_rectangle_gradient_h(int(rect.x), int(rect.y), int(rect.width), int(rect.height),
                                 start_color, end_color)

  def _handle_interaction(self, rect):
    mouse_pos = rl.get_mouse_position()
    mouse_in_rect = rl.check_collision_point_rec(mouse_pos, rect)

    self.is_pressed = mouse_in_rect and rl.is_mouse_button_down(rl.MOUSE_BUTTON_LEFT)
    return mouse_in_rect and rl.is_mouse_button_released(rl.MOUSE_BUTTON_LEFT)

  def _render(self, rect):
    if self._handle_interaction(rect):
      self.experimental_mode = not self.experimental_mode
      # TODO: Opening settings for ExperimentalMode
      self.params.put_bool("ExperimentalMode", self.experimental_mode)

    rl.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
    self._draw_gradient_background(rect)
    rl.end_scissor_mode()

    # HACK: Hide the square corners of the rectangle by drawing a thick rounded rectangle border the same color as the background
    rl.draw_rectangle_rounded_lines_ex(rect, self.rounded_corners, 20, 5, self.background_color)

    # Draw vertical separator line
    line_x = rect.x + rect.width - self.img_width - (2 * self.horizontal_padding)
    separator_color = rl.Color(0, 0, 0, 77)  # 0x4d = 77
    rl.draw_line_ex(rl.Vector2(line_x, rect.y), rl.Vector2(line_x, rect.y + rect.height), 3, separator_color)

    # Draw text label (left aligned)
    text = "EXPERIMENTAL MODE ON" if self.experimental_mode else "CHILL MODE ON"
    text_x = rect.x + self.horizontal_padding
    text_y = rect.y + rect.height / 2 - 45 // 2  # Center vertically

    rl.draw_text_ex(gui_app.font(FontWeight.NORMAL), text, rl.Vector2(int(text_x), int(text_y)), 45, 0, rl.Color(0, 0, 0, 255))

    # Draw icon (right aligned)
    icon_x = rect.x + rect.width - self.horizontal_padding - self.img_width
    icon_y = rect.y + (rect.height - self.img_width) / 2
    icon_rect = rl.Rectangle(icon_x, icon_y, self.img_width, self.img_width)

    # Draw current mode icon
    current_icon = self.experimental_pixmap if self.experimental_mode else self.chill_pixmap
    source_rect = rl.Rectangle(0, 0, current_icon.width, current_icon.height)
    rl.draw_texture_pro(current_icon, source_rect, icon_rect, rl.Vector2(0, 0), 0, rl.Color(255, 255, 255, 255))
