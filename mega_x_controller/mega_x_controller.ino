/*
  Milestone 3: Arduino Mega XYZ motion with dual-Y alarm safety.

  Scope: position reporting, jog, relative move, immediate software STOP,
  six limit inputs, dual-Y synchronized STEP pulses, homing to MINUS limits,
  independent machine/scan coordinates, dual-Y driver alarm interlock,
  and a serial watchdog.
*/

#include <Arduino.h>
#include <stdlib.h>
#include <string.h>

const char CONTROLLER_ID[] = "MEGA_GANTRY_M3";

const uint8_t X_STEP = 2;
const uint8_t X_DIR = 3;
const uint8_t Z_STEP = 4;
const uint8_t Z_DIR = 5;
const uint8_t Y1_STEP = 6;
const uint8_t Y1_DIR = 7;
const uint8_t Y_MINUS = 8;
const uint8_t Y_PLUS = 10;
const uint8_t Y2_STEP = 11;
const uint8_t Y2_DIR = 12;
const uint8_t X_MINUS = 22;
const uint8_t X_PLUS = 24;
const uint8_t Z_MINUS = 25;
const uint8_t Z_PLUS = 28;
const uint8_t Y1_ALARM = 37;
const uint8_t Y2_ALARM = 38;

const float STEPS_PER_MM = 160.0f;
const float DEFAULT_SPEED_MM_S = 2.0f;
const float HOME_SPEED_MM_S = 1.0f;
const float MIN_SPEED_MM_S = 0.05f;
const float MAX_SPEED_MM_S = 5.0f;
const unsigned int DIR_SETUP_US = 20;
// Timer1 /64 gives 4 us/tick.  Eight ticks leave enough margin for AVR ISR
// entry and digitalWrite() before the next compare, while remaining far above
// the driver's minimum STEP pulse width.
const uint16_t TIMER_HIGH_TICKS = 8;  // 32 us HIGH pulse.
const unsigned long TIMER_TICKS_PER_SECOND = F_CPU / 64UL;
const unsigned long STATUS_INTERVAL_MS = 100;
const unsigned long COMMAND_WATCHDOG_MS = 750;
const size_t COMMAND_BUFFER_SIZE = 96;

enum MotionMode {
  MODE_IDLE,
  MODE_JOG,
  MODE_MOVE,
  MODE_HOME
};

struct AxisState {
  char name;
  long positionSteps;
  unsigned long remainingSteps;
  int8_t direction;
  MotionMode mode;
  uint16_t periodTicks;
  long scanZeroSteps;
  bool homed;
};

AxisState xAxis = {'X', 0, 0, 0, MODE_IDLE, 0, 0, false};
AxisState yAxis = {'Y', 0, 0, 0, MODE_IDLE, 0, 0, false};
AxisState zAxis = {'Z', 0, 0, 0, MODE_IDLE, 0, 0, false};

float speedMM = DEFAULT_SPEED_MM_S;
AxisState *volatile activeAxis = NULL;
volatile bool timerPulseHigh = false;
volatile bool motionCompleted = false;
volatile char completedAxisName = 0;
unsigned long lastStatusMillis = 0;
unsigned long lastCommandMillis = 0;
char commandBuffer[COMMAND_BUFFER_SIZE];
size_t commandLength = 0;

uint16_t timerPeriodForSpeed(float speedMMPerSecond);

bool limitTriggered(uint8_t pin) {
  return digitalRead(pin) == LOW;
}

bool alarmTriggered(uint8_t pin) {
  // Verified wiring: open/HIGH is normal; closed/LOW is alarm.
  return digitalRead(pin) == LOW;
}

bool anyYAlarm() {
  return alarmTriggered(Y1_ALARM) || alarmTriggered(Y2_ALARM);
}

bool yMotionAllowed(const AxisState &axis) {
  return axis.name != 'Y' || !anyYAlarm();
}

