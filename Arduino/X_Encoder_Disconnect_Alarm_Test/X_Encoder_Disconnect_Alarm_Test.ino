/*
  X-axis encoder-disconnect / driver-alarm test (Arduino Mega 2560)

  Test sequence:
    1. Power the system with the X axis mechanically clear and an emergency
       stop within reach.
    2. Confirm the serial output reports NORMAL before disturbing the encoder.
    3. Disconnect the encoder only by the drive/motor manufacturer's approved
       procedure. Wait until this sketch reports ALARM.
    4. Press Enter in the Serial Monitor. The sketch then sends exactly 160
       STEP pulses, unless the ALARM input ceases to be active.

  IMPORTANT:
    - This is an engineering diagnostic, not a safety-rated validation.
    - The test assumes the alarm contact closes during a fault:
          open contact   -> HIGH -> NORMAL
          closed contact -> LOW  -> ALARM
    - The sketch refuses to send pulses unless it has observed NORMAL first,
      followed by a transition to ALARM after startup. This prevents a
      polarity mismatch from authorizing a pulse batch immediately at boot.
    - At 160 pulses/mm, 160 accepted pulses command 1 mm of travel. The actual
      distance depends on the drive microstep setting and the mechanics.
    - Never connect 24 V to an Arduino input.

  Wiring used by the existing X-axis controller:
    X STEP       -> Mega pin 2
    X DIR        -> Mega pin 3
    Drive ALM-   -> Mega GND
    Drive ALM+   -> Mega pin 29
    4.7 kOhm pull-up from pin 29 to Mega +5 V
*/

const uint8_t X_STEP_PIN = 2;
const uint8_t X_DIR_PIN = 3;
const uint8_t X_ALARM_PIN = 29;

const uint8_t ALARM_ACTIVE_LEVEL = LOW;
const uint8_t TEST_DIRECTION_LEVEL = LOW;

const uint16_t TEST_PULSE_COUNT = 160;
const unsigned int STEP_HIGH_US = 10;
const unsigned int STEP_PERIOD_US = 10000;  // 100 Hz; batch lasts about 1.6 s.

const unsigned long DEBOUNCE_MS = 20;
const unsigned long HEARTBEAT_MS = 1000;

int lastRawLevel = HIGH;
int stableLevel = HIGH;
unsigned long rawChangedAt = 0;
unsigned long lastHeartbeatAt = 0;
bool normalSeenSinceBoot = false;
bool alarmTransitionArmed = false;

bool alarmIsActive(int level)
{
  return level == ALARM_ACTIVE_LEVEL;
}

void printState(const __FlashStringHelper *prefix, int level)
{
  Serial.print(prefix);
  Serial.print(F(",raw="));
  Serial.print(level == HIGH ? F("HIGH") : F("LOW"));
  Serial.print(F(",state="));
  Serial.println(alarmIsActive(level) ? F("ALARM") : F("NORMAL"));
}

void updateAlarmState()
{
  const unsigned long now = millis();
  const int rawLevel = digitalRead(X_ALARM_PIN);

  if (rawLevel != lastRawLevel)
  {
    lastRawLevel = rawLevel;
    rawChangedAt = now;
  }

  if (rawLevel != stableLevel && now - rawChangedAt >= DEBOUNCE_MS)
  {
    stableLevel = rawLevel;
    digitalWrite(LED_BUILTIN, alarmIsActive(stableLevel) ? HIGH : LOW);
    printState(F("CHANGED"), stableLevel);

    if (!alarmIsActive(stableLevel))
    {
      normalSeenSinceBoot = true;
      alarmTransitionArmed = false;
      Serial.println(F("INTERLOCK: normal state observed; waiting for alarm transition."));
    }
    else if (normalSeenSinceBoot)
    {
      alarmTransitionArmed = true;
      Serial.println(F("INTERLOCK_ARMED: normal-to-alarm transition observed."));
    }
  }
}

void sendTestPulseBatch()
{
  updateAlarmState();
  if (!normalSeenSinceBoot || !alarmTransitionArmed ||
      !alarmIsActive(stableLevel) || !alarmIsActive(digitalRead(X_ALARM_PIN)))
  {
    Serial.println(F("REFUSED: requires NORMAL after boot, then a stable ALARM transition."));
    return;
  }

  Serial.println(F("TEST_START,pulses_requested=160,rate_hz=100"));
  uint16_t pulsesSent = 0;

  for (uint16_t i = 0; i < TEST_PULSE_COUNT; ++i)
  {
    // Abort before every rising edge if the active-low alarm circuit opens.
    if (!alarmIsActive(digitalRead(X_ALARM_PIN)))
    {
      Serial.print(F("TEST_ABORT: alarm input became inactive,pulses_sent="));
      Serial.println(pulsesSent);
      digitalWrite(X_STEP_PIN, LOW);
      return;
    }

    digitalWrite(X_STEP_PIN, HIGH);
    delayMicroseconds(STEP_HIGH_US);
    digitalWrite(X_STEP_PIN, LOW);
    ++pulsesSent;
    delayMicroseconds(STEP_PERIOD_US - STEP_HIGH_US);
  }

  Serial.print(F("TEST_COMPLETE,pulses_sent="));
  Serial.println(pulsesSent);
}

void setup()
{
  Serial.begin(115200);

  pinMode(X_STEP_PIN, OUTPUT);
  pinMode(X_DIR_PIN, OUTPUT);
  pinMode(X_ALARM_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);

  digitalWrite(X_STEP_PIN, LOW);
  digitalWrite(X_DIR_PIN, TEST_DIRECTION_LEVEL);

  delay(50);
  lastRawLevel = digitalRead(X_ALARM_PIN);
  stableLevel = lastRawLevel;
  rawChangedAt = millis();
  digitalWrite(LED_BUILTIN, alarmIsActive(stableLevel) ? HIGH : LOW);
  normalSeenSinceBoot = !alarmIsActive(stableLevel);

  Serial.println(F("X_ENCODER_DISCONNECT_ALARM_TEST_READY"));
  Serial.println(F("HIGH=NORMAL, LOW=ALARM. Enter sends 160 pulses only during ALARM."));
  Serial.println(F("Pulse rate: 100 Hz. Nominal command: 1 mm only if calibration is 160 pulses/mm."));
  Serial.println(F("Interlock requires HIGH first, followed by a debounced transition to LOW."));
  printState(F("INITIAL"), stableLevel);
}

void loop()
{
  updateAlarmState();

  bool enterReceived = false;
  while (Serial.available() > 0)
  {
    const char incoming = Serial.read();
    if (incoming == '\r' || incoming == '\n')
    {
      enterReceived = true;
    }
  }

  // CR+LF in one serial message produces only one batch.
  if (enterReceived)
  {
    sendTestPulseBatch();
  }

  const unsigned long now = millis();
  if (now - lastHeartbeatAt >= HEARTBEAT_MS)
  {
    lastHeartbeatAt = now;
    printState(F("STATUS"), stableLevel);
  }
}
