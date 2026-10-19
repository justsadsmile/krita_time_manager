"""Krita Time Manager: docker with clock, stopwatch and countdown timer.

Structure:
    Constants -> Enums -> Helper -> Tab classes (Clock / Stopwatch / Timer)
    -> TimeManager docker -> plugin registration.
"""

import math
import time
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import Optional

from PyQt5 import uic
from PyQt5.QtCore import QDate, QEvent, QLocale, QObject, QPointF, QRectF, QSize, Qt, QTime, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita

from . import theme


# --- Constants ---

CLOCK_UPDATE_MS = 1000 # MUST stay 1000 !!! DO NOT MAKE CHANGES WITHOUT MODIFYING class ClockTab:
UI_TICK_MS = 100
TIME_FORMAT = "hh:mm:ss"
DATE_FORMAT = "dddd, dd MMMM yyyy"
ICON_PLAY = "animation_play"
ICON_PAUSE = "animation_pause"
ICON_RESET = "reload-preset"
CLOCK_FONT_MIN = 14
CLOCK_FONT_MAX = 60
CLOCK_FONT_SIDE_PAD = 32
CLOCK_FONT_V_PAD = 4
TIMER_DEFAULT_S = 300
TIMER_MAX_S = 99 * 3600 + 59 * 60 + 59
TIMER_PROGRESS_HEIGHT = 12

SWEEP_PERIOD_S = 4.0
RING_UPDATE_MS = 16
RING_EDGE_PAD = 4
RING_TEXT_PAD = 20
RING_PEN_RATIO = 0.05
RING_PEN_MIN = 5
RING_PEN_MAX = 12
RING_DOT_RATIO = 1.9


# --- Enums ---

class StopwatchState(Enum):
    STOPPED = auto()
    RUNNING = auto()
    PAUSED = auto()


class TimerState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    FINISHED = auto()


# --- Helpers ---

def format_seconds(total: float) -> str:
    """Seconds -> "hh:mm:ss" string."""
    total = int(total)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)


def timer_digits_to_seconds(digits: str) -> int:
    """Positional "hhmmss" digits -> seconds, carrying overflow upwards."""
    return int(digits[0:2]) * 3600 + int(digits[2:4]) * 60 + int(digits[4:6])


def seconds_to_timer_digits(total: int) -> str:
    """Seconds -> positional 6-digit "hhmmss" string (the edit seed)."""
    return "{:02d}{:02d}{:02d}".format(
        total // 3600, total % 3600 // 60, total % 60
    )


def format_elapsed(total: float) -> str:
    """Seconds -> progressive "h:mm:ss.t" / "m:ss.t" / "s.t" string

    5.9 -> 59.9 -> 1:00.0
    """
    tenths = int(total * 10) 
    hours, rest = divmod(tenths, 36000)
    minutes, rest = divmod(rest, 600)
    seconds, tenth = divmod(rest, 10)
    if hours:
        return "{}:{:02d}:{:02d}.{}".format(hours, minutes, seconds, tenth)
    if minutes:
        return "{}:{:02d}.{}".format(minutes, seconds, tenth)
    return "{}.{}".format(seconds, tenth)


def style_panels(ui: QWidget) -> None:
    """Give each tab page an inset "window" look (theme-aware)."""
    palette = ui.palette()
    for page in (ui.clockTab, ui.stopwatchTab, ui.timerTab):
        page.setPalette(theme.inset_palette(palette))
        page.setAutoFillBackground(True)


# --- Sweep ring ---

