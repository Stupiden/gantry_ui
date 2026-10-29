"""Manual, non-blocking CV window using the verified MG400 ToolkitPy setup."""

import csv
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from cv_plot_scale import autoscale_cv_axes
from gamry_ie_range import choices as ie_range_choices
from gamry_ie_range import display_value as ie_range_display
from gamry_ie_range import enum_value as ie_range_enum


def initialize_pstat_for_cv(pstat, tkp):
    """Mirror the verified initialization in MG400/pre_gamry_cv.py."""
    pstat.set_ach_select(tkp.ACHSELECT_GND)
    pstat.set_ie_stability(tkp.STABILITY_NORM)
    pstat.set_ca_speed(tkp.CASPEED_NORM)
    pstat.set_ground(tkp.FLOAT)
    pstat.set_ich_range(3.0)
    pstat.set_ich_range_mode(False)
    pstat.set_ich_offset_enable(False)
    pstat.set_ie_range_mode(True)
    pstat.set_vch_range(10.0)
    pstat.set_vch_range_mode(True)
    pstat.set_vch_offset_enable(False)
    pstat.set_vch_filter(3)
    pstat.set_ach_range(3.0)
    pstat.set_ie_range_lower_limit(0)
    pstat.set_pos_feed_enable(False)
    pstat.set_analog_out(0.0)
    pstat.set_voltage(0.0)
    pstat.set_pos_feed_resistance(0.0)


