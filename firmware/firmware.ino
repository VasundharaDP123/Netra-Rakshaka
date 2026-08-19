#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <VL53L0X.h>
#include <Adafruit_BMP280.h>
#include <BH1750.h>
#include <MPU6050.h> 

// Credentials live in secrets.h, which is gitignored so they are never committed.
// Copy firmware/src/secrets.example.h to firmware/src/secrets.h and fill it in.
// Without that file the placeholders below are used, so a fresh clone still builds.
#if __has_include("secrets.h")
  #include "secrets.h"
#endif
#ifndef WIFI_SSID
  #define WIFI_SSID   "YOUR_WIFI_NAME"
#endif
#ifndef WIFI_PASS
  #define WIFI_PASS   "YOUR_WIFI_PASSWORD"
#endif
#ifndef SERVER_HOST
  #define SERVER_HOST "192.168.1.50"
#endif
#ifndef SERVER_PORT
  #define SERVER_PORT 5000
#endif

#define STR2(x) #x
#define STR(x) STR2(x)
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASS;
const char* serverName = "http://" SERVER_HOST ":" STR(SERVER_PORT) "/sensor_data";

#define I2C_SDA 8
#define I2C_SCL 9
#define IR_PIN 4 

VL53L0X distanceSensor;
Adafruit_BMP280 bmp;
BH1750 lightSensor;
MPU6050 imu(0x68); 

bool tofOK = false, bmpOK = false, bhOK = false, mpuOK = false;

// --- SENSOR VARIABLES ---
long baselineIR = 0;
bool eyeClosed = false;
unsigned long closeStartTime = 0;

int cumulativeBlinks = 0;
int totalBlinks = 0; 
int incompleteBlinks = 0;
int currentIncompletePct = 0;

unsigned long lastBPMCalcTime = 0;
int lastMinuteBlinks = 16; 
bool minuteCompleted = false;

#define BLINK_HIST_SIZE 64
unsigned long blinkHistory[BLINK_HIST_SIZE];
uint8_t blinkHistHead = 0;
unsigned long bootStartTime = 0;

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

float baselineTemp = 0.0;
float rollingAvgTemp = 0.0;
int tempReadings = 0;

unsigned long lastPostTime = 0;

void initSensors() {
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000); // 100kHz I2C clock for stability

  if (!tofOK) {
    distanceSensor.setTimeout(500);
    tofOK = distanceSensor.init();
    if (tofOK) distanceSensor.startContinuous();
  }

  if (!bmpOK) {
    bmpOK = bmp.begin(0x76);
    if (!bmpOK) bmpOK = bmp.begin(0x77);
  }

  if (!bhOK) {
    bhOK = lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  }

  if (!mpuOK) {
    imu.initialize();
    mpuOK = imu.testConnection();
  }
}

void recoverI2CBus() {
  Serial.println("⚠️ I2C Bus Recovery Triggered...");
  Wire.end();
  delay(50);
  tofOK = false; bmpOK = false; bhOK = false; mpuOK = false;
  initSensors();
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(IR_PIN, INPUT);
  analogReadResolution(12);

  for (int i = 0; i < BLINK_HIST_SIZE; i++) blinkHistory[i] = 0;

  initSensors();

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
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " Connected!" : " Offline Mode (USB)");
  
  bootStartTime = millis();
  lastBPMCalcTime = millis();
  lastPostTime = millis();
}

