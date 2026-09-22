/*
Purpose:
Calibrate a load cell with the ADS1220 by taring the unloaded sensor, measuring a known load, and displaying the raw ADC difference needed to calculate the calibration equation.
*/

#include <SPI.h>

const int RDY_PIN = 14;
const int NSS_PIN = 53;

// ADS1220 commands
#define CMD_RESET  0x06
#define CMD_START  0x08
#define CMD_RDATA  0x10
#define CMD_WREG   0x40

// ==========================
// Calibration / weighing settings
// ==========================

const int TARE_SAMPLES = 30;       // Tare average samples
const int MEASURE_SAMPLES = 30;    // Measurement average samples

long tareRaw = 0;


// ==========================
// ADS1220 register write
// ==========================

void writeRegister(byte reg, byte value)
{
  digitalWrite(NSS_PIN, LOW);
  delayMicroseconds(2);

  SPI.transfer(CMD_WREG | (reg << 2));
  SPI.transfer(value);

  delayMicroseconds(2);
  digitalWrite(NSS_PIN, HIGH);
}


// ==========================
// Read one ADC value
// ==========================

long readADC()
{
  // Start one conversion
  digitalWrite(NSS_PIN, LOW);
  SPI.transfer(CMD_START);
  digitalWrite(NSS_PIN, HIGH);

  // Wait until conversion complete
  unsigned long start = millis();

  while (digitalRead(RDY_PIN) == HIGH)
  {
    if (millis() - start > 1000)
    {
      Serial.println("ERROR: DRDY timeout");
      return 0;
    }
  }

  // Read 24-bit result
  digitalWrite(NSS_PIN, LOW);

  SPI.transfer(CMD_RDATA);

  uint8_t b1 = SPI.transfer(0xFF);
  uint8_t b2 = SPI.transfer(0xFF);
  uint8_t b3 = SPI.transfer(0xFF);

  digitalWrite(NSS_PIN, HIGH);

  long value =
      ((long)b1 << 16) |
      ((long)b2 << 8)  |
      b3;

  // 24-bit signed -> 32-bit signed
  if (value & 0x800000)
  {
    value |= 0xFF000000;
  }

  return value;
}


// ==========================
// Average multiple readings
// ==========================

long readAverage(int samples)
{
  int64_t sum = 0;

  for (int i = 0; i < samples; i++)
  {
    long raw = readADC();
    sum += raw;

    delay(20);
  }

  return (long)(sum / samples);
}


// ==========================
// Clear serial buffer
// ==========================

void clearSerialBuffer()
{
  while (Serial.available())
  {
    Serial.read();
  }
}


// ==========================
// Wait for ENTER
// ==========================

void waitForEnter()
{
  clearSerialBuffer();

  while (true)
  {
    if (Serial.available())
    {
      char c = Serial.read();

      if (c == '\n' || c == '\r')
      {
        delay(50);
        clearSerialBuffer();
        return;
      }
    }
  }
}


// ==========================
// TARE
// ==========================

void tare()
{
  Serial.println();
  Serial.println("================================");
  Serial.println("TARE");
  Serial.println("Remove everything from load cell.");
  Serial.println("Measuring zero...");
  Serial.println("================================");

  delay(1000);

  tareRaw = readAverage(TARE_SAMPLES);

  Serial.print("TARE RAW = ");
  Serial.println(tareRaw);

  Serial.println("Zero complete.");
}


// ==========================
// Setup
// ==========================

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

  /*
     REG0
     MUX = AIN0 - AIN1
     PGA gain = 1
     PGA enabled
  */
  writeRegister(0, 0x00);

  /*
     REG1
     Normal mode
     Single-shot
  */
  writeRegister(1, 0x00);

  /*
     REG2
     Internal 2.048 V reference
  */
  writeRegister(2, 0x00);

  writeRegister(3, 0x00);

  Serial.println();
  Serial.println("ADS1220 Load Cell Calibration Test");
  Serial.println("==================================");
  Serial.println();

  Serial.println("Step 1:");
  Serial.println("Remove everything from load cell.");
  Serial.println("Press ENTER to TARE.");

  waitForEnter();

  tare();
}


// ==========================
// Main loop
// ==========================

void loop()
{
  Serial.println();
  Serial.println("--------------------------------");
  Serial.println("Place object on load cell.");
  Serial.println("Wait until stable.");
  Serial.println("Press ENTER to measure.");
  Serial.println("--------------------------------");

  waitForEnter();

  // Absolute ADC reading
  long rawLoaded = readAverage(MEASURE_SAMPLES);

  // Difference relative to tare
  long rawDifference = rawLoaded - tareRaw;

  Serial.println();
  Serial.println("******** RESULT ********");

  Serial.print("TARE RAW       = ");
  Serial.println(tareRaw);

  Serial.print("LOADED RAW     = ");
  Serial.println(rawLoaded);

  Serial.print("RAW DIFFERENCE = ");
  Serial.println(rawDifference);

  Serial.println("***********************");


  Serial.println();
  Serial.println("Remove object from load cell.");
  Serial.println("Press ENTER after removing it.");
  
  waitForEnter();


  // Re-tare before next measurement
  tare();

  Serial.println();
  Serial.println("Ready for next object.");
}