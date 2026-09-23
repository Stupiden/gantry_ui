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

    def test_python_sources_parse_on_python_37_grammar(self):
        for relative_path in (
            "x_control.py",
            "gamry_cv_manual.py",
            "tests/test_motion_protocol.py",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            ast.parse(source, filename=relative_path)


if __name__ == "__main__":
    unittest.main()
