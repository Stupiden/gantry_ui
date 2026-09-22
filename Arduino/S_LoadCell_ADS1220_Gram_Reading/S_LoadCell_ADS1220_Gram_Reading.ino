/*
Purpose:
Read an S-shaped load cell through the ADS1220, automatically tare it at startup, convert the measured signal to grams using the calibration equation, and display the weight in the Serial Monitor.
*/

#include <SPI.h>

const int RDY_PIN = 14;
const int NSS_PIN = 53;

// ADS1220 commands
#define CMD_RESET  0x06
#define CMD_START  0x08
#define CMD_RDATA  0x10
#define CMD_WREG   0x40

// Calibration
const float CAL_SLOPE = 0.1165;
const float CAL_INTERCEPT = -0.6286;

// Tare
const int TARE_SAMPLES = 30;
long tareRaw = 0;


// ========================
// Write ADS1220 register
// ========================

void writeRegister(byte reg, byte value)
{
  digitalWrite(NSS_PIN, LOW);
  delayMicroseconds(2);

  SPI.transfer(CMD_WREG | (reg << 2));
  SPI.transfer(value);

  delayMicroseconds(2);
  digitalWrite(NSS_PIN, HIGH);
}


// ========================
// Read one RAW value
// ========================

long readADC()
{
  digitalWrite(NSS_PIN, LOW);
  SPI.transfer(CMD_START);
  digitalWrite(NSS_PIN, HIGH);

  unsigned long start = millis();

  while (digitalRead(RDY_PIN) == HIGH)
  {
    if (millis() - start > 1000)
    {
      Serial.println("ERROR: DRDY timeout");
      return 0;
    }
  }

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

  // 24-bit signed -> 32-bit signed
  if (value & 0x800000)
  {
    value |= 0xFF000000;
  }

  return value;
}


// ========================
// Average RAW readings
// ========================

long readAverage(int samples)
{
  int64_t sum = 0;

  for (int i = 0; i < samples; i++)
  {
    sum += readADC();
    delay(20);
  }

  return (long)(sum / samples);
}


// ========================
// Tare
// ========================

void tare()
{
  Serial.println();
  Serial.println("TARE...");
  Serial.println("Keep load cell unloaded.");

  delay(1000);

  tareRaw = readAverage(TARE_SAMPLES);

  Serial.print("TARE RAW = ");
  Serial.println(tareRaw);

  Serial.println("TARE complete.");
  Serial.println();
}


// ========================
// Setup
// ========================

void setup()
{
  Serial.begin(115200);

  pinMode(NSS_PIN, OUTPUT);
  digitalWrite(NSS_PIN, HIGH);

  pinMode(RDY_PIN, INPUT);

  SPI.begin();

  SPI.beginTransaction(
    SPISettings(1000000, MSBFIRST, SPI_MODE1)
  );

  delay(100);

  // Reset ADS1220
  digitalWrite(NSS_PIN, LOW);
  SPI.transfer(CMD_RESET);
  digitalWrite(NSS_PIN, HIGH);

  delay(10);

  // Same configuration as your working RAW code
  writeRegister(0, 0x00);
  writeRegister(1, 0x00);
  writeRegister(2, 0x00);
  writeRegister(3, 0x00);

  Serial.println();
  Serial.println("ADS1220 Load Cell - Gram Reading");
  Serial.println("--------------------------------");

  // Automatic tare at startup
  tare();
}


// ========================
// Main loop
// ========================

void loop()
{
  // Average 10 measurements
  long raw = readAverage(10);

  // RAW relative to tare
  long rawDifference = raw - tareRaw;

  // Calibration equation
  float grams =
      CAL_SLOPE * rawDifference
      + CAL_INTERCEPT;

  Serial.print("Weight = ");
  Serial.print(grams, 2);
  Serial.println(" g");

  delay(100);
}