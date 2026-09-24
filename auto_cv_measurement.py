"""Reusable non-blocking single-point CV acquisition for automation."""

import csv
import time
from datetime import datetime
from pathlib import Path

from gamry_cv_manual import initialize_pstat_for_cv


class AutoCVMeasurement:
    POLL_MS = 50

    def __init__(self, tk_owner, pstat, tkp):
        self.tk_owner = tk_owner
        self.pstat = pstat
        self.tkp = tkp
        self.running = False
        self.after_id = None
        self.curve = None
        self.signal = None

    def start(self, params, csv_path, point, position, on_complete, on_error):
        if self.running:
            raise RuntimeError("CV measurement is already running")
        self.params = dict(params)
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.point = dict(point)
        self.position = tuple(position)
        self.on_complete = on_complete
        self.on_error = on_error
        self.last_count = 0
        self.curve_started = False
        self.curve_started_at = 0.0

        try:
            if not self.tkp.pstat_is_valid(self.pstat):
                raise RuntimeError("PSTAT connection is not valid")
            self._call("set control mode", self.pstat.set_ctrl_mode, self.tkp.PSTATMODE)
            self._call("initialize PSTAT", initialize_pstat_for_cv, self.pstat, self.tkp)
            self._call("disable auto IE range", self.pstat.set_ie_range_mode, False)
            self._call("set IE range", self.pstat.set_ie_range, params["ie_range"])
            self._call("clear positive feedback resistance", self.pstat.set_pos_feed_resistance, 0.0)
            self._call("disable positive feedback", self.pstat.set_pos_feed_enable, False)
            sample_time = params["step_size"] / params["scan_rate"]
            self.signal = self._call(
                "create CV signal", self.pstat.signal_r_up_dn_new,
                [params["initial"], params["apex1"], params["apex2"], params["final"]],
                [params["scan_rate"], params["scan_rate"], params["scan_rate"]],
                [0.0, 0.0, 0.0], sample_time, params["cycles"], self.tkp.PSTATMODE,
            )
            self._call("attach CV signal", self.pstat.set_signal_r_up_dn, self.signal)
            self._call("initialize CV signal", self.pstat.init_signal)
            self.curve = self._call("create CV curve", self.tkp.RcvCurve, self.pstat, 10000)
            self.precharge_deadline = time.monotonic() + params["precharge"]
            self.running = True
            self.pstat.set_cell(True)
            self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
        except Exception:
            self._hardware_off()
            self.running = False
            raise

    @staticmethod
    def _call(stage, function, *args):
        try:
            return function(*args)
        except Exception as error:
            raise RuntimeError("{} failed: {}".format(stage, error))

    def _poll(self):
        self.after_id = None
        if not self.running:
            return
        try:
            if not self.curve_started:
                if time.monotonic() < self.precharge_deadline:
                    self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
                    return
                self._call("start CV curve", self.curve.run, True)
                self.curve_started = True
                self.curve_started_at = time.monotonic()

            data = self.curve.acq_data()
            if len(data) > self.last_count:
                self._append_csv(data[self.last_count:])
                self.last_count = len(data)

            if self.curve.running() and self.tkp.pstat_is_valid(self.pstat):
                self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
            elif self.last_count == 0 and time.monotonic() - self.curve_started_at < 2.0:
                self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
            elif self.last_count == 0:
                raise RuntimeError("CV ended without data")
            else:
                count = self.last_count
                callback = self.on_complete
                self._cleanup()
                callback(count, str(self.csv_path))
        except Exception as error:
            callback = self.on_error
            self._cleanup()
            callback(error)

    def _append_csv(self, data):
        names = list(data.dtype.names or ())
        if not names:
            raise ValueError("ToolkitPy returned CV data without named fields")
        need_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        machine_x, machine_y, machine_z = self.position
        with open(str(self.csv_path), "a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if need_header:
                writer.writerow([
                    "timestamp", "point_index", "scan_x_mm", "scan_y_mm",
                    "machine_x_mm", "machine_y_mm", "machine_z_mm",
                    "initial_v", "apex1_v", "apex2_v", "final_v",
                    "scan_rate_v_s", "step_v", "cycles", "precharge_s", "ie_range",
                ] + names)
            for row in data:
                writer.writerow([
                    datetime.now().isoformat(), self.point["index"],
                    self.point["x"], self.point["y"],
                    machine_x, machine_y, machine_z,
                    self.params["initial"], self.params["apex1"],
                    self.params["apex2"], self.params["final"],
                    self.params["scan_rate"], self.params["step_size"],
                    self.params["cycles"], self.params["precharge"],
                    self.params["ie_range"],
                ] + [row[name] for name in names])

    def stop(self):
        if self.curve_started and self.curve is not None:
            try:
                self.curve.stop()
            except Exception:
                pass
        self._cleanup()

    def _hardware_off(self):
        try:
            self.pstat.set_pos_feed_enable(False)
            self.pstat.set_pos_feed_resistance(0.0)
        except Exception:
            pass
        try:
            self.pstat.set_cell(False)
        except Exception:
            pass

    def _cleanup(self):
        if self.after_id is not None:
            try:
                self.tk_owner.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        self.running = False
        self._hardware_off()
        self.curve = None
        self.signal = None
        self.curve_started = False
