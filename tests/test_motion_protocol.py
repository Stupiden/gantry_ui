import ast
import pathlib
import sys
import types
import unittest


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
from x_control import extract_complete_lines, parse_status_line  # noqa: E402
from circular_scan_path import generate_circular_path  # noqa: E402
from rectangular_scan_path import generate_rectangular_path  # noqa: E402


class MotionProtocolTests(unittest.TestCase):
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
            b"GANTRY_M6\r\nSTATUS,X=0,Y=0,Z=0,SX=0,SY=0,SZ=0,"
            b"L=000000,M=000,H=000,A=00,P=00,PI=2,PO=2,"
            b"LC=1,T=0,R=0,FR=0,F=0,PR=0,PD=0\n",
        )
        self.assertEqual(
            lines,
            [
                "IDENTITY,MEGA_GANTRY_M6",
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
        self.assertIn("PUMP_OUT_STEP = 32", firmware)
        self.assertIn("ISR(TIMER3_COMPA_vect)", firmware)
        self.assertIn("ISR(TIMER4_COMPA_vect)", firmware)
        self.assertIn("digitalWrite(pumps[pumpId].dirPin, LOW)", firmware)
        self.assertIn("stopAllPumps();", firmware)
        self.assertIn('strcmp(verb, "MOTION_STOP")', firmware)
        self.assertIn('self.send_command("PUMP START {}".format(pump_id))', ui)
        self.assertIn('self.send_command("PUMP STOP {}".format(pump_id))', ui)

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
        self.assertIn("self.signal = self.pstat.signal_r_up_dn_new", cv)
        self.assertIn("CV ended without data", cv)
        self.assertIn("self.auto_ie_range_var = tk.BooleanVar(value=False)", cv)
        self.assertIn("self.ie_range_var = tk.DoubleVar(value=10.0)", cv)
        self.assertIn("self.pstat.set_ie_range_mode(True)", cv)
        self.assertIn("self.pstat.set_ie_range_mode(False)", cv)
        self.assertIn('self.pstat.set_ie_range(params["ie_range"])', cv)
        self.assertIn("set_pos_feed_enable", cv)
        self.assertIn("set_cell(True)", cv)
        self.assertIn("set_cell(False)", cv)
        self.assertIn("curve.stop()", cv)
        self.assertGreaterEqual(cv.count("tk.DoubleVar(value=0.0)"), 4)
        self.assertIn('value=0.10', cv)
        self.assertIn('value=0.001', cv)
        self.assertIn('value=4', cv)
        self.assertNotIn("GamryCA", cv)
        self.assertNotIn("GamryEIS", cv)

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
        self.assertIn("self.dc_bias_var = tk.DoubleVar(value=0.0)", eis_ui)
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
        self.assertIn("self.lift_speed_var = tk.DoubleVar(value=2.0)", source)
        self.assertIn('"LIFT {:.3f} {:.3f}".format(', source)
        self.assertIn('line == "EVENT,LIFT_DONE"', source)
        self.assertIn("maximum press travel reached", source)
        self.assertNotIn('"PUMP ', source)
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
        self.assertIn("self.pump_rpm_vars", source)
        self.assertIn("self.pump_duration_vars", source)
        self.assertIn("self.pressed_stabilization_var = tk.DoubleVar(value=1.0)", source)
        self.assertIn("self.pre_lift_wait_var = tk.DoubleVar(value=1.0)", source)
        self.assertIn('self.press_state = "stabilizing"', source)
        self.assertIn("self._start_pumps", source)
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
        runner = (ROOT / "auto_cv_measurement.py").read_text(encoding="utf-8")
        self.assertIn("AutoCVMeasurement", source)
        self.assertIn("CV_point_{:04d}", source)
        self.assertLess(source.index('"PUMP START {}"'), source.index("self.cv_runner.start("))
        self.assertLess(source.index("self.cv_runner.start("), source.index('self.press_state = "lifting"'))
        cv_complete = source[source.index("    def _cv_complete"):source.index("    def _start_lift_after_wait")]
        self.assertIn("self.pump_after_ids[pump_id] = self.after(", cv_complete)
        self.assertIn("self._pump_time_complete(selected_id)", cv_complete)
        lift_wait = source[source.index("    def _start_lift_after_wait"):source.index("    def _cv_error")]
        self.assertIn("self.reported_pump_states != (False, False)", lift_wait)
        self.assertIn('"LIFT {:.3f} {:.3f}"', lift_wait)
        self.assertIn("initialize_pstat_for_cv", runner)
        self.assertIn("self.pstat.set_cell(True)", runner)
        self.assertIn("self.pstat.set_cell(False)", runner)
        self.assertIn('"point_index", "scan_x_mm", "scan_y_mm"', runner)

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
            "tests/test_motion_protocol.py",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            ast.parse(source, filename=relative_path)


if __name__ == "__main__":
    unittest.main()
