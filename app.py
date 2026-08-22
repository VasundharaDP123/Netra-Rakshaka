import os
import sys
import json
import logging
import threading
import time

# Force UTF-8 stdout encoding on Windows to support emojis cleanly
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS

from simulator import simulator_instance
from classifier import classify_strain
from session_logger import log_data, get_history, init_log
from database import (init_db, log_telemetry_db, log_break_db, get_user_settings, 
                      update_user_settings, get_analytics_summary, log_compliance_db, log_deep_work_session_db)

# Mute noisy Flask HTTP request logging for clean terminal output
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'netrarakshaka_secret'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize CSV and SQLite database
init_log()
init_db()

# ── Deep Work state ────────────────────────────────────────────────────────
# Deep Work has to be a fact the whole system agrees on, not just browser state:
# screen_control.py runs in its own process and would otherwise keep enforcing
# breaks at OS level while the dashboard says the session is silent. The expiry
# is stored so a missed "complete" event can never suppress breaks for ever.
deep_work_until = 0.0
deep_work_lock = threading.Lock()

def deep_work_active():
    with deep_work_lock:
        return time.time() < deep_work_until

def deep_work_remaining():
    with deep_work_lock:
        return max(0, int(deep_work_until - time.time()))

CACHE_FILE = os.path.join("data", "wifi_cache.json")
last_wifi_data = None
last_wifi_lock = threading.Lock()

