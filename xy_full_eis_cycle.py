"""Milestone 15B: automated scan cycle with EIS/GetRu."""

from datetime import datetime
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from auto_eis_measurement import AutoEISMeasurement
from automatic_live_plot import AutomaticLivePlot
from gamry_eis_manual import DEFAULT_FREQUENCIES
from xy_pump_cycle import XYPumpCycleWindow


class XYFullEISCycleWindow(XYPumpCycleWindow):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        machine_position_provider, start_guard, lock_controls,
        pstat, claim_measurement, release_measurement
    ):
        XYPumpCycleWindow.__init__(
            self, parent, points, path_name, send_command, position_provider,
            start_guard, lock_controls
        )
        self.title("Full measurement cycle — EIS / GetRu only")
        self.geometry("940x790")
        self.machine_position_provider = machine_position_provider
        self.pstat = pstat
        self.claim_measurement = claim_measurement
        self.release_measurement = release_measurement
        self.measurement_claimed = False
        self.reported_pump_states = None
        self.pump_start_deadline = None
        self.pump_stop_deadline = None
        self.eis_runner = AutoEISMeasurement(self, pstat)
        self.live_plot = AutomaticLivePlot(self, show_eis=True)

        self.frequencies_var = tk.StringVar(value=DEFAULT_FREQUENCIES)
        self.repeats_var = tk.IntVar(value=3)
        self.ac_amplitude_var = tk.DoubleVar(value=0.007)
        self.dc_bias_var = tk.DoubleVar(value=0.0)
        self.gain_var = tk.DoubleVar(value=1.0)
        self.zmod_var = tk.DoubleVar(value=40.0)
        self.passes_var = tk.IntVar(value=10)
        self.cycles_delay_var = tk.IntVar(value=1)
        self.settle_var = tk.DoubleVar(value=0.5)
        self.max_phase_var = tk.DoubleVar(value=7.5)
        self.z_bad_var = tk.DoubleVar(value=1000.0)
        self.min_bad_var = tk.IntVar(value=2)
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "auto_eis_data"))
        self.eis_widgets = []
        self._build_eis_controls()

    def _build_eis_controls(self):
        frame = self.start_button.master
        for child in frame.winfo_children():
            try:
                if child.cget("text") == "Duration (s)":
                    child.configure(text="Post-EIS run (s)")
            except tk.TclError:
                pass
        ttk.Label(frame, text="Frequencies Hz").grid(
            row=16, column=0, sticky="w", padx=5, pady=(7, 2)
        )
        frequency_entry = ttk.Entry(frame, textvariable=self.frequencies_var)
        frequency_entry.grid(row=17, column=0, columnspan=5, sticky="ew", padx=5)
        self.eis_widgets.append(frequency_entry)
        specs = (
            ("Repeats", self.repeats_var), ("AC amplitude V", self.ac_amplitude_var),
            ("DC bias V", self.dc_bias_var), ("Gain", self.gain_var),
            ("Zmod Ω", self.zmod_var), ("Passes", self.passes_var),
            ("Cycles delay", self.cycles_delay_var), ("Settle s", self.settle_var),
            ("Max phase °", self.max_phase_var), ("Bad Z Ω", self.z_bad_var),
            ("Min bad points", self.min_bad_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = 18 + (index // 6) * 2
            column = index % 6
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5)
            entry = ttk.Entry(frame, textvariable=variable, width=10)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5)
            self.eis_widgets.append(entry)
        ttk.Label(frame, text="EIS output folder").grid(
            row=22, column=0, sticky="w", padx=5, pady=(7, 2)
        )
        output_entry = ttk.Entry(frame, textvariable=self.output_dir_var)
        output_entry.grid(row=23, column=0, columnspan=5, sticky="ew", padx=5)
        browse = ttk.Button(frame, text="BROWSE", command=self._browse_output)
        browse.grid(row=23, column=5, padx=5)
        self.eis_widgets.extend((output_entry, browse))
        self.start_button.grid_configure(row=24)
        self.stop_button.grid_configure(row=24)
        self.start_button.configure(text="START FULL EIS CYCLE")

    def _browse_output(self):
        path = filedialog.askdirectory(parent=self, title="Choose automatic EIS output folder")
        if path:
            self.output_dir_var.set(path)

    def _eis_parameters(self):
        try:
            frequencies = [
                float(item) for item in re.split(r"[,;\s]+", self.frequencies_var.get()) if item
            ]
            params = {
                "frequencies": frequencies,
                "n_times": int(self.repeats_var.get()),
                "ac_amplitude": float(self.ac_amplitude_var.get()),
                "dc_bias": float(self.dc_bias_var.get()),
                "gain": float(self.gain_var.get()),
                "zmod": float(self.zmod_var.get()),
                "passes": int(self.passes_var.get()),
                "cycles_delay": int(self.cycles_delay_var.get()),
                "dc_settle_s": float(self.settle_var.get()),
                "max_phase_deg_for_ru": float(self.max_phase_var.get()),
                "z_bad_threshold_ohm": float(self.z_bad_var.get()),
                "min_bad_points": int(self.min_bad_var.get()),
            }
        except (tk.TclError, ValueError):
            raise ValueError("EIS parameters must be numbers")
        if not frequencies or any(value <= 0 for value in frequencies):
            raise ValueError("Frequencies must contain positive numbers")
        if params["n_times"] < 1 or params["passes"] < 1:
            raise ValueError("Repeats and passes must be at least 1")
        if params["ac_amplitude"] <= 0 or params["zmod"] <= 0:
            raise ValueError("AC amplitude and Zmod must be greater than zero")
        if params["dc_settle_s"] < 0 or params["min_bad_points"] < 1:
            raise ValueError("Settle time/min bad points are invalid")
        output_dir = Path(self.output_dir_var.get()).expanduser()
        if not str(output_dir).strip():
            raise ValueError("Choose an EIS output folder")
        output_dir.mkdir(parents=True, exist_ok=True)
        return params, output_dir

    def start(self):
        try:
            self.eis_params, self.output_dir = self._eis_parameters()
        except Exception as error:
            messagebox.showerror("Full EIS cycle", str(error), parent=self)
            return
        XYPumpCycleWindow.start(self)
        if not self.running:
            return
        if self.claim_measurement is not None:
            if not self.claim_measurement("AUTO_EIS"):
                XYPumpCycleWindow.stop(self)
                messagebox.showerror(
                    "Full EIS cycle", "Another Gamry measurement is already running", parent=self
                )
                return
            self.measurement_claimed = True
        for widget in self.eis_widgets:
            widget.configure(state="disabled")

    def _start_pumps(self):
        self.after_id = None
        if not self.running:
            return
        self.press_state = "starting_pumps"
        for pump_id, rpm, unused_duration in self.pump_configs:
            if not self.send_command("PUMP SPEED {} {:.2f}".format(pump_id, rpm)):
                self._finish("FAILED — pump speed command was not sent")
                return
            if not self.send_command("PUMP START {}".format(pump_id)):
                self._finish("FAILED — pump start command was not sent")
                return
            self.running_pumps.add(pump_id)
        self.pump_start_deadline = time.monotonic() + 5.0
        self.after_id = self.after(100, self._wait_for_pumps_before_eis)

    def _wait_for_pumps_before_eis(self):
        self.after_id = None
        if not self.running:
            return
        required = {pump_id for pump_id, unused_rpm, unused_duration in self.pump_configs}
        reported = self.reported_pump_states
        if reported is None or not all(reported[pump_id] for pump_id in required):
            if time.monotonic() >= self.pump_start_deadline:
                self._finish("ABORTED — selected pumps were not confirmed running; use manual LIFT")
                return
            self.status_var.set("WAITING — confirming pumps are running before EIS")
            self.after_id = self.after(100, self._wait_for_pumps_before_eis)
            return
        self.pump_start_deadline = None
        self._start_eis()

    def _start_eis(self):
        point = self._current_target()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "EIS_point_{:04d}_x{:+.3f}_y{:+.3f}_{}.csv".format(
            int(point["index"]), float(point["x"]), float(point["y"]), stamp
        )
        self.press_state = "measuring_eis"
        self.live_plot.deiconify()
        self.live_plot.clear_eis()
        self.status_var.set("EIS RUNNING AT POINT {} — pumps ON".format(point["index"]))
        try:
            self.eis_runner.start(
                self.eis_params, self.output_dir / filename, point,
                self.machine_position_provider(), self._eis_complete, self._eis_error,
                self.live_plot.update_eis_csv,
            )
        except Exception as error:
            self._eis_error(error)

    def handle_status(self, values):
        try:
            self.reported_pump_states = (
                values["PUMP0RUNNING"] == "1", values["PUMP1RUNNING"] == "1"
            )
        except KeyError:
            pass
        XYPumpCycleWindow.handle_status(self, values)

    def _eis_complete(self, result):
        if not self.running:
            return
        self.press_state = "post_eis_pumping"
        self.status_var.set(
            "EIS SAVED — Ru {:.6g} Ω — pumps remain ON".format(float(result["avg_ru"]))
        )
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
                self._finish("ABORTED — pumps did not confirm stopped; use manual LIFT")
                return
            self.status_var.set("WAITING — confirming both pumps stopped before Lift")
            self.after_id = self.after(100, self._start_lift_after_wait)
            return
        self.pump_stop_deadline = None
        self.press_state = "lifting"
        if not self.send_command("LIFT {:.3f} {:.3f}".format(self.lift_distance, self.lift_speed)):
            self._finish("FAILED — lift command was not sent after EIS")

    def _eis_error(self, error):
        self._finish("ABORTED — EIS/GetRu failed: {} — use manual LIFT".format(error))

    def stop(self):
        if self.eis_runner.running:
            self.eis_runner.stop()
        XYPumpCycleWindow.stop(self)

    def emergency_stopped(self):
        if self.eis_runner.running:
            self.eis_runner.stop()
        XYPumpCycleWindow.emergency_stopped(self)

    def _release_eis_claim(self):
        if self.eis_runner.running:
            self.after(200, self._release_eis_claim)
            return
        if self.measurement_claimed and self.release_measurement is not None:
            self.release_measurement("AUTO_EIS")
        self.measurement_claimed = False

    def _finish(self, message):
        self.pump_start_deadline = None
        self.pump_stop_deadline = None
        if hasattr(self, "eis_runner") and self.eis_runner.running:
            self.eis_runner.stop()
        XYPumpCycleWindow._finish(self, message)
        self._release_eis_claim()
        if hasattr(self, "eis_widgets"):
            for widget in self.eis_widgets:
                widget.configure(state="normal")
