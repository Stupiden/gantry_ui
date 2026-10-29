"""Manual, non-blocking CA window based on the verified MG400 CA routine."""

import csv
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from gamry_cv_manual import initialize_pstat_for_cv
from gamry_ie_range import choices as ie_range_choices
from gamry_ie_range import display_value as ie_range_display
from gamry_ie_range import enum_value as ie_range_enum


class ManualCAWindow(tk.Toplevel):
    POLL_MS = 20

    def __init__(
        self, parent, pstat, tkp, position_provider,
        claim_measurement=None, release_measurement=None
    ):
        tk.Toplevel.__init__(self, parent)
        self.title("Manual Gamry CA")
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
        self.curve_started_at = 0.0
        self.last_point_count = 0
        self.position_snapshot = (0.0, 0.0, 0.0)
        self.active_params = {}

        # Defaults from MG400/main.py manual CA call.
        self.vinit_var = tk.DoubleVar(value=0.0)
        self.tinit_var = tk.DoubleVar(value=0.5)
        self.vstep1_var = tk.DoubleVar(value=0.0)
        self.tstep1_var = tk.DoubleVar(value=3.0)
        self.vstep2_var = tk.DoubleVar(value=0.0)
        self.tstep2_var = tk.DoubleVar(value=0.150)
        self.sample_period_var = tk.DoubleVar(value=0.005)
        self.auto_ie_range_var = tk.BooleanVar(value=True)
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
        return str(Path.cwd() / "CA_{}.csv".format(stamp))

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.LabelFrame(self, text="CA parameters", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=6)

        specs = (
            ("V initial", self.vinit_var),
            ("Initial time s", self.tinit_var),
            ("V step 1", self.vstep1_var),
            ("Step 1 time s", self.tstep1_var),
            ("V step 2", self.vstep2_var),
            ("Step 2 time s", self.tstep2_var),
            ("Sample period s", self.sample_period_var),
        )
        self.parameter_widgets = []
        for index, (label, variable) in enumerate(specs):
            row = index // 4
            column = (index % 4) * 2
            ttk.Label(controls, text=label).grid(
                row=row, column=column, padx=(0, 4), pady=3, sticky="e"
            )
            entry = ttk.Entry(controls, textvariable=variable, width=10)
            entry.grid(row=row, column=column + 1, padx=(0, 10), pady=3)
            self.parameter_widgets.append(entry)

        self.auto_ie_range_check = ttk.Checkbutton(
            controls, text="Auto IE range", variable=self.auto_ie_range_var
        )
        self.auto_ie_range_check.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(controls, text="Fixed IE range").grid(row=2, column=2, sticky="e")
        self.ie_range_entry = ttk.Combobox(
            controls, textvariable=self.ie_range_var,
            values=self.ie_range_choices, state="readonly", width=14
        )
        self.ie_range_entry.grid(row=2, column=3, sticky="w", padx=(4, 10))

        ttk.Label(controls, text="CSV").grid(row=3, column=0, sticky="e", pady=3)
        self.csv_entry = ttk.Entry(controls, textvariable=self.csv_var)
        self.csv_entry.grid(row=3, column=1, columnspan=5, sticky="ew", padx=4)
        controls.columnconfigure(5, weight=1)
        self.browse_button = ttk.Button(controls, text="BROWSE", command=self.browse_csv)
        self.browse_button.grid(row=3, column=6, padx=4)
        self.start_button = ttk.Button(controls, text="START CA", command=self.start_ca)
        self.start_button.grid(row=3, column=7, padx=4)
        self.stop_button = ttk.Button(
            controls, text="STOP CA", command=self.stop_ca, state="disabled"
        )
        self.stop_button.grid(row=3, column=8, padx=4)

        figure = Figure(figsize=(7.5, 4.8), dpi=100)
        self.axes = figure.add_subplot(111)
        self.axes.set_xlabel("Time / s")
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
            title="Save CA data",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=Path(self.csv_var.get()).name,
        )
        if path:
            self.csv_var.set(path)

    def _parameters(self):
        values = {
            "vinit": float(self.vinit_var.get()),
            "tinit": float(self.tinit_var.get()),
            "vstep1": float(self.vstep1_var.get()),
            "tstep1": float(self.tstep1_var.get()),
            "vstep2": float(self.vstep2_var.get()),
            "tstep2": float(self.tstep2_var.get()),
            "sample_period": float(self.sample_period_var.get()),
            "ie_range": ie_range_enum(self.ie_range_var.get()),
        }
        if values["tinit"] < 0:
            raise ValueError("Initial time cannot be negative")
        if values["tstep1"] <= 0 or values["tstep2"] <= 0:
            raise ValueError("Step times must be greater than zero")
        if values["sample_period"] <= 0:
            raise ValueError("Sample period must be greater than zero")
        if not self.auto_ie_range_var.get() and values["ie_range"] <= 0:
            raise ValueError("Fixed IE range must be greater than zero")
        return values

    def start_ca(self):
        if self.running:
            return
        try:
            params = self._parameters()
            if self.claim_measurement is not None:
                if not self.claim_measurement("CA"):
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

            self.signal = self.pstat.signal_d_step_new(
                params["vinit"], params["tinit"],
                params["vstep1"], params["tstep1"],
                params["vstep2"], params["tstep2"],
                params["sample_period"], self.tkp.PSTATMODE,
            )
            self.pstat.set_signal_d_step(self.signal)
            self.pstat.init_signal()
            self.curve = self.tkp.ChronoCurve(self.pstat, 10000)
            self.active_params = params
            self.position_snapshot = self.position_provider()
            self.last_point_count = 0
            self.running = True
            self.pstat.set_cell(True)
            self.curve.run(True)
            self.curve_started_at = time.monotonic()
            self._set_controls_running(True)
            self.line.set_data([], [])
            self.canvas.draw_idle()
            self.status_var.set("CA RUNNING")
            self.after(self.POLL_MS, self._poll_ca)
        except Exception as error:
            self._safe_hardware_off()
            self._release_measurement()
            messagebox.showerror("CA start failed", str(error), parent=self)
            self.status_var.set("START FAILED: {}".format(error))

    def _poll_ca(self):
        if not self.running:
            return
        try:
            data = self.curve.acq_data()
            if len(data) > self.last_point_count:
                new_data = data[self.last_point_count:]
                self._append_csv(new_data)
                self.last_point_count = len(data)
            self._update_plot(data)

            curve_running = self.curve.running()
            pstat_valid = self.tkp.pstat_is_valid(self.pstat)
            if curve_running and pstat_valid:
                self.after(self.POLL_MS, self._poll_ca)
            elif (
                pstat_valid and self.last_point_count == 0
                and time.monotonic() - self.curve_started_at < 2.0
            ):
                self.status_var.set("CA STARTING — waiting for first point")
                self.after(self.POLL_MS, self._poll_ca)
            elif self.last_point_count == 0:
                raise RuntimeError(
                    "CA ended without data (PSTAT valid={}, curve running={})".format(
                        pstat_valid, curve_running
                    )
                )
            else:
                self._finish("CA COMPLETE — {} points".format(self.last_point_count))
        except Exception as error:
            self._finish("CA ERROR: {}".format(error))
            messagebox.showerror("CA error", str(error), parent=self)

    def _append_csv(self, data):
        if data is None or len(data) == 0:
            return
        names = list(data.dtype.names or ())
        if not names:
            raise ValueError("ToolkitPy returned CA data without named fields")
        path = Path(self.csv_var.get()).expanduser()
        need_header = not path.exists() or path.stat().st_size == 0
        x_mm, y_mm, z_mm = self.position_snapshot
        with open(str(path), "a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if need_header:
                writer.writerow([
                    "timestamp", "x_mm", "y_mm", "z_mm",
                    "vinit", "tinit_s", "vstep1", "tstep1_s",
                    "vstep2", "tstep2_s", "sample_period_s",
                    "ie_range", "auto_ie_range",
                ] + names)
            for row in data:
                writer.writerow([
                    datetime.now().isoformat(), x_mm, y_mm, z_mm,
                    self.active_params["vinit"], self.active_params["tinit"],
                    self.active_params["vstep1"], self.active_params["tstep1"],
                    self.active_params["vstep2"], self.active_params["tstep2"],
                    self.active_params["sample_period"],
                    self.active_params["ie_range"],
                    int(bool(self.auto_ie_range_var.get())),
                ] + [row[name] for name in names])

    def _update_plot(self, data):
        if data is None or len(data) == 0:
            return
        names = list(data.dtype.names or ())
        time_field = "t" if "t" in names else ("time" if "time" in names else None)
        if time_field is None or "im" not in names:
            raise ValueError("CA data is missing time/im fields: {}".format(names))
        self.line.set_data(
            np.asarray(data[time_field], dtype=float),
            np.asarray(data["im"], dtype=float),
        )
        self.axes.relim()
        self.axes.autoscale_view()
        self.canvas.draw_idle()

    def stop_ca(self):
        if not self.running:
            return
        if self.curve is not None:
            try:
                self.curve.stop()
            except Exception:
                pass
        self._finish("CA STOPPED BY USER")

    def _safe_hardware_off(self):
        try:
            self.pstat.set_cell(False)
        except Exception:
            pass

    def _finish(self, status):
        self.running = False
        self._safe_hardware_off()
        self.curve = None
        self.signal = None
        self._release_measurement()
        self._set_controls_running(False)
        self.status_var.set(status)

    def _release_measurement(self):
        if self.measurement_claimed and self.release_measurement is not None:
            self.release_measurement("CA")
        self.measurement_claimed = False

    def _set_controls_running(self, running):
        state = "disabled" if running else "normal"
        for widget in self.parameter_widgets:
            widget.configure(state=state)
        self.auto_ie_range_check.configure(state=state)
        self.ie_range_entry.configure(state=state)
        self.csv_entry.configure(state=state)
        self.browse_button.configure(state=state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def shutdown(self):
        if self.running:
            self.stop_ca()
        else:
            self._safe_hardware_off()

    def close_window(self):
        self.shutdown()
        self.destroy()
