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

SERVER = 'http://127.0.0.1:5000'

# Deep Work silences this agent too. The dashboard suppressing its own overlay is
# not enough: this process dims the physical display, so a focus session would be
# interrupted at OS level no matter what the browser showed.
#
# An absolute expiry is stored rather than a flag, so a missed "complete" event
# can never leave the screen unprotected for ever - the suppression lapses on its
# own when the session would have ended.
deep_work_until = 0.0

def deep_work_active():
    return time.time() < deep_work_until

def sync_deep_work_state():
    """Ask the server on connect, so an agent started mid-session is not deaf."""
    global deep_work_until
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen(SERVER + '/api/deep_work_status', timeout=3) as r:
            state = _json.loads(r.read().decode())
        if state.get('active'):
            remaining = int(state.get('remaining_sec', 0))
            deep_work_until = time.time() + remaining
            print('[DEEP WORK] session already running - breaks held for %ds' % remaining)
    except Exception:
        pass

# How dark to go during a break. The screen is never set ABOVE its current
# level: on a laptop already at 0% a "dim to 20" would brighten it, which is the
# opposite of a rest.
DIM_LEVEL = 20

def get_brightness():
    """Current panel brightness 0-100, or None if the panel does not report it."""
    try:
        pythoncom.CoInitialize()
        c = wmi.WMI(namespace='wmi')
        return int(c.WmiMonitorBrightness()[0].CurrentBrightness)
    except Exception as e:
        print(f"Warning: could not read current brightness ({e}).")
        return None

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

    # ── The escape hatch ───────────────────────────────────────────────────
    # A break that cannot be dismissed is dangerous: it takes the whole screen
    # for 20 seconds, and the user may be presenting or in a call. Expecting
    # them to have switched Deep Work on beforehand repeats the very mistake
    # this project exists to fix - people forget.
    #
    # So the way out is not a bare "skip". The user declares a focus session,
    # which means the choice is deliberate, it is recorded, and the protection
    # is held for the whole session rather than only this one break.
    closed = {'done': False}

    def finish():
        # Both the countdown and the button can reach here, so guard it.
        if closed['done']:
            return
        closed['done'] = True
        try:
            root.quit()
            root.destroy()
        except Exception:
            pass

    def start_deep_work():
        global deep_work_until
        # Hold locally first, so the user is protected even if the server call
        # below fails - the screen must never be seized again a second later.
        deep_work_until = time.time() + 25 * 60
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(
                SERVER + '/api/deep_work_start',
                data=_json.dumps({"duration_min": 25}).encode('utf-8'),
                headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=2)
            print("🧠 [DEEP WORK] Started from the break screen - breaks held for 25 minutes.")
        except Exception:
            print("🧠 [DEEP WORK] Started locally (server unreachable) - breaks held for 25 minutes.")
        finish()

    btn_frame = tk.Frame(root, bg='#0d1117')
    btn_frame.pack(expand=True, pady=(0, 70))

    tk.Button(btn_frame, text="I'm busy right now  —  Start Deep Work (25 min)",
              font=('DM Sans', 15, 'bold'),
              fg='#ffffff', bg='#2563eb',
              activebackground='#1d4ed8', activeforeground='#ffffff',
              relief='flat', bd=0, padx=30, pady=15,
              cursor='hand2', command=start_deep_work).pack()

    tk.Label(btn_frame,
             text="Use this only if you are presenting or in a meeting.",
             font=('DM Sans', 11), fg='#7d8590', bg='#0d1117').pack(pady=(12, 0))

    def countdown(count):
        if closed['done']:
            return                      # the user left early via Deep Work
        if count > 0:
            count_label.config(text=str(count))
            root.after(1000, countdown, count - 1)
        else:
            finish()

    countdown(20)
    root.mainloop()

@sio.on('deep_work_event')
def on_deep_work_event(data):
    global deep_work_until, critical_count
    action = (data or {}).get('action')
    if action == 'start':
        mins = int((data or {}).get('duration_min', 25))
        deep_work_until = time.time() + mins * 60
        critical_count = 0          # drop any streak built up before the session
        print('[DEEP WORK] %d min focus session - enforced breaks held until it ends.' % mins)
    elif action in ('complete', 'end', 'cancel'):
        deep_work_until = 0.0
        critical_count = 0
        print('[DEEP WORK ENDED] Enforced breaks are active again.')

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

    # Deep Work: hold every break, and do not accumulate a streak while holding,
    # so the session does not end with a break already queued up.
    if deep_work_active():
        critical_count = 0
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

            # Remember what the user had before touching anything, so the break
            # restores THEIR brightness instead of forcing 100%.
            previous_brightness = get_brightness()
            if previous_brightness is None:
                dim_target = DIM_LEVEL
                print("   (brightness unreadable - it will be left dimmed after the break)")
            else:
                dim_target = min(DIM_LEVEL, previous_brightness)
                print(f"   (was {previous_brightness}% -> dimming to {dim_target}%)")

            if previous_brightness is None or dim_target < previous_brightness:
                set_brightness(dim_target)
            else:
                print("   (already at or below the dim level - leaving brightness alone)")
            
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
            
            if previous_brightness is None:
                print("⚠️  Original brightness unknown - not forcing a level. Adjust manually if needed.")
            elif dim_target < previous_brightness:
                print(f"✅ Restoring screen brightness to {previous_brightness}%")
                set_brightness(previous_brightness)
            else:
                print("✅ Break over (brightness was never changed)")
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
