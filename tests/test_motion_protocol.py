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
            "STATUS,X=1.250,Y=-2.000,Z=0.006,L=010101,M=101"
        )
        self.assertEqual(values["X"], "1.250")
        self.assertEqual(values["YMIN"], "0")
        self.assertEqual(values["YPLUS"], "1")
        self.assertEqual(values["XMOVING"], "1")
        self.assertEqual(values["YMOVING"], "0")
        self.assertEqual(values["ZMOVING"], "1")

    def test_bad_status_is_rejected(self):
        bad_lines = (
            "OK,STOP",
            "STATUS,X=0,Y=0,Z=0,L=00000,M=000",
            "STATUS,X=0,Y=0,Z=0,L=00000X,M=000",
            "STATUS,X=0,Y=0,Z=0,L=000000,M=00",
            "STATUS,X=no,Y=0,Z=0,L=000000,M=000",
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
            b"GANTRY_M1\r\nSTATUS,X=0.000,Y=0.000,Z=0.000,L=000000,M=000\n",
        )
        self.assertEqual(
            lines,
            [
                "IDENTITY,MEGA_GANTRY_M1",
                "STATUS,X=0.000,Y=0.000,Z=0.000,L=000000,M=000",
            ],
        )
        self.assertEqual(buffer, b"")

    def test_jog_release_uses_immediate_stop(self):
        source = (ROOT / "x_control.py").read_text(encoding="utf-8")
        function_source = source.split("    def stop_jog", 1)[1].split(
            "    def on_global_button_release", 1
        )[0]
        self.assertIn('self.send_command("STOP")', function_source)
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

    def test_python_sources_parse_on_python_37_grammar(self):
        for relative_path in ("x_control.py", "tests/test_motion_protocol.py"):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            ast.parse(source, filename=relative_path)


if __name__ == "__main__":
    unittest.main()
