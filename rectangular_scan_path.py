"""Milestone 11: generate and preview a rectangular XY scan path only."""

import csv
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle


MAX_PREVIEW_POINTS = 100000


def _centered_offsets(length, spacing):
    intervals = int(math.floor(length / spacing + 1e-12))
    return [(index - intervals / 2.0) * spacing for index in range(intervals + 1)]


def generate_rectangular_path(
    center_x, center_y, width, height, x_spacing, y_spacing, serpentine=True
):
    """Return a centered rectangular grid in row order or serpentine order."""
    values = tuple(
        float(value)
        for value in (center_x, center_y, width, height, x_spacing, y_spacing)
    )
    center_x, center_y, width, height, x_spacing, y_spacing = values
    if not all(math.isfinite(value) for value in values):
        raise ValueError("All rectangular path parameters must be finite numbers")
    if width <= 0.0 or height <= 0.0:
        raise ValueError("Width and height must be greater than zero")
    if x_spacing <= 0.0 or y_spacing <= 0.0:
        raise ValueError("X and Y spacing must be greater than zero")

    x_offsets = _centered_offsets(width, x_spacing)
    y_offsets = _centered_offsets(height, y_spacing)
    if len(x_offsets) * len(y_offsets) > MAX_PREVIEW_POINTS:
        raise ValueError("Spacing is too small: preview exceeds 100000 points")

    points = []
    for row_number, y_offset in enumerate(y_offsets):
        row_x = list(x_offsets)
        if serpentine and row_number % 2:
            row_x.reverse()
        for x_offset in row_x:
            points.append((center_x + x_offset, center_y + y_offset))

    return [
        {"index": index, "x": point[0], "y": point[1]}
        for index, point in enumerate(points, start=1)
    ]


