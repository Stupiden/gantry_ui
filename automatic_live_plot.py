"""Live CV and EIS plots shared by automated measurement cycles."""

import csv
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


class AutomaticLivePlot(tk.Toplevel):
    def __init__(self, parent, show_cv=False, show_eis=False, show_ca=False):
        tk.Toplevel.__init__(self, parent)
        self.title("Automatic measurement — live plots")
        self.geometry("940x680")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        if show_cv:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text="CV")
            figure = Figure(figsize=(7.5, 5.5), dpi=100)
            self.cv_axes = figure.add_subplot(111)
            self.cv_axes.set_xlabel("Voltage / V")
            self.cv_axes.set_ylabel("Current / A")
            self.cv_axes.grid(True)
            self.cv_line, = self.cv_axes.plot([], [])
            self.cv_canvas = FigureCanvasTkAgg(figure, master=frame)
            self.cv_canvas.get_tk_widget().pack(fill="both", expand=True)
        if show_eis:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text="EIS / GetRu")
            figure = Figure(figsize=(8.5, 5.5), dpi=100)
            self.nyquist_axes = figure.add_subplot(221)
            self.bode_axes = figure.add_subplot(222)
            self.phase_axes = figure.add_subplot(212)
            self.nyquist_axes.set(xlabel="Zreal / Ω", ylabel="-Zimag / Ω")
            self.bode_axes.set(xlabel="Frequency / Hz", ylabel="|Z| / Ω", xscale="log", yscale="log")
            self.phase_axes.set(xlabel="Frequency / Hz", ylabel="Phase / °", xscale="log")
            for axes in (self.nyquist_axes, self.bode_axes, self.phase_axes):
                axes.grid(True)
            self.nyquist_line, = self.nyquist_axes.plot([], [], "o-")
            self.bode_line, = self.bode_axes.plot([], [], "o-")
            self.phase_line, = self.phase_axes.plot([], [], "o-")
            self.eis_canvas = FigureCanvasTkAgg(figure, master=frame)
            self.eis_canvas.get_tk_widget().pack(fill="both", expand=True)
        if show_ca:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text="CA")
            figure = Figure(figsize=(7.5, 5.5), dpi=100)
            self.ca_axes = figure.add_subplot(111)
            self.ca_axes.set_xlabel("Time / s")
            self.ca_axes.set_ylabel("Current / A")
            self.ca_axes.grid(True)
            self.ca_line, = self.ca_axes.plot([], [])
            self.ca_canvas = FigureCanvasTkAgg(figure, master=frame)
            self.ca_canvas.get_tk_widget().pack(fill="both", expand=True)

    def clear_cv(self):
        if not hasattr(self, "cv_line"):
            return
        self.cv_line.set_data([], [])
        self.cv_canvas.draw_idle()

    def update_cv(self, data):
        names = list(data.dtype.names or ())
        voltage = "vf" if "vf" in names else ("vu" if "vu" in names else None)
        if voltage is None or "im" not in names:
            return
        self.cv_line.set_data(np.asarray(data[voltage], float), np.asarray(data["im"], float))
        self.cv_axes.relim()
        self.cv_axes.autoscale_view()
        self.cv_canvas.draw_idle()

    def clear_eis(self):
        if not hasattr(self, "nyquist_line"):
            return
        for line in (self.nyquist_line, self.bode_line, self.phase_line):
            line.set_data([], [])
        self.eis_canvas.draw_idle()

    def update_eis_csv(self, csv_path):
        if not hasattr(self, "nyquist_line"):
            return
        path = Path(csv_path)
        if not path.exists() or path.stat().st_size == 0:
            return
        try:
            with open(str(path), "r", newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            zreal = [float(row["zreal"]) for row in rows if row.get("zreal")]
            zimag = [float(row["zimag"]) for row in rows if row.get("zimag")]
            freq = [float(row["zfreq"]) for row in rows if row.get("zfreq")]
            zmod = [float(row["zmod"]) for row in rows if row.get("zmod")]
            phase = [float(row["phase_deg"]) for row in rows if row.get("phase_deg")]
        except (OSError, ValueError, KeyError):
            return
        if len(zreal) == len(zimag) and zreal:
            self.nyquist_line.set_data(zreal, [-value for value in zimag])
            self.nyquist_axes.relim(); self.nyquist_axes.autoscale_view()
        if len(freq) == len(zmod) and freq:
            self.bode_line.set_data(freq, zmod)
            self.bode_axes.relim(); self.bode_axes.autoscale_view()
        if len(freq) == len(phase) and freq:
            self.phase_line.set_data(freq, phase)
            self.phase_axes.relim(); self.phase_axes.autoscale_view()
        self.eis_canvas.draw_idle()

    def clear_ca(self):
        if not hasattr(self, "ca_line"):
            return
        self.ca_line.set_data([], [])
        self.ca_canvas.draw_idle()

    def update_ca(self, data):
        names = list(data.dtype.names or ())
        time_field = "t" if "t" in names else ("time" if "time" in names else None)
        if time_field is None or "im" not in names:
            return
        self.ca_line.set_data(np.asarray(data[time_field], float), np.asarray(data["im"], float))
        self.ca_axes.relim()
        self.ca_axes.autoscale_view()
        self.ca_canvas.draw_idle()
