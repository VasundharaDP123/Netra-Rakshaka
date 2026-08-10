#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include <VL53L0X.h>
#include <MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME680.h>
#include <BH1750.h>

// I2C Pins for ESP32-S3 Mini
#define I2C_SDA 21
#define I2C_SCL 22

// Analog Pin for IR Photodiode (Blink Detection)
#define IR_PIN 34

// Sensor Objects
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
VL53L0X distanceSensor;
MPU6050 imu;
Adafruit_BME680 bme;
BH1750 lightSensor;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10); // Wait for serial port to connect

  Serial.println("\n--- Netra Rakshaka: Sensor Initialization ---");

  // 1. Initialize I2C Bus
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println("I2C Bus Initialized on SDA:21, SCL:22");

  // 2. Initialize IR Sensor Pin
  pinMode(IR_PIN, INPUT);

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

  // 6. Initialize BME680 (Environmental)
  if (!bme.begin()) {
    Serial.println("[ERROR] BME680 not found. Check wiring!");
  } else {
    // Set up oversampling and filter initialization
    bme.setTemperatureOversampling(BME680_OS_8X);
    bme.setHumidityOversampling(BME680_OS_2X);
    bme.setPressureOversampling(BME680_OS_4X);
    bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
    bme.setGasHeater(320, 150); // 320*C for 150 ms
    Serial.println("[OK] BME680 Environmental Sensor ready.");
  }

  // 7. Initialize BH1750 (Ambient Light)
  if (!lightSensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire)) {
    Serial.println("[ERROR] BH1750 not found. Check wiring!");
  } else {
    Serial.println("[OK] BH1750 Light Sensor ready.");
  }

  Serial.println("--- All sensors initialized successfully! ---\n");
}

void loop() {
  Serial.println("============================================");
  
  // 1. IR Photodiode (Blinks)
  int irValue = analogRead(IR_PIN);
  Serial.print("IR Photodiode (Blink Proxy): ");
  Serial.println(irValue);

  // 2. MLX90614 (Eye Temperature)
  Serial.print("Eye Surface Temp (*C):       ");
  Serial.println(mlx.readObjectTempC());
  
  // 3. VL53L0X (Screen Distance)
  Serial.print("Screen Distance (mm):        ");
  Serial.println(distanceSensor.readRangeContinuousMillimeters());
  if (distanceSensor.timeoutOccurred()) {
    Serial.println(" -> [WARNING] VL53L0X timeout!");
  }

  // 4. MPU6050 (Head Posture)
  int16_t ax, ay, az, gx, gy, gz;
  imu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);
  Serial.print("Head Pitch (Y-axis accel):   ");
  Serial.println(ay);

  // 5. BME680 (Environment)
  if (bme.performReading()) {
    Serial.print("Room Temperature (*C):       ");
    Serial.println(bme.temperature);
    Serial.print("Room Humidity (%):           ");
    Serial.println(bme.humidity);
  } else {
    Serial.println(" -> [WARNING] BME680 failed to perform reading.");
  }

  // 6. BH1750 (Ambient Light)
  float lux = lightSensor.readLightLevel();
  Serial.print("Ambient Light (Lux):         ");
  Serial.println(lux);

  Serial.println("============================================\n");

  // Read loop delay (simulate 500ms active + 400ms sleep for now)
  delay(900); 
}
