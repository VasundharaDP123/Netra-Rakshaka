import os
import pickle
import pandas as pd
import numpy as np

# Paths
MODELS_DIR = "models"
MODEL_PATH = os.path.join(MODELS_DIR, "strain_classifier.pkl")
SCALER_PATH = os.path.join(MODELS_DIR, "scaler.pkl")

# Global variables for model and scaler
clf = None
scaler = None

# Attempt to load model and scaler at startup
if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
    try:
        with open(MODEL_PATH, "rb") as f:
            clf = pickle.load(f)
        with open(SCALER_PATH, "rb") as f:
            scaler = pickle.load(f)
        print("[AI ENGINE] Successfully loaded Random Forest model and scaler.")
    except Exception as e:
        print(f"[AI ENGINE] Error loading model/scaler: {e}")

def classify_strain_rule_based(data):
    score = 0

    if data["blink_rate"] < 6:       score += 40
    elif data["blink_rate"] < 10:    score += 20

    if data["screen_distance_cm"] < 20:   score += 25
    elif data["screen_distance_cm"] < 30: score += 10

    if data["eye_temp_celsius"] < 34.0:  score += 15

    if data["room_humidity_pct"] < 28:   score += 10

    if data["ambient_lux"] < 100:        score += 10

    if data["head_tilt_degrees"] > 35:   score += 10

    if score >= 60:   
        return "Critical", score
    elif score >= 30: 
        return "Moderate", score
    
    return "Safe", score

def classify_strain(data):
    global clf, scaler
    
    # If the model and scaler are loaded, use them for ML classification
    if clf is not None and scaler is not None:
        try:
            # Extract features in the exact same order as training
            features = [
                data["blink_rate"],
                data["blink_duration_ms"],
                data["eye_temp_celsius"],
                data["screen_distance_cm"],
                data["ambient_lux"],
                data["room_humidity_pct"],
                data["head_tilt_degrees"]
            ]
            
            # Format for prediction (DataFrame with matching feature names)
            features_df = pd.DataFrame([features], columns=[
                "blink_rate",
                "blink_duration_ms",
                "eye_temp_celsius",
                "screen_distance_cm",
                "ambient_lux",
                "room_humidity_pct",
                "head_tilt_degrees"
            ])
            
            # Scale features
            features_scaled = scaler.transform(features_df)
            
            # Predict class
            prediction = clf.predict(features_scaled)[0]
            
            # Predict probabilities
            probs = clf.predict_proba(features_scaled)[0]
            
            # Map classes to their probabilities
            class_prob_map = dict(zip(clf.classes_, probs))
            
            # Define risk score: probability of eye fatigue (1 - Safe probability)
            prob_safe = class_prob_map.get("Safe", 0.0)
            score = int((1.0 - prob_safe) * 100)
            
            return prediction, score
        except Exception as e:
            print(f"[AI ENGINE] Error during model inference: {e}. Falling back to rule-based.")
            return classify_strain_rule_based(data)
            
    # Fallback to rule-based if files are not present
    return classify_strain_rule_based(data)
