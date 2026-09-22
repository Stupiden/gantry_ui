/*
Purpose:
Calibrate a cylindrical load cell with the ADS1220 by taring the unloaded sensor, measuring a known load, and displaying the raw ADC difference for calibration.
*/

#include <SPI.h>

// ============================================================
// PINS
// ============================================================

// ADS1220
const int RDY_PIN = 14;
const int NSS_PIN = 53;

// Z axis
const int Z_STEP = 4;
const int Z_DIR  = 5;

// Change LOW -> HIGH if Z moves in wrong direction
const int Z_DOWN = LOW;


// ============================================================
// Z SPEED
// ============================================================

// Slow descent
//
// 10000 us = 100 steps/s
// If 400 steps/mm:
//
// 100 / 400 = 0.25 mm/s
//
// This is much slower and better for gradual pressing.
const unsigned long STEP_INTERVAL_US = 10000;

const unsigned int STEP_PULSE_US = 5;


// ============================================================
// ADS1220 COMMANDS
// ============================================================

#define CMD_RESET  0x06
#define CMD_START  0x08
#define CMD_RDATA  0x10
#define CMD_WREG   0x40


// ============================================================
// CALIBRATION
// ============================================================

// OLD:
// 0.1644 g/count @ gain = 1
//
// NEW provisional value:
// 0.1644 / 128
//
// IMPORTANT:
// Recalibrate later with a known weight.

const float CAL_SLOPE = 0.001284375;

const float CAL_INTERCEPT = 0.6832;


// ============================================================
// STOP
// ============================================================

const float STOP_WEIGHT_G = 100.0;


// ============================================================
// TARE
// ============================================================

const int TARE_SAMPLES = 175;

long tareRaw = 0;


// ============================================================
// FAST FILTER
//
// Used for Z safety.
// 5 samples @ 175 SPS ≈ 29 ms
// ============================================================

const int FAST_N = 5;

float fastBuffer[FAST_N];

int fastIndex = 0;
int fastCount = 0;

float fastSum = 0;


// ============================================================
// DISPLAY FILTER
//
// Only affects displayed value.
// Does NOT delay Z stopping.
// ============================================================

const int DISPLAY_N = 30;

float displayBuffer[DISPLAY_N];

int displayIndex = 0;
int displayCount = 0;

float displaySum = 0;


// ============================================================
// STATE
// ============================================================

bool zRunning = false;
bool stopped = false;

unsigned long lastStepTime = 0;
unsigned long lastPrintTime = 0;


// ============================================================
// WRITE REGISTER
// ============================================================

void writeRegister(byte reg, byte value)
{
  digitalWrite(NSS_PIN, LOW);

  delayMicroseconds(2);

  SPI.transfer(CMD_WREG | (reg << 2));

  SPI.transfer(value);

  delayMicroseconds(2);

  digitalWrite(NSS_PIN, HIGH);
}


// ============================================================
// READ ADC
// ============================================================

long readADC()
{
  digitalWrite(NSS_PIN, LOW);

  SPI.transfer(CMD_RDATA);

  uint8_t b1 = SPI.transfer(0xFF);
  uint8_t b2 = SPI.transfer(0xFF);
  uint8_t b3 = SPI.transfer(0xFF);

  digitalWrite(NSS_PIN, HIGH);

  long value =
      ((long)b1 << 16) |
      ((long)b2 << 8) |
      b3;

  // Convert 24-bit signed number
  if (value & 0x800000)
  {
    value |= 0xFF000000;
  }

  return value;
}


// ============================================================
// RAW -> GRAMS
// ============================================================

float rawToGrams(long raw)
{
  long diff = raw - tareRaw;

  return CAL_SLOPE * diff
         + CAL_INTERCEPT;
}


// ============================================================
// CLEAR FILTERS
// ============================================================

