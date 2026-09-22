/*
Purpose:
Read and display the raw 24-bit ADC output from a load cell connected to the ADS1220 using single-shot conversions.
*/

#include <SPI.h>

const int RDY_PIN = 14;
const int NSS_PIN = 53;

// ADS1220 commands
#define CMD_RESET  0x06
#define CMD_START  0x08
#define CMD_RDATA  0x10
#define CMD_WREG   0x40

void writeRegister(byte reg, byte value)
{
  digitalWrite(NSS_PIN, LOW);
  delayMicroseconds(2);

  // WREG: 0100 rr nn
  // nn = 0 means write 1 register
  SPI.transfer(CMD_WREG | (reg << 2));
  SPI.transfer(value);

  delayMicroseconds(2);
  digitalWrite(NSS_PIN, HIGH);
}

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

  // Read 24-bit conversion result
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

  // Sign extension: 24-bit signed → 32-bit signed
  if (value & 0x800000)
    value |= 0xFF000000;

  return value;
}

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
     Single-shot conversion
     Conservative data rate
  */
  writeRegister(1, 0x00);

  /*
     REG2
     Internal 2.048 V reference
  */
  writeRegister(2, 0x00);

  writeRegister(3, 0x00);

  Serial.println();
  Serial.println("ADS1220 Load Cell RAW Test");
  Serial.println("--------------------------");
}

void loop()
{
  long raw = readADC();

  Serial.print("RAW = ");
  Serial.println(raw);

  delay(100);
}