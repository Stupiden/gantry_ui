"""Automated scan cycle with CA and a live time-current plot."""

from datetime import datetime
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from auto_ca_measurement import AutoCAMeasurement
from automatic_live_plot import AutomaticLivePlot
from experiment_data import ExperimentDataManager
from gamry_ie_range import choices as ie_range_choices
from gamry_ie_range import display_value as ie_range_display
from gamry_ie_range import enum_value as ie_range_enum
from xy_pump_cycle import XYPumpCycleWindow


class XYFullCACycleWindow(XYPumpCycleWindow):
    def __init__(self, parent, points, path_name, send_command, position_provider,
                 machine_position_provider, start_guard, lock_controls, pstat, tkp,
                 claim_measurement, release_measurement):
        XYPumpCycleWindow.__init__(self, parent, points, path_name, send_command,
            position_provider, start_guard, lock_controls)
        self.title("Full measurement cycle — CA only")
        self.geometry("900x770")
        self.machine_position_provider, self.pstat, self.tkp = machine_position_provider, pstat, tkp
        self.claim_measurement, self.release_measurement = claim_measurement, release_measurement
        self.measurement_claimed = False
        self.reported_pump_states = None
        self.pump_start_deadline = self.pump_stop_deadline = None
        self.ca_runner = AutoCAMeasurement(self, pstat, tkp)
        self.live_plot = AutomaticLivePlot(self, show_ca=True)
        self.vinit_var, self.tinit_var = tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.5)
        self.vstep1_var, self.tstep1_var = tk.DoubleVar(value=0.0), tk.DoubleVar(value=3.0)
        self.vstep2_var, self.tstep2_var = tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.150)
        self.sample_period_var = tk.DoubleVar(value=0.005)
        self.auto_ie_range_var = tk.BooleanVar(value=True)
        self.ie_range_choices = ie_range_choices(self.pstat)
        self.ie_range_var = tk.StringVar(
            value=ie_range_display(8, self.ie_range_choices)
        )
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "auto_ca_data"))
        self.ca_widgets = []
        self._build_ca_controls()

    def _build_ca_controls(self):
        frame = self.start_button.master
        for child in frame.winfo_children():
            try:
                if child.cget("text") == "Duration (s)": child.configure(text="Post-CA run (s)")
            except tk.TclError: pass
        specs = (("V initial", self.vinit_var), ("Initial time s", self.tinit_var),
            ("V step 1", self.vstep1_var), ("Step 1 time s", self.tstep1_var),
            ("V step 2", self.vstep2_var), ("Step 2 time s", self.tstep2_var),
            ("Sample period s", self.sample_period_var), ("IE range", self.ie_range_var))
        for index, (label, variable) in enumerate(specs):
            row, column = 16 + (index // 5) * 2, index % 5
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5)
            if label == "IE range":
                entry = ttk.Combobox(
                    frame, textvariable=variable, values=self.ie_range_choices,
                    state="readonly", width=14
                )
            else:
                entry = ttk.Entry(frame, textvariable=variable, width=10)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5); self.ca_widgets.append(entry)
        auto = ttk.Checkbutton(frame, text="Auto IE range", variable=self.auto_ie_range_var)
        auto.grid(row=20, column=0, sticky="w", padx=5); self.ca_widgets.append(auto)
        ttk.Label(frame, text="CA output folder").grid(row=20, column=1, sticky="w", padx=5)
        output = ttk.Entry(frame, textvariable=self.output_dir_var)
        output.grid(row=21, column=0, columnspan=4, sticky="ew", padx=5)
        browse = ttk.Button(frame, text="BROWSE", command=self._browse)
        browse.grid(row=21, column=4, padx=5); self.ca_widgets.extend((output, browse))
        self.start_button.grid_configure(row=23, columnspan=1); self.restart_button.grid_configure(row=23); self.stop_button.grid_configure(row=23)
        self.start_button.configure(text="START FULL CA CYCLE")

    def _browse(self):
        path = filedialog.askdirectory(parent=self, title="Choose automatic CA output folder")
        if path: self.output_dir_var.set(path)

    def _parameters(self):
        try:
            params = {"vinit": float(self.vinit_var.get()), "tinit": float(self.tinit_var.get()),
                "vstep1": float(self.vstep1_var.get()), "tstep1": float(self.tstep1_var.get()),
                "vstep2": float(self.vstep2_var.get()), "tstep2": float(self.tstep2_var.get()),
                "sample_period": float(self.sample_period_var.get()),
                "auto_ie_range": bool(self.auto_ie_range_var.get()),
                "ie_range": ie_range_enum(self.ie_range_var.get())}
        except (tk.TclError, ValueError): raise ValueError("CA parameters must be numbers")
        if params["tinit"] < 0 or params["tstep1"] <= 0 or params["tstep2"] <= 0:
            raise ValueError("CA times are invalid")
        if params["sample_period"] <= 0 or (not params["auto_ie_range"] and params["ie_range"] <= 0):
            raise ValueError("CA sample period/IE range is invalid")
        folder = Path(self.output_dir_var.get()).expanduser(); folder.mkdir(parents=True, exist_ok=True)
        return params, folder

    def start(self):
        if self.running:
            return
        try: self.ca_params, self.output_dir = self._parameters()
        except Exception as error:
            messagebox.showerror("Full CA cycle", str(error), parent=self); return
        XYPumpCycleWindow.start(self)
        if not self.running: return
        if self.data_manager is None:
            self.data_manager = ExperimentDataManager(
                self.output_dir, "CA", self.path_name, self.points,
                self._manifest_settings(self.ca_params)
            )
        if self.claim_measurement is not None and not self.claim_measurement("AUTO_CA"):
            XYPumpCycleWindow.stop(self); return
        self.measurement_claimed = True
        for widget in self.ca_widgets: widget.configure(state="disabled")

    def _start_pumps(self):
        self.after_id = None
        if not self.running: return
        self.press_state = "starting_pumps"
        self._start_selected_pumps(self._all_pumps_started_before_ca)

    def _all_pumps_started_before_ca(self):
        if not self.running: return
        self.pump_start_deadline = time.monotonic() + 5.0
        self.after_id = self.after(100, self._wait_pumps)

    def _wait_pumps(self):
        self.after_id = None
        required = {item[0] for item in self.pump_configs}
        if self.reported_pump_states is None or not all(self.reported_pump_states[item] for item in required):
            if time.monotonic() >= self.pump_start_deadline:
                self._finish("ABORTED — pumps not confirmed running; use manual LIFT"); return
            self.after_id = self.after(100, self._wait_pumps); return
        self.pump_start_deadline = None
        self._hold_with_pumps_before(self._start_ca, "CA")

    def _start_ca(self):
        point, stamp = self._current_target(), datetime.now().strftime("%Y%m%d_%H%M%S")
        attempt_dir = self.data_manager.active_folder
        path = attempt_dir / "CA_point_{:04d}_{}.csv".format(int(point["index"]), stamp)
        self.press_state = "measuring_ca"; self.live_plot.deiconify(); self.live_plot.clear_ca()
        try:
            self.ca_runner.start(self.ca_params, path, point, self.machine_position_provider(),
                self._ca_complete, self._ca_error, self.live_plot.update_ca)
            self._restore_measurement_pumps()
        except Exception as error: self._ca_error(error)

    def handle_status(self, values):
        try: self.reported_pump_states = (values["PUMP0RUNNING"] == "1", values["PUMP1RUNNING"] == "1")
        except KeyError: pass
        XYPumpCycleWindow.handle_status(self, values)

    def _ca_complete(self, count, unused_path):
        self.press_state = "post_ca_pumping"; self.status_var.set("CA SAVED — {} points — pumps ON".format(count))
        for pump_id, unused_rpm, duration in self.pump_configs:
            self.pump_after_ids[pump_id] = self.after(int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id))

    def _start_lift_after_wait(self):
        self.after_id = None
        if self.pump_stop_deadline is None: self.pump_stop_deadline = time.monotonic() + 5.0
        if self.reported_pump_states != (False, False):
            if time.monotonic() >= self.pump_stop_deadline:
                self._finish("ABORTED — pumps not confirmed stopped; use manual LIFT"); return
            self.after_id = self.after(100, self._start_lift_after_wait); return
        XYPumpCycleWindow._start_lift_after_wait(self)

    def _ca_error(self, error): self._finish("ABORTED — CA failed: {} — use manual LIFT".format(error))
    def stop(self):
        if self.ca_runner.running: self.ca_runner.stop()
        XYPumpCycleWindow.stop(self)
    def emergency_stopped(self):
        if self.ca_runner.running: self.ca_runner.stop()
        XYPumpCycleWindow.emergency_stopped(self)
    def _finish(self, message):
        if hasattr(self, "ca_runner") and self.ca_runner.running: self.ca_runner.stop()
        XYPumpCycleWindow._finish(self, message)
        if self.measurement_claimed and self.release_measurement is not None: self.release_measurement("AUTO_CA")
        self.measurement_claimed = False
        if hasattr(self, "ca_widgets"):
            for widget in self.ca_widgets: widget.configure(state="normal")
