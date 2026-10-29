"""Headless checks for optional pump pulses during automatic pressdown."""

import unittest
from types import SimpleNamespace

from xy_press_cycle import XYPressCycleWindow
from xy_pump_cycle import XYPumpCycleWindow


def press_window(enabled=True):
    window = XYPumpCycleWindow.__new__(XYPumpCycleWindow)
    commands = []
    timers = []
    cancelled = []
    window.running = True
    window.press_state = "idle"
    window.press_force = 40.0
    window.press_travel = 20.0
    window.press_speed = 0.25
    window.press_pump_enabled = enabled
    window.press_pump_duration = 0.5
    window.press_pump_speeds = (0.5, 0.5)
    window.press_pump_timer_id = None
    window.press_pump_running = set()
    window.data_manager = None
    window.xy_move_axis = None
    window.after_id = None
    window.status_var = SimpleNamespace(set=lambda message: None)
    window._current_target = lambda: {"index": 1}
    window.send_command = lambda command, **kwargs: commands.append(command) or True
    window.after = lambda delay, callback: timers.append((delay, callback)) or len(timers)
    window.after_cancel = cancelled.append
    return window, commands, timers, cancelled


class PressPumpTests(unittest.TestCase):
    def test_disabled_leaves_existing_press_sequence_unchanged(self):
        window, commands, timers, unused = press_window(enabled=False)
        XYPumpCycleWindow._start_press(window)
        self.assertEqual(commands, ["PRESS 40.00 20.000 0.250"])
        self.assertEqual(timers, [])

    def test_enabled_runs_each_pump_at_0_5_rpm_for_0_5_seconds(self):
        window, commands, timers, unused = press_window()
        XYPumpCycleWindow._start_press(window)
        self.assertEqual(commands, [
            "PRESS 40.00 20.000 0.250", "PUMP RUN 0 0.50", "PUMP RUN 1 0.50"
        ])
        self.assertEqual(timers[0][0], 500)
        timers[0][1]()
        self.assertEqual(commands[-2:], ["PUMP STOP 0", "PUMP STOP 1"])
        self.assertEqual(window.press_pump_running, set())

    def test_zero_duration_skips_both_pumps(self):
        window, commands, timers, unused = press_window()
        window.press_pump_duration = 0.0
        XYPumpCycleWindow._start_press(window)
        self.assertEqual(commands, ["PRESS 40.00 20.000 0.250"])
        self.assertEqual(timers, [])

    def test_zero_speed_skips_only_that_pump(self):
        window, commands, timers, unused = press_window()
        window.press_pump_speeds = (0.0, 0.5)
        XYPumpCycleWindow._start_press(window)
        self.assertEqual(commands, ["PRESS 40.00 20.000 0.250", "PUMP RUN 1 0.50"])
        timers[0][1]()
        self.assertEqual(commands[-1], "PUMP STOP 1")
        self.assertNotIn("PUMP STOP 0", commands)

    def test_both_zero_speeds_schedule_no_timer(self):
        window, commands, timers, unused = press_window()
        window.press_pump_speeds = (0.0, 0.0)
        XYPumpCycleWindow._start_press(window)
        self.assertEqual(commands, ["PRESS 40.00 20.000 0.250"])
        self.assertEqual(timers, [])

    def test_force_stop_cancels_prewet_before_normal_pumps(self):
        window, commands, unused, cancelled = press_window()
        XYPumpCycleWindow._start_press(window)
        window._start_pumps = lambda: commands.append("NORMAL PUMPS")
        XYPumpCycleWindow.handle_controller_line(window, "EVENT,PRESS_FORCE_STOP")
        self.assertEqual(commands[-3:], ["PUMP STOP 0", "PUMP STOP 1", "NORMAL PUMPS"])
        self.assertEqual(cancelled, [1])

    def test_start_failure_stops_press_and_any_started_pump(self):
        window, commands, unused, unused_cancelled = press_window()
        window.send_command = lambda command, **kwargs: commands.append(command) or command != "PUMP RUN 1 0.50"
        failures = []
        window._finish = lambda message: (failures.append(message), window._stop_press_pumps())
        XYPumpCycleWindow._start_press(window)
        self.assertIn("MOTION_STOP", commands)
        self.assertIn("PUMP STOP 0", commands)
        self.assertIn("press pump command was not sent", failures[0])

    def test_configuration_is_validated_only_when_enabled(self):
        window, unused_commands, unused_timers, unused_cancelled = press_window()
        window.press_pump_enabled_var = SimpleNamespace(get=lambda: True)
        window.press_pump_duration_var = SimpleNamespace(get=lambda: 0.5)
        window.press_pump_rpm_vars = [SimpleNamespace(get=lambda: 0.5), SimpleNamespace(get=lambda: 0.5)]
        self.assertEqual(XYPressCycleWindow._press_pump_parameters(window), (True, 0.5, (0.5, 0.5)))
        window.press_pump_rpm_vars[1] = SimpleNamespace(get=lambda: 0.0)
        self.assertEqual(XYPressCycleWindow._press_pump_parameters(window), (True, 0.5, (0.5, 0.0)))
        window.press_pump_duration_var = SimpleNamespace(get=lambda: 0.0)
        self.assertEqual(XYPressCycleWindow._press_pump_parameters(window), (True, 0.0, (0.5, 0.0)))
        window.press_pump_rpm_vars[1] = SimpleNamespace(get=lambda: 0.05)
        with self.assertRaises(ValueError):
            XYPressCycleWindow._press_pump_parameters(window)


if __name__ == "__main__":
    unittest.main()
