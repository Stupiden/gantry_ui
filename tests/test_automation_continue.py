"""Headless regression tests for stop/continue and explicit restart."""
import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from xy_dry_run import XYDryRunWindow


def window(offset=1):
    ui = SimpleNamespace(
        running=False, point_offset=offset,
        points=[dict(index=i, x=i, y=0) for i in range(1, 4)],
        speed_var=Mock(), dwell_var=Mock(), start_guard=lambda: (True, ''),
        needs_lift_confirmation=False, data_manager=object(), awaiting_axis=None,
        after_id=None, lock_controls=Mock(), send_command=Mock(return_value=True),
        after=Mock(return_value='scheduled'), after_cancel=Mock(), _move_x=Mock(),
    )
    ui.speed_var.get.return_value = 1
    ui.dwell_var.get.return_value = 1
    for name in ('speed_entry', 'dwell_entry', 'start_button', 'stop_button',
                 'restart_button', 'status_var', 'completed_var', 'current_point_var'):
        setattr(ui, name, Mock())
    ui._confirm_clearance = lambda: XYDryRunWindow._confirm_clearance(ui)
    ui._current_target = lambda: ui.points[ui.point_offset]
    ui._finish = lambda message: XYDryRunWindow._finish(ui, message)
    return ui


class ContinueTests(unittest.TestCase):
    def test_stop_then_continue_preserves_current_point_and_manager(self):
        ui = window()
        manager = ui.data_manager
        ui.running = True
        XYDryRunWindow.stop(ui)
        self.assertFalse(ui.running)
        ui.start_button.configure.assert_called_with(text='CONTINUE', state='normal')
        XYDryRunWindow.start(ui)
        self.assertTrue(ui.running)
        self.assertEqual(ui.point_offset, 1)
        self.assertIs(ui.data_manager, manager)
        ui.status_var.set.assert_called_with('RUNNING — moving to point 2')

    @patch('xy_dry_run.messagebox.askyesno', return_value=False)
    def test_restart_cancel_keeps_progress_and_files(self, question):
        ui = window()
        manager = ui.data_manager
        XYDryRunWindow.restart(ui)
        self.assertEqual(ui.point_offset, 1)
        self.assertIs(ui.data_manager, manager)

    @patch('xy_dry_run.messagebox.askyesno', return_value=True)
    def test_restart_only_resets_after_confirmation_and_waits_for_start(self, question):
        ui = window()
        XYDryRunWindow.restart(ui)
        self.assertEqual(ui.point_offset, 0)
        self.assertIsNone(ui.data_manager)
        self.assertFalse(ui.running)
        ui.send_command.assert_not_called()

    @patch('xy_dry_run.messagebox.showinfo')
    def test_completed_run_cannot_silently_restart(self, info):
        ui = window(offset=3)
        XYDryRunWindow.start(ui)
        self.assertFalse(ui.running)
        ui.send_command.assert_not_called()
        info.assert_called_once()

    @patch('xy_dry_run.messagebox.askyesno', return_value=False)
    def test_continue_requires_clearance_confirmation_when_interrupted_under_load(self, question):
        ui = window()
        ui.needs_lift_confirmation = True
        XYDryRunWindow.start(ui)
        self.assertFalse(ui.running)
        self.assertTrue(ui.needs_lift_confirmation)
        ui.send_command.assert_not_called()

    @patch('xy_dry_run.messagebox.showinfo')
    def test_continue_waits_for_old_measurement_worker(self, info):
        ui = window()
        ui.eis_runner = SimpleNamespace(running=True)
        XYDryRunWindow.start(ui)
        self.assertFalse(ui.running)
        ui.send_command.assert_not_called()

    def test_completion_disables_continue_but_allows_explicit_restart(self):
        ui = window(offset=3)
        ui.running = True
        XYDryRunWindow._finish(ui, 'COMPLETE')
        ui.start_button.configure.assert_called_with(text='COMPLETE', state='disabled')
        ui.restart_button.configure.assert_called_with(state='normal')


class MeasurementRecordTests(unittest.TestCase):
    def test_continue_reuses_experiment_and_restart_creates_new_record(self):
        # Exercise each real measurement start body without loading hardware drivers.
        for filename, parameter_method in (
            ('xy_full_cv_cycle.py', '_cv_parameters'),
            ('xy_full_ca_cycle.py', '_parameters'),
            ('xy_full_eis_cycle.py', '_eis_parameters'),
        ):
            with self.subTest(mode=filename):
                source = ast.parse((Path(__file__).resolve().parents[1] / filename).read_text(encoding='utf-8'))
                cls = next(node for node in source.body if isinstance(node, ast.ClassDef))
                method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'start')
                factory = Mock(return_value=object())
                namespace = {
                    'ExperimentDataManager': factory,
                    'XYPumpCycleWindow': SimpleNamespace(start=lambda ui: setattr(ui, 'running', True)),
                }
                exec(compile(ast.Module(body=[method], type_ignores=[]), filename, 'exec'), namespace)
                manager = object()
                ui = SimpleNamespace(
                    running=False, data_manager=manager, claim_measurement=None,
                    points=[], path_name='path', max_eis_attempts=3,
                    _manifest_settings=lambda params: {}, _experiment_method=lambda: 'EIS',
                    _experiment_settings=lambda: {}, cv_widgets=[], ca_widgets=[], eis_widgets=[],
                )
                setattr(ui, parameter_method, lambda: ({}, 'output'))
                namespace['start'](ui)
                factory.assert_not_called()
                self.assertIs(ui.data_manager, manager)
                ui.running = False
                ui.data_manager = None
                namespace['start'](ui)
                factory.assert_called_once()
                self.assertIs(ui.data_manager, factory.return_value)


if __name__ == '__main__':
    unittest.main()
