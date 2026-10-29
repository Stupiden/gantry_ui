"""Manual single-direction LSV using the verified Gamry CV setup."""

import csv
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

from gamry_cv_manual import ManualCVWindow
from gamry_ie_range import enum_value as ie_range_enum


class ManualLSVWindow(ManualCVWindow):
    MEASUREMENT_NAME = "LSV"

    def __init__(self, *args, **kwargs):
        ManualCVWindow.__init__(self, *args, **kwargs)
        self.title("Manual Gamry LSV")
        self.scan_rate_var.set(0.01)

    def _default_csv_path(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(Path.cwd() / "LSV_{}.csv".format(stamp))

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.LabelFrame(self, text="LSV parameters", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        specs = (
            ("Initial V", self.initial_var), ("Final V", self.final_var),
            ("Scan V/s", self.scan_rate_var), ("Step V", self.step_size_var),
            ("Precharge s", self.precharge_var),
        )
        self.parameter_widgets = []
        for index, (label, variable) in enumerate(specs):
            ttk.Label(controls, text=label).grid(row=0, column=index * 2, padx=(0, 4))
            entry = ttk.Entry(controls, textvariable=variable, width=9)
            entry.grid(row=0, column=index * 2 + 1, padx=(0, 9))
            self.parameter_widgets.append(entry)

        self.pos_feed_check = ttk.Checkbutton(
            controls, text="Enable positive feedback", variable=self.pos_feed_var
        )
        self.pos_feed_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(controls, text="Ru Ω").grid(row=1, column=2, sticky="e")
        self.ru_entry = ttk.Entry(controls, textvariable=self.ru_var, width=9)
        self.ru_entry.grid(row=1, column=3, sticky="w")
        ttk.Label(controls, text="Ru fraction").grid(row=1, column=4, sticky="e")
        self.ru_fraction_entry = ttk.Entry(controls, textvariable=self.ru_fraction_var, width=9)
        self.ru_fraction_entry.grid(row=1, column=5, sticky="w")
        self.auto_ie_range_check = ttk.Checkbutton(
            controls, text="Auto IE range", variable=self.auto_ie_range_var
        )
        self.auto_ie_range_check.grid(row=1, column=6, sticky="w")
        ttk.Label(controls, text="IE range").grid(row=1, column=7, sticky="e")
        self.ie_range_entry = ttk.Combobox(
            controls, textvariable=self.ie_range_var,
            values=self.ie_range_choices, state="readonly", width=14
        )
        self.ie_range_entry.grid(row=1, column=8, sticky="w")

        ttk.Label(controls, text="CSV").grid(row=2, column=0, sticky="e")
        self.csv_entry = ttk.Entry(controls, textvariable=self.csv_var)
        self.csv_entry.grid(row=2, column=1, columnspan=5, sticky="ew", padx=4)
        controls.columnconfigure(5, weight=1)
        self.browse_button = ttk.Button(controls, text="BROWSE", command=self.browse_csv)
        self.browse_button.grid(row=2, column=6, padx=4)
        self.start_button = ttk.Button(controls, text="START LSV", command=self.start_cv)
        self.start_button.grid(row=2, column=7, padx=4)
        self.stop_button = ttk.Button(controls, text="STOP LSV", command=self.stop_cv, state="disabled")
        self.stop_button.grid(row=2, column=8, padx=4)

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        figure = Figure(figsize=(7.5, 4.8), dpi=100)
        self.axes = figure.add_subplot(111)
        self.axes.set(xlabel="Potential / V", ylabel="Current / A")
        self.axes.grid(True)
        self.line, = self.axes.plot([], [])
        self.canvas = FigureCanvasTkAgg(figure, master=self)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, sticky="ew", padx=8, pady=(0, 6)
        )

    def browse_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save LSV data", defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=Path(self.csv_var.get()).name,
        )
        if path:
            self.csv_var.set(path)

    def _parameters(self):
        values = {
            "initial": float(self.initial_var.get()),
            "final": float(self.final_var.get()),
            "scan_rate": float(self.scan_rate_var.get()),
            "step_size": float(self.step_size_var.get()),
            "precharge": float(self.precharge_var.get()),
            "ru": float(self.ru_var.get()),
            "ru_fraction": float(self.ru_fraction_var.get()),
            "ie_range": ie_range_enum(self.ie_range_var.get()),
        }
        if values["initial"] == values["final"]:
            raise ValueError("Initial and final voltage must be different")
        if values["scan_rate"] <= 0 or values["step_size"] <= 0:
            raise ValueError("Scan rate and step size must be greater than zero")
        sample_period = values["step_size"] / values["scan_rate"]
        if not 0.1 <= sample_period <= 600.0:
            raise ValueError("Step V / Scan V/s must give a 0.1–600 s sample period")
        if values["precharge"] < 0:
            raise ValueError("Precharge cannot be negative")
        if self.pos_feed_var.get() and (
            values["ru"] <= 0 or not 0.0 < values["ru_fraction"] <= 1.0
        ):
            raise ValueError("Positive feedback requires Ru > 0 and fraction within 0–1")
        if not self.auto_ie_range_var.get() and values["ie_range"] <= 0:
            raise ValueError("Fixed IE range must be greater than zero")
        return values

    def _create_signal(self, params, sample_time):
        return self.pstat.signal_ramp_new(
            params["initial"], params["final"], params["scan_rate"],
            sample_time, self.tkp.PSTATMODE,
        )

    def _attach_signal(self, signal):
        self.pstat.set_signal_ramp(signal)

    def _append_csv(self, data):
        if data is None or len(data) == 0:
            return
        names = list(data.dtype.names or ())
        if not names:
            raise ValueError("ToolkitPy returned LSV data without named fields")
        path = Path(self.csv_var.get()).expanduser()
        need_header = not path.exists() or path.stat().st_size == 0
        x_mm, y_mm, z_mm = self.position_snapshot
        with path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if need_header:
                writer.writerow([
                    "timestamp", "x_mm", "y_mm", "z_mm", "initial_v", "final_v",
                    "scan_rate_v_s", "step_v", "precharge_s", "ru_ohm",
                    "ru_fraction", "pos_feed_resistance_ohm", "pos_feed_enabled",
                    "ie_range", "auto_ie_range",
                ] + names)
            for row in data:
                writer.writerow([
                    datetime.now().isoformat(), x_mm, y_mm, z_mm,
                    self.active_params["initial"], self.active_params["final"],
                    self.active_params["scan_rate"], self.active_params["step_size"],
                    self.active_params["precharge"], self.active_params["ru"],
                    self.active_params["ru_fraction"], self.pos_feed_resistance,
                    int(bool(self.pos_feed_var.get())), self.active_params["ie_range"],
                    int(bool(self.auto_ie_range_var.get())),
                ] + [row[name] for name in names])
