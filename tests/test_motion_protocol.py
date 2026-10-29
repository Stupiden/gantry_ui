import ast
import pathlib
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]

# The protocol parser itself does not use pyserial. These stubs let the test run
# before pyserial is installed in the required Gamry Python environment.
if "serial" not in sys.modules:
    serial_module = types.ModuleType("serial")
    serial_tools_module = types.ModuleType("serial.tools")
    serial_list_ports_module = types.ModuleType("serial.tools.list_ports")
    serial_list_ports_module.comports = lambda: []
    serial_tools_module.list_ports = serial_list_ports_module
    serial_module.tools = serial_tools_module
    serial_module.SerialException = Exception
    sys.modules["serial"] = serial_module
    sys.modules["serial.tools"] = serial_tools_module
    sys.modules["serial.tools.list_ports"] = serial_list_ports_module

sys.path.insert(0, str(ROOT))
from x_control import (  # noqa: E402
    GantryMotionUI,
    append_environment_record,
    extract_complete_lines,
    load_scan_start_offsets,
    parse_status_line,
    save_scan_start_offsets,
)
from experiment_data import ExperimentDataManager  # noqa: E402
from experiment_resume_preview import load_experiment_preview  # noqa: E402
from xy_eis_comp_cv_cycle import XYEISCompCVCycleWindow  # noqa: E402
from xy_full_eis_cycle import XYFullEISCycleWindow  # noqa: E402
from xy_press_cycle import XYPressCycleWindow  # noqa: E402
from xy_pump_cycle import XYPumpCycleWindow  # noqa: E402
from circular_scan_path import generate_circular_path  # noqa: E402
from rectangular_scan_path import generate_rectangular_path  # noqa: E402
from gamry_lsv_manual import ManualLSVWindow  # noqa: E402
from auto_lsv_measurement import AutoLSVMeasurement  # noqa: E402
from auto_cv_measurement import AutoCVMeasurement  # noqa: E402
from xy_eis_comp_lsv_cycle import XYEISCompLSVCycleWindow  # noqa: E402


