/*
Purpose:
Move the Z axis downward at 0.25 mm/s while monitoring force with the ADS1220. Stop automatically when force reaches 100 g, a Z limit switch is triggered, or downward travel reaches 20 mm.
*/

#include <SPI.h>

// ============================================================
// PINS
// ============================================================

// ADS1220
const int RDY_PIN = 14;
const int NSS_PIN = 53;

// Z axis
const int Z_STEP = 4;
const int Z_DIR  = 5;

// Z limit switches: LOW = triggered
const int Z_MINUS = 25;
const int Z_PLUS  = 28;

// Confirmed working downward direction
const int Z_DOWN = LOW;


// ============================================================
// Z SPEED
// ============================================================

// 10 mm lead screw, 1600 STEP pulses/rev
// 1600 / 10 = 160 steps/mm
const float STEPS_PER_MM = 160.0;

// Slow pressing speed: 0.25 mm/s
// 0.25 mm/s * 160 steps/mm = 40 steps/s
// 1 / 40 s = 25,000 us between STEP pulses
const float Z_SPEED_MM_S = 0.25;
const unsigned long STEP_INTERVAL_US = 25000;
const unsigned int STEP_PULSE_US = 5;

// Maximum allowed downward travel
const float MAX_TRAVEL_MM = 20.0;
const long MAX_TRAVEL_STEPS = 3200;  // 20.0 mm * 160 steps/mm


// ============================================================
// ADS1220 COMMANDS
// ============================================================

#define CMD_RESET  0x06
#define CMD_START  0x08
#define CMD_RDATA  0x10
#define CMD_WREG   0x40


// ============================================================
// CALIBRATION
// ============================================================

// New calibration:
// Weight(g) = 0.1165 * raw_difference - 0.6286

const float CAL_SLOPE = 0.1165;
const float CAL_INTERCEPT = -0.6286;


// ============================================================
// STOP
// ============================================================

const float STOP_WEIGHT_G = 100.0;


// ============================================================
// TARE
// ============================================================

const int TARE_SAMPLES = 175;

long tareRaw = 0;


// ============================================================
// FAST FILTER
//
// Used for Z safety.
// 5 samples @ 175 SPS ≈ 29 ms
// ============================================================

const int FAST_N = 5;

float fastBuffer[FAST_N];

int fastIndex = 0;
int fastCount = 0;

float fastSum = 0;


// ============================================================
// DISPLAY FILTER
//
// Only affects displayed value.
// Does NOT delay Z stopping.
// ============================================================

const int DISPLAY_N = 30;

float displayBuffer[DISPLAY_N];

int displayIndex = 0;
int displayCount = 0;

float displaySum = 0;


// ============================================================
// STATE
// ============================================================

bool zRunning = false;
bool stopped = false;
long zStepCount = 0;

unsigned long lastStepTime = 0;
unsigned long lastPrintTime = 0;


// ============================================================
// WRITE REGISTER
// ============================================================