void reportYAlarmBlocked() {
  bool y1 = alarmTriggered(Y1_ALARM);
  bool y2 = alarmTriggered(Y2_ALARM);
  Serial.print(F("ERROR,Y_ALARM_ACTIVE,"));
  if (y1 && y2) {
    Serial.println(F("BOTH"));
  } else {
    Serial.println(y1 ? F("Y1") : F("Y2"));
  }
}

AxisState *axisFromName(const char *token) {
  if (token == NULL || token[1] != '\0') return NULL;
  if (token[0] == 'X') return &xAxis;
  if (token[0] == 'Y') return &yAxis;
  if (token[0] == 'Z') return &zAxis;
  return NULL;
}

bool axisIsMoving(const AxisState &axis) {
  return axis.mode != MODE_IDLE;
}

bool anyAxisMoving() {
  return axisIsMoving(xAxis) || axisIsMoving(yAxis) || axisIsMoving(zAxis);
}

bool movementAllowed(const AxisState &axis, int8_t direction) {
  if (axis.name == 'X') {
    return direction > 0 ? !limitTriggered(X_PLUS) : !limitTriggered(X_MINUS);
  }
  if (axis.name == 'Y') {
    return direction > 0 ? !limitTriggered(Y_PLUS) : !limitTriggered(Y_MINUS);
  }
  return direction > 0 ? !limitTriggered(Z_PLUS) : !limitTriggered(Z_MINUS);
}

const char *activeLimitName(const AxisState &axis, int8_t direction) {
  if (axis.name == 'X') return direction > 0 ? "XPLUS" : "XMIN";
  if (axis.name == 'Y') return direction > 0 ? "YPLUS" : "YMIN";
  return direction > 0 ? "ZPLUS" : "ZMIN";
}

void setAxisDirection(const AxisState &axis, int8_t direction) {
  // Confirmed convention: X+ LOW, Y+ HIGH, Z+ is downward.
  if (axis.name == 'X') {
    digitalWrite(X_DIR, direction > 0 ? LOW : HIGH);
  } else if (axis.name == 'Y') {
    uint8_t level = direction > 0 ? HIGH : LOW;
    digitalWrite(Y1_DIR, level);
    digitalWrite(Y2_DIR, level);
  } else {
    // Machine convention: Z+ is downward.  This drive needs LOW for Z+.
    digitalWrite(Z_DIR, direction > 0 ? LOW : HIGH);
  }
}

void setStepLevel(const AxisState &axis, uint8_t level) {
  if (axis.name == 'X') {
    digitalWrite(X_STEP, level);
  } else if (axis.name == 'Y') {
    digitalWrite(Y1_STEP, level);
    digitalWrite(Y2_STEP, level);
  } else {
    digitalWrite(Z_STEP, level);
  }
}

void stopTimer() {
  TIMSK1 &= ~_BV(OCIE1A);
  TCCR1B = 0;
  timerPulseHigh = false;
}

void stopAll() {
  noInterrupts();
  stopTimer();
  activeAxis = NULL;
  xAxis.mode = MODE_IDLE;
  yAxis.mode = MODE_IDLE;
  zAxis.mode = MODE_IDLE;
  xAxis.remainingSteps = 0;
  yAxis.remainingSteps = 0;
  zAxis.remainingSteps = 0;
  xAxis.direction = 0;
  yAxis.direction = 0;
  zAxis.direction = 0;
  interrupts();
  setStepLevel(xAxis, LOW);
  setStepLevel(yAxis, LOW);
  setStepLevel(zAxis, LOW);
}

void startTimer(AxisState &axis) {
  noInterrupts();
  activeAxis = &axis;
  timerPulseHigh = false;
  TCNT1 = 0;
  OCR1A = axis.periodTicks - 1;
  TIFR1 = _BV(OCF1A);
  TCCR1A = 0;
  TCCR1B = _BV(WGM12);
  TIMSK1 = _BV(OCIE1A);
  TCCR1B |= _BV(CS11) | _BV(CS10);  // Prescaler 64, 4 us/tick.
  interrupts();
}

