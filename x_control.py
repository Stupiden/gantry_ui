"""Milestone 7: motion, force control, and Gamry connection status."""

import sys
import struct
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serial
import serial.tools.list_ports

from gamry_cv_manual import ManualCVWindow
from gamry_ca_manual import ManualCAWindow
from gamry_eis_manual import ManualEISWindow
from circular_scan_path import CircularScanPathWindow
from rectangular_scan_path import RectangularScanPathWindow
from xy_dry_run import XYDryRunWindow
from xy_press_cycle import XYPressCycleWindow

try:
    import toolkitpy as tkp
except ImportError:
    tkp = None


BAUD_RATE = 115200
CONTROLLER_ID = "MEGA_GANTRY_M6"
DEFAULT_SPEED_MM_S = 2.0
DEFAULT_PUMP_RPM = 2.0
SERIAL_POLL_MS = 20
PORT_REFRESH_MS = 1500
KEEPALIVE_MS = 250
HANDSHAKE_RETRY_MS = 500
CONTROLLER_BOOT_WAIT_MS = 2000
MAX_RX_BUFFER_BYTES = 4096


def parse_status_line(line):
    """Parse and expand one compact Milestone-2 STATUS line."""
    if not line.startswith("STATUS,"):
        raise ValueError("not a STATUS line")

    values = {}
    for item in line.split(",")[1:]:
        if "=" in item:
            key, value = item.split("=", 1)
            values[key.strip()] = value.strip()

    packed_limits = values.get("L")
    limit_keys = ("XMIN", "XPLUS", "YMIN", "YPLUS", "ZMIN", "ZPLUS")
    if packed_limits is None or len(packed_limits) != len(limit_keys):
        raise ValueError("invalid packed limit field")
    if any(state not in "01" for state in packed_limits):
        raise ValueError("invalid packed limit state")
    for key, state in zip(limit_keys, packed_limits):
        values[key] = state

    packed_motion = values.get("M")
    if packed_motion is None or len(packed_motion) != 3:
        raise ValueError("invalid packed motion field")
    if any(state not in "01" for state in packed_motion):
        raise ValueError("invalid packed motion state")
    for axis, state in zip(("X", "Y", "Z"), packed_motion):
        values["{}MOVING".format(axis)] = state

    packed_homed = values.get("H")
    if packed_homed is None or len(packed_homed) != 3:
        raise ValueError("invalid packed homed field")
    if any(state not in "01" for state in packed_homed):
        raise ValueError("invalid packed homed state")
    for axis, state in zip(("X", "Y", "Z"), packed_homed):
        values["{}HOMED".format(axis)] = state

    packed_alarms = values.get("A")
    if packed_alarms is None or len(packed_alarms) != 2:
        raise ValueError("invalid packed alarm field")
    if any(state not in "01" for state in packed_alarms):
        raise ValueError("invalid packed alarm state")
    values["Y1ALARM"] = packed_alarms[0]
    values["Y2ALARM"] = packed_alarms[1]

    packed_pumps = values.get("P")
    if packed_pumps is None or len(packed_pumps) != 2:
        raise ValueError("invalid packed pump field")
    if any(state not in "01" for state in packed_pumps):
        raise ValueError("invalid packed pump state")
    values["PUMP0RUNNING"] = packed_pumps[0]
    values["PUMP1RUNNING"] = packed_pumps[1]

    for key in (
        "X", "Y", "Z", "SX", "SY", "SZ", "PI", "PO", "R", "FR", "F",
        "PR", "PD"
    ):
        float(values[key])
    if values.get("LC") not in ("0", "1") or values.get("T") not in ("0", "1"):
        raise ValueError("invalid load-cell state")
    if values.get("PR") not in ("0", "1", "2"):
        raise ValueError("invalid press state")
    return values


def extract_complete_lines(buffer, chunk):
    """Return complete newline-terminated ASCII lines and the remainder."""
    buffer += chunk
    lines = []
    while b"\n" in buffer:
        raw_line, buffer = buffer.split(b"\n", 1)
        line = raw_line.rstrip(b"\r").decode("ascii", errors="replace").strip()
        if line:
            lines.append(line)
    if len(buffer) > MAX_RX_BUFFER_BYTES:
        buffer = b""
    return lines, buffer


