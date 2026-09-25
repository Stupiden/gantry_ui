"""Read-only Milestone 17 checkpoint/recovery preview."""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D


VALID_POINT_STATUSES = {"pending", "running", "completed", "failed"}
STATUS_COLORS = {
    "completed": "#2ca02c",
    "failed": "#d62728",
    "running": "#ff8c00",
    "pending": "#8c8c8c",
}


def load_experiment_preview(manifest_path):
    """Load and validate an experiment manifest without modifying it."""
    path = Path(manifest_path)
    with open(str(path), "r", encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported experiment schema version")
    for name in ("method", "path_name", "settings", "points"):
        if name not in data:
            raise ValueError("Experiment JSON is missing '{}'".format(name))
    if not isinstance(data["points"], list) or not data["points"]:
        raise ValueError("Experiment JSON contains no points")

    seen = set()
    counts = {status: 0 for status in VALID_POINT_STATUSES}
    next_point = None
    for point in data["points"]:
        for name in ("index", "x", "y", "status", "attempts"):
            if name not in point:
                raise ValueError("A point is missing '{}'".format(name))
        index = int(point["index"])
        if index in seen:
            raise ValueError("Duplicate point index {}".format(index))
        seen.add(index)
        float(point["x"])
        float(point["y"])
        status = point["status"]
        if status not in VALID_POINT_STATUSES:
            raise ValueError("Point {} has invalid status '{}'".format(index, status))
        if not isinstance(point["attempts"], list):
            raise ValueError("Point {} attempts must be a list".format(index))
        counts[status] += 1
        if next_point is None and status != "completed":
            next_point = point

    return {
        "path": path,
        "data": data,
        "counts": counts,
        "next_point": next_point,
    }


class ExperimentResumePreview(tk.Toplevel):
    def __init__(self, parent, preview, resume_callback=None):
        tk.Toplevel.__init__(self, parent)
        self.title("Experiment recovery preview — read only")
        self.geometry("980x760")
        self.minsize(760, 600)
        self.transient(parent)
        self.preview = preview
        self.resume_callback = resume_callback
        self.selected_indices = set()
        self.selection_var = tk.StringVar(value="Selected for future remeasure: none")
        self.hover_annotation = None
        self.point_scatter = None
        self.selection_scatter = None
        self._build()

    def _build(self):
        data = self.preview["data"]
        counts = self.preview["counts"]
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        summary = ttk.LabelFrame(frame, text="Checkpoint summary", padding=7)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        summary.columnconfigure(1, weight=1)

        rows = (
            ("Manifest", str(self.preview["path"])),
            ("Method", str(data["method"])),
            ("Path", str(data["path_name"])),
            ("Created", str(data.get("created_at", "—"))),
            ("Points", str(len(data["points"]))),
            ("Completed", str(counts["completed"])),
            ("Failed", str(counts["failed"])),
            ("Interrupted/running", str(counts["running"])),
            ("Pending", str(counts["pending"])),
        )
        for row, (label, value) in enumerate(rows):
            ttk.Label(summary, text=label + ":", font=("Segoe UI", 9, "bold")).grid(
                row=row, column=0, sticky="nw", padx=(0, 10), pady=2
            )
            ttk.Label(summary, text=value, wraplength=780).grid(
                row=row, column=1, sticky="nw", pady=2
            )

        next_point = self.preview["next_point"]
        if next_point is None:
            next_text = "None — all points are completed"
        else:
            next_text = "Index {}  X={:.3f}  Y={:.3f}  previous status={}".format(
                int(next_point["index"]), float(next_point["x"]),
                float(next_point["y"]), next_point["status"]
            )
        ttk.Label(summary, text="Next resumable point:", font=("Segoe UI", 9, "bold")).grid(
            row=9, column=0, sticky="nw", padx=(0, 10), pady=(4, 0)
        )
        ttk.Label(summary, text=next_text, wraplength=780).grid(
            row=9, column=1, sticky="nw", pady=(4, 0)
        )

        plot_frame = ttk.Frame(frame)
        plot_frame.grid(row=1, column=0, sticky="nsew")
        plot_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)
        figure = Figure(figsize=(7.5, 4.8), dpi=100)
        self.axes = figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(figure, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.canvas.mpl_connect("motion_notify_event", self._on_hover)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self._draw_points()

        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        controls.columnconfigure(0, weight=1)
        ttk.Label(controls, textvariable=self.selection_var).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            controls,
            text="Preview only: hover for details; click points to select. No machine/Gamry action.",
            foreground="#9a6700",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 5))
        if self.resume_callback is not None and data["method"] == "EIS_GetRu_compensated_CV":
            ttk.Button(
                controls, text="RESUME UNFINISHED", command=lambda: self._resume("unfinished"),
                state="normal" if next_point is not None else "disabled",
            ).grid(row=2, column=0, sticky="ew", padx=4)
            ttk.Button(
                controls, text="RESTART ALL", command=lambda: self._resume("all")
            ).grid(row=2, column=1, sticky="ew", padx=4)
            self.selected_button = ttk.Button(
                controls, text="REMEASURE SELECTED", command=lambda: self._resume("selected"),
                state="disabled",
            )
            self.selected_button.grid(row=2, column=2, sticky="ew", padx=4)
            close_column = 3
        else:
            close_column = 1
        ttk.Button(controls, text="CLOSE", command=self.destroy).grid(
            row=2, column=close_column, sticky="ew", padx=5
        )

    def _draw_points(self):
        points = self.preview["data"]["points"]
        x_values = [float(point["x"]) for point in points]
        y_values = [float(point["y"]) for point in points]
        colors = [STATUS_COLORS[point["status"]] for point in points]
        self.axes.clear()
        self.axes.plot(x_values, y_values, color="#b0b0b0", linewidth=0.8, zorder=1)
        self.point_scatter = self.axes.scatter(
            x_values, y_values, c=colors, s=48, edgecolors="black", linewidths=0.4,
            picker=6, zorder=2,
        )
        self.selection_scatter = self.axes.scatter(
            [], [], s=120, facecolors="none", edgecolors="#0066ff", linewidths=2.0, zorder=3
        )
        self.axes.set_xlabel("Scan X / mm")
        self.axes.set_ylabel("Scan Y / mm — operator view")
        self.axes.set_aspect("equal", adjustable="datalim")
        self.axes.grid(True, alpha=0.35)
        self.axes.invert_yaxis()
        self.axes.legend(handles=[
            Line2D([], [], marker="o", linestyle="", color=STATUS_COLORS[status], label=status)
            for status in ("completed", "failed", "running", "pending")
        ], loc="best", fontsize=8)
        self.hover_annotation = self.axes.annotate(
            "", xy=(0.0, 0.0), xytext=(8, 8), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="white", ec="0.4", alpha=0.95), zorder=10,
        )
        self.hover_annotation.set_visible(False)
        self.canvas.draw_idle()

    def _point_details(self, offset):
        point = self.preview["data"]["points"][offset]
        reason = ""
        if point["attempts"]:
            reason = str(point["attempts"][-1].get("reason", ""))
        text = "Index {}\nX={:.3f}, Y={:.3f}\nStatus: {}\nAttempts: {}".format(
            int(point["index"]), float(point["x"]), float(point["y"]),
            point["status"], len(point["attempts"]),
        )
        if reason:
            text += "\nLast reason: {}".format(reason)
        return point, text

    def _on_hover(self, event):
        if event.inaxes != self.axes or self.point_scatter is None:
            if self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False); self.canvas.draw_idle()
            return
        contains, details = self.point_scatter.contains(event)
        if not contains or not details.get("ind"):
            if self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False); self.canvas.draw_idle()
            return
        offset = int(details["ind"][0])
        point, text = self._point_details(offset)
        self.hover_annotation.xy = (float(point["x"]), float(point["y"]))
        self.hover_annotation.set_text(text)
        self.hover_annotation.set_visible(True)
        self.canvas.draw_idle()

    def _on_click(self, event):
        if event.inaxes != self.axes or self.point_scatter is None:
            return
        contains, details = self.point_scatter.contains(event)
        if not contains or not details.get("ind"):
            return
        point = self.preview["data"]["points"][int(details["ind"][0])]
        index = int(point["index"])
        if index in self.selected_indices:
            self.selected_indices.remove(index)
        else:
            self.selected_indices.add(index)
        selected = [
            point for point in self.preview["data"]["points"]
            if int(point["index"]) in self.selected_indices
        ]
        offsets = [(float(point["x"]), float(point["y"])) for point in selected]
        self.selection_scatter.set_offsets(offsets or [[float("nan"), float("nan")]])
        if self.selected_indices:
            self.selection_var.set(
                "Selected for future remeasure: {}".format(
                    ", ".join(str(item) for item in sorted(self.selected_indices))
                )
            )
        else:
            self.selection_var.set("Selected for future remeasure: none")
        if hasattr(self, "selected_button"):
            self.selected_button.configure(state="normal" if self.selected_indices else "disabled")
        self.canvas.draw_idle()

    def _resume(self, mode):
        if self.resume_callback(self.preview, mode, set(self.selected_indices)):
            self.destroy()


def choose_and_show_experiment(parent, resume_callback=None):
    selected = filedialog.askopenfilename(
        parent=parent,
        title="Choose experiment.json",
        filetypes=(("Experiment JSON", "experiment.json"), ("JSON files", "*.json")),
    )
    if not selected:
        return None
    try:
        preview = load_experiment_preview(selected)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        messagebox.showerror("Experiment recovery preview", str(error), parent=parent)
        return None
    return ExperimentResumePreview(parent, preview, resume_callback)
