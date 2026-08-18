#include <Arduino.h>
#include <Wire.h>
#include <VL53L0X.h>
#include <Adafruit_BMP280.h>
#include <BH1750.h>
#include <WiFi.h>
#include <HTTPClient.h>

// ═══════════════════════════════════════════════════════════════════════════
//  Wi-Fi credentials live in secrets.h, which is gitignored so they are never
//  committed. Copy firmware/src/secrets.example.h to firmware/src/secrets.h and
//  fill in your network and the LAN IP of the PC running app.py.
//
//  Without that file the placeholders below are used, so the project still
//  builds on a fresh clone - it simply will not associate until you add it.
// ═══════════════════════════════════════════════════════════════════════════
#define ENABLE_WIFI       1                 // 0 = USB serial only

#if __has_include("secrets.h")
  #include "secrets.h"
#endif

#ifndef WIFI_SSID
  #define WIFI_SSID       "YOUR_WIFI_NAME"
#endif
#ifndef WIFI_PASS
  #define WIFI_PASS       "YOUR_WIFI_PASSWORD"
#endif
#ifndef SERVER_HOST
  #define SERVER_HOST     "192.168.1.50"    // PC running app.py
#endif
#ifndef SERVER_PORT
  #define SERVER_PORT     5000
#endif

// How often to POST. The telemetry frame itself is built every TELEMETRY_MS
// (200 ms), but an HTTP POST blocks the loop while it completes, and the IR
// sampler must not miss its 4 ms cadence or short blinks slip through. 500 ms is
// a deliberate compromise: the server holds each frame for 30 s and re-broadcasts
// to the dashboard at 5 Hz regardless, so nothing downstream looks any slower.
#define WIFI_POST_MS      500
#define WIFI_HTTP_TIMEOUT 400               // ms; never stall the sensor loop
#define WIFI_RETRY_MS     5000              // reconnect attempt interval

#define I2C_SDA_DEFAULT 8
#define I2C_SCL_DEFAULT 9
// Actual pins in use. Start at the configured defaults; if nothing answers there,
// setup() probes alternatives (including SDA/SCL swapped, which is indisting-
// uishable from a dead bus in software) and adopts whichever pair finds a device.
int I2C_SDA = I2C_SDA_DEFAULT;
int I2C_SCL = I2C_SCL_DEFAULT;
#define IR_PIN 4  // HW-870 / TCRT5000 signal (works with either the AO or DO pin)
#define VIB_PIN 5 // Coin Vibration Motor Pin

#define I2C_CLOCK         100000

// ---- Blink detector tuning -------------------------------------------------
// The signal drifts by thousands of counts over tens of seconds (sensor shifting
// against the face, supply variation, ambient IR). A blink is a 100-400ms event.
// So detection is on FAST TRANSIENTS against a ~1s local reference, never on an
// absolute level: drift is tracked out, blinks stand proud of it.
#define IR_SAMPLE_MS      4      // IR poll period. MUST stay well under blink length.
#define IR_FAST_ALPHA     0.25f  // smoothing of the raw ADC (tau ~16ms)
#define IR_REF_ALPHA      0.004f // local reference (tau ~1s) - this is what kills drift
#define IR_SIGMA_K        5.0f   // trip at 5x the measured noise of the open signal
// Floor must clear the signal's ordinary wander (~50 counts observed), not just
// ADC quantisation. A real closure on this rig moves 1300+ counts, so there is a
// wide margin between "noise" and "blink" - sit well above the noise.
#define IR_DEV_FLOOR      20.0f  // Sensitive to small 20+ count photodiode movements
#define IR_DEV_CEILING    1200.0f// ...and never demand more than this
#define IR_DEBOUNCE_N     1      // 1 sample fast trigger
#define MIN_BLINK_MS      30     // shorter than this = electrical noise, not a blink
// Measured closures on this rig run 600-800ms: the sensor sees the lid travel, not
// just the shut instant, so the event is longer than the textbook 100-400ms blink.
#define MAX_BLINK_MS      900    // longer than this = squint, not a blink
#define DROWSY_MS         1200   // eyes held shut this long = drowsiness event
#define MAX_CLOSURE_MS    2000   // hard release: drift can never latch us shut
#define IR_WARMUP_MS      2000   // settle the reference before counting anything
#define BPM_WINDOW_MS     60000UL
#define BPM_MIN_ELAPSED_MS 5000UL
// Set to 1 only once the motor has a transistor driver + flyback diode. Wired
// straight to a GPIO it draws far more than the pin can source and browns out
// the 3.3V rail, which kills the I2C sensors mid-session.
#define ENABLE_HAPTIC     0
// Set to 1 to add IR diagnostics (raw / reference / noise) to the telemetry line.
#define IR_DEBUG          1

