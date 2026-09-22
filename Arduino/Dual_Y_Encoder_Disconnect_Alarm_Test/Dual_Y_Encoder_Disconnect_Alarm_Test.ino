/*
  Dual Y1/Y2 encoder-disconnect / driver-alarm test (Arduino Mega 2560)

  Operation:
    1. Both alarm inputs must first report NORMAL (HIGH).
    2. Disconnect/fault both encoder channels and wait until both inputs report
       ALARM (LOW). Each channel must make a debounced NORMAL-to-ALARM
       transition after startup.
    3. Press Enter in Serial Monitor. The sketch sends 160 synchronized STEP
       pulses to Y1 and Y2 at 100 Hz (about 1.6 seconds total).
    4. The batch aborts before the next pulse if either alarm input stops being
       active.

  IMPORTANT:
    - This is an engineering diagnostic, not a safety-rated validation.
    - The test assumes the verified active-low behavior:
          open contact   -> HIGH -> NORMAL
          closed contact -> LOW  -> ALARM
    - With 160 pulses/mm, 160 accepted pulses command approximately 1 mm.
      Confirm the actual drive microstep and mechanical calibration.
    - The two Y motors share one mechanical gantry. A failed driver can cause
      asymmetric motion/racking. Clear the mechanism and keep the hardware
      emergency stop within reach.
    - Never connect 24 V to an Arduino input.

  Wiring:
    Y1 STEP      -> Mega pin 6
    Y1 DIR       -> Mega pin 7
    Y2 STEP      -> Mega pin 11
    Y2 DIR       -> Mega pin 12
    Y1 ALM-      -> Mega GND
    Y1 ALM+      -> Mega pin 37, with 4.7 kOhm pull-up to Mega +5 V
    Y2 ALM-      -> Mega GND
    Y2 ALM+      -> Mega pin 38, with 4.7 kOhm pull-up to Mega +5 V
*/

const uint8_t Y1_STEP_PIN = 6;
const uint8_t Y1_DIR_PIN = 7;
const uint8_t Y2_STEP_PIN = 11;
const uint8_t Y2_DIR_PIN = 12;
const uint8_t Y1_ALARM_PIN = 37;
const uint8_t Y2_ALARM_PIN = 38;

const uint8_t ALARM_ACTIVE_LEVEL = LOW;
const uint8_t TEST_DIRECTION_LEVEL = LOW;

const uint16_t TEST_PULSE_COUNT = 160;
const unsigned int STEP_HIGH_US = 10;
const unsigned int STEP_PERIOD_US = 10000;  // 100 Hz; about 1.6 s per batch.

const unsigned long DEBOUNCE_MS = 20;
const unsigned long HEARTBEAT_MS = 1000;

struct AlarmChannel
{
  const char *name;
  uint8_t pin;
  int lastRawLevel;
  int stableLevel;
  unsigned long rawChangedAt;
  bool normalSeenSinceBoot;
  bool alarmTransitionArmed;
};

AlarmChannel y1Alarm = {"Y1", Y1_ALARM_PIN, HIGH, HIGH, 0, false, false};
AlarmChannel y2Alarm = {"Y2", Y2_ALARM_PIN, HIGH, HIGH, 0, false, false};
unsigned long lastHeartbeatAt = 0;

bool alarmIsActive(int level)
{
  return level == ALARM_ACTIVE_LEVEL;
}

void printChannelState(const __FlashStringHelper *prefix, const AlarmChannel &channel)
{
  Serial.print(prefix);
  Serial.print(F(",channel="));
  Serial.print(channel.name);
  Serial.print(F(",raw="));
  Serial.print(channel.stableLevel == HIGH ? F("HIGH") : F("LOW"));
  Serial.print(F(",state="));
  Serial.print(alarmIsActive(channel.stableLevel) ? F("ALARM") : F("NORMAL"));
  Serial.print(F(",armed="));
  Serial.println(channel.alarmTransitionArmed ? F("YES") : F("NO"));
}

void initializeAlarmChannel(AlarmChannel &channel)
{
  channel.lastRawLevel = digitalRead(channel.pin);
  channel.stableLevel = channel.lastRawLevel;
  channel.rawChangedAt = millis();
  channel.normalSeenSinceBoot = !alarmIsActive(channel.stableLevel);
  channel.alarmTransitionArmed = false;
}

void updateAlarmChannel(AlarmChannel &channel)
{
  const unsigned long now = millis();
  const int rawLevel = digitalRead(channel.pin);

  if (rawLevel != channel.lastRawLevel)
  {
    channel.lastRawLevel = rawLevel;
    channel.rawChangedAt = now;
  }

  if (rawLevel == channel.stableLevel || now - channel.rawChangedAt < DEBOUNCE_MS)
  {
    return;
  }

  channel.stableLevel = rawLevel;
  printChannelState(F("CHANGED"), channel);

  if (!alarmIsActive(channel.stableLevel))
  {
    channel.normalSeenSinceBoot = true;
    channel.alarmTransitionArmed = false;
    Serial.print(F("INTERLOCK: "));
    Serial.print(channel.name);
    Serial.println(F(" normal observed; waiting for alarm transition."));
  }
  else if (channel.normalSeenSinceBoot)
  {
    channel.alarmTransitionArmed = true;
    Serial.print(F("INTERLOCK_ARMED: "));
    Serial.print(channel.name);
    Serial.println(F(" normal-to-alarm transition observed."));
  }
}

