"""Automated per-point GetRu followed by positive-feedback compensated CV."""

from datetime import datetime
import math
import tkinter as tk
from tkinter import messagebox, ttk

from auto_cv_measurement import AutoCVMeasurement
from automatic_live_plot import AutomaticLivePlot
from xy_full_eis_cycle import XYFullEISCycleWindow


class XYEISCompCVCycleWindow(XYFullEISCycleWindow):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        machine_position_provider, start_guard, lock_controls,
        pstat, tkp, claim_measurement, release_measurement
    ):
        XYFullEISCycleWindow.__init__(
            self, parent, points, path_name, send_command, position_provider,
            machine_position_provider, start_guard, lock_controls,
            pstat, claim_measurement, release_measurement,
        )
        self.title("Full measurement cycle — EIS/GetRu + compensated CV")
        self.geometry("980x860")
        self.tkp = tkp
        self.cv_runner = AutoCVMeasurement(self, pstat, tkp)
        self.live_plot.destroy()
        self.live_plot = AutomaticLivePlot(self, show_cv=True, show_eis=True)
        self.cv_initial_var = tk.DoubleVar(value=0.0)
        self.cv_apex1_var = tk.DoubleVar(value=0.0)
        self.cv_apex2_var = tk.DoubleVar(value=0.0)
        self.cv_final_var = tk.DoubleVar(value=0.0)
        self.cv_scan_rate_var = tk.DoubleVar(value=0.10)
        self.cv_step_var = tk.DoubleVar(value=0.001)
        self.cv_cycles_var = tk.IntVar(value=4)
        self.cv_precharge_var = tk.DoubleVar(value=10.0)
        self.cv_ie_range_var = tk.DoubleVar(value=10.0)
        self.comp_fraction_var = tk.DoubleVar(value=0.85)
        self.min_ru_var = tk.DoubleVar(value=0.01)
        self.max_ru_var = tk.DoubleVar(value=10000.0)
        self.comp_widgets = []
        self._build_comp_cv_controls()

    def _build_comp_cv_controls(self):
        frame = self.start_button.master
        specs = (
            ("CV Initial V", self.cv_initial_var), ("Apex 1 V", self.cv_apex1_var),
            ("Apex 2 V", self.cv_apex2_var), ("Final V", self.cv_final_var),
            ("Scan V/s", self.cv_scan_rate_var), ("Step V", self.cv_step_var),
            ("Cycles", self.cv_cycles_var), ("Precharge s", self.cv_precharge_var),
            ("CV IE range", self.cv_ie_range_var), ("Ru fraction", self.comp_fraction_var),
            ("Min Ru Ω", self.min_ru_var), ("Max Ru Ω", self.max_ru_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = 24 + (index // 6) * 2
            column = index % 6
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5)
            entry = ttk.Entry(frame, textvariable=variable, width=10)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5)
            self.comp_widgets.append(entry)
        self.start_button.grid_configure(row=28)
        self.stop_button.grid_configure(row=28)
        self.start_button.configure(text="START EIS + COMPENSATED CV")

    def _comp_cv_parameters(self):
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
                "ie_range": float(self.cv_ie_range_var.get()),
            }
            fraction = float(self.comp_fraction_var.get())
            min_ru = float(self.min_ru_var.get())
            max_ru = float(self.max_ru_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("CV compensation parameters must be numbers")
        if params["scan_rate"] <= 0 or params["step_size"] <= 0 or params["cycles"] < 1:
            raise ValueError("CV scan rate/step/cycles are invalid")
        if params["precharge"] < 0 or params["ie_range"] <= 0:
            raise ValueError("CV precharge/IE range are invalid")
        if not 0.0 < fraction <= 0.95:
            raise ValueError("Ru compensation fraction must be > 0 and <= 0.95")
        if min_ru <= 0 or max_ru <= min_ru:
            raise ValueError("Ru limits are invalid")
        return params, fraction, min_ru, max_ru

    def start(self):
        try:
            self.cv_params, self.comp_fraction, self.min_ru, self.max_ru = self._comp_cv_parameters()
        except Exception as error:
            messagebox.showerror("EIS + compensated CV", str(error), parent=self)
            return
        XYFullEISCycleWindow.start(self)
        if self.running:
            for widget in self.comp_widgets:
                widget.configure(state="disabled")

    def _eis_complete(self, result):
        if not self.running:
            return
        ru = float(result["avg_ru"])
        if not math.isfinite(ru) or not self.min_ru <= ru <= self.max_ru:
            self._finish("ABORTED — GetRu value {:.6g} Ω is outside limits; use manual LIFT".format(ru))
            return
        resistance = ru * self.comp_fraction
        params = dict(self.cv_params)
        params.update({
            "ru_ohm": ru,
            "compensation_fraction": self.comp_fraction,
            "pos_feed_resistance": resistance,
        })
        point = self._current_target()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "CV_point_{:04d}_Ru{:.6g}_{}.csv".format(
            int(point["index"]), ru, stamp
        )
        self.press_state = "measuring_comp_cv"
        self.status_var.set(
            "Ru {:.6g} Ω — CV with {:.1f}% compensation ({:.6g} Ω) — pumps ON".format(
                ru, self.comp_fraction * 100.0, resistance
            )
        )
        self.live_plot.clear_cv()
        try:
            self.cv_runner.start(
                params, self.active_attempt_dir / filename, point,
                self.machine_position_provider(), self._cv_complete, self._cv_error,
                self.live_plot.update_cv,
            )
            self._restore_measurement_pumps()
        except Exception as error:
            self._cv_error(error)

    def _experiment_method(self):
        return "EIS_GetRu_compensated_CV"

    def _experiment_settings(self):
        return {
            "eis": self.eis_params,
            "cv": self.cv_params,
            "compensation_fraction": self.comp_fraction,
            "min_ru_ohm": self.min_ru,
            "max_ru_ohm": self.max_ru,
        }

    def _cv_complete(self, point_count, csv_path):
        if not self.running:
            return
        self.press_state = "post_cv_pumping"
        self.status_var.set("COMPENSATED CV SAVED — {} points — pumps remain ON".format(point_count))
        for pump_id, unused_rpm, duration in self.pump_configs:
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _cv_error(self, error):
        self._finish("ABORTED — compensated CV failed: {} — use manual LIFT".format(error))

    def stop(self):
        if self.cv_runner.running:
            self.cv_runner.stop()
        XYFullEISCycleWindow.stop(self)

    def emergency_stopped(self):
        if self.cv_runner.running:
            self.cv_runner.stop()
        XYFullEISCycleWindow.emergency_stopped(self)

    def _finish(self, message):
        if hasattr(self, "cv_runner") and self.cv_runner.running:
            self.cv_runner.stop()
        XYFullEISCycleWindow._finish(self, message)
        if hasattr(self, "comp_widgets"):
            for widget in self.comp_widgets:
                widget.configure(state="normal")
