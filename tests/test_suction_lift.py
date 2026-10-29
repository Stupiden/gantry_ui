"""Headless sequencing tests; no hardware commands are sent."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from automatic_live_plot import SeparateEISVoltammetryPlots
from xy_pump_cycle import XYPumpCycleWindow
from xy_full_eis_cycle import XYFullEISCycleWindow


def cycle(concurrent=True, cls=XYPumpCycleWindow):
    ui = cls.__new__(cls)
    ui.running = True
    ui.press_state = 'post_measurement_pumping'
    ui.suction_with_lift_var = SimpleNamespace(get=lambda: concurrent)
    ui.suction_configs = [(0, -2., 1.), (1, 2., 2.)]
    ui.running_pumps = {0}
    ui.pump_after_ids = {0: 'normal'}
    ui.status_var = Mock()
    ui.send_command = Mock(return_value=True)
    ui.after = Mock(side_effect=lambda ms, fn: (ms, fn))
    ui.after_cancel = Mock()
    ui.pre_lift_wait = .5
    ui.suction_lift_delay = 1.
    ui.lift_distance = 3.
    ui.lift_speed = 1.
    ui.xy_move_axis = None
    ui._complete_pressed_point = Mock()
    ui._finish = Mock(side_effect=lambda message: setattr(ui, 'running', False))
    return ui


class SuctionLiftTests(unittest.TestCase):
    def test_zero_suction_rpm_or_duration_skips_that_pump(self):
        ui = cycle()
        ui.suction_enabled_var = SimpleNamespace(get=lambda: True)
        ui.suction_rpm_vars = [SimpleNamespace(get=lambda: 0.0),
                               SimpleNamespace(get=lambda: 2.0)]
        ui.suction_duration_vars = [SimpleNamespace(get=lambda: 3.0),
                                    SimpleNamespace(get=lambda: 0.0)]
        self.assertEqual(ui._suction_parameters(), [])
        ui.suction_duration_vars[1] = SimpleNamespace(get=lambda: 2.0)
        self.assertEqual(ui._suction_parameters(), [(1, 2.0, 2.0)])

    def test_no_active_suction_goes_to_existing_pre_lift_wait(self):
        ui = cycle()
        ui.suction_configs = []
        ui._pump_time_complete(0)
        self.assertEqual(ui.press_state, 'pre_lift_wait')
        self.assertFalse(any(c[0][0].startswith('PUMP RUN')
                             for c in ui.send_command.call_args_list))

    def start_overlap(self, ui):
        ui._pump_time_complete(0)
        self.assertEqual(ui.press_state, 'pre_lift_wait')
        ui.send_command.assert_called_once_with('PUMP STOP 0', require_ready=False)
        ms, callback = ui.after.call_args[0]
        self.assertEqual(ms, 500)
        callback()
        self.assertEqual(ui.press_state, 'suction_lifting')
        self.assertEqual([c[0][0] for c in ui.send_command.call_args_list][-3:],
                         ['PUMP STOP 0', 'PUMP RUN 0 -2.00', 'PUMP RUN 1 2.00'])
        self.assertEqual(ui.after.call_args[0][0], 1000)
        ui.after.call_args[0][1]()
        ui.send_command.assert_called_with('LIFT 3.000 1.000')

    def test_lift_finishes_first_waits_for_both_suction_timers(self):
        ui = cycle(); self.start_overlap(ui)
        ui.handle_controller_line('EVENT,LIFT_DONE')
        ui._complete_pressed_point.assert_not_called()
        ui._pump_time_complete(0)
        ui._complete_pressed_point.assert_not_called()
        ui._pump_time_complete(1)
        ui._complete_pressed_point.assert_called_once()
        self.assertEqual(ui.press_state, 'idle')

    def test_suction_finishes_first_waits_for_lift_event(self):
        ui = cycle(); self.start_overlap(ui)
        ui._pump_time_complete(0); ui._pump_time_complete(1)
        ui._complete_pressed_point.assert_not_called()
        ui.handle_controller_line('EVENT,LIFT_DONE')
        ui._complete_pressed_point.assert_called_once()
        ui.handle_controller_line('EVENT,LIFT_DONE')
        ui._complete_pressed_point.assert_called_once()

    def test_lift_does_not_start_before_suction_delay(self):
        ui = cycle()
        ui._pump_time_complete(0)
        ui.after.call_args[0][1]()  # Pre-lift wait ends; suction begins.
        self.assertFalse(any(c[0][0].startswith('LIFT') for c in ui.send_command.call_args_list))
        ui.handle_controller_line('EVENT,LIFT_DONE')  # Ignore stale lift event.
        ui._complete_pressed_point.assert_not_called()
        ui.after.call_args[0][1]()  # One second of suction has elapsed.
        ui.send_command.assert_called_with('LIFT 3.000 1.000')

    def test_unchecked_preserves_sequential_suction(self):
        ui = cycle(False); ui._pump_time_complete(0)
        self.assertEqual(ui.press_state, 'suction')
        self.assertFalse(any(c[0][0].startswith('LIFT') for c in ui.send_command.call_args_list))
        ui._pump_time_complete(0); ui._pump_time_complete(1)
        self.assertEqual(ui.press_state, 'pre_lift_wait')
        ui.after.call_args[0][1]()
        self.assertEqual(ui.press_state, 'lifting')
        ui.send_command.assert_called_with('LIFT 3.000 1.000')

    def test_eis_checks_pumps_stopped_before_overlap(self):
        ui = cycle(cls=XYFullEISCycleWindow)
        ui.running_pumps.clear(); ui.pump_stop_deadline = None
        ui.reported_pump_states = (True, False)
        ui._start_lift_after_wait()
        ui.send_command.assert_not_called()
        ui.reported_pump_states = (False, False)
        ui.after.call_args[0][1]()
        self.assertEqual(ui.press_state, 'suction_lifting')

    def test_suction_send_failure_does_not_start_lift(self):
        ui = cycle(); ui.send_command.side_effect = lambda cmd, **kw: not cmd.startswith('PUMP RUN')
        ui._pump_time_complete(0); ui.after.call_args[0][1]()
        ui._finish.assert_called_once()
        self.assertFalse(any(c[0][0].startswith('LIFT') for c in ui.send_command.call_args_list))

    def test_stop_cancels_pending_pump_callbacks(self):
        ui = cycle(); self.start_overlap(ui)
        ui._stop_all_selected_pumps()
        self.assertFalse(ui.running_pumps)
        self.assertFalse(ui.pump_after_ids)
        self.assertEqual(ui.after_cancel.call_count, 2)

    def test_overlap_delay_must_leave_suction_running(self):
        ui = cycle()
        ui.suction_lift_delay_var = SimpleNamespace(get=lambda: 1.)
        self.assertEqual(ui._suction_lift_delay(ui.suction_configs), 1.)
        ui.suction_lift_delay_var = SimpleNamespace(get=lambda: 2.)
        with self.assertRaises(ValueError):
            ui._suction_lift_delay(ui.suction_configs)

    @patch('automatic_live_plot.AutomaticLivePlot')
    def test_eis_and_cv_use_independent_plot_windows(self, plot_class):
        plot_class.side_effect = (Mock(), Mock())
        plots = SeparateEISVoltammetryPlots(object())
        self.assertEqual(plot_class.call_count, 2)
        self.assertTrue(plot_class.call_args_list[0][1]['show_eis'])
        self.assertTrue(plot_class.call_args_list[1][1]['show_cv'])
        plots.deiconify()
        plots.clear_eis()
        plots.update_eis_csv('test.csv')
        plots.clear_cv()
        plots.update_cv('data')
        plots.eis.deiconify.assert_called_once()
        plots.voltammetry.deiconify.assert_called_once()
        plots.eis.update_eis_csv.assert_called_once_with('test.csv')
        plots.voltammetry.update_cv.assert_called_once_with('data')

if __name__ == '__main__': unittest.main()