void clearFilters()
{
  fastIndex = 0;
  fastCount = 0;
  fastSum = 0;

  displayIndex = 0;
  displayCount = 0;
  displaySum = 0;

  for (int i = 0; i < FAST_N; i++)
  {
    fastBuffer[i] = 0;
  }

  for (int i = 0; i < DISPLAY_N; i++)
  {
    displayBuffer[i] = 0;
  }
}


// ============================================================
// FAST FILTER
// ============================================================

float updateFastFilter(float value)
{
  if (fastCount < FAST_N)
  {
    fastBuffer[fastIndex] = value;

    fastSum += value;

    fastCount++;
  }
  else
  {
    fastSum -= fastBuffer[fastIndex];

    fastBuffer[fastIndex] = value;

    fastSum += value;
  }

  fastIndex++;

  if (fastIndex >= FAST_N)
  {
    fastIndex = 0;
  }

  return fastSum / fastCount;
}


// ============================================================
// DISPLAY FILTER
// ============================================================

float updateDisplayFilter(float value)
{
  if (displayCount < DISPLAY_N)
  {
    displayBuffer[displayIndex] = value;

    displaySum += value;

    displayCount++;
  }
  else
  {
    displaySum -= displayBuffer[displayIndex];

    displayBuffer[displayIndex] = value;

    displaySum += value;
  }

  displayIndex++;

  if (displayIndex >= DISPLAY_N)
  {
    displayIndex = 0;
  }

  return displaySum / displayCount;
}


// ============================================================
// TARE
// ============================================================

void tare()
{
  Serial.println();
  Serial.println("============================");
  Serial.println("TARE");
  Serial.println("DO NOT TOUCH LOAD CELL");
  Serial.println("Z WILL NOT MOVE");
  Serial.println("============================");

  // Allow system to settle
  delay(1500);

  int64_t sum = 0;

  int count = 0;

  // Wait for a conversion to become ready
  while (digitalRead(RDY_PIN) == HIGH)
  {
  }

  while (count < TARE_SAMPLES)
  {
    if (digitalRead(RDY_PIN) == LOW)
    {
      long raw = readADC();

      sum += raw;

      count++;

      // Wait until this conversion is finished
      // and ADS1220 starts next conversion
      while (digitalRead(RDY_PIN) == LOW)
      {
      }
    }
  }

  tareRaw =
      (long)(sum / TARE_SAMPLES);

  clearFilters();

  Serial.print("TARE RAW = ");
  Serial.println(tareRaw);

  Serial.println("TARE COMPLETE");
  Serial.println();
}


// ============================================================
// STOP Z
// ============================================================

void stopZ(float weight)
{
  zRunning = false;

  stopped = true;

  // Immediately stop STEP pulse generation
  digitalWrite(Z_STEP, LOW);

  Serial.println();
  Serial.println("############################");
  Serial.println("100 g LIMIT REACHED");
  Serial.println("Z STOPPED");

  Serial.print("STOP WEIGHT = ");
  Serial.print(weight, 2);
  Serial.println(" g");

  Serial.println("ADC CONTINUES READING");
  Serial.println("############################");
  Serial.println();
}


// ============================================================
// Z MOTION
// ============================================================

void updateZ()
{
  if (!zRunning)
  {
    return;
  }

  unsigned long now = micros();

  if (now - lastStepTime >= STEP_INTERVAL_US)
  {
    lastStepTime = now;

    digitalWrite(Z_STEP, HIGH);

    delayMicroseconds(STEP_PULSE_US);

    digitalWrite(Z_STEP, LOW);
  }
}


// ============================================================
// LOAD CELL
// ============================================================

