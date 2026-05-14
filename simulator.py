import random
import time

class Simulator:
    def __init__(self):
        self.mode = "Normal" # "Normal", "Degrading", "Critical"
        self.degrade_step = 0
        self.start_time = time.time()
        self.continuous_screen_time_min = 0

    def set_mode(self, mode):
        self.mode = mode
        self.degrade_step = 0
        if mode == "Normal":
            self.start_time = time.time() # Reset continuous time for demo purposes

    def get_data(self):
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