class ManualCVWindow(tk.Toplevel):
    POLL_MS = 50
    MEASUREMENT_NAME = "CV"

    def __init__(
        self, parent, pstat, tkp, position_provider,
        claim_measurement=None, release_measurement=None
    ):
        tk.Toplevel.__init__(self, parent)
        self.title("Manual Gamry CV")
        self.geometry("900x680")
        self.minsize(760, 560)
        self.pstat = pstat
        self.tkp = tkp
        self.position_provider = position_provider
        self.claim_measurement = claim_measurement
        self.release_measurement = release_measurement
        self.measurement_claimed = False
        self.curve = None
        self.signal = None
        self.running = False
        self.curve_started = False
        self.curve_started_at = 0.0
        self.precharge_deadline = 0.0
        self.last_point_count = 0
        self.position_snapshot = (0.0, 0.0, 0.0)
        self.active_params = {}
        self.pos_feed_resistance = 0.0

        self.initial_var = tk.DoubleVar(value=-0.5)
        self.apex1_var = tk.DoubleVar(value=-1.5)
        self.apex2_var = tk.DoubleVar(value=-0.5)
        self.final_var = tk.DoubleVar(value=-0.5)
        self.scan_rate_var = tk.DoubleVar(value=0.10)
        self.step_size_var = tk.DoubleVar(value=0.001)
        self.cycles_var = tk.IntVar(value=4)
        self.precharge_var = tk.DoubleVar(value=0.0)
        self.ru_var = tk.DoubleVar(value=0.0)
        self.ru_fraction_var = tk.DoubleVar(value=0.85)
        self.pos_feed_var = tk.BooleanVar(value=False)
        self.auto_ie_range_var = tk.BooleanVar(value=False)
        self.ie_range_choices = ie_range_choices(self.pstat)
        self.ie_range_var = tk.StringVar(
            value=ie_range_display(10, self.ie_range_choices)
        )
        self.csv_var = tk.StringVar(value=self._default_csv_path())
        self.status_var = tk.StringVar(value="READY")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close_window)

    def _default_csv_path(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(Path.cwd() / "CV_{}.csv".format(stamp))

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        controls = ttk.LabelFrame(self, text="CV parameters", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        parameter_specs = (
            ("Initial V", self.initial_var),
            ("Apex 1 V", self.apex1_var),
            ("Apex 2 V", self.apex2_var),
            ("Final V", self.final_var),
            ("Scan V/s", self.scan_rate_var),
            ("Step V", self.step_size_var),
            ("Cycles", self.cycles_var),
            ("Precharge s", self.precharge_var),
        )
        self.parameter_widgets = []
        for index, (label, variable) in enumerate(parameter_specs):
            column = (index % 4) * 2
            row = index // 4
            ttk.Label(controls, text=label).grid(
                row=row, column=column, padx=(0, 4), pady=3, sticky="e"
            )
            entry = ttk.Entry(controls, textvariable=variable, width=10)
            entry.grid(row=row, column=column + 1, padx=(0, 10), pady=3)
            self.parameter_widgets.append(entry)

        self.pos_feed_check = ttk.Checkbutton(
            controls,
            text="Enable positive feedback",
            variable=self.pos_feed_var,
        )
        self.pos_feed_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(controls, text="Ru Ω").grid(row=2, column=2, sticky="e")
        self.ru_entry = ttk.Entry(controls, textvariable=self.ru_var, width=10)
        self.ru_entry.grid(row=2, column=3, sticky="w", padx=(4, 10))
        ttk.Label(controls, text="Ru fraction").grid(row=2, column=4, sticky="e")
        self.ru_fraction_entry = ttk.Entry(
            controls, textvariable=self.ru_fraction_var, width=10
        )
        self.ru_fraction_entry.grid(row=2, column=5, sticky="w", padx=(4, 10))
        self.auto_ie_range_check = ttk.Checkbutton(
            controls, text="Auto IE range", variable=self.auto_ie_range_var
        )
        self.auto_ie_range_check.grid(row=2, column=6, sticky="w", padx=(4, 6))
        ttk.Label(controls, text="IE range").grid(row=2, column=7, sticky="e")
        self.ie_range_entry = ttk.Combobox(
            controls, textvariable=self.ie_range_var,
            values=self.ie_range_choices, state="readonly", width=14
        )
        self.ie_range_entry.grid(row=2, column=8, sticky="w", padx=(4, 0))

        ttk.Label(controls, text="CSV").grid(row=3, column=0, sticky="e", pady=3)
        self.csv_entry = ttk.Entry(controls, textvariable=self.csv_var)
        self.csv_entry.grid(row=3, column=1, columnspan=5, sticky="ew", padx=4)
        controls.columnconfigure(5, weight=1)
        self.browse_button = ttk.Button(controls, text="BROWSE", command=self.browse_csv)
        self.browse_button.grid(row=3, column=6, padx=4)
        self.start_button = ttk.Button(controls, text="START CV", command=self.start_cv)
        self.start_button.grid(row=3, column=7, padx=4)
        self.stop_button = ttk.Button(
            controls, text="STOP CV", command=self.stop_cv, state="disabled"
        )
        self.stop_button.grid(row=3, column=8, padx=4)

        figure = Figure(figsize=(7.5, 4.8), dpi=100)
        self.axes = figure.add_subplot(111)
        self.axes.set_xlabel("Potential / V")
        self.axes.set_ylabel("Current / A")
        self.axes.grid(True)
        self.line, = self.axes.plot([], [])
        self.canvas = FigureCanvasTkAgg(figure, master=self)
        self.canvas.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=8, pady=(0, 4)
        )
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, sticky="ew", padx=8, pady=(0, 6)
        )

    def browse_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save CV data",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=Path(self.csv_var.get()).name,
        )
        if path:
            self.csv_var.set(path)

    def _parameters(self):
        values = {
            "initial": float(self.initial_var.get()),
            "apex1": float(self.apex1_var.get()),
            "apex2": float(self.apex2_var.get()),
            "final": float(self.final_var.get()),
            "scan_rate": float(self.scan_rate_var.get()),
            "step_size": float(self.step_size_var.get()),
            "cycles": int(self.cycles_var.get()),
            "precharge": float(self.precharge_var.get()),
            "ru": float(self.ru_var.get()),
            "ru_fraction": float(self.ru_fraction_var.get()),
            "ie_range": ie_range_enum(self.ie_range_var.get()),
        }
        if values["scan_rate"] <= 0 or values["step_size"] <= 0:
            raise ValueError("Scan rate and step size must be greater than zero")
        if values["cycles"] < 1 or values["precharge"] < 0:
            raise ValueError("Cycles must be at least 1 and precharge cannot be negative")
        if self.pos_feed_var.get() and (
            values["ru"] <= 0 or not 0.0 < values["ru_fraction"] <= 1.0
        ):
            raise ValueError("Positive feedback requires Ru > 0 and fraction within 0–1")
        if not self.auto_ie_range_var.get() and values["ie_range"] <= 0:
            raise ValueError("Fixed IE range must be greater than zero")
        return values

    def start_cv(self):
        if self.running:
            return
        try:
            params = self._parameters()
            if self.claim_measurement is not None:
                if not self.claim_measurement(self.MEASUREMENT_NAME):
                    raise RuntimeError("Another Gamry measurement is already running")
                self.measurement_claimed = True
            if not self.tkp.pstat_is_valid(self.pstat):
                raise RuntimeError("PSTAT connection is not valid")
            csv_path = Path(self.csv_var.get()).expanduser()
            if not str(csv_path).strip():
                raise ValueError("Choose a CSV output path")
            csv_path.parent.mkdir(parents=True, exist_ok=True)

            self.pstat.set_ctrl_mode(self.tkp.PSTATMODE)
            initialize_pstat_for_cv(self.pstat, self.tkp)
            if self.auto_ie_range_var.get():
                self.pstat.set_ie_range_mode(True)
            else:
                self.pstat.set_ie_range_mode(False)
                self.pstat.set_ie_range(params["ie_range"])
            pos_feed_resistance = 0.0
            if self.pos_feed_var.get():
                pos_feed_resistance = params["ru"] * params["ru_fraction"]
            self.active_params = params
            self.pos_feed_resistance = pos_feed_resistance
            self.pstat.set_pos_feed_resistance(pos_feed_resistance)
            self.pstat.set_pos_feed_enable(bool(self.pos_feed_var.get()))

            sample_time = params["step_size"] / params["scan_rate"]
            self.signal = self._create_signal(params, sample_time)
            self._attach_signal(self.signal)

            # ToolkitPy signal wrappers must stay alive for the full run.  The
            # original MG400 blocking function naturally kept this local in
            # scope; this non-blocking UI stores it explicitly between polls.
            self.pstat.init_signal()
            self.curve = self.tkp.RcvCurve(self.pstat, 10000)
            self.position_snapshot = self.position_provider()
            self.last_point_count = 0
            self.curve_started = False
            self.running = True
            self.precharge_deadline = time.monotonic() + params["precharge"]
            self.pstat.set_cell(True)
            self._set_controls_running(True)
            self.line.set_data([], [])
            self.canvas.draw_idle()
            self.status_var.set("PRECHARGING — cell ON")
            self.after(self.POLL_MS, self._poll_cv)
        except Exception as error:
            self._safe_hardware_off()
            self._release_measurement()
            messagebox.showerror("{} start failed".format(self.MEASUREMENT_NAME), str(error), parent=self)
            self.status_var.set("START FAILED: {}".format(error))

    def _create_signal(self, params, sample_time):
        return self.pstat.signal_r_up_dn_new(
            [params["initial"], params["apex1"], params["apex2"], params["final"]],
            [params["scan_rate"], params["scan_rate"], params["scan_rate"]],
            [0.0, 0.0, 0.0], sample_time, params["cycles"], self.tkp.PSTATMODE,
        )

    def _attach_signal(self, signal):
        self.pstat.set_signal_r_up_dn(signal)

    def _poll_cv(self):
        if not self.running:
            return
        try:
            if not self.curve_started:
                remaining = self.precharge_deadline - time.monotonic()
                if remaining > 0:
                    self.status_var.set("PRECHARGING — {:.1f} s".format(remaining))
                    self.after(self.POLL_MS, self._poll_cv)
                    return
                self.curve.run(True)
                self.curve_started = True
                self.curve_started_at = time.monotonic()
                self.status_var.set("{} RUNNING".format(self.MEASUREMENT_NAME))

            data = self.curve.acq_data()
            if len(data) > self.last_point_count:
                new_data = data[self.last_point_count:]
                self._append_csv(new_data)
                self.last_point_count = len(data)
            self._update_plot(data)

            curve_running = self.curve.running()
            pstat_valid = self.tkp.pstat_is_valid(self.pstat)
            if curve_running and pstat_valid:
                self.after(self.POLL_MS, self._poll_cv)
            elif (
                pstat_valid
                and self.last_point_count == 0
                and time.monotonic() - self.curve_started_at < 2.0
            ):
                self.status_var.set("{} STARTING — waiting for first point".format(self.MEASUREMENT_NAME))
                self.after(self.POLL_MS, self._poll_cv)
            elif self.last_point_count == 0:
                raise RuntimeError(
                    "{} ended without data (PSTAT valid={}, curve running={})".format(
                        self.MEASUREMENT_NAME,
                        pstat_valid, curve_running
                    )
                )
            else:
                self._finish("{} COMPLETE — {} points".format(self.MEASUREMENT_NAME, self.last_point_count))
        except Exception as error:
            self._finish("{} ERROR: {}".format(self.MEASUREMENT_NAME, error))
            messagebox.showerror("{} error".format(self.MEASUREMENT_NAME), str(error), parent=self)

    def _append_csv(self, data):
        if data is None or len(data) == 0:
            return
        names = list(data.dtype.names or ())
        if not names:
            raise ValueError("ToolkitPy returned CV data without named fields")
        csv_path = Path(self.csv_var.get()).expanduser()
        need_header = not csv_path.exists() or csv_path.stat().st_size == 0
        x_mm, y_mm, z_mm = self.position_snapshot
        with open(str(csv_path), "a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if need_header:
                writer.writerow(
                    [
                        "timestamp", "x_mm", "y_mm", "z_mm",
                        "initial_v", "apex1_v", "apex2_v", "final_v",
                        "scan_rate_v_s", "step_v", "cycles", "precharge_s",
                        "ru_ohm", "ru_fraction", "pos_feed_resistance_ohm",
                        "pos_feed_enabled", "ie_range", "auto_ie_range",
                    ] + names
                )
            for row in data:
                writer.writerow(
                    [
                        datetime.now().isoformat(), x_mm, y_mm, z_mm,
                        self.active_params["initial"],
                        self.active_params["apex1"],
                        self.active_params["apex2"],
                        self.active_params["final"],
                        self.active_params["scan_rate"],
                        self.active_params["step_size"],
                        self.active_params["cycles"],
                        self.active_params["precharge"],
                        self.active_params["ru"],
                        self.active_params["ru_fraction"],
                        self.pos_feed_resistance,
                        int(bool(self.pos_feed_var.get())),
                        self.active_params["ie_range"],
                        int(bool(self.auto_ie_range_var.get())),
                    ]
                    + [row[name] for name in names]
                )

    def _update_plot(self, data):
        if data is None or len(data) == 0:
            return
        names = list(data.dtype.names or ())
        if "vf" not in names or "im" not in names:
            raise ValueError("CV data is missing vf/im fields: {}".format(names))
        voltage = np.asarray(data["vf"], dtype=float)
        current = np.asarray(data["im"], dtype=float)
        self.line.set_data(voltage, current)
        autoscale_cv_axes(self.axes, voltage, current)
        self.canvas.draw_idle()

    def stop_cv(self):
        if not self.running:
            return
        if self.curve_started and self.curve is not None:
            try:
                self.curve.stop()
            except Exception:
                pass
        self._finish("{} STOPPED BY USER".format(self.MEASUREMENT_NAME))

    def _safe_hardware_off(self):
        try:
            self.pstat.set_pos_feed_enable(False)
            self.pstat.set_pos_feed_resistance(0.0)
        except Exception:
            pass
        try:
            self.pstat.set_cell(False)
        except Exception:
            pass

    def _finish(self, status):
        self.running = False
        self._safe_hardware_off()
        self.curve = None
        self.signal = None
        self.curve_started = False
        self._release_measurement()
        self._set_controls_running(False)
        self.status_var.set(status)

    def _release_measurement(self):
        if self.measurement_claimed and self.release_measurement is not None:
            self.release_measurement(self.MEASUREMENT_NAME)
        self.measurement_claimed = False

    def _set_controls_running(self, running):
        state = "disabled" if running else "normal"
        for widget in self.parameter_widgets:
            widget.configure(state=state)
        self.pos_feed_check.configure(state=state)
        self.auto_ie_range_check.configure(state=state)
        self.ie_range_entry.configure(state=state)
        self.ru_entry.configure(state=state)
        self.ru_fraction_entry.configure(state=state)
        self.csv_entry.configure(state=state)
        self.browse_button.configure(state=state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def shutdown(self):
        if self.running:
            self.stop_cv()
        else:
            self._safe_hardware_off()

    def close_window(self):
        self.shutdown()
        self.destroy()
