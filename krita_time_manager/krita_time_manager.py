import math
import os
import time

from PyQt5 import uic
from PyQt5.QtCore import QDate, QTime, QTimer
from PyQt5.QtWidgets import QApplication, QWidget

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita


def format_seconds(total):
    """Seconds -> "hh:mm:ss" string."""
    total = int(total)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)


class TimeManager(DockWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Krita Time Manager")

        # --- Container and .ui loading ---
        self.widget = QWidget(self)
        ui_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            'krita_time_manager.ui'
        )
        uic.loadUi(ui_path, self.widget)
        self.setWidget(self.widget)

        # --- Clock ---
        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()

        # --- Stopwatch ---
        self.sw_accumulated = 0.0   # accumulated seconds (while paused)
        self.sw_started_at = None   # start moment (time.monotonic) or None

        # --- Timer ---
        self.timer_remaining = None  # seconds left; None = never started
        self.timer_deadline = None   # finish moment (time.monotonic) or None
        self.timer_blink_on = False

        # --- Display refresh (10 times per second) ---
        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.on_tick)
        self.tick_timer.start(100)

        # --- Blinking of the timer label when finished ---
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.on_blink)

        # --- Buttons ---
        self.widget.swStartButton.clicked.connect(self.on_sw_start_click)
        self.widget.swResetButton.clicked.connect(self.on_sw_reset_click)
        self.widget.timerStartButton.clicked.connect(self.on_timer_start_click)
        self.widget.timerResetButton.clicked.connect(self.on_timer_reset_click)

        # --- Duration preview when spinboxes change ---
        self.widget.timerHoursSpin.valueChanged.connect(self.on_timer_input_changed)
        self.widget.timerMinutesSpin.valueChanged.connect(self.on_timer_input_changed)
        self.widget.timerSecondsSpin.valueChanged.connect(self.on_timer_input_changed)

        self.update_stopwatch_display()
        self.update_timer_display()

    # --- Clock ---

    def update_clock(self):
        self.widget.clockLabel.setText(QTime.currentTime().toString("hh:mm:ss"))
        self.widget.dateLabel.setText(QDate.currentDate().toString("dddd, dd MMMM yyyy"))

    # --- Common tick ---

    def on_tick(self):
        self.update_stopwatch_display()
        if self.timer_deadline is not None and time.monotonic() >= self.timer_deadline:
            self.on_timer_finished()
        self.update_timer_display()

    # --- Stopwatch ---

    def stopwatch_elapsed(self):
        if self.sw_started_at is not None:
            return self.sw_accumulated + (time.monotonic() - self.sw_started_at)
        return self.sw_accumulated

    def update_stopwatch_display(self):
        self.widget.stopwatchLabel.setText(format_seconds(self.stopwatch_elapsed()))

    def on_sw_start_click(self):
        if self.sw_started_at is None:
            self.sw_started_at = time.monotonic()
            self.widget.swStartButton.setText("Pause")
        else:
            self.sw_accumulated = self.stopwatch_elapsed()
            self.sw_started_at = None
            self.widget.swStartButton.setText("Start")
        self.update_stopwatch_display()

    def on_sw_reset_click(self):
        self.sw_accumulated = 0.0
        self.sw_started_at = None
        self.widget.swStartButton.setText("Start")
        self.update_stopwatch_display()

    # --- Timer ---

    def read_timer_input(self):
        return (self.widget.timerHoursSpin.value() * 3600 +
                self.widget.timerMinutesSpin.value() * 60 +
                self.widget.timerSecondsSpin.value())

    def update_timer_display(self):
        if self.timer_deadline is not None:
            left = max(0.0, self.timer_deadline - time.monotonic())
        elif self.timer_remaining is not None:
            left = self.timer_remaining
        else:
            left = self.read_timer_input()
        self.widget.timerLabel.setText(format_seconds(math.ceil(left)))

    def on_timer_input_changed(self, _value):
        if self.timer_deadline is None and self.timer_remaining is None:
            self.update_timer_display()

    def on_timer_start_click(self):
        if self.timer_deadline is not None:
            self.timer_remaining = max(0.0, self.timer_deadline - time.monotonic())
            self.timer_deadline = None
            self.widget.timerStartButton.setText("Start")
        else:
            if self.timer_remaining is None:
                duration = self.read_timer_input()
                if duration <= 0:
                    return
                self.timer_remaining = float(duration)
            self.stop_blinking()
            self.timer_deadline = time.monotonic() + self.timer_remaining
            self.widget.timerStartButton.setText("Pause")
        self.update_timer_display()

    def on_timer_reset_click(self):
        self.timer_deadline = None
        self.timer_remaining = None
        self.stop_blinking()
        self.widget.timerStartButton.setText("Start")
        self.update_timer_display()

    def on_timer_finished(self):
        self.timer_deadline = None
        self.timer_remaining = 0.0
        self.widget.timerStartButton.setText("Start")
        QApplication.beep()
        self.blink_timer.start(500)
        self.update_timer_display()

    def on_blink(self):
        self.timer_blink_on = not self.timer_blink_on
        if self.timer_blink_on:
            self.widget.timerLabel.setStyleSheet("color: #e74c3c;")
        else:
            self.widget.timerLabel.setStyleSheet("")

    def stop_blinking(self):
        if self.blink_timer.isActive():
            self.blink_timer.stop()
        self.timer_blink_on = False
        self.widget.timerLabel.setStyleSheet("")

    # --- Krita API ---

    def canvasChanged(self, canvas):
        pass


# --- Plugin registration in Krita ---
instance = Krita.instance()
factory = DockWidgetFactory(
    'krita_time_manager',
    DockWidgetFactoryBase.DockRight,
    TimeManager
)
instance.addDockWidgetFactory(factory)
