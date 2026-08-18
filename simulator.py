import os
import random
import time
import threading
import json

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

class Simulator:
    def __init__(self):
        self.mode = "Normal" # "Normal", "Degrading", "Critical"
        self.degrade_step = 0
        self.start_time = time.time()
        self.continuous_screen_time_min = 0
        self.total_blinks = 0
        self.last_blink_time = time.time()

        # Real hardware states
        self.use_hardware = False
        self.serial_port = None
        self.latest_hardware_data = {}
        self.hardware_thread = None
        self.lock = threading.Lock()

        # DISABLED to prevent conflict with Wi-Fi receiver
        # if SERIAL_AVAILABLE:
        #     self.start_hardware_polling()

    def start_hardware_polling(self):
        self.hardware_thread = threading.Thread(target=self._hardware_loop, daemon=True)
        self.hardware_thread.start()

    def _hardware_loop(self):
        while True:
            # Skip USB polling if Wi-Fi telemetry is active
            cache_file = os.path.join("data", "wifi_cache.json")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r") as f:
                        cache_data = json.load(f)
                    if time.time() - cache_data.get("_received_at", 0) < 15.0:
                        time.sleep(2)
                        continue
                except Exception:
                    pass

            # Check if we need to establish a connection
            has_port = False
            with self.lock:
                has_port = (self.serial_port is not None and self.serial_port.is_open)

            if not has_port:
                # Scan for USB Serial ports
                ports = list(serial.tools.list_ports.comports())
                target_port = None
                for port in ports:
                    desc = port.description.lower()
                    device = port.device
                    # Common USB-Serial chip drivers or board descriptions
                    if any(x in desc for x in ["usb", "serial", "cp210", "ch340", "espressif", "jtag"]):
                        target_port = device
                        break
                
                if target_port:
                    try:
                        ser = serial.Serial(target_port, 115200, timeout=1.5)
                        with self.lock:
                            self.serial_port = ser
                            self.use_hardware = True
                    except Exception:
                        with self.lock:
                            self.use_hardware = False
                        time.sleep(5)
                else:
                    with self.lock:
                        self.use_hardware = False
                    time.sleep(4)
            else:
                # Read line from the serial port
                try:
                    line = b""
                    with self.lock:
                        if self.serial_port and self.serial_port.is_open:
                            line = self.serial_port.readline()
                    
                    if line:
                        decoded_line = line.decode('utf-8', errors='ignore').strip()
                        if decoded_line.startswith("DATA:"):
                            json_str = decoded_line[5:]
                            parsed_data = json.loads(json_str)
                            with self.lock:
                                self.latest_hardware_data = parsed_data
                                self.last_hardware_time = time.time()
                                self.use_hardware = True
                except Exception:
                    pass
            time.sleep(0.05)

    def set_mode(self, mode):
        self.mode = mode
        self.degrade_step = 0
        if mode == "Normal":
            self.start_time = time.time()

    # A sensor that is not responding sends 0, which is a plausible-looking reading
    # and poisons both the dashboard and the classifier. Where the firmware reports
    # a sensor offline, substitute the simulated value for that field only - the
    # working sensors keep their real readings.
    HEALTH_FLAGS = {
        "tof_ok":  ["screen_distance_cm"],
        "bmp_ok":  ["eye_temp_celsius", "room_temp_celsius"],
        "bh_ok":   ["ambient_lux"],
        "mpu_ok":  ["head_tilt_degrees"],
    }

    def _patch_offline_fields(self, data, fallback):
        substituted = []
        for flag, fields in self.HEALTH_FLAGS.items():
            if flag in data and not data[flag]:
                for f in fields:
                    if f in fallback:
                        data[f] = fallback[f]
                        substituted.append(f)
        data["simulated_fields"] = substituted
        return data

    def get_data(self):
        # 1. Always prefer real hardware telemetry if received within last 15 seconds
        with self.lock:
            if hasattr(self, 'last_hardware_time') and (time.time() - self.last_hardware_time < 15.0) and self.latest_hardware_data:
                data = self.latest_hardware_data.copy()
                self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)
                data["continuous_screen_time_min"] = self.continuous_screen_time_min
                # Fill in only the fields whose sensor is offline.
                if any(f in data for f in self.HEALTH_FLAGS):
                    data = self._patch_offline_fields(data, self._simulated_values())
                return data

        # 2. Fall back to the simulation model if hardware is not connected
        return self._simulated_values()

    def _simulated_values(self):
        # Calculate continuous time
        self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)

        if self.mode == "Normal":
            return {
                "blink_rate": 17,
                "blink_count": 85,
                "blink_duration_ms": 180,
                "eye_temp_celsius": 34.8,
                "screen_distance_cm": 46,
                "ambient_lux": 300,
                "room_humidity_pct": 50,
                "head_tilt_degrees": 5,
                "room_temp_celsius": 25.0,
                "continuous_screen_time_min": max(12, self.continuous_screen_time_min)
            }
        elif self.mode == "Degrading":
            self.degrade_step += 1
            return {
                "blink_rate": 9,
                "blink_count": 120,
                "blink_duration_ms": 280,
                "eye_temp_celsius": 36.1,
                "screen_distance_cm": 24,
                "ambient_lux": 110,
                "room_humidity_pct": 40,
                "head_tilt_degrees": 24,
                "room_temp_celsius": 26.5,
                "continuous_screen_time_min": max(45, self.continuous_screen_time_min)
            }
        elif self.mode == "Critical":
            return {
                "blink_rate": 4,
                "blink_count": 140,
                "blink_duration_ms": 450,
                "eye_temp_celsius": 37.4,
                "screen_distance_cm": 14,
                "ambient_lux": 70,
                "room_humidity_pct": 25,
                "head_tilt_degrees": 38,
                "room_temp_celsius": 28.0,
                "continuous_screen_time_min": max(75, self.continuous_screen_time_min)
            }
        else:
            return {
                "blink_rate": 17,
                "blink_count": 50,
                "blink_duration_ms": 180,
                "eye_temp_celsius": 34.8,
                "screen_distance_cm": 45,
                "ambient_lux": 300,
                "room_humidity_pct": 50,
                "head_tilt_degrees": 5,
                "room_temp_celsius": 25.0,
                "continuous_screen_time_min": self.continuous_screen_time_min
            }
            data["continuous_screen_time_min"] = 50

        # Clamp bounds
        data["screen_distance_cm"] = max(0, data["screen_distance_cm"])

        return data

simulator_instance = Simulator()
