#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <VL53L0X.h>
#include <Adafruit_BMP280.h>
#include <BH1750.h>
#include <MPU6050.h> 

const char* ssid = "OnePlus NOrd CE4 Lite 5G";
const char* password = "vasu1327@_";
const char* serverName = "http://10.51.159.133:5000/sensor_data"; 

// Default I2C Pins (ESP32-S3)
int I2C_SDA = 8;
int I2C_SCL = 9;
#define IR_PIN 4 

VL53L0X distanceSensor;
Adafruit_BMP280 bmp;
BH1750 lightSensor;
MPU6050 imu(0x68); 

bool tofOK = false, bmpOK = false, bhOK = false, mpuOK = false, tcrtOK = true;

// --- SENSOR VARIABLES ---
long baselineIR = 0;
bool eyeClosed = false;
unsigned long closeStartTime = 0;

int cumulativeBlinks = 0;
int totalBlinks = 0; 
int incompleteBlinks = 0;
int currentIncompletePct = 0;

#define BLINK_HIST_SIZE 64
unsigned long blinkHistory[BLINK_HIST_SIZE];
uint8_t blinkHistHead = 0;
unsigned long bootStartTime = 0;

unsigned long lastBPMCalcTime = 0;
unsigned long lastPostTime = 0;
unsigned long lastInitRetryTime = 0;

int lastValidDistCm = 40;

int getRollingBPM(unsigned long now) {
  unsigned long elapsed = now - bootStartTime;
  if (elapsed < 3000) return 16;
  int count = 0;
  unsigned long window = (elapsed < 60000) ? elapsed : 60000;
  for (int i = 0; i < BLINK_HIST_SIZE; i++) {
    if (blinkHistory[i] > 0 && (now - blinkHistory[i]) <= window) {
      count++;
    }
  }
  return (int)((count * 60000.0) / (float)window + 0.5);
}

int scanI2CBus() {
  int found = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      found++;
    }
  }
  return found;
}

bool autoDetectI2CPins() {
  // Candidate I2C pin pairs (excluding Pin 4 which is reserved for IR_PIN)
  static const int CANDIDATES[][2] = {
    {8, 9}, {9, 8}, {21, 22}, {22, 21}, {1, 2}, {2, 1}, {17, 18}, {18, 17}, {47, 21}
  };
  const int n = sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);

  for (int i = 0; i < n; i++) {
    int sda = CANDIDATES[i][0], scl = CANDIDATES[i][1];
    Wire.end();
    Wire.begin(sda, scl);
    Wire.setClock(100000);
    delay(10);
    if (scanI2CBus() > 0) {
      I2C_SDA = sda;
      I2C_SCL = scl;
      Serial.print("✅ I2C devices detected on SDA=");
      Serial.print(sda);
      Serial.print(" SCL=");
      Serial.println(scl);
      return true;
    }
  }
  Wire.end();
  Wire.begin(8, 9);
  Wire.setClock(100000);
  return false;
}

void initSensors() {
  if (!tofOK) {
    distanceSensor.setTimeout(500);
    tofOK = distanceSensor.init();
    if (tofOK) distanceSensor.startContinuous();
  }

  if (!bmpOK) {
    bmpOK = bmp.begin(0x76);
    if (!bmpOK) bmpOK = bmp.begin(0x77);
    if (bmpOK) {
      bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                      Adafruit_BMP280::SAMPLING_X2,
                      Adafruit_BMP280::SAMPLING_X16,
                      Adafruit_BMP280::FILTER_X16,
                      Adafruit_BMP280::STANDBY_MS_500);
    }
  }

  if (!bhOK) {
    bhOK = lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  }

  if (!mpuOK) {
    imu.initialize();
    mpuOK = imu.testConnection();
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("Initializing Netra Rakshaka Sensors...");
  autoDetectI2CPins();
  
  pinMode(IR_PIN, INPUT);
  analogReadResolution(12);

  for (int i = 0; i < BLINK_HIST_SIZE; i++) blinkHistory[i] = 0;

  initSensors();

  Serial.print("Sensor Health -> TCRT5000: "); Serial.print(tcrtOK);
  Serial.print(" | ToF: "); Serial.print(tofOK);
  Serial.print(" | BMP280: "); Serial.print(bmpOK);
  Serial.print(" | BH1750: "); Serial.print(bhOK);
  Serial.print(" | MPU6050: "); Serial.println(mpuOK);

  // Calibrate IR Baseline
  long tempIR = 0;
  for (int i = 0; i < 50; i++) {
    tempIR += analogRead(IR_PIN);
    delay(20);
  }
  baselineIR = tempIR / 50;

  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 15) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " Connected!" : " Offline Mode (USB)");

  bootStartTime = millis();
  lastBPMCalcTime = millis();
  lastPostTime = millis();
  lastInitRetryTime = millis();
}

