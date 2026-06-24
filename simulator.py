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

        # Real hardware states
        self.use_hardware = False
        self.serial_port = None
        self.latest_hardware_data = {}
        self.hardware_thread = None
        self.lock = threading.Lock()

        if SERIAL_AVAILABLE:
            self.start_hardware_polling()

    def start_hardware_polling(self):
        self.hardware_thread = threading.Thread(target=self._hardware_loop, daemon=True)
        self.hardware_thread.start()

    def _hardware_loop(self):
        while True:
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
                        print(f"\n[HARDWARE] Found potential device. Connecting to {target_port}...")
                        ser = serial.Serial(target_port, 115200, timeout=1.5)
                        with self.lock:
                            self.serial_port = ser
                            self.use_hardware = True
                        print(f"[HARDWARE] Connected successfully on {target_port}!")
                    except Exception as e:
                        print(f"[HARDWARE] Connection failed on {target_port}: {e}")
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
                                self.use_hardware = True
                except Exception as e:
                    print(f"[HARDWARE] Serial communication error: {e}")
                    with self.lock:
                        self.use_hardware = False
                        if self.serial_port:
                            try:
                                self.serial_port.close()
                            except:
                                pass
                            self.serial_port = None
                    time.sleep(2)
            time.sleep(0.1)

    def set_mode(self, mode):
        self.mode = mode
        self.degrade_step = 0
        if mode == "Normal":
            self.start_time = time.time() # Reset continuous time for demo purposes

    def get_data(self):
        # 1. Try to fetch real hardware readings first
        with self.lock:
            if self.use_hardware and self.latest_hardware_data:
                data = self.latest_hardware_data.copy()
                self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)
                data["continuous_screen_time_min"] = self.continuous_screen_time_min
                return data

        # 2. Fall back to simulation model if hardware is not connected
        # Base values
        data = {
            "blink_rate": 16,
            "blink_duration_ms": 200,
            "eye_temp_celsius": 35.0,
            "screen_distance_cm": 45,
            "ambient_lux": 300,
            "room_humidity_pct": 50,
            "head_tilt_degrees": 5,
        }

        # Calculate continuous time
        self.continuous_screen_time_min = int((time.time() - self.start_time) / 60)
        data["continuous_screen_time_min"] = self.continuous_screen_time_min

        if self.mode == "Normal":
            pass # Keep base values
            
        elif self.mode == "Degrading":
            self.degrade_step += 1
            # Slowly drop blink rate and distance, increase temp and tilt
            data["blink_rate"] = max(4, 16 - (self.degrade_step // 2))
            data["screen_distance_cm"] = max(15, 45 - self.degrade_step)
            data["eye_temp_celsius"] = max(33.0, 35.0 - (self.degrade_step * 0.1))
            data["head_tilt_degrees"] = min(40, 5 + self.degrade_step)
            data["blink_duration_ms"] = min(800, 200 + (self.degrade_step * 10))
            
        elif self.mode == "Critical":
            data["blink_rate"] = 4
            data["blink_duration_ms"] = 700
            data["eye_temp_celsius"] = 33.5
            data["screen_distance_cm"] = 15
            data["ambient_lux"] = 80
            data["room_humidity_pct"] = 25
            data["head_tilt_degrees"] = 40
            data["continuous_screen_time_min"] = 50

        # Inject Noise
        data["blink_rate"] += random.randint(-1, 1)
        data["blink_duration_ms"] += random.randint(-20, 20)
        data["eye_temp_celsius"] += round(random.uniform(-0.2, 0.2), 1)
        data["screen_distance_cm"] += random.randint(-2, 2)
        data["ambient_lux"] += random.randint(-10, 10)
        data["room_humidity_pct"] += random.randint(-1, 1)
        data["head_tilt_degrees"] += random.randint(-2, 2)
        
        # Clamp bounds
        data["blink_rate"] = max(0, data["blink_rate"])
        data["screen_distance_cm"] = max(5, data["screen_distance_cm"])

        return data

simulator_instance = Simulator()
