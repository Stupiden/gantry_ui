#include <AccelStepper.h>

// ============================================================
// X AXIS CONTROLLER - ARDUINO MEGA
// ============================================================
//
// X STEP  = 2
// X DIR   = 3
// X-      = 22   <-- ALSO USED AS HOME / ZERO
// X+      = 24
//
// LOW  = triggered
// HIGH = not triggered
//
// Lead screw: 10 mm/rev
// Driver:     1600 pulses/rev
//
// 160 steps/mm
// 0.00625 mm/step
//
// Coordinate system:
//
// X- = 0.000 mm
// Moving toward X+ = positive position
//
// ============================================================


// ============================================================
// PINS
// ============================================================

const int X_STEP  = 2;
const int X_DIR   = 3;

const int X_MINUS = 22;
const int X_PLUS  = 24;


// ============================================================
// MECHANICS
// ============================================================

const float STEPS_PER_MM = 160.0;


// ============================================================
// DIRECTION
//
// Your actual motor direction is reversed.
//
// Software +X must physically move toward X+.
// Software -X must physically move toward X-.
//
// ============================================================

const int X_DIR_SIGN = -1;


// ============================================================
// STEPPER
// ============================================================

AccelStepper xMotor(
    AccelStepper::DRIVER,
    X_STEP,
    X_DIR
);


// ============================================================
// MOTION SETTINGS
// ============================================================

float speedMM = 1.0;     // mm/s
float accelMM = 3.0;     // mm/s^2


// ============================================================
// MOTION MODE
// ============================================================

enum MotionMode
{
    MODE_IDLE,
    MODE_MOVE,
    MODE_JOG,
    MODE_HOME
};

MotionMode mode = MODE_IDLE;


// ============================================================
// JOG DIRECTION
//
// +1 = toward X+
// -1 = toward X-
//
// ============================================================

int jogDirection = 0;


// ============================================================
// SERIAL
// ============================================================

String commandBuffer = "";

unsigned long lastStatusTime = 0;

const unsigned long STATUS_INTERVAL = 100;


// ============================================================
// FUNCTION DECLARATIONS
//
// Explicit declarations avoid Arduino/C++ ordering problems.
// ============================================================

bool xMinusTriggered();
bool xPlusTriggered();

void applyMotionSettings();

float getXPositionMM();

bool movementAllowed(int softwareDirection);

void stopMotor();

void startRelativeMove(float mm);
void updateMove();

void startJog(int direction);
void updateJog();

void startHome();
void updateHome();

void sendStatus(bool force);

void processCommand(String cmd);
void readSerial();


// ============================================================
// LIMIT SWITCHES
// ============================================================

bool xMinusTriggered()
{
    return digitalRead(X_MINUS) == LOW;
}


bool xPlusTriggered()
{
    return digitalRead(X_PLUS) == LOW;
}


// ============================================================
// SPEED / ACCELERATION
// ============================================================

void applyMotionSettings()
{
    xMotor.setMaxSpeed(
        speedMM * STEPS_PER_MM
    );

    xMotor.setAcceleration(
        accelMM * STEPS_PER_MM
    );
}


// ============================================================
// POSITION
//
// AccelStepper stores MOTOR coordinates.
//
// Convert motor coordinates into our SOFTWARE X coordinates.
//
// ============================================================

float getXPositionMM()
{
    float motorMM =
        (float)xMotor.currentPosition()
        /
        STEPS_PER_MM;

    return motorMM * X_DIR_SIGN;
}


// ============================================================
// LIMIT CHECK
//
// +1 = trying to move toward X+
// -1 = trying to move toward X-
//
// A triggered limit ONLY blocks movement further INTO it.
//
// Example:
//
// X- triggered:
//     X- blocked
//     X+ allowed
//
// ============================================================

bool movementAllowed(int softwareDirection)
{
    if (softwareDirection > 0)
    {
        if (xPlusTriggered())
        {
            return false;
        }
    }


    if (softwareDirection < 0)
    {
        if (xMinusTriggered())
        {
            return false;
        }
    }


    return true;
}


// ============================================================
// IMMEDIATE STOP
// ============================================================

void stopMotor()
{
    mode = MODE_IDLE;

    jogDirection = 0;

    xMotor.setSpeed(0);

    xMotor.moveTo(
        xMotor.currentPosition()
    );
}


// ============================================================
// RELATIVE MOVE
//
// MOVE -10
// MOVE -1
// MOVE +1
// MOVE +10
//
// Uses BOTH:
// speedMM
// accelMM
//
// ============================================================

