import sys
from datetime import datetime

import serial
import serial.tools.list_ports
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


BAUD_RATE = 115200
DEFAULT_RPM = 2.0
DEFAULT_ACCELERATION = 5.0
DEFAULT_PULSES_PER_REV = 1600


class PumpPanel(QGroupBox):
    def __init__(self, pump_id: int, command_callback, parent=None):
        super().__init__(f"Pump {pump_id}", parent)
        self.pump_id = pump_id
        self.command_callback = command_callback

        layout = QGridLayout(self)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)

        self.status_label = QLabel("STOPPED")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setMinimumHeight(38)

        self.rpm_box = QDoubleSpinBox()
        self.rpm_box.setRange(-1000.0, 1000.0)
        self.rpm_box.setDecimals(3)
        self.rpm_box.setSingleStep(0.1)
        self.rpm_box.setValue(DEFAULT_RPM)
        self.rpm_box.setSuffix(" rpm")

        self.acceleration_box = QDoubleSpinBox()
        self.acceleration_box.setRange(0.001, 10000.0)
        self.acceleration_box.setDecimals(3)
        self.acceleration_box.setSingleStep(0.5)
        self.acceleration_box.setValue(DEFAULT_ACCELERATION)
        self.acceleration_box.setSuffix(" rpm/s")

        self.pulses_per_rev_box = QDoubleSpinBox()
        self.pulses_per_rev_box.setRange(1.0, 100000.0)
        self.pulses_per_rev_box.setDecimals(0)
        self.pulses_per_rev_box.setSingleStep(100.0)
        self.pulses_per_rev_box.setValue(DEFAULT_PULSES_PER_REV)
        self.pulses_per_rev_box.setSuffix(" pulses/rev")

        self.apply_button = QPushButton("APPLY SETTINGS")
        self.start_button = QPushButton("START")
        self.stop_button = QPushButton("STOP")

        self.apply_button.clicked.connect(self.apply_settings)
        self.start_button.clicked.connect(self.start_pump)
        self.stop_button.clicked.connect(self.stop_pump)

        layout.addWidget(self.status_label, 0, 0, 1, 2)
        layout.addWidget(QLabel("Speed:"), 1, 0)
        layout.addWidget(self.rpm_box, 1, 1)
        layout.addWidget(QLabel("Acceleration:"), 2, 0)
        layout.addWidget(self.acceleration_box, 2, 1)
        layout.addWidget(QLabel("Motor resolution:"), 3, 0)
        layout.addWidget(self.pulses_per_rev_box, 3, 1)
        layout.addWidget(self.apply_button, 4, 0, 1, 2)
        layout.addWidget(self.start_button, 5, 0)
        layout.addWidget(self.stop_button, 5, 1)

        self.set_running(False)
        self.set_controls_enabled(False)

    def set_controls_enabled(self, enabled: bool):
        self.rpm_box.setEnabled(enabled)
        self.acceleration_box.setEnabled(enabled)
        self.pulses_per_rev_box.setEnabled(enabled)
        self.apply_button.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)

    def set_running(self, running: bool):
        if running:
            self.status_label.setText("RUNNING")
            self.status_label.setStyleSheet(
                "background: #d9f7df; color: #176b2c; font-weight: bold;"
            )
        else:
            self.status_label.setText("STOPPED")
            self.status_label.setStyleSheet(
                "background: #eeeeee; color: #555555; font-weight: bold;"
            )

    def apply_settings(self) -> bool:
        rpm = self.rpm_box.value()
        acceleration = self.acceleration_box.value()
        pulses_per_rev = self.pulses_per_rev_box.value()
        commands = [
            f"SET {self.pump_id} PPR {pulses_per_rev:.0f}",
            f"SET {self.pump_id} MAXRPM {rpm:.3f}",
            f"SET {self.pump_id} ACC {acceleration:.3f}",
        ]
        return self.command_callback(commands)

    def start_pump(self):
        if self.apply_settings() and self.command_callback([f"START {self.pump_id}"]):
            self.set_running(True)

    def stop_pump(self):
        if self.command_callback([f"STOP {self.pump_id}"]):
            self.set_running(False)


