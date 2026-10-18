"""Krita Time Manager: docker with clock, stopwatch and countdown timer.

Structure:
    Constants -> Enums -> Helper -> Tab classes (Clock / Stopwatch / Timer)
    -> TimeManager docker -> plugin registration.
"""

import math
import os
import time
from enum import Enum, auto
from typing import Optional

from PyQt5 import uic
from PyQt5.QtCore import QDate, QLocale, QPointF, Qt, QTime, QTimer
from PyQt5.QtGui import QPainter, QPalette, QPen
from PyQt5.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita


# --- Constants ---

CLOCK_UPDATE_MS = 1000 # MUST stay 1000 !!! DO NOT MAKE CHANGES WITHOUT MODIFYING class ClockTab:
UI_TICK_MS = 100
FINISHED_STYLE = "color: #e74c3c;"
TIME_FORMAT = "hh:mm:ss"
DATE_FORMAT = "dddd, dd MMMM yyyy"
PANEL_DARKEN = 115

SWEEP_PERIOD_S = 4.0
RING_UPDATE_MS = 16
RING_TRACK_LIGHTEN = 125
RING_HEAD_DARKEN = 120
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
    for page in (ui.clockTab, ui.stopwatchTab, ui.timerTab):
        palette = page.palette()
        palette.setColor(
            QPalette.Window,
            palette.color(QPalette.Window).darker(PANEL_DARKEN),
        )
        page.setPalette(palette)
        page.setAutoFillBackground(True)


# --- Sweep ring ---

