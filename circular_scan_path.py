"""Milestone 10: generate and preview a circular XY scan path only."""

import csv
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle


MAX_PREVIEW_POINTS = 100000


def generate_circular_path(center_x, center_y, diameter, spacing):
    """Return a centered, row-serpentine square grid clipped to a circle."""
    center_x = float(center_x)
    center_y = float(center_y)
    diameter = float(diameter)
    spacing = float(spacing)

    if not all(math.isfinite(value) for value in (center_x, center_y, diameter, spacing)):
        raise ValueError("Center, diameter, and spacing must be finite numbers")
    if diameter <= 0.0:
        raise ValueError("Diameter must be greater than zero")
    if spacing <= 0.0:
        raise ValueError("Spacing must be greater than zero")

    radius = diameter / 2.0
    half_steps = int(math.floor(radius / spacing + 1e-12))
    estimated_side = 2 * half_steps + 1
    if estimated_side * estimated_side > MAX_PREVIEW_POINTS * 2:
        raise ValueError("Spacing is too small: preview would contain too many points")

    radius_sq = radius * radius
    tolerance = max(1e-12, radius_sq * 1e-12)
    points = []

    for row_number, y_step in enumerate(range(-half_steps, half_steps + 1)):
        y_offset = y_step * spacing
        row = []
        for x_step in range(-half_steps, half_steps + 1):
            x_offset = x_step * spacing
            if x_offset * x_offset + y_offset * y_offset <= radius_sq + tolerance:
                row.append((center_x + x_offset, center_y + y_offset))

        if row_number % 2:
            row.reverse()
        points.extend(row)

        if len(points) > MAX_PREVIEW_POINTS:
            raise ValueError("Spacing is too small: preview exceeds 100000 points")

    return [
        {"index": index, "x": point[0], "y": point[1]}
        for index, point in enumerate(points, start=1)
    ]


