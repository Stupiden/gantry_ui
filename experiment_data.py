"""Milestone 16 experiment manifest and non-overwriting attempt folders."""

import json
from datetime import datetime
from pathlib import Path


class ExperimentDataManager:
    def __init__(self, output_parent, method, path_name, points, settings):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_method = "".join(ch if ch.isalnum() else "_" for ch in method).strip("_")
        base = Path(output_parent) / "experiment_{}_{}".format(stamp, safe_method)
        self.root = base
        suffix = 1
        while self.root.exists():
            self.root = Path("{}_{}".format(str(base), suffix))
            suffix += 1
        self.root.mkdir(parents=True, exist_ok=False)
        self.manifest_path = self.root / "experiment.json"
        self.active_index = None
        self.active_attempt = None
        self.active_folder = None
        self.data = {
            "schema_version": 1,
            "created_at": datetime.now().isoformat(),
            "method": method,
            "path_name": path_name,
            "settings": settings,
            "points": [
                {"index": int(point["index"]), "x": float(point["x"]),
                 "y": float(point["y"]), "status": "pending", "attempts": []}
                for point in points
            ],
        }
        self._save()

    @classmethod
    def from_manifest(cls, manifest_path):
        path = Path(manifest_path)
        with open(str(path), "r", encoding="utf-8") as stream:
            data = json.load(stream)
        manager = cls.__new__(cls)
        manager.root = path.parent
        manager.manifest_path = path
        manager.active_index = None
        manager.active_attempt = None
        manager.active_folder = None
        manager.data = data
        return manager

    def resumable_points(self):
        return [dict(point) for point in self.data["points"] if point["status"] != "completed"]

    def points_for_resume(self, mode, selected_indices=None):
        if mode == "unfinished":
            records = [point for point in self.data["points"] if point["status"] != "completed"]
        elif mode == "all":
            records = list(self.data["points"])
        elif mode == "selected":
            selected = {int(index) for index in (selected_indices or ())}
            if not selected:
                raise ValueError("Select at least one point to remeasure")
            known = {int(point["index"]) for point in self.data["points"]}
            if selected - known:
                raise ValueError("Unknown selected point(s): {}".format(sorted(selected - known)))
            records = [point for point in self.data["points"] if int(point["index"]) in selected]
        else:
            raise ValueError("Unknown resume mode '{}'".format(mode))
        return [dict(point) for point in records]

    def begin_point(self, point):
        record = self._point(point["index"])
        if record["status"] == "running" and record["attempts"]:
            stale = record["attempts"][-1]
            stale["status"] = "failed"
            stale["finished_at"] = datetime.now().isoformat()
            stale["reason"] = "INTERRUPTED BEFORE RESUME"
        attempt_number = len(record["attempts"]) + 1
        folder = self.root / "measurements" / "point_{:04d}".format(int(point["index"])) / "attempt_{:03d}".format(attempt_number)
        folder.mkdir(parents=True, exist_ok=False)
        attempt = {"attempt": attempt_number, "status": "running",
                   "started_at": datetime.now().isoformat(), "folder": str(folder.relative_to(self.root))}
        record["status"] = "running"
        record["attempts"].append(attempt)
        self.active_index, self.active_attempt = int(point["index"]), attempt_number
        self.active_folder = folder
        self._save()
        return folder

    def complete_point(self, point_index):
        self._finish_attempt(point_index, "completed")

    def fail_active(self, reason):
        if self.active_index is not None:
            self._finish_attempt(self.active_index, "failed", reason)

    def _finish_attempt(self, point_index, status, reason=None):
        record = self._point(point_index)
        record["status"] = status
        if record["attempts"]:
            attempt = record["attempts"][-1]
            attempt["status"] = status
            attempt["finished_at"] = datetime.now().isoformat()
            if reason: attempt["reason"] = str(reason)
        self.active_index = self.active_attempt = self.active_folder = None
        self._save()

    def _point(self, point_index):
        for record in self.data["points"]:
            if record["index"] == int(point_index): return record
        raise KeyError("Unknown point {}".format(point_index))

    def _save(self):
        temporary = self.manifest_path.with_suffix(".json.tmp")
        with open(str(temporary), "w", encoding="utf-8") as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        temporary.replace(self.manifest_path)
