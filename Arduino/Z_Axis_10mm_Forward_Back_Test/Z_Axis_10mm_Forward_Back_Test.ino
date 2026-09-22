#include <AccelStepper.h>

// X axis
const int Z_STEP = 4;
const int Z_DIR  =5;

/*
Purpose:
Test Z-axis motion with AccelStepper by moving 10 mm in one direction, returning 10 mm to the starting position, and then stopping.
*/

AccelStepper stepperX(AccelStepper::DRIVER, Z_STEP, Z_DIR);

// 10 mm lead, 1600 pulses/rev
const float STEPS_PER_MM = 160.0;

void setup()
{
  pinMode(Z_STEP, OUTPUT);
  pinMode(Z_DIR, OUTPUT);

  digitalWrite(Z_STEP, LOW);
  digitalWrite(Z_DIR, LOW);

  // Speed and acceleration
  stepperX.setMaxSpeed(400);       // 800 steps/s = 5 mm/s
  stepperX.setAcceleration(200);   // 400 steps/s²

  stepperX.setCurrentPosition(0);

  delay(1000);

  // =====================================
  // 1. Move +10 mm
  // =====================================
  stepperX.moveTo(10.0 * STEPS_PER_MM);

  while (stepperX.distanceToGo() != 0)
  {
    stepperX.run();
  }

  delay(1000);

  // =====================================
  // 2. Move back -10 mm to starting point
  // =====================================
  stepperX.moveTo(0);

  while (stepperX.distanceToGo() != 0)
  {
    stepperX.run();
  }

  // =====================================
  // 3. Finished - stay stopped
  // =====================================
  digitalWrite(Z_STEP, LOW);
}

void loop()
{
  // Do nothing.
  // No more STEP pulses are generated.
}