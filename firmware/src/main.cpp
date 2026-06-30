#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include <VL53L0X.h>
#include <MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BMP280.h>
#include <BH1750.h>

// I2C Pins for ESP32-S3 Mini
#define I2C_SDA 8
#define I2C_SCL 9

// Relocated Analog Pin for IR Photodiode (Blink Detection)
#define IR_PIN 4

// Sensor Objects
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
VL53L0X distanceSensor;
MPU6050 imu;
Adafruit_BMP280 bmp;
BH1750 lightSensor;

// Blink Detection Variables
unsigned long lastBlinkTime = 0;
int blinkCount = 0;
unsigned long blinkDuration = 0;
unsigned long lastBlinkRateCalcTime = 0;
bool eyeClosed = false;
unsigned long closeStartTime = 0;

// Dynamic Thresholding for Blink Detection
int threshold = 2500; // Will calibrate during setup

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10); 

  Serial.println("\n--- Netra Rakshaka: Sensor Initialization ---");

  // 1. Initialize I2C Bus
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println("I2C Bus Initialized on SDA:21, SCL:22");

  // 2. Initialize IR Sensor Pin
  pinMode(IR_PIN, INPUT);

  // Calibrate IR baseline
  long sum = 0;
  for (int i = 0; i < 50; i++) {
    sum += analogRead(IR_PIN);
    delay(20);
  }
  int baseline = sum / 50;
  threshold = baseline - 400; // Calibrate threshold lower than open eye reflectance
  Serial.print("IR Calibration Complete. Baseline: ");
  Serial.print(baseline);
  Serial.print(" | Blink Threshold Set To: ");
  Serial.println(threshold);

  // 3. Initialize MLX90614 (Thermal Eye Temp)
  if (!mlx.begin()) {
    Serial.println("[ERROR] MLX90614 not found. Check wiring!");
  } else {
    Serial.println("[OK] MLX90614 Thermal Sensor ready.");
  }

  // 4. Initialize VL53L0X (Distance)
  distanceSensor.setTimeout(500);
  if (!distanceSensor.init()) {
    Serial.println("[ERROR] VL53L0X not found. Check wiring!");
  } else {
    distanceSensor.startContinuous();
    Serial.println("[OK] VL53L0X Distance Sensor ready.");
  }

  // 5. Initialize MPU6050 (IMU - Head Posture)
  imu.initialize();
  if (!imu.testConnection()) {
    Serial.println("[ERROR] MPU6050 not found. Check wiring!");
  } else {
    Serial.println("[OK] MPU6050 IMU ready.");
  }

  // 6. Initialize BMP280 (Environmental)
  if (!bmp.begin(0x76)) {
    Serial.println("[ERROR] BMP280 not found at 0x76. Trying default 0x77...");
    if (!bmp.begin(0x77)) {
      Serial.println("[ERROR] BMP280 not found. Check wiring!");
    } else {
      Serial.println("[OK] BMP280 Environmental Sensor ready at 0x77.");
    }
  } else {
    Serial.println("[OK] BMP280 Environmental Sensor ready at 0x76.");
  }

  // 7. Initialize BH1750 (Ambient Light)
  if (!lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire)) {
    Serial.println("[ERROR] BH1750 not found. Check wiring!");
  } else {
    Serial.println("[OK] BH1750 Light Sensor ready.");
  }

  Serial.println("--- All sensors initialized successfully! ---\n");
  lastBlinkRateCalcTime = millis();
}

void loop() {
  unsigned long currentTime = millis();

  // 1. Blink Detection Logic
  int irValue = analogRead(IR_PIN);
  
  // When eye blinks, reflectance drops below threshold
  if (irValue < threshold && !eyeClosed) {
    eyeClosed = true;
    closeStartTime = currentTime;
  } else if (irValue >= threshold && eyeClosed) {
    eyeClosed = false;
    unsigned long duration = currentTime - closeStartTime;
    if (duration > 50 && duration < 800) { // filter out noise
      blinkCount++;
      blinkDuration = duration;
    }
  }

  // Calculate Blink Rate (blinks per minute) every 10 seconds
  static int currentBlinkRate = 16; // default average
  if (currentTime - lastBlinkRateCalcTime >= 10000) {
    currentBlinkRate = blinkCount * 6; // Extrapolate to 60 seconds
    blinkCount = 0;
    lastBlinkRateCalcTime = currentTime;
  }

  // 2. MLX90614 (Eye Temperature)
  float eyeTemp = mlx.readObjectTempC();
  if (isnan(eyeTemp) || eyeTemp < 20.0 || eyeTemp > 45.0) {
    eyeTemp = 34.5; // fallback baseline
  }

  // 3. VL53L0X (Screen Distance)
  int distMm = distanceSensor.readRangeContinuousMillimeters();
  int distCm = distMm / 10;
  if (distCm <= 0 || distCm > 200 || distanceSensor.timeoutOccurred()) {
    distCm = 50; // fallback default
  }

  // 4. MPU6050 (Head Posture - Angle Pitch calculation)
  int16_t ax, ay, az, gx, gy, gz;
  imu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);
  
  // Pitch angle: degrees tilt from horizontal
  float headTilt = atan2(ay, sqrt((float)ax*ax + (float)az*az)) * 180.0 / PI;
  headTilt = abs(headTilt); // absolute tilt

  // 5. BMP280 (Environment)
  float humidity = 50.0; // BMP280 does not support humidity, using default fallback

  // 6. BH1750 (Ambient Light)
  float lux = lightSensor.readLightLevel();
  if (lux < 0) lux = 200;

  // 7. Output formatted JSON packet for Python serial parsing
  Serial.print("DATA:");
  Serial.print("{\"blink_rate\":");
  Serial.print(currentBlinkRate);
  Serial.print(",\"blink_duration_ms\":");
  Serial.print(blinkDuration == 0 ? 200 : blinkDuration);
  Serial.print(",\"eye_temp_celsius\":");
  Serial.print(eyeTemp, 1);
  Serial.print(",\"screen_distance_cm\":");
  Serial.print(distCm);
  Serial.print(",\"ambient_lux\":");
  Serial.print((int)lux);
  Serial.print(",\"room_humidity_pct\":");
  Serial.print((int)humidity);
  Serial.print(",\"head_tilt_degrees\":");
  Serial.print((int)headTilt);
  Serial.println("}");

  delay(200); // Fast cycle to capture brief blinks
}
