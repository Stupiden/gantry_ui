/*
Purpose:
Test all X, Y, and Z positive/negative limit switches on the Arduino Mega 2560 and display their trigger status in the Serial Monitor. HOME pins are reserved and not used.
*/

// ============================================================
// TEST ALL +/- LIMIT SWITCHES
// Arduino Mega 2560
//
// LOW  = TRIGGERED
// HIGH = NOT TRIGGERED
//
// HOME pins are RESERVED ONLY, not tested.
// ============================================================

// X limits
const int X_MINUS = 22;
const int X_PLUS  = 24;

// Y limits
const int Y_MINUS = 8;
const int Y_PLUS  = 10;

// Z limits
const int Z_MINUS = 25;
const int Z_PLUS  = 28;

// Reserved HOME pins - NOT USED
// X_HOME  = 23
// Y1_HOME = 9
// Y2_HOME = 13
// Z_HOME  = 27


void setup()
{
  Serial.begin(115200);

  // NPN sensors: LOW = triggered
  pinMode(X_MINUS, INPUT_PULLUP);
  pinMode(X_PLUS,  INPUT_PULLUP);

  pinMode(Y_MINUS, INPUT_PULLUP);
  pinMode(Y_PLUS,  INPUT_PULLUP);

  pinMode(Z_MINUS, INPUT_PULLUP);
  pinMode(Z_PLUS,  INPUT_PULLUP);

  Serial.println("================================");
  Serial.println("XYZ +/- LIMIT SWITCH TEST");
  Serial.println("LOW  = TRIGGERED");
  Serial.println("HIGH = NOT TRIGGERED");
  Serial.println("HOME pins are reserved only.");
  Serial.println("================================");
}


void loop()
{
  bool xMinus = (digitalRead(X_MINUS) == LOW);
  bool xPlus  = (digitalRead(X_PLUS)  == LOW);

  bool yMinus = (digitalRead(Y_MINUS) == LOW);
  bool yPlus  = (digitalRead(Y_PLUS)  == LOW);

  bool zMinus = (digitalRead(Z_MINUS) == LOW);
  bool zPlus  = (digitalRead(Z_PLUS)  == LOW);

  Serial.print("X-:");
  Serial.print(xMinus ? "TRIGGERED" : "OK");

  Serial.print("  X+:");
  Serial.print(xPlus ? "TRIGGERED" : "OK");

  Serial.print("  | Y-:");
  Serial.print(yMinus ? "TRIGGERED" : "OK");

  Serial.print("  Y+:");
  Serial.print(yPlus ? "TRIGGERED" : "OK");

  Serial.print("  | Z-:");
  Serial.print(zMinus ? "TRIGGERED" : "OK");

  Serial.print("  Z+:");
  Serial.println(zPlus ? "TRIGGERED" : "OK");

  delay(200);
}