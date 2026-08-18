import csv
import os
import time

DATA_DIR = "data"
SESSION_FILE = os.path.join(DATA_DIR, "session_log.csv")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def init_log():
    if not os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "blink_rate", "blink_duration_ms", "eye_temp_celsius", 
                             "screen_distance_cm", "ambient_lux", "room_humidity_pct", 
                             "head_tilt_degrees", "strain_level", "strain_score"])

def log_data(data, strain_level, strain_score):
    init_log()
    with open(SESSION_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            data.get("blink_rate", 0),
            data.get("blink_duration_ms", 200),
            data.get("eye_temp_celsius", 0.0),
            data.get("screen_distance_cm", 0),
            data.get("ambient_lux", 0),
            data.get("room_humidity_pct", 50.0),
            data.get("head_tilt_degrees", 0),
            strain_level,
            strain_score
        ])

def get_history(limit=60):
    if not os.path.exists(SESSION_FILE):
        return []
    
    import pandas as pd
    try:
        df = pd.read_csv(SESSION_FILE)
        return df.tail(limit).to_dict('records')
    except Exception:
        return []
