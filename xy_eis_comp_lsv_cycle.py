"""Automated per-point GetRu followed by positive-feedback compensated LSV."""

from datetime import datetime
import math
import tkinter as tk
from tkinter import messagebox, ttk

from auto_lsv_measurement import AutoLSVMeasurement
from automatic_live_plot import SeparateEISVoltammetryPlots
from gamry_ie_range import enum_value as ie_range_enum
from xy_eis_comp_cv_cycle import XYEISCompCVCycleWindow


class XYEISCompLSVCycleWindow(XYEISCompCVCycleWindow):
    def __init__(self, *args, **kwargs):
        XYEISCompCVCycleWindow.__init__(self, *args, **kwargs)
        self.title("Full measurement cycle — EIS/GetRu + compensated LSV")
        self.cv_runner = AutoLSVMeasurement(self, self.pstat, self.tkp)
        self.live_plot.destroy()
        self.live_plot = SeparateEISVoltammetryPlots(
            self, voltammetry_label="LSV"
        )
        self.cv_scan_rate_var.set(0.01)

    def _build_comp_cv_controls(self):
        frame = self.start_button.master
        specs = (
            ("LSV Initial V", self.cv_initial_var),
            ("LSV Final V", self.cv_final_var),
            ("Scan V/s", self.cv_scan_rate_var),
            ("Step V", self.cv_step_var),
            ("Precharge s", self.cv_precharge_var),
            ("LSV IE range", self.cv_ie_range_var),
            ("Ru fraction", self.comp_fraction_var),
            ("Min Ru Ω", self.min_ru_var),
            ("Max Ru Ω", self.max_ru_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = 24 + (index // 6) * 2
            column = index % 6
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5)
            if label == "LSV IE range":
                entry = ttk.Combobox(
                    frame, textvariable=variable, values=self.cv_ie_range_choices,
                    state="readonly", width=14,
                )
                self.cv_ie_range_entry = entry
            else:
                entry = ttk.Entry(frame, textvariable=variable, width=10)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5)
            self.comp_widgets.append(entry)
        self._build_ie_mode_controls(frame)
        self.start_button.grid_configure(row=28, columnspan=1); self.restart_button.grid_configure(row=28)
        self.stop_button.grid_configure(row=28)
        self.start_button.configure(text="START EIS + COMPENSATED LSV")

    def _comp_cv_parameters(self):
        try:
            params = {
                "initial": float(self.cv_initial_var.get()),
                "final": float(self.cv_final_var.get()),
                "scan_rate": float(self.cv_scan_rate_var.get()),
                "step_size": float(self.cv_step_var.get()),
                "precharge": float(self.cv_precharge_var.get()),
                "ie_range": ie_range_enum(self.cv_ie_range_var.get()),
                "auto_ie_range": bool(self.auto_ie_range_var.get()),
            }
            positive_feedback = bool(self.pos_feed_var.get())
            fraction = float(self.comp_fraction_var.get()) if positive_feedback else 0.0
            min_ru = float(self.min_ru_var.get())
            max_ru = float(self.max_ru_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("LSV compensation parameters must be numbers")
        if params["initial"] == params["final"]:
            raise ValueError("LSV initial and final voltage must be different")
        if params["scan_rate"] <= 0 or params["step_size"] <= 0:
            raise ValueError("LSV scan rate and step must be positive")
        sample_period = params["step_size"] / params["scan_rate"]
        if not 0.1 <= sample_period <= 600.0:
            raise ValueError("Step V / Scan V/s must give a 0.1–600 s sample period")
        if params["precharge"] < 0 or (not params["auto_ie_range"] and params["ie_range"] <= 0):
            raise ValueError("LSV precharge/IE range are invalid")
        if positive_feedback and params["auto_ie_range"]:
            raise ValueError("Auto IE range requires positive feedback to be OFF")
        if positive_feedback and not 0.0 < fraction <= 0.95:
            raise ValueError("Ru compensation fraction must be > 0 and <= 0.95")
        if min_ru <= 0 or max_ru <= min_ru:
            raise ValueError("Ru limits are invalid")
        return params, fraction, min_ru, max_ru

    def start(self):
        if self.running:
            return
        try:
            self.cv_params, self.comp_fraction, self.min_ru, self.max_ru = self._comp_cv_parameters()
        except Exception as error:
            messagebox.showerror("EIS + compensated LSV", str(error), parent=self)
            return
        self._start_standard_measurement()

    def _eis_complete(self, result):
        if not self.running:
            return
        ru = float(result["avg_ru"])
        if not math.isfinite(ru) or not self.min_ru <= ru <= self.max_ru:
            self._eis_error("GetRu value {:.6g} Ω is outside limits".format(ru))
            return
        resistance = ru * self.comp_fraction
        params = dict(self.cv_params)
        params.update({
            "ru_ohm": ru,
            "compensation_fraction": self.comp_fraction,
            "pos_feed_resistance": resistance,
        })
        point = self._current_target()
        filename = "LSV_point_{:04d}_Ru{:.6g}_{}.csv".format(
            int(point["index"]), ru, datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        self.press_state = "measuring_comp_lsv"
        if self.pos_feed_var.get():
            self.status_var.set(
                "Ru {:.6g} Ω — LSV with {:.1f}% compensation ({:.6g} Ω) — pumps ON".format(
                    ru, self.comp_fraction * 100.0, resistance
                )
            )
        else:
            self.status_var.set("Ru {:.6g} Ω — LSV without PF — pumps ON".format(ru))
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
        return "EIS_GetRu_compensated_LSV"

    def _experiment_settings(self):
        return {
            "eis": self.eis_params,
            "lsv": self.cv_params,
            "compensation_fraction": self.comp_fraction,
            "positive_feedback_enabled": bool(self.pos_feed_var.get()),
            "min_ru_ohm": self.min_ru,
            "max_ru_ohm": self.max_ru,
        }

    def _cv_complete(self, point_count, csv_path):
        if not self.running:
            return
        self.press_state = "post_lsv_pumping"
        self.status_var.set("COMPENSATED LSV SAVED — {} points — pumps remain ON".format(point_count))
        for pump_id, unused_rpm, duration in self.pump_configs:
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _cv_error(self, error):
        self._finish("ABORTED — compensated LSV failed: {} — use manual LIFT".format(error))
