"""Headless checks for the homepage suction/lift action."""

import unittest

from manual_suck_lift import SuckLiftSequence, validate_suck_lift


class FakeOwner:
    def __init__(self):
        self.pending = {}
        self.next_id = 0

    def after(self, milliseconds, callback):
        self.next_id += 1
        self.pending[self.next_id] = (milliseconds, callback)
        return self.next_id

    def after_cancel(self, after_id):
        self.pending.pop(after_id, None)

    def fire(self, milliseconds):
        for after_id, (delay, callback) in list(self.pending.items()):
            if delay == milliseconds:
                del self.pending[after_id]
                callback()
                return
        raise AssertionError("No {} ms callback".format(milliseconds))


class ManualSuckLiftTests(unittest.TestCase):
    def setUp(self):
        self.owner = FakeOwner()
        self.commands = []
        self.statuses = []
        self.results = []

        def send(command, require_ready=True):
            self.commands.append(command)
            return True

        self.sequence = SuckLiftSequence(
            self.owner, send, self.statuses.append, self.results.append,
        )

    def test_validation_matches_automatic_signed_pump_contract(self):
        configs, delay, distance, speed = validate_suck_lift(
            -2.0, 3.5, 2.0, 5.0, 0.5, 3.0, 0.2,
        )
        self.assertEqual(configs, [(0, -2.0, 3.5), (1, 2.0, 5.0)])
        self.assertEqual((delay, distance, speed), (0.5, 3.0, 0.2))
        with self.assertRaises(ValueError):
            validate_suck_lift(-2, 3, 2, 5, 5, 3, 0.2)

    def test_zero_suction_rpm_or_duration_skips_each_pump(self):
        configs, delay, unused_distance, unused_speed = validate_suck_lift(
            0.0, 3.5, 2.0, 0.0, 0.5, 3.0, 0.2,
        )
        self.assertEqual(configs, [])
        self.assertEqual(delay, 0.0)
        configs, delay, unused_distance, unused_speed = validate_suck_lift(
            0.0, 3.5, 2.0, 2.0, 0.5, 3.0, 0.2,
        )
        self.assertEqual(configs, [(1, 2.0, 2.0)])
        self.assertEqual(delay, 0.5)

    def test_no_active_suction_lifts_without_starting_pumps(self):
        self.assertTrue(self.sequence.start([], 0.0, 3.0, 0.2))
        self.assertEqual(self.commands, ["LIFT 3.000 0.200"])
        self.sequence.handle_controller_line("EVENT,LIFT_DONE")
        self.assertEqual(self.results, ["COMPLETE — lift finished"])

    def test_suck_then_lift_while_each_pump_finishes_independently(self):
        self.assertTrue(self.sequence.start(
            [(0, -2.0, 3.5), (1, 2.0, 5.0)], 0.5, 3.0, 0.2,
        ))
        self.assertEqual(self.commands[:2], ["PUMP RUN 0 -2.00", "PUMP RUN 1 2.00"])
        self.assertFalse(any(command.startswith("LIFT") for command in self.commands))
        self.sequence.handle_status({"PUMP0RUNNING": "1", "PUMP1RUNNING": "1"})
        self.owner.fire(500)
        self.assertEqual(self.commands[-1], "LIFT 3.000 0.200")
        self.sequence.handle_controller_line("EVENT,LIFT_DONE")
        self.assertFalse(self.results)
        self.owner.fire(3500)
        self.assertEqual(self.commands[-1], "PUMP STOP 0")
        self.sequence.handle_status({"PUMP0RUNNING": "0", "PUMP1RUNNING": "1"})
        self.owner.fire(5000)
        self.assertEqual(self.commands[-1], "PUMP STOP 1")
        self.sequence.handle_status({"PUMP0RUNNING": "0", "PUMP1RUNNING": "0"})
        self.assertEqual(self.results, ["COMPLETE — suction and lift finished"])

    def test_missing_pump_aborts_without_lift(self):
        self.sequence.start([(0, -2.0, 1.0)], 0.5, 3.0, 0.2)
        self.sequence.handle_controller_line("ERROR,BAD_PUMP_SPEED")
        self.assertFalse(self.sequence.running)
        self.assertIn("PUMP STOP 0", self.commands)
        self.assertFalse(any(command.startswith("LIFT") for command in self.commands))

    def test_force_limit_aborts_and_stops_running_suction(self):
        self.sequence.start([(0, -2.0, 2.0)], 0.5, 3.0, 0.2)
        self.sequence.handle_status({"PUMP0RUNNING": "1", "PUMP1RUNNING": "0"})
        self.owner.fire(500)
        self.sequence.handle_controller_line("EVENT,Z_FORCE_LIMIT_STOP,501.0")
        self.assertFalse(self.sequence.running)
        self.assertEqual(self.commands[-2:], ["PUMP STOP 0", "MOTION_STOP"])


if __name__ == "__main__":
    unittest.main()
