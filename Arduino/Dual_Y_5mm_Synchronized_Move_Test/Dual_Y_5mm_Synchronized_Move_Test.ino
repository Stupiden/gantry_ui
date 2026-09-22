/*
Purpose:
Test synchronized motion of both Y-axis motors by moving Y1 and Y2 together 5 mm in the positive direction using AccelStepper with controlled speed and acceleration.
*/

#include <AccelStepper.h>

// ============================================================
// PINS
// ============================================================

const int Y1_STEP = 6;
const int Y1_DIR  = 7;

const int Y2_STEP = 11;
const int Y2_DIR  = 12;


// ============================================================
// MOTORS
// DRIVER = STEP/DIR driver such as Leadshine CS-D508
// ============================================================

AccelStepper Y1(AccelStepper::DRIVER, Y1_STEP, Y1_DIR);
AccelStepper Y2(AccelStepper::DRIVER, Y2_STEP, Y2_DIR);


// ============================================================
// MECHANICAL SETTINGS
// ============================================================

// CS-D508: 1600 pulses/rev
// Leadscrew: 10 mm/rev
//
// 1600 / 10 = 160 steps/mm

const float STEPS_PER_MM = 160.0;


// ============================================================
// TEST SETTINGS
// ============================================================

// Only move 1 mm
const float MOVE_MM =10.0;

// VERY SLOW:
// 20 steps/s / 160 steps/mm = 0.125 mm/s
const float MAX_SPEED = 100.0;

// Gentle acceleration
const float ACCELERATION =40.0;


// ============================================================
// SETUP
// ============================================================

void setup()
{
  Serial.begin(115200);

  // ---------------- Y1 ----------------

  Y1.setMaxSpeed(MAX_SPEED);
  Y1.setAcceleration(ACCELERATION);

  // Minimum STEP pulse width for driver
  Y1.setMinPulseWidth(5);


  // ---------------- Y2 ----------------

  Y2.setMaxSpeed(MAX_SPEED);
  Y2.setAcceleration(ACCELERATION);

  Y2.setMinPulseWidth(5);


  // Current position = 0
  Y1.setCurrentPosition(0);
  Y2.setCurrentPosition(0);


  Serial.println("Dual Y slow test");
  Serial.println("Wait 3 seconds...");

  delay(3000);


  // ==========================================================
  // HIGH DIRECTION
  // ==========================================================

  long targetSteps = (long)(MOVE_MM * STEPS_PER_MM);

  Serial.print("Target steps: ");
  Serial.println(targetSteps);

  // Positive movement
  // Normally corresponds to DIR HIGH
  Y1.moveTo(targetSteps);
  Y2.moveTo(targetSteps);


  // ==========================================================
  // RUN BOTH MOTORS
  // ==========================================================

  while (Y1.distanceToGo() != 0 ||
         Y2.distanceToGo() != 0)
  {
    Y1.run();
    Y2.run();
  }


  Serial.println("Finished.");
  Serial.println("Moved 1 mm.");
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
  // Do nothing
}