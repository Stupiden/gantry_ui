/*
  Milestone 1: Arduino Mega XYZ manual-motion foundation.

  Scope: position reporting, jog, relative move, immediate software STOP,
  six limit inputs, dual-Y synchronized STEP pulses, and a serial watchdog.
  Position is open-loop session position; homing is intentionally not included.
*/

#include <Arduino.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

const char CONTROLLER_ID[] = "MEGA_GANTRY_M1";

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

const float STEPS_PER_MM = 160.0f;
const float DEFAULT_SPEED_MM_S = 2.0f;
const float MIN_SPEED_MM_S = 0.05f;
const float MAX_SPEED_MM_S = 20.0f;
const unsigned int STEP_PULSE_US = 5;
const unsigned int DIR_SETUP_US = 10;
const unsigned long STATUS_INTERVAL_MS = 100;
const unsigned long COMMAND_WATCHDOG_MS = 750;
const size_t COMMAND_BUFFER_SIZE = 96;

enum MotionMode {
  MODE_IDLE,
  MODE_JOG,
  MODE_MOVE
};

struct AxisState {
  char name;
  long positionSteps;
  unsigned long remainingSteps;
  int8_t direction;
  MotionMode mode;
  unsigned long nextStepMicros;
};

AxisState xAxis = {'X', 0, 0, 0, MODE_IDLE, 0};
AxisState yAxis = {'Y', 0, 0, 0, MODE_IDLE, 0};
AxisState zAxis = {'Z', 0, 0, 0, MODE_IDLE, 0};

float speedMM = DEFAULT_SPEED_MM_S;
unsigned long stepIntervalMicros = 3125;
unsigned long lastStatusMillis = 0;
unsigned long lastCommandMillis = 0;
char commandBuffer[COMMAND_BUFFER_SIZE];
size_t commandLength = 0;

bool limitTriggered(uint8_t pin) {
  return digitalRead(pin) == LOW;
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
  // Confirmed convention: X+ LOW, Y+ HIGH, Z+ downward LOW.
  if (axis.name == 'X') {
    digitalWrite(X_DIR, direction > 0 ? LOW : HIGH);
  } else if (axis.name == 'Y') {
    uint8_t level = direction > 0 ? HIGH : LOW;
    digitalWrite(Y1_DIR, level);
    digitalWrite(Y2_DIR, level);
  } else {
    digitalWrite(Z_DIR, direction > 0 ? LOW : HIGH);
  }
}

void pulseAxis(const AxisState &axis) {
  if (axis.name == 'X') {
    digitalWrite(X_STEP, HIGH);
    delayMicroseconds(STEP_PULSE_US);
    digitalWrite(X_STEP, LOW);
  } else if (axis.name == 'Y') {
    digitalWrite(Y1_STEP, HIGH);
    digitalWrite(Y2_STEP, HIGH);
    delayMicroseconds(STEP_PULSE_US);
    digitalWrite(Y1_STEP, LOW);
    digitalWrite(Y2_STEP, LOW);
  } else {
    digitalWrite(Z_STEP, HIGH);
    delayMicroseconds(STEP_PULSE_US);
    digitalWrite(Z_STEP, LOW);
  }
}

void stopAxis(AxisState &axis) {
  axis.mode = MODE_IDLE;
  axis.remainingSteps = 0;
  axis.direction = 0;
}

void stopAll() {
  stopAxis(xAxis);
  stopAxis(yAxis);
  stopAxis(zAxis);
  digitalWrite(X_STEP, LOW);
  digitalWrite(Y1_STEP, LOW);
  digitalWrite(Y2_STEP, LOW);
  digitalWrite(Z_STEP, LOW);
}

bool startJog(AxisState &axis, int8_t direction) {
  if (!movementAllowed(axis, direction)) {
    Serial.print(F("ERROR,LIMIT_BLOCKED,"));
    Serial.print(axis.name);
    Serial.print(',');
    Serial.println(activeLimitName(axis, direction));
    return false;
  }
  stopAll();
  axis.direction = direction;
  setAxisDirection(axis, direction);
  axis.nextStepMicros = micros() + DIR_SETUP_US;
  axis.mode = MODE_JOG;
  return true;
}

bool startRelativeMove(AxisState &axis, float distanceMM) {
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
  setAxisDirection(axis, direction);
  axis.nextStepMicros = micros() + DIR_SETUP_US;
  axis.mode = MODE_MOVE;
  return true;
}

void updateAxis(AxisState &axis) {
  if (axis.mode == MODE_IDLE) return;

  if (!movementAllowed(axis, axis.direction)) {
    const char *limitName = activeLimitName(axis, axis.direction);
    stopAxis(axis);
    Serial.print(F("EVENT,LIMIT_STOP,"));
    Serial.print(axis.name);
    Serial.print(',');
    Serial.println(limitName);
    return;
  }

  unsigned long now = micros();
  if (static_cast<long>(now - axis.nextStepMicros) < 0) return;

  pulseAxis(axis);
  axis.positionSteps += axis.direction;
  axis.nextStepMicros = now + stepIntervalMicros;

  if (axis.mode == MODE_MOVE) {
    if (axis.remainingSteps > 0) axis.remainingSteps--;
    if (axis.remainingSteps == 0) stopAxis(axis);
  }
}

void applySpeed(float newSpeedMM) {
  speedMM = newSpeedMM;
  float stepsPerSecond = speedMM * STEPS_PER_MM;
  stepIntervalMicros = static_cast<unsigned long>(1000000.0f / stepsPerSecond);
  if (stepIntervalMicros <= STEP_PULSE_US) {
    stepIntervalMicros = STEP_PULSE_US + 1;
  }
}

void printPosition(const AxisState &axis) {
  Serial.print(static_cast<float>(axis.positionSteps) / STEPS_PER_MM, 3);
}

void sendStatus(bool force) {
  unsigned long now = millis();
  if (!force && now - lastStatusMillis < STATUS_INTERVAL_MS) return;
  lastStatusMillis = now;

  Serial.print(F("STATUS,X="));
  printPosition(xAxis);
  Serial.print(F(",Y="));
  printPosition(yAxis);
  Serial.print(F(",Z="));
  printPosition(zAxis);
  // Compact fields keep this periodic line below the Mega serial TX buffer.
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
  Serial.println(axisIsMoving(zAxis) ? 1 : 0);
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
  if (strcmp(verb, "JOG") == 0) {
    AxisState *axis = axisFromName(strtok_r(NULL, " ", &savePointer));
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

  applySpeed(DEFAULT_SPEED_MM_S);
  lastCommandMillis = millis();
  Serial.print(F("READY,"));
  Serial.println(CONTROLLER_ID);
  sendStatus(true);
}

void loop() {
  readSerial();

  if (anyAxisMoving() && millis() - lastCommandMillis > COMMAND_WATCHDOG_MS) {
    stopAll();
    Serial.println(F("EVENT,WATCHDOG_STOP"));
    sendStatus(true);
  }

  updateAxis(xAxis);
  updateAxis(yAxis);
  updateAxis(zAxis);
  sendStatus(false);
}
