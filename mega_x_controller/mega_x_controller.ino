/*
  Milestone 5: Arduino Mega motion, pumps, and S-type load-cell reading.

  Scope: position reporting, jog, relative move, immediate software STOP,
  six limit inputs, dual-Y synchronized STEP pulses, homing to MINUS limits,
  independent machine/scan coordinates, dual-Y driver alarm interlock,
  two fixed-direction peristaltic pumps, non-blocking ADS1220 force reading,
  and a serial watchdog. Press-down automation is intentionally not included.
*/

#include <Arduino.h>
#include <SPI.h>
#include <stdlib.h>
#include <string.h>

const char CONTROLLER_ID[] = "MEGA_GANTRY_M5";

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
const uint8_t PUMP_IN_STEP = 30;
const uint8_t PUMP_IN_DIR = 31;
const uint8_t PUMP_OUT_STEP = 32;
const uint8_t PUMP_OUT_DIR = 33;
const uint8_t ADS1220_DRDY = 14;
const uint8_t ADS1220_CS = 53;

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
const float PUMP_PULSES_PER_REV = 1600.0f;
const float DEFAULT_PUMP_RPM = 2.0f;
const float MIN_PUMP_RPM = 0.1f;
const float MAX_PUMP_RPM = 20.0f;
const uint16_t PUMP_HIGH_TICKS = 2;  // Timer3/4 /256: 32 us HIGH.
const unsigned long PUMP_TIMER_TICKS_PER_SECOND = F_CPU / 256UL;
const uint8_t ADS1220_CMD_RESET = 0x06;
const uint8_t ADS1220_CMD_START = 0x08;
const uint8_t ADS1220_CMD_RDATA = 0x10;
const uint8_t ADS1220_CMD_WREG = 0x40;
const float LOAD_CELL_CAL_SLOPE = 0.1165f;
const float LOAD_CELL_CAL_INTERCEPT = -0.6286f;
const uint8_t LOAD_CELL_FILTER_SAMPLES = 10;
const uint8_t LOAD_CELL_TARE_SAMPLES = 30;
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

struct PumpState {
  uint8_t stepPin;
  uint8_t dirPin;
  float rpm;
  volatile bool running;
  volatile bool pulseHigh;
  volatile uint16_t periodTicks;
};

AxisState xAxis = {'X', 0, 0, 0, MODE_IDLE, 0, 0, false};
AxisState yAxis = {'Y', 0, 0, 0, MODE_IDLE, 0, 0, false};
AxisState zAxis = {'Z', 0, 0, 0, MODE_IDLE, 0, 0, false};
PumpState pumps[2] = {
    {PUMP_IN_STEP, PUMP_IN_DIR, DEFAULT_PUMP_RPM, false, false, 0},
    {PUMP_OUT_STEP, PUMP_OUT_DIR, DEFAULT_PUMP_RPM, false, false, 0},
};

float speedMM = DEFAULT_SPEED_MM_S;
AxisState *volatile activeAxis = NULL;
volatile bool timerPulseHigh = false;
volatile bool motionCompleted = false;
volatile char completedAxisName = 0;
unsigned long lastStatusMillis = 0;
unsigned long lastCommandMillis = 0;
char commandBuffer[COMMAND_BUFFER_SIZE];
size_t commandLength = 0;
long loadCellRaw = 0;
long loadCellFilteredRaw = 0;
long loadCellTareRaw = 0;
long loadCellFilterBuffer[LOAD_CELL_FILTER_SAMPLES];
uint8_t loadCellFilterIndex = 0;
uint8_t loadCellFilterCount = 0;
int64_t loadCellFilterSum = 0;
int64_t loadCellTareSum = 0;
uint8_t loadCellTareCount = 0;
bool loadCellHasReading = false;
bool loadCellTareActive = true;
bool loadCellTared = false;
float loadCellForceGrams = 0.0f;

uint16_t timerPeriodForSpeed(float speedMMPerSecond);
uint16_t pumpTimerPeriod(float rpm);