bool channelReady(const AlarmChannel &channel)
{
  return channel.normalSeenSinceBoot &&
         channel.alarmTransitionArmed &&
         alarmIsActive(channel.stableLevel) &&
         alarmIsActive(digitalRead(channel.pin));
}

void sendSynchronizedTestBatch()
{
  updateAlarmChannel(y1Alarm);
  updateAlarmChannel(y2Alarm);

  if (!channelReady(y1Alarm) || !channelReady(y2Alarm))
  {
    Serial.println(F("REFUSED: both Y1 and Y2 require NORMAL-to-ALARM transitions."));
    printChannelState(F("CHECK"), y1Alarm);
    printChannelState(F("CHECK"), y2Alarm);
    return;
  }

  Serial.println(F("TEST_START,channels=Y1+Y2,pulses_requested=160,rate_hz=100"));
  uint16_t pulsesSent = 0;

  for (uint16_t i = 0; i < TEST_PULSE_COUNT; ++i)
  {
    if (!alarmIsActive(digitalRead(Y1_ALARM_PIN)) ||
        !alarmIsActive(digitalRead(Y2_ALARM_PIN)))
    {
      digitalWrite(Y1_STEP_PIN, LOW);
      digitalWrite(Y2_STEP_PIN, LOW);
      Serial.print(F("TEST_ABORT: an alarm input became inactive,pulses_sent="));
      Serial.println(pulsesSent);
      return;
    }

    // Raise and lower both STEP outputs together to minimize relative skew.
    digitalWrite(Y1_STEP_PIN, HIGH);
    digitalWrite(Y2_STEP_PIN, HIGH);
    delayMicroseconds(STEP_HIGH_US);
    digitalWrite(Y1_STEP_PIN, LOW);
    digitalWrite(Y2_STEP_PIN, LOW);
    ++pulsesSent;
    delayMicroseconds(STEP_PERIOD_US - STEP_HIGH_US);
  }

  Serial.print(F("TEST_COMPLETE,channels=Y1+Y2,pulses_sent="));
  Serial.println(pulsesSent);
}

void setup()
{
  Serial.begin(115200);

  pinMode(Y1_STEP_PIN, OUTPUT);
  pinMode(Y1_DIR_PIN, OUTPUT);
  pinMode(Y2_STEP_PIN, OUTPUT);
  pinMode(Y2_DIR_PIN, OUTPUT);
  pinMode(Y1_ALARM_PIN, INPUT_PULLUP);
  pinMode(Y2_ALARM_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);

  digitalWrite(Y1_STEP_PIN, LOW);
  digitalWrite(Y2_STEP_PIN, LOW);
  digitalWrite(Y1_DIR_PIN, TEST_DIRECTION_LEVEL);
  digitalWrite(Y2_DIR_PIN, TEST_DIRECTION_LEVEL);

  delay(50);
  initializeAlarmChannel(y1Alarm);
  initializeAlarmChannel(y2Alarm);

  Serial.println(F("DUAL_Y_ENCODER_DISCONNECT_ALARM_TEST_READY"));
  Serial.println(F("Y1 ALM=37, Y2 ALM=38; HIGH=NORMAL, LOW=ALARM."));
  Serial.println(F("Enter sends 160 synchronized pulses only after both alarm transitions."));
  Serial.println(F("Pulse rate: 100 Hz; nominal command: 1 mm at 160 pulses/mm."));
  printChannelState(F("INITIAL"), y1Alarm);
  printChannelState(F("INITIAL"), y2Alarm);
}

void loop()
{
  updateAlarmChannel(y1Alarm);
  updateAlarmChannel(y2Alarm);

  digitalWrite(
    LED_BUILTIN,
    alarmIsActive(y1Alarm.stableLevel) || alarmIsActive(y2Alarm.stableLevel) ? HIGH : LOW
  );

  bool enterReceived = false;
  while (Serial.available() > 0)
  {
    const char incoming = Serial.read();
    if (incoming == '\r' || incoming == '\n')
    {
      enterReceived = true;
    }
  }

  // CR+LF received together starts only one batch.
  if (enterReceived)
  {
    sendSynchronizedTestBatch();
  }

  const unsigned long now = millis();
  if (now - lastHeartbeatAt >= HEARTBEAT_MS)
  {
    lastHeartbeatAt = now;
    printChannelState(F("STATUS"), y1Alarm);
    printChannelState(F("STATUS"), y2Alarm);
  }
}
