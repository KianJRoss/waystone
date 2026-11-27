"""Repository-wide pytest setup.

Waystone draws its own controls, so it always runs Qt Quick Controls' "Basic" style (app.py sets it at startup).
Tests that build QML directly must match: on Windows Qt otherwise picks the native "Windows" style, which rejects
custom backgrounds/content and floods every view with "style does not support customization" warnings.
"""
import os

os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
