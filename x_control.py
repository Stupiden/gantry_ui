import sys
import serial
import serial.tools.list_ports

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QMessageBox,
)


# ============================================================
# CONFIGURATION
# ============================================================

BAUD_RATE = 115200

DEFAULT_SPEED = 2.0       # mm/s
DEFAULT_ACCEL = 1.0       # mm/s²


# ============================================================
# MAIN WINDOW
# ============================================================

class XControlUI(QWidget):

    def __init__(self):
        super().__init__()

        self.serial = None

        self.setWindowTitle("X Axis Control")
        self.resize(700, 560)

        self.build_ui()

        # ----------------------------------------------------
        # Read serial every 20 ms
        # ----------------------------------------------------

        self.serial_timer = QTimer(self)
        self.serial_timer.timeout.connect(self.read_serial)
        self.serial_timer.start(20)

        # ----------------------------------------------------
        # Refresh serial ports
        # ----------------------------------------------------

        self.port_timer = QTimer(self)
        self.port_timer.timeout.connect(self.refresh_ports)
        self.port_timer.start(1500)

        self.refresh_ports()


    # ========================================================
    # BUILD UI
    # ========================================================

    def build_ui(self):

        main = QVBoxLayout(self)

        main.setSpacing(14)


        # ====================================================
        # TITLE
        # ====================================================

        title = QLabel("X AXIS CONTROL")

        title.setAlignment(Qt.AlignCenter)

        title.setStyleSheet("""
            font-size: 26px;
            font-weight: bold;
            padding: 10px;
        """)

        main.addWidget(title)


        # ====================================================
        # SERIAL CONNECTION
        # ====================================================

        serial_layout = QHBoxLayout()


        serial_layout.addWidget(
            QLabel("Serial:")
        )


        self.port_box = QComboBox()

        serial_layout.addWidget(
            self.port_box,
            1
        )


        self.connect_button = QPushButton(
            "CONNECT"
        )

        self.connect_button.clicked.connect(
            self.toggle_connection
        )

        serial_layout.addWidget(
            self.connect_button
        )


        self.connection_label = QLabel(
            "● DISCONNECTED"
        )

        self.connection_label.setStyleSheet(
            "color: red; font-weight: bold;"
        )

        serial_layout.addWidget(
            self.connection_label
        )


        main.addLayout(
            serial_layout
        )


        # ====================================================
        # POSITION
        # ====================================================

        position_group = QGroupBox(
            "Position"
        )

        position_layout = QVBoxLayout(
            position_group
        )


        self.position_label = QLabel(
            "0.000 mm"
        )

        self.position_label.setAlignment(
            Qt.AlignCenter
        )

        self.position_label.setStyleSheet("""
            font-size: 36px;
            font-weight: bold;
            padding: 15px;
        """)


        position_layout.addWidget(
            self.position_label
        )


        main.addWidget(
            position_group
        )


        # ====================================================
        # LIMIT SWITCHES
        # ====================================================

        limit_group = QGroupBox(
            "Limit Switches"
        )

        limit_layout = QHBoxLayout(
            limit_group
        )


        self.x_minus_indicator = QLabel(
            "● X− SAFE"
        )

        self.x_minus_indicator.setAlignment(
            Qt.AlignCenter
        )

        self.x_minus_indicator.setStyleSheet("""
            color: green;
            font-size: 18px;
            font-weight: bold;
            padding: 10px;
        """)


        self.x_plus_indicator = QLabel(
            "● X+ SAFE"
        )

        self.x_plus_indicator.setAlignment(
            Qt.AlignCenter
        )

        self.x_plus_indicator.setStyleSheet("""
            color: green;
            font-size: 18px;
            font-weight: bold;
            padding: 10px;
        """)


        limit_layout.addWidget(
            self.x_minus_indicator
        )

        limit_layout.addWidget(
            self.x_plus_indicator
        )


        main.addWidget(
            limit_group
        )


        # ====================================================
        # JOG
        # ====================================================

        jog_group = QGroupBox(
            "JOG — Hold Button to Move"
        )

        jog_layout = QHBoxLayout(
            jog_group
        )


        # ----------------------------------------------------
        # X- JOG
        # ----------------------------------------------------

        self.jog_minus_button = QPushButton(
            "◀  X− JOG"
        )

        self.jog_minus_button.setMinimumHeight(
            60
        )

        self.jog_minus_button.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
        """)

        # Press = START
        self.jog_minus_button.pressed.connect(
            self.start_jog_minus
        )

        # Release = STOP
        self.jog_minus_button.released.connect(
            self.stop_jog
        )


        # ----------------------------------------------------
        # X+ JOG
        # ----------------------------------------------------

        self.jog_plus_button = QPushButton(
            "X+ JOG  ▶"
        )

        self.jog_plus_button.setMinimumHeight(
            60
        )

        self.jog_plus_button.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
        """)

        # Press = START
        self.jog_plus_button.pressed.connect(
            self.start_jog_plus
        )

        # Release = STOP
        self.jog_plus_button.released.connect(
            self.stop_jog
        )


        jog_layout.addWidget(
            self.jog_minus_button
        )

        jog_layout.addWidget(
            self.jog_plus_button
        )


        main.addWidget(
            jog_group
        )


        # ====================================================
        # RELATIVE MOVE
        # ====================================================

        move_group = QGroupBox(
            "Relative Move"
        )

        move_layout = QGridLayout(
            move_group
        )


        self.move_minus_10 = QPushButton(
            "−10 mm"
        )

        self.move_minus_1 = QPushButton(
            "−1 mm"
        )

        self.move_plus_1 = QPushButton(
            "+1 mm"
        )

        self.move_plus_10 = QPushButton(
            "+10 mm"
        )


        move_buttons = [
            self.move_minus_10,
            self.move_minus_1,
            self.move_plus_1,
            self.move_plus_10,
        ]


        for button in move_buttons:

            button.setMinimumHeight(
                50
            )

            button.setStyleSheet(
                "font-size: 16px;"
            )


        self.move_minus_10.clicked.connect(
            lambda: self.move_relative(-10.0)
        )

        self.move_minus_1.clicked.connect(
            lambda: self.move_relative(-1.0)
        )

        self.move_plus_1.clicked.connect(
            lambda: self.move_relative(1.0)
        )

        self.move_plus_10.clicked.connect(
            lambda: self.move_relative(10.0)
        )


        move_layout.addWidget(
            self.move_minus_10,
            0,
            0
        )

        move_layout.addWidget(
            self.move_minus_1,
            0,
            1
        )

        move_layout.addWidget(
            self.move_plus_1,
            0,
            2
        )

        move_layout.addWidget(
            self.move_plus_10,
            0,
            3
        )


        main.addWidget(
            move_group
        )


        # ====================================================
        # SPEED / ACCELERATION
        # ====================================================

        settings_group = QGroupBox(
            "Motion Settings"
        )

        settings_layout = QGridLayout(
            settings_group
        )


        # ----------------------------------------------------
        # SPEED
        # ----------------------------------------------------

        settings_layout.addWidget(
            QLabel("Speed:"),
            0,
            0
        )


        self.speed_box = QDoubleSpinBox()

        self.speed_box.setRange(
            0.1,
            20.0
        )

        self.speed_box.setDecimals(
            1
        )

        self.speed_box.setSingleStep(
            0.1
        )

        self.speed_box.setValue(
            DEFAULT_SPEED
        )

        self.speed_box.setSuffix(
            " mm/s"
        )


        settings_layout.addWidget(
            self.speed_box,
            0,
            1
        )


        # ----------------------------------------------------
        # ACCELERATION
        # ----------------------------------------------------

        settings_layout.addWidget(
            QLabel("Acceleration:"),
            0,
            2
        )


        self.accel_box = QDoubleSpinBox()

        self.accel_box.setRange(
            0.1,
            50.0
        )

        self.accel_box.setDecimals(
            1
        )

        self.accel_box.setSingleStep(
            0.1
        )

        self.accel_box.setValue(
            DEFAULT_ACCEL
        )

        self.accel_box.setSuffix(
            " mm/s²"
        )


        settings_layout.addWidget(
            self.accel_box,
            0,
            3
        )


        # IMPORTANT:
        #
        # Changing the UI value immediately sends the new
        # setting to Arduino.

        self.speed_box.valueChanged.connect(
            self.speed_changed
        )

        self.accel_box.valueChanged.connect(
            self.accel_changed
        )


        main.addWidget(
            settings_group
        )


        # ====================================================
        # HOME / STOP
        # ====================================================

        control_layout = QHBoxLayout()


        self.home_button = QPushButton(
            "HOME X"
        )

        self.home_button.setMinimumHeight(
            60
        )

        self.home_button.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
        """)

        self.home_button.clicked.connect(
            self.home_x
        )


        self.stop_button = QPushButton(
            "STOP"
        )

        self.stop_button.setMinimumHeight(
            60
        )

        self.stop_button.setStyleSheet("""
            background-color: #b00020;
            color: white;
            font-size: 20px;
            font-weight: bold;
        """)

        self.stop_button.clicked.connect(
            self.emergency_stop
        )


        control_layout.addWidget(
            self.home_button
        )

        control_layout.addWidget(
            self.stop_button
        )


        main.addLayout(
            control_layout
        )


        # ====================================================
        # SERIAL STATUS
        # ====================================================

        self.status_label = QLabel(
            "Ready"
        )

        self.status_label.setStyleSheet("""
            color: #555;
            padding: 6px;
        """)

        main.addWidget(
            self.status_label
        )


    # ========================================================
    # SERIAL PORTS
    # ========================================================

    def refresh_ports(self):

        # Don't refresh while connected
        if (
            self.serial is not None
            and
            self.serial.is_open
        ):
            return


        current_port = (
            self.port_box.currentText()
        )


        ports = [
            port.device
            for port
            in serial.tools.list_ports.comports()
        ]


        current_list = [
            self.port_box.itemText(i)
            for i
            in range(
                self.port_box.count()
            )
        ]


        if ports == current_list:
            return


        self.port_box.clear()

        self.port_box.addItems(
            ports
        )


        if current_port in ports:

            self.port_box.setCurrentText(
                current_port
            )


    # ========================================================
    # CONNECT / DISCONNECT
    # ========================================================

    def toggle_connection(self):

        # ====================================================
        # DISCONNECT
        # ====================================================

        if (
            self.serial is not None
            and
            self.serial.is_open
        ):

            try:

                self.send_command(
                    "STOP"
                )

                self.serial.close()

            except Exception:
                pass


            self.serial = None


            self.connect_button.setText(
                "CONNECT"
            )


            self.connection_label.setText(
                "● DISCONNECTED"
            )

            self.connection_label.setStyleSheet(
                "color: red; font-weight: bold;"
            )


            self.status_label.setText(
                "Disconnected"
            )


            return


        # ====================================================
        # CONNECT
        # ====================================================

        port = (
            self.port_box.currentText()
        )


        if not port:

            QMessageBox.warning(
                self,
                "Serial",
                "No serial port selected."
            )

            return


        try:

            self.serial = serial.Serial(
                port=port,
                baudrate=BAUD_RATE,
                timeout=0
            )


            self.connect_button.setText(
                "DISCONNECT"
            )


            self.connection_label.setText(
                "● CONNECTED"
            )

            self.connection_label.setStyleSheet(
                "color: green; font-weight: bold;"
            )


            self.status_label.setText(
                "Connected. Waiting for Arduino..."
            )


            # Arduino Mega often resets when the serial
            # connection opens.
            #
            # Wait 1.5 seconds before sending settings.

            QTimer.singleShot(
                1500,
                self.initialize_arduino
            )


        except Exception as error:

            self.serial = None


            QMessageBox.critical(
                self,
                "Serial Error",
                str(error)
            )


    # ========================================================
    # INITIALIZE ARDUINO
    # ========================================================

    def initialize_arduino(self):

        if not self.is_connected():
            return


        # ----------------------------------------------------
        # Send the CURRENT UI settings.
        #
        # Defaults:
        #
        # 2.0 mm/s
        # 1.0 mm/s²
        #
        # ----------------------------------------------------

        self.send_command(
            f"SPEED {self.speed_box.value():.3f}"
        )


        self.send_command(
            f"ACCEL {self.accel_box.value():.3f}"
        )


        self.status_label.setText(
            "Controller initialized"
        )


    # ========================================================
    # CONNECTION CHECK
    # ========================================================

    def is_connected(self):

        return (
            self.serial is not None
            and
            self.serial.is_open
        )


    # ========================================================
    # SEND COMMAND
    # ========================================================

    def send_command(
        self,
        command
    ):

        if not self.is_connected():

            self.status_label.setText(
                "Not connected"
            )

            return False


        try:

            message = (
                command + "\n"
            ).encode(
                "utf-8"
            )


            self.serial.write(
                message
            )


            self.serial.flush()


            self.status_label.setText(
                "TX: " + command
            )


            return True


        except Exception as error:

            self.status_label.setText(
                "TX ERROR: "
                + str(error)
            )


            return False


    # ========================================================
    # SPEED
    # ========================================================

    def speed_changed(
        self,
        value
    ):

        if not self.is_connected():
            return


        self.send_command(
            f"SPEED {value:.3f}"
        )


    # ========================================================
    # ACCELERATION
    # ========================================================

    def accel_changed(
        self,
        value
    ):

        if not self.is_connected():
            return


        self.send_command(
            f"ACCEL {value:.3f}"
        )


    # ========================================================
    # JOG X-
    # ========================================================

    def start_jog_minus(self):

        # EXACT Arduino command:
        #
        # JOG X-

        self.send_command(
            "JOG X-"
        )


    # ========================================================
    # JOG X+
    # ========================================================

    def start_jog_plus(self):

        # EXACT Arduino command:
        #
        # JOG X+

        self.send_command(
            "JOG X+"
        )


    # ========================================================
    # STOP JOG
    # ========================================================

    def stop_jog(self):

        self.send_command(
            "JOG STOP"
        )


    # ========================================================
    # RELATIVE MOVE
    # ========================================================

    def move_relative(
        self,
        distance
    ):

        self.send_command(
            f"MOVE {distance:.3f}"
        )


    # ========================================================
    # HOME
    # ========================================================

    def home_x(self):

        self.send_command(
            "HOME"
        )


    # ========================================================
    # STOP
    # ========================================================

    def emergency_stop(self):

        self.send_command(
            "STOP"
        )


    # ========================================================
    # READ SERIAL
    # ========================================================

    def read_serial(self):

        if not self.is_connected():
            return


        try:

            while (
                self.serial.in_waiting > 0
            ):

                line = (
                    self.serial.readline()
                    .decode(
                        "utf-8",
                        errors="ignore"
                    )
                    .strip()
                )


                if not line:
                    continue


                # --------------------------------------------
                # STATUS
                # --------------------------------------------

                if line.startswith(
                    "STATUS,"
                ):

                    self.parse_status(
                        line
                    )


                # --------------------------------------------
                # OTHER MESSAGE
                # --------------------------------------------

                else:

                    self.status_label.setText(
                        "RX: " + line
                    )


        except Exception as error:

            self.status_label.setText(
                "RX ERROR: "
                + str(error)
            )


    # ========================================================
    # PARSE STATUS
    #
    # Arduino sends:
    #
    # STATUS,X=10.000,XMIN=0,XPLUS=0,HOME=0,MOVING=0
    #
    # ========================================================

    def parse_status(
        self,
        line
    ):

        try:

            values = {}


            parts = line.split(",")


            for part in parts[1:]:

                if "=" not in part:
                    continue


                key, value = (
                    part.split(
                        "=",
                        1
                    )
                )


                values[
                    key.strip()
                ] = value.strip()


            # =================================================
            # POSITION
            # =================================================

            if "X" in values:

                position = float(
                    values["X"]
                )


                # Avoid -0.000

                if abs(position) < 0.0005:

                    position = 0.0


                self.position_label.setText(
                    f"{position:.3f} mm"
                )


            # =================================================
            # X-
            # =================================================

            x_minus_triggered = (
                values.get(
                    "XMIN",
                    "0"
                )
                ==
                "1"
            )


            self.update_limit_indicator(
                self.x_minus_indicator,
                "X−",
                x_minus_triggered
            )


            # =================================================
            # X+
            # =================================================

            x_plus_triggered = (
                values.get(
                    "XPLUS",
                    "0"
                )
                ==
                "1"
            )


            self.update_limit_indicator(
                self.x_plus_indicator,
                "X+",
                x_plus_triggered
            )


        except Exception as error:

            self.status_label.setText(
                "STATUS ERROR: "
                + str(error)
            )


    # ========================================================
    # LIMIT INDICATOR
    # ========================================================

    def update_limit_indicator(
        self,
        label,
        name,
        triggered
    ):

        if triggered:

            label.setText(
                f"● {name} TRIGGERED"
            )


            label.setStyleSheet("""
                color: red;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            """)


        else:

            label.setText(
                f"● {name} SAFE"
            )


            label.setStyleSheet("""
                color: green;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            """)


    # ========================================================
    # WINDOW CLOSE
    # ========================================================

    def closeEvent(
        self,
        event
    ):

        # Stop X before program exits

        if self.is_connected():

            try:

                self.serial.write(
                    b"STOP\n"
                )

                self.serial.flush()

                self.serial.close()

            except Exception:
                pass


        event.accept()


# ============================================================
# PROGRAM START
# ============================================================

if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )


    window = XControlUI()

    window.show()


    sys.exit(
        app.exec()
    )