bool startJog(AxisState &axis, int8_t direction) {
  if (!yMotionAllowed(axis)) {
    reportYAlarmBlocked();
    return false;
  }
  if (!movementAllowed(axis, direction)) {
    Serial.print(F("ERROR,LIMIT_BLOCKED,"));
    Serial.print(axis.name);
    Serial.print(',');
    Serial.println(activeLimitName(axis, direction));
    return false;
  }
  stopAll();
  axis.direction = direction;
  axis.periodTicks = timerPeriodForSpeed(speedMM);
  setAxisDirection(axis, direction);
  delayMicroseconds(DIR_SETUP_US);
  axis.mode = MODE_JOG;
  startTimer(axis);
  return true;
}

bool startRelativeMove(AxisState &axis, float distanceMM) {
  if (!yMotionAllowed(axis)) {
    reportYAlarmBlocked();
    return false;
  }
  long signedSteps = lround(distanceMM * STEPS_PER_MM);
  if (signedSteps == 0) {
    Serial.println(F("ERROR,MOVE_TOO_SMALL"));
    return false;
  }
  int8_t direction = signedSteps > 0 ? 1 : -1;
  if (!movementAllowed(axis, direction)) {
    Serial.print(F("ERROR,LIMIT_BLOCKED,"));
    Serial.print(axis.name);
    Serial.print(',');
    Serial.println(activeLimitName(axis, direction));
    return false;
  }
  stopAll();
  axis.direction = direction;
  axis.remainingSteps = static_cast<unsigned long>(labs(signedSteps));
  axis.periodTicks = timerPeriodForSpeed(speedMM);
  setAxisDirection(axis, direction);
  delayMicroseconds(DIR_SETUP_US);
  axis.mode = MODE_MOVE;
  startTimer(axis);
  return true;
}

bool startHome(AxisState &axis) {
  stopAll();

  // An axis already resting on its MINUS switch is at machine zero.
  if (!movementAllowed(axis, -1)) {
    axis.positionSteps = 0;
    axis.homed = true;
    return false;
  }

  axis.direction = -1;
  axis.periodTicks = timerPeriodForSpeed(HOME_SPEED_MM_S);
  setAxisDirection(axis, -1);
  delayMicroseconds(DIR_SETUP_US);
  axis.mode = MODE_HOME;
  startTimer(axis);
  return true;
}

uint16_t timerPeriodForSpeed(float speedMMPerSecond) {
  float stepsPerSecond = speedMMPerSecond * STEPS_PER_MM;
  unsigned long ticks =
      static_cast<unsigned long>(TIMER_TICKS_PER_SECOND / stepsPerSecond + 0.5f);
  if (ticks <= TIMER_HIGH_TICKS + 1) ticks = TIMER_HIGH_TICKS + 2;
  if (ticks > 65535UL) ticks = 65535UL;
  return static_cast<uint16_t>(ticks);
}

ISR(TIMER1_COMPA_vect) {
  AxisState *axis = activeAxis;
  if (axis == NULL || axis->mode == MODE_IDLE) {
    stopTimer();
    return;
  }

  if (!timerPulseHigh) {
    // Load the short phase before touching the pins.  Loading it afterwards
    // can miss the compare point because digitalWrite() itself takes time.
    OCR1A = TIMER_HIGH_TICKS - 1;
    setStepLevel(*axis, HIGH);
    timerPulseHigh = true;
    return;
  }

  OCR1A = axis->periodTicks - TIMER_HIGH_TICKS - 1;
  setStepLevel(*axis, LOW);
  timerPulseHigh = false;
  axis->positionSteps += axis->direction;

  if (axis->mode == MODE_MOVE) {
    if (axis->remainingSteps > 0) axis->remainingSteps--;
    if (axis->remainingSteps == 0) {
      char axisName = axis->name;
      axis->mode = MODE_IDLE;
      axis->direction = 0;
      activeAxis = NULL;
      stopTimer();
      completedAxisName = axisName;
      motionCompleted = true;
      return;
    }
  }

}