class SweepRing(QWidget):
    """Decorative dot orbiting a ring; colors follow the Krita theme palette."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.angle = 0.0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_angle(self, angle: float) -> None:
        """Place the dot at the given angle: 0 = top, growing clockwise."""
        self.angle = angle % 360.0
        self.update()

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

        palette = QApplication.palette()
        track_color = palette.color(QPalette.Window).lighter(RING_TRACK_LIGHTEN)
        head_color = palette.color(QPalette.WindowText).darker(RING_HEAD_DARKEN)

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


# --- Clock tab ---

class ClockTab:
    """Shows the current time and date, aligned to the second boundary.
    
    A one-shot precise timer re-arms itself on every tick, computing the
    remaining time until the next .000 boundary, so the display never drifts.
    """
    #There are a lot of comments because I'm a little dumb

    def __init__(self, ui: QWidget) -> None:
        self.ui = ui

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

class StopwatchTab:
    """Counts time up. Start/Pause toggles, Reset clears."""

    def __init__(self, ui: QWidget) -> None:
        self.ui = ui
        self.state = StopwatchState.STOPPED
        self.accumulated = 0.0
        self.started_at: Optional[float] = None

        self.ui.swStartButton.clicked.connect(self.on_start_clicked)
        self.ui.swResetButton.clicked.connect(self.on_reset_clicked)

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

        self.ring_timer = QTimer(self.ui)
        self.ring_timer.timeout.connect(self.update_ring)

        self.update_buttons()
        self.update_display()

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

    def on_reset_clicked(self) -> None:
        self.accumulated = 0.0
        self.started_at = None
        self.state = StopwatchState.STOPPED
        self.sync_tick_timer()
        self.update_buttons()
        self.update_display()
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

    def update_ring(self) -> None:
        """Dot angle from elapsed time: SWEEP_PERIOD_S seconds per revolution."""
        self.ring.set_angle(
            self.elapsed() % SWEEP_PERIOD_S / SWEEP_PERIOD_S * 360.0
        )

    def update_buttons(self) -> None:
        if self.state == StopwatchState.RUNNING:
            self.ui.swStartButton.setText("Pause")
        elif self.state == StopwatchState.PAUSED:
            self.ui.swStartButton.setText("Resume")
        else:
            self.ui.swStartButton.setText("Start")
        self.ui.swResetButton.setEnabled(self.state != StopwatchState.STOPPED)


# --- Timer tab ---

class TimerTab:
    """Counts time down. Start/Pause toggles; while paused the pending time can be edited."""

    def __init__(self, ui: QWidget) -> None:
        self.ui = ui
        self.state = TimerState.IDLE
        self.run_duration = 0.0
        self.deadline: Optional[float] = None
        self.paused_left: Optional[float] = None

        self.ui.timerStartButton.clicked.connect(self.on_start_clicked)
        self.ui.timerResetButton.clicked.connect(self.on_reset_clicked)
        self.ui.timerHoursSpin.valueChanged.connect(self.on_input_changed)
        self.ui.timerMinutesSpin.valueChanged.connect(self.on_input_changed)
        self.ui.timerSecondsSpin.valueChanged.connect(self.on_input_changed)

        self.tick_timer = QTimer(self.ui)
        self.tick_timer.timeout.connect(self.on_tick)

        self.update_buttons()
        self.update_input_visibility()
        self.update_display()

    def read_input(self) -> int:
        """Duration from the spinboxes, in seconds."""
        return (self.ui.timerHoursSpin.value() * 3600 +
                self.ui.timerMinutesSpin.value() * 60 +
                self.ui.timerSecondsSpin.value())

    def set_input(self, total: int) -> None:
        """Write whole seconds back into the spinboxes."""
        total = min(total, 99 * 3600 + 59 * 60 + 59)
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        self.ui.timerHoursSpin.setValue(hours)
        self.ui.timerMinutesSpin.setValue(minutes)
        self.ui.timerSecondsSpin.setValue(seconds)

    def seconds_left(self) -> float:
        """Seconds left in the current state."""
        if self.state == TimerState.RUNNING:
            return max(0.0, self.deadline - time.perf_counter())
        if self.state == TimerState.FINISHED:
            return 0.0
        return float(self.read_input())

    def on_start_clicked(self) -> None:
        if self.state == TimerState.RUNNING:
            left = self.seconds_left()
            if left <= 0:
                self.on_finished()
                return
            self.paused_left = left
            self.set_input(math.ceil(left))
            self.deadline = None
            self.state = TimerState.PAUSED
        elif self.state == TimerState.IDLE:
            duration = self.read_input()
            if duration <= 0:
                return
            self.run_duration = float(duration)
            self.deadline = time.perf_counter() + duration
            self.state = TimerState.RUNNING
        elif self.state == TimerState.PAUSED:
            duration = self.read_input()
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
            self.clear_finished_style()
            self.deadline = time.perf_counter() + self.run_duration
            self.state = TimerState.RUNNING
        else:
            return
        self.sync_tick_timer()
        self.update_buttons()
        self.update_input_visibility()
        self.update_display()

    def on_reset_clicked(self) -> None:
        self.deadline = None
        self.paused_left = None
        self.state = TimerState.IDLE
        self.clear_finished_style()
        self.sync_tick_timer()
        self.update_buttons()
        self.update_input_visibility()
        self.update_display()

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
        self.update_input_visibility()
        QApplication.beep()
        self.ui.timerLabel.setStyleSheet(FINISHED_STYLE)
        self.update_display()

    def on_input_changed(self, _value: int) -> None:
        if self.state in (TimerState.IDLE, TimerState.PAUSED):
            self.update_buttons()
            self.update_display()

    def clear_finished_style(self) -> None:
        self.ui.timerLabel.setStyleSheet("")

    def sync_tick_timer(self) -> None:
        """Repaint only while counting; on pause/finish the display is static."""
        if self.state == TimerState.RUNNING:
            self.tick_timer.start(UI_TICK_MS)
        else:
            self.tick_timer.stop()

    def update_input_visibility(self) -> None:
        """Spinboxes are editable only in IDLE and PAUSED."""
        visible = self.state in (TimerState.IDLE, TimerState.PAUSED)
        self.ui.timerHoursSpin.setVisible(visible)
        self.ui.timerMinutesSpin.setVisible(visible)
        self.ui.timerSecondsSpin.setVisible(visible)

    def update_display(self) -> None:
        self.ui.timerLabel.setText(format_seconds(math.ceil(self.seconds_left())))

    def update_buttons(self) -> None:
        if self.state == TimerState.RUNNING:
            self.ui.timerStartButton.setText("Pause")
            self.ui.timerStartButton.setEnabled(True)
        elif self.state == TimerState.PAUSED:
            self.ui.timerStartButton.setText("Resume")
            self.ui.timerStartButton.setEnabled(self.read_input() > 0)
        elif self.state == TimerState.FINISHED:
            self.ui.timerStartButton.setText("Restart")
            self.ui.timerStartButton.setEnabled(True)
        else:
            self.ui.timerStartButton.setText("Start")
            self.ui.timerStartButton.setEnabled(self.read_input() > 0)
        self.ui.timerResetButton.setEnabled(self.state != TimerState.IDLE)


# --- Docker ---

class TimeManager(DockWidget):
    """Docker that assembles the three tabs."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Krita Time Manager")

        self.widget = QWidget(self)
        ui_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            'krita_time_manager.ui'
        )
        uic.loadUi(ui_path, self.widget)
        self.setWidget(self.widget)
        style_panels(self.widget)

        self.clock = ClockTab(self.widget)
        self.stopwatch = StopwatchTab(self.widget)
        self.timer = TimerTab(self.widget)

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