// ---- Telemetry / sensor cadence -------------------------------------------
#define TELEMETRY_MS      200
#define SLOW_SENSOR_MS    100
#define BMP_READ_MS       500
#define MPU_READ_MS       50

// ---- Sensor accuracy tuning -----------------------------------------------
// Calibration offset for the BMP280, in degrees C. Leave at 0 and the reported
// value is exactly what the chip measures (+/-1C datasheet accuracy). Only set
// this after comparing against a trusted thermometer in the same room - the
// previous hardcoded -3.2 was invented, not measured, and read 7C low.
#define TEMP_OFFSET_C     0.0f
#define TOF_TIMING_BUDGET 100000 // 100ms per ranging: accurate without starving the poll
#define TOF_MEDIAN_N      5      // median filter kills single-sample spikes
#define LUX_MEDIAN_N      3
#define TILT_ALPHA        0.2f   // smoothing on head tilt
#define MPU_ADDR_A        0x68
#define MPU_ADDR_B        0x69

VL53L0X distanceSensor;
Adafruit_BMP280 bmp;
BH1750 lightSensor;

bool tofOK = false, bmpOK = false, bhOK = false, mpuOK = false;
uint8_t mpuAddr = 0;
unsigned long sessionStartTime = 0;

// ---- Blink state ----------------------------------------------------------
int  blinkCount = 0;                  // cumulative, monotonic
unsigned long blinkDuration = 0;      // last accepted blink, ms
int  drowsyEvents = 0;
bool eyeClosed = false;               // debounced, confirmed state
bool rawClosed = false;               // instantaneous candidate state
int  debounceRun = 0;
unsigned long closeStartTime = 0;
bool longClosureReported = false;

float irFast = 0.0f;                  // lightly smoothed signal
float irRef  = 0.0f;                  // ~1s local reference (frozen during a closure)
float irMad  = 8.0f;                  // noise of (fast - ref) measured while open
int   lastIrRaw = 0;

#define BLINK_HIST 64
unsigned long blinkTimes[BLINK_HIST];
uint8_t blinkHead = 0;

// ---- Light sensor state ---------------------------------------------------
const byte MTREG_LADDER[3] = {31, 69, 254}; // coarse -> fine
int   mtIdx = 1;                            // start at the BH1750 default (69)
float luxFiltered = -1.0f;
int   lux = 0;
uint16_t luxBuf[LUX_MEDIAN_N];
uint8_t luxIdx = 0, luxFill = 0;
unsigned long bhFailStreak = 0;

// ---- Distance state -------------------------------------------------------
int currentDistCm = 0;
uint16_t distBuf[TOF_MEDIAN_N];
uint8_t distIdx = 0, distFill = 0;
unsigned long lastTofOkTime = 0, lastTofInitTry = 0;

// ---- Temperature / tilt state ---------------------------------------------
float roomTemp = 0.0f;
float headTiltDeg = 0.0f;
float tiltZeroRef = 0.0f;             // orientation captured at boot = "upright"
unsigned long i2cFailStreak = 0, lastI2CRecover = 0;

// Bus-level recovery: a slave interrupted mid-transfer can hold SDA low forever
// and wedge the bus. Clocking SCL 9 times lets it finish its byte and release.
//
// Only runs when SDA is ACTUALLY stuck low. Tearing the peripheral down and back
// up on a hunch is worse than the fault it treats - an unconditional version of
// this took every sensor offline until a power cycle.
bool i2cRecover() {
  pinMode(I2C_SDA, INPUT_PULLUP);
  if (digitalRead(I2C_SDA) == HIGH) {   // bus is fine; the fault is elsewhere
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(I2C_CLOCK);
    return false;
  }

  Wire.end();
  pinMode(I2C_SCL, OUTPUT);
  for (int i = 0; i < 9 && digitalRead(I2C_SDA) == LOW; i++) {
    digitalWrite(I2C_SCL, HIGH); delayMicroseconds(10);
    digitalWrite(I2C_SCL, LOW);  delayMicroseconds(10);
  }
  digitalWrite(I2C_SCL, HIGH);
  delayMicroseconds(10);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(I2C_CLOCK);
  return true;
}

