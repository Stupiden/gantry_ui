from __future__ import annotations

import csv
import math
import time
from pathlib import Path
from typing import Sequence

import toolkitpy as tkp

try:
    from toolkitreadz import ReadZ
except ImportError:
    from toolkitpy.toolkitreadz import ReadZ


class EISContactError(RuntimeError):
    """
    Raised when EIS measurements indicate poor contact.

    This happens when ALL zreal and zimag values across every frequency point
    in a scan run have absolute values above `z_bad_threshold_ohm`, and this
    condition persists for `bad_contact_consecutive_runs` consecutive runs.
    The caller should lift the probe, re-approach, and retry the same index.
    """


DEFAULT_GETRU_FREQUENCIES = [
    1000000.0,
    300000.0,
    170000.0,
    100000.0,
    30000.0,
    10000.0,
    3000.0,
    1000.0,
    500.0,
    2.5,
]

# How long to wait after applying dc_bias before measuring idc/vdc.
# Gamry software waits for the electrode to settle at the operating potential.
# Without this delay, idc is measured at near-OCV, not at dc_bias, which causes
# ReadZ to normalise impedance against the wrong DC operating point and makes
# every frequency point fail the phase/zreal acceptance criteria.
_DC_SETTLE_S = 0.5


def initialize_pstat(pstat) -> None:
    """
    One-time hardware setup for a GetRu-style potentiostatic EIS measurement.

    This must be called exactly ONCE before a measurement session begins
    (inside open() or the first call to run_eis).  It must NOT be called
    again at the start of every run_eis() call, because resetting ie_range
    and stability settings mid-session forces the hardware to re-settle and
    is the primary cause of repeated EIS retries when using a non-zero dc_bias.
    """
    try:
        pstat.set_cell(False)
    except Exception:
        pass

    try:
        pstat.instrument_specific_initialize()
    except Exception:
        pass

    pstat.set_ach_select(tkp.ACHSELECT_GND)
    pstat.set_ctrl_mode(tkp.PSTATMODE)
    # Match Framework Scripts/GetRu.exp::InitializePstat().
    pstat.set_ie_stability(tkp.STABILITY_FAST)
    pstat.set_ca_speed(tkp.CASPEED_MEDFAST)
    try:
        pstat.set_sense_speed_mode(True)
    except Exception:
        pass
    pstat.set_ground(tkp.FLOAT)

    pstat.set_ich_range(3.0)
    pstat.set_ich_range_mode(False)
    pstat.set_ich_offset_enable(True)
    try:
        pstat.set_ich_filter(2.5)
    except Exception:
        pass

    try:
        pstat.set_ich_offset(0.0)
    except Exception:
        pass

    pstat.set_vch_range(3.0)
    pstat.set_vch_range_mode(False)
    pstat.set_vch_offset_enable(True)
    try:
        pstat.set_vch_filter(2.5)
    except Exception:
        pass

    try:
        pstat.set_vch_offset(0.0)
        # No voltage filter so the full EIS frequency range can pass through.
    except Exception:
        pass

    pstat.set_ach_range(3.0)
    pstat.set_ach_offset_enable(False)

    try:
        pstat.set_ie_range_lower_limit(None)
    except Exception:
        pstat.set_ie_range_lower_limit(1)

    try:
        pstat.set_ie_range(0.03)
    except Exception:
        pass

    # Fix the IE range; do not let auto-ranging interfere during EIS.
    pstat.set_ie_range_mode(False)

    pstat.set_analog_out(0.0)
    pstat.set_voltage(0.0)
    pstat.set_pos_feed_enable(False)
    pstat.set_pos_feed_resistance(0.0)

    try:
        pstat.set_irupt_mode(tkp.IRUPTOFF)
    except Exception:
        pass


def _normalize_pose4(robot_pose: Sequence[float]) -> tuple[float, float, float, float]:
    if robot_pose is None or len(robot_pose) < 4:
        raise ValueError("robot_pose must contain at least 4 values: [x, y, z, r]")

    return (
        float(robot_pose[0]),
        float(robot_pose[1]),
        float(robot_pose[2]),
        float(robot_pose[3]),
    )


