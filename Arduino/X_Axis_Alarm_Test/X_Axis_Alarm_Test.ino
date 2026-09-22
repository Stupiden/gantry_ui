/*
  Purpose:
  Test the X-axis Leadshine drive ALM output with an Arduino Mega 2560.

  The sketch monitors the alarm output and can issue one manual X STEP pulse
  when the character P is received over USB serial. The pulse may move the
  axis by one microstep. It does not guarantee an encoder alarm by itself.

  Wiring (5 V logic only):
    Drive ALM-  -> Arduino GND
    Drive ALM+  -> Arduino pin 29
    4.7 kOhm resistor from Arduino pin 29 to Arduino +5 V

  IMPORTANT:
    - Do not connect 24 V to an Arduino input.
    - This test assumes the alarm output closes during a fault. With the
      pull-up above, an open contact reads HIGH (NORMAL), while a closed
      contact pulls the input LOW (ALARM).
    - If the actual drive output behaves in the opposite direction, change
      ALARM_ACTIVE_LEVEL back to HIGH after verifying it electrically.
*/

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const uint8_t X_ALARM_PIN = 29;
const uint8_t ALARM_ACTIVE_LEVEL = LOW;
const uint8_t X_STEP_PIN = 2;
const unsigned int STEP_PULSE_US = 10;

// The external 4.7 kOhm pull-up is recommended. INPUT_PULLUP also keeps the
// pin defined if that resistor is temporarily absent during bench testing.
const uint8_t X_ALARM_PIN_MODE = INPUT_PULLUP;

const unsigned long DEBOUNCE_MS = 20;
const unsigned long HEARTBEAT_MS = 1000;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

int lastRawLevel = HIGH;
int stableLevel = HIGH;
unsigned long rawChangedAt = 0;
unsigned long lastHeartbeatAt = 0;

bool alarmIsActive(int level)
{
  return level == ALARM_ACTIVE_LEVEL;
}

void issueTestPulse()
{
  // STEP is held LOW at rest. One rising edge commands one driver step.
  digitalWrite(X_STEP_PIN, HIGH);
  delayMicroseconds(STEP_PULSE_US);
  digitalWrite(X_STEP_PIN, LOW);
  Serial.println(F("TEST_PULSE_SENT,count=1"));
}

void printState(const __FlashStringHelper *prefix, int level)
{
  Serial.print(prefix);
  Serial.print(F(",raw="));
  Serial.print(level == HIGH ? F("HIGH") : F("LOW"));
  Serial.print(F(",state="));
  Serial.println(alarmIsActive(level) ? F("ALARM") : F("NORMAL"));
}

void setup()
{
  Serial.begin(115200);
  pinMode(X_ALARM_PIN, X_ALARM_PIN_MODE);
  pinMode(X_STEP_PIN, OUTPUT);
  digitalWrite(X_STEP_PIN, LOW);
  pinMode(LED_BUILTIN, OUTPUT);

  delay(50);

  lastRawLevel = digitalRead(X_ALARM_PIN);
  stableLevel = lastRawLevel;
  rawChangedAt = millis();

  digitalWrite(LED_BUILTIN, alarmIsActive(stableLevel) ? HIGH : LOW);

  Serial.println(F("X_AXIS_ALARM_TEST_READY"));
  Serial.print(F("Pin "));
  Serial.print(X_ALARM_PIN);
  Serial.println(F(": HIGH=NORMAL, LOW=ALARM (active-low assumption)"));
  Serial.println(F("Built-in LED turns on when an alarm is detected."));
  Serial.println(F("Send P over serial to issue one 10 us STEP pulse on pin 2."));
  printState(F("INITIAL"), stableLevel);
}

void loop()
{
  const unsigned long now = millis();
  const int rawLevel = digitalRead(X_ALARM_PIN);

  while (Serial.available() > 0)
  {
    const char command = Serial.read();
    if (command == 'P' || command == 'p')
    {
      issueTestPulse();
    }
  }

  // Record when the raw input last changed. A new state is accepted only
  // after it remains unchanged for DEBOUNCE_MS.
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
  }

  if (now - lastHeartbeatAt >= HEARTBEAT_MS)
  {
    lastHeartbeatAt = now;
    printState(F("STATUS"), stableLevel);
  }
}