void updateLoadCell()
{
  // No new ADS1220 conversion available
  if (digitalRead(RDY_PIN) == HIGH)
  {
    return;
  }

  long raw = readADC();

  float grams =
      rawToGrams(raw);


  // ----------------------------------------------------------
  // FAST SAFETY SIGNAL
  // ----------------------------------------------------------

  float fastWeight =
      updateFastFilter(grams);


  // ----------------------------------------------------------
  // STABLE DISPLAY SIGNAL
  // ----------------------------------------------------------

  float displayWeight =
      updateDisplayFilter(grams);


  // ==========================================================
  // SAFETY STOP
  // ==========================================================

  if (
      zRunning &&
      fastCount >= FAST_N &&
      fastWeight >= STOP_WEIGHT_G
     )
  {
    stopZ(fastWeight);
  }


  // ==========================================================
  // SERIAL DISPLAY
  // ==========================================================

  if (millis() - lastPrintTime >= 50)
  {
    lastPrintTime = millis();

    Serial.print("Weight = ");
    Serial.print(displayWeight, 2);

    Serial.print(" g   Fast = ");
    Serial.print(fastWeight, 2);

    Serial.print(" g   RAW = ");
    Serial.print(raw);

    if (zRunning)
    {
      Serial.println("   Z = DOWN");
    }
    else
    {
      Serial.println("   Z = STOPPED");
    }
  }


  // Avoid reading the same ADS conversion twice
  while (digitalRead(RDY_PIN) == LOW)
  {
  }
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
  Serial.begin(115200);


  // ==========================================================
  // Z AXIS
  // ==========================================================

  pinMode(Z_STEP, OUTPUT);
  pinMode(Z_DIR, OUTPUT);

  digitalWrite(Z_STEP, LOW);

  digitalWrite(Z_DIR, Z_DOWN);


  // ==========================================================
  // ADS1220
  // ==========================================================

  pinMode(NSS_PIN, OUTPUT);

  digitalWrite(NSS_PIN, HIGH);

  pinMode(RDY_PIN, INPUT);

  SPI.begin();

  SPI.beginTransaction(
    SPISettings(
      1000000,
      MSBFIRST,
      SPI_MODE1
    )
  );

  delay(100);


  // ==========================================================
  // RESET ADS1220
  // ==========================================================

  digitalWrite(NSS_PIN, LOW);

  SPI.transfer(CMD_RESET);

  digitalWrite(NSS_PIN, HIGH);

  delay(10);


  // ==========================================================
  // REGISTER 0
  //
  // MUX:
  // AIN0 - AIN1
  //
  // GAIN:
  // 128
  //
  // PGA:
  // enabled
  //
  // 0000 1110
  //
  // REG0 = 0x0E
  // ==========================================================

  writeRegister(0, 0x0E);


  // ==========================================================
  // REGISTER 1
  //
  // DR = 100
  //
  // 175 SPS
  //
  // Continuous conversion
  //
  // 1000 0100
  //
  // = 0x84
  // ==========================================================

  writeRegister(1, 0x84);


  // Same as previous working configuration
  writeRegister(2, 0x00);

  writeRegister(3, 0x00);


  // ==========================================================
  // START CONTINUOUS CONVERSION
  // ==========================================================

  digitalWrite(NSS_PIN, LOW);

  SPI.transfer(CMD_START);

  digitalWrite(NSS_PIN, HIGH);

  delay(100);


  Serial.println();
  Serial.println("============================");
  Serial.println("LOAD CELL + SLOW Z TEST");
  Serial.println("ADS1220 = 175 SPS");
  Serial.println("PGA = x128");
  Serial.println("Z SPEED = 0.25 mm/s");
  Serial.println("STOP = 100 g");
  Serial.println("============================");


  // ==========================================================
  // TARE
  // ==========================================================

  tare();


  // ==========================================================
  // START Z DOWN
  // ==========================================================

  Serial.println("STARTING SLOW Z DOWN...");
  Serial.println();

  digitalWrite(Z_DIR, Z_DOWN);

  lastStepTime = micros();

  zRunning = true;
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
  // Load cell gets highest priority
  updateLoadCell();

  // STEP pulses are generated only while safe
  updateZ();
}