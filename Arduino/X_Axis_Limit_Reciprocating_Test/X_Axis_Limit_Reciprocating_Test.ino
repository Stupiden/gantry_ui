/*
Purpose:
Continuously move the X axis back and forth between the X− and X+ limit switches using AccelStepper, automatically reversing direction whenever either limit switch is triggered.
*/

#include <AccelStepper.h>

// ============================================================
// X AXIS
// ============================================================

const int X_STEP  = 2;
const int X_DIR   = 3;

const int X_MINUS = 22;
const int X_PLUS  = 24;


// STEP + DIR driver
AccelStepper stepperX(AccelStepper::DRIVER, X_STEP, X_DIR);


// ============================================================
// MECHANICAL PARAMETERS
// ============================================================
//
// Lead screw = 10 mm/rev
// Driver     = 1600 pulses/rev
//
// 1600 / 10 = 160 steps/mm
//
const float STEPS_PER_MM = 160.0;


// ============================================================
// MOTION PARAMETERS
// ============================================================

// 320 steps/s = 2 mm/s
const float MAX_SPEED = 640.0;

// 320 steps/s² = 2 mm/s²
const float ACCELERATION = 320.0;


// ============================================================
// LONG TARGET
// ============================================================
//
// We don't know the exact distance between the limits.
// Therefore use a very large target.
//
// The limit switches determine the REAL travel boundaries.
//
const long LONG_TRAVEL = 1000000L;


// ============================================================
// DIRECTION
// ============================================================
//
// IMPORTANT:
//
// Your actual test confirmed:
//
// DIR HIGH -> X-
// DIR LOW  -> X+
//
// AccelStepper's +/- direction depends on DIR inversion.
//
// Set direction inversion here.
//
const bool INVERT_DIR = true;


// ============================================================
// STATE
// ============================================================

bool movingToPlus = false;


// ============================================================
// SETUP
// ============================================================

void setup()
{
  Serial.begin(115200);

  // Limit switches
  pinMode(X_MINUS, INPUT_PULLUP);
  pinMode(X_PLUS, INPUT_PULLUP);

  // Motor configuration
  stepperX.setMaxSpeed(MAX_SPEED);
  stepperX.setAcceleration(ACCELERATION);

  // Invert DIR so:
  // negative position -> X-
  // positive position -> X+
  stepperX.setPinsInverted(INVERT_DIR, false, false);

  stepperX.setCurrentPosition(0);

  delay(1000);

  Serial.println("================================");
  Serial.println("X AccelStepper reciprocating test");
  Serial.println("X- = pin 22");
  Serial.println("X+ = pin 24");
  Serial.println("LOW = triggered");
  Serial.println("================================");


  // ==========================================================
  // Start toward X-
  // ==========================================================

  movingToPlus = false;

  stepperX.moveTo(-LONG_TRAVEL);

  Serial.println("Start -> moving toward X-");
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
  bool xMinus = (digitalRead(X_MINUS) == LOW);
  bool xPlus  = (digitalRead(X_PLUS) == LOW);


  // ==========================================================
  // X- triggered
  // ==========================================================

  if (xMinus)
  {
    Serial.println("X- TRIGGERED -> reverse to X+");

    // Define this position as 0
    stepperX.setCurrentPosition(0);

    movingToPlus = true;

    // New target far toward X+
    stepperX.moveTo(LONG_TRAVEL);

    delay(20);
  }


  // ==========================================================
  // X+ triggered
  // ==========================================================

  if (xPlus)
  {
    Serial.println("X+ TRIGGERED -> reverse to X-");

    // Define this position as 0
    stepperX.setCurrentPosition(0);

    movingToPlus = false;

    // New target far toward X-
    stepperX.moveTo(-LONG_TRAVEL);

    delay(20);
  }


  // ==========================================================
  // AccelStepper motion
  // ==========================================================

  stepperX.run();
}