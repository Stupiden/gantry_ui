"""Manual EIS/GetRu window around the verified MG400 GamryEIS engine."""

import csv
import queue
import re
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from pre_gamry_eis_getru_max import GamryEIS


DEFAULT_FREQUENCIES = (
    "1000000, 300000, 170000, 100000, 30000, "
    "10000, 3000, 1000, 500, 2.5"
)


class ManualEISWindow(tk.Toplevel):
    POLL_MS = 200

    def __init__(
        self, parent, pstat, position_provider,
        claim_measurement=None, release_measurement=None
    ):
        tk.Toplevel.__init__(self, parent)
        self.title("Manual Gamry EIS / GetRu")
        self.geometry("1000x760")
        self.minsize(820, 620)
        self.pstat = pstat
        self.position_provider = position_provider
        self.claim_measurement = claim_measurement
        self.release_measurement = release_measurement
        self.measurement_claimed = False
        self.running = False
        self.close_pending = False
        self.worker = None
        self.result_queue = queue.Queue()

        self.frequencies_var = tk.StringVar(value=DEFAULT_FREQUENCIES)
        self.repeats_var = tk.IntVar(value=3)
        # Official GetRu.exp defaults: 7 mV RMS and 40 ohm ZGuess.
        self.ac_amplitude_var = tk.DoubleVar(value=0.007)
        self.dc_bias_var = tk.DoubleVar(value=-1.0)
        self.gain_var = tk.DoubleVar(value=1.0)
        self.zmod_var = tk.DoubleVar(value=40.0)
        self.passes_var = tk.IntVar(value=10)
        self.cycles_delay_var = tk.IntVar(value=1)
        self.settle_var = tk.DoubleVar(value=0.5)
        self.max_phase_var = tk.DoubleVar(value=7.5)
        self.z_bad_var = tk.DoubleVar(value=10000.0)
        self.min_bad_var = tk.IntVar(value=2)
        self.csv_var = tk.StringVar(value=self._default_csv_path())
        self.status_var = tk.StringVar(value="READY")
        self.ru_var = tk.StringVar(value="Ru: —")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close_window)

    def _default_csv_path(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(Path.cwd() / "EIS_{}.csv".format(stamp))

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.LabelFrame(self, text="EIS / GetRu parameters", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Frequencies Hz").grid(row=0, column=0, sticky="e")
        self.frequency_entry = ttk.Entry(
            controls, textvariable=self.frequencies_var
        )
        self.frequency_entry.grid(row=0, column=1, columnspan=9, sticky="ew", padx=4)

        specs = (
            ("Repeats", self.repeats_var),
            ("AC amplitude V", self.ac_amplitude_var),
            ("DC bias V", self.dc_bias_var),
            ("Gain", self.gain_var),
            ("Zmod Ω", self.zmod_var),
            ("Passes", self.passes_var),
            ("Cycles delay", self.cycles_delay_var),
            ("Settle s", self.settle_var),
            ("Max phase °", self.max_phase_var),
            ("Bad Z Ω", self.z_bad_var),
            ("Min bad points", self.min_bad_var),
        )
        self.parameter_widgets = [self.frequency_entry]
        for index, (label, variable) in enumerate(specs):
            row = 1 + index // 4
            column = (index % 4) * 2
            ttk.Label(controls, text=label).grid(
                row=row, column=column, sticky="e", padx=(0, 4), pady=3
            )
            entry = ttk.Entry(controls, textvariable=variable, width=10)
            entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 10), pady=3)
            self.parameter_widgets.append(entry)

        ttk.Label(controls, text="CSV").grid(row=4, column=0, sticky="e")
        self.csv_entry = ttk.Entry(controls, textvariable=self.csv_var)
        self.csv_entry.grid(row=4, column=1, columnspan=5, sticky="ew", padx=4)
        self.browse_button = ttk.Button(controls, text="BROWSE", command=self.browse_csv)
        self.browse_button.grid(row=4, column=6, padx=4)
        self.start_button = ttk.Button(controls, text="START EIS", command=self.start_eis)
        self.start_button.grid(row=4, column=7, padx=4)
        self.stop_button = ttk.Button(
            controls, text="STOP EIS", command=self.stop_eis, state="disabled"
        )
        self.stop_button.grid(row=4, column=8, padx=4)
        ttk.Label(
            controls, textvariable=self.ru_var, font=("Segoe UI", 10, "bold")
        ).grid(row=4, column=9, padx=(8, 0))

        figure = Figure(figsize=(9, 5), dpi=100)
        grid = figure.add_gridspec(
            2, 2, width_ratios=(1.05, 1.0), hspace=0.12, wspace=0.30
        )
        self.nyquist_axes = figure.add_subplot(grid[:, 0])
        self.bode_axes = figure.add_subplot(grid[0, 1])
        self.phase_axes = figure.add_subplot(grid[1, 1], sharex=self.bode_axes)
        self.nyquist_axes.set_xlabel("Z real / Ω")
        self.nyquist_axes.set_ylabel("−Z imaginary / Ω")
        self.nyquist_axes.grid(True)
        self.bode_axes.set_ylabel("|Z| / Ω")
        self.bode_axes.set_xscale("log")
        self.bode_axes.set_yscale("log")
        self.bode_axes.grid(True)
        self.bode_axes.tick_params(axis="x", labelbottom=False)
        self.phase_axes.set_xlabel("Frequency / Hz")
        self.phase_axes.set_ylabel("Phase / °")
        self.phase_axes.set_xscale("log")
        self.phase_axes.grid(True)
        self.phase_axes.axhline(0.0, color="0.55", linewidth=0.8)
        self.nyquist_line, = self.nyquist_axes.plot([], [], "o-")
        self.bode_line, = self.bode_axes.plot([], [], "o-")
        self.phase_line, = self.phase_axes.plot([], [], "o-", color="tab:orange")
        self.canvas = FigureCanvasTkAgg(figure, master=self)
        self.canvas.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=8, pady=(0, 4)
        )
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, sticky="ew", padx=8, pady=(0, 6)
        )

    def browse_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save EIS data", defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=Path(self.csv_var.get()).name,
        )
        if path:
            self.csv_var.set(path)

    def _frequencies(self):
        tokens = [item for item in re.split(r"[,;\s]+", self.frequencies_var.get()) if item]
        values = [float(item) for item in tokens]
        if not values or any(value <= 0 for value in values):
            raise ValueError("Frequencies must contain positive numbers")
        return values

    def _parameters(self):
        values = {
            "frequencies": self._frequencies(),
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
        if values["n_times"] < 1 or values["passes"] < 1:
            raise ValueError("Repeats and passes must be at least 1")
        if values["ac_amplitude"] <= 0 or values["zmod"] <= 0:
            raise ValueError("AC amplitude and Zmod must be greater than zero")
        if values["dc_settle_s"] < 0 or values["min_bad_points"] < 1:
            raise ValueError("Settle time/min bad points are invalid")
        return values

    def start_eis(self):
        if self.running:
            return
        try:
            params = self._parameters()
            path = Path(self.csv_var.get()).expanduser()
            if not str(path).strip():
                raise ValueError("Choose a CSV output path")
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.claim_measurement is not None:
                if not self.claim_measurement("EIS"):
                    raise RuntimeError("Another Gamry measurement is already running")
                self.measurement_claimed = True
            position = self.position_provider()
            pose4 = (position[0], position[1], position[2], 0.0)
        except Exception as error:
            self._release_measurement()
            messagebox.showerror("EIS start failed", str(error), parent=self)
            return

        self.running = True
        self.ru_var.set("Ru: —")
        self.status_var.set("EIS RUNNING")
        self._set_controls_running(True)
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(str(path), pose4, params),
            daemon=True,
        )
        self.worker.start()
        self.after(self.POLL_MS, self._poll_worker)

    def _run_worker(self, csv_path, pose4, params):
        try:
            engine = GamryEIS(
                output_csv=csv_path,
                pstat=self.pstat,
                auto_initialize=True,
            )
            result = engine.run_eis(
                measurement_index=0,
                robot_pose=pose4,
                inoise=1e-10,
                vnoise=1e-6,
                ienoise=1e-10,
                drift_cor=False,
                **params
            )
            self.result_queue.put(("done", result))
        except Exception as error:
            self.result_queue.put(("error", str(error)))

    def _poll_worker(self):
        if not self.running:
            return
        self._refresh_plot()
        try:
            kind, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(self.POLL_MS, self._poll_worker)
            return

        if kind == "done":
            self.ru_var.set("Ru: {:.6g} Ω".format(float(payload["avg_ru"])))
            self._finish("EIS COMPLETE — {} rows".format(payload["rows_written"]))
        else:
            self._finish("EIS ERROR: {}".format(payload))
            if not self.close_pending:
                messagebox.showerror("EIS error", payload, parent=self)

    def _refresh_plot(self):
        path = Path(self.csv_var.get()).expanduser()
        if not path.exists() or path.stat().st_size == 0:
            return
        try:
            with open(str(path), "r", newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            zreal = [float(row["zreal"]) for row in rows if row.get("zreal")]
            zimag = [float(row["zimag"]) for row in rows if row.get("zimag")]
            frequencies = [float(row["zfreq"]) for row in rows if row.get("zfreq")]
            zmod = [float(row["zmod"]) for row in rows if row.get("zmod")]
            phase = [float(row["phase_deg"]) for row in rows if row.get("phase_deg")]
            if len(zreal) == len(zimag) and zreal:
                self.nyquist_line.set_data(zreal, [-value for value in zimag])
                self.nyquist_axes.relim()
                self.nyquist_axes.autoscale_view()
            if len(frequencies) == len(zmod) and frequencies:
                self.bode_line.set_data(frequencies, zmod)
                self.bode_axes.relim()
                self.bode_axes.autoscale_view()
            if len(frequencies) == len(phase) and frequencies:
                self.phase_line.set_data(frequencies, phase)
                self.phase_axes.relim()
                self.phase_axes.autoscale_view()
            self.canvas.draw_idle()
            self.status_var.set("EIS RUNNING — {} rows".format(len(rows)))
        except (OSError, ValueError, KeyError):
            return

    def stop_eis(self):
        if not self.running:
            return
        self.status_var.set("EIS STOPPING")
        try:
            self.pstat.set_cell(False)
        except Exception:
            pass

    def _finish(self, status):
        self.running = False
        try:
            self.pstat.set_cell(False)
            self.pstat.set_pos_feed_enable(False)
            self.pstat.set_pos_feed_resistance(0.0)
        except Exception:
            pass
        self._release_measurement()
        self._set_controls_running(False)
        self._refresh_plot()
        self.status_var.set(status)
        if self.close_pending:
            self.destroy()

    def _release_measurement(self):
        if self.measurement_claimed and self.release_measurement is not None:
            self.release_measurement("EIS")
        self.measurement_claimed = False

    def _set_controls_running(self, running):
        state = "disabled" if running else "normal"
        for widget in self.parameter_widgets:
            widget.configure(state=state)
        self.csv_entry.configure(state=state)
        self.browse_button.configure(state=state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def shutdown(self):
        if self.running:
            self.stop_eis()

    def close_window(self):
        if self.running:
            self.close_pending = True
            self.stop_eis()
            return
        self.destroy()
