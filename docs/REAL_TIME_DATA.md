# Getting real sensor values into Netra Rakshaka

How to move from simulated telemetry to live readings off the spectacles, where those
values surface at each layer, and how to prove they are genuinely real.

---

## Current status: neither hardware path is active

| Path | Server side | Device side | Status |
|---|---|---|---|
| **USB serial** | `simulator.py` can read `DATA:` frames | `main.cpp` prints `DATA:` frames every 200 ms | **Reader is disabled** — [`simulator.py:30-32`](../simulator.py#L30-L32) has `start_hardware_polling()` commented out |
| **Wi-Fi POST** | `/sensor_data` works and is tested | — | **No Wi-Fi client in the firmware** — `main.cpp` contains no `WiFi`, `HTTPClient` or SSID code at all |

So today every value you see comes from `simulator.py`'s scenario model, which is why the
console badge reads **Backend simulator**. Pick one of the two options below to change that.

---

## How the backend chooses a source

`get_active_sensor_data()` in [`app.py`](../app.py) tries these in order, and stamps the
winner into `_source`, which is what the console badge displays:

| Priority | Source | Freshness window | `_source` |
|---|---|---|---|
| 1 | Wi-Fi frame held in memory | 30 s | `WIFI` |
| 2 | `data/wifi_cache.json` on disk | 30 s | `WIFI` |
| 3 | USB serial via `simulator_instance` | 15 s | `SERIAL` |
| 4 | Built-in scenario model | — | `SIMULATOR` |

Anything older than its window is treated as stale and the next source takes over, so a
disconnected device degrades to simulation instead of freezing on a dead reading.

---

## Option A — USB serial (fastest route to real data)

The firmware **already emits everything you need**. Over USB at 115 200 baud it prints one
line every 200 ms:

```
DATA:{"blink_rate":16,"blink_count":90,"blink_duration_ms":180,"eye_temp_celsius":34.6,...}
```

### 1. Confirm the device is talking

```bash
cd firmware
pio device monitor          # 115200 baud
```

You should see `DATA:` lines scrolling. If not, fix that before touching the server —
nothing downstream can work until this line exists.

### 2. Enable the reader

In [`simulator.py`](../simulator.py), uncomment the two lines in `__init__`:

```python
# DISABLED to prevent conflict with Wi-Fi receiver
if SERIAL_AVAILABLE:
    self.start_hardware_polling()
```

The comment's concern is already handled inside `_hardware_loop()`: it skips USB polling
whenever `data/wifi_cache.json` holds a frame newer than 15 s, so the two transports cannot
fight over the same reading.

### 3. Restart and verify

```bash
python app.py
```

- **Terminal** — the live line now reflects what the sensors see; blink at the IR sensor and
  `Blinks` should increment
- **Console badge** — top right changes from `Backend simulator` to `ESP32 · USB serial`
- **Device rail** — the five sensor dots turn green from the firmware's `*_ok` flags

> The port scan matches descriptions containing `usb`, `serial`, `cp210`, `ch340`,
> `espressif` or `jtag`, and opens the first match at 115 200 baud. If you have other
> USB-serial devices attached, pin the port explicitly rather than relying on the scan.

---

## Option B — Wi-Fi (untethered, needs firmware work)

The server side is finished and tested. Prove it without hardware first:

```bash
curl -X POST http://127.0.0.1:5000/sensor_data \
  -H "Content-Type: application/json" \
  -d '{"blink_rate":16,"screen_distance_cm":44,"eye_temp_celsius":34.6,"ambient_lux":280,
       "head_tilt_degrees":6,"room_humidity_pct":50,"blink_duration_ms":180,"blink_count":90,
       "room_temp_celsius":25.1,"tof_ok":1,"bmp_ok":1,"bh_ok":1,"mpu_ok":1,"humidity_ok":0}'
```

The console badge should flip to **ESP32 · Wi-Fi** within a second and hold it for 30 s.

### What the firmware still needs

`main.cpp` already builds the exact JSON string in `jsonPayload`. Adding Wi-Fi is a matter of
sending that same string:

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "your-network";
const char* WIFI_PASS = "your-password";
const char* SERVER_URL = "http://192.168.1.50:5000/sensor_data";   // the PC running app.py

void setupWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  Serial.printf("WiFi: %s  IP: %s\n",
                WiFi.status() == WL_CONNECTED ? "connected" : "FAILED",
                WiFi.localIP().toString().c_str());
}