class SweepRing(QWidget):
    """Decorative dot orbiting a ring; colors follow the Krita theme palette."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.angle = 0.0
        self.state_color: Optional[QColor] = None
        self.track_color: Optional[QColor] = None
        self.head_color: Optional[QColor] = None
        self.overlay: Optional[QWidget] = None
        self.overlay_ratio = 1.0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_angle(self, angle: float) -> None:
        """Place the dot at the given angle: 0 = top, growing clockwise."""
        self.angle = angle % 360.0
        self.update()

    def set_state_color(self, color: Optional[QColor]) -> None:
        """Tint the ring with a state color; None restores the theme colors."""
        self.state_color = color
        if color is None:
            self.track_color = None
            self.head_color = None
        else:
            self.track_color = theme.ring_state_track(self, color)
            self.head_color = theme.ring_state_head(self, color)
        self.update()

    def set_overlay(self, widget: QWidget, ratio: float = 1.0) -> None:
        """Center widget over the ring; side = ratio of the drawn diameter."""
        self.overlay = widget
        self.overlay_ratio = ratio
        self.place_overlay()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.place_overlay()

    def place_overlay(self) -> None:
        if self.overlay is None:
            return
        diameter = min(self.width(), self.height()) - 2 * RING_EDGE_PAD
        side = max(0, int(diameter * self.overlay_ratio))
        self.overlay.setGeometry(
            (self.width() - side) // 2,
            (self.height() - side) // 2,
            side,
            side,
        )
        self.overlay.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        diameter = min(self.width(), self.height()) - 2 * RING_EDGE_PAD
        if diameter <= 0:
            return

        pen_width = min(
            RING_PEN_MAX,
            max(RING_PEN_MIN, diameter * RING_PEN_RATIO),
        )
        radius = (diameter - pen_width) / 2
        cx = self.width() / 2
        cy = self.height() / 2

        track_color = (
            theme.ring_track(self)
            if self.track_color is None
            else self.track_color
        )
        head_color = (
            theme.ring_dot(self)
            if self.head_color is None
            else self.head_color
        )

        painter.setPen(QPen(track_color, pen_width))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        radians = math.radians(self.angle)
        dot_radius = pen_width * RING_DOT_RATIO / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(head_color)
        painter.drawEllipse(
            QPointF(
                cx + radius * math.sin(radians),
                cy - radius * math.cos(radians),
            ),
            dot_radius,
            dot_radius,
        )


# --- Progress bar ---

class StateProgress(QWidget):
    """Rounded progress bar tinted by the timer state."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.fraction = 1.0
        self.finished = False
        self.track_color = theme.ring_track(self)
        self.chunk_color = theme.accent(self)
        self.setFixedHeight(TIMER_PROGRESS_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_fraction(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        self.update()

    def set_state(self, color: Optional[QColor], finished: bool = False) -> None:
        """Recolor the bar; computed only on state and theme changes."""
        self.finished = finished
        if color is None:
            self.track_color = theme.ring_track(self)
            self.chunk_color = theme.accent(self)
        else:
            self.track_color = theme.ring_state_track(self, color)
            self.chunk_color = theme.ring_state_head(self, color)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.track_color)
        painter.drawRoundedRect(rect, radius, radius)

        fraction = 1.0 if self.finished else self.fraction
        if fraction <= 0:
            return
        chunk = QRectF(rect)
        chunk.setWidth(rect.width() * fraction)
        chunk_radius = min(radius, chunk.width() / 2)
        painter.setBrush(self.chunk_color)
        painter.drawRoundedRect(chunk, chunk_radius, chunk_radius)


# --- Clock tab ---

class ClockTab(QObject):
    """Shows the current time and date, aligned to the second boundary.
    
    A one-shot precise timer re-arms itself on every tick, computing the
    remaining time until the next .000 boundary, so the display never drifts.
    """
    #There are a lot of comments because I'm a little dumb

    def __init__(self, ui: QWidget) -> None:
        super().__init__(ui)
        self.ui = ui

        # Balance the bottom spacer so the clock block sits in the tab center
        self.ui.clockTabLayout.insertStretch(0)

        # Elastic clock: scale the font from the base size as the tab resizes
        self.clock_font = QFont(self.ui.clockLabel.font())
        self.clock_base_size = self.clock_font.pointSize()
        self.clock_current_size = self.clock_base_size
        self.ui.clockLabel.setMinimumSize(1, 1)
        self.ui.clockTab.installEventFilter(self)

        # Always format dates in English, regardless of system locale
        self.locale = QLocale(QLocale.English)

        # One-shot precise timer: each timeout re-arms the next tick
        self.tick_timer = QTimer(self.ui)
        self.tick_timer.setSingleShot(True)
        self.tick_timer.setTimerType(Qt.PreciseTimer)  # ~1 ms vs ~50 ms default
        self.tick_timer.timeout.connect(self.on_tick)

        # Align the first tick to the next second boundary and render immediately
        self.schedule_next_tick()
        self.update()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.ui.clockTab and event.type() == QEvent.Resize:
            self.fit_clock_font()
        return super().eventFilter(obj, event)

    def fit_clock_font(self) -> None:
        """Scale the clock font to the tab size, bounded and width-driven."""
        metrics = QFontMetrics(self.clock_font)
        text_width = metrics.horizontalAdvance(self.ui.clockLabel.text())
        text_height = metrics.height()
        if text_width <= 0 or text_height <= 0:
            return

        margins = self.ui.clockTabLayout.contentsMargins()
        spacing = self.ui.clockTabLayout.spacing()
        avail_width = (
            self.ui.clockTab.width()
            - margins.left() - margins.right() - 2 * CLOCK_FONT_SIDE_PAD
        )
        avail_height = (
            self.ui.clockTab.height()
            - margins.top() - margins.bottom() - 2 * CLOCK_FONT_V_PAD
            - self.ui.dateLabel.sizeHint().height() - 3 * spacing
        )
        if avail_width <= 0 or avail_height <= 0:
            return

        scale = min(avail_width / text_width, avail_height / text_height)
        point_size = max(
            CLOCK_FONT_MIN,
            min(CLOCK_FONT_MAX, int(self.clock_base_size * scale)),
        )
        if point_size == self.clock_current_size:
            return

        font = QFont(self.clock_font)
        font.setPointSize(point_size)
        self.ui.clockLabel.setFont(font)
        self.clock_current_size = point_size

    def on_tick(self) -> None:
        """Called once per second: refresh the display and schedule the next ring."""
        self.update()                # show the current time and date
        self.schedule_next_tick()    # queue the next ring

    def schedule_next_tick(self) -> None:
        """Start the timer so it rings exactly at the next second boundary."""
        # msec() is the milliseconds already elapsed in the current second
        # so CLOCK_UPDATE_MS - msec() is the delay until the next boundary
        delay_ms = CLOCK_UPDATE_MS - QTime.currentTime().msec()
        self.tick_timer.start(delay_ms)

    def update(self) -> None:
        """Write the current time and date into the labels."""
        self.ui.clockLabel.setText(QTime.currentTime().toString(TIME_FORMAT))
        self.ui.dateLabel.setText(self.locale.toString(QDate.currentDate(), DATE_FORMAT))


# --- Stopwatch tab ---

class StopwatchTab(QObject):
    """Counts time up. Start/Pause toggles, Reset clears."""

    def __init__(self, ui: QWidget) -> None:
        super().__init__(ui)
        self.ui = ui
        self.state = StopwatchState.STOPPED
        self.accumulated = 0.0
        self.started_at: Optional[float] = None

        self.ui.swStartButton.clicked.connect(self.on_start_clicked)
        self.ui.swResetButton.clicked.connect(self.on_reset_clicked)

        for button in (self.ui.swStartButton, self.ui.swResetButton):
            button.setText("")
            button.setIconSize(QSize(18, 18))
        self.ui.swResetButton.setIcon(Krita.instance().icon(ICON_RESET))

        self.tick_timer = QTimer(self.ui)
        self.tick_timer.timeout.connect(self.update_display)

        self.ring = SweepRing(self.ui.stopwatchTab)
        ring_layout = QVBoxLayout(self.ring)
        ring_layout.setContentsMargins(
            RING_TEXT_PAD, RING_TEXT_PAD, RING_TEXT_PAD, RING_TEXT_PAD
        )
        ring_layout.addWidget(self.ui.stopwatchLabel)
        label_size = self.ui.stopwatchLabel.sizeHint()
        side = max(label_size.width(), label_size.height()) + 2 * RING_TEXT_PAD
        self.ring.setMinimumSize(side, side)
        self.ui.stopwatchTabLayout.insertWidget(0, self.ring)
        self.ui.stopwatchTabLayout.setStretch(0, 1)

        # invis button
        self.ring_button = QPushButton(self.ring)
        self.ring_button.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self.ring_button.setFlat(True)
        self.ring_button.setCursor(Qt.PointingHandCursor)
        self.ring_button.setFocusPolicy(Qt.NoFocus)
        self.ring_button.clicked.connect(self.on_start_clicked)
        self.ring.set_overlay(self.ring_button, 1.0)

        self.ring_timer = QTimer(self.ui)
        self.ring_timer.timeout.connect(self.update_ring)

        self.update_buttons()
        self.update_display()
        self.update_label_color()

    def elapsed(self) -> float:
        """Elapsed seconds, computed from the monotonic clock."""
        if self.state == StopwatchState.RUNNING:
            return self.accumulated + (time.perf_counter() - self.started_at)
        return self.accumulated

    def on_start_clicked(self) -> None:
        if self.state == StopwatchState.RUNNING:
            self.accumulated = self.elapsed()
            self.started_at = None
            self.state = StopwatchState.PAUSED
        else:
            self.started_at = time.perf_counter()
            self.state = StopwatchState.RUNNING
        self.sync_tick_timer()
        self.update_buttons()
        self.update_display()
        self.update_label_color()

    def on_reset_clicked(self) -> None:
        self.accumulated = 0.0
        self.started_at = None
        self.state = StopwatchState.STOPPED
        self.sync_tick_timer()
        self.update_buttons()
        self.update_display()
        self.update_label_color()
        self.update_ring()

    def sync_tick_timer(self) -> None:
        """Repaint only while counting; on pause/stop the display is frozen."""
        if self.state == StopwatchState.RUNNING:
            self.tick_timer.start(UI_TICK_MS)
            self.ring_timer.start(RING_UPDATE_MS)
        else:
            self.tick_timer.stop()
            self.ring_timer.stop()

    def update_display(self) -> None:
        self.ui.stopwatchLabel.setText(format_elapsed(self.elapsed()))

    def update_label_color(self) -> None:
        colors = {
            StopwatchState.RUNNING: theme.success,
            StopwatchState.PAUSED: theme.warning,
        }
        color = colors.get(self.state)
        self.ring.set_state_color(None if color is None else color(self.ring))

    def apply_theme(self) -> None:
        self.update_label_color()
        self.ring.update()

    def update_ring(self) -> None:
        """Dot angle from elapsed time: SWEEP_PERIOD_S seconds per revolution."""
        self.ring.set_angle(
            self.elapsed() % SWEEP_PERIOD_S / SWEEP_PERIOD_S * 360.0
        )

    def update_buttons(self) -> None:
        if self.state == StopwatchState.RUNNING:
            self.ui.swStartButton.setIcon(Krita.instance().icon(ICON_PAUSE))
            self.ui.swStartButton.setToolTip("Pause")
        elif self.state == StopwatchState.PAUSED:
            self.ui.swStartButton.setIcon(Krita.instance().icon(ICON_PLAY))
            self.ui.swStartButton.setToolTip("Resume")
        else:
            self.ui.swStartButton.setIcon(Krita.instance().icon(ICON_PLAY))
            self.ui.swStartButton.setToolTip("Start")
        self.ui.swResetButton.setEnabled(self.state != StopwatchState.STOPPED)


# --- Timer tab ---

class TimerTab(QObject):
    """Counts time down. Start/Pause toggles; click the digits to type a duration."""

    def __init__(self, ui: QWidget) -> None:
        super().__init__(ui)
        self.ui = ui

        # Balance the bottom spacer so the timer block sits in the tab center
        self.ui.timerTabLayout.insertStretch(0)

        self.state = TimerState.IDLE
        self.run_duration = 0.0
        self.deadline: Optional[float] = None
        self.paused_left: Optional[float] = None
        self.duration = TIMER_DEFAULT_S
        self.editing = False
        self.edit_digits = ""

        self.ui.timerStartButton.clicked.connect(self.on_start_clicked)
        self.ui.timerResetButton.clicked.connect(self.on_reset_clicked)
        self.ui.timerPlus30Button.clicked.connect(partial(self.add_time, 30))
        self.ui.timerPlus60Button.clicked.connect(partial(self.add_time, 60))
        self.ui.timerPlus300Button.clicked.connect(partial(self.add_time, 300))

        for button in (self.ui.timerStartButton, self.ui.timerResetButton):
            button.setText("")
            button.setIconSize(QSize(18, 18))
        self.ui.timerResetButton.setIcon(Krita.instance().icon(ICON_RESET))

        # Editor that appears in place of the big digits
        self.editor = QLineEdit(self.ui.timerTab)
        self.editor.setFont(QFont(self.ui.timerLabel.font()))
        self.editor.setAlignment(Qt.AlignCenter)
        self.editor.setVisible(False)
        self.ui.timerTabLayout.insertWidget(2, self.editor)
        self.ui.timerLabel.setToolTip("Click to edit")
        self.ui.timerLabel.installEventFilter(self)
        self.editor.installEventFilter(self)

        self.progress = StateProgress(self.ui.timerTab)
        self.ui.timerTabLayout.insertWidget(3, self.progress)

        self.tick_timer = QTimer(self.ui)
        self.tick_timer.timeout.connect(self.on_tick)

        self.update_buttons()
        self.update_display()
        self.update_state_color()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.ui.timerLabel and event.type() == QEvent.MouseButtonPress:
            self.begin_edit()
        elif obj is self.editor:
            if event.type() == QEvent.KeyPress:
                return self.on_edit_key(event)
            if event.type() == QEvent.FocusOut and self.editing:
                self.end_edit(True)
        return super().eventFilter(obj, event)

    def on_edit_key(self, event) -> bool:
        key = event.key()
        if key == Qt.Key_Escape:
            self.end_edit(False)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.end_edit(True)
            return True
        if key == Qt.Key_Backspace:
            self.edit_digits = "0" + self.edit_digits[:-1]
            self.refresh_editor()
            return True
        if Qt.Key_0 <= key <= Qt.Key_9:
            self.edit_digits = self.edit_digits[1:] + str(key - Qt.Key_0)
            self.refresh_editor()
            return True
        return False

    def refresh_editor(self) -> None:
        text = "{}:{}:{}".format(
            self.edit_digits[0:2], self.edit_digits[2:4], self.edit_digits[4:6]
        )
        self.editor.setText(text)
        self.editor.setCursorPosition(len(text))

    def begin_edit(self) -> None:
        if self.state not in (TimerState.IDLE, TimerState.PAUSED):
            return
        self.editing = True
        self.edit_digits = seconds_to_timer_digits(int(self.duration))
        self.refresh_editor()
        self.ui.timerLabel.setVisible(False)
        self.editor.setVisible(True)
        self.editor.setFocus()

    def end_edit(self, commit: bool) -> None:
        if not self.editing:
            return
        self.editing = False
        self.editor.setVisible(False)
        self.ui.timerLabel.setVisible(True)
        if commit:
            self.duration = min(TIMER_MAX_S, timer_digits_to_seconds(self.edit_digits))
            self.update_buttons()
        self.update_display()

    def add_time(self, delta: int) -> None:
        if self.state not in (TimerState.IDLE, TimerState.PAUSED):
            return
        if self.state == TimerState.PAUSED and self.paused_left is not None:
            self.paused_left = min(float(TIMER_MAX_S), self.paused_left + delta)
            self.duration = math.ceil(self.paused_left)
        else:
            self.duration = min(TIMER_MAX_S, self.duration + delta)
        self.update_buttons()
        self.update_display()

    def seconds_left(self) -> float:
        """Seconds left in the current state."""
        if self.state == TimerState.RUNNING:
            return max(0.0, self.deadline - time.perf_counter())
        if self.state == TimerState.FINISHED:
            return 0.0
        return float(self.duration)

    def on_start_clicked(self) -> None:
        if self.state == TimerState.RUNNING:
            left = self.seconds_left()
            if left <= 0:
                self.on_finished()
                return
            self.paused_left = left
            self.duration = math.ceil(left)
            self.deadline = None
            self.state = TimerState.PAUSED
        elif self.state == TimerState.IDLE:
            duration = self.duration
            if duration <= 0:
                return
            self.run_duration = float(duration)
            self.deadline = time.perf_counter() + duration
            self.state = TimerState.RUNNING
        elif self.state == TimerState.PAUSED:
            duration = self.duration
            if (self.paused_left is not None
                    and duration == math.ceil(self.paused_left)):
                duration = self.paused_left
            self.paused_left = None
            if duration <= 0:
                return
            self.run_duration = float(duration)
            self.deadline = time.perf_counter() + duration
            self.state = TimerState.RUNNING
        elif self.state == TimerState.FINISHED:
            self.deadline = time.perf_counter() + self.run_duration
            self.state = TimerState.RUNNING
        else:
            return
        self.sync_tick_timer()
        self.update_buttons()
        self.update_display()
        self.update_state_color()

    def on_reset_clicked(self) -> None:
        self.deadline = None
        self.paused_left = None
        self.state = TimerState.IDLE
        self.sync_tick_timer()
        self.update_buttons()
        self.update_display()
        self.update_state_color()

    def on_tick(self) -> None:
        if (self.state == TimerState.RUNNING
                and time.perf_counter() >= self.deadline):
            self.on_finished()
            return
        self.update_display()

    def on_finished(self) -> None:
        self.deadline = None
        self.paused_left = None
        self.state = TimerState.FINISHED
        self.sync_tick_timer()
        self.update_buttons()
        QApplication.beep()
        self.update_state_color()
        self.update_display()

    def sync_tick_timer(self) -> None:
        """Repaint only while counting; on pause/finish the display is static."""
        if self.state == TimerState.RUNNING:
            self.tick_timer.start(UI_TICK_MS)
        else:
            self.tick_timer.stop()

    def update_display(self) -> None:
        self.ui.timerLabel.setText(format_seconds(math.ceil(self.seconds_left())))
        total = float(self.duration) if self.state == TimerState.IDLE else self.run_duration
        if total <= 0:
            self.progress.set_fraction(0.0)
            return
        self.progress.set_fraction(self.seconds_left() / total)

    def update_state_color(self) -> None:
        colors = {
            TimerState.RUNNING: theme.success,
            TimerState.PAUSED: theme.warning,
            TimerState.FINISHED: theme.danger,
        }
        color = colors.get(self.state)
        self.progress.set_state(
            None if color is None else color(self.progress),
            self.state == TimerState.FINISHED,
        )

    def apply_theme(self) -> None:
        self.update_state_color()

    def update_buttons(self) -> None:
        if self.state == TimerState.RUNNING:
            self.ui.timerStartButton.setIcon(Krita.instance().icon(ICON_PAUSE))
            self.ui.timerStartButton.setToolTip("Pause")
            self.ui.timerStartButton.setEnabled(True)
        elif self.state == TimerState.PAUSED:
            self.ui.timerStartButton.setIcon(Krita.instance().icon(ICON_PLAY))
            self.ui.timerStartButton.setToolTip("Resume")
            self.ui.timerStartButton.setEnabled(self.duration > 0)
        elif self.state == TimerState.FINISHED:
            self.ui.timerStartButton.setIcon(Krita.instance().icon(ICON_PLAY))
            self.ui.timerStartButton.setToolTip("Restart")
            self.ui.timerStartButton.setEnabled(True)
        else:
            self.ui.timerStartButton.setIcon(Krita.instance().icon(ICON_PLAY))
            self.ui.timerStartButton.setToolTip("Start")
            self.ui.timerStartButton.setEnabled(self.duration > 0)
        editable = self.state in (TimerState.IDLE, TimerState.PAUSED)
        for button in (self.ui.timerPlus30Button, self.ui.timerPlus60Button,
                       self.ui.timerPlus300Button):
            button.setEnabled(editable)
        self.ui.timerLabel.setCursor(Qt.IBeamCursor if editable else Qt.ArrowCursor)
        self.ui.timerResetButton.setEnabled(self.state != TimerState.IDLE)


# --- Docker ---

class TimeManager(DockWidget):
    """Docker that assembles the three tabs."""

    _applying_theme = False
    _ready = False

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Krita Time Manager")

        self.widget = QWidget(self)
        ui_path = Path(__file__).resolve().parent / 'krita_time_manager.ui'
        uic.loadUi(str(ui_path), self.widget)
        self.setWidget(self.widget)

        self.clock = ClockTab(self.widget)
        self.stopwatch = StopwatchTab(self.widget)
        self.timer = TimerTab(self.widget)

        self._ready = True
        self.apply_theme()
        self._watch_theme()

    def apply_theme(self) -> None:
        """Re-apply palette-derived colors after a theme change."""
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            style_panels(self.widget)
            self.stopwatch.apply_theme()
            self.timer.apply_theme()
        finally:
            self._applying_theme = False

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.PaletteChange and self._ready:
            self.apply_theme()
        super().changeEvent(event)

    def _watch_theme(self) -> None:
        notifier = Krita.instance().notifier()
        window_creating = getattr(notifier, 'windowIsBeingCreated', None)
        if window_creating is not None:
            window_creating.connect(self._watch_window)
        else:
            window_created = getattr(notifier, 'windowCreated', None)
            if window_created is not None:
                window_created.connect(self._watch_all_windows)
        self._watch_all_windows()

    def _watch_all_windows(self) -> None:
        for window in Krita.instance().windows():
            self._watch_window(window)

    def _watch_window(self, window) -> None:
        if window is None or not hasattr(window, 'themeChanged'):
            return
        window.themeChanged.connect(self.apply_theme)

    def canvasChanged(self, canvas) -> None:
        pass


# --- Plugin registration in Krita ---

instance = Krita.instance()
factory = DockWidgetFactory(
    'krita_time_manager',
    DockWidgetFactoryBase.DockRight,
    TimeManager
)
instance.addDockWidgetFactory(factory)
