"""Milestone 15 first increment: full scan cycle with CV only."""

from datetime import datetime
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from auto_cv_measurement import AutoCVMeasurement
from automatic_live_plot import AutomaticLivePlot
from experiment_data import ExperimentDataManager
from gamry_ie_range import choices as ie_range_choices
from gamry_ie_range import display_value as ie_range_display
from gamry_ie_range import enum_value as ie_range_enum
from xy_pump_cycle import XYPumpCycleWindow


class XYFullCVCycleWindow(XYPumpCycleWindow):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        machine_position_provider, start_guard, lock_controls,
        pstat, tkp, claim_measurement, release_measurement
    ):
        XYPumpCycleWindow.__init__(
            self, parent, points, path_name, send_command, position_provider,
            start_guard, lock_controls
        )
        self.title("Full measurement cycle — CV only")
        self.geometry("880x760")
        self.machine_position_provider = machine_position_provider
        self.pstat = pstat
        self.tkp = tkp
        self.claim_measurement = claim_measurement
        self.release_measurement = release_measurement
        self.measurement_claimed = False
        self.reported_pump_states = None
        self.pump_start_deadline = None
        self.pump_stop_deadline = None
        self.cv_runner = AutoCVMeasurement(self, pstat, tkp)
        self.live_plot = AutomaticLivePlot(self, show_cv=True)

        self.cv_initial_var = tk.DoubleVar(value=-0.9)
        self.cv_apex1_var = tk.DoubleVar(value=-1.6)
        self.cv_apex2_var = tk.DoubleVar(value=-0.9)
        self.cv_final_var = tk.DoubleVar(value=-0.9)
        self.cv_scan_rate_var = tk.DoubleVar(value=0.10)
        self.cv_step_var = tk.DoubleVar(value=0.001)
        self.cv_cycles_var = tk.IntVar(value=3)
        self.cv_precharge_var = tk.DoubleVar(value=0.0)
        self.cv_ie_range_choices = ie_range_choices(self.pstat)
        self.cv_ie_range_var = tk.StringVar(
            value=ie_range_display(8, self.cv_ie_range_choices)
        )
        self.auto_ie_range_var = tk.BooleanVar(value=False)
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "auto_cv_data"))
        self.cv_widgets = []
        self._build_cv_controls()

    def _build_cv_controls(self):
        frame = self.start_button.master
        for child in frame.winfo_children():
            try:
                if child.cget("text") == "Duration (s)":
                    child.configure(text="Post-CV run (s)")
            except tk.TclError:
                pass
        self.start_button.grid_configure(row=23, columnspan=1); self.restart_button.grid_configure(row=23)
        self.stop_button.grid_configure(row=23)
        specs = (
            ("Initial V", self.cv_initial_var), ("Apex 1 V", self.cv_apex1_var),
            ("Apex 2 V", self.cv_apex2_var), ("Final V", self.cv_final_var),
            ("Scan V/s", self.cv_scan_rate_var), ("Step V", self.cv_step_var),
            ("Cycles", self.cv_cycles_var), ("Precharge s", self.cv_precharge_var),
            ("IE range", self.cv_ie_range_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = 16 + (index // 5) * 2
            column = index % 5
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5, pady=(7, 2))
            if label == "IE range":
                entry = ttk.Combobox(
                    frame, textvariable=variable, values=self.cv_ie_range_choices,
                    state="readonly", width=14
                )
                self.cv_ie_range_entry = entry
            else:
                entry = ttk.Entry(frame, textvariable=variable, width=9)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5)
            self.cv_widgets.append(entry)
        auto_range = ttk.Checkbutton(
            frame, text="Auto IE range (PF off)", variable=self.auto_ie_range_var
        )
        auto_range.grid(row=20, column=0, sticky="w", padx=5)
        self.cv_widgets.append(auto_range)
        ttk.Label(frame, text="CV output folder").grid(row=20, column=1, sticky="w", padx=5, pady=(8, 2))
        output_entry = ttk.Entry(frame, textvariable=self.output_dir_var, width=54)
        output_entry.grid(row=21, column=0, columnspan=4, sticky="ew", padx=5)
        browse = ttk.Button(frame, text="BROWSE", command=self._browse_output)
        browse.grid(row=21, column=4, padx=5)
        self.cv_widgets.extend((output_entry, browse))
        self.start_button.configure(text="START FULL CV CYCLE")

    def _browse_output(self):
        path = filedialog.askdirectory(parent=self, title="Choose automatic CV output folder")
        if path:
            self.output_dir_var.set(path)

    def _cv_parameters(self):
        try:
            params = {
                "initial": float(self.cv_initial_var.get()),
                "apex1": float(self.cv_apex1_var.get()),
                "apex2": float(self.cv_apex2_var.get()),
                "final": float(self.cv_final_var.get()),
                "scan_rate": float(self.cv_scan_rate_var.get()),
                "step_size": float(self.cv_step_var.get()),
                "cycles": int(self.cv_cycles_var.get()),
                "precharge": float(self.cv_precharge_var.get()),
                "ie_range": ie_range_enum(self.cv_ie_range_var.get()),
                "auto_ie_range": bool(self.auto_ie_range_var.get()),
            }
        except (tk.TclError, ValueError):
            raise ValueError("CV parameters must be numbers")
        if params["scan_rate"] <= 0 or params["step_size"] <= 0:
            raise ValueError("CV scan rate and step must be greater than zero")
        if params["cycles"] < 1 or params["precharge"] < 0 or (
            not params["auto_ie_range"] and params["ie_range"] <= 0
        ):
            raise ValueError("CV cycles/IE range must be positive and precharge non-negative")
        output_dir = Path(self.output_dir_var.get()).expanduser()
        if not str(output_dir).strip():
            raise ValueError("Choose a CV output folder")
        output_dir.mkdir(parents=True, exist_ok=True)
        return params, output_dir

    def start(self):
        if self.running:
            return
        try:
            self.cv_params, self.output_dir = self._cv_parameters()
        except Exception as error:
            messagebox.showerror("Full CV cycle", str(error), parent=self)
            return
        XYPumpCycleWindow.start(self)
        if not self.running:
            return
        if self.data_manager is None:
            self.data_manager = ExperimentDataManager(
                self.output_dir, "CV", self.path_name, self.points,
                self._manifest_settings(self.cv_params)
            )
        if self.claim_measurement is not None:
            if not self.claim_measurement("AUTO_CV"):
                XYPumpCycleWindow.stop(self)
                messagebox.showerror(
                    "Full CV cycle", "Another Gamry measurement is already running", parent=self
                )
                return
            self.measurement_claimed = True
        for widget in self.cv_widgets:
            widget.configure(state="disabled")

    def _start_pumps(self):
        self.after_id = None
        if not self.running:
            return
        self.press_state = "starting_pumps"
        point = self._current_target()
        self.status_var.set("STABILIZED — starting pumps at point {}".format(point["index"]))
        self._start_selected_pumps(self._all_pumps_started_before_cv)

    def _all_pumps_started_before_cv(self):
        if not self.running:
            return
        self.pump_start_deadline = time.monotonic() + 5.0
        self.after_id = self.after(100, self._wait_for_pumps_before_cv)

    def _wait_for_pumps_before_cv(self):
        self.after_id = None
        if not self.running:
            return
        required = {pump_id for pump_id, unused_rpm, unused_duration in self.pump_configs}
        reported = self.reported_pump_states
        confirmed = reported is not None and all(reported[pump_id] for pump_id in required)
        if not confirmed:
            if time.monotonic() >= self.pump_start_deadline:
                self._finish("ABORTED — controller did not confirm selected pumps running; use manual LIFT")
                return
            self.status_var.set("WAITING — confirming selected pumps are running before CV")
            self.after_id = self.after(100, self._wait_for_pumps_before_cv)
            return
        self.pump_start_deadline = None
        self._hold_with_pumps_before(self._start_cv, "CV")

    def _start_cv(self):
        point = self._current_target()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "CV_point_{:04d}_x{:+.3f}_y{:+.3f}_{}.csv".format(
            int(point["index"]), float(point["x"]), float(point["y"]), stamp
        )
        attempt_dir = self.data_manager.active_folder
        self.press_state = "measuring_cv"
        self.live_plot.deiconify()
        self.live_plot.clear_cv()
        self.status_var.set("CV RUNNING AT POINT {} — pumps ON — cell ON".format(point["index"]))
        try:
            self.cv_runner.start(
                self.cv_params, attempt_dir / filename, point,
                self.machine_position_provider(), self._cv_complete, self._cv_error,
                self.live_plot.update_cv,
            )
            self._restore_measurement_pumps()
        except Exception as error:
            self._cv_error(error)

    def handle_status(self, values):
        try:
            self.reported_pump_states = (
                values["PUMP0RUNNING"] == "1", values["PUMP1RUNNING"] == "1"
            )
        except KeyError:
            pass
        XYPumpCycleWindow.handle_status(self, values)

    def _cv_complete(self, point_count, csv_path):
        if not self.running:
            return
        self.press_state = "post_cv_pumping"
        self.status_var.set("CV SAVED — {} points — pumps remain ON".format(point_count))
        for pump_id, unused_rpm, duration in self.pump_configs:
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _start_lift_after_wait(self):
        self.after_id = None
        if not self.running:
            return
        if self.pump_stop_deadline is None:
            self.pump_stop_deadline = time.monotonic() + 5.0
        if self.reported_pump_states != (False, False):
            if time.monotonic() >= self.pump_stop_deadline:
                self._finish("ABORTED — controller did not confirm both pumps stopped; use manual LIFT")
                return
            self.status_var.set("WAITING — confirming both pumps are stopped before Lift")
            self.after_id = self.after(100, self._start_lift_after_wait)
            return
        self.pump_stop_deadline = None
        self.status_var.set("PUMPS STOPPED — pre-lift wait complete — lifting")
        XYPumpCycleWindow._start_lift_after_wait(self)

    def _cv_error(self, error):
        self._finish("ABORTED — CV failed: {} — use manual LIFT".format(error))

    def stop(self):
        if self.cv_runner.running:
            self.cv_runner.stop()
        XYPumpCycleWindow.stop(self)

    def emergency_stopped(self):
        if self.cv_runner.running:
            self.cv_runner.stop()
        XYPumpCycleWindow.emergency_stopped(self)

    def _release_cv_claim(self):
        if self.measurement_claimed and self.release_measurement is not None:
            self.release_measurement("AUTO_CV")
        self.measurement_claimed = False

    def _finish(self, message):
        self.pump_start_deadline = None
        self.pump_stop_deadline = None
        if hasattr(self, "cv_runner") and self.cv_runner.running:
            self.cv_runner.stop()
        XYPumpCycleWindow._finish(self, message)
        self._release_cv_claim()
        if hasattr(self, "cv_widgets"):
            for widget in self.cv_widgets:
                widget.configure(state="normal")
            self.cv_ie_range_entry.configure(state="readonly")
