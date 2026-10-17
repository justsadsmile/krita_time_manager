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
from PyQt5.QtCore import QDate, QLocale, Qt, QTime, QTimer
from PyQt5.QtWidgets import QApplication, QWidget

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita


# --- Constants ---

CLOCK_UPDATE_MS = 1000 # MUST stay 1000 !!! DO NOT MAKE CHANGES WITHOUT MODIFYING class ClockTab:
UI_TICK_MS = 100
BLINK_INTERVAL_MS = 500
BLINK_STYLE = "color: #e74c3c;"
TIME_FORMAT = "hh:mm:ss"
DATE_FORMAT = "dddd, dd MMMM yyyy"


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
        self.tick_timer = QTimer()
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

        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.update_display)
        self.tick_timer.start(UI_TICK_MS)

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
        self.update_buttons()
        self.update_display()

    def on_reset_clicked(self) -> None:
        self.accumulated = 0.0
        self.started_at = None
        self.state = StopwatchState.STOPPED
        self.update_buttons()
        self.update_display()

    def update_display(self) -> None:
        self.ui.stopwatchLabel.setText(format_elapsed(self.elapsed()))

    def update_buttons(self) -> None:
        if self.state == StopwatchState.RUNNING:
            self.ui.swStartButton.setText("Pause")
        else:
            self.ui.swStartButton.setText("Start")


# --- Timer tab ---

class TimerTab:
    """Counts time down. Start/Pause toggles, Reset restores the input duration."""

    def __init__(self, ui: QWidget) -> None:
        self.ui = ui
        self.state = TimerState.IDLE
        self.remaining = 0.0
        self.deadline: Optional[float] = None
        self.blink_on = False

        self.ui.timerStartButton.clicked.connect(self.on_start_clicked)
        self.ui.timerResetButton.clicked.connect(self.on_reset_clicked)
        self.ui.timerHoursSpin.valueChanged.connect(self.on_input_changed)
        self.ui.timerMinutesSpin.valueChanged.connect(self.on_input_changed)
        self.ui.timerSecondsSpin.valueChanged.connect(self.on_input_changed)

        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.on_blink)

        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.on_tick)
        self.tick_timer.start(UI_TICK_MS)

        self.update_display()

    def read_input(self) -> int:
        """Duration from the spinboxes, in seconds."""
        return (self.ui.timerHoursSpin.value() * 3600 +
                self.ui.timerMinutesSpin.value() * 60 +
                self.ui.timerSecondsSpin.value())

    def seconds_left(self) -> float:
        """Seconds left in the current state."""
        if self.state == TimerState.RUNNING:
            return max(0.0, self.deadline - time.monotonic())
        if self.state == TimerState.IDLE:
            return float(self.read_input())
        return self.remaining

    def on_start_clicked(self) -> None:
        if self.state == TimerState.RUNNING:
            self.remaining = self.seconds_left()
            self.deadline = None
            self.state = TimerState.PAUSED
        elif self.state == TimerState.IDLE:
            duration = self.read_input()
            if duration <= 0:
                return
            self.remaining = float(duration)
            self.deadline = time.monotonic() + self.remaining
            self.state = TimerState.RUNNING
        elif self.state == TimerState.PAUSED:
            if self.remaining <= 0:
                return
            self.stop_blinking()
            self.deadline = time.monotonic() + self.remaining
            self.state = TimerState.RUNNING
        else:
            return
        self.update_buttons()
        self.update_display()

    def on_reset_clicked(self) -> None:
        self.deadline = None
        self.remaining = 0.0
        self.state = TimerState.IDLE
        self.stop_blinking()
        self.update_buttons()
        self.update_display()

    def on_tick(self) -> None:
        if (self.state == TimerState.RUNNING
                and time.monotonic() >= self.deadline):
            self.on_finished()
        self.update_display()

    def on_finished(self) -> None:
        self.deadline = None
        self.remaining = 0.0
        self.state = TimerState.FINISHED
        self.update_buttons()
        QApplication.beep()
        self.blink_timer.start(BLINK_INTERVAL_MS)
        self.update_display()

    def on_input_changed(self, _value: int) -> None:
        if self.state == TimerState.IDLE:
            self.update_display()

    def on_blink(self) -> None:
        self.blink_on = not self.blink_on
        if self.blink_on:
            self.ui.timerLabel.setStyleSheet(BLINK_STYLE)
        else:
            self.ui.timerLabel.setStyleSheet("")

    def stop_blinking(self) -> None:
        self.blink_timer.stop()
        self.blink_on = False
        self.ui.timerLabel.setStyleSheet("")

    def update_display(self) -> None:
        self.ui.timerLabel.setText(format_seconds(math.ceil(self.seconds_left())))

    def update_buttons(self) -> None:
        if self.state == TimerState.RUNNING:
            self.ui.timerStartButton.setText("Pause")
        else:
            self.ui.timerStartButton.setText("Start")


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
