#include <AccelStepper.h>

// Arduino Mega pin assignments from the validated project pin table.
constexpr uint8_t PUMP_IN_STEP = 30;
constexpr uint8_t PUMP_IN_DIR = 31;
constexpr uint8_t PUMP_OUT_STEP = 32;
constexpr uint8_t PUMP_OUT_DIR = 33;

constexpr uint8_t PUMP_COUNT = 2;
constexpr float DEFAULT_PULSES_PER_REV = 1600.0f;
constexpr float DEFAULT_MAX_RPM = 2.0f;
constexpr float DEFAULT_ACCEL_RPM_S = 5.0f;
constexpr long CONTINUOUS_TARGET = 1000000000L;

AccelStepper inletPump(AccelStepper::DRIVER, PUMP_IN_STEP, PUMP_IN_DIR);
AccelStepper outletPump(AccelStepper::DRIVER, PUMP_OUT_STEP, PUMP_OUT_DIR);
AccelStepper *pumps[PUMP_COUNT] = {&inletPump, &outletPump};

float pulsesPerRev[PUMP_COUNT] = {
    DEFAULT_PULSES_PER_REV,
    DEFAULT_PULSES_PER_REV,
};
float maxRpm[PUMP_COUNT] = {DEFAULT_MAX_RPM, DEFAULT_MAX_RPM};
float accelerationRpmS[PUMP_COUNT] = {
    DEFAULT_ACCEL_RPM_S,
    DEFAULT_ACCEL_RPM_S,
};
bool pumpRunning[PUMP_COUNT] = {false, false};

String commandBuffer;

float rpmToStepsPerSecond(uint8_t pumpId, float rpm) {
  return rpm * pulsesPerRev[pumpId] / 60.0f;
}

void applySettings(uint8_t pumpId) {
  AccelStepper &pump = *pumps[pumpId];
  float speed = rpmToStepsPerSecond(pumpId, maxRpm[pumpId]);
  float acceleration =
      rpmToStepsPerSecond(pumpId, accelerationRpmS[pumpId]);

  // AccelStepper requires positive magnitudes for these two settings.
  pump.setMaxSpeed(max(0.001f, abs(speed)));
  pump.setAcceleration(max(0.001f, abs(acceleration)));

  if (pumpRunning[pumpId]) {
    pump.moveTo(speed >= 0.0f ? CONTINUOUS_TARGET : -CONTINUOUS_TARGET);
  }
}

void stopPump(uint8_t pumpId) {
  pumpRunning[pumpId] = false;
  pumps[pumpId]->stop();
}

void startPump(uint8_t pumpId) {
  applySettings(pumpId);
  pumps[pumpId]->enableOutputs();
  pumpRunning[pumpId] = true;
  float speed = rpmToStepsPerSecond(pumpId, maxRpm[pumpId]);
  pumps[pumpId]->moveTo(speed >= 0.0f ? CONTINUOUS_TARGET
                                      : -CONTINUOUS_TARGET);
}

bool parsePumpId(const String &token, uint8_t &pumpId) {
  if (token != "0" && token != "1") {
    return false;
  }
  pumpId = static_cast<uint8_t>(token.toInt());
  return true;
}

void replyError(const __FlashStringHelper *message) {
  Serial.print(F("ERR,"));
  Serial.println(message);
}