class GantryMotionUI(tk.Tk):
    AXES = ("X", "Y", "Z")
    LIMITS = ("XMIN", "XPLUS", "YMIN", "YPLUS", "ZMIN", "ZPLUS")
    ALARMS = ("Y1ALARM", "Y2ALARM")

    def __init__(self):
        tk.Tk.__init__(self)
        self.title("Gantry Motion Control — Milestone 7")
        self.geometry("900x740")
        self.minsize(800, 680)
        self.option_add("*Font", ("Segoe UI", 9))

        self.serial_port = None
        self.controller_ready = False
        self.jog_axis = None
        self.last_handshake_attempt = 0.0
        self.serial_opened_at = 0.0
        self.handshake_started = False
        self.last_keepalive = 0.0
        self.last_ports = []
        self.rx_buffer = b""
        self.gamry_pstat = None
        self.gamry_toolkit_initialized = False
        self.cv_window = None
        self.ca_window = None
        self.eis_window = None
        self.circular_path_window = None
        self.rectangular_path_window = None
        self.xy_dry_run_window = None
        self.xy_press_cycle_window = None
        self.gamry_measurement_owner = None

        self.port_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="● DISCONNECTED")
        self.gamry_device_var = tk.StringVar(value="PSTAT")
        self.gamry_status_var = tk.StringVar(value="● NOT DETECTED")
        self.status_var = tk.StringVar(value="Ready")
        self.speed_var = tk.DoubleVar(value=DEFAULT_SPEED_MM_S)
        self.move_axis_var = tk.StringVar(value="X")
        self.distance_var = tk.DoubleVar(value=1.0)
        self.position_vars = {
            axis: tk.StringVar(value="0.000 mm") for axis in self.AXES
        }
        self.scan_position_vars = {
            axis: tk.StringVar(value="0.000 mm") for axis in self.AXES
        }
        self.homed_vars = {
            axis: tk.StringVar(value="NOT HOMED") for axis in self.AXES
        }
        self.pump_speed_vars = [
            tk.DoubleVar(value=DEFAULT_PUMP_RPM),
            tk.DoubleVar(value=DEFAULT_PUMP_RPM),
        ]
        self.pump_status_vars = [
            tk.StringVar(value="STOPPED"),
            tk.StringVar(value="STOPPED"),
        ]
        self.load_cell_raw_var = tk.StringVar(value="—")
        self.load_cell_filtered_var = tk.StringVar(value="—")
        self.load_cell_force_var = tk.StringVar(value="— g")
        self.load_cell_status_var = tk.StringVar(value="WAITING")
        self.press_force_var = tk.DoubleVar(value=100.0)
        self.press_travel_var = tk.DoubleVar(value=20.0)
        self.press_speed_var = tk.DoubleVar(value=0.25)
        self.press_status_var = tk.StringVar(value="IDLE")
        self.limit_vars = {
            name: tk.StringVar(value="SAFE") for name in self.LIMITS
        }
        self.moving_vars = {
            axis: tk.StringVar(value="IDLE") for axis in self.AXES
        }

        self.motion_widgets = []
        self.limit_labels = {}
        self.alarm_labels = {}
        self.build_ui()
        self.set_motion_enabled(False)
        self.refresh_ports()

        self.bind_all("<ButtonRelease-1>", self.on_global_button_release, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(SERIAL_POLL_MS, self.service_serial)
        self.after(PORT_REFRESH_MS, self.periodic_port_refresh)

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        body = ttk.Frame(self, padding=(10, 4, 10, 6))
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self.build_connection_panel(body)
        self.build_position_panel(body)
        self.build_jog_panel(body)
        self.build_home_panel(body)
        self.build_relative_move_panel(body)
        self.build_limit_panel(body)
        self.build_load_cell_panel(body)
        self.build_pump_panel(body)
        self.build_alarm_panel(body)
        self.build_stop_panel(body)

    def build_connection_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Connections", padding=4)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Serial port:").grid(row=0, column=0, padx=(0, 8))
        self.port_box = ttk.Combobox(
            frame, textvariable=self.port_var, state="readonly", width=42
        )
        self.port_box.grid(row=0, column=1, sticky="ew")

        self.refresh_button = ttk.Button(frame, text="REFRESH", command=self.refresh_ports)
        self.refresh_button.grid(row=0, column=2, padx=8)

        self.connect_button = ttk.Button(
            frame, text="CONNECT", command=self.toggle_connection
        )
        self.connect_button.grid(row=0, column=3)

        self.connection_label = tk.Label(
            frame,
            textvariable=self.connection_var,
            fg="#b00020",
            font=("Segoe UI", 10, "bold"),
        )
        self.connection_label.grid(row=0, column=4, padx=(12, 0))

        ttk.Label(frame, text="Gamry:").grid(
            row=1, column=0, padx=(0, 8), pady=(3, 0)
        )
        self.gamry_device_box = ttk.Combobox(
            frame,
            textvariable=self.gamry_device_var,
            values=("PSTAT",),
            state="readonly",
            width=42,
        )
        self.gamry_device_box.grid(
            row=1, column=1, sticky="ew", pady=(3, 0)
        )
        self.gamry_detect_button = ttk.Button(
            frame, text="DETECT", command=self.detect_gamry
        )
        self.gamry_detect_button.grid(
            row=1, column=2, padx=8, pady=(3, 0)
        )
        self.gamry_connect_button = ttk.Button(
            frame, text="CONNECT", command=self.toggle_gamry_connection
        )
        self.gamry_connect_button.grid(row=1, column=3, pady=(3, 0))
        self.gamry_status_label = tk.Label(
            frame,
            textvariable=self.gamry_status_var,
            fg="#666666",
            font=("Segoe UI", 9, "bold"),
        )
        self.gamry_status_label.grid(row=1, column=4, padx=(12, 0), pady=(3, 0))
        self.cv_button = ttk.Button(
            frame, text="MANUAL CV", command=self.open_cv_window, state="disabled"
        )
        self.cv_button.grid(row=1, column=5, padx=(8, 0), pady=(3, 0))
        self.ca_button = ttk.Button(
            frame, text="MANUAL CA", command=self.open_ca_window, state="disabled"
        )
        self.ca_button.grid(row=1, column=6, padx=(5, 0), pady=(3, 0))
        self.eis_button = ttk.Button(
            frame, text="MANUAL EIS", command=self.open_eis_window, state="disabled"
        )
        self.eis_button.grid(row=1, column=7, padx=(5, 0), pady=(3, 0))

    def ensure_gamry_environment(self):
        if tkp is None:
            raise RuntimeError("Gamry ToolkitPy is not installed")
        if struct.calcsize("P") * 8 != 32:
            raise RuntimeError("Gamry requires the 32-bit Python 3.7.9 environment")

    def initialize_gamry_toolkit(self):
        self.ensure_gamry_environment()
        if not self.gamry_toolkit_initialized:
            tkp.toolkitpy_init("gantry_ui.py")
            self.gamry_toolkit_initialized = True

    def close_gamry_toolkit(self):
        if self.gamry_toolkit_initialized:
            tkp.toolkitpy_close()
            self.gamry_toolkit_initialized = False

    def detect_gamry(self):
        if self.gamry_pstat is not None:
            self.gamry_status_var.set("● DISCONNECT BEFORE DETECT")
            self.gamry_status_label.configure(fg="#9a6700")
            return
        detected_pstat = None
        try:
            self.initialize_gamry_toolkit()
            detected_pstat = tkp.Pstat("PSTAT")
            if not tkp.pstat_is_valid(detected_pstat):
                raise RuntimeError("PSTAT handle is not valid")
        except Exception as error:
            self.gamry_status_var.set("● DETECTION ERROR")
            self.gamry_status_label.configure(fg="#b00020")
            self.status_var.set("Gamry detection failed: {}".format(error))
            detected_pstat = None
            try:
                self.close_gamry_toolkit()
            except Exception:
                pass
            return
        finally:
            detected_pstat = None

        self.close_gamry_toolkit()
        self.gamry_status_var.set("● PSTAT DETECTED")
        self.gamry_status_label.configure(fg="#187a2f")
        self.status_var.set("Gamry PSTAT detected with ToolkitPy")

    def toggle_gamry_connection(self):
        if self.gamry_pstat is not None:
            self.disconnect_gamry()
        else:
            self.connect_gamry()

    def connect_gamry(self):
        try:
            self.initialize_gamry_toolkit()
            self.gamry_pstat = tkp.Pstat("PSTAT")
            if not tkp.pstat_is_valid(self.gamry_pstat):
                raise RuntimeError("PSTAT handle is not valid")
        except Exception as error:
            self.gamry_pstat = None
            try:
                self.close_gamry_toolkit()
            except Exception:
                pass
            self.gamry_status_var.set("● CONNECTION ERROR")
            self.gamry_status_label.configure(fg="#b00020")
            self.status_var.set("Gamry connection failed: {}".format(error))
            return

        self.gamry_connect_button.configure(text="DISCONNECT")
        self.gamry_detect_button.configure(state="disabled")
        self.gamry_device_box.configure(state="disabled")
        self.cv_button.configure(state="normal")
        self.ca_button.configure(state="normal")
        self.eis_button.configure(state="normal")
        self.gamry_status_var.set("● CONNECTED")
        self.gamry_status_label.configure(fg="#187a2f")
        self.status_var.set("Gamry connected: PSTAT")

    def disconnect_gamry(self):
        if self.cv_window is not None and self.cv_window.running:
            self.status_var.set("Stop CV before disconnecting Gamry")
            return
        if self.ca_window is not None and self.ca_window.running:
            self.status_var.set("Stop CA before disconnecting Gamry")
            return
        if self.eis_window is not None and self.eis_window.running:
            self.status_var.set("Stop EIS before disconnecting Gamry")
            return
        if self.gamry_pstat is not None:
            try:
                self.gamry_pstat.set_cell(False)
            except Exception as error:
                self.status_var.set("Gamry close warning: {}".format(error))
        self.gamry_pstat = None
        try:
            self.close_gamry_toolkit()
        except Exception as error:
            self.status_var.set("Gamry ToolkitPy close warning: {}".format(error))
        self.gamry_connect_button.configure(text="CONNECT")
        self.gamry_detect_button.configure(state="normal")
        self.gamry_device_box.configure(state="readonly")
        self.cv_button.configure(state="disabled")
        self.ca_button.configure(state="disabled")
        self.eis_button.configure(state="disabled")
        self.gamry_status_var.set("● DISCONNECTED")
        self.gamry_status_label.configure(fg="#666666")

    def current_machine_position(self):
        positions = []
        for axis in self.AXES:
            text = self.position_vars[axis].get().split()[0]
            positions.append(float(text))
        return tuple(positions)

    def current_scan_xy(self):
        return tuple(
            float(self.scan_position_vars[axis].get().split()[0])
            for axis in ("X", "Y")
        )

    def open_cv_window(self):
        if self.gamry_pstat is None:
            self.status_var.set("Connect Gamry before opening manual CV")
            return
        if self.cv_window is not None and self.cv_window.winfo_exists():
            self.cv_window.lift()
            self.cv_window.focus_force()
            return
        self.cv_window = ManualCVWindow(
            self,
            self.gamry_pstat,
            tkp,
            self.current_machine_position,
            self.claim_gamry_measurement,
            self.release_gamry_measurement,
        )

    def open_ca_window(self):
        if self.gamry_pstat is None:
            self.status_var.set("Connect Gamry before opening manual CA")
            return
        if self.ca_window is not None and self.ca_window.winfo_exists():
            self.ca_window.lift()
            self.ca_window.focus_force()
            return
        self.ca_window = ManualCAWindow(
            self,
            self.gamry_pstat,
            tkp,
            self.current_machine_position,
            self.claim_gamry_measurement,
            self.release_gamry_measurement,
        )

    def open_eis_window(self):
        if self.gamry_pstat is None:
            self.status_var.set("Connect Gamry before opening manual EIS")
            return
        if self.eis_window is not None and self.eis_window.winfo_exists():
            self.eis_window.lift()
            self.eis_window.focus_force()
            return
        self.eis_window = ManualEISWindow(
            self,
            self.gamry_pstat,
            self.current_machine_position,
            self.claim_gamry_measurement,
            self.release_gamry_measurement,
        )

    def claim_gamry_measurement(self, owner):
        if self.gamry_measurement_owner is not None:
            self.status_var.set(
                "Gamry is busy with {}".format(self.gamry_measurement_owner)
            )
            return False
        self.gamry_measurement_owner = owner
        return True

    def release_gamry_measurement(self, owner):
        if self.gamry_measurement_owner == owner:
            self.gamry_measurement_owner = None

    def build_position_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Coordinate system", padding=4)
        frame.grid(row=1, column=0, sticky="ew", pady=2)
        frame.columnconfigure(0, weight=0)
        for column in range(1, 4):
            frame.columnconfigure(column, weight=1)

        ttk.Label(frame, text="Machine", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, sticky="w", padx=(0, 12)
        )
        ttk.Label(frame, text="Scan", font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, sticky="w", padx=(0, 12)
        )

        for column, axis in enumerate(self.AXES, start=1):
            ttk.Label(
                frame, text=axis, anchor="center", font=("Segoe UI", 11, "bold")
            ).grid(row=0, column=column, sticky="ew")
            ttk.Label(
                frame,
                textvariable=self.position_vars[axis],
                anchor="center",
                font=("Consolas", 14, "bold"),
            ).grid(row=1, column=column, sticky="ew", pady=(2, 0))
            ttk.Label(
                frame,
                textvariable=self.scan_position_vars[axis],
                anchor="center",
                font=("Consolas", 13),
            ).grid(row=2, column=column, sticky="ew")
            ttk.Label(
                frame, textvariable=self.homed_vars[axis], anchor="center"
            ).grid(row=3, column=column, sticky="ew", pady=(3, 0))
            ttk.Label(
                frame, textvariable=self.moving_vars[axis], anchor="center"
            ).grid(row=4, column=column, sticky="ew")

    def build_jog_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Jog — hold to move", padding=4)
        frame.grid(row=2, column=0, sticky="ew", pady=2)
        for column in range(3):
            frame.columnconfigure(column, weight=1)

        for column, axis in enumerate(self.AXES):
            axis_frame = ttk.Frame(frame)
            axis_frame.grid(row=0, column=column, sticky="ew", padx=6)
            axis_frame.columnconfigure(0, weight=1)
            axis_frame.columnconfigure(1, weight=1)
            ttk.Label(axis_frame, text=axis, anchor="center").grid(
                row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4)
            )

            minus = ttk.Button(axis_frame, text="{}−".format(axis))
            plus = ttk.Button(axis_frame, text="{}+".format(axis))
            minus.grid(row=1, column=0, sticky="ew", padx=(0, 3))
            plus.grid(row=1, column=1, sticky="ew", padx=(3, 0))
            minus.bind(
                "<ButtonPress-1>",
                lambda event, selected_axis=axis: self.start_jog(selected_axis, -1),
            )
            minus.bind("<ButtonRelease-1>", self.stop_jog)
            plus.bind(
                "<ButtonPress-1>",
                lambda event, selected_axis=axis: self.start_jog(selected_axis, 1),
            )
            plus.bind("<ButtonRelease-1>", self.stop_jog)
            self.motion_widgets.extend((minus, plus))

    def build_relative_move_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Relative move", padding=4)
        frame.grid(row=4, column=0, sticky="ew", pady=2)
        frame.columnconfigure(3, weight=1)

        ttk.Label(frame, text="Axis:").grid(row=0, column=0, padx=(0, 6))
        axis_box = ttk.Combobox(
            frame,
            textvariable=self.move_axis_var,
            values=self.AXES,
            state="readonly",
            width=5,
        )
        axis_box.grid(row=0, column=1, padx=(0, 16))

        ttk.Label(frame, text="Distance:").grid(row=0, column=2, padx=(0, 6))
        distance_box = ttk.Spinbox(
            frame,
            from_=-1000.0,
            to=1000.0,
            increment=0.1,
            textvariable=self.distance_var,
            width=12,
        )
        distance_box.grid(row=0, column=3, sticky="w")
        ttk.Label(frame, text="mm").grid(row=0, column=4, padx=(4, 16))

        move_button = ttk.Button(frame, text="MOVE", command=self.move_relative)
        move_button.grid(row=0, column=5, padx=(0, 8))

        ttk.Label(frame, text="Speed:").grid(row=0, column=6, padx=(8, 6))
        speed_box = ttk.Spinbox(
            frame,
            from_=0.05,
            to=5.0,
            increment=0.05,
            textvariable=self.speed_var,
            width=9,
            command=self.apply_speed,
        )
        speed_box.grid(row=0, column=7)
        speed_box.bind("<Return>", lambda event: self.apply_speed())
        speed_box.bind("<FocusOut>", lambda event: self.apply_speed())
        ttk.Label(frame, text="mm/s").grid(row=0, column=8, padx=(4, 0))

        self.motion_widgets.extend((axis_box, distance_box, move_button, speed_box))

    def build_home_panel(self, parent):
        frame = ttk.LabelFrame(
            parent, text="Homing / scan coordinate", padding=4
        )
        frame.grid(row=3, column=0, sticky="ew", pady=2)
        for column in range(4):
            frame.columnconfigure(column, weight=1)

        for column, axis in enumerate(self.AXES):
            button = ttk.Button(
                frame,
                text="HOME {}".format(axis),
                command=lambda selected_axis=axis: self.home_axis(selected_axis),
            )
            button.grid(row=0, column=column, sticky="ew", padx=4)
            self.motion_widgets.append(button)

        scan_zero_button = ttk.Button(
            frame,
            text="SET CURRENT AS SCAN ZERO",
            command=self.set_scan_zero,
        )
        scan_zero_button.grid(row=0, column=3, sticky="ew", padx=4)
        self.motion_widgets.append(scan_zero_button)

        circular_path_button = ttk.Button(
            frame,
            text="CIRCULAR PATH PREVIEW",
            command=self.open_circular_path_window,
        )
        circular_path_button.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 0))

        rectangular_path_button = ttk.Button(
            frame,
            text="RECTANGULAR PATH PREVIEW",
            command=self.open_rectangular_path_window,
        )
        rectangular_path_button.grid(
            row=1, column=2, columnspan=2, sticky="ew", padx=4, pady=(4, 0)
        )

    def open_circular_path_window(self):
        if self.circular_path_window is not None and self.circular_path_window.winfo_exists():
            self.circular_path_window.lift()
            self.circular_path_window.focus_force()
            return
        self.circular_path_window = CircularScanPathWindow(
            self, self.open_xy_dry_run, self.open_xy_press_cycle
        )

    def open_rectangular_path_window(self):
        if self.rectangular_path_window is not None and self.rectangular_path_window.winfo_exists():
            self.rectangular_path_window.lift()
            self.rectangular_path_window.focus_force()
            return
        self.rectangular_path_window = RectangularScanPathWindow(
            self, self.open_xy_dry_run, self.open_xy_press_cycle
        )

    def xy_dry_run_guard(self):
        if not self.controller_ready:
            return False, "Connect the Mega controller first"
        if (
            self.xy_dry_run_window is not None
            and self.xy_dry_run_window.winfo_exists()
            and self.xy_dry_run_window.running
        ):
            return False, "An XY dry run is already running"
        if (
            self.xy_press_cycle_window is not None
            and self.xy_press_cycle_window.winfo_exists()
            and self.xy_press_cycle_window.running
        ):
            return False, "An XY press cycle is already running"
        if self.homed_vars["X"].get() != "HOMED" or self.homed_vars["Y"].get() != "HOMED":
            return False, "Home X and Y before an automated dry run"
        if any(value.get() != "STOPPED" for value in self.pump_status_vars):
            return False, "Stop both pumps before an XY dry run"
        if self.gamry_measurement_owner is not None:
            return False, "Stop the Gamry measurement before an XY dry run"
        if (
            self.moving_vars["Z"].get() != "IDLE"
            or self.press_status_var.get().startswith("PRESSING")
            or self.press_status_var.get() == "LIFTING"
        ):
            return False, "Press/Lift must be idle before an XY dry run"
        return True, ""

    def lock_for_xy_dry_run(self, locked):
        self.set_motion_enabled(not locked and self.controller_ready)

    def open_xy_dry_run(self, points, path_name):
        if self.xy_dry_run_window is not None and self.xy_dry_run_window.winfo_exists():
            if self.xy_dry_run_window.running:
                self.xy_dry_run_window.lift()
                self.xy_dry_run_window.focus_force()
                return
            self.xy_dry_run_window.destroy()
        self.xy_dry_run_window = XYDryRunWindow(
            self, points, path_name, self.send_command, self.current_scan_xy,
            self.xy_dry_run_guard, self.lock_for_xy_dry_run,
        )

    def open_xy_press_cycle(self, points, path_name):
        if self.xy_press_cycle_window is not None and self.xy_press_cycle_window.winfo_exists():
            if self.xy_press_cycle_window.running:
                self.xy_press_cycle_window.lift()
                self.xy_press_cycle_window.focus_force()
                return
            self.xy_press_cycle_window.destroy()
        self.xy_press_cycle_window = XYPressCycleWindow(
            self, points, path_name, self.send_command, self.current_scan_xy,
            self.xy_dry_run_guard, self.lock_for_xy_dry_run,
        )

    def build_limit_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Limit switches", padding=4)
        frame.grid(row=5, column=0, sticky="ew", pady=2)
        for column in range(6):
            frame.columnconfigure(column, weight=1)

        labels = (
            ("XMIN", "X−"),
            ("XPLUS", "X+"),
            ("YMIN", "Y−"),
            ("YPLUS", "Y+"),
            ("ZMIN", "Z−"),
            ("ZPLUS", "Z+"),
        )
        for column, (key, display_name) in enumerate(labels):
            label = tk.Label(
                frame,
                text="{} SAFE".format(display_name),
                fg="#187a2f",
                font=("Segoe UI", 9, "bold"),
                padx=5,
                pady=4,
            )
            label.grid(row=0, column=column, sticky="ew")
            self.limit_labels[key] = (label, display_name)

    def build_stop_panel(self, parent):
        frame = ttk.Frame(parent)
        frame.grid(row=9, column=0, sticky="ew", pady=(2, 0))
        frame.columnconfigure(0, weight=1)

        self.stop_button = tk.Button(
            frame,
            text="STOP",
            command=self.emergency_stop,
            bg="#b00020",
            fg="white",
            activebackground="#8b0018",
            activeforeground="white",
            font=("Segoe UI", 15, "bold"),
            height=1,
            state=tk.DISABLED,
        )
        self.stop_button.grid(row=0, column=0, sticky="ew")

        ttk.Label(frame, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, sticky="ew", pady=(4, 0)
        )

    def build_alarm_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Y driver alarms", padding=4)
        frame.grid(row=8, column=0, sticky="ew", pady=2)
        for column in range(2):
            frame.columnconfigure(column, weight=1)

        for column, key in enumerate(self.ALARMS):
            display_name = "Y1" if key == "Y1ALARM" else "Y2"
            label = tk.Label(
                frame,
                text="{} NORMAL".format(display_name),
                fg="#187a2f",
                font=("Segoe UI", 9, "bold"),
                padx=5,
                pady=4,
            )
            label.grid(row=0, column=column, sticky="ew")
            self.alarm_labels[key] = (label, display_name)

    def build_pump_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Peristaltic pumps — clockwise", padding=4)
        frame.grid(row=7, column=0, sticky="ew", pady=2)
        for column in range(2):
            frame.columnconfigure(column, weight=1)

        for pump_id, name in enumerate(("Pump IN", "Pump OUT")):
            pump_frame = ttk.Frame(frame)
            pump_frame.grid(row=0, column=pump_id, sticky="ew", padx=6)
            pump_frame.columnconfigure(1, weight=1)

            ttk.Label(
                pump_frame, text=name, font=("Segoe UI", 9, "bold")
            ).grid(row=0, column=0, padx=(0, 8))
            ttk.Label(
                pump_frame, textvariable=self.pump_status_vars[pump_id]
            ).grid(row=0, column=1, sticky="w")

            ttk.Label(pump_frame, text="Speed:").grid(row=1, column=0, pady=(3, 0))
            speed_box = ttk.Spinbox(
                pump_frame,
                from_=0.1,
                to=20.0,
                increment=0.1,
                textvariable=self.pump_speed_vars[pump_id],
                width=8,
                command=lambda selected=pump_id: self.apply_pump_speed(selected),
            )
            speed_box.grid(row=1, column=1, sticky="w", pady=(3, 0))
            speed_box.bind(
                "<Return>",
                lambda event, selected=pump_id: self.apply_pump_speed(selected),
            )
            ttk.Label(pump_frame, text="RPM").grid(
                row=1, column=1, sticky="w", padx=(70, 0), pady=(3, 0)
            )

            start_button = ttk.Button(
                pump_frame,
                text="START",
                command=lambda selected=pump_id: self.start_pump(selected),
            )
            stop_button = ttk.Button(
                pump_frame,
                text="STOP",
                command=lambda selected=pump_id: self.stop_pump(selected),
            )
            start_button.grid(row=2, column=0, sticky="ew", pady=(3, 0), padx=(0, 4))
            stop_button.grid(row=2, column=1, sticky="ew", pady=(3, 0), padx=(4, 0))
            self.motion_widgets.extend((speed_box, start_button, stop_button))

    def build_load_cell_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="S-type load cell", padding=4)
        frame.grid(row=6, column=0, sticky="ew", pady=2)
        for column in range(8):
            frame.columnconfigure(column, weight=1 if column in (1, 3, 5) else 0)

        ttk.Label(frame, text="RAW:").grid(row=0, column=0, padx=(0, 4))
        ttk.Label(frame, textvariable=self.load_cell_raw_var).grid(
            row=0, column=1, sticky="w", padx=(0, 12)
        )
        ttk.Label(frame, text="Filtered RAW:").grid(row=0, column=2, padx=(0, 4))
        ttk.Label(frame, textvariable=self.load_cell_filtered_var).grid(
            row=0, column=3, sticky="w", padx=(0, 12)
        )
        ttk.Label(frame, text="Force:").grid(row=0, column=4, padx=(0, 4))
        ttk.Label(
            frame,
            textvariable=self.load_cell_force_var,
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=5, sticky="w", padx=(0, 12))
        ttk.Label(frame, textvariable=self.load_cell_status_var).grid(
            row=0, column=6, padx=(0, 8)
        )
        tare_button = ttk.Button(frame, text="TARE", command=self.tare_load_cell)
        tare_button.grid(row=0, column=7)
        self.motion_widgets.append(tare_button)

        ttk.Label(frame, text="Press force:").grid(row=1, column=0, pady=(5, 0))
        force_box = ttk.Spinbox(
            frame, from_=0.1, to=5000.0, increment=1.0,
            textvariable=self.press_force_var, width=7
        )
        force_box.grid(row=1, column=1, sticky="w", pady=(5, 0))
        ttk.Label(frame, text="Max travel:").grid(row=1, column=2, pady=(5, 0))
        travel_box = ttk.Spinbox(
            frame, from_=0.1, to=50.0, increment=0.5,
            textvariable=self.press_travel_var, width=7
        )
        travel_box.grid(row=1, column=3, sticky="w", pady=(5, 0))
        ttk.Label(frame, text="Speed:").grid(row=1, column=4, pady=(5, 0))
        speed_box = ttk.Spinbox(
            frame, from_=0.05, to=0.50, increment=0.05,
            textvariable=self.press_speed_var, width=6
        )
        speed_box.grid(row=1, column=5, sticky="w", pady=(5, 0))
        press_button = ttk.Button(frame, text="PRESS", command=self.start_press)
        press_button.grid(row=1, column=6, pady=(5, 0), padx=(0, 4))
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=1, column=7, pady=(5, 0))
        press_stop_button = ttk.Button(
            button_frame, text="STOP", command=self.stop_press, width=6
        )
        lift_button = ttk.Button(
            button_frame, text="LIFT", command=self.lift_press, width=6
        )
        press_stop_button.grid(row=0, column=0, padx=(0, 3))
        lift_button.grid(row=0, column=1)
        ttk.Label(frame, textvariable=self.press_status_var).grid(
            row=2, column=0, columnspan=8, sticky="w", pady=(4, 0)
        )
        self.motion_widgets.extend(
            (force_box, travel_box, speed_box, press_button,
             press_stop_button, lift_button)
        )

    def refresh_ports(self):
        if self.is_serial_open():
            return
        ports = list(serial.tools.list_ports.comports())
        devices = [port.device for port in ports]
        labels = ["{} — {}".format(port.device, port.description) for port in ports]
        if devices == self.last_ports:
            return
        selected = self.port_var.get().split(" — ", 1)[0]
        self.last_ports = devices
        self.port_box["values"] = labels
        if selected in devices:
            self.port_box.current(devices.index(selected))
        elif labels:
            self.port_box.current(0)
        else:
            self.port_var.set("")

    def periodic_port_refresh(self):
        self.refresh_ports()
        self.after(PORT_REFRESH_MS, self.periodic_port_refresh)

    def toggle_connection(self):
        if self.is_serial_open():
            self.disconnect_serial("Disconnected")
        else:
            self.connect_serial()

    def connect_serial(self):
        port = self.port_var.get().split(" — ", 1)[0]
        if not port:
            messagebox.warning("Serial", "No serial port selected.")
            return
        try:
            self.serial_port = serial.Serial(
                port=port,
                baudrate=BAUD_RATE,
                timeout=0,
                write_timeout=0.5,
            )
            self.serial_port.reset_input_buffer()
        except (OSError, serial.SerialException) as error:
            self.serial_port = None
            messagebox.showerror("Serial error", str(error))
            return

        self.controller_ready = False
        self.jog_axis = None
        self.rx_buffer = b""
        self.last_handshake_attempt = 0.0
        self.serial_opened_at = time.monotonic()
        self.handshake_started = False
        self.connect_button.configure(text="DISCONNECT")
        self.port_box.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.stop_button.configure(state=tk.NORMAL)
        self.connection_var.set("● CONNECTING")
        self.connection_label.configure(fg="#9a6700")
        self.status_var.set("Waiting for {}...".format(CONTROLLER_ID))

    def disconnect_serial(self, reason):
        if self.xy_dry_run_window is not None and self.xy_dry_run_window.winfo_exists():
            self.xy_dry_run_window.emergency_stopped()
        if self.xy_press_cycle_window is not None and self.xy_press_cycle_window.winfo_exists():
            self.xy_press_cycle_window.emergency_stopped()
        if self.is_serial_open():
            try:
                self.send_command("STOP", require_ready=False)
            except Exception:
                pass
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
        self.serial_port = None
        self.controller_ready = False
        self.jog_axis = None
        self.rx_buffer = b""
        self.serial_opened_at = 0.0
        self.handshake_started = False
        self.set_motion_enabled(False)
        self.stop_button.configure(state=tk.DISABLED)
        self.connect_button.configure(text="CONNECT")
        self.port_box.configure(state="readonly")
        self.refresh_button.configure(state="normal")
        self.connection_var.set("● DISCONNECTED")
        self.connection_label.configure(fg="#b00020")
        self.status_var.set(reason)
        for axis in self.AXES:
            self.moving_vars[axis].set("IDLE")
        for pump_status in self.pump_status_vars:
            pump_status.set("STOPPED")
        self.load_cell_raw_var.set("—")
        self.load_cell_filtered_var.set("—")
        self.load_cell_force_var.set("— g")
        self.load_cell_status_var.set("WAITING")
        self.press_status_var.set("IDLE")

    def is_serial_open(self):
        return self.serial_port is not None and self.serial_port.is_open

    def set_motion_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in self.motion_widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                widget.configure(state=state)

    def send_command(self, command, require_ready=True):
        if not self.is_serial_open():
            return False
        if require_ready and not self.controller_ready:
            self.status_var.set("Controller handshake is not complete")
            return False
        try:
            self.serial_port.write((command.strip() + "\n").encode("ascii"))
            self.serial_port.flush()
            return True
        except (OSError, serial.SerialException) as error:
            self.disconnect_serial("Serial write failed: {}".format(error))
            return False

    def service_serial(self):
        if self.is_serial_open():
            try:
                waiting = self.serial_port.in_waiting
                if waiting:
                    chunk = self.serial_port.read(waiting)
                    lines, self.rx_buffer = extract_complete_lines(
                        self.rx_buffer, chunk
                    )
                    for line in lines:
                        self.handle_serial_line(line)
            except (OSError, serial.SerialException) as error:
                self.disconnect_serial("Serial read failed: {}".format(error))

        now = time.monotonic()
        if self.is_serial_open() and not self.controller_ready:
            boot_wait_finished = (
                (now - self.serial_opened_at) * 1000.0 >= CONTROLLER_BOOT_WAIT_MS
            )
            if boot_wait_finished and not self.handshake_started:
                self.serial_port.reset_input_buffer()
                self.rx_buffer = b""
                self.handshake_started = True
                self.last_handshake_attempt = 0.0
            if self.handshake_started and (
                (now - self.last_handshake_attempt) * 1000.0 >= HANDSHAKE_RETRY_MS
            ):
                self.last_handshake_attempt = now
                self.send_command("HELLO", require_ready=False)
        elif self.controller_ready:
            if (now - self.last_keepalive) * 1000.0 >= KEEPALIVE_MS:
                self.last_keepalive = now
                self.send_command("KEEPALIVE")

        self.after(SERIAL_POLL_MS, self.service_serial)

    def handle_serial_line(self, line):
        if line == "READY,{}".format(CONTROLLER_ID) or line == "IDENTITY,{}".format(
            CONTROLLER_ID
        ):
            if not self.controller_ready:
                self.controller_ready = True
                self.connection_var.set("● CONNECTED")
                self.connection_label.configure(fg="#187a2f")
                self.set_motion_enabled(True)
                self.apply_speed()
                self.apply_pump_speed(0)
                self.apply_pump_speed(1)
                self.send_command("STATUS")
                self.status_var.set("Controller ready")
            return

        if line.startswith("STATUS,"):
            self.parse_status(line)
            return

        if line.startswith("EVENT,") or line.startswith("ERROR,"):
            if self.xy_dry_run_window is not None and self.xy_dry_run_window.winfo_exists():
                self.xy_dry_run_window.handle_controller_line(line)
            if self.xy_press_cycle_window is not None and self.xy_press_cycle_window.winfo_exists():
                self.xy_press_cycle_window.handle_controller_line(line)
            self.status_var.set(line)
            if line.startswith("EVENT,PRESS_FORCE_STOP"):
                self.press_status_var.set("STOPPED — FORCE THRESHOLD")
            elif line.startswith("EVENT,PRESS_MAX_TRAVEL_STOP"):
                self.press_status_var.set("STOPPED — MAX TRAVEL")
            elif line.startswith("EVENT,PRESS_LIMIT_STOP"):
                self.press_status_var.set("STOPPED — Z LIMIT")
            elif line.startswith("EVENT,LIFT_DONE"):
                self.press_status_var.set("LIFT COMPLETE")
            elif line.startswith("ERROR,LOAD_CELL_NOT_READY"):
                self.press_status_var.set("PRESS REJECTED — LOAD CELL NOT READY")
            elif line.startswith("ERROR,FORCE_ALREADY_AT_THRESHOLD"):
                self.press_status_var.set("PRESS REJECTED — FORCE ALREADY HIGH")
            elif line.startswith("ERROR,LIMIT_BLOCKED,Z,ZMIN"):
                self.press_status_var.set("PRESS REJECTED — Z− LIMIT")
            elif line.startswith("ERROR,LIMIT_BLOCKED,Z,ZPLUS"):
                self.press_status_var.set("LIFT REJECTED — Z+ LIMIT")
            elif line.startswith("ERROR,BAD_PRESS"):
                self.press_status_var.set("PRESS REJECTED — BAD PARAMETERS")
            elif line.startswith("ERROR,NO_PRESS_TO_LIFT"):
                self.press_status_var.set("LIFT REJECTED — NO PRESS DISTANCE")
            if (
                "STOP" in line
                or "LIMIT" in line
                or "WATCHDOG" in line
                or "ALARM" in line
            ):
                self.jog_axis = None
            return

        if line.startswith("OK,"):
            self.status_var.set(line)

    def parse_status(self, line):
        try:
            values = parse_status_line(line)

            for axis in self.AXES:
                machine_position = float(values[axis])
                scan_position = float(values["S{}".format(axis)])
                if abs(machine_position) < 0.0005:
                    machine_position = 0.0
                if abs(scan_position) < 0.0005:
                    scan_position = 0.0
                self.position_vars[axis].set(
                    "{:.3f} mm".format(machine_position)
                )
                self.scan_position_vars[axis].set(
                    "{:.3f} mm".format(scan_position)
                )
                self.homed_vars[axis].set(
                    "HOMED" if values["{}HOMED".format(axis)] == "1" else "NOT HOMED"
                )
                moving_key = "{}MOVING".format(axis)
                self.moving_vars[axis].set(
                    "MOVING" if values.get(moving_key, "0") == "1" else "IDLE"
                )

            for key in self.LIMITS:
                self.update_limit(key, values.get(key, "0") == "1")
            for key in self.ALARMS:
                self.update_alarm(key, values.get(key, "0") == "1")
            self.pump_status_vars[0].set(
                "RUNNING" if values["PUMP0RUNNING"] == "1" else "STOPPED"
            )
            self.pump_status_vars[1].set(
                "RUNNING" if values["PUMP1RUNNING"] == "1" else "STOPPED"
            )
            if values["LC"] == "1":
                self.load_cell_raw_var.set(values["R"])
                self.load_cell_filtered_var.set(values["FR"])
                self.load_cell_force_var.set("{:.2f} g".format(float(values["F"])))
                self.load_cell_status_var.set(
                    "TARING" if values["T"] == "1" else "READY"
                )
            else:
                self.load_cell_status_var.set("WAITING")
            press_state = values["PR"]
            if press_state == "1":
                self.press_status_var.set(
                    "PRESSING — {:.3f} mm".format(float(values["PD"]))
                )
            elif press_state == "2":
                self.press_status_var.set("LIFTING")
            else:
                current_press_status = self.press_status_var.get()
                if (
                    current_press_status.startswith("PRESSING")
                    or current_press_status == "LIFTING"
                ):
                    self.press_status_var.set("IDLE")
            if self.xy_dry_run_window is not None and self.xy_dry_run_window.winfo_exists():
                self.xy_dry_run_window.handle_status(values)
            if self.xy_press_cycle_window is not None and self.xy_press_cycle_window.winfo_exists():
                self.xy_press_cycle_window.handle_status(values)
        except (KeyError, ValueError):
            self.status_var.set("Invalid STATUS: {}".format(line))

    def update_limit(self, key, triggered):
        label, display_name = self.limit_labels[key]
        if triggered:
            label.configure(text="{} TRIGGERED".format(display_name), fg="#b00020")
        else:
            label.configure(text="{} SAFE".format(display_name), fg="#187a2f")

    def update_alarm(self, key, triggered):
        label, display_name = self.alarm_labels[key]
        if triggered:
            label.configure(text="{} ALARM".format(display_name), fg="#b00020")
        else:
            label.configure(text="{} NORMAL".format(display_name), fg="#187a2f")

    def validated_speed(self):
        try:
            speed = float(self.speed_var.get())
        except (tk.TclError, ValueError):
            self.status_var.set("Speed must be a number")
            return None
        if not 0.05 <= speed <= 5.0:
            self.status_var.set("Speed must be between 0.05 and 5.0 mm/s")
            return None
        return speed

    def apply_speed(self):
        speed = self.validated_speed()
        if speed is not None:
            self.send_command("SPEED {:.3f}".format(speed))

    def start_jog(self, axis, direction):
        speed = self.validated_speed()
        if speed is None:
            return
        self.send_command("SPEED {:.3f}".format(speed))
        sign = "+" if direction > 0 else "-"
        if self.send_command("JOG {} {}".format(axis, sign)):
            self.jog_axis = axis

    def stop_jog(self, event=None):
        if self.jog_axis is not None:
            # Jog is a hold-to-run control: releasing the mouse must stop now.
            # Stop only XYZ; the red STOP remains the global motion+pump stop.
            self.send_command("MOTION_STOP")
            self.jog_axis = None

    def on_global_button_release(self, event):
        self.stop_jog(event)

    def move_relative(self):
        speed = self.validated_speed()
        if speed is None:
            return
        try:
            distance = float(self.distance_var.get())
        except (tk.TclError, ValueError):
            self.status_var.set("Distance must be a number")
            return
        if distance == 0.0 or abs(distance) > 1000.0:
            self.status_var.set("Distance must be non-zero and within ±1000 mm")
            return
        axis = self.move_axis_var.get()
        if axis not in self.AXES:
            return
        self.send_command("SPEED {:.3f}".format(speed))
        self.send_command("MOVE {} {:.3f}".format(axis, distance))

    def home_axis(self, axis):
        if axis in self.AXES:
            self.jog_axis = None
            self.send_command("HOME {}".format(axis))

    def set_scan_zero(self):
        self.send_command("SCAN_ZERO")

    def validated_pump_speed(self, pump_id):
        try:
            rpm = float(self.pump_speed_vars[pump_id].get())
        except (tk.TclError, ValueError, IndexError):
            self.status_var.set("Pump speed must be a number")
            return None
        if not 0.1 <= rpm <= 20.0:
            self.status_var.set("Pump speed must be between 0.1 and 20.0 RPM")
            return None
        return rpm

    def apply_pump_speed(self, pump_id):
        rpm = self.validated_pump_speed(pump_id)
        if rpm is not None:
            self.send_command("PUMP SPEED {} {:.2f}".format(pump_id, rpm))

    def start_pump(self, pump_id):
        rpm = self.validated_pump_speed(pump_id)
        if rpm is None:
            return
        self.send_command("PUMP SPEED {} {:.2f}".format(pump_id, rpm))
        self.send_command("PUMP START {}".format(pump_id))

    def stop_pump(self, pump_id):
        self.send_command("PUMP STOP {}".format(pump_id))

    def tare_load_cell(self):
        self.load_cell_status_var.set("TARING")
        self.send_command("TARE")

    def validated_press_parameters(self):
        try:
            force = float(self.press_force_var.get())
            travel = float(self.press_travel_var.get())
            speed = float(self.press_speed_var.get())
        except (tk.TclError, ValueError):
            self.status_var.set("Press parameters must be numbers")
            return None
        if not 0.0 < force <= 5000.0:
            self.status_var.set("Press force must be within 0–5000 g")
            return None
        if not 0.0 < travel <= 50.0:
            self.status_var.set("Press travel must be within 0–50 mm")
            return None
        if not 0.05 <= speed <= 0.50:
            self.status_var.set("Press speed must be within 0.05–0.50 mm/s")
            return None
        return force, travel, speed

    def start_press(self):
        parameters = self.validated_press_parameters()
        if parameters is None:
            return
        force, travel, speed = parameters
        self.press_status_var.set("PRESS COMMAND SENT")
        self.send_command(
            "PRESS {:.2f} {:.3f} {:.3f}".format(force, travel, speed)
        )

    def stop_press(self):
        self.send_command("MOTION_STOP")

    def lift_press(self):
        self.send_command("LIFT")

    def emergency_stop(self):
        if self.xy_dry_run_window is not None and self.xy_dry_run_window.winfo_exists():
            self.xy_dry_run_window.emergency_stopped()
        if self.xy_press_cycle_window is not None and self.xy_press_cycle_window.winfo_exists():
            self.xy_press_cycle_window.emergency_stopped()
        if self.cv_window is not None and self.cv_window.winfo_exists():
            self.cv_window.stop_cv()
        if self.ca_window is not None and self.ca_window.winfo_exists():
            self.ca_window.stop_ca()
        if self.eis_window is not None and self.eis_window.winfo_exists():
            self.eis_window.stop_eis()
        self.send_command("STOP", require_ready=False)
        self.jog_axis = None
        self.status_var.set("STOP sent")

    def on_close(self):
        if self.xy_dry_run_window is not None and self.xy_dry_run_window.winfo_exists():
            self.xy_dry_run_window.stop()
        if self.xy_press_cycle_window is not None and self.xy_press_cycle_window.winfo_exists():
            self.xy_press_cycle_window.stop()
        if self.eis_window is not None and self.eis_window.winfo_exists():
            if self.eis_window.running:
                self.eis_window.close_pending = True
                self.eis_window.stop_eis()
                self.status_var.set("Waiting for EIS to stop safely before closing")
                self.after(250, self.on_close)
                return
        if self.cv_window is not None and self.cv_window.winfo_exists():
            self.cv_window.shutdown()
        if self.ca_window is not None and self.ca_window.winfo_exists():
            self.ca_window.shutdown()
        if self.eis_window is not None and self.eis_window.winfo_exists():
            self.eis_window.shutdown()
        if self.gamry_pstat is not None:
            self.disconnect_gamry()
        if self.gamry_toolkit_initialized:
            self.close_gamry_toolkit()
        if self.is_serial_open():
            try:
                self.serial_port.write(b"STOP\n")
                self.serial_port.flush()
            except (OSError, serial.SerialException):
                pass
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
        self.destroy()


def main():
    app = GantryMotionUI()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
