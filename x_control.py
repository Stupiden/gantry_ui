"""Milestone 1: manual XYZ motion control for the Arduino Mega gantry."""

import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serial
import serial.tools.list_ports


BAUD_RATE = 115200
CONTROLLER_ID = "MEGA_GANTRY_M1"
DEFAULT_SPEED_MM_S = 2.0
SERIAL_POLL_MS = 20
PORT_REFRESH_MS = 1500
KEEPALIVE_MS = 250
HANDSHAKE_RETRY_MS = 500


def parse_status_line(line):
    """Parse and expand one compact Milestone-1 STATUS line."""
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

    for axis in ("X", "Y", "Z"):
        float(values[axis])
    return values


class GantryMotionUI(tk.Tk):
    AXES = ("X", "Y", "Z")
    LIMITS = ("XMIN", "XPLUS", "YMIN", "YPLUS", "ZMIN", "ZPLUS")

    def __init__(self):
        tk.Tk.__init__(self)
        self.title("Gantry Motion Control — Milestone 1")
        self.geometry("860x690")
        self.minsize(760, 620)

        self.serial_port = None
        self.controller_ready = False
        self.jog_axis = None
        self.last_handshake_attempt = 0.0
        self.last_keepalive = 0.0
        self.last_ports = []

        self.port_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="● DISCONNECTED")
        self.status_var = tk.StringVar(value="Ready")
        self.speed_var = tk.DoubleVar(value=DEFAULT_SPEED_MM_S)
        self.move_axis_var = tk.StringVar(value="X")
        self.distance_var = tk.DoubleVar(value=1.0)
        self.position_vars = {
            axis: tk.StringVar(value="0.000 mm") for axis in self.AXES
        }
        self.limit_vars = {
            name: tk.StringVar(value="SAFE") for name in self.LIMITS
        }
        self.moving_vars = {
            axis: tk.StringVar(value="IDLE") for axis in self.AXES
        }

        self.motion_widgets = []
        self.limit_labels = {}
        self.build_ui()
        self.set_motion_enabled(False)
        self.refresh_ports()

        self.bind_all("<ButtonRelease-1>", self.on_global_button_release, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(SERIAL_POLL_MS, self.service_serial)
        self.after(PORT_REFRESH_MS, self.periodic_port_refresh)

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        title = ttk.Label(
            self,
            text="XYZ POSITION / MOTION",
            anchor="center",
            font=("Segoe UI", 20, "bold"),
        )
        title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))

        body = ttk.Frame(self, padding=(16, 8, 16, 16))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self.build_connection_panel(body)
        self.build_position_panel(body)
        self.build_jog_panel(body)
        self.build_relative_move_panel(body)
        self.build_limit_panel(body)
        self.build_stop_panel(body)

    def build_connection_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Arduino Mega connection", padding=10)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
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

    def build_position_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Session position", padding=10)
        frame.grid(row=1, column=0, sticky="ew", pady=8)
        for column in range(3):
            frame.columnconfigure(column, weight=1)

        for column, axis in enumerate(self.AXES):
            ttk.Label(
                frame, text=axis, anchor="center", font=("Segoe UI", 14, "bold")
            ).grid(row=0, column=column, sticky="ew")
            ttk.Label(
                frame,
                textvariable=self.position_vars[axis],
                anchor="center",
                font=("Consolas", 24, "bold"),
            ).grid(row=1, column=column, sticky="ew", pady=(2, 0))
            ttk.Label(
                frame, textvariable=self.moving_vars[axis], anchor="center"
            ).grid(row=2, column=column, sticky="ew")

    def build_jog_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Jog — hold to move", padding=10)
        frame.grid(row=2, column=0, sticky="ew", pady=8)
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
        frame = ttk.LabelFrame(parent, text="Relative move", padding=10)
        frame.grid(row=3, column=0, sticky="ew", pady=8)
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
            to=20.0,
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

    def build_limit_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Limit switches", padding=10)
        frame.grid(row=4, column=0, sticky="ew", pady=8)
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
                font=("Segoe UI", 10, "bold"),
                padx=5,
                pady=8,
            )
            label.grid(row=0, column=column, sticky="ew")
            self.limit_labels[key] = (label, display_name)

    def build_stop_panel(self, parent):
        frame = ttk.Frame(parent)
        frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)

        self.stop_button = tk.Button(
            frame,
            text="STOP",
            command=self.emergency_stop,
            bg="#b00020",
            fg="white",
            activebackground="#8b0018",
            activeforeground="white",
            font=("Segoe UI", 18, "bold"),
            height=2,
            state=tk.DISABLED,
        )
        self.stop_button.grid(row=0, column=0, sticky="ew")

        ttk.Label(frame, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
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
        self.last_handshake_attempt = 0.0
        self.connect_button.configure(text="DISCONNECT")
        self.port_box.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.stop_button.configure(state=tk.NORMAL)
        self.connection_var.set("● CONNECTING")
        self.connection_label.configure(fg="#9a6700")
        self.status_var.set("Waiting for {}...".format(CONTROLLER_ID))

    def disconnect_serial(self, reason):
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
                while self.serial_port.in_waiting:
                    line = self.serial_port.readline().decode(
                        "ascii", errors="replace"
                    ).strip()
                    if line:
                        self.handle_serial_line(line)
            except (OSError, serial.SerialException) as error:
                self.disconnect_serial("Serial read failed: {}".format(error))

        now = time.monotonic()
        if self.is_serial_open() and not self.controller_ready:
            if (now - self.last_handshake_attempt) * 1000.0 >= HANDSHAKE_RETRY_MS:
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
                self.send_command("STATUS")
                self.status_var.set("Controller ready")
            return

        if line.startswith("STATUS,"):
            self.parse_status(line)
            return

        if line.startswith("EVENT,") or line.startswith("ERROR,"):
            self.status_var.set(line)
            if "STOP" in line or "LIMIT" in line or "WATCHDOG" in line:
                self.jog_axis = None
            return

        if line.startswith("OK,"):
            self.status_var.set(line)

    def parse_status(self, line):
        try:
            values = parse_status_line(line)

            for axis in self.AXES:
                if axis in values:
                    position = float(values[axis])
                    if abs(position) < 0.0005:
                        position = 0.0
                    self.position_vars[axis].set("{:.3f} mm".format(position))
                moving_key = "{}MOVING".format(axis)
                self.moving_vars[axis].set(
                    "MOVING" if values.get(moving_key, "0") == "1" else "IDLE"
                )

            for key in self.LIMITS:
                self.update_limit(key, values.get(key, "0") == "1")
        except (KeyError, ValueError):
            self.status_var.set("Invalid STATUS: {}".format(line))

    def update_limit(self, key, triggered):
        label, display_name = self.limit_labels[key]
        if triggered:
            label.configure(text="{} TRIGGERED".format(display_name), fg="#b00020")
        else:
            label.configure(text="{} SAFE".format(display_name), fg="#187a2f")

    def validated_speed(self):
        try:
            speed = float(self.speed_var.get())
        except (tk.TclError, ValueError):
            self.status_var.set("Speed must be a number")
            return None
        if not 0.05 <= speed <= 20.0:
            self.status_var.set("Speed must be between 0.05 and 20.0 mm/s")
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
            self.send_command("STOP")
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

    def emergency_stop(self):
        self.send_command("STOP", require_ready=False)
        self.jog_axis = None
        self.status_var.set("STOP sent")

    def on_close(self):
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
