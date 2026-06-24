import os
import pickle
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix

# Paths
DATA_PATH = os.path.join("data", "session_log.csv")
MODELS_DIR = "models"
MODEL_PATH = os.path.join(MODELS_DIR, "strain_classifier.pkl")
SCALER_PATH = os.path.join(MODELS_DIR, "scaler.pkl")

def train_model():
    print("=== Netra Rakshaka AI Model Training ===")
    
    # 1. Load Dataset
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Error: Dataset not found at {DATA_PATH}. Please generate or log data first.")
        
    print(f"Loading dataset from: {DATA_PATH}...")
    df = pd.read_csv(DATA_PATH)
    print(f"Dataset successfully loaded. Total rows: {len(df)}")
    
    # 2. Define Features and Labels
    # We use the 7 active sensor inputs
    features = [
        "blink_rate",
        "blink_duration_ms",
        "eye_temp_celsius",
        "screen_distance_cm",
        "ambient_lux",
        "room_humidity_pct",
        "head_tilt_degrees"
    ]
    target = "strain_level"
    
    # Drop rows with missing values in our features or target
    df = df.dropna(subset=features + [target])
    
    X = df[features]
    y = df[target]
    
    print("\nClass distribution in dataset:")
    print(y.value_counts())
    
    # 3. Train-Test Split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    print(f"\nSplit complete. Train set size: {len(X_train)} | Test set size: {len(X_test)}")
    
    # 4. Standardize Features
    print("Standardizing features using StandardScaler...")
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # 5. Train Random Forest Classifier
    print("Training Random Forest Classifier model...")
    # Using balanced class weights since classes might be slightly unbalanced in logs
    clf = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
    clf.fit(X_train_scaled, y_train)
    print("Model training complete!")
    
    # 6. Evaluate Model
    y_pred = clf.predict(X_test_scaled)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n--- Evaluation Results ---")
    print(f"Accuracy: {acc * 100:.2f}%")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))
    
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, y_pred))
    
    # 7. Save Model and Scaler
    if not os.path.exists(MODELS_DIR):
        os.makedirs(MODELS_DIR)
        print(f"Created directory: {MODELS_DIR}")
        
    print(f"\nSaving model to: {MODEL_PATH}...")
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)
        
    print(f"Saving scaler to: {SCALER_PATH}...")
    with open(SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)
        
    print("=== AI Model Training Pipeline Completed Successfully ===")

if __name__ == "__main__":
    train_model()
