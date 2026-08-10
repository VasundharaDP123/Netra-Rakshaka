import threading
import time
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS

from simulator import simulator_instance
from classifier import classify_strain
from session_logger import log_data, get_history, init_log

app = Flask(__name__)
app.config['SECRET_KEY'] = 'netrarakshaka_secret'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize CSV log
init_log()

wifi_data = None
last_wifi_time = 0

import json
import os

@app.route("/sensor_data", methods=["POST"])
def receive_wifi_data():
    data = request.json
    if data:
        # Write to file to guarantee data is shared across all Python threads/processes
        try:
            with open("wifi_cache.json", "w") as f:
                json.dump({"time": time.time(), "data": data}, f)
        except Exception:
            pass
            
        print(f"\n[WIFI] Live 5-Sensor Data Received:")
        print(f"Dist: {data.get('screen_distance_cm')}cm | Gyro: {data.get('head_tilt_degrees')}° | Temp: {data.get('eye_temp_celsius')}C | Lux: {data.get('ambient_lux')} | Blink: {data.get('blink_rate')} BPM")
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "error"}), 400

def stream_data():
    start_time = time.time()
    while True:
        wifi_active = False
        # 1. Read Wi-Fi data from the secure file cache
        if os.path.exists("wifi_cache.json"):
            try:
                with open("wifi_cache.json", "r") as f:
                    cache = json.load(f)
                if time.time() - cache["time"] < 5:
                    data = cache["data"]
                    data["continuous_screen_time_min"] = int((time.time() - start_time) / 60)
                    wifi_active = True
            except Exception:
                pass
                
        # 2. Fallback to simulator if Wi-Fi is disconnected for >5 seconds
        if not wifi_active:
            data = simulator_instance.get_data()
        
        # 3. Classify strain
        strain_level, strain_score = classify_strain(data)
        data["strain_level"] = strain_level
        data["strain_score"] = strain_score
        
        # 3. Log data
        log_data(data, strain_level, strain_score)
        
        # 4. Push to dashboard via WebSocket
        socketio.emit("sensor_update", data)
        
        # 5. Wait 500ms
        socketio.sleep(0.5)

@app.route("/")
def dashboard():
    return render_template("dashboard.html")

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
    
    # Run server
    print("Netra Rakshaka Server running on http://127.0.0.1:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)