void noteI2CFailure(unsigned long now) {
  // Ignore failures while the sensors are still powering up and settling.
  if (now - sessionStartTime < 3000) return;

  i2cFailStreak++;
  if (i2cFailStreak >= 25 && (now - lastI2CRecover) > 5000) {
    lastI2CRecover = now;
    i2cFailStreak = 0;
    if (i2cRecover()) {
      // Bus really was wedged: force every driver to re-initialise cleanly.
      tofOK = bmpOK = bhOK = mpuOK = false;
    }
  }
}

uint16_t medianOf(uint16_t *src, uint8_t n) {
  uint16_t tmp[TOF_MEDIAN_N];
  for (uint8_t i = 0; i < n; i++) tmp[i] = src[i];
  for (uint8_t i = 1; i < n; i++) {          // insertion sort, n is tiny
    uint16_t k = tmp[i];
    int8_t j = i - 1;
    while (j >= 0 && tmp[j] > k) { tmp[j + 1] = tmp[j]; j--; }
    tmp[j + 1] = k;
  }
  return tmp[n / 2];
}

static inline float mtregSaturationLux(byte mt) {
  // MODE_2 saturates at raw 65535: 65535 / 1.2 / 2 * (69 / MTreg)
  return 27306.25f * (69.0f / (float)mt);
}

void applyMtreg(int idx) {
  mtIdx = constrain(idx, 0, 2);
  lightSensor.setMTreg(MTREG_LADDER[mtIdx]);
  lightSensor.configure(BH1750::CONTINUOUS_HIGH_RES_MODE_2);
}

bool beginLightSensor() {
  bool ok = lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE_2, 0x23, &Wire) ||
            lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE_2, 0x5C, &Wire);
  if (ok) applyMtreg(mtIdx);
  return ok;
}

void initTof() {
  distanceSensor.setTimeout(300);
  tofOK = distanceSensor.init();
  if (tofOK) {
    // 200ms budget: the VL53L0X averages more photons per reading, which is what
    // actually buys accuracy. Safe here only because the read is polled, never
    // blocked on - see pollDistance().
    distanceSensor.setMeasurementTimingBudget(TOF_TIMING_BUDGET);
    distanceSensor.startContinuous();
    lastTofOkTime = millis();
    distFill = distIdx = 0;
  }
}

bool bmpBegin() {
  if (!(bmp.begin(0x76) || bmp.begin(0x77))) return false;
  // X16 oversampling on temperature (was X2) plus the strongest IIR filter: this
  // is the difference between +/-0.3C of jitter and a stable reading.
  bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                  Adafruit_BMP280::SAMPLING_X16,   // temperature
                  Adafruit_BMP280::SAMPLING_X2,    // pressure (unused here)
                  Adafruit_BMP280::FILTER_X16,
                  Adafruit_BMP280::STANDBY_MS_250);
  return true;
}

// ---- MPU6050 head tilt, via direct register access -------------------------
// Real posture sensing when the IMU is on the bus; the previous head_tilt_degrees
// was a millis()-driven sawtooth, which is why it swept to 35 degrees every 4
// seconds and kept tripping the Critical classifier.
bool mpuBegin() {
  const uint8_t addrs[2] = {MPU_ADDR_A, MPU_ADDR_B};
  for (uint8_t i = 0; i < 2; i++) {
    Wire.beginTransmission(addrs[i]);
    if (Wire.endTransmission() != 0) continue;

    Wire.beginTransmission(addrs[i]);
    Wire.write(0x6B); Wire.write(0x00);          // PWR_MGMT_1: wake from sleep
    if (Wire.endTransmission() != 0) continue;
    Wire.beginTransmission(addrs[i]);
    Wire.write(0x1A); Wire.write(0x03);          // CONFIG: 44Hz DLPF, kills vibration
    Wire.endTransmission();
    Wire.beginTransmission(addrs[i]);
    Wire.write(0x1C); Wire.write(0x00);          // ACCEL_CONFIG: +/-2g, finest scale
    Wire.endTransmission();

    mpuAddr = addrs[i];
    return true;
  }
  return false;
}

bool mpuReadTilt(float &pitchDeg) {
  Wire.beginTransmission(mpuAddr);
  Wire.write(0x3B);                              // ACCEL_XOUT_H
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)mpuAddr, 6) != 6) return false;

  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();
  if (ax == 0 && ay == 0 && az == 0) return false;   // all-zero = failed read

  // Pitch from the gravity vector. Absolute and drift-free, unlike a gyro.
  pitchDeg = atan2f(-(float)ax, sqrtf((float)ay * ay + (float)az * az)) * 57.2957795f;
  return true;
}

void registerBlink(unsigned long now, unsigned long duration) {
  blinkCount++;
  blinkDuration = duration;
  blinkTimes[blinkHead] = now;
  blinkHead = (blinkHead + 1) % BLINK_HIST;
}