void startRelativeMove(float mm)
{
    if (mm == 0.0)
    {
        return;
    }


    int softwareDirection;

    if (mm > 0)
    {
        softwareDirection = +1;
    }
    else
    {
        softwareDirection = -1;
    }


    // Check limit before starting

    if (!movementAllowed(softwareDirection))
    {
        Serial.println("ERROR,LIMIT_BLOCKED");

        return;
    }


    // Cancel previous motion

    stopMotor();


    // Apply current UI speed / acceleration

    applyMotionSettings();


    // Convert mm -> software steps

    long softwareSteps =
        lround(
            mm * STEPS_PER_MM
        );


    // Convert software direction -> motor direction

    long motorSteps =
        softwareSteps * X_DIR_SIGN;


    xMotor.move(
        motorSteps
    );


    mode = MODE_MOVE;
}


// ============================================================
// UPDATE RELATIVE MOVE
// ============================================================

void updateMove()
{
    if (mode != MODE_MOVE)
    {
        return;
    }


    long remaining =
        xMotor.distanceToGo();


    // Finished

    if (remaining == 0)
    {
        mode = MODE_IDLE;

        return;
    }


    // Determine MOTOR direction

    int motorDirection;

    if (remaining > 0)
    {
        motorDirection = +1;
    }
    else
    {
        motorDirection = -1;
    }


    // Convert MOTOR direction back to SOFTWARE direction

    int softwareDirection =
        motorDirection * X_DIR_SIGN;


    // Check corresponding limit continuously

    if (!movementAllowed(softwareDirection))
    {
        stopMotor();

        Serial.println("LIMIT_STOP");

        return;
    }


    // Acceleration controlled movement

    xMotor.run();
}


// ============================================================
// START JOG
//
// JOG X+
// JOG X-
//
// ============================================================

void startJog(int direction)
{
    // Check limit

    if (!movementAllowed(direction))
    {
        Serial.println("ERROR,LIMIT_BLOCKED");

        return;
    }


    // Cancel previous movement

    stopMotor();


    jogDirection = direction;


    // Convert UI speed to step/s

    float motorSpeed =
        speedMM
        *
        STEPS_PER_MM
        *
        direction
        *
        X_DIR_SIGN;


    xMotor.setSpeed(
        motorSpeed
    );


    mode = MODE_JOG;
}


// ============================================================
// UPDATE JOG
// ============================================================

void updateJog()
{
    if (mode != MODE_JOG)
    {
        return;
    }


    // Check limit continuously

    if (!movementAllowed(jogDirection))
    {
        stopMotor();

        Serial.println("LIMIT_STOP");

        return;
    }


    // Constant-speed jogging

    xMotor.runSpeed();
}


// ============================================================
// START HOME
//
// IMPORTANT:
//
// X- IS HOME.
//
// HOME command:
//
// current position
//       ↓
// move toward X-
//       ↓
// X- becomes LOW
//       ↓
// STOP
//       ↓
// setCurrentPosition(0)
//
// ============================================================

void startHome()
{
    // Stop any existing movement first

    stopMotor();


    // --------------------------------------------------------
    // Already touching X-
    //
    // Then we are already at HOME.
    // Set zero immediately.
    // --------------------------------------------------------

    if (xMinusTriggered())
    {
        xMotor.setCurrentPosition(0);

        mode = MODE_IDLE;


        Serial.println("HOME_DONE");


        sendStatus(true);


        return;
    }


    // Apply UI speed / acceleration

    applyMotionSettings();


    // --------------------------------------------------------
    // Move toward SOFTWARE X-
    //
    // Give it a very large relative distance.
    // X- switch will terminate movement.
    // --------------------------------------------------------

    long softwareSteps =
        -1000L * 160L;


    long motorSteps =
        softwareSteps * X_DIR_SIGN;


    xMotor.move(
        motorSteps
    );


    mode = MODE_HOME;
}


// ============================================================
// UPDATE HOME
// ============================================================

void updateHome()
{
    if (mode != MODE_HOME)
    {
        return;
    }


    // --------------------------------------------------------
    // X- TRIGGERED
    //
    // THIS IS HOME.
    // --------------------------------------------------------

    if (xMinusTriggered())
    {
        // Stop pulse generation

        xMotor.setSpeed(0);


        // Cancel old target

        xMotor.moveTo(
            xMotor.currentPosition()
        );


        // ====================================================
        // SET HOME = EXACTLY ZERO
        // ====================================================

        xMotor.setCurrentPosition(0);


        mode = MODE_IDLE;


        Serial.println("HOME_DONE");


        // Force immediate UI update

        sendStatus(true);


        return;
    }


    // Continue acceleration-controlled HOME motion

    xMotor.run();
}


// ============================================================
// STATUS
// ============================================================