void ads1220Command(uint8_t command) {
  digitalWrite(ADS1220_CS, LOW);
  SPI.transfer(command);
  digitalWrite(ADS1220_CS, HIGH);
}

void ads1220WriteRegister(uint8_t reg, uint8_t value) {
  digitalWrite(ADS1220_CS, LOW);
  delayMicroseconds(2);
  SPI.transfer(ADS1220_CMD_WREG | (reg << 2));
  SPI.transfer(value);
  delayMicroseconds(2);
  digitalWrite(ADS1220_CS, HIGH);
}

long ads1220ReadData() {
  digitalWrite(ADS1220_CS, LOW);
  SPI.transfer(ADS1220_CMD_RDATA);
  uint8_t b1 = SPI.transfer(0xFF);
  uint8_t b2 = SPI.transfer(0xFF);
  uint8_t b3 = SPI.transfer(0xFF);
  digitalWrite(ADS1220_CS, HIGH);
  long value = (static_cast<long>(b1) << 16) |
               (static_cast<long>(b2) << 8) | b3;
  if (value & 0x800000L) value |= 0xFF000000L;
  return value;
}

void beginTare() {
  loadCellTareSum = 0;
  loadCellTareCount = 0;
  loadCellTareActive = true;
  loadCellTared = false;
}

void updateLoadCellReading(long raw) {
  loadCellRaw = raw;
  if (loadCellFilterCount < LOAD_CELL_FILTER_SAMPLES) {
    loadCellFilterBuffer[loadCellFilterIndex] = raw;
    loadCellFilterSum += raw;
    loadCellFilterCount++;
  } else {
    loadCellFilterSum -= loadCellFilterBuffer[loadCellFilterIndex];
    loadCellFilterBuffer[loadCellFilterIndex] = raw;
    loadCellFilterSum += raw;
  }
  loadCellFilterIndex =
      (loadCellFilterIndex + 1) % LOAD_CELL_FILTER_SAMPLES;
  loadCellFilteredRaw =
      static_cast<long>(loadCellFilterSum / loadCellFilterCount);
  loadCellHasReading = true;

  if (loadCellTareActive) {
    loadCellTareSum += raw;
    loadCellTareCount++;
    if (loadCellTareCount >= LOAD_CELL_TARE_SAMPLES) {
      loadCellTareRaw =
          static_cast<long>(loadCellTareSum / LOAD_CELL_TARE_SAMPLES);
      loadCellTareActive = false;
      loadCellTared = true;
      Serial.print(F("EVENT,TARE_DONE,"));
      Serial.println(loadCellTareRaw);
    }
  }

  if (loadCellTared) {
    loadCellForceGrams =
        LOAD_CELL_CAL_SLOPE * (loadCellFilteredRaw - loadCellTareRaw) +
        LOAD_CELL_CAL_INTERCEPT;
  }
}

void serviceLoadCell() {
  if (digitalRead(ADS1220_DRDY) != LOW) return;
  updateLoadCellReading(ads1220ReadData());
  ads1220Command(ADS1220_CMD_START);
}

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

bool anyPumpRunning() {
  return pumps[0].running || pumps[1].running;
}

void stopPump(uint8_t pumpId) {
  if (pumpId > 1) return;
  noInterrupts();
  if (pumpId == 0) {
    TIMSK3 &= ~_BV(OCIE3A);
    TCCR3B = 0;
  } else {
    TIMSK4 &= ~_BV(OCIE4A);
    TCCR4B = 0;
  }
  pumps[pumpId].running = false;
  pumps[pumpId].pulseHigh = false;
  interrupts();
  digitalWrite(pumps[pumpId].stepPin, LOW);
}

void stopAllPumps() {
  stopPump(0);
  stopPump(1);
}

