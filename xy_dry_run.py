"""Milestone 12: execute an XY-only scan-path dry run."""

import tkinter as tk
from tkinter import messagebox, ttk


POSITION_TOLERANCE_MM = 0.002


class XYDryRunWindow(tk.Toplevel):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        start_guard, lock_controls
    ):
        tk.Toplevel.__init__(self, parent)
        self.title("Automated XY dry run — no Z / pump / Gamry")
        self.geometry("540x310")
        self.resizable(False, False)
        self.points = [dict(point) for point in points]
        self.path_name = path_name
        self.send_command = send_command
        self.position_provider = position_provider
        self.start_guard = start_guard
        self.lock_controls = lock_controls
        self.speed_var = tk.DoubleVar(value=1.0)
        self.dwell_var = tk.DoubleVar(value=1.0)
        self.current_point_var = tk.StringVar(value="—")
        self.completed_var = tk.StringVar(value="0 / {}".format(len(self.points)))
        self.xy_var = tk.StringVar(value="X —   Y —")
        self.status_var = tk.StringVar(value="READY — preview path loaded")
        self.running = False
        self.point_offset = 0
        self.awaiting_axis = None
        self.after_id = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close_window)

    def _build_ui(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Path").grid(row=0, column=0, sticky="e", padx=5, pady=4)
        ttk.Label(frame, text="{} ({} points)".format(self.path_name, len(self.points))).grid(
            row=0, column=1, columnspan=3, sticky="w", padx=5, pady=4
        )
        ttk.Label(frame, text="Speed (mm/s)").grid(row=1, column=0, sticky="e", padx=5, pady=4)
        self.speed_entry = ttk.Entry(frame, textvariable=self.speed_var, width=10)
        self.speed_entry.grid(row=1, column=1, sticky="w", padx=5, pady=4)
        ttk.Label(frame, text="Wait at point (s)").grid(row=1, column=2, sticky="e", padx=5, pady=4)
        self.dwell_entry = ttk.Entry(frame, textvariable=self.dwell_var, width=10)
        self.dwell_entry.grid(row=1, column=3, sticky="w", padx=5, pady=4)

        for row, (label, variable) in enumerate((
            ("Current point", self.current_point_var),
            ("Completed", self.completed_var),
            ("Current scan XY", self.xy_var),
        ), start=2):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="e", padx=5, pady=4)
            ttk.Label(frame, textvariable=variable, font=("Consolas", 11, "bold")).grid(
                row=row, column=1, columnspan=3, sticky="w", padx=5, pady=4
            )

        ttk.Label(frame, textvariable=self.status_var, wraplength=500).grid(
            row=5, column=0, columnspan=4, sticky="w", padx=5, pady=(8, 6)
        )
        self.start_button = ttk.Button(frame, text="START XY DRY RUN", command=self.start)
        self.start_button.grid(row=6, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        self.stop_button = ttk.Button(
            frame, text="STOP", command=self.stop, state="disabled"
        )
        self.stop_button.grid(row=6, column=2, columnspan=2, sticky="ew", padx=5, pady=5)

    def start(self):
        if self.running:
            return
        try:
            speed = float(self.speed_var.get())
            dwell = float(self.dwell_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("XY dry run", "Speed and wait must be numbers", parent=self)
            return
        if not 0.05 <= speed <= 5.0:
            messagebox.showerror("XY dry run", "Speed must be 0.05–5.0 mm/s", parent=self)
            return
        if not 0.0 <= dwell <= 60.0:
            messagebox.showerror("XY dry run", "Wait must be 0–60 seconds", parent=self)
            return
        allowed, reason = self.start_guard()
        if not allowed:
            messagebox.showerror("XY dry run", reason, parent=self)
            return
        if not self.points:
            return

        self.running = True
        self.point_offset = 0
        self.awaiting_axis = None
        self.dwell_seconds = dwell
        self.lock_controls(True)
        self.speed_entry.configure(state="disabled")
        self.dwell_entry.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        if not self.send_command("SPEED {:.3f}".format(speed)):
            self._finish("FAILED — could not send speed")
            return
        self.status_var.set("RUNNING — moving to point 1")
        self.after_id = self.after(100, self._move_x)

    def _current_target(self):
        return self.points[self.point_offset]

    def _send_axis_move(self, axis, target):
        current_x, current_y = self.position_provider()
        current = current_x if axis == "X" else current_y
        delta = float(target) - current
        if abs(delta) <= POSITION_TOLERANCE_MM:
            return False
        self.awaiting_axis = axis
        if not self.send_command("MOVE {} {:.3f}".format(axis, delta)):
            self._finish("FAILED — serial command was not sent")
        return True

    def _move_x(self):
        self.after_id = None
        if not self.running:
            return
        point = self._current_target()
        self.current_point_var.set(
            "{}  X={:.3f}  Y={:.3f}".format(point["index"], point["x"], point["y"])
        )
        self.status_var.set("RUNNING — point {}: moving X".format(point["index"]))
        if not self._send_axis_move("X", point["x"]):
            self._move_y()

    def _move_y(self):
        if not self.running:
            return
        point = self._current_target()
        self.status_var.set("RUNNING — point {}: moving Y".format(point["index"]))
        if not self._send_axis_move("Y", point["y"]):
            self._arrived()

    def _arrived(self):
        if not self.running:
            return
        point = self._current_target()
        self.point_offset += 1
        self.completed_var.set("{} / {}".format(self.point_offset, len(self.points)))
        if self.point_offset >= len(self.points):
            self._finish("COMPLETE — all XY points visited")
            return
        self.status_var.set(
            "WAITING {:.3f} s after point {}".format(self.dwell_seconds, point["index"])
        )
        self.after_id = self.after(int(self.dwell_seconds * 1000), self._move_x)

    def handle_controller_line(self, line):
        if not self.running:
            return
        if line == "EVENT,MOVE_DONE,X" and self.awaiting_axis == "X":
            self.awaiting_axis = None
            self.after_id = self.after(20, self._move_y)
        elif line == "EVENT,MOVE_DONE,Y" and self.awaiting_axis == "Y":
            self.awaiting_axis = None
            self.after_id = self.after(20, self._arrived)
        elif line.startswith("ERROR,") or "LIMIT_STOP" in line or "ALARM_STOP" in line:
            self._finish("ABORTED — {}".format(line))

    def handle_status(self, values):
        try:
            self.xy_var.set(
                "X {:.3f} mm   Y {:.3f} mm".format(float(values["SX"]), float(values["SY"]))
            )
        except (KeyError, ValueError):
            pass

    def stop(self):
        if self.running:
            self.send_command("MOTION_STOP")
            self._finish("STOPPED BY USER")

    def emergency_stopped(self):
        if self.running:
            self._finish("EMERGENCY STOPPED")

    def _finish(self, message):
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        was_running = self.running
        self.running = False
        self.awaiting_axis = None
        if was_running:
            self.lock_controls(False)
        self.speed_entry.configure(state="normal")
        self.dwell_entry.configure(state="normal")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set(message)

    def close_window(self):
        if self.running:
            self.stop()
        self.destroy()
