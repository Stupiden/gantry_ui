"""Reusable threaded EIS/GetRu acquisition for automated point cycles."""

import queue
import threading
from pathlib import Path

from pre_gamry_eis_getru_max import GamryEIS


class AutoEISMeasurement:
    POLL_MS = 200

    def __init__(self, tk_owner, pstat):
        self.tk_owner = tk_owner
        self.pstat = pstat
        self.running = False
        self.cancelled = False
        self.after_id = None
        self.worker = None
        self.engine = None
        self.result_queue = queue.Queue()

    def start(self, params, csv_path, point, position, on_complete, on_error, on_progress=None):
        if self.running:
            raise RuntimeError("EIS measurement is already running")
        self.params = dict(params)
        self.csv_path = str(csv_path)
        self.point = dict(point)
        self.position = tuple(position)
        self.on_complete = on_complete
        self.on_error = on_error
        self.on_progress = on_progress
        self.cancelled = False
        self.result_queue = queue.Queue()
        pose4 = (self.position[0], self.position[1], self.position[2], 0.0)
        self.running = True
        self.worker = threading.Thread(
            target=self._run_worker, args=(pose4,), daemon=True
        )
        self.worker.start()
        self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)

    def _run_worker(self, pose4):
        try:
            if self.engine is None:
                self.engine = GamryEIS(
                    output_csv=self.csv_path, pstat=self.pstat, auto_initialize=True
                )
            else:
                self.engine.output_csv = Path(self.csv_path)
            result = self.engine.run_eis(
                measurement_index=int(self.point["index"]),
                robot_pose=pose4,
                inoise=1e-10,
                vnoise=1e-6,
                ienoise=1e-10,
                drift_cor=False,
                **self.params
            )
            self.result_queue.put(("done", result))
        except Exception as error:
            self.result_queue.put(("error", str(error)))

    def _poll(self):
        self.after_id = None
        if self.on_progress is not None:
            self.on_progress(self.csv_path)
        try:
            kind, payload = self.result_queue.get_nowait()
        except queue.Empty:
            if self.running:
                self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)
            return
        self.running = False
        self.worker = None
        self._hardware_off()
        if self.cancelled:
            return
        if kind == "done":
            self.on_complete(payload)
        else:
            self.on_error(payload)

    def stop(self):
        self.cancelled = True
        self._hardware_off()
        if self.running and self.after_id is None:
            self.after_id = self.tk_owner.after(self.POLL_MS, self._poll)

    def _hardware_off(self):
        try:
            self.pstat.set_cell(False)
        except Exception:
            pass
        try:
            self.pstat.set_pos_feed_resistance(0.0)
            self.pstat.set_pos_feed_enable(False)
        except Exception:
            pass