// call from the telemetry block, alongside the existing Serial.println
void postTelemetry(const String& payload) {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(400);                 // never stall the 200 ms sensor loop
  http.POST(payload);
  http.end();
}
```

Two practical notes: post at **5 Hz at most** (every frame is fine, but a blocking POST that
takes longer than the loop period will starve the IR sampler, which needs its 4 ms cadence);
and the PC's firewall must allow inbound 5000 on the local network — `app.py` already binds
`0.0.0.0`, so it listens on every interface.

---

## Where the values appear once they flow

| Layer | What you see | How to look |
|---|---|---|
| Firmware | Raw frame | `pio device monitor` |
| Server terminal | One formatted line per 250 ms | stdout of `python app.py` |
| Socket.IO | `sensor_update` event, 5 Hz | Browser devtools → Network → WS |
| Console | Every panel, plus the source badge | http://127.0.0.1:5000 |
| REST | Last *N* logged rows | `GET /api/history?limit=200` |
| CSV | Append-only training log | `data/session_log.csv` |
| SQLite | `telemetry_history` table | `data/netra_rakshaka.db` |
| Aggregates | 24 h / 7 d summary | `GET /api/analytics?window=daily` |

---

## Proving the values are real, not simulated

Simulated frames are *plausible*, which is exactly what makes them dangerous during a demo.
Five checks that cannot be faked by the scenario model:

1. **Source badge** reads `ESP32 · Wi-Fi` or `ESP32 · USB serial`, never `Backend simulator`
2. **`i2c` field** lists actual bus addresses (`0x23,0x29,0x68,0x76`); the simulator sends none
3. **Blink count rises when you blink** and stays flat when you hold your eyes open — the
   simulator's count climbs on a fixed schedule regardless
4. **Cover the ToF sensor** — `screen_distance_cm` should collapse within ~200 ms
5. **Shine a light at the BH1750** — `ambient_lux` should jump by hundreds instantly

If a sensor is dead the firmware clears its `*_ok` flag, the backend substitutes a simulated
value for **that field only**, and it is listed in `simulated_fields` — the device rail shows
that channel amber. Check this before trusting any single reading.

---

## Ground truth for validating each sensor

Do this once, before collecting training data. Each check takes a minute:

| Channel | Reference | Acceptance |
|---|---|---|
| `blink_rate` | Count your own blinks for 60 s while a partner watches the reading | Within ±2 bpm; **this matters most** — it drives the hard Critical rule |
| `blink_duration_ms` | Deliberate slow blinks (~300 ms) vs normal (~150 ms) | The two are clearly separable |
| `screen_distance_cm` | Tape measure at 30 / 50 / 70 cm | Within ±3 cm across the range |
| `head_tilt_degrees` | Phone level app held against your head, at 0° / 20° / 40° | Within ±5°, and 0° reads ~0 when upright |
| `ambient_lux` | Any phone lux meter app, in the same spot | Same order of magnitude; absolute agreement is not expected |
| `eye_temp_celsius` | Room thermometer | Within ~2 °C — the BMP280 reads its own surface, not your cornea |

Record the results. "Blink rate validated against manual count at ±2 bpm" is the sentence
that makes the rest of the system credible.

---

## Collecting data worth retraining on

The shipped model is binary (`Safe` / `Critical`) and 61 % of its training rows have
`blink_rate: 0`, meaning no spectacles were attached — it has largely learned sensor absence.
Fixing that needs deliberate sessions, not more idle logging.

1. **Wear the spectacles for the whole session.** Discard any run whose rows are mostly
   `blink_rate: 0`, and check `simulated_fields` is empty.
2. **Cover all three states.** `Moderate` is currently absent from the training set, so it
   cannot be predicted. Produce it on purpose: work at ~28 cm with a mild forward lean, or
   let a long session fatigue you naturally, and note the wall-clock windows.
3. **Log ground-truth labels separately** — a plain text file of `HH:MM start / HH:MM end /
   how your eyes actually felt` is enough to relabel rows afterwards.
4. **Aim for balance**, not volume. A few thousand rows per class from genuine wear beat
   50,000 rows of simulator constants.
5. **Retrain and read the per-class scores**, not just accuracy:
   ```bash
   python train_classifier.py
   ```
   Accuracy will look excellent even for a useless model while one class dominates; the
   `Moderate` recall is the number that tells you whether it learned anything.

> **Worth adding first:** `session_logger.py` does not record which source a row came from,
> so simulator rows cannot be filtered out of training after the fact. Adding a `source`
> column (`data["_source"]` is already available at the call site) makes every future retrain
> trustworthy.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Badge stays `Backend simulator` | Neither path is enabled — see the status table at the top |
| Badge flickers between real and simulator | Frames arriving slower than the freshness window; check the firmware loop is not blocking on a slow POST |
| `SERIAL` never appears | Reader still commented out in `simulator.py`, `pyserial` missing, or the port was claimed by `pio device monitor` — close the monitor first, only one process can hold a COM port |
| Wi-Fi POST returns 200 but the badge does not change | The frame is landing but the stream loop is not running; check the server terminal for a `UnicodeEncodeError` on the emoji print (`set PYTHONIOENCODING=utf-8`) |
| Values arrive but every sensor dot is amber | Firmware reports the sensors off the I²C bus; check wiring on `SDA 8` / `SCL 9` and the `i2c` field |