void sendStatus(bool force)
{
    if (!force)
    {
        if (
            millis() - lastStatusTime
            <
            STATUS_INTERVAL
        )
        {
            return;
        }
    }


    lastStatusTime = millis();


    // Position

    Serial.print("STATUS,X=");

    Serial.print(
        getXPositionMM(),
        3
    );


    // X-

    Serial.print(",XMIN=");

    Serial.print(
        xMinusTriggered()
        ? 1
        : 0
    );


    // X+

    Serial.print(",XPLUS=");

    Serial.print(
        xPlusTriggered()
        ? 1
        : 0
    );


    // HOME
    //
    // Since X- IS HOME,
    // HOME status = X- status.

    Serial.print(",HOME=");

    Serial.print(
        xMinusTriggered()
        ? 1
        : 0
    );


    // Moving

    Serial.print(",MOVING=");

    Serial.println(
        mode == MODE_IDLE
        ? 0
        : 1
    );
}


// ============================================================
// COMMAND PROCESSOR
// ============================================================

void processCommand(String cmd)
{
    cmd.trim();


    // --------------------------------------------------------
    // STOP
    // --------------------------------------------------------

    if (cmd == "STOP")
    {
        stopMotor();

        Serial.println("OK,STOP");

        return;
    }


    // --------------------------------------------------------
    // JOG X+
    // --------------------------------------------------------

    if (cmd == "JOG X+")
    {
        startJog(+1);

        Serial.println("OK,JOG_X_PLUS");

        return;
    }


    // --------------------------------------------------------
    // JOG X-
    // --------------------------------------------------------

    if (cmd == "JOG X-")
    {
        startJog(-1);

        Serial.println("OK,JOG_X_MINUS");

        return;
    }


    // --------------------------------------------------------
    // JOG STOP
    // --------------------------------------------------------

    if (cmd == "JOG STOP")
    {
        stopMotor();

        Serial.println("OK,JOG_STOP");

        return;
    }


    // --------------------------------------------------------
    // MOVE
    //
    // MOVE -10
    // MOVE -1
    // MOVE 1
    // MOVE 10
    //
    // --------------------------------------------------------

    if (cmd.startsWith("MOVE "))
    {
        float mm =
            cmd.substring(5).toFloat();


        startRelativeMove(mm);


        Serial.print("OK,MOVE,");

        Serial.println(mm);


        return;
    }


    // --------------------------------------------------------
    // SPEED
    //
    // Unit = mm/s
    // --------------------------------------------------------

    if (cmd.startsWith("SPEED "))
    {
        float value =
            cmd.substring(6).toFloat();


        if (
            value > 0
            &&
            value <= 50
        )
        {
            speedMM = value;


            applyMotionSettings();


            // If currently jogging,
            // update jog speed immediately.

            if (mode == MODE_JOG)
            {
                float motorSpeed =
                    speedMM
                    *
                    STEPS_PER_MM
                    *
                    jogDirection
                    *
                    X_DIR_SIGN;


                xMotor.setSpeed(
                    motorSpeed
                );
            }


            Serial.print("OK,SPEED,");

            Serial.println(speedMM);
        }


        return;
    }


    // --------------------------------------------------------
    // ACCEL
    //
    // Unit = mm/s^2
    // --------------------------------------------------------

    if (cmd.startsWith("ACCEL "))
    {
        float value =
            cmd.substring(6).toFloat();


        if (
            value > 0
            &&
            value <= 500
        )
        {
            accelMM = value;


            applyMotionSettings();


            Serial.print("OK,ACCEL,");

            Serial.println(accelMM);
        }


        return;
    }


    // --------------------------------------------------------
    // HOME
    // --------------------------------------------------------

    if (cmd == "HOME")
    {
        startHome();

        Serial.println("OK,HOME");

        return;
    }


    // --------------------------------------------------------
    // UNKNOWN COMMAND
    // --------------------------------------------------------

    Serial.print("ERROR,UNKNOWN,");

    Serial.println(cmd);
}


// ============================================================
// SERIAL READER
// ============================================================

void readSerial()
{
    while (Serial.available() > 0)
    {
        char c =
            Serial.read();


        // Complete command

        if (c == '\n')
        {
            processCommand(
                commandBuffer
            );


            commandBuffer = "";


            continue;
        }


        // Ignore CR

        if (c != '\r')
        {
            commandBuffer += c;
        }
    }
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(115200);


    // Limits

    pinMode(
        X_MINUS,
        INPUT_PULLUP
    );


    pinMode(
        X_PLUS,
        INPUT_PULLUP
    );


    // Step pulse width

    xMotor.setMinPulseWidth(5);


    // Initial speed settings

    applyMotionSettings();


    Serial.println(
        "X_CONTROLLER_READY"
    );


    sendStatus(true);
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    // Always process serial first.
    // STOP therefore has priority.

    readSerial();


    // --------------------------------------------------------
    // Motion state machine
    // --------------------------------------------------------

    if (mode == MODE_JOG)
    {
        updateJog();
    }

    else if (mode == MODE_MOVE)
    {
        updateMove();
    }

    else if (mode == MODE_HOME)
    {
        updateHome();
    }


    // 10 Hz UI status

    sendStatus(false);
}