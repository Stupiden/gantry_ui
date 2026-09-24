"""Milestone 14: automated XY, press, timed pump, and lift cycle."""

import tkinter as tk
from tkinter import messagebox, ttk

from xy_press_cycle import XYPressCycleWindow


class XYPumpCycleWindow(XYPressCycleWindow):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        start_guard, lock_controls
    ):
        XYPressCycleWindow.__init__(
            self, parent, points, path_name, send_command, position_provider,
            start_guard, lock_controls
        )
        self.title("Automated XY + press + pump cycle — no Gamry")
        self.geometry("760x620")
        self.pump_enabled_vars = [
            tk.BooleanVar(value=True),
            tk.BooleanVar(value=False),
        ]
        self.pump_rpm_vars = [tk.DoubleVar(value=2.0), tk.DoubleVar(value=2.0)]
        self.pump_duration_vars = [tk.DoubleVar(value=2.0), tk.DoubleVar(value=2.0)]
        self.pressed_stabilization_var = tk.DoubleVar(value=1.0)
        self.pre_lift_wait_var = tk.DoubleVar(value=1.0)
        self.pump_widgets = []
        self.running_pumps = set()
        self.pump_after_ids = {}
        self._build_pump_controls()

    def _build_pump_controls(self):
        frame = self.start_button.master
        self.start_button.grid_configure(row=16)
        self.stop_button.grid_configure(row=16)
        ttk.Label(frame, text="Pump").grid(row=11, column=0, sticky="w", padx=5, pady=(8, 3))
        ttk.Label(frame, text="Enable").grid(row=11, column=1, sticky="w", padx=5, pady=(8, 3))
        ttk.Label(frame, text="Speed (rpm)").grid(row=11, column=2, sticky="w", padx=5, pady=(8, 3))
        ttk.Label(frame, text="Duration (s)").grid(row=11, column=3, sticky="w", padx=5, pady=(8, 3))
        for pump_id, pump_name in enumerate(("IN", "OUT")):
            row = 12 + pump_id
            ttk.Label(frame, text=pump_name).grid(row=row, column=0, sticky="w", padx=5, pady=3)
            enabled = ttk.Checkbutton(frame, variable=self.pump_enabled_vars[pump_id])
            enabled.grid(row=row, column=1, sticky="w", padx=5, pady=3)
            rpm_entry = ttk.Entry(frame, textvariable=self.pump_rpm_vars[pump_id], width=9)
            rpm_entry.grid(row=row, column=2, sticky="w", padx=5, pady=3)
            duration_entry = ttk.Entry(
                frame, textvariable=self.pump_duration_vars[pump_id], width=9
            )
            duration_entry.grid(row=row, column=3, sticky="w", padx=5, pady=3)
            self.pump_widgets.extend((enabled, rpm_entry, duration_entry))
        ttk.Label(frame, text="Pressed stabilization (s)").grid(
            row=14, column=0, sticky="w", padx=5, pady=(8, 3)
        )
        stabilization_entry = ttk.Entry(
            frame, textvariable=self.pressed_stabilization_var, width=9
        )
        stabilization_entry.grid(row=14, column=1, sticky="w", padx=5, pady=(8, 3))
        self.pump_widgets.append(stabilization_entry)
        ttk.Label(frame, text="Pre-lift wait (s)").grid(
            row=14, column=2, sticky="w", padx=5, pady=(8, 3)
        )
        pre_lift_entry = ttk.Entry(frame, textvariable=self.pre_lift_wait_var, width=9)
        pre_lift_entry.grid(row=14, column=3, sticky="w", padx=5, pady=(8, 3))
        self.pump_widgets.append(pre_lift_entry)
        self.start_button.configure(text="START PUMP CYCLE")

    def _pump_parameters(self):
        configs = []
        for pump_id, pump_name in enumerate(("IN", "OUT")):
            if not self.pump_enabled_vars[pump_id].get():
                continue
            try:
                rpm = float(self.pump_rpm_vars[pump_id].get())
                duration = float(self.pump_duration_vars[pump_id].get())
            except (tk.TclError, ValueError):
                raise ValueError("Pump {} speed and duration must be numbers".format(pump_name))
            if not 0.1 <= rpm <= 20.0:
                raise ValueError("Pump {} speed must be 0.1–20 rpm".format(pump_name))
            if not 0.0 < duration <= 3600.0:
                raise ValueError(
                    "Pump {} duration must be greater than 0 and at most 3600 s".format(
                        pump_name
                    )
                )
            configs.append((pump_id, rpm, duration))
        if not configs:
            raise ValueError("Enable at least one pump")
        try:
            stabilization = float(self.pressed_stabilization_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("Pressed stabilization time must be a number")
        if not 0.0 <= stabilization <= 3600.0:
            raise ValueError("Pressed stabilization time must be 0–3600 s")
        try:
            pre_lift_wait = float(self.pre_lift_wait_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("Pre-lift wait must be a number")
        if not 0.0 <= pre_lift_wait <= 3600.0:
            raise ValueError("Pre-lift wait must be 0–3600 s")
        return configs, stabilization, pre_lift_wait

    def start(self):
        try:
            (
                self.pump_configs,
                self.pressed_stabilization,
                self.pre_lift_wait,
            ) = self._pump_parameters()
        except ValueError as error:
            messagebox.showerror("Pump cycle", str(error), parent=self)
            return
        self.running_pumps.clear()
        self.pump_after_ids.clear()
        XYPressCycleWindow.start(self)
        if self.running:
            for widget in self.pump_widgets:
                widget.configure(state="disabled")

    def handle_controller_line(self, line):
        if not self.running:
            return
        if line.startswith("EVENT,PRESS_FORCE_STOP") and self.press_state == "pressing":
            self.press_state = "stabilizing"
            point = self._current_target()
            self.status_var.set(
                "FORCE REACHED — holding {:.3f} s at point {}".format(
                    self.pressed_stabilization, point["index"]
                )
            )
            self.after_id = self.after(
                int(self.pressed_stabilization * 1000), self._start_pumps
            )
            return
        XYPressCycleWindow.handle_controller_line(self, line)

    def _start_pumps(self):
        self.after_id = None
        if not self.running:
            return
        self.press_state = "pumping"
        point = self._current_target()
        self.status_var.set("STABILIZED — pumping at point {}".format(point["index"]))
        for pump_id, rpm, duration in self.pump_configs:
            if not self.send_command("PUMP SPEED {} {:.2f}".format(pump_id, rpm)):
                self._finish("FAILED — pump speed command was not sent")
                return
            if not self.send_command("PUMP START {}".format(pump_id)):
                self._finish("FAILED — pump start command was not sent")
                return
            self.running_pumps.add(pump_id)
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _pump_time_complete(self, pump_id):
        self.pump_after_ids.pop(pump_id, None)
        if not self.running:
            return
        self._stop_pump(pump_id)
        if self.running_pumps:
            names = ", ".join("IN" if item == 0 else "OUT" for item in sorted(self.running_pumps))
            self.status_var.set("WAITING FOR PUMP {}".format(names))
            return
        self.press_state = "pre_lift_wait"
        self.status_var.set(
            "PUMPS COMPLETE — holding {:.3f} s before lift".format(
                self.pre_lift_wait
            )
        )
        self.after_id = self.after(
            int(self.pre_lift_wait * 1000), self._start_lift_after_wait
        )

    def _start_lift_after_wait(self):
        self.after_id = None
        if not self.running:
            return
        self.press_state = "lifting"
        self.status_var.set("PRE-LIFT WAIT COMPLETE — lifting")
        lift_command = "LIFT {:.3f} {:.3f}".format(
            self.lift_distance, self.lift_speed
        )
        if not self.send_command(lift_command):
            self._finish("FAILED — lift command was not sent")

    def _stop_pump(self, pump_id):
        if pump_id in self.running_pumps:
            self.send_command("PUMP STOP {}".format(pump_id), require_ready=False)
            self.running_pumps.discard(pump_id)

    def _stop_all_selected_pumps(self):
        for after_id in list(self.pump_after_ids.values()):
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self.pump_after_ids.clear()
        for pump_id in list(self.running_pumps):
            self._stop_pump(pump_id)

    def stop(self):
        self._stop_all_selected_pumps()
        XYPressCycleWindow.stop(self)

    def emergency_stopped(self):
        self.running_pumps.clear()
        self.pump_after_ids.clear()
        XYPressCycleWindow.emergency_stopped(self)

    def _finish(self, message):
        if hasattr(self, "running_pumps"):
            self._stop_all_selected_pumps()
        XYPressCycleWindow._finish(self, message)
        if hasattr(self, "pump_widgets"):
            for widget in self.pump_widgets:
                widget.configure(state="normal")
