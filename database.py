import sqlite3
import os
import time

DB_PATH = os.path.join("data", "netra_rakshaka.db")

def get_connection():
    if not os.path.exists("data"):
        os.makedirs("data")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Telemetry History Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS telemetry_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        blink_rate INTEGER,
        blink_count INTEGER,
        screen_distance_cm INTEGER,
        eye_temp_celsius REAL,
        ambient_lux INTEGER,
        head_tilt_degrees INTEGER,
        strain_level TEXT,
        strain_score INTEGER
    )
    """)
    
    # 2. Break Events Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS break_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        trigger_reason TEXT,
        duration_sec INTEGER DEFAULT 20
    )
    """)
    
    # 3. User Configuration Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        sensitivity_mode TEXT DEFAULT 'Normal',
        cooldown_sec INTEGER DEFAULT 50,
        sound_alerts INTEGER DEFAULT 1,
        min_bpm_threshold INTEGER DEFAULT 8,
        min_distance_threshold INTEGER DEFAULT 20
    )
    """)
    
    # 4. Compliance Log Table (20-20-20 break user behavior tracking)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compliance_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        event_type TEXT DEFAULT '20-20-20',
        action TEXT CHECK (action IN ('complied', 'skipped', 'ignored'))
    )
    """)

    # 5. Deep Work Sessions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS deep_work_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        duration_min INTEGER DEFAULT 25,
        alerts_during_session INTEGER DEFAULT 0,
        status TEXT DEFAULT 'completed'
    )
    """)
    
    conn.commit()
    conn.close()

def log_telemetry_db(data, strain_level, strain_score):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO telemetry_history (
            blink_rate, blink_count, screen_distance_cm, eye_temp_celsius, ambient_lux, head_tilt_degrees, strain_level, strain_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("blink_rate", 0),
            data.get("blink_count", 0),
            data.get("screen_distance_cm", 0),
            data.get("eye_temp_celsius", 0.0),
            data.get("ambient_lux", 0),
            data.get("head_tilt_degrees", 0),
            strain_level,
            strain_score
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging telemetry to DB: {e}")

def log_break_db(reason="Critical Strain", duration=20):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO break_events (trigger_reason, duration_sec)
        VALUES (?, ?)
        """, (reason, duration))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging break to DB: {e}")

def get_user_settings():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_settings WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "id": 1,
        "sensitivity_mode": "Normal",
        "cooldown_sec": 50,
        "sound_alerts": 1,
        "min_bpm_threshold": 8,
        "min_distance_threshold": 20
    }

def update_user_settings(settings):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE user_settings SET
            sensitivity_mode = ?,
            cooldown_sec = ?,
            sound_alerts = ?,
            min_bpm_threshold = ?,
            min_distance_threshold = ?
        WHERE id = 1
        """, (
            settings.get("sensitivity_mode", "Normal"),
            int(settings.get("cooldown_sec", 50)),
            1 if settings.get("sound_alerts") else 0,
            int(settings.get("min_bpm_threshold", 8)),
            int(settings.get("min_distance_threshold", 20))
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating user settings: {e}")
        return False

def get_analytics_summary(days=1):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Calculate time cutoff
        seconds = days * 86400
        cutoff = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - seconds))
        
        # 1. Telemetry Aggregates
        cursor.execute("""
        SELECT 
            COUNT(*) as total_samples,
            AVG(blink_rate) as avg_bpm,
            AVG(screen_distance_cm) as avg_distance,
            AVG(eye_temp_celsius) as avg_temp,
            AVG(ambient_lux) as avg_lux,
            SUM(CASE WHEN screen_distance_cm >= 20 THEN 1 ELSE 0 END) as compliant_dist_samples,
            SUM(CASE WHEN head_tilt_degrees <= 25 THEN 1 ELSE 0 END) as compliant_posture_samples,
            SUM(CASE WHEN strain_level = 'Safe' THEN 1 ELSE 0 END) as safe_samples,
            SUM(CASE WHEN strain_level = 'Moderate' THEN 1 ELSE 0 END) as mod_samples,
            SUM(CASE WHEN strain_level = 'Critical' THEN 1 ELSE 0 END) as crit_samples
        FROM telemetry_history
        WHERE timestamp >= ?
        """, (cutoff,))
        t_row = cursor.fetchone()
        
        # 2. Break Events Count
        cursor.execute("""
        SELECT COUNT(*) as total_breaks
        FROM break_events
        WHERE timestamp >= ?
        """, (cutoff,))
        b_row = cursor.fetchone()
        conn.close()
        
        total_samples = t_row["total_samples"] or 0
        if total_samples > 0:
            avg_bpm = round(t_row["avg_bpm"] or 0, 1)
            avg_distance = round(t_row["avg_distance"] or 0, 1)
            dist_compliance = round((t_row["compliant_dist_samples"] / total_samples) * 100, 1)
            posture_compliance = round((t_row["compliant_posture_samples"] / total_samples) * 100, 1)
            screen_time_min = round(total_samples * 0.25 / 60, 1) # 4 samples per sec
            safe_pct = round((t_row["safe_samples"] / total_samples) * 100, 1)
            mod_pct = round((t_row["mod_samples"] / total_samples) * 100, 1)
            crit_pct = round((t_row["crit_samples"] / total_samples) * 100, 1)
        else:
            avg_bpm = 0
            avg_distance = 0
            dist_compliance = 100.0
            posture_compliance = 100.0
            screen_time_min = 0.0
            safe_pct = 100.0
            mod_pct = 0.0
            crit_pct = 0.0
            
        total_breaks = b_row["total_breaks"] if b_row else 0
        
        return {
            "time_window": "Daily (24h)" if days == 1 else "Weekly (7d)",
            "total_screen_time_min": screen_time_min,
            "average_bpm": avg_bpm,
            "average_distance_cm": avg_distance,
            "distance_compliance_pct": dist_compliance,
            "posture_compliance_pct": posture_compliance,
            "total_breaks_taken": total_breaks,
            "strain_breakdown": {
                "safe_pct": safe_pct,
                "moderate_pct": mod_pct,
                "critical_pct": crit_pct
            }
        }
    except Exception as e:
        print(f"Error fetching analytics summary: {e}")
        return {
            "time_window": "Daily (24h)",
            "total_screen_time_min": 0,
            "average_bpm": 0,
            "average_distance_cm": 0,
            "distance_compliance_pct": 100,
            "posture_compliance_pct": 100,
            "total_breaks_taken": 0,
            "strain_breakdown": {"safe_pct": 100, "moderate_pct": 0, "critical_pct": 0}
        }

def log_compliance_db(action="complied", event_type="20-20-20"):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO compliance_log (event_type, action) VALUES (?, ?)", (event_type, action))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging compliance to DB: {e}")

def log_deep_work_session_db(duration_min=25, alerts=0, status="completed"):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO deep_work_sessions (duration_min, alerts_during_session, status) VALUES (?, ?, ?)",
                       (duration_min, alerts, status))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging deep work session to DB: {e}")
