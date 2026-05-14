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

def stream_data():
    while True:
        # 1. Get simulated sensor data
        data = simulator_instance.get_data()
        
        # 2. Classify strain
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
    print("Netra Rakshaka Server running on http://127.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)