class CircularScanPathWindow(tk.Toplevel):
    """Generate, inspect, plot, and export a circular path without motion."""

    def __init__(
        self, parent, dry_run_callback=None, press_cycle_callback=None,
        pump_cycle_callback=None
    ):
        tk.Toplevel.__init__(self, parent)
        self.title("Circular scan path — preview only")
        self.geometry("980x680")
        self.minsize(760, 540)

        self.center_x_var = tk.DoubleVar(value=0.0)
        self.center_y_var = tk.DoubleVar(value=0.0)
        self.path_diameter_var = tk.DoubleVar(value=43.0)
        self.reference_diameter_var = tk.DoubleVar(value=46.0)
        self.spacing_var = tk.DoubleVar(value=2.0)
        self.summary_var = tk.StringVar(value="No path generated")
        self.points = []
        self.dry_run_callback = dry_run_callback
        self.press_cycle_callback = press_cycle_callback
        self.pump_cycle_callback = pump_cycle_callback

        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        controls = ttk.LabelFrame(self, text="Circular path parameters", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        specs = (
            ("Center X (mm)", self.center_x_var),
            ("Center Y (mm)", self.center_y_var),
            ("Path diameter (mm)", self.path_diameter_var),
            ("Reference diameter (mm)", self.reference_diameter_var),
            ("Spacing (mm)", self.spacing_var),
        )
        for column, (label, variable) in enumerate(specs):
            ttk.Label(controls, text=label).grid(row=0, column=column * 2, padx=(0, 4))
            entry = ttk.Entry(controls, textvariable=variable, width=11)
            entry.grid(row=0, column=column * 2 + 1, padx=(0, 12))
            entry.bind("<Return>", lambda event: self.generate())

        ttk.Button(controls, text="GENERATE", command=self.generate).grid(
            row=0, column=10, padx=(4, 4)
        )
        self.export_button = ttk.Button(
            controls, text="EXPORT CSV", command=self.export_csv, state="disabled"
        )
        self.export_button.grid(row=0, column=11, padx=(4, 0))
        self.dry_run_button = ttk.Button(
            controls, text="XY DRY RUN", command=self.start_dry_run, state="disabled"
        )
        self.dry_run_button.grid(row=1, column=10, columnspan=2, padx=4, pady=(5, 0))
        self.press_cycle_button = ttk.Button(
            controls, text="PRESS CYCLE", command=self.start_press_cycle, state="disabled"
        )
        self.press_cycle_button.grid(row=2, column=10, columnspan=2, padx=4, pady=(5, 0))
        self.pump_cycle_button = ttk.Button(
            controls, text="PUMP CYCLE", command=self.start_pump_cycle, state="disabled"
        )
        self.pump_cycle_button.grid(row=3, column=10, columnspan=2, padx=4, pady=(5, 0))
        ttk.Label(controls, textvariable=self.summary_var).grid(
            row=1, column=0, columnspan=12, sticky="w", pady=(7, 0)
        )

        content = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        content.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        table_frame = ttk.Frame(content)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(
            table_frame, columns=("index", "x", "y"), show="headings", height=20
        )
        for key, heading, width in (
            ("index", "Index", 70),
            ("x", "X (mm)", 115),
            ("y", "Y (mm)", 115),
        ):
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, anchor="e", stretch=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        plot_frame = ttk.Frame(content)
        plot_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)
        figure = Figure(figsize=(5.5, 5.5), dpi=100)
        self.axes = figure.add_subplot(111)
        self.axes.set_xlabel("Scan X / mm")
        self.axes.set_ylabel("Scan Y / mm")
        self.axes.grid(True)
        self.axes.set_aspect("equal", adjustable="box")
        self.canvas = FigureCanvasTkAgg(figure, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        content.add(table_frame, weight=1)
        content.add(plot_frame, weight=2)

    def generate(self):
        try:
            points = generate_circular_path(
                self.center_x_var.get(),
                self.center_y_var.get(),
                self.path_diameter_var.get(),
                self.spacing_var.get(),
            )
        except (tk.TclError, ValueError) as error:
            messagebox.showerror("Circular path", str(error), parent=self)
            return

        self.points = points
        self.table.delete(*self.table.get_children())
        for point in points:
            self.table.insert(
                "", "end",
                values=(point["index"], "{:.3f}".format(point["x"]), "{:.3f}".format(point["y"])),
            )

        center_x = float(self.center_x_var.get())
        center_y = float(self.center_y_var.get())
        path_diameter = float(self.path_diameter_var.get())
        reference_diameter = float(self.reference_diameter_var.get())
        if not math.isfinite(reference_diameter) or reference_diameter <= 0.0:
            messagebox.showerror(
                "Circular path", "Reference diameter must be greater than zero", parent=self
            )
            return
        path_radius = path_diameter / 2.0
        reference_radius = reference_diameter / 2.0
        self.axes.clear()
        self.axes.set_xlabel("Scan X / mm")
        self.axes.set_ylabel("Scan Y / mm")
        self.axes.grid(True)
        self.axes.set_aspect("equal", adjustable="box")
        self.axes.add_patch(
            Circle(
                (center_x, center_y), path_radius, fill=False, color="tab:blue",
                linewidth=1.4, label="Path circle: D={:.3f} mm".format(path_diameter)
            )
        )
        self.axes.add_patch(
            Circle(
                (center_x, center_y), reference_radius, fill=False, color="black",
                linestyle="--", linewidth=1.2,
                label="Reference circle: D={:.3f} mm".format(reference_diameter)
            )
        )
        self.axes.plot(center_x, center_y, "+", color="black", markersize=9)
        if points:
            x_values = [point["x"] for point in points]
            y_values = [point["y"] for point in points]
            self.axes.plot(x_values, y_values, "o-", markersize=3, linewidth=0.8)
            self.axes.plot(x_values[0], y_values[0], "o", color="green", markersize=7)
            self.axes.plot(x_values[-1], y_values[-1], "s", color="red", markersize=6)
        self.axes.legend(loc="best", fontsize=8)
        display_radius = max(path_radius, reference_radius)
        margin = max(display_radius * 0.08, 0.5)
        self.axes.set_xlim(center_x - display_radius - margin, center_x + display_radius + margin)
        self.axes.set_ylim(center_y - display_radius - margin, center_y + display_radius + margin)
        self.canvas.draw_idle()

        self.summary_var.set(
            "{} points | path D {:.3f} mm | reference D {:.3f} mm | preview only — no motion commands".format(
                len(points), path_diameter, reference_diameter
            )
        )
        self.export_button.configure(state="normal")
        if self.dry_run_callback is not None:
            self.dry_run_button.configure(state="normal")
        if self.press_cycle_callback is not None:
            self.press_cycle_button.configure(state="normal")
        if self.pump_cycle_callback is not None:
            self.pump_cycle_button.configure(state="normal")

    def start_dry_run(self):
        if self.points and self.dry_run_callback is not None:
            self.dry_run_callback(self.points, "Circular path")

    def start_press_cycle(self):
        if self.points and self.press_cycle_callback is not None:
            self.press_cycle_callback(self.points, "Circular path")

    def start_pump_cycle(self):
        if self.points and self.pump_cycle_callback is not None:
            self.pump_cycle_callback(self.points, "Circular path")

    def export_csv(self):
        if not self.points:
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export circular scan path",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile="circular_scan_path.csv",
        )
        if not path:
            return
        with open(str(Path(path)), "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("index", "x", "y"))
            writer.writeheader()
            writer.writerows(self.points)
        self.summary_var.set("Exported {} points to {}".format(len(self.points), path))
