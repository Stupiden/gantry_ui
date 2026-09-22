/*
Purpose:
Move both Y-axis motors synchronously toward the Y− direction at a slow constant speed and stop both motors immediately when the Y− limit switch is triggered.
*/
#include <AccelStepper.h>

// ============================================================
// Y AXIS PINS
// ============================================================

const int Y1_STEP = 6;
const int Y1_DIR  = 7;

const int Y2_STEP = 11;
const int Y2_DIR  = 12;

// Limit switches
const int Y_MINUS = 8;
const int Y_PLUS  = 10;


// ============================================================
// MOTORS
// ============================================================

AccelStepper Y1(AccelStepper::DRIVER, Y1_STEP, Y1_DIR);
AccelStepper Y2(AccelStepper::DRIVER, Y2_STEP, Y2_DIR);


// ============================================================
// SETTINGS
// ============================================================

// 1600 pulses/rev
// 10 mm lead screw
// = 160 steps/mm

const float STEPS_PER_MM = 160.0;

// Very slow:
// 20 steps/s / 160 steps/mm
// = 0.125 mm/s
const float TEST_SPEED = 60.0;


// ============================================================
// SETUP
// ============================================================

void setup()
{
  Serial.begin(115200);

  // LOW = triggered
  pinMode(Y_MINUS, INPUT_PULLUP);
  pinMode(Y_PLUS, INPUT_PULLUP);

  Y1.setMinPulseWidth(5);
  Y2.setMinPulseWidth(5);

  Y1.setMaxSpeed(TEST_SPEED);
  Y2.setMaxSpeed(TEST_SPEED);

  Serial.println();
  Serial.println("==============================");
  Serial.println("Dual Y -> Y- limit test");
  Serial.println("==============================");

  Serial.println("Y1 STEP = 6");
  Serial.println("Y1 DIR  = 7");

  Serial.println("Y2 STEP = 11");
  Serial.println("Y2 DIR  = 12");

  Serial.println("Y- = D8");

  Serial.println();
  Serial.println("LOW direction test");
  Serial.println("Starting in 3 seconds...");

  delay(3000);


  // ==========================================================
  // SAFETY CHECK
  // ==========================================================

  if (digitalRead(Y_MINUS) == LOW)
  {
    Serial.println();
    Serial.println("Y- already triggered!");
    Serial.println("Movement cancelled.");

    while (true)
    {
    }
  }


  // ==========================================================
  // LOW / NEGATIVE DIRECTION
  // ==========================================================

  Y1.setSpeed(-TEST_SPEED);
  Y2.setSpeed(-TEST_SPEED);

  Serial.println();
  Serial.println("Moving toward Y-...");
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
  // ==========================================================
  // CHECK Y- LIMIT
  // ==========================================================

  if (digitalRead(Y_MINUS) == LOW)
  {
    Serial.println();
    Serial.println("==============================");
    Serial.println("Y- TRIGGERED!");
    Serial.println("Y1 + Y2 STOPPED");
    Serial.println("==============================");

    // No more runSpeed()
    // -> no more STEP pulses

    while (true)
    {
    }
  }


  // ==========================================================
  // RUN BOTH Y MOTORS
  // ==========================================================

  Y1.runSpeed();
  Y2.runSpeed();
}
