import base64
import zlib
import urllib.request
import os

mermaid_code = """graph TD
    %% Hardware/Sensor Layer
    subgraph Hardware ["Edge Device (ESP32 Smart Spectacles)"]
        IR["IR Sensor: Blink Rate"]
        TOF["ToF Sensor: Distance"]
        TEMP["Thermal Sensor: Eye Temp"]
        IMU["IMU Sensor: Head Posture"]
        ENV["Environment: Lux & Humidity"]
    end

    %% Software Simulator Layer
    subgraph Sim ["Software Simulator (simulator.py)"]
        Gen["Data Generator: Scenarios"]
    end

    %% Backend Layer
    subgraph Backend ["Python Flask Backend (app.py)"]
        Stream["Data Streamer: 0.5s Loop"]
        Classify["Rule-Based Classifier"]
        Log["Data Logger (CSV)"]
        WS["WebSocket Server"]
    end

    %% Data Storage
    subgraph Storage ["Local Storage"]
        CSV[("Session Logs (CSV)")]
        TXT[("Break Logs (TXT)")]
    end

    %% Intervention Layer
    subgraph Intervention ["Cyber-Physical Intervention (screen_control.py)"]
        WMI["WMI OS Control: Screen Dimming"]
        TK["Tkinter UI: Mandatory Break Overlay"]
    end

    %% Frontend Layer
    subgraph Frontend ["Web Dashboard (HTML/CSS/JS)"]
        UI["Glassmorphic UI"]
        Charts["Real-Time Charts (Chart.js)"]
    end

    %% Connections
    Hardware -.->|Future BLE/Serial| Stream
    Sim -->|Mocked Telemetry| Stream
    
    Stream -->|Raw Data| Classify
    Classify -->|Strain Score & State| Stream
    
    Stream -->|Raw + State| Log
    Log -->|Append| CSV
    
    Stream -->|Push Event| WS
    
    WS -->|Sensor Update| UI
    WS -->|Sensor Update| Intervention
    
    UI -->|Fetch History API| CSV
    UI -->|API Post: Change Mode| Sim
    
    Intervention -->|If 3x Critical| WMI
    Intervention -->|Trigger| TK
    Intervention -->|Log Break| TXT
    
    classDef hardware fill:#e1f5fe,stroke:#0288d1,stroke-width:2px,color:#000;
    classDef backend fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#000;
    classDef intervention fill:#ffebee,stroke:#d32f2f,stroke-width:2px,color:#000;
    classDef storage fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#000;
    classDef frontend fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#000;
    
    class Hardware hardware;
    class Backend backend;
    class Intervention intervention;
    class Storage storage;
    class Frontend frontend;
"""

try:
    compressed = zlib.compress(mermaid_code.encode('utf-8'), 9)
    encoded = base64.urlsafe_b64encode(compressed).decode('ascii')
    url = f"https://kroki.io/mermaid/png/{encoded}"

    print(f"Downloading Architecture Diagram PNG...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    output_path = os.path.join(r"c:\Users\dpvas\OneDrive\Documents\Desktop\Netra-Rakshaka", "Architecture_Diagram.png")
    with urllib.request.urlopen(req) as response, open(output_path, 'wb') as out_file:
        data = response.read()
        out_file.write(data)
    print(f"Successfully saved to {output_path}")
except Exception as e:
    print(f"Error downloading diagram: {e}")
