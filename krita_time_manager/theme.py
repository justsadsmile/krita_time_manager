"""Palette-derived colors for the Krita Time Manager docker.

Every color is derived from the current widget palette, so the docker follows
the active Krita theme, including custom themes.
"""

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QWidget


PANEL_DARKEN = 115
RING_TRACK_LIGHTEN = 125
RING_HEAD_DARKEN = 120
RING_TRACK_MIX = 15
MIN_CONTRAST = 80
CONTRAST_STEPS = 10
CONTRAST_FACTOR = 115
STATE_SATURATION_MIN = 150

HUE_SUCCESS = 140
HUE_WARNING = 45
HUE_DANGER = 0


def _mix(first: QColor, second: QColor, percent: int) -> QColor:
    """Mix colors in RGB: percent of the first, remainder of the second."""
    ratio = percent / 100
    return QColor(
        round(first.red() * ratio + second.red() * (1 - ratio)),
        round(first.green() * ratio + second.green() * (1 - ratio)),
        round(first.blue() * ratio + second.blue() * (1 - ratio)),
    )


def _ensure_contrast(color: QColor, background: QColor) -> QColor:
    """Lighten or darken the color until it stands out from the background."""
    lighten = background.lightness() < 128
    result = QColor(color)
    for _ in range(CONTRAST_STEPS):
        if abs(result.lightness() - background.lightness()) >= MIN_CONTRAST:
            break
        if lighten:
            result = result.lighter(CONTRAST_FACTOR)
        else:
            result = result.darker(CONTRAST_FACTOR)
    return result


def _state_color(widget: QWidget, hue: int) -> QColor:
    """Shade the semantic hue with the theme saturation and text brightness."""
    palette = widget.palette()
    _, saturation, _, alpha = accent(widget).getHsv()
    _, _, value, _ = palette.color(QPalette.WindowText).getHsv()
    color = QColor()
    color.setHsv(hue, max(saturation, STATE_SATURATION_MIN), value, alpha)
    return _ensure_contrast(color, palette.color(QPalette.Window))


def accent(widget: QWidget) -> QColor:
    """Theme accent color (selection/highlight)."""
    return widget.palette().color(QPalette.Highlight)


def success(widget: QWidget) -> QColor:
    """Green-tinted theme color."""
    return _state_color(widget, HUE_SUCCESS)


def warning(widget: QWidget) -> QColor:
    """Amber-tinted theme color."""
    return _state_color(widget, HUE_WARNING)


def danger(widget: QWidget) -> QColor:
    """Red-tinted theme color."""
    return _state_color(widget, HUE_DANGER)


def ring_track(widget: QWidget) -> QColor:
    """Ring background, a subtle step away from the panel color."""
    return widget.palette().color(QPalette.Window).lighter(RING_TRACK_LIGHTEN)


def ring_state_track(widget: QWidget, color: QColor) -> QColor:
    """Muted state color, so the head stays the brightest element."""
    return _mix(color, widget.palette().color(QPalette.Window), RING_TRACK_MIX)


def ring_dot(widget: QWidget) -> QColor:
    """Dot color with contrast against the ring track."""
    return widget.palette().color(QPalette.WindowText).darker(RING_HEAD_DARKEN)


def ring_state_head(widget: QWidget, color: QColor) -> QColor:
    """Full-strength state color for the brightest ring element."""
    return _ensure_contrast(color, widget.palette().color(QPalette.Window))


def inset_palette(palette: QPalette) -> QPalette:
    """Copy the palette with a slightly darker window color for tab pages."""
    result = QPalette(palette)
    result.setColor(
        QPalette.Window,
        palette.color(QPalette.Window).darker(PANEL_DARKEN),
    )
    return result
