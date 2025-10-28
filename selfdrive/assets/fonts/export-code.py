#!/usr/bin/env python3
import os


def main():
  os.environ["EXPORT_FONTS_AS_C"] = "1"
  from openpilot.system.ui.lib.application import gui_app

  gui_app.init_window("Exporting fonts")
  for _ in gui_app.render():
    pass


if __name__ == "__main__":
  main()
