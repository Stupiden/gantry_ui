"""Reusable non-blocking CA acquisition for automated point cycles."""

import csv
import time
from datetime import datetime
from pathlib import Path

from gamry_cv_manual import initialize_pstat_for_cv


class AutoCAMeasurement:
    POLL_MS = 20

    def __init__(self, tk_owner, pstat, tkp):
        self.tk_owner, self.pstat, self.tkp = tk_owner, pstat, tkp
        self.running = False
        self.after_id = self.curve = self.signal = None

    def start(self, params, csv_path, point, position, on_complete, on_error, on_data=None):
        if self.running:
            raise RuntimeError("CA measurement is already running")
        self.params, self.csv_path = dict(params), Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.point, self.position = dict(point), tuple(position)
        self.on_complete, self.on_error, self.on_data = on_complete, on_error, on_data
        self.last_count = 0
        try:
            if not self.tkp.pstat_is_valid(self.pstat):
                raise RuntimeError("PSTAT connection is not valid")
            self.pstat.set_ctrl_mode(self.tkp.PSTATMODE)
            initialize_pstat_for_cv(self.pstat, self.tkp)
            self.pstat.set_ie_range_mode(bool(params["auto_ie_range"]))
            if not params["auto_ie_range"]:
                self.pstat.set_ie_range(params["ie_range"])
            self.signal = self.pstat.signal_d_step_new(
                params["vinit"], params["tinit"], params["vstep1"], params["tstep1"],
                params["vstep2"], params["tstep2"], params["sample_period"], self.tkp.PSTATMODE,
            )
            self.pstat.set_signal_d_step(self.signal)
            self.pstat.init_signal()
            self.curve = self.tkp.ChronoCurve(self.pstat, 10000)
            self.running = True
            self.pstat.set_cell(True)
            self.curve.run(True)
            self.started_at = time.monotonic()
            self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
        except Exception:
            self._cleanup()
            raise

    def _poll(self):
        self.after_id = None
        if not self.running:
            return
        try:
            data = self.curve.acq_data()
            if self.on_data is not None and len(data):
                self.on_data(data)
            if len(data) > self.last_count:
                self._append_csv(data[self.last_count:])
                self.last_count = len(data)
            valid = self.tkp.pstat_is_valid(self.pstat)
            if self.curve.running() and valid:
                self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
            elif valid and not self.last_count and time.monotonic() - self.started_at < 2.0:
                self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
            elif not self.last_count:
                raise RuntimeError("CA ended without data")
            else:
                count, callback = self.last_count, self.on_complete
                self._cleanup(); callback(count, str(self.csv_path))
        except Exception as error:
            callback = self.on_error
            self._cleanup(); callback(error)

    def _append_csv(self, data):
        names = list(data.dtype.names or ())
        if not names:
            raise ValueError("ToolkitPy returned CA data without named fields")
        header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        x, y, z = self.position
        with open(str(self.csv_path), "a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if header:
                writer.writerow(["timestamp", "point_index", "scan_x_mm", "scan_y_mm",
                    "machine_x_mm", "machine_y_mm", "machine_z_mm", "vinit", "tinit_s",
                    "vstep1", "tstep1_s", "vstep2", "tstep2_s", "sample_period_s",
                    "ie_range", "auto_ie_range"] + names)
            for row in data:
                writer.writerow([datetime.now().isoformat(), self.point["index"], self.point["x"],
                    self.point["y"], x, y, z, self.params["vinit"], self.params["tinit"],
                    self.params["vstep1"], self.params["tstep1"], self.params["vstep2"],
                    self.params["tstep2"], self.params["sample_period"], self.params["ie_range"],
                    int(bool(self.params["auto_ie_range"]))] + [row[name] for name in names])

    def stop(self):
        if self.curve is not None:
            try: self.curve.stop()
            except Exception: pass
        self._cleanup()

    def _cleanup(self):
        if self.after_id is not None:
            try: self.tk_owner.after_cancel(self.after_id)
            except Exception: pass
        self.after_id = None
        self.running = False
        try: self.pstat.set_cell(False)
        except Exception: pass
        self.curve = self.signal = None
