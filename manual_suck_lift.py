"""Small manual suction/overlapping-lift action for the main PRESS panel."""

import time
import tkinter as tk
from tkinter import ttk


def validate_suck_lift(in_rpm, in_seconds, out_rpm, out_seconds,
                       before_lift_seconds, lift_mm, lift_speed):
    configs = []
    for pump_id, rpm, duration in ((0, in_rpm, in_seconds),
                                   (1, out_rpm, out_seconds)):
        duration = float(duration)
        if not 0.0 <= duration <= 3600.0:
            raise ValueError("Suction duration must be 0–3600 s")
        if duration == 0.0:
            continue
        rpm = float(rpm)
        if rpm == 0.0:
            continue
        if not 0.1 <= abs(rpm) <= 20.0:
            raise ValueError("Active suction RPM magnitude must be 0.1–20")
        configs.append((pump_id, rpm, duration))
    delay = float(before_lift_seconds) if configs else 0.0
    distance, speed = float(lift_mm), float(lift_speed)
    if configs and not 0.0 <= delay < max(item[2] for item in configs):
        raise ValueError("Suck-before-lift must be shorter than suction duration")
    if not 0.0 <= distance <= 50.0:
        raise ValueError("Lift distance must be 0–50 mm")
    if not 0.05 <= speed <= 5.0:
        raise ValueError("Lift speed must be 0.05–5.0 mm/s")
    return configs, delay, distance, speed


class SuckLiftSequence:
    """Use the automation's PUMP RUN -> delay -> LIFT -> timed stop order."""

    def __init__(self, owner, send_command, on_status, on_done):
        self.owner = owner
        self.send_command = send_command
        self.on_status = on_status
        self.on_done = on_done
        self.running = False
        self.timers = {}
        self.configs = []
        self.requested_pumps = set()
        self.active_pumps = set()
        self.pending_stops = set()
        self.lift_started = False
        self.lift_done = False
        self.state = "idle"

    def _schedule(self, key, milliseconds, callback):
        self.timers[key] = self.owner.after(milliseconds, callback)

    def _cancel_timers(self):
        for after_id in self.timers.values():
            try:
                self.owner.after_cancel(after_id)
            except tk.TclError:
                pass
        self.timers.clear()

    def start(self, configs, delay, lift_mm, lift_speed):
        if self.running:
            return False
        self.configs = list(configs)
        self.delay = delay
        self.lift_mm = lift_mm
        self.lift_speed = lift_speed
        self.requested_pumps = {item[0] for item in configs}
        self.active_pumps.clear()
        self.pending_stops.clear()
        self.lift_started = False
        self.lift_done = False
        self.running = True
        self.state = "starting"
        self.start_deadline = time.monotonic() + 3.0
        self.deadline = time.monotonic() + max(
            8.0 + max((item[2] for item in configs), default=0.0),
            8.0 + delay + (lift_mm if lift_mm > 0 else 50.0) / lift_speed,
        )
        if not self.configs:
            self._start_lift()
            if self.running:
                self._schedule("poll", 100, self._poll)
            return self.running
        for pump_id, rpm, unused_duration in self.configs:
            if not self.send_command("PUMP RUN {} {:.2f}".format(pump_id, rpm)):
                self.abort("FAILED — suction command was not sent")
                return False
        self.on_status("WAITING — confirming suction pumps are running")
        self._schedule("poll", 100, self._poll)
        return True

    def _poll(self):
        self.timers.pop("poll", None)
        if not self.running:
            return
        if time.monotonic() >= self.deadline:
            self.abort("ABORTED — suction/lift timed out")
            return
        if self.state == "starting" and time.monotonic() >= self.start_deadline:
            self.abort("ABORTED — suction pumps were not confirmed running")
            return
        if not self.send_command("STATUS", require_ready=False):
            self.abort("ABORTED — controller status unavailable")
            return
        self._schedule("poll", 100, self._poll)

    def handle_status(self, values):
        if not self.running:
            return
        try:
            reported = {pump_id for pump_id in (0, 1)
                        if values["PUMP{}RUNNING".format(pump_id)] == "1"}
        except KeyError:
            return
        self.pending_stops -= {pump_id for pump_id in self.pending_stops
                               if pump_id not in reported}
        if self.state == "starting":
            if not self.requested_pumps <= reported:
                return
            self.state = "sucking"
            self.active_pumps = set(self.requested_pumps)
            for pump_id, unused_rpm, duration in self.configs:
                self._schedule("pump{}".format(pump_id), int(duration * 1000),
                               lambda selected=pump_id: self._stop_pump(selected))
            self._schedule("lift", int(self.delay * 1000), self._start_lift)
            self.on_status("SUCTION ON — lifting after {:.2f} s".format(self.delay))
            return
        if not self.active_pumps <= reported:
            self.abort("ABORTED — suction pump stopped unexpectedly")
            return
        self._maybe_complete()

    def _start_lift(self):
        self.timers.pop("lift", None)
        if not self.running:
            return
        if not self.send_command("LIFT {:.3f} {:.3f}".format(
                self.lift_mm, self.lift_speed)):
            self.abort("FAILED — lift command was not sent")
            return
        self.lift_started = True
        self.state = "lifting"
        self.on_status("LIFTING — remaining suction continues")

    def _stop_pump(self, pump_id):
        self.timers.pop("pump{}".format(pump_id), None)
        if not self.running or pump_id not in self.active_pumps:
            return
        self.active_pumps.remove(pump_id)
        self.pending_stops.add(pump_id)
        if not self.send_command("PUMP STOP {}".format(pump_id), require_ready=False):
            self.abort("FAILED — suction pump stop was not sent")
            return
        self.on_status("WAITING — lift and remaining suction to finish")

    def handle_controller_line(self, line):
        if not self.running:
            return
        if line == "EVENT,LIFT_DONE" and self.lift_started:
            self.lift_done = True
            self._maybe_complete()
        elif line.startswith("ERROR,") or line in (
                "EVENT,WATCHDOG_STOP", "EVENT,PUMP_WATCHDOG_STOP") or any(
                marker in line for marker in (
                    "LIMIT_STOP", "ALARM_STOP", "SENSOR_FAULT_STOP",
                    "FORCE_LIMIT_STOP")):
            self.abort("ABORTED — {}".format(line))

    def _maybe_complete(self):
        if self.running and self.lift_done and not self.active_pumps and not self.pending_stops:
            self._cancel_timers()
            self.running = False
            self.state = "idle"
            self.on_done("COMPLETE — suction and lift finished" if self.requested_pumps
                         else "COMPLETE — lift finished")

    def abort(self, message="STOPPED", send_stop=True):
        if not self.running:
            return
        self._cancel_timers()
        self.running = False
        if send_stop:
            for pump_id in self.requested_pumps:
                self.send_command("PUMP STOP {}".format(pump_id), require_ready=False)
            if self.lift_started:
                self.send_command("MOTION_STOP", require_ready=False)
        self.state = "idle"
        self.on_done(message)