// True blinks-per-minute over a rolling 60s window, not a session-long average.
int rollingBPM(unsigned long now) {
  unsigned long elapsed = now - sessionStartTime;
  if (elapsed < BPM_MIN_ELAPSED_MS) return 0;

  unsigned long window = (elapsed < BPM_WINDOW_MS) ? elapsed : BPM_WINDOW_MS;
  int inWindow = 0;
  for (int i = 0; i < BLINK_HIST; i++) {
    if (blinkTimes[i] != 0 && (now - blinkTimes[i]) <= window) inWindow++;
  }
  return (int)((inWindow * 60000.0f) / (float)window + 0.5f);
}

void releaseClosure() {
  eyeClosed = false;
  rawClosed = false;
  debounceRun = 0;
  longClosureReported = false;
  irRef = irFast;   // snap the reference back onto the signal
  // We tripped on something that outlasted any blink, so the threshold was too
  // tight. Widen it. This is the escape hatch from a too-sensitive noise estimate:
  // repeated false trips raise the bar until only real closures get through.
  irMad = min(irMad * 1.5f, 400.0f);
}

void sampleIR(unsigned long now) {
  int irVal = analogRead(IR_PIN);
  lastIrRaw = irVal;
  irFast += IR_FAST_ALPHA * ((float)irVal - irFast);

  // Warm-up: track hard, count nothing. Stops a bad boot seed from latching us shut.
  if (now - sessionStartTime < IR_WARMUP_MS) {
    irRef = irFast;
    eyeClosed = rawClosed = false;
    debounceRun = 0;
    return;
  }

  // A blink is a fast excursion from the local reference or a Digital DO pin flip
  int digVal = digitalRead(IR_PIN);
  float dev = fabsf(irFast - irRef);
  float margin = IR_SIGMA_K * irMad;
  margin = constrain(margin, IR_DEV_FLOOR, IR_DEV_CEILING);
  bool candidateClosed = (digVal == HIGH) || (eyeClosed ? (dev > margin * 0.40f) : (dev > margin));

  // Learn the reference and the noise from the open-eye signal only, so a blink
  // can never inflate the threshold that is meant to detect it. The noise estimate
  // learns from a CLIPPED deviation: ordinary wander is absorbed in full so irMad
  // can always grow to match the real noise, while a blink's huge excursion is
  // capped at 3x and cannot run the threshold away. (A hard gate here instead
  // deadlocks: it stops irMad growing exactly when the noise exceeds it.)
  if (!eyeClosed && !candidateClosed) {
    irRef += IR_REF_ALPHA * (irFast - irRef);
    float learn = min(dev, 3.0f * irMad);
    irMad += IR_REF_ALPHA * (learn - irMad);
    if (irMad < 3.0f) irMad = 3.0f;
  }

  // --- debounce: a state must hold for IR_DEBOUNCE_N samples to be accepted ---
  if (candidateClosed != rawClosed) {
    rawClosed = candidateClosed;
    debounceRun = 1;
  } else if (debounceRun < IR_DEBOUNCE_N) {
    debounceRun++;
  }
  if (debounceRun < IR_DEBOUNCE_N || rawClosed == eyeClosed) {
    if (eyeClosed) {
      unsigned long held = now - closeStartTime;
      if (!longClosureReported && held >= DROWSY_MS) {
        drowsyEvents++;
        longClosureReported = true;
      }
      if (held >= MAX_CLOSURE_MS) releaseClosure();
    }
    return;
  }

  if (rawClosed) {              // eye just closed
    eyeClosed = true;
    closeStartTime = now;
    longClosureReported = false;
  } else {                      // eye reopened
    eyeClosed = false;
    unsigned long duration = now - closeStartTime;
    if (duration >= MIN_BLINK_MS && duration <= MAX_BLINK_MS) {
      registerBlink(now, duration);
    }
  }
}