void processSetCommand(const String &command) {
  int firstSpace = command.indexOf(' ');
  int secondSpace = command.indexOf(' ', firstSpace + 1);
  int thirdSpace = command.indexOf(' ', secondSpace + 1);
  if (firstSpace < 0 || secondSpace < 0 || thirdSpace < 0) {
    replyError(F("BAD_SET_FORMAT"));
    return;
  }

  String idToken = command.substring(firstSpace + 1, secondSpace);
  String parameter = command.substring(secondSpace + 1, thirdSpace);
  String valueToken = command.substring(thirdSpace + 1);
  uint8_t pumpId;
  if (!parsePumpId(idToken, pumpId)) {
    replyError(F("BAD_PUMP_ID"));
    return;
  }

  float value = valueToken.toFloat();
  if (parameter == "PPR") {
    if (value < 1.0f) {
      replyError(F("PPR_MUST_BE_POSITIVE"));
      return;
    }
    pulsesPerRev[pumpId] = value;
  } else if (parameter == "MAXRPM") {
    maxRpm[pumpId] = value;
  } else if (parameter == "ACC") {
    if (value <= 0.0f) {
      replyError(F("ACC_MUST_BE_POSITIVE"));
      return;
    }
    accelerationRpmS[pumpId] = value;
  } else {
    replyError(F("UNKNOWN_PARAMETER"));
    return;
  }

  applySettings(pumpId);
  Serial.print(F("OK,SET,"));
  Serial.print(pumpId);
  Serial.print(',');
  Serial.print(parameter);
  Serial.print(',');
  Serial.println(value, 3);
}

void processRunCommand(const String &command, bool start) {
  int space = command.indexOf(' ');
  if (space < 0) {
    replyError(F("MISSING_PUMP_ID"));
    return;
  }

  uint8_t pumpId;
  if (!parsePumpId(command.substring(space + 1), pumpId)) {
    replyError(F("BAD_PUMP_ID"));
    return;
  }

  if (start) {
    startPump(pumpId);
    Serial.print(F("OK,START,"));
  } else {
    stopPump(pumpId);
    Serial.print(F("OK,STOP,"));
  }
  Serial.println(pumpId);
}

void processCommand(String command) {
  command.trim();
  command.toUpperCase();
  if (command.startsWith("SET ")) {
    processSetCommand(command);
  } else if (command.startsWith("START ")) {
    processRunCommand(command, true);
  } else if (command.startsWith("STOP ")) {
    processRunCommand(command, false);
  } else if (command == "STOPALL") {
    stopPump(0);
    stopPump(1);
    Serial.println(F("OK,STOPALL"));
  } else if (command == "STATUS") {
    for (uint8_t pumpId = 0; pumpId < PUMP_COUNT; ++pumpId) {
      Serial.print(F("STATUS,"));
      Serial.print(pumpId);
      Serial.print(',');
      Serial.print(pumpRunning[pumpId] ? F("RUNNING") : F("STOPPED"));
      Serial.print(',');
      Serial.print(maxRpm[pumpId], 3);
      Serial.print(',');
      Serial.println(pulsesPerRev[pumpId], 0);
    }
  } else if (command.length() > 0) {
    replyError(F("UNKNOWN_COMMAND"));
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char incoming = static_cast<char>(Serial.read());
    if (incoming == '\n' || incoming == '\r') {
      if (commandBuffer.length() > 0) {
        processCommand(commandBuffer);
        commandBuffer = "";
      }
    } else if (commandBuffer.length() < 95) {
      commandBuffer += incoming;
    } else {
      commandBuffer = "";
      replyError(F("COMMAND_TOO_LONG"));
    }
  }
}

void updatePumps() {
  for (uint8_t pumpId = 0; pumpId < PUMP_COUNT; ++pumpId) {
    pumps[pumpId]->run();
    if (!pumpRunning[pumpId] && pumps[pumpId]->distanceToGo() == 0) {
      pumps[pumpId]->disableOutputs();
    }
  }
}

void setup() {
  Serial.begin(115200);
  commandBuffer.reserve(96);

  for (uint8_t pumpId = 0; pumpId < PUMP_COUNT; ++pumpId) {
    pumps[pumpId]->setMinPulseWidth(3);
    applySettings(pumpId);
    pumps[pumpId]->disableOutputs();
  }

  Serial.println(F("READY,DUAL_PUMP_CONTROLLER"));
}

void loop() {
  readSerialCommands();
  updatePumps();
}
