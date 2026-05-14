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
    root.configure(bg='black')
    
    label = tk.Label(root, text="👁 NETRA RAKSHAKA", font=('Helvetica', 48, 'bold'), fg='#00bcd4', bg='black')
    label.pack(expand=True, pady=(100, 0))

    label2 = tk.Label(root, text="ENFORCED EYE BREAK IN PROGRESS", font=('Helvetica', 24), fg='white', bg='black')
    label2.pack(expand=True)
    
    count_label = tk.Label(root, text="20", font=('Helvetica', 96, 'bold'), fg='#ff1744', bg='black')
    count_label.pack(expand=True, pady=(0, 100))

    def countdown(count):
        if count > 0:
            count_label.config(text=str(count))
            root.after(1000, countdown, count - 1)
        else:
            root.destroy()

    countdown(20)
    root.mainloop()

@sio.on('sensor_update')
def on_sensor_update(data):
    global critical_count, trigger_dim, last_dim_time
    
    # 50 second cooldown after a break so you don't get trapped in a loop!
    if time.time() - last_dim_time < 50:
        return
        
    if data['strain_level'] == 'Critical':
        critical_count += 1
        if critical_count >= 3:
            trigger_dim = True
            critical_count = 0
    else:
        critical_count = 0

def main_loop():
    global trigger_dim, last_dim_time
    while True:
        if trigger_dim:
            print("🔴 TRIGGERING SCREEN DIM!")
            set_brightness(20)
            
            if not os.path.exists('data'):
                os.makedirs('data')
            with open("data/breaks_log.txt", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Enforced 20-second break\n")
            
            # Show overlay (blocks for 20s) in main thread
            show_overlay()
            
            print("✅ Restoring screen brightness")
            set_brightness(100)
            
            trigger_dim = False
            last_dim_time = time.time()
            print("⏳ Cooldown period started (50s) to prevent loop...")
        
        time.sleep(0.1)

if __name__ == '__main__':
    print("Starting Screen Control Intervention Script...")
    try:
        sio.connect('http://localhost:5000')
        # Run the tkinter loop in the main thread to prevent thread crashes
        main_loop()
    except Exception as e:
        print(f"Error: {e}")
