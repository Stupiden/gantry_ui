#include <AccelStepper.h>

// ==========================
// Z AXIS
// ==========================
const int Z_STEP = 4;
const int Z_DIR  = 5;

// Z limit switches
const int Z_MINUS = 25;
const int Z_PLUS  = 28;

// 10 mm lead screw
// 1600 pulses/rev / 10 mm = 160 steps/mm
const float STEPS_PER_MM = 160.0;

// Slow test
const float SPEED_MM_S = 4.0;       // 1 mm/s
const float ACCEL_MM_S2 = 2.0;      // 1 mm/s²
const float MOVE_MM = 10.0;         // ±1 mm

AccelStepper zMotor(AccelStepper::DRIVER, Z_STEP, Z_DIR);


// ==========================
// SETUP
// ==========================
void setup()
{
  Serial.begin(115200);

  pinMode(Z_MINUS, INPUT_PULLUP);
  pinMode(Z_PLUS, INPUT_PULLUP);

  zMotor.setMaxSpeed(SPEED_MM_S * STEPS_PER_MM);
  zMotor.setAcceleration(ACCEL_MM_S2 * STEPS_PER_MM);

  Serial.println("================================");
  Serial.println("Z AXIS SLOW TEST");
  Serial.println("1. Move Z+");
  Serial.println("2. Move Z-");
  Serial.println("3. STOP");
  Serial.println("================================");

  delay(2000);

  // Start at zero
  zMotor.setCurrentPosition(0);

  // First move +10 mm
  Serial.println("Moving Z+...");
  zMotor.moveTo(MOVE_MM * STEPS_PER_MM);
}


// ==========================
// LOOP
// ==========================
enum State
{
  MOVE_POSITIVE,
  MOVE_NEGATIVE,
  FINISHED
};

State state = MOVE_POSITIVE;

void loop()
{
  // --------------------------
  // Z+ limit protection
  // --------------------------
  if (state == MOVE_POSITIVE &&
      digitalRead(Z_PLUS) == LOW)
  {
    zMotor.stop();

    while (zMotor.isRunning())
      zMotor.run();

    Serial.println("Z+ LIMIT TRIGGERED!");

    state = MOVE_NEGATIVE;

    zMotor.moveTo(0);
  }


  // --------------------------
  // Z- limit protection
  // --------------------------
  if (state == MOVE_NEGATIVE &&
      digitalRead(Z_MINUS) == LOW)
  {
    zMotor.stop();

    while (zMotor.isRunning())
      zMotor.run();

    Serial.println("Z- LIMIT TRIGGERED!");
    Serial.println("TEST FINISHED.");

    state = FINISHED;
  }


  // --------------------------
  // Normal movement
  // --------------------------
  if (state != FINISHED)
    zMotor.run();


  // --------------------------
  // Finished Z+ movement
  // --------------------------
  if (state == MOVE_POSITIVE &&
      zMotor.distanceToGo() == 0)
  {
    Serial.println("Z+ movement finished.");

    delay(1000);

    Serial.println("Moving Z-...");

    state = MOVE_NEGATIVE;

    zMotor.moveTo(0);
  }


  // --------------------------
  // Finished Z- movement
  // --------------------------
  if (state == MOVE_NEGATIVE &&
      zMotor.distanceToGo() == 0)
  {
    Serial.println("Z- movement finished.");
    Serial.println("Z MOTOR STOPPED.");

    state = FINISHED;
  }
}