class PumpTestUI(QWidget):
    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.setWindowTitle("Dual Peristaltic Pump Test")
        self.resize(820, 620)
        self.build_ui()

        self.read_timer = QTimer(self)
        self.read_timer.timeout.connect(self.read_serial)
        self.read_timer.start(30)

        self.port_timer = QTimer(self)
        self.port_timer.timeout.connect(self.refresh_ports)
        self.port_timer.start(1500)
        self.refresh_ports()

    def build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(14)

        title = QLabel("DUAL PERISTALTIC PUMP TEST")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 25px; font-weight: bold; padding: 10px;")
        main_layout.addWidget(title)

        connection_layout = QHBoxLayout()
        connection_layout.addWidget(QLabel("Serial port:"))
        self.port_box = QComboBox()
        connection_layout.addWidget(self.port_box, 1)
        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.clicked.connect(self.refresh_ports)
        connection_layout.addWidget(self.refresh_button)
        self.connect_button = QPushButton("CONNECT")
        self.connect_button.clicked.connect(self.toggle_connection)
        connection_layout.addWidget(self.connect_button)
        self.connection_label = QLabel("● DISCONNECTED")
        self.connection_label.setStyleSheet("color: #b71c1c; font-weight: bold;")
        connection_layout.addWidget(self.connection_label)
        main_layout.addLayout(connection_layout)

        pump_layout = QHBoxLayout()
        self.pump_panels = [
            PumpPanel(0, self.send_commands),
            PumpPanel(1, self.send_commands),
        ]
        for panel in self.pump_panels:
            pump_layout.addWidget(panel)
        main_layout.addLayout(pump_layout)

        combined_group = QGroupBox("Combined Control")
        combined_layout = QHBoxLayout(combined_group)
        self.start_all_button = QPushButton("START BOTH")
        self.start_all_button.setMinimumHeight(48)
        self.start_all_button.clicked.connect(self.start_all)
        self.stop_all_button = QPushButton("STOP BOTH")
        self.stop_all_button.setMinimumHeight(48)
        self.stop_all_button.clicked.connect(self.stop_all)
        self.emergency_button = QPushButton("SOFTWARE STOP ALL")
        self.emergency_button.setMinimumHeight(48)
        self.emergency_button.setStyleSheet(
            "background: #c62828; color: white; font-size: 16px; font-weight: bold;"
        )
        self.emergency_button.clicked.connect(self.stop_all)
        combined_layout.addWidget(self.start_all_button)
        combined_layout.addWidget(self.stop_all_button)
        combined_layout.addWidget(self.emergency_button)
        main_layout.addWidget(combined_group)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Serial log"))
        log_header.addStretch()
        clear_button = QPushButton("CLEAR")
        clear_button.clicked.connect(self.clear_log)
        log_header.addWidget(clear_button)
        main_layout.addLayout(log_header)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(1000)
        self.log_box.setPlaceholderText("Commands and controller replies appear here.")
        main_layout.addWidget(self.log_box, 1)

        self.set_pump_controls_enabled(False)

    def refresh_ports(self):
        current = self.port_box.currentData()
        ports = list(serial.tools.list_ports.comports())
        port_names = [port.device for port in ports]
        existing_names = [self.port_box.itemData(i) for i in range(self.port_box.count())]
        if port_names == existing_names:
            return

        self.port_box.clear()
        for port in ports:
            label = f"{port.device} — {port.description}"
            self.port_box.addItem(label, port.device)
        if current in port_names:
            self.port_box.setCurrentIndex(port_names.index(current))

    def toggle_connection(self):
        if self.is_connected():
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self):
        port = self.port_box.currentData()
        if not port:
            self.append_log("ERROR", "No serial port is available.")
            return
        try:
            self.serial_port = serial.Serial(
                port=port,
                baudrate=BAUD_RATE,
                timeout=0,
                write_timeout=0.5,
            )
            self.serial_port.reset_input_buffer()
            self.connection_label.setText("● CONNECTED")
            self.connection_label.setStyleSheet("color: #1b7f35; font-weight: bold;")
            self.connect_button.setText("DISCONNECT")
            self.port_box.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.set_pump_controls_enabled(True)
            self.append_log("INFO", f"Connected to {port} at {BAUD_RATE} baud.")
        except (OSError, serial.SerialException) as error:
            self.serial_port = None
            self.append_log("ERROR", f"Connection failed: {error}")

    def disconnect_serial(self):
        if self.is_connected():
            self.send_commands(["STOP 0", "STOP 1"])
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException) as error:
                self.append_log("ERROR", f"Close failed: {error}")
        self.serial_port = None
        self.mark_disconnected()
        self.append_log("INFO", "Serial port disconnected.")

    def mark_disconnected(self):
        self.connection_label.setText("● DISCONNECTED")
        self.connection_label.setStyleSheet("color: #b71c1c; font-weight: bold;")
        self.connect_button.setText("CONNECT")
        self.port_box.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.set_pump_controls_enabled(False)
        for panel in self.pump_panels:
            panel.set_running(False)

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def set_pump_controls_enabled(self, enabled: bool):
        for panel in self.pump_panels:
            panel.set_controls_enabled(enabled)
        self.start_all_button.setEnabled(enabled)
        self.stop_all_button.setEnabled(enabled)
        self.emergency_button.setEnabled(enabled)

    def send_commands(self, commands) -> bool:
        if not self.is_connected():
            self.append_log("ERROR", "Connect the pump controller first.")
            return False
        try:
            for command in commands:
                self.serial_port.write((command.strip() + "\n").encode("ascii"))
                self.serial_port.flush()
                self.append_log("TX", command)
            return True
        except (OSError, serial.SerialException) as error:
            self.append_log("ERROR", f"Serial write failed: {error}")
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None
            self.mark_disconnected()
            return False

    def read_serial(self):
        if not self.is_connected():
            return
        try:
            while self.serial_port.in_waiting:
                reply = self.serial_port.readline().decode("ascii", errors="replace").strip()
                if reply:
                    self.append_log("RX", reply)
        except (OSError, serial.SerialException) as error:
            self.append_log("ERROR", f"Serial read failed: {error}")
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None
            self.mark_disconnected()

    def start_all(self):
        settings_ok = all(panel.apply_settings() for panel in self.pump_panels)
        if settings_ok and self.send_commands(["START 0", "START 1"]):
            for panel in self.pump_panels:
                panel.set_running(True)

    def stop_all(self):
        if self.send_commands(["STOP 0", "STOP 1"]):
            for panel in self.pump_panels:
                panel.set_running(False)

    def clear_log(self):
        self.log_box.clear()

    def append_log(self, direction: str, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_box.appendPlainText(f"[{timestamp}] {direction:>5}  {message}")

    def closeEvent(self, event: QCloseEvent):
        if self.is_connected():
            self.send_commands(["STOP 0", "STOP 1"])
            try:
                self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PumpTestUI()
    window.show()
    sys.exit(app.exec())
