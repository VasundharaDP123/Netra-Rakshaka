# Netra Rakshaka
**Intelligent Wearable System for Real-Time Vision Risk Awareness and Eye Protection**

## Complete Build Overview

### Stage 1 — Understanding What You Are Building
The project is a smart spectacle system that monitors eye health continuously. It works in three layers working together:
1. **The hardware layer** collects raw data from the eyes and environment through sensors mounted on the spectacle frame
2. **The edge AI layer** processes that data in real time on the ESP32 chip itself
3. **The cloud AI layer** analyzes patterns over days and weeks to predict long-term vision risks

These three layers together form a complete closed-loop system where the glasses detect strain, the AI classifies it, and the system actively intervenes by dimming your laptop screen or sending alerts through a mobile app.

### Stage 2 — Setting Up the Hardware
- **ESP32-S3 Mini** is the core controller. It receives data from all sensors via I2C (SDA and SCL), processes data using a TinyML model, and transmits results via Bluetooth Low Energy (BLE).
- **IR LED and Photodiode pair** (left frame inner corner) tracks blink rate and complete/incomplete blinks.
- **MLX90614 Thermal Sensor** (inner upper frame) measures eye surface temperature to detect tear film evaporation.
- **VL53L0X ToF Distance Sensor** (nose bridge) measures screen distance.
- **MPU6050 IMU Sensor** (right temple arm) measures head tilt for Text Neck and micro-nods for drowsiness.
- **BME680 Environmental Sensor** (outer frame) measures room temperature, humidity, and CO2.
- **BH1750 Light Sensor** (outer frame) measures ambient room brightness.
- **Coin Vibration Motor** (left temple arm) provides haptic alerts.
- **3.7V LiPo Battery and TP4056 Charger** (split across temple arms) for power and charging.

### Stage 3 — Writing the ESP32 Firmware
Every 500 milliseconds:
- Reads IR for blinks.
- Reads MLX90614 for eye temperature.
- Reads VL53L0X for screen distance.
- Reads MPU6050 for head tilt.
- Reads BME680 for humidity/CO2.
- Reads BH1750 for brightness.

Data is packed, timestamped, sent to local TinyML, and transmitted via BLE. Deep sleep for 400ms between readings to extend battery to 8-10 hours.

### Stage 4 — Calibration
Initial one-minute calibration to record natural blink rate, viewing distance, and room lighting. Stored in flash as personal baseline.

### Stage 5 — Training the TinyML Edge Model
1D CNN model trained via Edge Impulse on labeled sensor data (Safe, Moderate Strain, Critical Strain) and exported as TensorFlow Lite for Microcontrollers.

### Stage 6 — Training the PyTorch LSTM Backend Model
LSTM time-series model (30-min window) to predict strain 20-30 mins into the future. Deployed on FastAPI.

### Stage 7 — Active Screen Intervention Script
Python background script connecting via BLE. Dims screen by 30% for 20 seconds upon Critical Strain signal, then restores.

### Stage 8 — Flutter Mobile App
Dashboard for real-time monitoring (blink rate, distance, temperature, environment, Eye Strain Score), fatigue graph, and push notifications.

### Stage 9 & 10 — Integration, Testing & Protective Coating
End-to-end testing, followed by conformal coating and hydrophobic nano-coating for IPX4 water resistance.

---

## All Features of Netra Rakshaka

### 🔵 Core Monitoring Features
1. Real-Time Blink Rate Monitoring
2. Incomplete Blink Detection
3. Thermal Tear Film Detection
4. Screen Distance Monitoring
5. Ambient Light Monitoring
6. Environmental Dry Eye Detection
7. Head Posture and Text Neck Detection
8. Drowsiness and Micro-Nod Detection

### 🟡 AI and Intelligence Features
9. Real-Time Edge AI Strain Classification (TinyML)
10. Long-Term Vision Risk Forecasting (PyTorch LSTM)
11. Personalized Baseline Calibration
12. Cognitive Load and Deep Focus Mapping

### 🟢 Intervention and Alert Features
13. Graduated Three-Level Alert System
14. Active Cyber-Physical Screen Intervention
15. Smart Environment Control via Webhooks
16. Deep Work Mode

### 🔴 Durability and Design Features
17. Camera-Free Privacy-Safe Design
18. All-Day Battery Life via Deep Sleep Duty Cycling
19. Conformal Coating Water Resistance
20. Cross-Platform OS Compatibility

---

## Patent Claims
1. **Thermal Ocular Surface Temperature Monitoring for Dry Eye Detection on a Consumer Wearable**
2. **Closed-Loop Cyber-Physical Biometric-to-Display Actuation Method**
3. **Multi-Modal Environmental and Physiological Sensor Fusion for Context-Aware Dry Eye Risk Prediction**

*"Netra Rakshaka is a camera-free, multi-modal IoT spectacle system that detects digital eye strain physiologically using thermal ocular temperature monitoring and sensor fusion, predicts long-term vision risk using Edge AI and LSTM forecasting, and actively enforces eye care protocols through a closed-loop biometric-to-display cyber-physical intervention mechanism."*