void loop() {
  unsigned long currentTime = millis();

  // ----------------------------------------------------
  // 1. HIGH-SENSITIVITY ADAPTIVE BLINK DETECTION 
  // ----------------------------------------------------
  int currentIR = analogRead(IR_PIN);
  
  // Freeze baseline while closed so closure signal doesn't distort open-eye reference
  if (!eyeClosed) {
    baselineIR = (baselineIR * 19 + currentIR) / 20;
  }
  
  int delta = abs(currentIR - baselineIR);
  
  // Sensitive detection threshold (delta > 35) or digital DO pin trigger
  bool candidateClosed = (delta > 35) || (digitalRead(IR_PIN) == HIGH);

  if (candidateClosed && !eyeClosed) {
    eyeClosed = true;
    closeStartTime = currentTime;
  } else if (!candidateClosed && eyeClosed) {
    eyeClosed = false;
    unsigned long duration = currentTime - closeStartTime;
    // Human eyelid blink duration is 30ms - 900ms
    if (duration >= 30 && duration <= 900) { 
      cumulativeBlinks++;
      totalBlinks++;
      blinkHistory[blinkHistHead] = currentTime;
      blinkHistHead = (blinkHistHead + 1) % BLINK_HIST_SIZE;
      if (duration < 120) incompleteBlinks++;
    }
  }

  if (totalBlinks > 0) currentIncompletePct = (incompleteBlinks * 100) / totalBlinks;

  if (currentTime - lastBPMCalcTime >= 60000) {
    lastMinuteBlinks = totalBlinks; 
    minuteCompleted = true;
    totalBlinks = 0;
    incompleteBlinks = 0;
    lastBPMCalcTime = currentTime;
  }

  // ----------------------------------------------------
  // 2. READ REAL HARDWARE SENSORS (Every 500ms)
  // ----------------------------------------------------
  if (currentTime - lastPostTime >= 500) {
    lastPostTime = currentTime;

    // Retry uninitialized sensors automatically
    if (!tofOK || !bmpOK || !bhOK || !mpuOK) {
      initSensors();
    }

    int distCm = 0; 
    if (tofOK) {
      int rawDist = distanceSensor.readRangeContinuousMillimeters();
      if (!distanceSensor.timeoutOccurred() && rawDist > 0 && rawDist < 8000) {
        distCm = rawDist / 10;
      }
    }

    float eyeTemp = 0.0;
    if (bmpOK) {
      float temp = bmp.readTemperature();
      if (!isnan(temp) && temp > 10.0 && temp < 80.0) eyeTemp = temp;
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
      if (ax != 0 || az != 0) {
        headTilt = abs(atan2(ay, sqrt((float)ax*ax + (float)az*az)) * 180.0 / PI);
      }
    }
    
    float humidity = 50.0; 

    // ----------------------------------------------------
    // 3. SENSOR FUSION STRAIN CLASSIFICATION
    // ----------------------------------------------------
    int criticalCount = 0;
    int warningCount = 0;

    if (minuteCompleted) {
      if (lastMinuteBlinks < 8) criticalCount++;
      else if (lastMinuteBlinks >= 8 && lastMinuteBlinks <= 11) warningCount++;
      
      if (currentIncompletePct > 40) criticalCount++;
      else if (currentIncompletePct >= 21 && currentIncompletePct <= 40) warningCount++;
    }

    if (distCm > 0 && distCm < 35) criticalCount++; 
    else if (distCm >= 35 && distCm <= 49) warningCount++;

    if (headTilt > 30) criticalCount++; 
    else if (headTilt >= 16 && headTilt <= 30) warningCount++;

    if (lux > 0 && lux < 80) warningCount++;

    if (currentTime < 10000) { 
      baselineTemp = (baselineTemp * tempReadings + eyeTemp) / (tempReadings + 1);
      tempReadings++;
      rollingAvgTemp = baselineTemp;
    } else if (eyeTemp > 0.0) {
      rollingAvgTemp = (rollingAvgTemp * 29 + eyeTemp) / 30; 
    }

    String edgeStrain = "Safe";
    if (criticalCount >= 1 || warningCount >= 2) edgeStrain = "Critical"; 
    else if (warningCount == 1) edgeStrain = "Moderate"; 

    // ----------------------------------------------------
    // 4. PRINT TELEMETRY & POST TO SERVER
    // ----------------------------------------------------
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
    jsonPayload += "\"mpu_ok\":" + String(mpuOK ? 1 : 0);
    jsonPayload += "}";

    // Send DATA: line over USB Serial so simulator.py parses JSON instantly
    Serial.print("DATA:");
    Serial.println(jsonPayload);

    // Human readable print for Serial Monitor
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
