"""Netra Rakshaka link checker.

Answers one question: where does the chain break between the spectacles and the
dashboard? Run it any time the console shows zeros.

    python tools/check_link.py
"""
import json
import os
import sys
import time

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"
verdicts = []


def step(title):
    print(f"\n{title}\n" + "-" * len(title))


# ── 1. USB serial ───────────────────────────────────────────────────────────
step("1. USB serial link")
serial_frames = 0
try:
    import serial
    import serial.tools.list_ports as lp

    ports = list(lp.comports())
    if not ports:
        print(f"  {BAD} no serial device attached to this PC")
        print("       -> plug the board into the NATIVE USB port with a DATA cable")
        verdicts.append("USB: nothing attached")
    else:
        for p in ports:
            print(f"  {OK} found {p.device}  ({p.description})")
        dev = ports[0].device
        try:
            ser = serial.Serial(dev, 115200, timeout=1)
            print(f"  {OK} {dev} opened - listening 6 s for DATA: frames...")
            end = time.time() + 6
            while time.time() < end:
                line = ser.readline().decode("utf-8", "ignore").strip()
                if line.startswith("DATA:"):
                    serial_frames += 1
            ser.close()
            if serial_frames:
                print(f"  {OK} {serial_frames} frames received - the board is talking")
            else:
                print(f"  {BAD} port opens but the board sends nothing")
                print("       -> press the physical RESET/EN button, or replug the USB")
                verdicts.append("USB: board silent")
        except Exception as exc:
            msg = str(exc)
            if "Access is denied" in msg or "PermissionError" in msg:
                print(f"  {BAD} {dev} is held by another program")
                print("       -> CLOSE the Arduino/PlatformIO serial monitor. One owner per COM port.")
                verdicts.append("USB: port held by a serial monitor")
            else:
                print(f"  {BAD} cannot open {dev}: {msg[:70]}")
                verdicts.append("USB: port error")
except ImportError:
    print(f"  {WARN} pyserial not installed (pip install pyserial)")

# ── 2. Wi-Fi ────────────────────────────────────────────────────────────────
step("2. Wi-Fi link")
cache = os.path.join("data", "wifi_cache.json")
if os.path.exists(cache):
    try:
        age = time.time() - json.load(open(cache)).get("_received_at", 0)
        if age < 30:
            print(f"  {OK} last Wi-Fi frame {age:.1f} s ago - the board is posting")
        else:
            print(f"  {BAD} last Wi-Fi frame was {age/60:.1f} minutes ago - nothing arriving now")
            verdicts.append("Wi-Fi: no recent frames")
    except Exception:
        print(f"  {WARN} wifi_cache.json unreadable")
else:
    print(f"  {BAD} no Wi-Fi frame has ever reached this server")
    verdicts.append("Wi-Fi: never connected")

print("\n  This PC's addresses - SERVER_HOST in firmware/src/secrets.h must be the")
print("  Wi-Fi one, and the board must be on the same network (2.4 GHz only):")
try:
    import re
    import subprocess

    out = subprocess.run(["ipconfig"], capture_output=True, text=True).stdout
    adapter = None
    for line in out.splitlines():
        s = line.strip()
        if s.endswith(":") and "adapter" in s.lower():
            adapter = s.rstrip(":")
        m = re.search(r"IPv4 Address[^:]*:\s*([\d.]+)", s)
        if m:
            star = " <-- use this" if "Wi-Fi" in (adapter or "") else ""
            print(f"       {m.group(1):<16} {adapter}{star}")
except Exception:
    pass

# ── 3. Server ───────────────────────────────────────────────────────────────
step("3. Server -> dashboard stream")
try:
    import socketio

    frames = []
    sio = socketio.Client()
    sio.on("sensor_update", lambda d: frames.append(d))
    sio.connect("http://127.0.0.1:5000")
    time.sleep(3)
    sio.disconnect()

    if not frames:
        print(f"  {BAD} server is up but emits nothing - the telemetry thread has died")
        verdicts.append("server: not streaming")
    else:
        f = frames[-1]
        src = f.get("_source")
        print(f"  {OK} {len(frames)} packets in 3 s | source: {src}")
        print(f"       blink {f.get('blink_rate')} bpm | distance {f.get('screen_distance_cm')} cm "
              f"| lux {f.get('ambient_lux')} | strain {f.get('strain_level')}")
        if src in ("WIFI", "SERIAL"):
            print(f"  {OK} REAL hardware data is reaching the dashboard")
        else:
            print(f"  {BAD} source is {src} - the dashboard can only show what arrives here")
            verdicts.append(f"server: receiving nothing ({src})")
except Exception as exc:
    print(f"  {BAD} cannot reach the server on 127.0.0.1:5000 - is app.py running? ({str(exc)[:50]})")
    verdicts.append("server: unreachable")

# ── verdict ─────────────────────────────────────────────────────────────────
step("Verdict")
if not verdicts:
    print("  Everything is connected. Live values are reaching the console.")
else:
    print("  Broken link(s):")
    for v in verdicts:
        print(f"    - {v}")
    print("\n  Fix them top-down: the dashboard can only display what the server")
    print("  receives, and the server can only receive what the board sends.")
sys.exit(0)