void applySpeed(float newSpeedMM) {
  speedMM = newSpeedMM;
}

void printPosition(const AxisState &axis) {
  long positionSnapshot;
  noInterrupts();
  positionSnapshot = axis.positionSteps;
  interrupts();
  Serial.print(static_cast<float>(positionSnapshot) / STEPS_PER_MM, 3);
}

void printScanPosition(const AxisState &axis) {
  long positionSnapshot;
  long scanZeroSnapshot;
  noInterrupts();
  positionSnapshot = axis.positionSteps;
  scanZeroSnapshot = axis.scanZeroSteps;
  interrupts();
  Serial.print(static_cast<float>(positionSnapshot - scanZeroSnapshot) /
                   STEPS_PER_MM,
               3);
}

void setScanZero() {
  noInterrupts();
  xAxis.scanZeroSteps = xAxis.positionSteps;
  yAxis.scanZeroSteps = yAxis.positionSteps;
  zAxis.scanZeroSteps = zAxis.positionSteps;
  interrupts();
}

void sendStatus(bool force) {
  unsigned long now = millis();
  // Keep the motion path quiet; start/stop/completion/limit events force status.
  if (!force && anyAxisMoving()) return;
  if (!force && now - lastStatusMillis < STATUS_INTERVAL_MS) return;
  lastStatusMillis = now;

  Serial.print(F("STATUS,X="));
  printPosition(xAxis);
  Serial.print(F(",Y="));
  printPosition(yAxis);
  Serial.print(F(",Z="));
  printPosition(zAxis);
  Serial.print(F(",SX="));
  printScanPosition(xAxis);
  Serial.print(F(",SY="));
  printScanPosition(yAxis);
  Serial.print(F(",SZ="));
  printScanPosition(zAxis);
  // L order: X-, X+, Y-, Y+, Z-, Z+. M order: X, Y, Z.
  Serial.print(F(",L="));
  Serial.print(limitTriggered(X_MINUS) ? 1 : 0);
  Serial.print(limitTriggered(X_PLUS) ? 1 : 0);
  Serial.print(limitTriggered(Y_MINUS) ? 1 : 0);
  Serial.print(limitTriggered(Y_PLUS) ? 1 : 0);
  Serial.print(limitTriggered(Z_MINUS) ? 1 : 0);
  Serial.print(limitTriggered(Z_PLUS) ? 1 : 0);
  Serial.print(F(",M="));
  Serial.print(axisIsMoving(xAxis) ? 1 : 0);
  Serial.print(axisIsMoving(yAxis) ? 1 : 0);
  Serial.print(axisIsMoving(zAxis) ? 1 : 0);
  // H order: X, Y, Z. Homed remains true through ordinary motion.
  Serial.print(F(",H="));
  Serial.print(xAxis.homed ? 1 : 0);
  Serial.print(yAxis.homed ? 1 : 0);
  Serial.print(zAxis.homed ? 1 : 0);
  // A order: Y1, Y2. Alarm inputs are active LOW.
  Serial.print(F(",A="));
  Serial.print(alarmTriggered(Y1_ALARM) ? 1 : 0);
  Serial.println(alarmTriggered(Y2_ALARM) ? 1 : 0);
}

bool parseFloatStrict(const char *token, float &value) {
  if (token == NULL || token[0] == '\0') return false;
  char *endPointer = NULL;
  value = strtod(token, &endPointer);
  return endPointer != token && *endPointer == '\0';
}