class ManualSuckLiftWindow(tk.Toplevel):
    def __init__(self, parent, send_command, start_guard, lock_controls):
        tk.Toplevel.__init__(self, parent)
        self.title("Manual suck + lift")
        self.geometry("430x260")
        self.resizable(False, False)
        self.start_guard = start_guard
        self.lock_controls = lock_controls
        self.in_rpm_var = tk.DoubleVar(value=-2.0)
        self.in_duration_var = tk.DoubleVar(value=3.5)
        self.out_rpm_var = tk.DoubleVar(value=2.0)
        self.out_duration_var = tk.DoubleVar(value=5.0)
        self.delay_var = tk.DoubleVar(value=0.5)
        self.lift_mm_var = tk.DoubleVar(value=3.0)
        self.lift_speed_var = tk.DoubleVar(value=0.2)
        self.status_var = tk.StringVar(value="READY — first finish a manual PRESS")
        self.sequence = SuckLiftSequence(self, send_command,
                                         self.status_var.set, self._done)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close_window)

    @property
    def running(self):
        return self.sequence.running

    def _build_ui(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        specs = (
            ("IN signed RPM", self.in_rpm_var), ("IN duration s", self.in_duration_var),
            ("OUT signed RPM", self.out_rpm_var), ("OUT duration s", self.out_duration_var),
            ("Suck before lift s", self.delay_var), ("Lift mm (0=press depth)", self.lift_mm_var),
            ("Lift speed mm/s", self.lift_speed_var),
        )
        self.entries = []
        for index, (label, variable) in enumerate(specs):
            row, column = divmod(index, 2)
            ttk.Label(frame, text=label).grid(row=row * 2, column=column, sticky="w", padx=5)
            entry = ttk.Entry(frame, textvariable=variable, width=18)
            entry.grid(row=row * 2 + 1, column=column, sticky="w", padx=5, pady=(0, 4))
            self.entries.append(entry)
        self.start_button = ttk.Button(frame, text="START SUCK + LIFT", command=self.start)
        self.start_button.grid(row=8, column=0, sticky="ew", padx=5, pady=5)
        self.stop_button = ttk.Button(frame, text="STOP", command=self.stop, state="disabled")
        self.stop_button.grid(row=8, column=1, sticky="ew", padx=5, pady=5)
        ttk.Label(frame, textvariable=self.status_var, wraplength=405).grid(
            row=9, column=0, columnspan=2, sticky="w", padx=5)

    def start(self):
        if self.running:
            return
        allowed, reason = self.start_guard()
        if not allowed:
            self.status_var.set(reason)
            return
        try:
            configs, delay, distance, speed = validate_suck_lift(
                self.in_rpm_var.get(), self.in_duration_var.get(),
                self.out_rpm_var.get(), self.out_duration_var.get(),
                self.delay_var.get(), self.lift_mm_var.get(), self.lift_speed_var.get())
        except (tk.TclError, ValueError) as error:
            self.status_var.set(str(error))
            return
        if self.sequence.start(configs, delay, distance, speed):
            self.lock_controls(True)
            for entry in self.entries:
                entry.configure(state="disabled")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")

    def _done(self, message):
        self.status_var.set(message)
        self.lock_controls(False)
        for entry in self.entries:
            entry.configure(state="normal")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def handle_status(self, values):
        self.sequence.handle_status(values)

    def handle_controller_line(self, line):
        self.sequence.handle_controller_line(line)

    def stop(self):
        self.sequence.abort("STOPPED BY USER")

    def emergency_stopped(self):
        self.sequence.abort("EMERGENCY STOPPED", send_stop=False)

    def close_window(self):
        self.stop()
        self.destroy()
