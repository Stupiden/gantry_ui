"""Reusable non-blocking single-point LSV acquisition for automation."""

import csv
from datetime import datetime

from auto_cv_measurement import AutoCVMeasurement


class AutoLSVMeasurement(AutoCVMeasurement):
    def _create_signal(self, params, sample_time):
        return self._call(
            "create LSV signal", self.pstat.signal_ramp_new,
            params["initial"], params["final"], params["scan_rate"],
            sample_time, self.tkp.PSTATMODE,
        )

    def _attach_signal(self, signal):
        self._call("attach LSV signal", self.pstat.set_signal_ramp, signal)

    def _append_csv(self, data):
        names = list(data.dtype.names or ())
        if not names:
            raise ValueError("ToolkitPy returned LSV data without named fields")
        need_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        machine_x, machine_y, machine_z = self.position
        with self.csv_path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if need_header:
                writer.writerow([
                    "timestamp", "point_index", "scan_x_mm", "scan_y_mm",
                    "machine_x_mm", "machine_y_mm", "machine_z_mm",
                    "initial_v", "final_v", "scan_rate_v_s", "step_v",
                    "precharge_s", "ie_range", "auto_ie_range", "ru_ohm",
                    "compensation_fraction", "pos_feed_resistance_ohm",
                ] + names)
            for row in data:
                writer.writerow([
                    datetime.now().isoformat(), self.point["index"],
                    self.point["x"], self.point["y"], machine_x, machine_y, machine_z,
                    self.params["initial"], self.params["final"],
                    self.params["scan_rate"], self.params["step_size"],
                    self.params["precharge"], self.params["ie_range"],
                    int(bool(self.params.get("auto_ie_range", False))),
                    self.params.get("ru_ohm", 0.0),
                    self.params.get("compensation_fraction", 0.0),
                    self.params.get("pos_feed_resistance", 0.0),
                ] + [row[name] for name in names])