class MotionProtocolTests(unittest.TestCase):
    def test_eis_cv_screenshot_defaults(self):
        class Var:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        names = (
            "speed", "dwell", "press_force", "press_travel", "press_speed",
            "lift_distance", "lift_speed", "pressed_stabilization", "pre_lift_wait",
            "press_pump_enabled", "press_pump_duration",
            "pump_start_interval", "suction_enabled", "suction_with_lift",
            "suction_lift_delay", "frequencies", "repeats", "ac_amplitude",
            "dc_bias", "gain", "zmod", "passes", "cycles_delay", "settle",
            "max_phase", "z_bad", "min_bad", "max_eis_attempts", "cv_initial",
            "cv_apex1", "cv_apex2", "cv_final", "cv_scan_rate", "cv_step",
            "cv_cycles", "cv_precharge", "cv_ie_range", "pos_feed",
            "auto_ie_range", "comp_fraction", "min_ru", "max_ru",
        )
        window = types.SimpleNamespace(**{name + "_var": Var() for name in names})
        for group in ("press_pump_rpm", "pump_enabled", "pump_rpm", "pump_duration", "suction_rpm", "suction_duration"):
            setattr(window, group + "_vars", [Var(), Var()])
        window.cv_ie_range_choices = ["8 — 600uA", "9 — 6mA"]
        XYEISCompCVCycleWindow._apply_default_setup(window)

        self.assertEqual(window.press_force_var.value, 45.0)
        self.assertEqual(window.lift_speed_var.value, 0.15)
        self.assertTrue(window.press_pump_enabled_var.value)
        self.assertEqual(window.press_pump_duration_var.value, 1.5)
        self.assertEqual([var.value for var in window.press_pump_rpm_vars], [1.0, 0.0])
        self.assertEqual([var.value for var in window.pump_rpm_vars], [2.2, 2.1])
        self.assertEqual([var.value for var in window.pump_duration_vars], [2.0, 2.0])
        self.assertEqual(window.pressed_stabilization_var.value, 10.0)
        self.assertEqual(window.pump_start_interval_var.value, 0.0)
        self.assertEqual([var.value for var in window.suction_duration_vars], [4.5, 6.0])
        self.assertEqual(window.suction_lift_delay_var.value, 4.0)
        self.assertEqual(window.frequencies_var.value, "1000000, 300000, 170000, 100000, 30000")
        self.assertEqual(window.dc_bias_var.value, -1.1)
        self.assertEqual(window.z_bad_var.value, 500.0)
        self.assertEqual([var.value for var in (
            window.cv_initial_var, window.cv_apex1_var,
            window.cv_apex2_var, window.cv_final_var,
        )], [-1.1, -1.5, -1.1, -1.1])
        self.assertEqual(window.cv_cycles_var.value, 1)
        self.assertEqual(window.cv_precharge_var.value, 0.0)
        self.assertEqual(window.cv_ie_range_var.value, "9 — 6mA")
        self.assertTrue(window.pos_feed_var.value)
        self.assertFalse(window.auto_ie_range_var.value)
        self.assertEqual(window.comp_fraction_var.value, 0.85)
        self.assertEqual(window.max_ru_var.value, 500.0)
        source = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        self.assertIn("if type(self) is XYEISCompCVCycleWindow:", source)
        self.assertLess(source.index("self._apply_default_setup()"),
                        source.index("self._apply_resume_settings(resume_manager.data"))

    def test_automatic_cv_ie_range_follows_pf_mode(self):
        def run_case(auto_range, resistance):
            calls = []
            pstat = types.SimpleNamespace(
                set_ctrl_mode=lambda mode: None,
                set_ie_range_mode=lambda enabled: calls.append(("auto", enabled)),
                set_ie_range=lambda selected: calls.append(("fixed", selected)),
                set_pos_feed_resistance=lambda value: None,
                set_pos_feed_enable=lambda enabled: None,
                set_cell=lambda enabled: None,
            )
            tkp = types.SimpleNamespace(
                PSTATMODE=1, pstat_is_valid=lambda unused: True,
                RcvCurve=lambda unused, capacity: object(),
            )
            owner = types.SimpleNamespace(after=lambda ms, callback: "timer")
            runner = AutoCVMeasurement(owner, pstat, tkp)
            runner._create_signal = lambda params, sample_time: object()
            runner._attach_signal = lambda signal: None
            pstat.init_signal = lambda: None
            params = {
                "auto_ie_range": auto_range, "ie_range": 8,
                "pos_feed_resistance": resistance, "scan_rate": 0.1,
                "step_size": 0.001, "precharge": 0.0,
            }
            with tempfile.TemporaryDirectory() as folder:
                with patch("auto_cv_measurement.initialize_pstat_for_cv"):
                    if auto_range and resistance > 0:
                        with self.assertRaises(ValueError):
                            runner.start(params, pathlib.Path(folder) / "cv.csv", {}, (0, 0, 0), None, None)
                    else:
                        runner.start(params, pathlib.Path(folder) / "cv.csv", {}, (0, 0, 0), None, None)
            return calls

        self.assertEqual(run_case(True, 0.0), [("auto", True)])
        self.assertEqual(run_case(False, 10.0), [("auto", False), ("fixed", 8)])
        self.assertEqual(run_case(True, 10.0), [])

    def test_combined_automatic_ie_range_requires_pf_off(self):
        values = {
            "cv_initial_var": -0.5, "cv_apex1_var": -1.2,
            "cv_apex2_var": -0.5, "cv_final_var": -0.5,
            "cv_scan_rate_var": 0.1, "cv_step_var": 0.001,
            "cv_cycles_var": 1, "cv_precharge_var": 10.0,
            "cv_ie_range_var": "8 — 600uA", "comp_fraction_var": 0.5,
            "min_ru_var": 0.01, "max_ru_var": 10000.0,
            "auto_ie_range_var": True, "pos_feed_var": False,
        }
        for window_class in (XYEISCompCVCycleWindow, XYEISCompLSVCycleWindow):
            window = window_class.__new__(window_class)
            for name, value in values.items():
                setattr(window, name, types.SimpleNamespace(get=lambda selected=value: selected))
            if window_class is XYEISCompLSVCycleWindow:
                window.cv_final_var = types.SimpleNamespace(get=lambda: -1.2)
                window.cv_scan_rate_var = types.SimpleNamespace(get=lambda: 0.01)
            params, fraction, unused_min, unused_max = window._comp_cv_parameters()
            self.assertTrue(params["auto_ie_range"])
            self.assertEqual(fraction, 0.0)
            window.pos_feed_var = types.SimpleNamespace(get=lambda: True)
            with self.assertRaises(ValueError):
                window._comp_cv_parameters()

    def test_environment_csv_is_simple_and_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "environment.csv"
            first = {
                "timestamp": "2026-09-28T10:00:00",
                "elapsed_minutes": 0.0,
                "temperature_c": 22.5,
                "humidity_rh_percent": 48.0,
                "measurement_mode": "CV",
                "point_index": 1,
                "x_mm": 1.0,
                "y_mm": 2.0,
                "z_mm": 3.0,
            }
            append_environment_record(path, first)
            append_environment_record(path, dict(first, elapsed_minutes=15.0))
            rows = path.read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(len(rows), 3)
            self.assertIn("temperature_c", rows[0])
            self.assertIn("humidity_rh_percent", rows[0])
            self.assertIn("2026-09-28T10:00:00", rows[1])

    def test_environment_sensor_is_on_demand_and_automatic_only(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("const uint8_t DHT_PIN = 26", firmware)
        self.assertIn('strcmp(verb, "ENV")', firmware)
        self.assertIn("if (anyAxisMoving())", firmware)
        self.assertIn('window.data_manager.root / "environment.csv"', ui)
        self.assertIn("values=tuple(range(15, 1441, 15))", ui)
        self.assertIn('self.send_command("ENV")', ui)

    def test_scan_start_offsets_persist_and_invalid_files_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = pathlib.Path(directory) / "settings.json"
            expected = {"X": 12.5, "Y": -3.25, "Z": 7.0}
            save_scan_start_offsets(settings_path, expected)
            self.assertEqual(load_scan_start_offsets(settings_path), expected)

            settings_path.write_text('{"last_scan_start_mm":{"X":"bad"}}', encoding="utf-8")
            self.assertEqual(
                load_scan_start_offsets(settings_path),
                {"X": 0.0, "Y": 0.0, "Z": 0.0},
            )

    def test_main_ui_saved_scan_return_is_sequential_and_requires_home(self):
        source = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("Last Scan Zero from Home:", source)
        self.assertIn("Home X, Y, and Z before returning", source)
        self.assertIn("self.scan_start_return_axes = list(self.AXES)", source)
        self.assertIn('line.startswith("EVENT,MOVE_DONE,")', source)
        self.assertIn('self.send_command("MOVE {} {:.3f}".format(axis, distance))', source)

    def test_compact_status_expands_positions_limits_and_motion(self):
        values = parse_status_line(
            "STATUS,X=1.250,Y=-2.000,Z=0.006,SX=0.250,SY=-3.000,SZ=0.000,"
            "L=010101,M=101,H=110,A=01,P=10,PI=2.00,PO=3.50,"
            "LC=1,T=0,R=1234,FR=1200,F=99.50,PR=1,PD=0.125"
        )
        self.assertEqual(values["X"], "1.250")
        self.assertEqual(values["YMIN"], "0")
        self.assertEqual(values["YPLUS"], "1")
        self.assertEqual(values["XMOVING"], "1")
        self.assertEqual(values["YMOVING"], "0")
        self.assertEqual(values["ZMOVING"], "1")
        self.assertEqual(values["SX"], "0.250")
        self.assertEqual(values["XHOMED"], "1")
        self.assertEqual(values["ZHOMED"], "0")
        self.assertEqual(values["Y1ALARM"], "0")
        self.assertEqual(values["Y2ALARM"], "1")
        self.assertEqual(values["PUMP0RUNNING"], "1")
        self.assertEqual(values["PUMP1RUNNING"], "0")
        self.assertEqual(values["PO"], "3.50")
        self.assertEqual(values["R"], "1234")
        self.assertEqual(values["FR"], "1200")
        self.assertEqual(values["F"], "99.50")
        self.assertEqual(values["PR"], "1")
        self.assertEqual(values["PD"], "0.125")

    def test_bad_status_is_rejected(self):
        bad_lines = (
            "OK,STOP",
            "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=00000,M=000,H=000,A=00,P=00,PI=2,PO=2,LC=1,T=0,R=0,FR=0,F=0",
            "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=00000X,M=000,H=000,A=00,P=00,PI=2,PO=2",
            "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=000000,M=00,H=000,A=00,P=00,PI=2,PO=2",
            "STATUS,X=no,Y=0,Z=0,SX=0,SY=0,SZ=0,L=000000,M=000,H=000,A=00,P=00,PI=2,PO=2",
            "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=000000,M=000,H=00,A=00,P=00,PI=2,PO=2",
            "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=000000,M=000,H=000,A=0,P=00,PI=2,PO=2",
            "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=000000,M=000,H=000,A=00,P=0,PI=2,PO=2",
        )
        for line in bad_lines:
            with self.assertRaises(ValueError):
                parse_status_line(line)

    def test_serial_lines_survive_fragmented_reads(self):
        buffer = b""
        lines, buffer = extract_complete_lines(buffer, b"IDENTITY,MEGA_")
        self.assertEqual(lines, [])
        lines, buffer = extract_complete_lines(
            buffer,
            b"GANTRY_M6_SAFE500\r\nSTATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,"
            b"L=000000,M=000,H=000,A=00,P=00,PI=2,PO=2,"
            b"LC=1,T=0,R=0,FR=0,F=0,PR=0,PD=0\n",
        )
        self.assertEqual(
            lines,
            [
                "IDENTITY,MEGA_GANTRY_M6_SAFE500",
                "STATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,L=000000,M=000,H=000,A=00,P=00,PI=2,PO=2,LC=1,T=0,R=0,FR=0,F=0,PR=0,PD=0",
            ],
        )
        self.assertEqual(buffer, b"")

    def test_jog_release_stops_motion_without_stopping_pumps(self):
        source = (ROOT / "x_control.py").read_text(encoding="utf-8")
        function_source = source.split("    def stop_jog", 1)[1].split(
            "    def on_global_button_release", 1
        )[0]
        self.assertIn('self.send_command("MOTION_STOP")', function_source)
        self.assertNotIn('self.send_command("STOP")', function_source)
        self.assertNotIn('self.send_command("JOG STOP")', function_source)

    def test_ui_has_no_acceleration_and_caps_speed_at_five(self):
        source = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertNotIn("ACCEL", source)
        self.assertNotIn("accel_var", source)
        self.assertIn("0.05 <= speed <= 5.0", source)

    def test_firmware_uses_timer_driven_direct_pulses(self):
        source = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        self.assertNotIn("AccelStepper", source)
        self.assertNotIn("ACCEL", source)
        self.assertIn("ISR(TIMER1_COMPA_vect)", source)
        self.assertIn("TIMER_HIGH_TICKS = 8", source)
        self.assertIn("if (!force && anyAxisMoving()) return", source)
        self.assertIn("MAX_SPEED_MM_S = 5.0f", source)
        self.assertIn("digitalWrite(Z_DIR, direction > 0 ? LOW : HIGH)", source)

    def test_milestone_two_homing_and_scan_zero_are_separate(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("MODE_HOME", firmware)
        self.assertIn('strcmp(verb, "HOME")', firmware)
        self.assertIn("axis->positionSteps = 0", firmware)
        self.assertIn("bool homeMovementAllowed", firmware)
        self.assertIn("if (axis.name == 'Z') return !limitTriggered(Z_PLUS)", firmware)
        self.assertIn("int8_t homeDirection = -1", firmware)
        self.assertIn("const uint8_t Z_HOME_DIR_LEVEL = LOW", firmware)
        self.assertIn("digitalWrite(Z_DIR, Z_HOME_DIR_LEVEL)", firmware)
        self.assertIn("scanZeroSteps", firmware)
        self.assertIn('strcmp(verb, "SCAN_ZERO")', firmware)
        self.assertIn('self.send_command("HOME {}".format(axis))', ui)
        self.assertIn('self.send_command("SCAN_ZERO")', ui)

    def test_milestone_three_dual_y_alarm_interlock(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("Y1_ALARM = 37", firmware)
        self.assertIn("Y2_ALARM = 38", firmware)
        self.assertIn("return digitalRead(pin) == LOW", firmware)
        self.assertIn("EVENT,ALARM_STOP", firmware)
        self.assertIn("!yMotionAllowed(axis)", firmware)
        self.assertIn("Y1ALARM", ui)
        self.assertIn("Y2ALARM", ui)

    def test_milestone_four_uses_independent_pump_timers(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("PUMP_IN_STEP = 30", firmware)
        self.assertIn("PUMP_IN_DIR = 31", firmware)
        self.assertIn("PUMP_OUT_STEP = 32", firmware)
        self.assertIn("PUMP_OUT_DIR = 33", firmware)
        self.assertIn("ISR(TIMER3_COMPA_vect)", firmware)
        self.assertIn("ISR(TIMER4_COMPA_vect)", firmware)
        self.assertIn("digitalWrite(pumps[pumpId].dirPin, reverse ? LOW : HIGH)", firmware)
        self.assertIn("stopAllPumps();", firmware)
        self.assertIn('strcmp(verb, "MOTION_STOP")', firmware)
        self.assertIn('self.send_command("PUMP START {}".format(pump_id))', ui)
        self.assertIn('self.send_command("PUMP STOP {}".format(pump_id))', ui)

    def test_automatic_cycles_start_pumps_only_after_press_force_stop(self):
        source = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        start_press = source[source.index("    def _start_press(self):"):
                             source.index("    def _pump_time_complete(self")]
        self.assertNotIn('self.send_command("PUMP START {}"', start_press)
        force_event = source[source.index("    def handle_controller_line(self, line):"):
                             source.index("    def _start_pumps(self):")]
        self.assertIn('line.startswith("EVENT,PRESS_FORCE_STOP")', force_event)
        self.assertIn("self._start_pumps()", force_event)

    def test_pump_speed_defaults_match_latest_experiment_setup(self):
        main = (ROOT / "x_control.py").read_text(encoding="utf-8")
        automatic = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_PUMP_RPM = 2.0", main)
        self.assertIn(
            "self.pump_rpm_vars = [tk.DoubleVar(value=1.2), tk.DoubleVar(value=1.0)]",
            automatic,
        )

    def test_automatic_pump_start_interval_controls_order(self):
        source = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        self.assertIn("self.pump_start_interval_var = tk.DoubleVar(value=0.5)", source)
        self.assertIn(
            "self.pump_duration_vars = [tk.DoubleVar(value=1.0), tk.DoubleVar(value=2.0)]",
            source,
        )
        self.assertIn("Pump start interval (s; +IN / -OUT)", source)
        self.assertIn("if self.pump_start_interval < 0:", source)
        self.assertIn("int(abs(self.pump_start_interval) * 1000)", source)
        self.assertIn('"pump_start_interval_s": self.pump_start_interval', source)

    def test_optional_suction_runs_after_pumps_and_before_lift(self):
        cycle = XYFullEISCycleWindow.__new__(XYFullEISCycleWindow)
        commands, timers = [], []
        cycle.running = True
        cycle.press_state = "post_eis_failure_pumping"
        cycle.running_pumps = {0, 1}
        cycle.pump_after_ids = {0: "normal-in", 1: "normal-out"}
        cycle.suction_configs = [(0, -2.0, 1.0), (1, 2.0, 1.5)]
        cycle.pre_lift_wait = 0.5
        cycle.reported_pump_states = (False, False)
        cycle.pump_stop_deadline = None
        cycle.lift_distance, cycle.lift_speed = 3.0, 1.0
        cycle.status_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.send_command = lambda command, require_ready=True: commands.append(command) or True
        cycle.after = lambda ms, callback: timers.append((ms, callback)) or len(timers)

        cycle._pump_time_complete(0)
        self.assertEqual(commands, ["PUMP STOP 0"])
        cycle._pump_time_complete(1)
        self.assertEqual(commands[1:], ["PUMP STOP 1", "PUMP RUN 0 -2.00", "PUMP RUN 1 2.00"])
        self.assertEqual([ms for ms, unused in timers], [1000, 1500])
        self.assertEqual(cycle.press_state, "suction")
        timers[0][1]()
        self.assertEqual(commands[-1], "PUMP STOP 0")
        timers[1][1]()
        self.assertEqual(commands[-1], "PUMP STOP 1")
        self.assertEqual(timers[2][0], 500)
        timers[2][1]()
        self.assertEqual(commands[-1], "LIFT 3.000 1.000")

    def test_suction_rejects_old_firmware_and_saves_resume_settings(self):
        cycle = XYPumpCycleWindow.__new__(XYPumpCycleWindow)
        failures = []
        cycle.running = True
        cycle.press_state = "suction"
        cycle._finish = failures.append
        XYPumpCycleWindow.handle_controller_line(cycle, "ERROR,BAD_PUMP_ACTION")
        self.assertIn("suction command rejected", failures[0])
        firmware = (ROOT / "mega_x_controller" / "mega_x_controller.ino").read_text(encoding="utf-8")
        self.assertIn('strcmp(action, "RUN")', firmware)
        self.assertIn("startPump(pumpId, signedRpm < 0.0f)", firmware)
        source = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        resume = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        self.assertIn('"suction": {', source)
        self.assertIn('settings.get("suction", {})', resume)

    def test_milestone_five_reads_s_type_load_cell_without_press_down(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("ADS1220_DRDY = 14", firmware)
        self.assertIn("ADS1220_CS = 53", firmware)
        self.assertIn("LOAD_CELL_CAL_SLOPE = 0.1165f", firmware)
        self.assertIn("void serviceLoadCell()", firmware)
        self.assertIn('strcmp(verb, "TARE")', firmware)
        self.assertIn('self.send_command("TARE")', ui)
        self.assertNotIn("PRESS_DOWN", firmware)

    def test_milestone_six_force_limited_press_and_measured_lift(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("MODE_PRESS", firmware)
        self.assertIn("MODE_LIFT", firmware)
        self.assertIn("loadCellForceGrams >= pressForceThresholdG", firmware)
        self.assertIn("capturePressLiftDistance()", firmware)
        self.assertIn("long depth = pressStartSteps - zAxis.positionSteps", firmware)
        self.assertIn("zAxis.direction = -1", firmware)
        self.assertIn("zAxis.direction = 1", firmware)
        self.assertIn("DEFAULT_PRESS_TRAVEL_MM = 20.0f", firmware)
        self.assertIn('strcmp(verb, "PRESS")', firmware)
        self.assertIn('strcmp(verb, "LIFT")', firmware)
        self.assertIn('self.send_command("MOTION_STOP")', ui)
        self.assertIn('self.send_command("LIFT")', ui)

    def test_press_stops_on_missing_or_frozen_load_cell_data(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        self.assertIn("pinMode(ADS1220_DRDY, INPUT_PULLUP)", firmware)
        self.assertIn("LOAD_CELL_SAMPLE_TIMEOUT_MS = 500", firmware)
        self.assertIn("LOAD_CELL_CHANGE_TIMEOUT_MS = 750", firmware)
        self.assertIn("millis() - lastLoadCellSampleMillis", firmware)
        self.assertIn("millis() - lastLoadCellChangeMillis", firmware)
        self.assertIn("if (loadCellFault() != NULL || !loadCellTared", firmware)
        self.assertIn('EVENT,PRESS_SENSOR_FAULT_STOP,', firmware)
        self.assertIn('ERROR,TARE_DURING_Z_MOTION', firmware)

        cycle = XYPressCycleWindow.__new__(XYPressCycleWindow)
        cycle.running = True
        cycle.press_state = "pressing"
        messages = []
        cycle._finish = messages.append
        cycle.handle_controller_line("EVENT,PRESS_SENSOR_FAULT_STOP,FROZEN")
        self.assertEqual(len(messages), 1)
        self.assertIn("re-tare", messages[0])

    def test_z_force_hard_limit_covers_all_motion_modes(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("Z_FORCE_LIMIT_G = 500.0f", firmware)
        self.assertIn("MAX_PRESS_FORCE_G = 500.0f", firmware)
        self.assertIn("if (axis.name == 'Z' && direction < 0 && !zDownwardSafetyReady())", firmware)
        self.assertIn("LOAD_CELL_CAL_SLOPE * (loadCellRaw - loadCellTareRaw)", firmware)
        self.assertIn("zSafetyForceGrams() >= Z_FORCE_LIMIT_G && !zForceOverloadLatched", firmware)
        self.assertIn('EVENT,Z_FORCE_LIMIT_STOP,', firmware)
        self.assertIn('Serial.print(F(",PL="))', firmware)
        self.assertIn('self.press_lift_available = values.get("PL") == "1"', ui)
        self.assertIn('text="SUCK + LIFT"', ui)

    def test_homepage_suck_lift_requires_controller_press_record(self):
        ui = GantryMotionUI.__new__(GantryMotionUI)
        ui.controller_ready = True
        ui.press_lift_available = False
        ui.gamry_measurement_owner = None
        ui.moving_vars = {"Z": types.SimpleNamespace(get=lambda: "IDLE")}
        ui.pump_status_vars = [types.SimpleNamespace(get=lambda: "STOPPED") for _ in range(2)]
        for name in (
            "xy_dry_run_window", "xy_press_cycle_window", "xy_pump_cycle_window",
            "xy_full_cv_window", "xy_full_eis_window", "xy_eis_cv_window",
            "xy_eis_lsv_window", "xy_full_ca_window",
        ):
            setattr(ui, name, None)
        self.assertFalse(ui.manual_suck_lift_guard()[0])
        ui.press_lift_available = True
        self.assertEqual(ui.manual_suck_lift_guard(), (True, ""))

    def test_milestone_seven_only_detects_and_connects_gamry(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("import toolkitpy as tkp", ui)
        self.assertIn('tkp.toolkitpy_init("gantry_ui.py")', ui)
        self.assertIn('tkp.Pstat("PSTAT")', ui)
        self.assertIn("tkp.pstat_is_valid", ui)
        self.assertIn("tkp.toolkitpy_close()", ui)
        self.assertIn("self.gamry_pstat.set_cell(False)", ui)
        self.assertIn('struct.calcsize("P") * 8 != 32', ui)
        self.assertNotIn("GamryDtaq", ui)
        self.assertNotIn("Start CV", ui)

    def test_milestone_eight_manual_cv_matches_verified_setup(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        cv = (ROOT / "gamry_cv_manual.py").read_text(encoding="utf-8")
        self.assertIn("ManualCVWindow", ui)
        self.assertIn("initialize_pstat_for_cv", cv)
        self.assertIn("signal_r_up_dn_new", cv)
        self.assertIn("RcvCurve", cv)
        self.assertIn("return self.pstat.signal_r_up_dn_new", cv)
        self.assertIn("ended without data", cv)
        self.assertIn("self.auto_ie_range_var = tk.BooleanVar(value=False)", cv)
        self.assertIn("self.ie_range_choices = ie_range_choices(self.pstat)", cv)
        self.assertIn("self.pstat.set_ie_range_mode(True)", cv)
        self.assertIn("self.pstat.set_ie_range_mode(False)", cv)
        self.assertIn('self.pstat.set_ie_range(params["ie_range"])', cv)
        self.assertIn("set_pos_feed_enable", cv)
        self.assertIn("set_cell(True)", cv)
        self.assertIn("set_cell(False)", cv)
        self.assertIn("curve.stop()", cv)
        self.assertIn("self.initial_var = tk.DoubleVar(value=-0.5)", cv)
        self.assertIn("self.apex1_var = tk.DoubleVar(value=-1.5)", cv)
        self.assertIn("self.apex2_var = tk.DoubleVar(value=-0.5)", cv)
        self.assertIn("self.final_var = tk.DoubleVar(value=-0.5)", cv)
        self.assertIn('value=0.10', cv)
        self.assertIn('value=0.001', cv)
        self.assertIn('value=4', cv)
        self.assertNotIn("GamryCA", cv)
        self.assertNotIn("GamryEIS", cv)

    def test_manual_and_automatic_lsv_use_single_ramp_signal(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        manual = (ROOT / "gamry_lsv_manual.py").read_text(encoding="utf-8")
        automatic = (ROOT / "auto_lsv_measurement.py").read_text(encoding="utf-8")
        cycle = (ROOT / "xy_eis_comp_lsv_cycle.py").read_text(encoding="utf-8")
        self.assertIn("ManualLSVWindow", ui)
        self.assertIn("signal_ramp_new", manual)
        self.assertIn("set_signal_ramp", manual)
        self.assertNotIn("signal_r_up_dn_new", manual)
        self.assertIn("signal_ramp_new", automatic)
        self.assertIn("set_signal_ramp", automatic)
        self.assertIn('return "EIS_GetRu_compensated_LSV"', cycle)
        self.assertIn('"pos_feed_resistance": resistance', cycle)
        self.assertIn("self._restore_measurement_pumps()", cycle)
        self.assertIn("EIS + COMP LSV", (ROOT / "circular_scan_path.py").read_text(encoding="utf-8"))
        self.assertIn("EIS + COMP LSV", (ROOT / "rectangular_scan_path.py").read_text(encoding="utf-8"))

        calls = []
        pstat = types.SimpleNamespace(
            signal_ramp_new=lambda *args: calls.append(("new", args)) or "signal",
            set_signal_ramp=lambda signal: calls.append(("set", signal)),
        )
        tkp = types.SimpleNamespace(PSTATMODE=7)
        manual_window = ManualLSVWindow.__new__(ManualLSVWindow)
        manual_window.pstat, manual_window.tkp = pstat, tkp
        signal = manual_window._create_signal(
            {"initial": -0.2, "final": 0.8, "scan_rate": 0.01}, 0.1
        )
        manual_window._attach_signal(signal)
        self.assertEqual(calls[0], ("new", (-0.2, 0.8, 0.01, 0.1, 7)))
        self.assertEqual(calls[1], ("set", "signal"))

        calls[:] = []
        automatic_runner = AutoLSVMeasurement.__new__(AutoLSVMeasurement)
        automatic_runner.pstat, automatic_runner.tkp = pstat, tkp
        signal = automatic_runner._create_signal(
            {"initial": 0.0, "final": 1.0, "scan_rate": 0.02}, 0.1
        )
        automatic_runner._attach_signal(signal)
        self.assertEqual(calls[0], ("new", (0.0, 1.0, 0.02, 0.1, 7)))
        self.assertEqual(calls[1], ("set", "signal"))

    def test_manual_ca_matches_verified_mg400_parameters(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        ca = (ROOT / "gamry_ca_manual.py").read_text(encoding="utf-8")
        self.assertIn("ManualCAWindow", ui)
        self.assertIn("signal_d_step_new", ca)
        self.assertIn("ChronoCurve", ca)
        self.assertIn("self.signal = self.pstat.signal_d_step_new", ca)
        self.assertIn("self.vinit_var = tk.DoubleVar(value=0.0)", ca)
        self.assertIn("self.vstep1_var = tk.DoubleVar(value=0.0)", ca)
        self.assertIn("self.tstep1_var = tk.DoubleVar(value=3.0)", ca)
        self.assertIn("self.vstep2_var = tk.DoubleVar(value=0.0)", ca)
        self.assertIn("self.sample_period_var = tk.DoubleVar(value=0.005)", ca)
        self.assertIn("set_cell(True)", ca)
        self.assertIn("set_cell(False)", ca)
        self.assertIn("self.curve.stop()", ca)
        self.assertIn("self.gamry_measurement_owner", ui)
        self.assertNotIn("GamryEIS", ca)

    def test_manual_eis_getru_uses_verified_engine_and_is_independent(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        eis_ui = (ROOT / "gamry_eis_manual.py").read_text(encoding="utf-8")
        engine = (ROOT / "pre_gamry_eis_getru_max.py").read_text(encoding="utf-8")
        self.assertIn("ManualEISWindow", ui)
        self.assertIn("GamryEIS", eis_ui)
        self.assertIn("DEFAULT_FREQUENCIES", eis_ui)
        self.assertIn("self.dc_bias_var = tk.DoubleVar(value=-1.0)", eis_ui)
        self.assertIn("self.ac_amplitude_var = tk.DoubleVar(value=0.007)", eis_ui)
        self.assertIn("self.zmod_var = tk.DoubleVar(value=40.0)", eis_ui)
        self.assertIn("self.max_phase_var = tk.DoubleVar(value=7.5)", eis_ui)
        self.assertIn("self.repeats_var = tk.IntVar(value=3)", eis_ui)
        self.assertIn("self.passes_var = tk.IntVar(value=10)", eis_ui)
        self.assertIn("self.gamry_measurement_owner", ui)
        self.assertIn("class EISContactError", engine)
        self.assertIn("def run_eis", engine)
        self.assertIn("GetRu accepted", engine)
        self.assertNotIn("run_cv", eis_ui)
        self.assertNotIn("run_ca", eis_ui)

    def test_gamry_disconnect_releases_all_pstat_owners(self):
        source = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertIn("def _dispose_gamry_windows(self):", source)
        self.assertIn('"xy_full_eis_window", "xy_eis_cv_window"', source)
        self.assertIn("runner.pstat = None", source)
        self.assertIn("self._dispose_gamry_windows()", source)
        self.assertIn("gc.collect()", source)

    def test_eis_initialization_clears_pos_feed_before_disabling_it(self):
        source = (ROOT / "pre_gamry_eis_getru_max.py").read_text(encoding="utf-8")
        initialize = source[source.index("def initialize_pstat"):source.index("def _normalize_pose4")]
        self.assertLess(
            initialize.index("pstat.set_pos_feed_resistance(0.0)"),
            initialize.index("pstat.set_pos_feed_enable(False)"),
        )

    def test_circular_scan_path_is_centered_clipped_and_serpentine(self):
        points = generate_circular_path(10.0, -5.0, 4.0, 1.0)
        self.assertEqual(len(points), 13)
        self.assertEqual(points[0], {"index": 1, "x": 10.0, "y": -7.0})
        self.assertEqual(points[-1], {"index": 13, "x": 10.0, "y": -3.0})
        for expected_index, point in enumerate(points, start=1):
            self.assertEqual(point["index"], expected_index)
            self.assertLessEqual(
                (point["x"] - 10.0) ** 2 + (point["y"] + 5.0) ** 2,
                4.0 + 1e-10,
            )
        middle_row = [point["x"] for point in points if point["y"] == -5.0]
        self.assertEqual(middle_row, [8.0, 9.0, 10.0, 11.0, 12.0])

    def test_circular_scan_path_rejects_invalid_geometry(self):
        for diameter, spacing in ((0.0, 1.0), (-1.0, 1.0), (2.0, 0.0), (2.0, -1.0)):
            with self.assertRaises(ValueError):
                generate_circular_path(0.0, 0.0, diameter, spacing)

    def test_circular_scan_preview_has_separate_path_and_reference_diameters(self):
        source = (ROOT / "circular_scan_path.py").read_text(encoding="utf-8")
        self.assertIn("self.path_diameter_var = tk.DoubleVar(value=43.0)", source)
        self.assertIn("self.reference_diameter_var = tk.DoubleVar(value=46.0)", source)
        self.assertIn("Path circle: D=", source)
        self.assertIn("Reference circle: D=", source)

    def test_rectangular_scan_path_is_centered_and_serpentine(self):
        points = generate_rectangular_path(10.0, 20.0, 4.0, 2.0, 2.0, 1.0, True)
        self.assertEqual(len(points), 9)
        self.assertEqual(
            [(point["x"], point["y"]) for point in points],
            [
                (8.0, 19.0), (10.0, 19.0), (12.0, 19.0),
                (12.0, 20.0), (10.0, 20.0), (8.0, 20.0),
                (8.0, 21.0), (10.0, 21.0), (12.0, 21.0),
            ],
        )

    def test_rectangular_scan_path_can_disable_serpentine(self):
        points = generate_rectangular_path(0.0, 0.0, 2.0, 1.0, 1.0, 1.0, False)
        self.assertEqual(
            [(point["x"], point["y"]) for point in points],
            [(-1.0, -0.5), (0.0, -0.5), (1.0, -0.5),
             (-1.0, 0.5), (0.0, 0.5), (1.0, 0.5)],
        )

    def test_rectangular_scan_preview_has_path_and_reference_rectangles(self):
        source = (ROOT / "rectangular_scan_path.py").read_text(encoding="utf-8")
        self.assertIn("self.width_var = tk.DoubleVar(value=10.0)", source)
        self.assertIn("self.height_var = tk.DoubleVar(value=10.0)", source)
        self.assertIn("self.reference_width_var = tk.DoubleVar(value=20.0)", source)
        self.assertIn("self.reference_height_var = tk.DoubleVar(value=20.0)", source)
        self.assertIn('label="Path:', source)
        self.assertIn('label="Reference:', source)

    def test_scan_previews_have_hover_index_and_operator_view(self):
        for filename in ("circular_scan_path.py", "rectangular_scan_path.py"):
            source = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn('mpl_connect("motion_notify_event", self._on_plot_hover)', source)
            self.assertIn('"Index {}\\nX={:.3f}, Y={:.3f}"', source)
            self.assertIn("self.axes.invert_yaxis()", source)
            self.assertNotIn("Operator view", source)

    def test_xy_dry_run_is_xy_only_and_waits_for_move_done(self):
        source = (ROOT / "xy_dry_run.py").read_text(encoding="utf-8")
        self.assertIn('"MOVE {} {:.3f}".format(axis, delta)', source)
        self.assertIn('line == "EVENT,MOVE_DONE,X"', source)
        self.assertIn('line == "EVENT,MOVE_DONE,Y"', source)
        self.assertIn('self.send_command("MOTION_STOP")', source)
        self.assertNotIn('"PRESS ', source)
        self.assertNotIn('"PUMP ', source)
        self.assertNotIn("gamry_pstat", source)

    def test_xy_press_cycle_waits_for_force_stop_and_lift_done(self):
        source = (ROOT / "xy_press_cycle.py").read_text(encoding="utf-8")
        self.assertIn('"PRESS {:.2f} {:.3f} {:.3f}"', source)
        self.assertIn('line.startswith("EVENT,PRESS_FORCE_STOP")', source)
        self.assertIn("self.lift_distance_var = tk.DoubleVar(value=3.0)", source)
        self.assertIn("self.lift_speed_var = tk.DoubleVar(value=0.25)", source)
        self.assertIn('"LIFT {:.3f} {:.3f}".format(', source)
        self.assertIn('line == "EVENT,LIFT_DONE"', source)
        self.assertIn("maximum press travel reached", source)
        self.assertIn("self.press_pump_enabled_var = tk.BooleanVar(value=False)", source)
        self.assertIn('"PUMP RUN {} {:.2f}"', source)
        self.assertNotIn("gamry_pstat", source)
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "bool startLift(float requestedLiftMM = 0.0f, float requestedLiftSpeed = 0.0f)",
            firmware,
        )
        self.assertIn('Serial.println(F("ERROR,BAD_LIFT"))', firmware)
        self.assertIn('Serial.println(F("ERROR,BAD_LIFT_SPEED"))', firmware)

    def test_xy_pump_cycle_stops_pump_before_lift_and_has_no_gamry(self):
        source = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        self.assertIn("self.pump_enabled_vars", source)
        self.assertIn("self.pump_enabled_vars = [\n            tk.BooleanVar(value=True),\n            tk.BooleanVar(value=True),\n        ]", source)
        self.assertIn("self.pump_rpm_vars", source)
        self.assertIn("self.pump_duration_vars", source)
        self.assertIn("self.pressed_stabilization_var = tk.DoubleVar(value=10.0)", source)
        self.assertIn("self.pre_lift_wait_var = tk.DoubleVar(value=1.0)", source)
        self.assertIn('self.press_state = "stabilizing_with_pumps"', source)
        self.assertIn("self._start_pumps", source)
        self.assertIn("def _hold_with_pumps_before", source)
        self.assertIn('self.press_state = "pre_lift_wait"', source)
        self.assertIn("self._start_lift_after_wait", source)
        self.assertIn('"PUMP SPEED {} {:.2f}"', source)
        self.assertIn('"PUMP START {}"', source)
        self.assertIn('"PUMP STOP {}"', source)
        self.assertIn("if self.running_pumps:", source)
        self.assertLess(source.index("self._stop_pump(pump_id)"), source.index('self.press_state = "lifting"'))
        self.assertNotIn("gamry_pstat", source)

    def test_full_cv_cycle_measures_before_lift_and_saves_per_point(self):
        source = (ROOT / "xy_full_cv_cycle.py").read_text(encoding="utf-8")
        pumps = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        runner = (ROOT / "auto_cv_measurement.py").read_text(encoding="utf-8")
        self.assertIn("AutoCVMeasurement", source)
        self.assertIn("CV_point_{:04d}", source)
        self.assertIn('"PUMP START {}"', pumps)
        self.assertIn("self._start_selected_pumps", source)
        self.assertLess(source.index("self.cv_runner.start("), source.index("XYPumpCycleWindow._start_lift_after_wait(self)"))
        cv_complete = source[source.index("    def _cv_complete"):source.index("    def _start_lift_after_wait")]
        self.assertIn("self.pump_after_ids[pump_id] = self.after(", cv_complete)
        self.assertIn("self._pump_time_complete(selected_id)", cv_complete)
        lift_wait = source[source.index("    def _start_lift_after_wait"):source.index("    def _cv_error")]
        self.assertIn("self.reported_pump_states != (False, False)", lift_wait)
        self.assertIn("XYPumpCycleWindow._start_lift_after_wait(self)", lift_wait)
        self.assertIn("initialize_pstat_for_cv", runner)
        self.assertIn("self.pstat.set_cell(True)", runner)
        self.assertIn("self.pstat.set_cell(False)", runner)
        self.assertIn('"point_index", "scan_x_mm", "scan_y_mm"', runner)
        self.assertIn("self._restore_measurement_pumps()", source)

    def test_automatic_cycles_require_homing_and_restore_pumps_after_gamry_setup(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        pump = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        ca = (ROOT / "xy_full_ca_cycle.py").read_text(encoding="utf-8")
        compensated = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        self.assertIn("Home X and Y before starting an automated cycle", ui)
        self.assertIn("def _restore_measurement_pumps", pump)
        self.assertIn("self._restore_measurement_pumps()", ca)
        self.assertIn("self._restore_measurement_pumps()", compensated)

    def test_pumps_have_a_separate_watchdog_for_blocking_eis_calls(self):
        firmware = (
            ROOT / "mega_x_controller" / "mega_x_controller.ino"
        ).read_text(encoding="utf-8")
        self.assertIn("const unsigned long PUMP_WATCHDOG_MS = 30000;", firmware)
        self.assertIn(
            "if (anyAxisMoving() && millis() - lastCommandMillis > COMMAND_WATCHDOG_MS)",
            firmware,
        )
        self.assertIn(
            "if (anyPumpRunning() && millis() - lastCommandMillis > PUMP_WATCHDOG_MS)",
            firmware,
        )

    def test_full_eis_cycle_keeps_pumps_on_until_getru_finishes(self):
        source = (ROOT / "xy_full_eis_cycle.py").read_text(encoding="utf-8")
        pumps = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        runner = (ROOT / "auto_eis_measurement.py").read_text(encoding="utf-8")
        self.assertIn("GamryEIS", runner)
        self.assertIn("result[\"avg_ru\"]", source)
        self.assertIn("EIS_point_{:04d}", source)
        self.assertIn('"PUMP START {}"', pumps)
        self.assertIn("self._start_selected_pumps", source)
        complete = source[source.index("    def _eis_complete"):source.index("    def _start_lift_after_wait")]
        self.assertIn("self._pump_time_complete(selected_id)", complete)
        self.assertIn("self.reported_pump_states != (False, False)", source)

    def test_eis_failure_retries_then_skips_and_stops_after_three_points(self):
        cycle = XYFullEISCycleWindow.__new__(XYFullEISCycleWindow)
        events = []
        failed_attempts = []
        cycle.running = True
        cycle.points = [{"index": index} for index in (1, 2, 3, 4)]
        cycle.point_offset = 0
        cycle.max_eis_attempts = 2
        cycle.eis_attempt_count = 0
        cycle.consecutive_failed_points = 0
        cycle.eis_failure_pending = None
        cycle.data_manager = types.SimpleNamespace(fail_active=failed_attempts.append)
        cycle.status_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.completed_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.after = lambda unused_ms, callback: events.append(callback.__name__) or "timer"
        cycle._finish = lambda message: events.append(message)
        cycle._arrived = lambda: events.append("arrived")
        cycle._move_x = lambda: events.append("move_x")

        for point_index in (0, 1, 2):
            cycle.eis_attempt_count = 1
            cycle.eis_failure_pending = "bad contact"
            XYFullEISCycleWindow._complete_pressed_point(cycle)
            self.assertEqual(cycle.point_offset, point_index)
            self.assertEqual(events[-1], "<lambda>")
            cycle.eis_attempt_count = 2
            cycle.eis_failure_pending = "bad contact"
            XYFullEISCycleWindow._complete_pressed_point(cycle)
            self.assertEqual(cycle.point_offset, point_index + 1)
        self.assertEqual(len(failed_attempts), 6)
        self.assertIn("three consecutive points", events[-1])

    def test_eis_error_runs_pumps_then_waits_before_lift(self):
        cycle = XYFullEISCycleWindow.__new__(XYFullEISCycleWindow)
        events = []
        timers = []
        cycle.running = True
        cycle.eis_failure_pending = None
        cycle.eis_attempt_count = 0
        cycle.max_eis_attempts = 3
        cycle.pump_configs = [(0, 4.0, 4.0), (1, 3.7, 2.0)]
        cycle.pump_after_ids = {}
        cycle.running_pumps = {0, 1}
        cycle.pre_lift_wait = 1.0
        cycle.status_var = types.SimpleNamespace(set=lambda unused: None)
        cycle._stop_pump = lambda pump_id: (events.append("stop {}".format(pump_id)), cycle.running_pumps.discard(pump_id))
        cycle._start_lift_after_wait = lambda: events.append("lift check")
        cycle.after = lambda ms, callback: timers.append((ms, callback)) or len(timers)
        XYFullEISCycleWindow._eis_error(cycle, "bad contact")
        self.assertEqual(events, [])
        self.assertEqual([ms for ms, unused in timers], [4000, 2000])
        timers[1][1]()
        self.assertEqual(events, ["stop 1"])
        timers[0][1]()
        self.assertEqual(events, ["stop 1", "stop 0"])
        self.assertEqual(timers[2][0], 1000)
        timers[2][1]()
        self.assertEqual(events[-1], "lift check")
        self.assertEqual(cycle.eis_attempt_count, 1)

    def test_resume_eis_failure_uses_saved_post_eis_times(self):
        cycle = XYEISCompCVCycleWindow.__new__(XYEISCompCVCycleWindow)
        timers = []
        stopped = []
        cycle.running = True
        cycle.resume_manager = object()
        cycle.eis_failure_pending = None
        cycle.eis_attempt_count = 0
        cycle.max_eis_attempts = 3
        cycle.pump_configs = [(0, 4.0, 1.8), (1, 3.7, 2.0)]
        cycle.pump_after_ids = {}
        cycle.running_pumps = {0, 1}
        cycle.pre_lift_wait = 1.0
        cycle.status_var = types.SimpleNamespace(set=lambda unused: None)
        cycle._stop_pump = lambda pump_id: (stopped.append(pump_id), cycle.running_pumps.discard(pump_id))
        cycle.after = lambda ms, callback: timers.append((ms, callback)) or len(timers)

        cycle._eis_error("bad contact")
        self.assertEqual([ms for ms, unused in timers], [1800, 2000])
        self.assertEqual(stopped, [])
        timers[0][1]()
        self.assertEqual(stopped, [0])
        timers[1][1]()
        self.assertEqual(stopped, [0, 1])
        self.assertEqual(timers[2][0], 1000)
        source = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        self.assertIn('enabled[pump_id]["post_measurement_s"]', source)

    def test_successful_point_resets_eis_failure_streak(self):
        cycle = XYFullEISCycleWindow.__new__(XYFullEISCycleWindow)
        completed = []
        cycle.running = True
        cycle.points = [{"index": 1}, {"index": 2}]
        cycle.point_offset = 0
        cycle.eis_failure_pending = None
        cycle.eis_attempt_count = 2
        cycle.consecutive_failed_points = 2
        cycle.data_manager = types.SimpleNamespace(
            active_index=1, complete_point=completed.append
        )
        cycle.completed_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.status_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.after = lambda unused_ms, unused_callback: "timer"
        XYFullEISCycleWindow._complete_pressed_point(cycle)
        self.assertEqual(completed, [1])
        self.assertEqual(cycle.point_offset, 1)
        self.assertEqual(cycle.eis_attempt_count, 0)
        self.assertEqual(cycle.consecutive_failed_points, 0)

    def test_getru_compensated_cv_uses_measured_ru_and_positive_feedback(self):
        source = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        runner = (ROOT / "auto_cv_measurement.py").read_text(encoding="utf-8")
        self.assertIn('ru = float(result["avg_ru"])', source)
        self.assertIn("resistance = ru * self.comp_fraction", source)
        self.assertIn('"pos_feed_resistance": resistance', source)
        self.assertIn("self.cv_runner.start(", source)
        self.assertIn("resistance > 0.0", runner)
        self.assertIn('self.pstat.set_pos_feed_enable(False)', runner)

    def test_compensated_cv_defaults_match_latest_experiment_screenshot(self):
        source = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        for expected in (
            "self.pressed_stabilization_var.set(10.0)",
            "self.ac_amplitude_var.set(0.040)",
            "self.dc_bias_var.set(-1.0)",
            "self.zmod_var.set(1000.0)",
            "self.cv_initial_var = tk.DoubleVar(value=-0.9)",
            "self.cv_apex1_var = tk.DoubleVar(value=-1.6)",
            "self.cv_apex2_var = tk.DoubleVar(value=-0.9)",
            "self.cv_final_var = tk.DoubleVar(value=-0.9)",
            "self.cv_cycles_var = tk.IntVar(value=3)",
            "self.comp_fraction_var = tk.DoubleVar(value=0.0)",
            "self.pos_feed_var = tk.BooleanVar(value=False)",
        ):
            self.assertIn(expected, source)

    def test_compensated_cv_uses_hardware_ie_range_list(self):
        source = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        lsv = (ROOT / "xy_eis_comp_lsv_cycle.py").read_text(encoding="utf-8")
        helper = (ROOT / "gamry_ie_range.py").read_text(encoding="utf-8")
        self.assertIn("pstat.ie_range_lower_limit()", helper)
        self.assertIn("pstat.ie_range_upper_limit()", helper)
        self.assertIn("pstat.ie_range_label(range_id)", helper)
        self.assertIn('state="readonly"', source)
        self.assertIn('"ie_range": ie_range_enum(self.cv_ie_range_var.get())', source)
        self.assertIn('"ie_range": ie_range_enum(self.cv_ie_range_var.get())', lsv)

    def test_every_ie_range_control_is_a_hardware_dropdown(self):
        files = (
            "gamry_cv_manual.py", "gamry_ca_manual.py", "gamry_lsv_manual.py",
            "xy_full_cv_cycle.py", "xy_full_ca_cycle.py",
            "xy_eis_comp_cv_cycle.py", "xy_eis_comp_lsv_cycle.py",
        )
        for filename in files:
            source = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("ie_range_enum(", source, filename)
        for filename in files[:-2] + ("xy_eis_comp_cv_cycle.py",):
            source = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("ttk.Combobox", source, filename)
            self.assertIn('state="readonly"', source, filename)

    def test_press_force_defaults_match_manual_and_automatic_setup(self):
        main = (ROOT / "x_control.py").read_text(encoding="utf-8")
        automatic = (ROOT / "xy_press_cycle.py").read_text(encoding="utf-8")
        self.assertIn("self.press_force_var = tk.DoubleVar(value=60.0)", main)
        self.assertIn("self.press_force_var = tk.DoubleVar(value=40.0)", automatic)

    def test_automatic_measurement_defaults_match_experiment_screenshot(self):
        eis = (ROOT / "xy_full_eis_cycle.py").read_text(encoding="utf-8")
        cv = (ROOT / "xy_full_cv_cycle.py").read_text(encoding="utf-8")
        combined = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        ca = (ROOT / "xy_full_ca_cycle.py").read_text(encoding="utf-8")
        for expected in (
            "self.ac_amplitude_var = tk.DoubleVar(value=0.040)",
            "self.dc_bias_var = tk.DoubleVar(value=-1.0)",
            "self.zmod_var = tk.DoubleVar(value=1000.0)",
        ):
            self.assertIn(expected, eis)
        for expected in (
            "self.cv_initial_var = tk.DoubleVar(value=-0.9)",
            "self.cv_apex1_var = tk.DoubleVar(value=-1.6)",
            "self.cv_cycles_var = tk.IntVar(value=3)",
            "value=ie_range_display(8, self.cv_ie_range_choices)",
        ):
            self.assertIn(expected, cv)
        self.assertIn("ie_range_display(8, self.cv_ie_range_choices)", combined)
        self.assertIn("ie_range_display(8, self.ie_range_choices)", ca)

    def test_press_shows_forty_gram_offset_hint_without_firmware_compensation(self):
        firmware = (ROOT / "mega_x_controller" / "mega_x_controller.ino").read_text(
            encoding="utf-8"
        )
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        automatic = (ROOT / "xy_press_cycle.py").read_text(encoding="utf-8")
        self.assertNotIn("PRESS_FORCE_OVERSHOOT_OFFSET_G", firmware)
        self.assertIn("Press force (actual ≈ set + 40 g):", ui)
        self.assertIn("actual ≈ set + 40", automatic)
        self.assertIn('self.press_status_var.set("PRESSING")', ui)
        self.assertNotIn('"PRESSING — {:.3f} mm"', ui)

    def test_automatic_cv_and_eis_have_live_plots(self):
        plot = (ROOT / "automatic_live_plot.py").read_text(encoding="utf-8")
        cv = (ROOT / "xy_full_cv_cycle.py").read_text(encoding="utf-8")
        eis = (ROOT / "xy_full_eis_cycle.py").read_text(encoding="utf-8")
        self.assertIn("def update_cv", plot)
        self.assertIn("def update_eis_csv", plot)
        self.assertIn("self.live_plot.update_cv", cv)
        self.assertIn("self.live_plot.update_eis_csv", eis)

    def test_manual_and_automatic_cv_use_robust_autoscaling(self):
        helper = (ROOT / "cv_plot_scale.py").read_text(encoding="utf-8")
        manual = (ROOT / "gamry_cv_manual.py").read_text(encoding="utf-8")
        automatic = (ROOT / "automatic_live_plot.py").read_text(encoding="utf-8")
        self.assertIn("startup_count", helper)
        self.assertIn("np.percentile(scale_values, (1.0, 99.0))", helper)
        self.assertIn("autoscale_cv_axes(self.axes, voltage, current)", manual)
        self.assertIn(
            "autoscale_cv_axes(self.cv_axes, voltage_values, current_values)", automatic
        )

    def test_automatic_ca_has_live_plot_and_measurement_interlocks(self):
        plot = (ROOT / "automatic_live_plot.py").read_text(encoding="utf-8")
        source = (ROOT / "xy_full_ca_cycle.py").read_text(encoding="utf-8")
        pumps = (ROOT / "xy_pump_cycle.py").read_text(encoding="utf-8")
        runner = (ROOT / "auto_ca_measurement.py").read_text(encoding="utf-8")
        self.assertIn("def update_ca", plot)
        self.assertIn("self.live_plot.update_ca", source)
        self.assertIn("signal_d_step_new", runner)
        self.assertIn('"PUMP START {}"', pumps)
        self.assertIn("self._start_selected_pumps", source)
        self.assertIn("self._pump_time_complete(selected_id)", source)

    def test_experiment_manifest_tracks_point_attempt_without_overwrite(self):
        points = [{"index": 1, "x": 1.0, "y": -2.0}]
        with tempfile.TemporaryDirectory() as folder:
            manager = ExperimentDataManager(folder, "CV", "test path", points, {"scan": 0.1})
            attempt = manager.begin_point(points[0])
            self.assertEqual(attempt.name, "attempt_001")
            manager.complete_point(1)
            with open(str(manager.manifest_path), "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            self.assertEqual(manifest["points"][0]["status"], "completed")
            self.assertEqual(manifest["points"][0]["attempts"][0]["status"], "completed")

    def test_experiment_resume_preview_is_read_only_and_finds_next_point(self):
        points = [
            {"index": 1, "x": 0.0, "y": 0.0},
            {"index": 2, "x": 1.0, "y": 0.0},
        ]
        with tempfile.TemporaryDirectory() as folder:
            manager = ExperimentDataManager(folder, "CV", "test path", points, {})
            manager.begin_point(points[0])
            manager.complete_point(1)
            before = manager.manifest_path.read_bytes()
            preview = load_experiment_preview(manager.manifest_path)
            after = manager.manifest_path.read_bytes()
            self.assertEqual(before, after)
            self.assertEqual(preview["counts"]["completed"], 1)
            self.assertEqual(preview["counts"]["pending"], 1)
            self.assertEqual(preview["next_point"]["index"], 2)

    def test_resume_manager_preserves_completed_and_creates_next_attempt(self):
        points = [
            {"index": 1, "x": 0.0, "y": 0.0},
            {"index": 2, "x": 1.0, "y": 0.0},
        ]
        with tempfile.TemporaryDirectory() as folder:
            manager = ExperimentDataManager(folder, "EIS_GetRu_compensated_CV", "path", points, {})
            manager.begin_point(points[0]); manager.complete_point(1)
            manager.begin_point(points[1])
            resumed = ExperimentDataManager.from_manifest(manager.manifest_path)
            remaining = resumed.resumable_points()
            self.assertEqual([item["index"] for item in remaining], [2])
            folder2 = resumed.begin_point(remaining[0])
            self.assertEqual(folder2.name, "attempt_002")
            self.assertEqual(resumed.data["points"][0]["status"], "completed")
            attempts = resumed.data["points"][1]["attempts"]
            self.assertEqual(attempts[0]["status"], "failed")
            self.assertEqual(attempts[1]["status"], "running")

    def test_resume_manager_supports_unfinished_all_and_selected_modes(self):
        points = [
            {"index": 1, "x": 0.0, "y": 0.0},
            {"index": 2, "x": 1.0, "y": 0.0},
            {"index": 3, "x": 2.0, "y": 0.0},
        ]
        with tempfile.TemporaryDirectory() as folder:
            manager = ExperimentDataManager(folder, "CV", "path", points, {})
            manager.begin_point(points[0]); manager.complete_point(1)
            self.assertEqual([p["index"] for p in manager.points_for_resume("unfinished")], [2, 3])
            self.assertEqual([p["index"] for p in manager.points_for_resume("all")], [1, 2, 3])
            self.assertEqual([p["index"] for p in manager.points_for_resume("selected", {3, 1})], [1, 3])
            with self.assertRaises(ValueError):
                manager.points_for_resume("selected", set())

    def test_eis_comp_cv_checkpoint_resume_is_explicitly_wired(self):
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        cycle = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        self.assertIn("def prepare_eis_cv_resume", ui)
        self.assertIn("resume_manager=manager", ui)
        self.assertIn("self.xy_resume_window = resume_window", ui)
        self.assertIn("def register_resume_serial_listener", ui)
        self.assertIn("def _active_resume_listener", ui)
        self.assertIn("resume_window.handle_controller_line(line)", ui)
        self.assertIn("resume_window.handle_status(values)", ui)
        self.assertIn("parent.register_resume_serial_listener(self)", cycle)
        self.assertIn("RESUME EVENT ERROR", ui)
        self.assertIn("RESUME STATUS ERROR", ui)
        self.assertIn("self.xy_eis_cv_window is not resume_window", ui)
        home_panel = ui[ui.index("    def build_home_panel"):ui.index("    def prepare_eis_cv_resume")]
        self.assertIn('text="LOAD EXPERIMENT CHECKPOINT"', home_panel)
        self.assertIn("resume_preview_button.grid(", home_panel)
        self.assertNotIn("Milestone 7", ui)
        self.assertIn("def _apply_resume_settings", cycle)
        self.assertIn('self.status_var.set("CHECKPOINT LOADED', cycle)

    def test_resume_restores_saved_scan_zero_without_automatic_homing(self):
        source = (ROOT / "xy_eis_comp_cv_cycle.py").read_text(encoding="utf-8")
        ui = (ROOT / "x_control.py").read_text(encoding="utf-8")
        self.assertNotIn('text="FORMAL EXPERIMENT', source)
        self.assertNotIn('self.send_command("HOME X")', source)
        self.assertNotIn('self.send_command("HOME Y")', source)
        self.assertIn('self.send_command("SCAN_ZERO")', source)
        self.assertIn("def _poll_formal_home", source)
        self.assertIn('self.send_command("STATUS", require_ready=False)', source)
        self.assertIn("def _start_formal_origin_move", source)
        self.assertIn("stopped {:.3f} mm from saved Scan Zero", source)
        self.assertIn("def _complete_formal_alignment", source)
        self.assertIn("def _start_standard_measurement", source)
        self.assertIn("self.after(100, self._start_standard_measurement)", source)
        self.assertNotIn("self.after(100, self.start)", source)
        self.assertIn('settings["scan_origin_machine"]', source)
        self.assertIn("homed_provider=self.xy_homed_state", ui)
        self.assertIn("home_status_provider=self.xy_home_status", ui)
        self.assertIn("def xy_home_status", ui)
        self.assertIn("def _start_resumed_measurement_only", source)
        self.assertIn("XYPumpCycleWindow.start(self)", source)
        self.assertIn("def _start_resume_axis_move", source)
        self.assertIn("def _poll_resume_move", source)
        self.assertIn("RESUME_POSITION_TOLERANCE_MM = 0.004", source)

    def test_resume_positioner_advances_x_then_y_then_arrives(self):
        cycle = XYEISCompCVCycleWindow.__new__(XYEISCompCVCycleWindow)
        position = [0.0, 0.0]
        sent = []
        statuses = []
        arrived = []
        scheduled = {"callback": None}
        cycle.resume_manager = object()
        cycle.formal_alignment_state = None
        cycle.running = True
        cycle.points = [{"index": 3, "x": 1.5, "y": -2.0}]
        cycle.point_offset = 0
        cycle.resume_move_axis = None
        cycle.resume_move_target = None
        cycle.resume_move_deadline = None
        cycle.resume_move_poll_id = None
        cycle.awaiting_axis = None
        cycle.after_id = None
        cycle.speed_var = types.SimpleNamespace(get=lambda: 1.0)
        cycle.status_var = types.SimpleNamespace(set=statuses.append)
        cycle.position_provider = lambda: tuple(position)
        cycle.send_command = lambda command, require_ready=True: sent.append(command) or True
        cycle.after_cancel = lambda unused_id: None
        cycle.after = lambda unused_ms, callback: scheduled.update(callback=callback) or "after-id"
        cycle._finish = lambda message: self.fail(message)
        cycle._arrived = lambda: arrived.append(True)

        cycle._start_resume_axis_move("X", 1.5)
        self.assertEqual(sent[-1], "MOVE X 1.500")
        position[0] = 1.5
        cycle._poll_resume_move()
        self.assertEqual(cycle.resume_move_axis, None)
        scheduled["callback"]()
        self.assertEqual(sent[-1], "MOVE Y -2.000")
        position[1] = -2.0
        cycle._poll_resume_move()
        scheduled["callback"]()
        self.assertEqual(arrived, [True])

    def test_resume_press_bridge_starts_existing_pump_flow_after_force_stop(self):
        cycle = XYEISCompCVCycleWindow.__new__(XYEISCompCVCycleWindow)
        started = []
        cycle.resume_manager = object()
        cycle.formal_alignment_state = None
        cycle.resume_move_axis = None
        cycle.resume_press_poll_id = None
        cycle.resume_press_peak_force = 0.0
        cycle.resume_press_motion_seen = False
        cycle.running = True
        cycle.press_state = "pressing"
        cycle.press_force = 30.0
        cycle.reported_pump_states = None
        cycle.awaiting_axis = None
        cycle.xy_move_axis = None
        cycle.xy_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.status_var = types.SimpleNamespace(set=lambda unused: None)
        cycle.after_cancel = lambda unused: None
        cycle._resume_trace = lambda unused: None
        cycle._start_pumps = lambda: started.append(True)

        moving = {
            "PR": "1", "F": "30.2", "PD": "0.5", "SX": "1.5", "SY": "1.5",
            "PUMP0RUNNING": "0", "PUMP1RUNNING": "0",
        }
        stopped = dict(moving, PR="0", F="29.5")
        cycle.handle_status(moving)
        self.assertEqual(started, [])
        cycle.handle_status(stopped)
        self.assertEqual(started, [True])

    def test_resume_listener_registers_during_window_construction_lifecycle(self):
        ui = GantryMotionUI.__new__(GantryMotionUI)
        ui.resume_serial_listeners = []
        listener = types.SimpleNamespace(winfo_exists=lambda: 1)
        ui.register_resume_serial_listener(listener)
        self.assertIs(ui._active_resume_listener(), listener)

    def test_checkpoint_preview_plots_status_and_supports_hover_selection(self):
        source = (ROOT / "experiment_resume_preview.py").read_text(encoding="utf-8")
        for status in ("completed", "failed", "running", "pending"):
            self.assertIn('"{}"'.format(status), source)
        self.assertIn('mpl_connect("motion_notify_event", self._on_hover)', source)
        self.assertIn('mpl_connect("button_press_event", self._on_click)', source)
        self.assertIn("Last reason:", source)
        self.assertIn("self.axes.invert_yaxis()", source)
        self.assertIn("self.selected_indices", source)
        self.assertIn('text="RESUME UNFINISHED"', source)
        self.assertIn('text="RESTART ALL"', source)
        self.assertIn('text="REMEASURE SELECTED"', source)

    def test_python_sources_parse_on_python_37_grammar(self):
        for relative_path in (
            "x_control.py",
            "gamry_cv_manual.py",
            "gamry_ca_manual.py",
            "gamry_eis_manual.py",
            "pre_gamry_eis_getru_max.py",
            "circular_scan_path.py",
            "rectangular_scan_path.py",
            "xy_dry_run.py",
            "xy_press_cycle.py",
            "xy_pump_cycle.py",
            "auto_cv_measurement.py",
            "xy_full_cv_cycle.py",
            "auto_eis_measurement.py",
            "xy_full_eis_cycle.py",
            "automatic_live_plot.py",
            "xy_eis_comp_cv_cycle.py",
            "auto_ca_measurement.py",
            "xy_full_ca_cycle.py",
            "experiment_data.py",
            "experiment_resume_preview.py",
            "tests/test_motion_protocol.py",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            ast.parse(source, filename=relative_path)


if __name__ == "__main__":
    unittest.main()