@app.route("/sensor_data", methods=["POST"])
def receive_sensor_data():
    global last_wifi_data
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload"}), 400
        
        # Save timestamp to track freshness
        data["_received_at"] = time.time()
        
        with last_wifi_lock:
            last_wifi_data = data.copy()
        
        # Write to physical cache file as disk backup
        if not os.path.exists("data"):
            os.makedirs("data")
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
            
        print(f"\n🎉 [WIFI TELEMETRY RECEIVED!] Blink: {data.get('blink_rate')} bpm | Distance: {data.get('screen_distance_cm')}cm | Eye Temp: {data.get('eye_temp_celsius')}°C | Tilt: {data.get('head_tilt_degrees')}°\n")
        return jsonify({"status": "success", "message": "Data received successfully"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def get_active_sensor_data():
    global last_wifi_data
    # 1. Check in-memory Wi-Fi cache first (freshness window: 30 seconds)
    with last_wifi_lock:
        if last_wifi_data:
            rec_time = last_wifi_data.get("_received_at", 0)
            if 0 <= time.time() - rec_time < 30.0:
                data_clean = {k: v for k, v in last_wifi_data.items() if not k.startswith("_")}
                if "continuous_screen_time_min" not in data_clean:
                    data_clean["continuous_screen_time_min"] = 0
                data_clean["_source"] = "WIFI"
                return data_clean

    # 2. Check disk Wi-Fi cache as backup (freshness window: 30 seconds)
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
            rec_time = data.get("_received_at", 0)
            if 0 <= time.time() - rec_time < 30.0:
                data_clean = {k: v for k, v in data.items() if not k.startswith("_")}
                if "continuous_screen_time_min" not in data_clean:
                    data_clean["continuous_screen_time_min"] = 0
                data_clean["_source"] = "WIFI"
                return data_clean
        except Exception:
            pass

    # 3. USB serial telemetry from the spectacles. This is real hardware data,
    #    not the simulation model - get_hardware_data() returns None rather than
    #    falling back, so nothing invented can reach the dashboard here.
    hw = simulator_instance.get_hardware_data()
    if hw:
        hw["_source"] = "SERIAL"
        return hw

    # 4. Nothing connected on either transport. Report zeros rather than inventing
    #    values, and label the source so the console can say so plainly.
    return {
        "blink_rate": 0,
        "blink_count": 0,
        "screen_distance_cm": 0,
        "eye_temp_celsius": 0.0,
        "ambient_lux": 0,
        "room_humidity_pct": 0,
        "head_tilt_degrees": 0,
        "edge_ai_strain": "Safe",
        "_source": "HARDWARE_DISCONNECTED"
    }

last_print_time = 0
last_warn_time = 0

def stream_data():
    global last_print_time, last_warn_time
    while True:
        # 1. Get sensor data (Wi-Fi POST -> USB Serial -> Simulator fallback)
        data = get_active_sensor_data()
        
        # 3. Classify strain
        strain_level, strain_score = classify_strain(data)
        data["strain_level"] = strain_level
        data["strain_score"] = strain_score
        
        # 3. Log data to CSV and SQLite DB
        log_data(data, strain_level, strain_score)
        log_telemetry_db(data, strain_level, strain_score)
        
        # 4. Push to dashboard via WebSocket
        socketio.emit("sensor_update", data)
        
        # 5. Print clean, formatted single-line output 4x per second
        now = time.time()
        if now - last_print_time >= 0.25:
            blink = data.get("blink_rate", 0)      # rolling 60s rate - rises and falls
            blinks = data.get("blink_count", 0)     # cumulative since boot - only rises
            dist = data.get("screen_distance_cm", 0)
            temp = data.get("eye_temp_celsius", 0.0)
            tilt = data.get("head_tilt_degrees", 0)
            lux = data.get("ambient_lux", 0)
            status_icon = "🟢" if strain_level == "Safe" else ("🟡" if strain_level == "Moderate" else "🔴")

            dist_s = f"{dist:>2}cm"
            temp_s = f"{temp:.1f}°C"
            lux_s = f"{lux:>5}"
            tilt_s = f"{tilt:>2}°"
            bus = ""

            # IR diagnostics - only sent while the firmware has IR_DEBUG set to 1
            ir = ""
            if "ir_raw" in data:
                ir = (f" | IR raw:{data.get('ir_raw',0):>4}"
                      f" ref:{data.get('ir_baseline',0):>4}"
                      f" mad:{data.get('ir_mad',0):>3}"
                      f" {'SHUT' if data.get('eye_closed') else 'open'}")

            source_tag = data.get("_source", "SERIAL")
            if source_tag == "HARDWARE_DISCONNECTED":
                print(f"📡 [SPECTACLES DISCONNECTED] Waiting for hardware... (Close Serial Monitor if open, or check USB/Wi-Fi connection)")
            else:
                print(f"📡 [{source_tag}] {status_icon} Strain: {strain_level:<8} ({strain_score:>2}/100) | Distance: {dist_s} | Blink Rate: {blink:>2}/min | Blinks: {blinks:>3} | Env Temp: {temp_s} | Head Tilt: {tilt_s} | Lux: {lux_s}{bus}{ir}")
            last_print_time = now

            # Offline sensors are no longer flagged inline, so warn separately and
            # sparingly - otherwise a 0 reading looks identical to a real one.
            offline = [n for n, k in (("TCRT5000", "tcrt_ok"), ("ToF", "tof_ok"), ("BMP280", "bmp_ok"),
                                      ("BH1750", "bh_ok"), ("MPU6050", "mpu_ok"))
                       if k in data and not data[k]]
            if offline and now - last_warn_time >= 15.0:
                print(f"   ⚠  Not responding: {', '.join(offline)}"
                      f"  (I2C bus: {data.get('i2c', '?')})"
                      f" - showing simulated values for these until the wiring is fixed")
                last_warn_time = now
        
        # 6. Wait 200ms - matches the firmware's telemetry cadence
        socketio.sleep(0.2)

@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/analytics", methods=["GET"])
def analytics():
    window = request.args.get("window", "daily")
    days = 7 if window == "weekly" else 1
    return jsonify(get_analytics_summary(days=days))

@app.route("/api/settings", methods=["GET", "POST"])
def user_settings():
    if request.method == "POST":
        req = request.json or {}
        success = update_user_settings(req)
        return jsonify({"status": "success" if success else "error", "settings": get_user_settings()})
    return jsonify(get_user_settings())

@app.route("/api/log_break", methods=["POST"])
def record_break():
    req = request.json or {}
    reason = req.get("reason", "Critical Strain")
    duration = int(req.get("duration", 20))
    log_break_db(reason=reason, duration=duration)
    return jsonify({"status": "success"})

@app.route("/api/compliance", methods=["POST"])
def record_compliance():
    req = request.json or {}
    action = req.get("action", "complied")
    event_type = req.get("event_type", "20-20-20")
    log_compliance_db(action=action, event_type=event_type)
    return jsonify({"status": "success", "action": action})

@app.route("/api/deep_work_start", methods=["POST"])
def start_deep_work():
    req = request.json or {}
    duration_min = int(req.get("duration_min", 25))

    global deep_work_until
    with deep_work_lock:
        deep_work_until = time.time() + duration_min * 60

    socketio.emit("deep_work_event", {"action": "start", "duration_min": duration_min})
    print(f"\n🧠 [DEEP WORK MODE STARTED] {duration_min} minutes silent focus session active.\n")
    return jsonify({"status": "success", "duration_min": duration_min})

@app.route("/api/deep_work_complete", methods=["POST"])
def complete_deep_work():
    req = request.json or {}
    duration_min = int(req.get("duration_min", 25))
    alerts = int(req.get("alerts", 0))
    status = req.get("status", "completed")
    log_deep_work_session_db(duration_min=duration_min, alerts=alerts, status=status)

    global deep_work_until
    with deep_work_lock:
        deep_work_until = 0.0

    socketio.emit("deep_work_event", {"action": "complete", "alerts": alerts})
    print(f"\n🎉 [DEEP WORK SESSION COMPLETED] {duration_min} min session. {alerts} critical alerts.\n")
    return jsonify({"status": "success"})

@app.route("/api/deep_work_status", methods=["GET"])
def deep_work_status():
    """Lets screen_control.py sync on connect, or recover after a restart."""
    return jsonify({"active": deep_work_active(), "remaining_sec": deep_work_remaining()})

@app.route("/api/scenario", methods=["POST"])
def set_scenario():
    req = request.json
    mode = req.get("mode", "Normal")
    if mode in ["Normal", "Degrading", "Critical"]:
        simulator_instance.set_mode(mode)
        return jsonify({"status": "success", "mode": mode})
    return jsonify({"status": "error", "message": "Invalid mode"}), 400

@app.route("/api/history", methods=["GET"])
def history():
    limit = int(request.args.get("limit", 60))
    return jsonify(get_history(limit))

if __name__ == "__main__":
    # Start background stream
    socketio.start_background_task(target=stream_data)
    
    ports_to_try = [5000, 5001, 5002, 5003]
    for p in ports_to_try:
        try:
            print("\n==========================================================================================")
            print(f"  Netra Rakshaka Server running on http://127.0.0.1:{p}")
            print("  Streaming live sensor telemetry line by line below...")
            print("==========================================================================================\n")
            
            socketio.run(app, host="0.0.0.0", port=p, debug=False, use_reloader=False)
            break
        except OSError:
            print(f"[PORT WARN] Port {p} is currently occupied by a background process. Trying fallback port...")
            continue