void processCommand(char *command) {
  lastCommandMillis = millis();

  char *savePointer = NULL;
  char *verb = strtok_r(command, " ", &savePointer);
  if (verb == NULL) return;

  if (strcmp(verb, "HELLO") == 0) {
    Serial.print(F("IDENTITY,"));
    Serial.println(CONTROLLER_ID);
    return;
  }
  if (strcmp(verb, "KEEPALIVE") == 0) return;
  if (strcmp(verb, "STATUS") == 0) {
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "STOP") == 0) {
    stopAll();
    Serial.println(F("OK,STOP"));
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "SPEED") == 0) {
    float requestedSpeed;
    if (!parseFloatStrict(strtok_r(NULL, " ", &savePointer), requestedSpeed) ||
        requestedSpeed < MIN_SPEED_MM_S || requestedSpeed > MAX_SPEED_MM_S) {
      Serial.println(F("ERROR,BAD_SPEED"));
      return;
    }
    applySpeed(requestedSpeed);
    Serial.print(F("OK,SPEED,"));
    Serial.println(speedMM, 3);
    return;
  }
  if (strcmp(verb, "HOME") == 0) {
    AxisState *axis = axisFromName(strtok_r(NULL, " ", &savePointer));
    if (axis == NULL) {
      Serial.println(F("ERROR,BAD_HOME"));
      return;
    }
    if (!yMotionAllowed(*axis)) {
      reportYAlarmBlocked();
      sendStatus(true);
      return;
    }
    bool started = startHome(*axis);
    if (started) {
      Serial.print(F("OK,HOMING,"));
    } else {
      Serial.print(F("EVENT,HOME_DONE,"));
    }
    Serial.println(axis->name);
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "SCAN_ZERO") == 0) {
    if (anyAxisMoving()) {
      Serial.println(F("ERROR,MOVING"));
      return;
    }
    setScanZero();
    Serial.println(F("OK,SCAN_ZERO"));
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "JOG") == 0) {
    char *axisToken = strtok_r(NULL, " ", &savePointer);
    // Compatibility and safety: every Jog-stop spelling is a hard stop.
    if (axisToken != NULL && strcmp(axisToken, "STOP") == 0) {
      stopAll();
      Serial.println(F("OK,JOG_STOP"));
      sendStatus(true);
      return;
    }
    AxisState *axis = axisFromName(axisToken);
    char *directionToken = strtok_r(NULL, " ", &savePointer);
    if (axis == NULL || directionToken == NULL || directionToken[1] != '\0' ||
        (directionToken[0] != '+' && directionToken[0] != '-')) {
      Serial.println(F("ERROR,BAD_JOG"));
      return;
    }
    int8_t direction = directionToken[0] == '+' ? 1 : -1;
    if (startJog(*axis, direction)) {
      Serial.print(F("OK,JOG,"));
      Serial.print(axis->name);
      Serial.print(',');
      Serial.println(directionToken);
      sendStatus(true);
    }
    return;
  }
  if (strcmp(verb, "MOVE") == 0) {
    AxisState *axis = axisFromName(strtok_r(NULL, " ", &savePointer));
    float distanceMM;
    if (axis == NULL ||
        !parseFloatStrict(strtok_r(NULL, " ", &savePointer), distanceMM) ||
        distanceMM == 0.0f || abs(distanceMM) > 1000.0f) {
      Serial.println(F("ERROR,BAD_MOVE"));
      return;
    }
    if (startRelativeMove(*axis, distanceMM)) {
      Serial.print(F("OK,MOVE,"));
      Serial.print(axis->name);
      Serial.print(',');
      Serial.println(distanceMM, 3);
      sendStatus(true);
    }
    return;
  }

  Serial.println(F("ERROR,UNKNOWN_COMMAND"));
}

