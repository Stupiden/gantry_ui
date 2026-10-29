"""Milestone 14: automated XY, press, timed pump, and lift cycle."""

import time
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
        self.geometry("900x620")
        self.pump_enabled_vars = [
            tk.BooleanVar(value=True),
            tk.BooleanVar(value=True),
        ]
        self.pump_rpm_vars = [tk.DoubleVar(value=1.2), tk.DoubleVar(value=1.0)]
        self.pump_duration_vars = [tk.DoubleVar(value=1.0), tk.DoubleVar(value=2.0)]
        self.pump_start_interval_var = tk.DoubleVar(value=0.5)
        self.pressed_stabilization_var = tk.DoubleVar(value=10.0)
        self.pre_lift_wait_var = tk.DoubleVar(value=1.0)
        self.suction_enabled_var = tk.BooleanVar(value=True)
        self.suction_with_lift_var = tk.BooleanVar(value=True)
        self.suction_lift_delay_var = tk.DoubleVar(value=1.0)
        self.suction_rpm_vars = [tk.DoubleVar(value=-2.0), tk.DoubleVar(value=2.0)]
        self.suction_duration_vars = [tk.DoubleVar(value=5.0), tk.DoubleVar(value=5.0)]
        self.suction_configs = []
        self.pump_widgets = []
        self.running_pumps = set()
        self.pump_after_ids = {}
        self.xy_move_axis = None
        self.xy_move_target = None
        self.xy_move_deadline = None
        self.xy_move_poll_id = None
        self._build_pump_controls()

    def _build_pump_controls(self):
        frame = self.start_button.master
        self.start_button.grid_configure(row=16, columnspan=1); self.restart_button.grid_configure(row=16)
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
        ttk.Label(frame, text="Pump start interval (s; +IN / -OUT)").grid(
            row=15, column=0, columnspan=2, sticky="w", padx=5, pady=(5, 3)
        )
        interval_entry = ttk.Entry(
            frame, textvariable=self.pump_start_interval_var, width=9
        )
        interval_entry.grid(row=15, column=2, sticky="w", padx=5, pady=(5, 3))
        self.pump_widgets.append(interval_entry)
        # Keep suction controls in their own grid so all five fields stay visible.
        suction_frame = ttk.Frame(frame)
        suction_frame.grid(row=11, column=4, rowspan=5, columnspan=2, sticky="nw", padx=5)
        suction_header = ttk.Frame(suction_frame)
        suction_header.grid(row=0, column=0, columnspan=2, sticky="w")
        suction_enabled = ttk.Checkbutton(
            suction_header, text="Suck", variable=self.suction_enabled_var
        )
        suction_enabled.pack(side="left")
        self.pump_widgets.append(suction_enabled)
        for pump_id, name in enumerate(("IN", "OUT")):
            rpm_row = 1 + pump_id * 2
            ttk.Label(suction_frame, text="{} signed RPM".format(name)).grid(
                row=rpm_row, column=0, sticky="w", padx=5
            )
            rpm_entry = ttk.Entry(
                suction_frame, textvariable=self.suction_rpm_vars[pump_id], width=8
            )
            rpm_entry.grid(row=rpm_row, column=1, sticky="w", padx=5)
            ttk.Label(suction_frame, text="{} duration s".format(name)).grid(
                row=rpm_row + 1, column=0, sticky="w", padx=5
            )
            duration_entry = ttk.Entry(
                suction_frame, textvariable=self.suction_duration_vars[pump_id], width=8
            )
            duration_entry.grid(row=rpm_row + 1, column=1, sticky="w", padx=5)
            self.pump_widgets.extend((rpm_entry, duration_entry))
        simultaneous = ttk.Checkbutton(
            suction_header, text="With lift", variable=self.suction_with_lift_var
        )
        simultaneous.pack(side="left", padx=(4, 0))
        self.pump_widgets.append(simultaneous)
        ttk.Label(suction_frame, text="Suck before lift (s)").grid(
            row=5, column=0, sticky="w", padx=5, pady=(5, 3)
        )
        delay_entry = ttk.Entry(
            suction_frame, textvariable=self.suction_lift_delay_var, width=8
        )
        delay_entry.grid(row=5, column=1, sticky="w", padx=5, pady=(5, 3))
        self.pump_widgets.append(delay_entry)
        self.start_button.configure(text="START PUMP CYCLE")

    def _suction_parameters(self):
        if not self.suction_enabled_var.get():
            return []
        configs = []
        for pump_id, name in enumerate(("IN", "OUT")):
            try:
                duration = float(self.suction_duration_vars[pump_id].get())
            except (tk.TclError, ValueError):
                raise ValueError("Suction {} duration must be a number".format(name))
            if not 0.0 <= duration <= 3600.0:
                raise ValueError("Suction {} duration must be 0–3600 s".format(name))
            if duration == 0.0:
                continue
            try:
                rpm = float(self.suction_rpm_vars[pump_id].get())
            except (tk.TclError, ValueError):
                raise ValueError("Suction {} RPM must be a number".format(name))
            if rpm == 0.0:
                continue
            if not 0.1 <= abs(rpm) <= 20.0:
                raise ValueError("Suction {} signed RPM must be 0 or have magnitude 0.1–20".format(name))
            configs.append((pump_id, rpm, duration))
        return configs

    def _suction_lift_delay(self, configs):
        if not configs or not self.suction_with_lift_var.get():
            return 0.0
        try:
            delay = float(self.suction_lift_delay_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("Suck-before-lift time must be a number")
        if not 0.0 <= delay < max(item[2] for item in configs):
            raise ValueError("Suck-before-lift time must be at least 0 and shorter than the longest suction duration")
        return delay

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
        try:
            start_interval = float(self.pump_start_interval_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("Pump start interval must be a number")
        if not -3600.0 <= start_interval <= 3600.0:
            raise ValueError("Pump start interval must be -3600–3600 s")
        return configs, stabilization, pre_lift_wait, start_interval

    def _manifest_settings(self, measurement):
        return {
            "measurement": measurement,
            "motion": {"speed_mm_s": float(self.speed_var.get()),
                       "wait_at_point_s": float(self.dwell_var.get())},
            "press": {"force_threshold_g": self.press_force,
                      "max_travel_mm": self.press_travel,
                      "speed_mm_s": self.press_speed,
                      "lift_distance_mm": self.lift_distance,
                      "lift_speed_mm_s": self.lift_speed},
            "press_pump": {
                "enabled": self.press_pump_enabled,
                "duration_s": self.press_pump_duration,
                "in_rpm": self.press_pump_speeds[0],
                "out_rpm": self.press_pump_speeds[1],
            },
            "pumps": [{"pump_id": item[0], "speed_rpm": item[1],
                       "post_measurement_s": item[2]} for item in self.pump_configs],
            "pressed_stabilization_s": self.pressed_stabilization,
            "pre_lift_wait_s": self.pre_lift_wait,
            "pump_start_interval_s": self.pump_start_interval,
            "suction": {
                "enabled": bool(self.suction_enabled_var.get()),
                "with_lift": bool(self.suction_with_lift_var.get()),
                "lift_delay_s": self.suction_lift_delay,
                "pumps": [
                    {"pump_id": pump_id, "signed_rpm": rpm, "duration_s": duration}
                    for pump_id, rpm, duration in self.suction_configs
                ],
            },
        }

    def start(self):
        if self.running:
            return
        try:
            (
                self.pump_configs,
                self.pressed_stabilization,
                self.pre_lift_wait,
                self.pump_start_interval,
            ) = self._pump_parameters()
            self.suction_configs = self._suction_parameters()
            self.suction_lift_delay = self._suction_lift_delay(self.suction_configs)
        except ValueError as error:
            messagebox.showerror("Pump cycle", str(error), parent=self)
            return
        self.running_pumps.clear()
        self.pump_after_ids.clear()
        self._cancel_xy_move_poll()
        self.xy_move_axis = None
        XYPressCycleWindow.start(self)
        if self.running:
            for widget in self.pump_widgets:
                widget.configure(state="disabled")

    def handle_controller_line(self, line):
        if not self.running:
            return
        if self.press_state in ("suction", "suction_lifting") and line.startswith("ERROR,BAD_PUMP"):
            self._finish("ABORTED — suction command rejected; use manual LIFT")
            return
        if (self.press_state == "suction_lifting" and
                getattr(self, "suction_lift_started", False) and
                line == "EVENT,LIFT_DONE"):
            self.suction_lift_done = True
            if not self.running_pumps:
                self.press_state = "idle"
                self._complete_pressed_point()
            else:
                self.status_var.set("LIFT DONE — waiting for suction to finish")
            return
        if line == "EVENT,MOVE_DONE,{}".format(self.xy_move_axis):
            self._cancel_xy_move_poll()
            self.xy_move_axis = None
            self.xy_move_target = None
            self.xy_move_deadline = None
        if line.startswith("EVENT,PRESS_FORCE_STOP") and self.press_state == "pressing":
            self._stop_press_pumps()
            point = self._current_target()
            self.status_var.set(
                "FORCE REACHED — starting pumps at point {}".format(point["index"])
            )
            self._start_pumps()
            return
        XYPressCycleWindow.handle_controller_line(self, line)

    def _send_axis_move(self, axis, target):
        current = self.position_provider()[0 if axis == "X" else 1]
        sent = XYPressCycleWindow._send_axis_move(self, axis, target)
        if sent and self.running:
            self.xy_move_axis = axis
            self.xy_move_target = float(target)
            expected = abs(float(target) - current) / max(float(self.speed_var.get()), 0.05)
            self.xy_move_deadline = time.monotonic() + max(5.0, expected * 2.0 + 5.0)
            self._schedule_xy_move_poll()
        return sent

    def _cancel_xy_move_poll(self):
        if self.xy_move_poll_id is not None:
            try:
                self.after_cancel(self.xy_move_poll_id)
            except Exception:
                pass
            self.xy_move_poll_id = None

    def _schedule_xy_move_poll(self):
        self._cancel_xy_move_poll()
        self.xy_move_poll_id = self.after(100, self._poll_xy_move)

    def _poll_xy_move(self):
        self.xy_move_poll_id = None
        if not self.running or self.xy_move_axis not in ("X", "Y"):
            return
        if time.monotonic() >= self.xy_move_deadline:
            axis = self.xy_move_axis
            self.send_command("MOTION_STOP", require_ready=False)
            self._finish("ABORTED — {} move did not reach its target".format(axis))
            return
        self.send_command("STATUS", require_ready=False)
        self._schedule_xy_move_poll()

    def handle_status(self, values):
        XYPressCycleWindow.handle_status(self, values)
        axis = self.xy_move_axis
        if not self.running or axis not in ("X", "Y"):
            return
        try:
            current = float(values["S{}".format(axis)])
            stopped = values["{}MOVING".format(axis)] == "0"
        except (KeyError, ValueError):
            return
        if stopped and abs(self.xy_move_target - current) <= 0.004:
            self._cancel_xy_move_poll()
            self.xy_move_axis = None
            self.xy_move_target = None
            self.xy_move_deadline = None
            if self.awaiting_axis == axis:
                self.awaiting_axis = None
                self.after_id = self.after(
                    20, self._move_y if axis == "X" else self._arrived
                )

    def _start_pumps(self):
        self.after_id = None
        if not self.running:
            return
        self.press_state = "pumping"
        point = self._current_target()
        self.status_var.set("STABILIZED — pumping at point {}".format(point["index"]))
        self._start_selected_pumps(self._basic_pumps_started)

    def _basic_pumps_started(self):
        if not self.running:
            return
        self.press_state = "stabilizing_with_pumps"
        self.status_var.set(
            "PUMPS ON — refreshing electrolyte for {:.3f} s".format(
                self.pressed_stabilization
            )
        )
        self.after_id = self.after(
            int(self.pressed_stabilization * 1000), self._start_pump_duration
        )

    def _start_selected_pumps(self, completed_callback):
        configs = list(self.pump_configs)
        if self.pump_start_interval < 0:
            configs.sort(key=lambda item: 0 if item[0] == 1 else 1)
        else:
            configs.sort(key=lambda item: 0 if item[0] == 0 else 1)

        def start_next(index):
            self.after_id = None
            if not self.running:
                return
            pump_id, rpm, unused_duration = configs[index]
            if not self.send_command("PUMP SPEED {} {:.2f}".format(pump_id, rpm)):
                self._finish("FAILED — pump speed command was not sent")
                return
            if not self.send_command("PUMP START {}".format(pump_id)):
                self._finish("FAILED — pump start command was not sent")
                return
            self.running_pumps.add(pump_id)
            if index + 1 >= len(configs):
                completed_callback()
                return
            delay_ms = int(abs(self.pump_start_interval) * 1000)
            self.status_var.set(
                "{} ON — waiting {:.3f} s before {}".format(
                    "IN" if pump_id == 0 else "OUT",
                    abs(self.pump_start_interval),
                    "IN" if configs[index + 1][0] == 0 else "OUT",
                )
            )
            self.after_id = self.after(delay_ms, lambda: start_next(index + 1))

        start_next(0)

    def _start_pump_duration(self):
        self.after_id = None
        if not self.running:
            return
        self.press_state = "pumping"
        for pump_id, unused_rpm, duration in self.pump_configs:
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _hold_with_pumps_before(self, callback, measurement_name):
        self.press_state = "stabilizing_with_pumps"
        self.status_var.set(
            "PUMPS ON — refreshing electrolyte for {:.3f} s before {}".format(
                self.pressed_stabilization, measurement_name
            )
        )
        self.after_id = self.after(
            int(self.pressed_stabilization * 1000), callback
        )

    def _restore_measurement_pumps(self):
        """Restart selected pumps after a blocking Gamry setup call.

        The Mega watchdog intentionally stops pumps if Tk cannot send a
        keepalive for 750 ms.  Gamry CV/CA initialization can exceed that
        interval, so restore the selected pumps immediately before the
        non-blocking measurement loop continues.
        """
        for pump_id, rpm, unused_duration in self.pump_configs:
            if not self.send_command("PUMP SPEED {} {:.2f}".format(pump_id, rpm)):
                raise RuntimeError("pump speed command was not sent")
            if not self.send_command("PUMP START {}".format(pump_id)):
                raise RuntimeError("pump start command was not sent")
            self.running_pumps.add(pump_id)

    def _start_press(self):
        manager = getattr(self, "data_manager", None)
        if manager is not None and manager.active_index is None:
            manager.begin_point(self._current_target())
        XYPressCycleWindow._start_press(self)

    def _pump_time_complete(self, pump_id):
        self.pump_after_ids.pop(pump_id, None)
        if not self.running:
            return
        self._stop_pump(pump_id)
        if self.press_state == "suction_lifting":
            if not self.running_pumps:
                if self.suction_lift_done:
                    self.press_state = "idle"
                    self._complete_pressed_point()
                else:
                    self.status_var.set("SUCTION DONE — waiting for lift to finish")
            return
        if self.running_pumps:
            names = ", ".join("IN" if item == 0 else "OUT" for item in sorted(self.running_pumps))
            self.status_var.set("WAITING FOR PUMP {}".format(names))
            return
        if self.press_state != "suction" and getattr(self, "suction_configs", ()):
            if self._suction_with_lift():
                self._start_pre_lift_wait()
            else:
                self._start_suction()
            return
        self._start_pre_lift_wait()

    def _suction_with_lift(self):
        variable = self.__dict__.get("suction_with_lift_var")
        return bool(variable is not None and variable.get())

    def _start_suction(self, with_lift=False):
        self.press_state = "suction_lifting" if with_lift else "suction"
        self.suction_lift_done = False
        self.suction_lift_started = False
        self.status_var.set("SUCTION + LIFT" if with_lift else "SUCTION BEFORE LIFT — signed RPM controls direction")
        for pump_id, rpm, duration in self.suction_configs:
            if not self.send_command("PUMP RUN {} {:.2f}".format(pump_id, rpm)):
                self._finish("FAILED — suction command was not sent; use manual LIFT")
                return
            self.running_pumps.add(pump_id)
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _start_pre_lift_wait(self):
        self.press_state = "pre_lift_wait"
        self.status_var.set(
            "PUMPS STOPPED — holding {:.3f} s before lift".format(
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
        if self._suction_with_lift() and self.suction_configs:
            self._start_suction(with_lift=True)
            if not self.running:
                return
            self.status_var.set(
                "SUCTION ON — lifting after {:.3f} s".format(self.suction_lift_delay)
            )
            self.after_id = self.after(
                int(self.suction_lift_delay * 1000), self._start_lift_during_suction
            )
            return
        else:
            self.press_state = "lifting"
            self.status_var.set("PRE-LIFT WAIT COMPLETE — lifting")
        self._send_lift_command()

    def _start_lift_during_suction(self):
        self.after_id = None
        if not self.running or self.press_state != "suction_lifting":
            return
        self.suction_lift_started = True
        self.status_var.set("LIFTING — remaining suction continues")
        self._send_lift_command()

    def _send_lift_command(self):
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
        self._cancel_xy_move_poll()
        self.xy_move_axis = None
        self.xy_move_target = None
        self.xy_move_deadline = None
        manager = getattr(self, "data_manager", None)
        if manager is not None and manager.active_index is not None:
            manager.fail_active(message)
        if hasattr(self, "running_pumps"):
            self._stop_all_selected_pumps()
        XYPressCycleWindow._finish(self, message)
        if hasattr(self, "pump_widgets"):
            for widget in self.pump_widgets:
                widget.configure(state="normal")

    def _complete_pressed_point(self):
        manager = getattr(self, "data_manager", None)
        if manager is not None and manager.active_index is not None:
            manager.complete_point(self._current_target()["index"])
        XYPressCycleWindow._complete_pressed_point(self)