void pollLight(unsigned long now) {
  if (!bhOK) {
    static unsigned long lastBhTry = 0;
    if (now - lastBhTry > 2000) {
      lastBhTry = now;
      bhOK = beginLightSensor();
      if (bhOK) luxFill = luxIdx = 0;
    }
    return;
  }

  if (!lightSensor.measurementReady(false)) return; // respect the conversion time

  float rawLux = lightSensor.readLightLevel();
  if (rawLux < 0.0f) {                              // -1 / -2 = library error
    noteI2CFailure(now);
    if (++bhFailStreak > 5) { bhOK = false; bhFailStreak = 0; }
    return;
  }
  bhFailStreak = 0;

  // --- auto-range the measurement time register ---
  float satLux = mtregSaturationLux(MTREG_LADDER[mtIdx]);
  int newIdx = mtIdx;
  if (rawLux >= satLux * 0.85f)      newIdx = mtIdx - 1;  // too bright -> coarser
  else if (rawLux < 1000.0f && mtIdx == 1) newIdx = 2;    // dim -> 0.06 lx steps
  else if (rawLux < 3000.0f && mtIdx == 0) newIdx = 1;
  if (newIdx != mtIdx && newIdx >= 0 && newIdx <= 2) {
    applyMtreg(newIdx);
    luxFill = luxIdx = 0;                                // range changed, drop history
    return;
  }

  // Median first (rejects single-sample spikes outright), then a light EMA.
  // 0 lx is a legitimate reading for a dark room - only negatives are errors.
  luxBuf[luxIdx] = (uint16_t)min(rawLux, 65535.0f);
  luxIdx = (luxIdx + 1) % LUX_MEDIAN_N;
  if (luxFill < LUX_MEDIAN_N) luxFill++;

  float med = (float)medianOf(luxBuf, luxFill);
  luxFiltered = (luxFiltered < 0.0f) ? med : (luxFiltered * 0.6f + med * 0.4f);
  lux = (int)(luxFiltered + 0.5f);
}

void pollDistance(unsigned long now) {
  if (!tofOK) {
    if (now - lastTofInitTry > 2000) { lastTofInitTry = now; initTof(); }
    return;
  }

  // RESULT_INTERRUPT_STATUS (0x13): poll instead of blocking on the read, so the
  // 200ms timing budget costs accuracy-free latency rather than stalling the loop.
  bool ready = (distanceSensor.readReg(0x13) & 0x07) != 0;
  bool stalled = (now - lastTofOkTime) > 2000;
  if (!ready && !stalled) return;

  uint16_t rangeMm = distanceSensor.readRangeContinuousMillimeters();
  if (distanceSensor.timeoutOccurred()) {
    noteI2CFailure(now);
    if (stalled) { tofOK = false; lastTofInitTry = now; }
    return;
  }
  lastTofOkTime = now;

  // 8190/8191 are the sensor's out-of-range sentinels, not distances.
  if (rangeMm >= 8000 || rangeMm < 30 || rangeMm > 2000) return;

  distBuf[distIdx] = rangeMm;
  distIdx = (distIdx + 1) % TOF_MEDIAN_N;
  if (distFill < TOF_MEDIAN_N) distFill++;
  currentDistCm = (int)((medianOf(distBuf, distFill) + 5) / 10);  // round, not truncate
}

void pollTemp(unsigned long now) {
  static uint8_t bmpFailStreak = 0;
  if (!bmpOK) {
    bmpOK = bmpBegin();
    bmpFailStreak = 0;
    // Return without reading: MODE_NORMAL with X16 oversampling needs a conversion
    // to complete first, and reading the stale register here returns garbage that
    // immediately marks the sensor bad again - an init/fail flap.
    return;
  }
  float rawTemp = bmp.readTemperature();
  if (!isnan(rawTemp) && rawTemp > 5.0f && rawTemp < 60.0f) {
    roomTemp = rawTemp + TEMP_OFFSET_C;
    bmpFailStreak = 0;
  } else if (++bmpFailStreak >= 3) {   // one bad read is a glitch, three is a fault
    noteI2CFailure(now);
    bmpOK = false;
  }
}

void pollTilt(unsigned long now) {
  static uint8_t mpuFailStreak = 0;
  if (!mpuOK) {
    static unsigned long lastMpuTry = 0;
    if (now - lastMpuTry > 2000) {
      lastMpuTry = now;
      mpuOK = mpuBegin();
      mpuFailStreak = 0;
    }
    return;
  }
  float pitch;
  if (!mpuReadTilt(pitch)) {
    if (++mpuFailStreak >= 5) {
      noteI2CFailure(now);
      mpuOK = false;
    }
    return;
  }
  mpuFailStreak = 0;
  float rel = fabsf(pitch - tiltZeroRef);          // degrees away from upright
  headTiltDeg += TILT_ALPHA * (rel - headTiltDeg);
}

// Non-blocking haptic pulse - delay() here would blind the blink detector.
unsigned long vibUntil = 0, lastVibTime = 0;
void pulseHaptic(unsigned long now, unsigned long ms) {
#if ENABLE_HAPTIC
  if (now - lastVibTime < 10000) return; // rate-limit to one buzz per 10s
  digitalWrite(VIB_PIN, HIGH);
  vibUntil = now + ms;
  lastVibTime = now;
#endif
}