uint16_t pumpTimerPeriod(float rpm) {
  float pulsesPerSecond = rpm * PUMP_PULSES_PER_REV / 60.0f;
  unsigned long ticks = static_cast<unsigned long>(
      PUMP_TIMER_TICKS_PER_SECOND / pulsesPerSecond + 0.5f);
  if (ticks <= PUMP_HIGH_TICKS + 1) ticks = PUMP_HIGH_TICKS + 2;
  if (ticks > 65535UL) ticks = 65535UL;
  return static_cast<uint16_t>(ticks);
}

void applyPumpSpeed(uint8_t pumpId, float rpm) {
  if (pumpId > 1) return;
  uint16_t ticks = pumpTimerPeriod(rpm);
  noInterrupts();
  pumps[pumpId].rpm = rpm;
  pumps[pumpId].periodTicks = ticks;
  interrupts();
}

void startPump(uint8_t pumpId) {
  if (pumpId > 1) return;
  stopPump(pumpId);
  // Verified independent test: the negative direction is clockwise, which
  // corresponds to DIR LOW for both installed pump drivers.
  digitalWrite(pumps[pumpId].dirPin, LOW);
  delayMicroseconds(DIR_SETUP_US);

  noInterrupts();
  pumps[pumpId].pulseHigh = false;
  pumps[pumpId].running = true;
  if (pumpId == 0) {
    TCNT3 = 0;
    OCR3A = pumps[0].periodTicks - 1;
    TIFR3 = _BV(OCF3A);
    TCCR3A = 0;
    TCCR3B = _BV(WGM32);
    TIMSK3 = _BV(OCIE3A);
    TCCR3B |= _BV(CS32);  // Prescaler 256, 16 us/tick.
  } else {
    TCNT4 = 0;
    OCR4A = pumps[1].periodTicks - 1;
    TIFR4 = _BV(OCF4A);
    TCCR4A = 0;
    TCCR4B = _BV(WGM42);
    TIMSK4 = _BV(OCIE4A);
    TCCR4B |= _BV(CS42);  // Prescaler 256, 16 us/tick.
  }
  interrupts();
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

ISR(TIMER3_COMPA_vect) {
  if (!pumps[0].running) {
    TIMSK3 &= ~_BV(OCIE3A);
    return;
  }
  if (!pumps[0].pulseHigh) {
    OCR3A = PUMP_HIGH_TICKS - 1;
    digitalWrite(PUMP_IN_STEP, HIGH);
    pumps[0].pulseHigh = true;
  } else {
    OCR3A = pumps[0].periodTicks - PUMP_HIGH_TICKS - 1;
    digitalWrite(PUMP_IN_STEP, LOW);
    pumps[0].pulseHigh = false;
  }
}

ISR(TIMER4_COMPA_vect) {
  if (!pumps[1].running) {
    TIMSK4 &= ~_BV(OCIE4A);
    return;
  }
  if (!pumps[1].pulseHigh) {
    OCR4A = PUMP_HIGH_TICKS - 1;
    digitalWrite(PUMP_OUT_STEP, HIGH);
    pumps[1].pulseHigh = true;
  } else {
    OCR4A = pumps[1].periodTicks - PUMP_HIGH_TICKS - 1;
    digitalWrite(PUMP_OUT_STEP, LOW);
    pumps[1].pulseHigh = false;
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
  Serial.print(alarmTriggered(Y2_ALARM) ? 1 : 0);
  Serial.print(F(",P="));
  Serial.print(pumps[0].running ? 1 : 0);
  Serial.print(pumps[1].running ? 1 : 0);
  Serial.print(F(",PI="));
  Serial.print(pumps[0].rpm, 2);
  Serial.print(F(",PO="));
  Serial.print(pumps[1].rpm, 2);
  Serial.print(F(",LC="));
  Serial.print(loadCellHasReading ? 1 : 0);
  Serial.print(F(",T="));
  Serial.print(loadCellTareActive ? 1 : 0);
  Serial.print(F(",R="));
  Serial.print(loadCellRaw);
  Serial.print(F(",FR="));
  Serial.print(loadCellFilteredRaw);
  Serial.print(F(",F="));
  Serial.println(loadCellTared ? loadCellForceGrams : 0.0f, 2);
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
    stopAllPumps();
    Serial.println(F("OK,STOP"));
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "MOTION_STOP") == 0) {
    stopAll();
    Serial.println(F("OK,MOTION_STOP"));
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "TARE") == 0) {
    beginTare();
    Serial.println(F("OK,TARE_STARTED"));
    sendStatus(true);
    return;
  }
  if (strcmp(verb, "PUMP") == 0) {
    char *action = strtok_r(NULL, " ", &savePointer);
    char *pumpToken = strtok_r(NULL, " ", &savePointer);
    if (action == NULL || pumpToken == NULL || pumpToken[1] != '\0' ||
        (pumpToken[0] != '0' && pumpToken[0] != '1')) {
      Serial.println(F("ERROR,BAD_PUMP"));
      return;
    }
    uint8_t pumpId = static_cast<uint8_t>(pumpToken[0] - '0');
    if (strcmp(action, "START") == 0) {
      startPump(pumpId);
      Serial.print(F("OK,PUMP_START,"));
      Serial.println(pumpId);
    } else if (strcmp(action, "STOP") == 0) {
      stopPump(pumpId);
      Serial.print(F("OK,PUMP_STOP,"));
      Serial.println(pumpId);
    } else if (strcmp(action, "SPEED") == 0) {
      float requestedRpm;
      if (!parseFloatStrict(strtok_r(NULL, " ", &savePointer), requestedRpm) ||
          requestedRpm < MIN_PUMP_RPM || requestedRpm > MAX_PUMP_RPM) {
        Serial.println(F("ERROR,BAD_PUMP_SPEED"));
        return;
      }
      applyPumpSpeed(pumpId, requestedRpm);
      Serial.print(F("OK,PUMP_SPEED,"));
      Serial.print(pumpId);
      Serial.print(',');
      Serial.println(requestedRpm, 2);
    } else {
      Serial.println(F("ERROR,BAD_PUMP_ACTION"));
      return;
    }
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
  TCCR3A = 0;
  TCCR3B = 0;
  TIMSK3 = 0;
  TCCR4A = 0;
  TCCR4B = 0;
  TIMSK4 = 0;

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
  pinMode(PUMP_IN_STEP, OUTPUT);
  pinMode(PUMP_IN_DIR, OUTPUT);
  pinMode(PUMP_OUT_STEP, OUTPUT);
  pinMode(PUMP_OUT_DIR, OUTPUT);
  pinMode(ADS1220_CS, OUTPUT);
  pinMode(ADS1220_DRDY, INPUT);
  digitalWrite(PUMP_IN_STEP, LOW);
  digitalWrite(PUMP_OUT_STEP, LOW);
  digitalWrite(PUMP_IN_DIR, LOW);
  digitalWrite(PUMP_OUT_DIR, LOW);
  digitalWrite(ADS1220_CS, HIGH);

  SPI.begin();
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE1));
  delay(100);
  ads1220Command(ADS1220_CMD_RESET);
  delay(10);
  // Preserve the register configuration from the verified S-type load-cell
  // sketch. Conversion is serviced asynchronously in loop().
  ads1220WriteRegister(0, 0x00);
  ads1220WriteRegister(1, 0x00);
  ads1220WriteRegister(2, 0x00);
  ads1220WriteRegister(3, 0x00);
  beginTare();
  ads1220Command(ADS1220_CMD_START);

  applyPumpSpeed(0, DEFAULT_PUMP_RPM);
  applyPumpSpeed(1, DEFAULT_PUMP_RPM);

  applySpeed(DEFAULT_SPEED_MM_S);
  lastCommandMillis = millis();
  Serial.print(F("READY,"));
  Serial.println(CONTROLLER_ID);
  sendStatus(true);
}

void loop() {
  readSerial();
  serviceLoadCell();

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

  if ((anyAxisMoving() || anyPumpRunning()) &&
      millis() - lastCommandMillis > COMMAND_WATCHDOG_MS) {
    stopAll();
    stopAllPumps();
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