void readSerial() {
  while (Serial.available() > 0) {
    char incoming = static_cast<char>(Serial.read());
    if (incoming == '\n' || incoming == '\r') {
      if (commandLength > 0) {
        commandBuffer[commandLength] = '\0';
        processCommand(commandBuffer);
        commandLength = 0;
      }
    } else if (commandLength < COMMAND_BUFFER_SIZE - 1) {
      if (incoming >= 'a' && incoming <= 'z') incoming -= 32;
      commandBuffer[commandLength++] = incoming;
    } else {
      commandLength = 0;
      Serial.println(F("ERROR,COMMAND_TOO_LONG"));
    }
  }
}

void setup() {
  Serial.begin(115200);

  TCCR1A = 0;
  TCCR1B = 0;
  TIMSK1 = 0;

  pinMode(X_STEP, OUTPUT);
  pinMode(X_DIR, OUTPUT);
  pinMode(Y1_STEP, OUTPUT);
  pinMode(Y1_DIR, OUTPUT);
  pinMode(Y2_STEP, OUTPUT);
  pinMode(Y2_DIR, OUTPUT);
  pinMode(Z_STEP, OUTPUT);
  pinMode(Z_DIR, OUTPUT);

  digitalWrite(X_STEP, LOW);
  digitalWrite(Y1_STEP, LOW);
  digitalWrite(Y2_STEP, LOW);
  digitalWrite(Z_STEP, LOW);

  pinMode(X_MINUS, INPUT_PULLUP);
  pinMode(X_PLUS, INPUT_PULLUP);
  pinMode(Y_MINUS, INPUT_PULLUP);
  pinMode(Y_PLUS, INPUT_PULLUP);
  pinMode(Z_MINUS, INPUT_PULLUP);
  pinMode(Z_PLUS, INPUT_PULLUP);
  // External 4.7 kOhm pull-ups are installed; INPUT_PULLUP also prevents a
  // floating input if an external connection is temporarily open.
  pinMode(Y1_ALARM, INPUT_PULLUP);
  pinMode(Y2_ALARM, INPUT_PULLUP);

  applySpeed(DEFAULT_SPEED_MM_S);
  lastCommandMillis = millis();
  Serial.print(F("READY,"));
  Serial.println(CONTROLLER_ID);
  sendStatus(true);
}

void loop() {
  readSerial();

  AxisState *axis = activeAxis;
  if (axis != NULL && axis->name == 'Y' && anyYAlarm()) {
    bool y1 = alarmTriggered(Y1_ALARM);
    bool y2 = alarmTriggered(Y2_ALARM);
    stopAll();
    Serial.print(F("EVENT,ALARM_STOP,"));
    if (y1 && y2) {
      Serial.println(F("BOTH"));
    } else {
      Serial.println(y1 ? F("Y1") : F("Y2"));
    }
    sendStatus(true);
    axis = NULL;
  }

  if (axis != NULL && !movementAllowed(*axis, axis->direction)) {
    char axisName = axis->name;
    MotionMode stoppedMode = axis->mode;
    const char *limitName = activeLimitName(*axis, axis->direction);
    stopAll();
    if (stoppedMode == MODE_HOME) {
      axis->positionSteps = 0;
      axis->homed = true;
      Serial.print(F("EVENT,HOME_DONE,"));
      Serial.println(axisName);
    } else {
      Serial.print(F("EVENT,LIMIT_STOP,"));
      Serial.print(axisName);
      Serial.print(',');
      Serial.println(limitName);
    }
    sendStatus(true);
  }

  if (anyAxisMoving() && millis() - lastCommandMillis > COMMAND_WATCHDOG_MS) {
    stopAll();
    Serial.println(F("EVENT,WATCHDOG_STOP"));
    sendStatus(true);
  }

  if (motionCompleted) {
    noInterrupts();
    char axisName = completedAxisName;
    motionCompleted = false;
    interrupts();
    Serial.print(F("EVENT,MOVE_DONE,"));
    Serial.println(axisName);
    sendStatus(true);
  }

  sendStatus(false);
}
