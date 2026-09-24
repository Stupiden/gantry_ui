"""Milestone 13: automated XY move, force-limited press, and lift cycle."""

import tkinter as tk
from tkinter import messagebox, ttk

from xy_dry_run import XYDryRunWindow


class XYPressCycleWindow(XYDryRunWindow):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        start_guard, lock_controls
    ):
        XYDryRunWindow.__init__(
            self, parent, points, path_name, send_command, position_provider,
            start_guard, lock_controls
        )
        self.title("Automated XY + press cycle — no pump / Gamry")
        self.geometry("700x455")
        self.press_force_var = tk.DoubleVar(value=100.0)
        self.press_travel_var = tk.DoubleVar(value=20.0)
        self.press_speed_var = tk.DoubleVar(value=0.25)
        self.lift_distance_var = tk.DoubleVar(value=0.0)
        self.lift_speed_var = tk.DoubleVar(value=2.0)
        self.press_widgets = []
        self.press_state = "idle"
        self._build_press_controls()

    def _build_press_controls(self):
        frame = self.start_button.master
        self.start_button.grid_configure(row=10)
        self.stop_button.grid_configure(row=10)
        specs = (
            ("Force threshold (g)", self.press_force_var),
            ("Max travel (mm)", self.press_travel_var),
            ("Press speed (mm/s)", self.press_speed_var),
            ("Lift mm (0=press depth)", self.lift_distance_var),
            ("Lift speed (mm/s)", self.lift_speed_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = 6 + (index // 3) * 2
            column = index % 3
            ttk.Label(frame, text=label).grid(
                row=row, column=column, sticky="w", padx=5, pady=(8, 3)
            )
            entry = ttk.Entry(frame, textvariable=variable, width=9)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5, pady=(0, 4))
            self.press_widgets.append(entry)
        self.start_button.configure(text="START PRESS CYCLE")

    def _press_parameters(self):
        try:
            force = float(self.press_force_var.get())
            travel = float(self.press_travel_var.get())
            speed = float(self.press_speed_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("Press parameters must be numbers")
        if not 0.0 < force <= 5000.0:
            raise ValueError("Force threshold must be 0–5000 g")
        if not 0.0 < travel <= 50.0:
            raise ValueError("Maximum travel must be 0–50 mm")
        if not 0.05 <= speed <= 0.50:
            raise ValueError("Press speed must be 0.05–0.50 mm/s")
        lift_distance = float(self.lift_distance_var.get())
        if not 0.0 <= lift_distance <= 50.0:
            raise ValueError("Lift distance must be 0–50 mm")
        lift_speed = float(self.lift_speed_var.get())
        if not 0.05 <= lift_speed <= 5.0:
            raise ValueError("Lift speed must be 0.05–5.0 mm/s")
        return force, travel, speed, lift_distance, lift_speed

    def start(self):
        try:
            (
                self.press_force,
                self.press_travel,
                self.press_speed,
                self.lift_distance,
                self.lift_speed,
            ) = self._press_parameters()
        except ValueError as error:
            messagebox.showerror("Press cycle", str(error), parent=self)
            return
        self.press_state = "idle"
        XYDryRunWindow.start(self)
        if self.running:
            for widget in self.press_widgets:
                widget.configure(state="disabled")

    def _arrived(self):
        if not self.running:
            return
        point = self._current_target()
        self.status_var.set(
            "POINT {} REACHED — settling {:.3f} s before press".format(
                point["index"], self.dwell_seconds
            )
        )
        self.after_id = self.after(int(self.dwell_seconds * 1000), self._start_press)

    def _start_press(self):
        self.after_id = None
        if not self.running:
            return
        point = self._current_target()
        self.press_state = "pressing"
        self.status_var.set("PRESSING at point {}".format(point["index"]))
        if not self.send_command(
            "PRESS {:.2f} {:.3f} {:.3f}".format(
                self.press_force, self.press_travel, self.press_speed
            )
        ):
            self._finish("FAILED — press command was not sent")

    def handle_controller_line(self, line):
        if not self.running:
            return
        if line.startswith("EVENT,PRESS_FORCE_STOP") and self.press_state == "pressing":
            self.press_state = "lifting"
            self.status_var.set("FORCE REACHED — lifting")
            lift_command = "LIFT {:.3f} {:.3f}".format(
                self.lift_distance, self.lift_speed
            )
            if not self.send_command(lift_command):
                self._finish("FAILED — lift command was not sent")
            return
        if line == "EVENT,LIFT_DONE" and self.press_state == "lifting":
            self.press_state = "idle"
            self._complete_pressed_point()
            return
        if line.startswith("EVENT,PRESS_MAX_TRAVEL_STOP"):
            self._finish("ABORTED — maximum press travel reached; use manual LIFT")
            return
        if line.startswith("EVENT,PRESS_LIMIT_STOP"):
            self._finish("ABORTED — Z limit during press; inspect before moving")
            return
        XYDryRunWindow.handle_controller_line(self, line)

    def _complete_pressed_point(self):
        point = self._current_target()
        self.point_offset += 1
        self.completed_var.set("{} / {}".format(self.point_offset, len(self.points)))
        if self.point_offset >= len(self.points):
            self._finish("COMPLETE — all XY press/lift points finished")
            return
        self.status_var.set("POINT {} COMPLETE — moving to next point".format(point["index"]))
        self.after_id = self.after(100, self._move_x)

    def _finish(self, message):
        self.press_state = "idle"
        XYDryRunWindow._finish(self, message)
        if hasattr(self, "press_widgets"):
            for widget in self.press_widgets:
                widget.configure(state="normal")
