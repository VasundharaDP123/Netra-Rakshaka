import time
import socketio
import wmi
import tkinter as tk
import ctypes
import os
import pythoncom

sio = socketio.Client()
critical_count = 0
trigger_dim = False
last_dim_time = 0
agent_start_time = time.time()

# A single Critical packet must not dim the screen. The simulated head tilt sweeps
# to 35 degrees every 4 seconds, which the classifier reads as Critical - so a
# one-shot trigger fires within seconds of startup, every time.
CRITICAL_STREAK_REQUIRED = 15  # ~3s of sustained Critical at 5 packets/sec
STARTUP_GRACE_SEC = 60         # no breaks while the sensors are still settling

def set_brightness(level):
    try:
        pythoncom.CoInitialize()
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)
        c = wmi.WMI(namespace='wmi')
        methods = c.WmiMonitorBrightnessMethods()[0]
        methods.WmiSetBrightness(level, 0)
    except Exception as e:
        print(f"Warning: Could not set brightness on this OS. Error: {e}")

def show_overlay():
    root = tk.Tk()
    root.attributes('-fullscreen', True)
    root.attributes('-topmost', True)
    root.configure(bg='#0d1117')
    
    # Main Warning
    label = tk.Label(root, text="🛑 MANDATORY EYE BREAK", font=('DM Sans', 48, 'bold'), fg='#f87171', bg='#0d1117')
    label.pack(expand=True, pady=(80, 0))

    label2 = tk.Label(root, text="Critical strain detected. Screen dimmed.\nRest your eyes for 20 seconds.", font=('DM Sans', 18), fg='#7d8590', bg='#0d1117')
    label2.pack(expand=True)
    
    # Countdown
    count_label = tk.Label(root, text="20", font=('DM Mono', 96, 'bold'), fg='#e6edf3', bg='#0d1117')
    count_label.pack(expand=True, pady=(0, 40))

    # Instructions Frame
    inst_frame = tk.Frame(root, bg='#0d1117')
    inst_frame.pack(expand=True, pady=(0, 100))

    # Option 1
    opt1 = tk.Frame(inst_frame, bg='#161b22', bd=1, relief="solid", padx=20, pady=20)
    opt1.grid(row=0, column=0, padx=20)
    tk.Label(opt1, text="👁", font=('Segoe UI Emoji', 36), fg='white', bg='#161b22').pack()
    tk.Label(opt1, text="Close your eyes\nor look away", font=('DM Sans', 14), fg='#e6edf3', bg='#161b22').pack(pady=(10,0))

    # Option 2
    opt2 = tk.Frame(inst_frame, bg='#161b22', bd=1, relief="solid", padx=20, pady=20)
    opt2.grid(row=0, column=1, padx=20)
    tk.Label(opt2, text="🌿", font=('Segoe UI Emoji', 36), fg='white', bg='#161b22').pack()
    tk.Label(opt2, text="Focus on something\n20 feet away", font=('DM Sans', 14), fg='#e6edf3', bg='#161b22').pack(pady=(10,0))

    # Option 3
    opt3 = tk.Frame(inst_frame, bg='#161b22', bd=1, relief="solid", padx=20, pady=20)
    opt3.grid(row=0, column=2, padx=20)
    tk.Label(opt3, text="💧", font=('Segoe UI Emoji', 36), fg='white', bg='#161b22').pack()
    tk.Label(opt3, text="Blink slowly\n10 times", font=('DM Sans', 14), fg='#e6edf3', bg='#161b22').pack(pady=(10,0))

    def countdown(count):
        if count > 0:
            count_label.config(text=str(count))
            root.after(1000, countdown, count - 1)
        else:
            try:
                root.quit()
                root.destroy()
            except Exception:
                pass

    countdown(20)
    root.mainloop()

@sio.on('sensor_update')
def on_sensor_update(data):
    global critical_count, trigger_dim, last_dim_time

    # Don't interrupt during startup, while the blink baseline is still settling
    # and blink_rate is legitimately 0.
    if time.time() - agent_start_time < STARTUP_GRACE_SEC:
        return

    # 50 second cooldown after a break so you don't get trapped in a loop!
    if time.time() - last_dim_time < 50:
        return

    if data['strain_level'] == 'Critical':
        critical_count += 1
        if critical_count >= CRITICAL_STREAK_REQUIRED:
            trigger_dim = True
            critical_count = 0
    else:
        critical_count = 0

def main_loop():
    global trigger_dim, last_dim_time
    print("DEBUG: Main loop started.")
    while True:
        if trigger_dim:
            print("🔴 TRIGGERING SCREEN DIM!")
            set_brightness(20)
            
            if not os.path.exists('data'):
                os.makedirs('data')
            with open("data/breaks_log.txt", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Enforced 20-second break\n")
            
            # Record break in SQLite DB via Flask API
            try:
                import urllib.request
                import json
                req = urllib.request.Request("http://127.0.0.1:5000/api/log_break", 
                                             data=json.dumps({"reason": "Critical Eye Strain", "duration": 20}).encode('utf-8'),
                                             headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass
            
            # Set cooldown timestamp and reset trigger BEFORE showing the overlay
            # This blocks the background thread from accumulating critical packets during the break
            last_dim_time = time.time()
            trigger_dim = False
            
            # Show overlay (blocks for 20s) in main thread
            show_overlay()
            
            print("✅ Restoring screen brightness")
            set_brightness(100)
            print("⏳ Cooldown period active (50s) to prevent loop...")
        
        time.sleep(0.1)

if __name__ == '__main__':
    print("=========================================================")
    print("  Netra Rakshaka Screen Control Intervention Agent Ready ")
    print("=========================================================")
    
    connected = False
    while not connected:
        try:
            print("DEBUG: Attempting Socket.IO connection to http://127.0.0.1:5000...")
            sio.connect('http://127.0.0.1:5000')
            print("\n✅ Connected successfully to Netra-Rakshaka Server!")
            print("👁️ Monitoring eye strain... Mandatory 20s breaks will trigger on Critical strain.\n")
            connected = True
        except Exception:
            print("⏳ Server (app.py) not active on port 5000 yet. Retrying in 3 seconds...")
            time.sleep(3)
            
    main_loop()