class RectangularScanPathWindow(tk.Toplevel):
    """Generate, inspect, plot, and export a rectangular path without motion."""

    def __init__(
        self, parent, dry_run_callback=None, press_cycle_callback=None,
        pump_cycle_callback=None, full_cv_callback=None
    ):
        tk.Toplevel.__init__(self, parent)
        self.title("Rectangular scan path — preview only")
        self.geometry("1040x680")
        self.minsize(800, 540)

        self.center_x_var = tk.DoubleVar(value=0.0)
        self.center_y_var = tk.DoubleVar(value=0.0)
        self.width_var = tk.DoubleVar(value=10.0)
        self.height_var = tk.DoubleVar(value=10.0)
        self.reference_width_var = tk.DoubleVar(value=20.0)
        self.reference_height_var = tk.DoubleVar(value=20.0)
        self.x_spacing_var = tk.DoubleVar(value=2.0)
        self.y_spacing_var = tk.DoubleVar(value=2.0)
        self.serpentine_var = tk.BooleanVar(value=True)
        self.summary_var = tk.StringVar(value="No path generated")
        self.points = []
        self.dry_run_callback = dry_run_callback
        self.press_cycle_callback = press_cycle_callback
        self.pump_cycle_callback = pump_cycle_callback
        self.full_cv_callback = full_cv_callback
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.LabelFrame(self, text="Rectangular path parameters", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        specs = (
            ("Center X", self.center_x_var),
            ("Center Y", self.center_y_var),
            ("Width", self.width_var),
            ("Height", self.height_var),
            ("X spacing", self.x_spacing_var),
            ("Y spacing", self.y_spacing_var),
            ("Reference width", self.reference_width_var),
            ("Reference height", self.reference_height_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = index // 3
            column = (index % 3) * 2
            ttk.Label(controls, text="{} (mm)".format(label)).grid(
                row=row, column=column, padx=(0, 4), pady=2
            )
            entry = ttk.Entry(controls, textvariable=variable, width=11)
            entry.grid(row=row, column=column + 1, padx=(0, 12), pady=2)
            entry.bind("<Return>", lambda event: self.generate())

        ttk.Checkbutton(
            controls, text="Serpentine", variable=self.serpentine_var
        ).grid(row=0, column=6, padx=(8, 8))
        ttk.Button(controls, text="GENERATE", command=self.generate).grid(
            row=0, column=7, padx=4
        )
        self.export_button = ttk.Button(
            controls, text="EXPORT CSV", command=self.export_csv, state="disabled"
        )
        self.export_button.grid(row=1, column=7, padx=4)
        self.dry_run_button = ttk.Button(
            controls, text="XY DRY RUN", command=self.start_dry_run, state="disabled"
        )
        self.dry_run_button.grid(row=2, column=7, padx=4)
        self.press_cycle_button = ttk.Button(
            controls, text="PRESS CYCLE", command=self.start_press_cycle, state="disabled"
        )
        self.press_cycle_button.grid(row=3, column=7, padx=4)
        self.pump_cycle_button = ttk.Button(
            controls, text="PUMP CYCLE", command=self.start_pump_cycle, state="disabled"
        )
        self.pump_cycle_button.grid(row=4, column=7, padx=4)
        self.full_cv_button = ttk.Button(
            controls, text="FULL CV CYCLE", command=self.start_full_cv, state="disabled"
        )
        self.full_cv_button.grid(row=5, column=7, padx=4)
        ttk.Label(controls, textvariable=self.summary_var).grid(
            row=3, column=0, columnspan=8, sticky="w", pady=(6, 0)
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
            ("index", "Index", 70), ("x", "X (mm)", 115), ("y", "Y (mm)", 115)
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
        self.canvas = FigureCanvasTkAgg(figure, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        content.add(table_frame, weight=1)
        content.add(plot_frame, weight=2)
        self._format_axes()

    def _format_axes(self):
        self.axes.set_xlabel("Scan X / mm")
        self.axes.set_ylabel("Scan Y / mm")
        self.axes.grid(True)
        self.axes.set_aspect("equal", adjustable="box")

    def generate(self):
        try:
            points = generate_rectangular_path(
                self.center_x_var.get(), self.center_y_var.get(),
                self.width_var.get(), self.height_var.get(),
                self.x_spacing_var.get(), self.y_spacing_var.get(),
                self.serpentine_var.get(),
            )
        except (tk.TclError, ValueError) as error:
            messagebox.showerror("Rectangular path", str(error), parent=self)
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
        width = float(self.width_var.get())
        height = float(self.height_var.get())
        reference_width = float(self.reference_width_var.get())
        reference_height = float(self.reference_height_var.get())
        if (
            not math.isfinite(reference_width)
            or not math.isfinite(reference_height)
            or reference_width <= 0.0
            or reference_height <= 0.0
        ):
            messagebox.showerror(
                "Rectangular path",
                "Reference width and height must be greater than zero",
                parent=self,
            )
            return
        self.axes.clear()
        self._format_axes()
        self.axes.add_patch(
            Rectangle(
                (center_x - width / 2.0, center_y - height / 2.0),
                width, height, fill=False, color="tab:blue", linewidth=1.4,
                label="Path: {:.3f} × {:.3f} mm".format(width, height),
            )
        )
        self.axes.add_patch(
            Rectangle(
                (
                    center_x - reference_width / 2.0,
                    center_y - reference_height / 2.0,
                ),
                reference_width, reference_height, fill=False, color="black",
                linestyle="--", linewidth=1.2,
                label="Reference: {:.3f} × {:.3f} mm".format(
                    reference_width, reference_height
                ),
            )
        )
        x_values = [point["x"] for point in points]
        y_values = [point["y"] for point in points]
        self.axes.plot(x_values, y_values, "o-", markersize=3, linewidth=0.8)
        self.axes.plot(x_values[0], y_values[0], "o", color="green", markersize=7)
        self.axes.plot(x_values[-1], y_values[-1], "s", color="red", markersize=6)
        self.axes.legend(loc="best", fontsize=8)
        display_width = max(width, reference_width)
        display_height = max(height, reference_height)
        margin = max(max(display_width, display_height) * 0.08, 0.5)
        self.axes.set_xlim(
            center_x - display_width / 2.0 - margin,
            center_x + display_width / 2.0 + margin,
        )
        self.axes.set_ylim(
            center_y - display_height / 2.0 - margin,
            center_y + display_height / 2.0 + margin,
        )
        self.canvas.draw_idle()
        mode = "serpentine" if self.serpentine_var.get() else "row order"
        self.summary_var.set(
            "{} points | path {:.3f}×{:.3f} mm | reference {:.3f}×{:.3f} mm | {} | preview only".format(
                len(points), width, height, reference_width, reference_height, mode
            )
        )
        self.export_button.configure(state="normal")
        if self.dry_run_callback is not None:
            self.dry_run_button.configure(state="normal")
        if self.press_cycle_callback is not None:
            self.press_cycle_button.configure(state="normal")
        if self.pump_cycle_callback is not None:
            self.pump_cycle_button.configure(state="normal")
        if self.full_cv_callback is not None:
            self.full_cv_button.configure(state="normal")

    def start_dry_run(self):
        if self.points and self.dry_run_callback is not None:
            self.dry_run_callback(self.points, "Rectangular path")

    def start_press_cycle(self):
        if self.points and self.press_cycle_callback is not None:
            self.press_cycle_callback(self.points, "Rectangular path")

    def start_pump_cycle(self):
        if self.points and self.pump_cycle_callback is not None:
            self.pump_cycle_callback(self.points, "Rectangular path")

    def start_full_cv(self):
        if self.points and self.full_cv_callback is not None:
            self.full_cv_callback(self.points, "Rectangular path")

    def export_csv(self):
        if not self.points:
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export rectangular scan path",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile="rectangular_scan_path.csv",
        )
        if not path:
            return
        with open(str(Path(path)), "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("index", "x", "y"))
            writer.writeheader()
            writer.writerows(self.points)
        self.summary_var.set("Exported {} points to {}".format(len(self.points), path))