// Live bus inventory, reported in the telemetry so a missing chip is visible on
// the dashboard without opening a serial monitor. A sensor reading 0 is ambiguous;
// "no address on the bus answers" is not.
String i2cPresent = "";
int sdaLevel = -1, sclLevel = -1;

// Electrical state of the two lines when nothing answers. This separates the last
// two possibilities that look identical from software:
//   both HIGH -> lines idle and pulled up, but no device is listening
//                (sensors unpowered, or SDA/SCL not actually reaching them)
//   either LOW -> that line is shorted to ground or held down by a dead device
void checkBusLines() {
  Wire.end();
  pinMode(I2C_SDA, INPUT_PULLUP);
  pinMode(I2C_SCL, INPUT_PULLUP);
  delayMicroseconds(50);
  sdaLevel = digitalRead(I2C_SDA);
  sclLevel = digitalRead(I2C_SCL);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(I2C_CLOCK);
}

// Sweep the whole address space, not just the addresses we expect - a module at an
// unexpected address would otherwise look identical to no module at all.
int scanBus(String *listOut) {
  String s = "";
  int found = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      if (found < 8) {
        if (s.length()) s += " ";
        s += String(addr, HEX);
      }
      found++;
    }
  }
  if (listOut) *listOut = s;
  return found;
}

void refreshI2CInventory() {
  String s;
  if (scanBus(&s) == 0) {
    checkBusLines();
    i2cPresent = "none";
  } else {
    i2cPresent = s;
  }
}

void startI2C(int sda, int scl) {
  Wire.end();
  Wire.begin(sda, scl);
  Wire.setClock(I2C_CLOCK);
  delay(5);
}

// If the configured pins find nothing, try the alternatives before declaring the
// bus dead. Swapped SDA/SCL is the single most common wiring fault and produces
// exactly the "no device at any address" symptom. Only pins that are free on this
// board are probed - GPIO4/5 carry the IR sensor and motor, 19/20 are USB.
bool autoDetectI2CPins() {
  static const int8_t CANDIDATES[][2] = {
    {8, 9}, {9, 8}, {1, 2}, {2, 1}, {6, 7}, {7, 6},
    {10, 11}, {11, 10}, {17, 18}, {18, 17}, {21, 47}, {47, 21}
  };
  const int n = sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);

  for (int i = 0; i < n; i++) {
    int sda = CANDIDATES[i][0], scl = CANDIDATES[i][1];
    startI2C(sda, scl);
    String s;
    if (scanBus(&s) > 0) {
      I2C_SDA = sda; I2C_SCL = scl;
      Serial.print("I2C devices found on SDA=GPIO"); Serial.print(sda);
      Serial.print(" SCL=GPIO"); Serial.print(scl);
      Serial.print("  ->  "); Serial.println(s);
      if (sda != I2C_SDA_DEFAULT || scl != I2C_SCL_DEFAULT) {
        Serial.println("NOTE: this is NOT the configured pin pair. Update"
                       " I2C_SDA_DEFAULT / I2C_SCL_DEFAULT, or fix the wiring.");
      }
      return true;
    }
  }
  startI2C(I2C_SDA_DEFAULT, I2C_SCL_DEFAULT);   // nothing anywhere; restore defaults
  I2C_SDA = I2C_SDA_DEFAULT; I2C_SCL = I2C_SCL_DEFAULT;
  return false;
}

// ═══════════════════════════════════════════════════════════════════════════
//  Wi-Fi telemetry
//
//  Serial output is never disabled by any of this: the USB path keeps working
//  whether or not Wi-Fi associates, so a failed connection degrades to a cable
//  rather than to nothing.
// ═══════════════════════════════════════════════════════════════════════════
#if ENABLE_WIFI
String serverUrl;
unsigned long lastPostAttempt = 0, lastWifiRetry = 0, lastPostError = 0;
unsigned long postOk = 0, postFail = 0;

void wifiBegin() {
  serverUrl = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + "/sensor_data";

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);            // modem sleep adds hundreds of ms of latency
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("WiFi: connecting to \"" WIFI_SSID "\"");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 12000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi: connected. Spectacles IP ");
    Serial.print(WiFi.localIP());
    Serial.print("  RSSI "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");
    Serial.print("WiFi: posting telemetry to "); Serial.println(serverUrl);
  } else {
    Serial.println("WiFi: NOT connected. Check SSID/password and that the network"
                   " is 2.4 GHz. Telemetry continues over USB serial.");
    Serial.println("      Retrying in the background every 5 s.");
  }
}

