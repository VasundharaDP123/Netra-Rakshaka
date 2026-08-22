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

        # USB serial reader. It cannot fight the Wi-Fi receiver: _hardware_loop
        # skips polling entirely while data/wifi_cache.json holds a frame newer
        # than 15 seconds, so whichever transport is live wins on its own.
        if SERIAL_AVAILABLE:
            self.start_hardware_polling()

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
                candidate_ports = []
                for port in ports:
                    desc = port.description.lower()
                    dev = port.device.lower()
                    # Filter for typical serial devices, USB UART bridges, and ESP32 chips
                    if any(x in desc or x in dev for x in ["usb", "serial", "cp210", "ch340", "espressif", "jtag", "com"]):
                        candidate_ports.append(port.device)
                
                connected = False
                for target_port in candidate_ports:
                    try:
                        ser = serial.Serial(target_port, 115200, timeout=1.5)
                        with self.lock:
                            self.serial_port = ser
                            self.use_hardware = True
                        print(f"\n✅ [SERIAL CONNECTED] Spectacles hardware successfully connected on {target_port} at 115200 baud!\n")
                        connected = True
                        break
                    except PermissionError:
                        print(f"\n⚠️  [SERIAL ERROR] Cannot open {target_port}: Access is denied (PermissionError).")
                        print(f"    👉 Another program (like PlatformIO Serial Monitor or Arduino IDE) has locked this port.")
                        print(f"    👉 Please CLOSE the Serial Monitor in your IDE so app.py can read your hardware.\n")
                    except Exception as err:
                        # Silently try next port if connection failed
                        pass

                if not connected:
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
                        parsed_data = None
                        if decoded_line.startswith("DATA:"):
                            try:
                                json_str = decoded_line[5:]
                                parsed_data = json.loads(json_str)
                            except Exception:
                                pass
                        elif "LIVE SENSORS" in decoded_line or "Distance:" in decoded_line:
                            import re
                            m = re.search(r"Distance:\s*(\d+)cm", decoded_line)
                            dist = int(m.group(1)) if m else 0
                            
                            m_rate = re.search(r"Blink Rate:\s*(\d+)", decoded_line)
                            b_rate = int(m_rate.group(1)) if m_rate else 0
                            
                            m_blinks = re.search(r"Blinks:\s*(\d+)", decoded_line)
                            blinks = int(m_blinks.group(1)) if m_blinks else 0
                            
                            m_temp = re.search(r"(?:Env|Eye)\s*Temp:\s*([\d\.]+)", decoded_line)
                            temp = float(m_temp.group(1)) if m_temp else 34.5
                            
                            m_tilt = re.search(r"Head Tilt:\s*(\d+)", decoded_line)
                            tilt = int(m_tilt.group(1)) if m_tilt else 0
                            
                            m_lux = re.search(r"Lux:\s*(\d+)", decoded_line)
                            lux = int(m_lux.group(1)) if m_lux else 200
                            
                            parsed_data = {
                                "screen_distance_cm": dist,
                                "blink_rate": b_rate,
                                "blink_count": blinks,
                                "eye_temp_celsius": temp,
                                "room_temp_celsius": temp,
                                "head_tilt_degrees": tilt,
                                "ambient_lux": lux,
                                "room_humidity_pct": 50,
                                "tof_ok": 1 if dist > 0 else 0,
                                "bmp_ok": 1 if temp > 0 else 0,
                                "bh_ok": 1,
                                "mpu_ok": 1,
                                "tcrt_ok": 1
                            }

                        if parsed_data:
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
    # a sensor offline, the simulated value for that field can be substituted - the
    # working sensors keep their real readings either way.
    #
    # OFF by default: during hardware bring-up a substituted value is worse than a
    # missing one, because it hides the fault. With this disabled you see exactly
    # what the sensor reported (usually 0) and the console marks the channel dead.
    # Set NR_SUBSTITUTE_OFFLINE=1 to restore substitution once the rig is working.
    SUBSTITUTE_OFFLINE = os.getenv("NR_SUBSTITUTE_OFFLINE", "0") == "1"

    HEALTH_FLAGS = {
        "tcrt_ok": ["blink_rate", "blink_count", "blink_duration_ms"],
        "tof_ok":  ["screen_distance_cm"],
        "bmp_ok":  ["eye_temp_celsius", "room_temp_celsius"],
        "bh_ok":   ["ambient_lux"],
        "mpu_ok":  ["head_tilt_degrees"],
    }

    def _patch_offline_fields(self, data, fallback):
        substituted = []
        offline = []
        for flag, fields in self.HEALTH_FLAGS.items():
            if flag in data and not data[flag]:
                for f in fields:
                    if f not in fallback:
                        continue
                    offline.append(f)
                    if self.SUBSTITUTE_OFFLINE:
                        data[f] = fallback[f]
                        substituted.append(f)
        data["simulated_fields"] = substituted
        # Always report which channels the firmware says are dead, whether or not
        # their values were replaced.
        data["offline_fields"] = offline
        return data

    def get_hardware_data(self):
        """The latest real frame off the USB serial link, or None.

        Unlike get_data() this never falls back to the simulation model, so the
        caller can tell "the spectacles said this" apart from "nothing is
        connected" - which is the whole point of the HARDWARE_DISCONNECTED path.
        """
        with self.lock:
            fresh = (hasattr(self, 'last_hardware_time')
                     and (time.time() - self.last_hardware_time) < 15.0
                     and self.latest_hardware_data)
            if not fresh:
                return None
            data = self.latest_hardware_data.copy()

        self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)
        data["continuous_screen_time_min"] = self.continuous_screen_time_min

        # Annotate (and optionally replace) the channels the firmware reports dead.
        if any(f in data for f in self.HEALTH_FLAGS):
            data = self._patch_offline_fields(data, self._simulated_values())
        return data

    def get_data(self):
        # 1. Always return real hardware telemetry if received within last 15 seconds
        with self.lock:
            if hasattr(self, 'last_hardware_time') and (time.time() - self.last_hardware_time < 15.0) and self.latest_hardware_data:
                data = self.latest_hardware_data.copy()
                self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)
                data["continuous_screen_time_min"] = self.continuous_screen_time_min
                data["_source"] = "HARDWARE"
                return data

        # 2. If no hardware connected, return raw zeroed hardware payload (NO FAKE SIMULATED VALUES)
        self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)
        return {
            "blink_rate": 0,
            "blink_count": 0,
            "blink_duration_ms": 0,
            "eye_temp_celsius": 0.0,
            "screen_distance_cm": 0,
            "ambient_lux": 0,
            "room_humidity_pct": 0,
            "head_tilt_degrees": 0,
            "room_temp_celsius": 0.0,
            "continuous_screen_time_min": self.continuous_screen_time_min,
            "drowsy_events": 0,
            "tof_ok": 0,
            "bmp_ok": 0,
            "bh_ok": 0,
            "mpu_ok": 0,
            "tcrt_ok": 0,
            "_source": "WAITING_FOR_HARDWARE"
        }

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