void loop() {
  unsigned long currentTime = millis();

  // 1. HIGH-SENSITIVITY BLINK DETECTION
  int currentIR = analogRead(IR_PIN);
  if (!eyeClosed) {
    baselineIR = (baselineIR * 31 + currentIR) / 32;
  }

  int delta = abs(currentIR - baselineIR);
  // Pure analog threshold detection (no digitalRead interference on pin 4)
  bool candidateClosed = (delta > 45);

  if (candidateClosed && !eyeClosed) {
    eyeClosed = true;
    closeStartTime = currentTime;
  } else if (!candidateClosed && eyeClosed) {
    eyeClosed = false;
    unsigned long duration = currentTime - closeStartTime;
    if (duration >= 40 && duration <= 900) {
      cumulativeBlinks++;
      totalBlinks++;
      blinkHistory[blinkHistHead] = currentTime;
      blinkHistHead = (blinkHistHead + 1) % BLINK_HIST_SIZE;
      if (duration < 120) incompleteBlinks++;
    }
  }

  if (totalBlinks > 0) currentIncompletePct = (incompleteBlinks * 100) / totalBlinks;

  if (currentTime - lastBPMCalcTime >= 60000) {
    totalBlinks = 0;
    incompleteBlinks = 0;
    lastBPMCalcTime = currentTime;
  }

  // 2. READ REAL HARDWARE SENSORS (Every 500ms)
  if (currentTime - lastPostTime >= 500) {
    lastPostTime = currentTime;

    // Retry offline sensors sparingly (every 10 seconds) to avoid bus lockup
    if ((!tofOK || !bmpOK || !bhOK || !mpuOK) && (currentTime - lastInitRetryTime >= 10000)) {
      lastInitRetryTime = currentTime;
      initSensors();
    }

    int distCm = 0;
    if (tofOK) {
      int rawDist = distanceSensor.readRangeContinuousMillimeters();
      if (distanceSensor.timeoutOccurred() || rawDist <= 0 || rawDist >= 8000) {
        distanceSensor.setTimeout(500);
        rawDist = distanceSensor.readRangeSingleMillimeters();
      }
      if (rawDist > 20 && rawDist < 2000) {
        distCm = rawDist / 10;
        lastValidDistCm = distCm;
      } else if (lastValidDistCm > 0) {
        distCm = lastValidDistCm;
      }
    }

    float eyeTemp = 34.5;
    if (bmpOK) {
      float temp = bmp.readTemperature();
      if (!isnan(temp) && temp > 10.0 && temp < 60.0) {
        eyeTemp = temp;
      }
    }

    int lux = 0;
    if (bhOK) {
      float l = lightSensor.readLightLevel();
      if (l >= 0) lux = (int)l;
    }

    int headTilt = 0;
    int16_t ax=0, ay=0, az=0, gx=0, gy=0, gz=0;
    if (mpuOK) {
      imu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);
      if (ax != 0 || az != 0 || ay != 0) {
        headTilt = abs((int)(atan2((float)ay, sqrt((float)ax*ax + (float)az*az)) * 180.0 / PI));
      }
    }

    float humidity = 50.0;

    // 3. CLASSIFICATION & TELEMETRY OUTPUT
    int criticalCount = 0;
    int warningCount = 0;

    if (distCm > 0 && distCm < 35) criticalCount++;
    else if (distCm >= 35 && distCm <= 49) warningCount++;

    if (headTilt > 30) criticalCount++;
    else if (headTilt >= 16 && headTilt <= 30) warningCount++;

    if (lux > 0 && lux < 80) warningCount++;

    String edgeStrain = "Safe";
    if (criticalCount >= 1 || warningCount >= 2) edgeStrain = "Critical";
    else if (warningCount == 1) edgeStrain = "Moderate";

    String statusIcon = (edgeStrain == "Critical") ? "🔴" : (edgeStrain == "Moderate" ? "🟡" : "🟢");
    int strainScore = (edgeStrain == "Critical") ? 95 : (edgeStrain == "Moderate" ? 50 : 15);

    int currentBPM = getRollingBPM(currentTime);

    String jsonPayload = "{";
    jsonPayload += "\"blink_rate\":" + String(currentBPM) + ",";
    jsonPayload += "\"blink_count\":" + String(cumulativeBlinks) + ",";
    jsonPayload += "\"incomplete_blink_pct\":" + String(currentIncompletePct) + ",";
    jsonPayload += "\"eye_temp_celsius\":" + String(eyeTemp, 1) + ",";
    jsonPayload += "\"screen_distance_cm\":" + String(distCm) + ",";
    jsonPayload += "\"ambient_lux\":" + String(lux) + ",";
    jsonPayload += "\"room_humidity_pct\":" + String(humidity, 1) + ",";
    jsonPayload += "\"head_tilt_degrees\":" + String(headTilt) + ",";
    jsonPayload += "\"edge_ai_strain\":\"" + edgeStrain + "\",";
    jsonPayload += "\"tof_ok\":" + String(tofOK ? 1 : 0) + ",";
    jsonPayload += "\"bmp_ok\":" + String(bmpOK ? 1 : 0) + ",";
    jsonPayload += "\"bh_ok\":" + String(bhOK ? 1 : 0) + ",";
    jsonPayload += "\"mpu_ok\":" + String(mpuOK ? 1 : 0) + ",";
    jsonPayload += "\"tcrt_ok\":" + String(tcrtOK ? 1 : 0);
    jsonPayload += "}";

    // USB Serial Output
    Serial.print("DATA:");
    Serial.println(jsonPayload);

    // Terminal formatted output
    Serial.printf("📡 [LIVE SENSORS] %s Strain: %-8s (%2d/100) | Distance: %2dcm | Blink Rate: %2d/min | Blinks: %3d | Env Temp: %.1f°C | Head Tilt: %2d° | Lux: %5d\n",
                   statusIcon.c_str(), edgeStrain.c_str(), strainScore, distCm, currentBPM, cumulativeBlinks, eyeTemp, headTilt, lux);

    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      http.begin(serverName);
      http.addHeader("Content-Type", "application/json");
      http.POST(jsonPayload);
      http.end();
    }
  }

  delay(20);
}
