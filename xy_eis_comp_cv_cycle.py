"""Automated per-point GetRu followed by positive-feedback compensated CV."""

from datetime import datetime
import math
from pathlib import Path
import time
import tkinter as tk
from tkinter import messagebox, ttk

from auto_cv_measurement import AutoCVMeasurement
from automatic_live_plot import AutomaticLivePlot
from xy_full_eis_cycle import XYFullEISCycleWindow
from xy_pump_cycle import XYPumpCycleWindow


RESUME_POSITION_TOLERANCE_MM = 0.004


class XYEISCompCVCycleWindow(XYFullEISCycleWindow):
    def __init__(
        self, parent, points, path_name, send_command, position_provider,
        machine_position_provider, start_guard, lock_controls,
        pstat, tkp, claim_measurement, release_measurement, resume_manager=None,
        homed_provider=None, home_status_provider=None
    ):
        XYFullEISCycleWindow.__init__(
            self, parent, points, path_name, send_command, position_provider,
            machine_position_provider, start_guard, lock_controls,
            pstat, claim_measurement, release_measurement,
        )
        self.title("Full measurement cycle — EIS/GetRu + compensated CV")
        self.geometry("980x860")
        self.tkp = tkp
        self.homed_provider = homed_provider
        self.home_status_provider = home_status_provider
        self.resume_manager = resume_manager
        self.scan_origin_machine = None
        self.formal_alignment_state = None
        self.formal_alignment_ready = False
        self.formal_home_started_at = None
        self.formal_home_deadline = None
        self.formal_poll_id = None
        self.resume_move_axis = None
        self.resume_move_target = None
        self.resume_move_deadline = None
        self.resume_move_poll_id = None
        self.resume_press_poll_id = None
        self.resume_press_peak_force = 0.0
        self.resume_press_motion_seen = False
        self.resume_trace_path = Path(__file__).resolve().with_name("automation_trace.log")
        self.cv_runner = AutoCVMeasurement(self, pstat, tkp)
        self.live_plot.destroy()
        self.live_plot = AutomaticLivePlot(self, show_cv=True, show_eis=True)
        self.cv_initial_var = tk.DoubleVar(value=0.0)
        self.cv_apex1_var = tk.DoubleVar(value=0.0)
        self.cv_apex2_var = tk.DoubleVar(value=0.0)
        self.cv_final_var = tk.DoubleVar(value=0.0)
        self.cv_scan_rate_var = tk.DoubleVar(value=0.10)
        self.cv_step_var = tk.DoubleVar(value=0.001)
        self.cv_cycles_var = tk.IntVar(value=4)
        self.cv_precharge_var = tk.DoubleVar(value=10.0)
        self.cv_ie_range_var = tk.DoubleVar(value=10.0)
        self.comp_fraction_var = tk.DoubleVar(value=0.85)
        self.min_ru_var = tk.DoubleVar(value=0.01)
        self.max_ru_var = tk.DoubleVar(value=10000.0)
        self.comp_widgets = []
        self._build_comp_cv_controls()
        if resume_manager is not None:
            parent.register_resume_serial_listener(self)
            self._apply_resume_settings(resume_manager.data["settings"])

    def _apply_resume_settings(self, settings):
        motion, press = settings["motion"], settings["press"]
        measurement = settings["measurement"]
        eis, cv = measurement["eis"], measurement["cv"]
        self.speed_var.set(motion["speed_mm_s"])
        self.dwell_var.set(motion["wait_at_point_s"])
        self.press_force_var.set(press["force_threshold_g"])
        self.press_travel_var.set(press["max_travel_mm"])
        self.press_speed_var.set(press["speed_mm_s"])
        self.lift_distance_var.set(press["lift_distance_mm"])
        self.lift_speed_var.set(press["lift_speed_mm_s"])
        self.pressed_stabilization_var.set(settings["pressed_stabilization_s"])
        self.pre_lift_wait_var.set(settings["pre_lift_wait_s"])
        enabled = {int(item["pump_id"]): item for item in settings["pumps"]}
        for pump_id in (0, 1):
            self.pump_enabled_vars[pump_id].set(pump_id in enabled)
            if pump_id in enabled:
                self.pump_rpm_vars[pump_id].set(enabled[pump_id]["speed_rpm"])
                self.pump_duration_vars[pump_id].set(enabled[pump_id]["post_measurement_s"])
        self.frequencies_var.set(", ".join(str(item) for item in eis["frequencies"]))
        for variable, key in ((self.repeats_var, "n_times"), (self.ac_amplitude_var, "ac_amplitude"),
                              (self.dc_bias_var, "dc_bias"), (self.gain_var, "gain"),
                              (self.zmod_var, "zmod"), (self.passes_var, "passes"),
                              (self.cycles_delay_var, "cycles_delay"), (self.settle_var, "dc_settle_s"),
                              (self.max_phase_var, "max_phase_deg_for_ru"),
                              (self.z_bad_var, "z_bad_threshold_ohm"),
                              (self.min_bad_var, "min_bad_points")):
            variable.set(eis[key])
        for variable, key in ((self.cv_initial_var, "initial"), (self.cv_apex1_var, "apex1"),
                              (self.cv_apex2_var, "apex2"), (self.cv_final_var, "final"),
                              (self.cv_scan_rate_var, "scan_rate"), (self.cv_step_var, "step_size"),
                              (self.cv_cycles_var, "cycles"), (self.cv_precharge_var, "precharge"),
                              (self.cv_ie_range_var, "ie_range")):
            variable.set(cv[key])
        self.comp_fraction_var.set(measurement["compensation_fraction"])
        self.min_ru_var.set(measurement["min_ru_ohm"])
        self.max_ru_var.set(measurement["max_ru_ohm"])
        self.output_dir_var.set(str(resume_manager.root.parent))
        self.status_var.set("CHECKPOINT LOADED — press START to resume remaining points")

    def _build_comp_cv_controls(self):
        frame = self.start_button.master
        specs = (
            ("CV Initial V", self.cv_initial_var), ("Apex 1 V", self.cv_apex1_var),
            ("Apex 2 V", self.cv_apex2_var), ("Final V", self.cv_final_var),
            ("Scan V/s", self.cv_scan_rate_var), ("Step V", self.cv_step_var),
            ("Cycles", self.cv_cycles_var), ("Precharge s", self.cv_precharge_var),
            ("CV IE range", self.cv_ie_range_var), ("Ru fraction", self.comp_fraction_var),
            ("Min Ru Ω", self.min_ru_var), ("Max Ru Ω", self.max_ru_var),
        )
        for index, (label, variable) in enumerate(specs):
            row = 24 + (index // 6) * 2
            column = index % 6
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", padx=5)
            entry = ttk.Entry(frame, textvariable=variable, width=10)
            entry.grid(row=row + 1, column=column, sticky="w", padx=5)
            self.comp_widgets.append(entry)
        self.start_button.grid_configure(row=28)
        self.stop_button.grid_configure(row=28)
        self.start_button.configure(text="START EIS + COMPENSATED CV")

    def _comp_cv_parameters(self):
        try:
            params = {
                "initial": float(self.cv_initial_var.get()),
                "apex1": float(self.cv_apex1_var.get()),
                "apex2": float(self.cv_apex2_var.get()),
                "final": float(self.cv_final_var.get()),
                "scan_rate": float(self.cv_scan_rate_var.get()),
                "step_size": float(self.cv_step_var.get()),
                "cycles": int(self.cv_cycles_var.get()),
                "precharge": float(self.cv_precharge_var.get()),
                "ie_range": float(self.cv_ie_range_var.get()),
            }
            fraction = float(self.comp_fraction_var.get())
            min_ru = float(self.min_ru_var.get())
            max_ru = float(self.max_ru_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("CV compensation parameters must be numbers")
        if params["scan_rate"] <= 0 or params["step_size"] <= 0 or params["cycles"] < 1:
            raise ValueError("CV scan rate/step/cycles are invalid")
        if params["precharge"] < 0 or params["ie_range"] <= 0:
            raise ValueError("CV precharge/IE range are invalid")
        if not 0.0 < fraction <= 0.95:
            raise ValueError("Ru compensation fraction must be > 0 and <= 0.95")
        if min_ru <= 0 or max_ru <= min_ru:
            raise ValueError("Ru limits are invalid")
        return params, fraction, min_ru, max_ru

    def start(self):
        if self.formal_alignment_state is not None:
            return
        try:
            self.cv_params, self.comp_fraction, self.min_ru, self.max_ru = self._comp_cv_parameters()
        except Exception as error:
            messagebox.showerror("EIS + compensated CV", str(error), parent=self)
            return
        if self.resume_manager is not None and not self.formal_alignment_ready:
            if not self._begin_formal_alignment():
                return
            return
        self._start_standard_measurement()

    def _start_standard_measurement(self):
        """Run the same point-measurement state machine after positioning.

        Resume positioning is deliberately finished before this method is
        entered.  From here onward the flow is identical to a new automatic
        EIS + compensated-CV experiment.
        """
        if self.resume_manager is None and self.scan_origin_machine is None:
            machine_x, machine_y, unused_z = self.machine_position_provider()
            scan_x, scan_y = self.position_provider()
            self.scan_origin_machine = (machine_x - scan_x, machine_y - scan_y)
        if self.resume_manager is None:
            XYFullEISCycleWindow.start(self)
        else:
            self._start_resumed_measurement_only()
        if self.running:
            for widget in self.comp_widgets:
                widget.configure(state="disabled")

    def _start_resumed_measurement_only(self):
        """Start the proven automation flow with checkpoint data only.

        This lives entirely in the resume subclass.  The normal full-EIS,
        press, pump, lift, and XY classes remain unchanged.
        """
        try:
            self.eis_params, self.output_dir = self._eis_parameters()
        except Exception as error:
            messagebox.showerror("Resume EIS + CV", str(error), parent=self)
            return
        XYPumpCycleWindow.start(self)
        if not self.running:
            return
        self.data_manager = self.resume_manager
        if self.claim_measurement is not None:
            if not self.claim_measurement("AUTO_EIS"):
                XYPumpCycleWindow.stop(self)
                messagebox.showerror(
                    "Resume EIS + CV", "Another Gamry measurement is already running",
                    parent=self,
                )
                return
            self.measurement_claimed = True
        for widget in self.eis_widgets:
            widget.configure(state="disabled")

    def _move_x(self):
        if self.resume_manager is None:
            return XYFullEISCycleWindow._move_x(self)
        self.after_id = None
        if not self.running:
            return
        point = self._current_target()
        self.current_point_var.set(
            "{}  X={:.3f}  Y={:.3f}".format(point["index"], point["x"], point["y"])
        )
        self._start_resume_axis_move("X", float(point["x"]))

    def _move_y(self):
        if self.resume_manager is None:
            return XYFullEISCycleWindow._move_y(self)
        if not self.running:
            return
        self._start_resume_axis_move("Y", float(self._current_target()["y"]))

    def _start_resume_axis_move(self, axis, target):
        current = self.position_provider()[0 if axis == "X" else 1]
        delta = target - current
        point = self._current_target()
        if abs(delta) <= RESUME_POSITION_TOLERANCE_MM:
            if axis == "X":
                self.after_id = self.after(20, self._move_y)
            else:
                self.after_id = self.after(20, self._arrived)
            return
        self.resume_move_axis = axis
        self.resume_move_target = target
        expected_seconds = abs(delta) / max(float(self.speed_var.get()), 0.05)
        self.resume_move_deadline = time.monotonic() + max(
            5.0, expected_seconds * 2.0 + 5.0
        )
        self.awaiting_axis = axis
        self.status_var.set(
            "RESUME POSITION — point {} moving {} to {:.3f}".format(
                point["index"], axis, target
            )
        )
        if not self.send_command("MOVE {} {:.3f}".format(axis, delta)):
            self._finish("FAILED — resume {} move was not sent".format(axis))
            return
        self._schedule_resume_move_poll()

    def _schedule_resume_move_poll(self):
        self._cancel_resume_move_poll()
        self.resume_move_poll_id = self.after(100, self._poll_resume_move)

    def _cancel_resume_move_poll(self):
        if self.resume_move_poll_id is not None:
            try:
                self.after_cancel(self.resume_move_poll_id)
            except Exception:
                pass
            self.resume_move_poll_id = None

    def _poll_resume_move(self):
        self.resume_move_poll_id = None
        if not self.running or self.resume_move_axis not in ("X", "Y"):
            return
        axis = self.resume_move_axis
        current = self.position_provider()[0 if axis == "X" else 1]
        error = self.resume_move_target - current
        self.status_var.set(
            "RESUME POSITION — {} current={:.3f} target={:.3f}".format(
                axis, current, self.resume_move_target
            )
        )
        if abs(error) <= RESUME_POSITION_TOLERANCE_MM:
            self._complete_resume_axis_move(axis)
            return
        if time.monotonic() >= self.resume_move_deadline:
            self.send_command("MOTION_STOP", require_ready=False)
            self._finish(
                "ABORTED — resume {} stopped {:.3f} mm from target".format(axis, error)
            )
            return
        self.send_command("STATUS", require_ready=False)
        self._schedule_resume_move_poll()

    def _complete_resume_axis_move(self, axis):
        if self.resume_move_axis != axis:
            return
        self._cancel_resume_move_poll()
        self.resume_move_axis = None
        self.resume_move_target = None
        self.resume_move_deadline = None
        self.awaiting_axis = None
        if axis == "X":
            self.after_id = self.after(20, self._move_y)
        else:
            self.after_id = self.after(20, self._arrived)

    def _start_press(self):
        if self.resume_manager is None:
            return XYPumpCycleWindow._start_press(self)
        self.resume_press_peak_force = 0.0
        self.resume_press_motion_seen = False
        XYPumpCycleWindow._start_press(self)
        self._resume_trace("PRESS SENT state={}".format(self.press_state))
        if self.running and self.press_state == "pressing":
            self._schedule_resume_press_poll()

    def _schedule_resume_press_poll(self):
        self._cancel_resume_press_poll()
        self.resume_press_poll_id = self.after(100, self._poll_resume_press)

    def _cancel_resume_press_poll(self):
        if self.resume_press_poll_id is not None:
            try:
                self.after_cancel(self.resume_press_poll_id)
            except Exception:
                pass
            self.resume_press_poll_id = None

    def _poll_resume_press(self):
        self.resume_press_poll_id = None
        if not self.running or self.press_state != "pressing":
            return
        self.send_command("STATUS", require_ready=False)
        self._schedule_resume_press_poll()

    def _resume_press_complete(self, source):
        if not self.running or self.press_state != "pressing":
            return
        self._cancel_resume_press_poll()
        self._resume_trace(
            "PRESS COMPLETE source={} peak={:.2f}".format(
                source, self.resume_press_peak_force
            )
        )
        self.status_var.set("RESUME PRESS COMPLETE — starting pumps")
        self._start_pumps()

    def _resume_trace(self, message):
        try:
            with self.resume_trace_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "{} RESUME {}\n".format(
                        time.strftime("%Y-%m-%d %H:%M:%S"), message
                    )
                )
        except OSError:
            pass

    def _begin_formal_alignment(self):
        allowed, reason = self.start_guard()
        if not allowed:
            messagebox.showerror("Formal experiment", reason, parent=self)
            return False
        settings = self.resume_manager.data["settings"]
        saved = settings.get("scan_origin_machine")
        if saved is None:
            legacy = settings.get("formal_experiment", {})
            if legacy.get("scan_zero_machine_x_mm") is not None:
                saved = {
                    "x_mm": legacy["scan_zero_machine_x_mm"],
                    "y_mm": legacy["scan_zero_machine_y_mm"],
                }
        if saved is None:
            messagebox.showerror(
                "Resume experiment", "Checkpoint does not contain a saved Scan Zero", parent=self
            )
            return False
        self.scan_origin_machine = (float(saved["x_mm"]), float(saved["y_mm"]))
        self.formal_origin_machine = self.scan_origin_machine
        self.lock_controls(True)
        self.start_button.configure(state="disabled")
        self.status_var.set("RESUME — returning from Home to saved Scan Zero")
        self._start_formal_origin_move("X")
        return True

    def _arm_formal_home_poll(self):
        self.formal_home_started_at = time.monotonic()
        self.formal_home_deadline = self.formal_home_started_at + 120.0
        if self.formal_poll_id is not None:
            try:
                self.after_cancel(self.formal_poll_id)
            except Exception:
                pass
        self.formal_poll_id = self.after(250, self._poll_formal_home)

    def _poll_formal_home(self):
        self.formal_poll_id = None
        if self.formal_alignment_state not in ("move_x", "move_y", "scan_zero"):
            return
        if time.monotonic() >= self.formal_home_deadline:
            self.send_command("MOTION_STOP", require_ready=False)
            self._abort_formal_alignment("{} timed out".format(self.formal_alignment_state.upper()))
            return
        elapsed = time.monotonic() - self.formal_home_started_at
        if self.home_status_provider is not None and elapsed >= 0.75:
            x_homed, x_moving, y_homed, y_moving = self.home_status_provider()
            if self.formal_alignment_state in ("move_x", "move_y"):
                axis = "X" if self.formal_alignment_state == "move_x" else "Y"
                offset = 0 if axis == "X" else 1
                moving = x_moving if axis == "X" else y_moving
                current = self.machine_position_provider()[offset]
                target = self.formal_origin_machine[offset]
                error = target - current
                self.status_var.set(
                    "FORMAL ALIGNMENT — return {} — current={:.3f} target={:.3f} moving={}".format(
                        axis, current, target, int(moving)
                    )
                )
                if not moving and abs(error) <= 0.002:
                    if axis == "X":
                        self._start_formal_origin_move("Y")
                    else:
                        self._set_formal_scan_zero()
                    return
                if not moving and elapsed >= 1.0:
                    self._abort_formal_alignment(
                        "return {} stopped {:.3f} mm from saved Scan Zero".format(axis, error)
                    )
                    return
            elif self.formal_alignment_state == "scan_zero":
                scan_x, scan_y = self.position_provider()
                self.status_var.set(
                    "FORMAL ALIGNMENT — verify Scan Zero — X={:.3f} Y={:.3f}".format(
                        scan_x, scan_y
                    )
                )
                if abs(scan_x) <= 0.002 and abs(scan_y) <= 0.002:
                    self._complete_formal_alignment()
                    return
        self.send_command("STATUS", require_ready=False)
        self.formal_poll_id = self.after(250, self._poll_formal_home)

    def _abort_formal_alignment(self, reason):
        if self.formal_poll_id is not None:
            try:
                self.after_cancel(self.formal_poll_id)
            except Exception:
                pass
            self.formal_poll_id = None
        self.formal_alignment_state = None
        self.formal_alignment_ready = False
        self.lock_controls(False)
        self.start_button.configure(state="normal")
        self.status_var.set("FORMAL ALIGNMENT ABORTED — {}".format(reason))

    def _start_formal_origin_move(self, axis):
        machine = self.machine_position_provider()
        offset = 0 if axis == "X" else 1
        delta = self.formal_origin_machine[offset] - machine[offset]
        if abs(delta) <= 0.002:
            if axis == "X":
                self._start_formal_origin_move("Y")
            else:
                self._set_formal_scan_zero()
            return
        self.formal_alignment_state = "move_{}".format(axis.lower())
        self.status_var.set("FORMAL ALIGNMENT — returning {} to saved Scan Zero".format(axis))
        if not self.send_command("MOVE {} {:.3f}".format(axis, delta)):
            self._abort_formal_alignment("could not send return {} move".format(axis))
            return
        self._arm_formal_home_poll()

    def handle_controller_line(self, line):
        state = self.formal_alignment_state
        if state is not None:
            if line.startswith("ERROR,") or "LIMIT_STOP" in line or "ALARM_STOP" in line or line == "EVENT,WATCHDOG_STOP":
                self._abort_formal_alignment(line)
            elif state == "move_x" and line == "EVENT,MOVE_DONE,X":
                self._start_formal_origin_move("Y")
            elif state == "move_y" and line == "EVENT,MOVE_DONE,Y":
                self._set_formal_scan_zero()
            return
        if (
            self.resume_manager is not None
            and line.startswith("EVENT,PRESS_FORCE_STOP")
            and self.press_state == "pressing"
        ):
            self._resume_trace("RX {}".format(line))
            self._resume_press_complete("event")
            return
        if self.resume_manager is not None and self.resume_move_axis is not None:
            if line == "EVENT,MOVE_DONE,{}".format(self.resume_move_axis):
                self._complete_resume_axis_move(self.resume_move_axis)
                return
            if line.startswith("ERROR,") or "LIMIT_STOP" in line or "ALARM_STOP" in line:
                self._finish("ABORTED — {}".format(line))
                return
        XYFullEISCycleWindow.handle_controller_line(self, line)

    def _set_formal_scan_zero(self):
        self.formal_alignment_state = "scan_zero"
        self.status_var.set("FORMAL ALIGNMENT — setting and verifying Scan Zero")
        if not self.send_command("SCAN_ZERO"):
            self._abort_formal_alignment("could not set Scan Zero")
            return
        self._arm_formal_home_poll()

    def _complete_formal_alignment(self):
        if self.formal_poll_id is not None:
            try:
                self.after_cancel(self.formal_poll_id)
            except Exception:
                pass
            self.formal_poll_id = None
        self.formal_alignment_state = None
        self.formal_alignment_ready = True
        self.lock_controls(False)
        self.status_var.set("POSITION VERIFIED — starting standard measurement")
        self.after(100, self._start_standard_measurement)

    def handle_status(self, values):
        if self.formal_alignment_state == "scan_zero":
            try:
                if abs(float(values["SX"])) <= 0.002 and abs(float(values["SY"])) <= 0.002:
                    self._complete_formal_alignment()
            except (KeyError, ValueError):
                pass
        if self.resume_manager is not None and self.resume_move_axis is not None:
            try:
                axis = self.resume_move_axis
                current = float(values["S{}".format(axis)])
                moving = values["{}MOVING".format(axis)] == "1"
                if not moving and abs(self.resume_move_target - current) <= RESUME_POSITION_TOLERANCE_MM:
                    self._complete_resume_axis_move(axis)
            except (KeyError, ValueError):
                pass
        if self.resume_manager is not None and self.press_state == "pressing":
            try:
                controller_state = values["PR"]
                force = float(values["F"])
                self.resume_press_peak_force = max(
                    self.resume_press_peak_force, force
                )
                if controller_state == "1":
                    self.resume_press_motion_seen = True
                tolerance = max(1.0, self.press_force * 0.02)
                threshold_seen = self.resume_press_peak_force >= self.press_force - tolerance
                if controller_state == "0" and threshold_seen:
                    self._resume_press_complete("status")
                    return
            except (KeyError, ValueError):
                pass
        XYFullEISCycleWindow.handle_status(self, values)

    def _eis_complete(self, result):
        if not self.running:
            return
        ru = float(result["avg_ru"])
        if not math.isfinite(ru) or not self.min_ru <= ru <= self.max_ru:
            self._finish("ABORTED — GetRu value {:.6g} Ω is outside limits; use manual LIFT".format(ru))
            return
        resistance = ru * self.comp_fraction
        params = dict(self.cv_params)
        params.update({
            "ru_ohm": ru,
            "compensation_fraction": self.comp_fraction,
            "pos_feed_resistance": resistance,
        })
        point = self._current_target()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "CV_point_{:04d}_Ru{:.6g}_{}.csv".format(
            int(point["index"]), ru, stamp
        )
        self.press_state = "measuring_comp_cv"
        self.status_var.set(
            "Ru {:.6g} Ω — CV with {:.1f}% compensation ({:.6g} Ω) — pumps ON".format(
                ru, self.comp_fraction * 100.0, resistance
            )
        )
        self.live_plot.clear_cv()
        try:
            self.cv_runner.start(
                params, self.active_attempt_dir / filename, point,
                self.machine_position_provider(), self._cv_complete, self._cv_error,
                self.live_plot.update_cv,
            )
            self._restore_measurement_pumps()
        except Exception as error:
            self._cv_error(error)

    def _experiment_method(self):
        return "EIS_GetRu_compensated_CV"

    def _experiment_settings(self):
        return {
            "eis": self.eis_params,
            "cv": self.cv_params,
            "compensation_fraction": self.comp_fraction,
            "min_ru_ohm": self.min_ru,
            "max_ru_ohm": self.max_ru,
        }

    def _manifest_settings(self, measurement):
        settings = XYFullEISCycleWindow._manifest_settings(self, measurement)
        settings["scan_origin_machine"] = {
            "x_mm": self.scan_origin_machine[0],
            "y_mm": self.scan_origin_machine[1],
        }
        return settings

    def _cv_complete(self, point_count, csv_path):
        if not self.running:
            return
        self.press_state = "post_cv_pumping"
        self.status_var.set("COMPENSATED CV SAVED — {} points — pumps remain ON".format(point_count))
        for pump_id, unused_rpm, duration in self.pump_configs:
            self.pump_after_ids[pump_id] = self.after(
                int(duration * 1000),
                lambda selected_id=pump_id: self._pump_time_complete(selected_id),
            )

    def _cv_error(self, error):
        self._finish("ABORTED — compensated CV failed: {} — use manual LIFT".format(error))

    def stop(self):
        if self.cv_runner.running:
            self.cv_runner.stop()
        XYFullEISCycleWindow.stop(self)

    def emergency_stopped(self):
        if self.formal_alignment_state is not None:
            self._abort_formal_alignment("EMERGENCY STOPPED")
            return
        if self.cv_runner.running:
            self.cv_runner.stop()
        XYFullEISCycleWindow.emergency_stopped(self)

    def close_window(self):
        if self.formal_alignment_state is not None:
            self.send_command("MOTION_STOP")
            self._abort_formal_alignment("window closed")
        XYFullEISCycleWindow.close_window(self)

    def _finish(self, message):
        self._cancel_resume_move_poll()
        self._cancel_resume_press_poll()
        self.resume_move_axis = None
        self.resume_move_target = None
        self.resume_move_deadline = None
        if hasattr(self, "cv_runner") and self.cv_runner.running:
            self.cv_runner.stop()
        XYFullEISCycleWindow._finish(self, message)
        self.formal_alignment_ready = False
        if hasattr(self, "comp_widgets"):
            for widget in self.comp_widgets:
                widget.configure(state="normal")