// Non-blocking association retry: never sits in a loop waiting for the router.
void wifiEnsureConnected(unsigned long now) {
  if (WiFi.status() == WL_CONNECTED) return;
  if (now - lastWifiRetry < WIFI_RETRY_MS) return;
  lastWifiRetry = now;
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void wifiPost(unsigned long now, const String& payload) {
  if (WiFi.status() != WL_CONNECTED) return;
  if (now - lastPostAttempt < WIFI_POST_MS) return;
  lastPostAttempt = now;

  HTTPClient http;
  http.setReuse(true);                       // keep the socket, skip the handshake
  http.setConnectTimeout(WIFI_HTTP_TIMEOUT);
  http.setTimeout(WIFI_HTTP_TIMEOUT);
  http.begin(serverUrl);
  http.addHeader("Content-Type", "application/json");

  int code = http.POST(payload);
  http.end();

  if (code == 200) {
    postOk++;
  } else {
    postFail++;
    // Rate-limited so a downed server cannot flood the serial console.
    if (now - lastPostError > 5000) {
      lastPostError = now;
      Serial.print("WiFi: POST failed (");
      Serial.print(code);                    // negative = client-side error
      Serial.print(") to "); Serial.print(serverUrl);
      Serial.println("  - is app.py running, and does the firewall allow port 5000?");
    }
  }
}
#endif  // ENABLE_WIFI

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(IR_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(IR_PIN, ADC_11db); // full 0-3.3V swing on the IR output
  pinMode(VIB_PIN, OUTPUT);
  digitalWrite(VIB_PIN, LOW);

  // Locate the bus before initialising anything on it. Probing a driver against
  // pins that carry no bus wastes the one chance each sensor gets at boot.
  if (!autoDetectI2CPins()) {
    checkBusLines();
    Serial.println("No I2C device found on ANY candidate pin pair.");
    Serial.print("  SDA(GPIO"); Serial.print(I2C_SDA);
    Serial.print(")="); Serial.print(sdaLevel);
    Serial.print("  SCL(GPIO"); Serial.print(I2C_SCL);
    Serial.print(")="); Serial.println(sclLevel);
    Serial.println("  1/1 = lines idle and pulled up, nothing listening"
                   " -> sensors unpowered or SDA/SCL not reaching them.");
    Serial.println("  0 on either = that line shorted to GND or held by a dead"
                   " device.");
  }

  initTof();
  bmpOK = bmpBegin();
  bhOK = beginLightSensor();
  mpuOK = mpuBegin();

  refreshI2CInventory();

  // Capture the boot orientation as "upright" so head_tilt_degrees is measured
  // relative to how the glasses actually sit, not to the IMU's chip axes.
  if (mpuOK) {
    float sum = 0; int n = 0;
    for (int i = 0; i < 20; i++) {
      float p;
      if (mpuReadTilt(p)) { sum += p; n++; }
      delay(10);
    }
    if (n > 0) tiltZeroRef = sum / n;
  }

  irFast = irRef = (float)analogRead(IR_PIN);
  for (int i = 0; i < BLINK_HIST; i++) blinkTimes[i] = 0;

  sessionStartTime = millis();

#if ENABLE_WIFI
  wifiBegin();
#else
  Serial.println("WiFi: disabled at compile time (ENABLE_WIFI 0) - USB serial only.");
#endif

  Serial.println("Netra Rakshaka Hardware Firmware Ready!");
  Serial.print("ToF: "); Serial.print(tofOK);
  Serial.print(" BMP280: "); Serial.print(bmpOK);
  Serial.print(" BH1750: "); Serial.print(bhOK);
  Serial.print(" MPU6050: "); Serial.println(mpuOK);
  if (!mpuOK) Serial.println("No IMU found - head_tilt_degrees will report 0.");
  Serial.println("No humidity sensor on this build - room_humidity_pct is a"
                 " placeholder (needs a DHT22 / SHT31 / BME280).");
}

void loop() {
  unsigned long now = millis();
  static unsigned long lastIrSample = 0, lastSlow = 0, lastBmp = 0,
                       lastMpu = 0, lastTelemetry = 0;

  // 1. IR EYE BLINK DETECTOR - sampled every 4ms so a 100-400ms blink cannot be missed
  if (now - lastIrSample >= IR_SAMPLE_MS) {
    lastIrSample = now;
    sampleIR(now);
  }

  // 2-5. Non-blocking I2C sensors
  if (now - lastSlow >= SLOW_SENSOR_MS) {
    lastSlow = now;
    pollDistance(now);
    pollLight(now);
  }
  if (now - lastBmp >= BMP_READ_MS) {
    lastBmp = now;
    pollTemp(now);
  }
  if (now - lastMpu >= MPU_READ_MS) {
    lastMpu = now;
    pollTilt(now);
  }
  // Re-probe the expected addresses only while something is missing, so a wire
  // that comes loose (or gets pushed back in) shows up within a few seconds.
  static unsigned long lastInv = 0;
  if ((!bmpOK || !bhOK || !mpuOK || !tofOK) && now - lastInv >= 5000) {
    lastInv = now;
    refreshI2CInventory();
  }

  // Haptic feedback: dry-eye (low BPM) or a drowsiness event
  int calculatedBPM = rollingBPM(now);
  float elapsedSec = (now - sessionStartTime) / 1000.0f;
  static int lastDrowsyEvents = 0;
  if (drowsyEvents != lastDrowsyEvents) {
    lastDrowsyEvents = drowsyEvents;
    pulseHaptic(now, 400);
  } else if (elapsedSec >= 60.0f && calculatedBPM < 8) {
    pulseHaptic(now, 200);
  }
  if (vibUntil && now >= vibUntil) { digitalWrite(VIB_PIN, LOW); vibUntil = 0; }

  // 6. OUTPUT TELEMETRY OVER USB SERIAL
  if (now - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = now;

    // No humidity sensor exists on this build. Reported as a fixed placeholder and
    // flagged via humidity_ok so nothing downstream mistakes it for a measurement.
    int humidity = 50;

    String jsonPayload = "{";
    jsonPayload += "\"blink_rate\":" + String(calculatedBPM) + ",";
    jsonPayload += "\"blink_count\":" + String(blinkCount) + ",";
    jsonPayload += "\"blink_duration_ms\":" + String(blinkDuration) + ",";
    jsonPayload += "\"eye_temp_celsius\":" + String(roomTemp, 1) + ",";
    jsonPayload += "\"room_temp_celsius\":" + String(roomTemp, 1) + ",";
    jsonPayload += "\"screen_distance_cm\":" + String(currentDistCm) + ",";
    jsonPayload += "\"ambient_lux\":" + String(lux) + ",";
    jsonPayload += "\"room_humidity_pct\":" + String(humidity) + ",";
    jsonPayload += "\"head_tilt_degrees\":" + String((int)(headTiltDeg + 0.5f)) + ",";
    jsonPayload += "\"drowsy_events\":" + String(drowsyEvents) + ",";
    jsonPayload += "\"tof_ok\":" + String(tofOK ? 1 : 0) + ",";
    jsonPayload += "\"bmp_ok\":" + String(bmpOK ? 1 : 0) + ",";
    jsonPayload += "\"bh_ok\":" + String(bhOK ? 1 : 0) + ",";
    jsonPayload += "\"mpu_ok\":" + String(mpuOK ? 1 : 0) + ",";
    jsonPayload += "\"humidity_ok\":0,";
    jsonPayload += "\"i2c\":\"" + i2cPresent + "\",";
    jsonPayload += "\"sda\":" + String(sdaLevel) + ",";
    jsonPayload += "\"scl\":" + String(sclLevel);
#if ENABLE_WIFI
    jsonPayload += ",\"wifi_rssi\":" +
                   String(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0);
#endif
#if IR_DEBUG
    jsonPayload += ",\"ir_raw\":" + String(lastIrRaw);
    jsonPayload += ",\"ir_baseline\":" + String((int)irRef);
    jsonPayload += ",\"ir_mad\":" + String((int)irMad);
    jsonPayload += ",\"eye_closed\":" + String(eyeClosed ? 1 : 0);
#endif
    jsonPayload += "}";

    // USB serial always goes out first, so a slow or failing POST can never cost
    // you the cable path.
    Serial.print("DATA:");
    Serial.println(jsonPayload);

#if ENABLE_WIFI
    wifiEnsureConnected(now);
    wifiPost(now, jsonPayload);

    // Heartbeat: proves the link is alive without printing on every frame.
    static unsigned long lastWifiStat = 0;
    if (now - lastWifiStat >= 30000) {
      lastWifiStat = now;
      if (WiFi.status() == WL_CONNECTED) {
        Serial.print("WiFi: "); Serial.print(postOk); Serial.print(" posts OK, ");
        Serial.print(postFail); Serial.print(" failed, RSSI ");
        Serial.print(WiFi.RSSI()); Serial.println(" dBm");
      } else {
        Serial.println("WiFi: still disconnected - retrying. USB serial unaffected.");
      }
    }
#endif
  }
}