def _safe_scalar(value):
    if value is None:
        return ""

    if isinstance(value, (list, tuple)):
        return repr(value)

    try:
        return float(value)
    except Exception:
        return str(value)


def _ensure_csv_header(csv_path: Path, field_names: list[str]) -> None:
    need_header = (not csv_path.exists()) or (csv_path.stat().st_size == 0)

    if need_header:
        header = ["measurement_index", "x", "y", "z", "r", *field_names]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)


class GamryEIS:
    """
    Reuse one PSTAT for many GetRu-style measurements.
    Append every measured impedance point into one CSV file.

    Ru selection follows Gamry GetRu-style logic:

        1. Scan frequencies from high to low.
        2. Accept the first point satisfying:
               0 < zreal <= max_ru_ohm_for_ru
               abs(phase_deg) < max_phase_deg_for_ru
        3. Do not average all frequency points.
        4. If no acceptable point exists, raise RuntimeError so main can use previous_ru.

    Key differences from the original Gamry Framework GetRu that this class
    must replicate correctly
    -----------------------------------------------------------------------
    a) DC operating point must be established BEFORE idc/vdc are sampled.
       Gamry applies dc_bias, waits for settling, then reads idc and vdc.
       The values fed to ReadZ.set_idc() / set_vdc() must be the actual
       steady-state current and voltage AT the dc_bias operating point,
       not the open-circuit values.

    b) Hardware initialisation (initialize_pstat) happens once per session,
       not once per measurement point.  Re-running it resets ie_range and
       stability settings, which forces the hardware to re-settle and causes
       every subsequent EIS point to fail the acceptance criteria.

    c) vdc passed to ReadZ is the MEASURED DC voltage (read back from the
       instrument after settling), not a copy of dc_bias.  ReadZ uses vdc
       to normalise the AC voltage signal; using the wrong value shifts the
       computed impedance phase.
    """

    def __init__(
        self,
        output_csv: str = "all_eis_data.csv",
        pstat=None,
        pstat_label: str = "PSTAT",
        toolkit_name: str = "Gamry_EIS.py",
        auto_initialize: bool = False,
    ) -> None:
        self.output_csv = Path(output_csv)
        self.pstat = pstat
        self.pstat_label = pstat_label
        self.toolkit_name = toolkit_name
        self.auto_initialize = auto_initialize

        self._owns_pstat = pstat is None
        self._toolkit_initialized_here = False
        self.initialized = pstat is not None

        if self.initialized and self.pstat is not None and self.auto_initialize:
            self.pstat.set_ctrl_mode(tkp.PSTATMODE)
            initialize_pstat(self.pstat)

    def open(self) -> None:
        if self.initialized:
            return

        if self._owns_pstat:
            tkp.toolkitpy_init(self.toolkit_name)
            self._toolkit_initialized_here = True
            self.pstat = tkp.Pstat(self.pstat_label)

        if self.pstat is None:
            raise RuntimeError("Failed to obtain Pstat handle.")

        self.pstat.set_ctrl_mode(tkp.PSTATMODE)
        initialize_pstat(self.pstat)
        self.initialized = True

    def close(self) -> None:
        if not self.initialized and self.pstat is None:
            return

        try:
            if self.pstat is not None:
                try:
                    self.pstat.set_pos_feed_enable(False)
                    self.pstat.set_pos_feed_resistance(0.0)
                except Exception:
                    pass

                try:
                    self.pstat.set_cell(False)
                except Exception:
                    pass

        finally:
            if self._owns_pstat:
                try:
                    if self.pstat is not None:
                        del self.pstat
                except Exception:
                    pass

                if self._toolkit_initialized_here:
                    try:
                        tkp.toolkitpy_close()
                    except Exception:
                        pass

                self.pstat = None
                self._toolkit_initialized_here = False

            self.initialized = False

    def _build_reader(
        self,
        *,
        gain: float,
        zmod: float,
        inoise: float,
        vnoise: float,
        ienoise: float,
        idc: float,
        vdc: float,
        speed: int,
        cycles_delay: int,
        passes: int,
        drift_cor: bool,
    ) -> ReadZ:
        reader = ReadZ(self.pstat)
        reader.passes = passes
        reader.set_gain(gain)
        reader.set_zmod(zmod)
        reader.set_inoise(inoise)
        reader.set_vnoise(vnoise)
        reader.set_ienoise(ienoise)
        reader.set_idc(idc)
        reader.set_vdc(vdc)
        reader.set_speed(speed)
        reader.set_cycles_delay(cycles_delay)
        reader.set_drift_cor(drift_cor)
        return reader

    def _field_names(self) -> list[str]:
        return [
            "run_index",
            "ac_amplitude_requested_v",
            "frequency_requested_hz",
            "frequency_measured_hz",
            "vmod",
            "vphz",
            "vsig",
            "imod",
            "iphz",
            "isig",
            "vdc",
            "idc",
            "zfreq",
            "zreal",
            "zimag",
            "zsig",
            "zmod",
            "zphz",
            "phase_deg",
            "valid_ru_point",
            "ihi",
            "ihr",
            "vthd",
            "ithd",
            "zreal_drift",
            "zimag_drift",
            "zmod_drift",
            "zphz_drift",
            "gain",
            "ie_range",
        ]

    def _append_row(
        self,
        measurement_index: int,
        robot_pose: Sequence[float],
        row_data: dict,
    ) -> None:
        field_names = self._field_names()
        _ensure_csv_header(self.output_csv, field_names)

        x, y, z, r = _normalize_pose4(robot_pose)

        with open(self.output_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    measurement_index,
                    x,
                    y,
                    z,
                    r,
                    *[row_data.get(name, "") for name in field_names],
                ]
            )

    def _phase_deg(self, zreal, zimag, zphz=None):
        """
        Prefer the zphz value returned directly by ReadZ (hardware-computed).
        Fall back to atan2(zimag, zreal) only when zphz is unavailable.
        """
        if zphz is not None:
            try:
                return float(zphz)
            except Exception:
                pass

        if zreal is None or zimag is None:
            return None

        try:
            return math.degrees(math.atan2(float(zimag), float(zreal)))
        except Exception:
            return None

    def _is_acceptable_getru_point(
        self,
        zreal,
        phase_deg,
        *,
        max_phase_deg: float,
        max_ru_ohm: float = None,
    ) -> bool:
        """
        Accept only physically reasonable GetRu candidates.

        Official GetRu accepts a point from phase, not from an arbitrary
        maximum-resistance cut-off.  A positive finite real component is
        retained here only to prevent nonsensical output.
        """
        if zreal is None or phase_deg is None:
            return False

        try:
            zr = float(zreal)
            ph = float(phase_deg)
        except Exception:
            return False

        return math.isfinite(zr) and zr > 0.0 and abs(ph) < max_phase_deg

    def _make_row_data(
        self,
        reader,
        *,
        run_index: int,
        frequency_requested_hz: float,
        phase_deg,
        valid_ru_point: bool,
        ac_amplitude_requested_v=None,
    ) -> dict:
        return {
            "run_index": int(run_index),
            "ac_amplitude_requested_v": _safe_scalar(ac_amplitude_requested_v),
            "frequency_requested_hz": float(frequency_requested_hz),
            "frequency_measured_hz": _safe_scalar(getattr(reader, "zfreq", None)),
            "vmod": _safe_scalar(getattr(reader, "vmod", None)),
            "vphz": _safe_scalar(getattr(reader, "vphz", None)),
            "vsig": _safe_scalar(getattr(reader, "vsig", None)),
            "imod": _safe_scalar(getattr(reader, "imod", None)),
            "iphz": _safe_scalar(getattr(reader, "iphz", None)),
            "isig": _safe_scalar(getattr(reader, "isig", None)),
            "vdc": _safe_scalar(getattr(reader, "vdc", None)),
            "idc": _safe_scalar(getattr(reader, "idc", None)),
            "zfreq": _safe_scalar(getattr(reader, "zfreq", None)),
            "zreal": _safe_scalar(getattr(reader, "zreal", None)),
            "zimag": _safe_scalar(getattr(reader, "zimag", None)),
            "zsig": _safe_scalar(getattr(reader, "zsig", None)),
            "zmod": _safe_scalar(getattr(reader, "zmod", None)),
            "zphz": _safe_scalar(getattr(reader, "zphz", None)),
            "phase_deg": _safe_scalar(phase_deg),
            "valid_ru_point": int(valid_ru_point),
            "ihi": _safe_scalar(getattr(reader, "ihi", None)),
            "ihr": _safe_scalar(getattr(reader, "ihr", None)),
            "vthd": _safe_scalar(getattr(reader, "vthd", None)),
            "ithd": _safe_scalar(getattr(reader, "ithd", None)),
            "zreal_drift": _safe_scalar(getattr(reader, "zreal_drift", None)),
            "zimag_drift": _safe_scalar(getattr(reader, "zimag_drift", None)),
            "zmod_drift": _safe_scalar(getattr(reader, "zmod_drift", None)),
            "zphz_drift": _safe_scalar(getattr(reader, "zphz_drift", None)),
            "gain": _safe_scalar(getattr(reader, "gain", None)),
            "ie_range": _safe_scalar(getattr(reader, "ie_range", None)),
        }

    def _run_one_getru_scan(
        self,
        *,
        measurement_index: int,
        robot_pose: Sequence[float],
        run_index: int,
        frequencies: Sequence[float],
        reader: ReadZ,
        ac_amplitude: float,
        dc_bias: float,
        max_phase_deg_for_ru: float,
        max_ru_ohm_for_ru: float,
    ):
        """
        Follow Framework GetRu.exp selection semantics.  Scan high-to-low,
        accept immediately below 7.5 degrees, and if phase becomes worse keep
        the previous (more resistive) reading.  A result up to 45 degrees is
        returned as questionable, exactly as the Framework script reports it.

        Returns a 6-tuple:
            (selected_ru, selected_frequency, selected_phase,
             rows_written, best_candidate, z_pairs)

        z_pairs is a list of (zreal, zimag) floats for every successfully
        measured frequency point in this scan run.  Used by run_eis() to
        detect poor contact (all values large).
        """
        rows_written = 0
        best_candidate = None
        z_pairs: list[tuple[float, float]] = []
        ru_old = None
        phase_old = None
        freq_old = None

        for freq in frequencies:
            ok = reader.measure(
                freq=float(freq),
                ac_ampl_req=float(ac_amplitude),
                # dc_ampl_req tells ReadZ the DC bias that is already applied
                # to the cell.  It must match the voltage set on the potentiostat
                # before the scan started.  Do NOT use this to apply a new bias
                # mid-scan; the cell voltage is already set in run_eis().
                dc_ampl_req=float(dc_bias),
            )

            if not ok:
                print(
                    f"Skipping failed GetRu point: "
                    f"measurement_index={measurement_index}, "
                    f"run_index={run_index}, "
                    f"freq={float(freq)} Hz"
                )
                continue

            zreal_value = getattr(reader, "zreal", None)
            zimag_value = getattr(reader, "zimag", None)
            zphz_value = getattr(reader, "zphz", None)
            measured_freq = getattr(reader, "zfreq", None)

            phase_deg = self._phase_deg(zreal_value, zimag_value, zphz_value)

            try:
                zr = float(zreal_value)
            except Exception:
                zr = None

            # Collect this point for contact-quality check in run_eis().
            try:
                zr_f = float(zreal_value)
                try:
                    zi_f = float(zimag_value)
                except Exception:
                    zi_f = float("nan")
                z_pairs.append((zr_f, zi_f))
            except Exception:
                pass  # skip points where zreal itself is not numeric

            acceptable = self._is_acceptable_getru_point(
                zreal_value,
                phase_deg,
                max_phase_deg=max_phase_deg_for_ru,
                max_ru_ohm=None,
            )

            self._append_row(
                measurement_index=measurement_index,
                robot_pose=robot_pose,
                row_data=self._make_row_data(
                    reader,
                    run_index=run_index,
                    frequency_requested_hz=float(freq),
                    phase_deg=phase_deg,
                    valid_ru_point=acceptable,
                    ac_amplitude_requested_v=ac_amplitude,
                ),
            )
            rows_written += 1

            freq_used = float(measured_freq) if measured_freq is not None else float(freq)

            if acceptable:
                selected_ru = float(zreal_value)
                print(
                    f"GetRu accepted: "
                    f"freq={freq_used:.6g} Hz, "
                    f"Ru={selected_ru:.6g} ohm, "
                    f"phase={float(phase_deg):.3f} deg"
                )
                return selected_ru, freq_used, float(phase_deg), rows_written, best_candidate, z_pairs

            # Framework GetRu: once a usable point exists, stop when the next
            # lower-frequency point has worse phase and retain the old point.
            if zr is not None and zr > 0.0 and phase_deg is not None:
                phase_abs = abs(float(phase_deg))
                if phase_old is not None and phase_abs > phase_old:
                    print(
                        "GetRu phase became worse; accepting previous point "
                        "as a questionable Framework-style Ru."
                    )
                    if phase_old <= 45.0:
                        return ru_old, freq_old, phase_old, rows_written, best_candidate, z_pairs
                    return None, None, None, rows_written, best_candidate, z_pairs

                ru_old = zr
                phase_old = phase_abs
                freq_old = freq_used
                best_candidate = {
                    "ru": zr,
                    "frequency_hz": freq_used,
                    "phase_deg": float(phase_deg),
                    "phase_abs": phase_abs,
                }

            # Build rejection log for diagnostics.
            reject_reason = []

            if zr is None:
                reject_reason.append("zreal is not numeric")
            elif zr <= 0.0:
                reject_reason.append("zreal <= 0")
            if phase_deg is None:
                reject_reason.append("phase is missing")
            else:
                try:
                    if abs(float(phase_deg)) >= max_phase_deg_for_ru:
                        reject_reason.append(
                            f"abs(phase) >= {max_phase_deg_for_ru:.6g} deg"
                        )
                except Exception:
                    reject_reason.append("phase is not numeric")

            print(
                f"GetRu rejected: "
                f"freq={float(freq):.6g} Hz, "
                f"zreal={_safe_scalar(zreal_value)}, "
                f"zimag={_safe_scalar(zimag_value)}, "
                f"phase={phase_deg}, "
                f"reason={'; '.join(reject_reason)}"
            )

            if zr is None or zr <= 0.0 or phase_deg is None:
                continue

            try:
                phase_abs = abs(float(phase_deg))
            except Exception:
                continue

        # Framework reports 7.5..45 degrees as questionable rather than
        # discarding it.  Above 45 degrees it reports failure.
        if ru_old is not None and phase_old is not None and phase_old <= 45.0:
            return ru_old, freq_old, phase_old, rows_written, best_candidate, z_pairs

        return None, None, None, rows_written, best_candidate, z_pairs

    def run_eis(
        self,
        measurement_index: int,
        robot_pose: Sequence[float],
        frequencies=None,
        *,
        n_times: int = 1,
        ac_amplitude: float = 0.007,
        dc_bias: float = 0.0,
        gain: float = 1.0,
        zmod: float = 40.0,
        inoise: float = 0.0,
        vnoise: float = 0.0,
        ienoise: float = 0.0,
        idc=None,
        vdc=None,
        passes: int = 10,
        cycles_delay: int = 1,
        speed=None,
        drift_cor: bool = False,
        max_phase_deg_for_ru: float = 7.5,
        max_ru_ohm_for_ru: float = 10000.0,
        dc_settle_s: float = _DC_SETTLE_S,
        ru_fraction_for_pos_feed=None,
        enable_pos_feed=None,
        z_bad_threshold_ohm: float = 3000.0,
        min_bad_points: int = 3,
    ) -> dict:
        """
        Run GetRu-style Ru acquisition, append measured rows to CSV, and return Ru.

        DC bias sequencing (mirrors Gamry Framework GetRu behaviour)
        -------------------------------------------------------------
        1. Open cell.
        2. Apply dc_bias to the potentiostat output.
        3. Turn cell ON.
        4. Wait dc_settle_s for the electrode to reach steady state.
        5. Measure idc and vdc AT the operating potential (not at OCV).
        6. Feed those measured values to ReadZ so it normalises impedance
           against the correct DC operating point.

        Why this matters
        ----------------
        ReadZ uses vdc and idc internally to scale the AC response.  If you
        read idc before applying dc_bias (as the old code did), you get the
        open-circuit current (~0 A), which is wrong.  The resulting impedance
        phase is shifted so far that every frequency point fails the acceptance
        criteria and the EIS loop retries forever.

        Parameters
        ----------
        dc_bias : float
            DC potential applied to the cell (V).  Example: -0.5 V.
        idc : float or None
            Override for the DC current (A).  When None (default) the value is
            measured automatically AFTER dc_bias has been applied and settled.
        vdc : float or None
            Override for the DC voltage (V).  When None (default) the value is
            read back from the instrument AFTER settling, not copied from dc_bias.
        dc_settle_s : float
            Seconds to wait after turning the cell on before sampling idc/vdc.
            Default is 0.5 s.  Increase for sluggish electrodes.
        passes : int
            Number of AC cycles ReadZ averages per frequency point.  10 is
            sufficient for most conditions; 20-30 rarely improves accuracy and
            significantly increases the chance of a timeout or reject at high
            frequency under a non-zero DC bias.
        z_bad_threshold_ohm : float
            Contact-quality threshold (default 3000 Ω).  A measurement point is
            counted as "bad" when BOTH |zreal| and |zimag| exceed this value.
        min_bad_points : int
            Number of bad points (default 3), counted cumulatively across all
            runs of this measurement, that triggers an EISContactError.  This
            is NOT an "all points" rule — a poor-contact run typically contains
            a few small-impedance points at the highest frequencies, so only a
            count of clearly-bad points is reliable.  The caller should lift the
            probe and retry the same measurement index.
        """
        if not self.initialized or self.pstat is None:
            raise RuntimeError("GamryEIS is not open. Call open() first.")

        if n_times < 1:
            raise ValueError("n_times must be >= 1")

        if frequencies is None:
            frequencies = DEFAULT_GETRU_FREQUENCIES

        frequencies = [float(freq) for freq in frequencies]

        if len(frequencies) == 0:
            raise ValueError("frequencies must not be empty")

        if ru_fraction_for_pos_feed is not None and ru_fraction_for_pos_feed < 0:
            raise ValueError("ru_fraction_for_pos_feed must be >= 0")

        if max_phase_deg_for_ru <= 0:
            raise ValueError("max_phase_deg_for_ru must be > 0")

        if max_ru_ohm_for_ru <= 0:
            raise ValueError("max_ru_ohm_for_ru must be > 0")

        x_pose, y_pose, z_pose, r_pose = _normalize_pose4(robot_pose)
        pose4 = (x_pose, y_pose, z_pose, r_pose)

        speed_reader = ReadZ(self.pstat)
        if speed is None:
            speed = speed_reader.SPEED_NORM

        rows_written = 0
        selected_ru_values: list[float] = []
        selected_frequencies: list[float] = []
        selected_phases: list[float] = []
        best_candidates: list[dict] = []
        total_bad_points = 0  # cumulative points with |zreal|>th AND |zimag|>th

        try:
            # ----------------------------------------------------------------
            # Step 1: set ctrl mode but do NOT call initialize_pstat() here.
            # initialize_pstat() resets ie_range and CA/IE stability settings,
            # which causes hardware re-settling and is the root cause of
            # repeated EIS failures under non-zero dc_bias.  Hardware init
            # must only happen once per session (in open() or __init__).
            # ----------------------------------------------------------------
            self.pstat.set_ctrl_mode(tkp.PSTATMODE)

            # ----------------------------------------------------------------
            # Step 2: apply dc_bias BEFORE turning the cell on so the output
            # stage is already at the target potential when the cell connects.
            # This avoids a current/voltage transient that would shift idc/vdc.
            # ----------------------------------------------------------------
            self.pstat.set_voltage(float(dc_bias))

            # ----------------------------------------------------------------
            # Step 3: turn the cell on and wait for the electrode to settle at
            # the dc_bias operating point before sampling idc and vdc.
            # ----------------------------------------------------------------
            self.pstat.set_cell(True)

            if dc_settle_s > 0:
                print(
                    f"EIS: cell on, dc_bias={dc_bias:.4f} V, "
                    f"settling for {dc_settle_s:.2f} s..."
                )
                time.sleep(dc_settle_s)

            # ----------------------------------------------------------------
            # Step 4: sample idc and vdc AT the operating point.
            # These values describe the DC state the electrode is actually in
            # during the EIS sweep.  ReadZ uses them to normalise the AC
            # response; wrong values corrupt the computed impedance phase.
            #
            # vdc: read the actual cell voltage (may differ from dc_bias due
            #      to iR drop or compliance limits).  Do NOT use dc_bias as a
            #      substitute for vdc — that is the bug fixed here.
            # idc: read the steady-state faradaic current at the operating
            #      point.  At dc_bias = -0.5 V this is typically a significant
            #      negative value, not zero.
            # ----------------------------------------------------------------
            if idc is None:
                try:
                    self.pstat.set_ie_range_mode(True)   # auto-range briefly
                    idc = float(self.pstat.measure_i())
                    self.pstat.set_ie_range_mode(False)   # lock range for EIS
                    print(f"EIS: measured idc = {idc:.6g} A at dc_bias={dc_bias:.4f} V")
                except Exception:
                    try:
                        self.pstat.set_ie_range_mode(False)
                    except Exception:
                        pass
                    idc = 0.0
                    print("EIS: idc measurement failed, using 0.0 A")

            if vdc is None:
                try:
                    vdc = float(self.pstat.measure_v())
                    print(f"EIS: measured vdc = {vdc:.6g} V (dc_bias requested {dc_bias:.4f} V)")
                except Exception:
                    # Fall back to dc_bias only if the instrument read fails.
                    vdc = float(dc_bias)
                    print(
                        f"EIS: vdc measurement failed, falling back to dc_bias={dc_bias:.4f} V"
                    )

            for run_index in range(1, n_times + 1):
                reader = self._build_reader(
                    gain=gain,
                    zmod=zmod,
                    inoise=inoise,
                    vnoise=vnoise,
                    ienoise=ienoise,
                    # Pass the MEASURED idc/vdc, not dc_bias.
                    # ReadZ uses these to normalise the AC impedance internally.
                    idc=float(idc),
                    vdc=float(vdc),
                    speed=speed,
                    cycles_delay=cycles_delay,
                    passes=passes,
                    drift_cor=drift_cor,
                )

                # GetRu.exp performs one unreported acquisition at the
                # highest frequency to establish Gain/Zmod/scales before the
                # selection loop.  Keep the same ReadZ instance so the
                # adapted values carry into the official scan.
                print(
                    "EIS GetRu pre-read: {:.6g} Hz".format(frequencies[0])
                )
                reader.measure(
                    freq=float(frequencies[0]),
                    ac_ampl_req=float(ac_amplitude),
                    dc_ampl_req=float(dc_bias),
                )

                (
                    selected_ru,
                    selected_frequency,
                    selected_phase,
                    n_rows,
                    best_candidate,
                    z_pairs,
                ) = self._run_one_getru_scan(
                    measurement_index=measurement_index,
                    robot_pose=pose4,
                    run_index=run_index,
                    frequencies=frequencies,
                    reader=reader,
                    ac_amplitude=ac_amplitude,
                    # dc_bias here is passed to reader.measure() as dc_ampl_req,
                    # which tells ReadZ the DC bias already on the cell.
                    # It is NOT used to apply a new voltage — the cell is
                    # already at dc_bias from Step 2 above.
                    dc_bias=dc_bias,
                    max_phase_deg_for_ru=max_phase_deg_for_ru,
                    max_ru_ohm_for_ru=max_ru_ohm_for_ru,
                )

                rows_written += n_rows

                # ----------------------------------------------------------------
                # Bad-contact detection (count-based, NOT all-points-based).
                #
                # A measurement point is "bad" when BOTH |zreal| and |zimag|
                # exceed z_bad_threshold_ohm — the impedance is far too large
                # for a real contact (open / near-open cell).
                #
                # Poor-contact runs contain MANY such points but also a few
                # small-impedance points (typically at the highest frequencies),
                # so requiring EVERY point to be bad never fires.  Instead we
                # count bad points across the whole measurement and raise
                # EISContactError as soon as the count reaches min_bad_points.
                #
                # Good contact finds an acceptable Ru quickly and returns early,
                # so it never accumulates enough bad points to trigger.
                # ----------------------------------------------------------------
                run_bad_points = [
                    (zr, zi)
                    for zr, zi in z_pairs
                    if not (math.isnan(zr) or math.isnan(zi))
                    and abs(zr) > z_bad_threshold_ohm
                    and abs(zi) > z_bad_threshold_ohm
                ]
                total_bad_points += len(run_bad_points)

                print(
                    f"EIS run {run_index} contact check: {len(z_pairs)} points, "
                    f"{len(run_bad_points)} bad this run "
                    f"(|zreal|>{z_bad_threshold_ohm:.0f} AND |zimag|>{z_bad_threshold_ohm:.0f}), "
                    f"total_bad={total_bad_points}/{min_bad_points}"
                )

                if total_bad_points >= min_bad_points:
                    raise EISContactError(
                        f"Poor contact detected at measurement_index={measurement_index}: "
                        f"{total_bad_points} points had both |zreal| and |zimag| "
                        f"> {z_bad_threshold_ohm:.0f} Ω (threshold {min_bad_points}). "
                        f"Probe lift and re-approach required."
                    )

                if best_candidate is not None:
                    best_candidates.append(best_candidate)

                if selected_ru is None:
                    print(
                        f"No acceptable GetRu point in run {run_index}. "
                        "No valid reading at or below 45 degrees phase."
                    )
                    continue

                selected_ru_values.append(float(selected_ru))
                selected_frequencies.append(float(selected_frequency))
                selected_phases.append(float(selected_phase))

            if not selected_ru_values:
                if best_candidates:
                    best = min(best_candidates, key=lambda item: item["phase_abs"])

                    raise RuntimeError(
                        "No acceptable GetRu Ru found. "
                        f"Best rejected point was Ru={best['ru']:.6g} ohm at "
                        f"{best['frequency_hz']:.6g} Hz with "
                        f"phase={best['phase_deg']:.3f} deg. "
                        "Framework GetRu rejects phase above 45 degrees."
                    )

                raise RuntimeError(
                    "No valid Framework-style GetRu reading was found."
                )

            avg_ru = float(sum(selected_ru_values) / len(selected_ru_values))

            pos_feed_resistance = None
            pos_feed_enabled = None

            if ru_fraction_for_pos_feed is not None:
                pos_feed_resistance = float(ru_fraction_for_pos_feed * avg_ru)
                self.pstat.set_pos_feed_resistance(pos_feed_resistance)

                if enable_pos_feed is None:
                    enable_pos_feed = True

            if enable_pos_feed is not None:
                self.pstat.set_pos_feed_enable(bool(enable_pos_feed))
                pos_feed_enabled = bool(enable_pos_feed)
            else:
                try:
                    pos_feed_enabled = bool(self.pstat.pos_feed_enable())
                except Exception:
                    pos_feed_enabled = None

            return {
                "measurement_index": measurement_index,
                "robot_pose": [x_pose, y_pose, z_pose, r_pose],
                "csv_path": str(self.output_csv),
                "rows_written": rows_written,
                "n_times": n_times,
                "n_frequencies": len(frequencies),
                "avg_ru": avg_ru,
                "ru_points_used": len(selected_ru_values),
                "ru_frequencies_hz": selected_frequencies,
                "ru_phases_deg": selected_phases,
                "max_phase_deg_for_ru": max_phase_deg_for_ru,
                "max_ru_ohm_for_ru": max_ru_ohm_for_ru,
                "pos_feed_resistance": pos_feed_resistance,
                "pos_feed_enabled": pos_feed_enabled,
            }

        finally:
            try:
                self.pstat.set_cell(False)
            except Exception:
                pass
